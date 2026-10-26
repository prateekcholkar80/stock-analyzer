import os
import unittest
from unittest.mock import patch

from pydantic import ValidationError

from app.exceptions import LLMConfigurationError
from app.llm.config import (
    LLMRole,
    LLMRoleSettings,
    LLMSettings,
    get_llm_settings,
)
from app.llm.factory import LLMGatewayFactory


class _ConfiguredGateway:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configuration_fingerprint(self):
        return "f" * 64

    def generate(self, *, system, messages, response_model):
        raise AssertionError("configuration test must not generate output")


class LLMSettingsTests(unittest.TestCase):
    def tearDown(self):
        get_llm_settings.cache_clear()

    def test_shared_model_configures_every_role(self):
        settings = LLMSettings.from_environment(
            {"JARVIS_LLM_MODEL": "openai/test-model"}
        )

        self.assertEqual(
            settings.for_role(LLMRole.BULL).model,
            "openai/test-model",
        )
        self.assertEqual(
            settings.for_role(LLMRole.BEAR).model,
            "openai/test-model",
        )
        self.assertEqual(
            settings.for_role(LLMRole.JUDGE).model,
            "openai/test-model",
        )
        self.assertEqual(
            settings.for_role(LLMRole.FUNDAMENTAL_ANALYST).model,
            "openai/test-model",
        )
        self.assertEqual(
            settings.for_role(LLMRole.CORPORATE_FILINGS_ANALYST).model,
            "openai/test-model",
        )
        self.assertEqual(
            settings.for_role(LLMRole.JARVIS).model,
            "openai/test-model",
        )
        self.assertEqual(
            settings.for_role(LLMRole.TICKER_RESOLVER).model,
            "openai/test-model",
        )

    def test_ticker_resolver_can_override_shared_model_and_generation_settings(
        self,
    ):
        settings = LLMSettings.from_environment(
            {
                "JARVIS_LLM_MODEL": "provider/shared",
                "JARVIS_TICKER_RESOLVER_LLM_MODEL": "provider/resolver",
                "JARVIS_TICKER_RESOLVER_LLM_TEMPERATURE": "0.1",
                "JARVIS_TICKER_RESOLVER_LLM_MAX_TOKENS": "150",
            }
        )

        resolver = settings.for_role(LLMRole.TICKER_RESOLVER)
        self.assertEqual(resolver.model, "provider/resolver")
        self.assertEqual(resolver.temperature, 0.1)
        self.assertEqual(resolver.max_tokens, 150)

    def test_persona_can_override_shared_model_and_generation_settings(self):
        settings = LLMSettings.from_environment(
            {
                "JARVIS_LLM_MODEL": "provider/shared",
                "JARVIS_PERSONA_LLM_MODEL": "provider/presenter",
                "JARVIS_PERSONA_LLM_TEMPERATURE": "0.1",
                "JARVIS_PERSONA_LLM_MAX_TOKENS": "3000",
            }
        )

        persona = settings.for_role(LLMRole.JARVIS)
        self.assertEqual(persona.model, "provider/presenter")
        self.assertEqual(persona.temperature, 0.1)
        self.assertEqual(persona.max_tokens, 3000)

    def test_fundamental_analyst_has_dedicated_generation_settings(self):
        settings = LLMSettings.from_environment(
            {
                "JARVIS_LLM_MODEL": "provider/shared",
                "JARVIS_FUNDAMENTAL_ANALYST_LLM_MODEL": (
                    "provider/fundamental"
                ),
                "JARVIS_FUNDAMENTAL_ANALYST_LLM_TEMPERATURE": "0.1",
                "JARVIS_FUNDAMENTAL_ANALYST_LLM_MAX_TOKENS": "3200",
            }
        )

        analyst = settings.for_role(LLMRole.FUNDAMENTAL_ANALYST)
        self.assertEqual(analyst.model, "provider/fundamental")
        self.assertEqual(analyst.temperature, 0.1)
        self.assertEqual(analyst.max_tokens, 3200)

        factory = LLMGatewayFactory(settings, _ConfiguredGateway)
        gateway = factory.for_role(LLMRole.FUNDAMENTAL_ANALYST)
        self.assertEqual(gateway.settings, analyst)
        self.assertIs(
            gateway,
            factory.for_role(LLMRole.FUNDAMENTAL_ANALYST),
        )

    def test_corporate_filings_analyst_has_dedicated_generation_settings(self):
        settings = LLMSettings.from_environment(
            {
                "JARVIS_LLM_MODEL": "provider/shared",
                "JARVIS_CORPORATE_FILINGS_ANALYST_LLM_MODEL": (
                    "provider/filings"
                ),
                "JARVIS_CORPORATE_FILINGS_ANALYST_LLM_TEMPERATURE": "0.1",
                "JARVIS_CORPORATE_FILINGS_ANALYST_LLM_MAX_TOKENS": "3600",
            }
        )

        analyst = settings.for_role(LLMRole.CORPORATE_FILINGS_ANALYST)
        self.assertEqual(analyst.model, "provider/filings")
        self.assertEqual(analyst.temperature, 0.1)
        self.assertEqual(analyst.max_tokens, 3600)

        factory = LLMGatewayFactory(settings, _ConfiguredGateway)
        gateway = factory.for_role(LLMRole.CORPORATE_FILINGS_ANALYST)
        self.assertEqual(gateway.settings, analyst)
        self.assertIs(
            gateway,
            factory.for_role(LLMRole.CORPORATE_FILINGS_ANALYST),
        )

    def test_role_specific_model_overrides_shared_model(self):
        settings = LLMSettings.from_environment(
            {
                "JARVIS_LLM_MODEL": "local/shared-model",
                "JARVIS_JUDGE_LLM_MODEL": "local/judge-model",
            }
        )

        self.assertEqual(
            settings.for_role(LLMRole.BULL).model,
            "local/shared-model",
        )
        self.assertEqual(
            settings.for_role(LLMRole.JUDGE).model,
            "local/judge-model",
        )

    def test_all_role_models_can_be_configured_without_shared_model(self):
        settings = LLMSettings.from_environment(
            {
                "JARVIS_BULL_LLM_MODEL": "provider/bull-model",
                "JARVIS_BEAR_LLM_MODEL": "provider/bear-model",
                "JARVIS_JUDGE_LLM_MODEL": "provider/judge-model",
            }
        )

        self.assertEqual(
            settings.for_role(LLMRole.BULL).model,
            "provider/bull-model",
        )
        self.assertEqual(
            settings.for_role(LLMRole.BEAR).model,
            "provider/bear-model",
        )
        self.assertEqual(
            settings.for_role(LLMRole.JUDGE).model,
            "provider/judge-model",
        )
        with self.assertRaisesRegex(ValueError, "fundamental_analyst"):
            settings.for_role(LLMRole.FUNDAMENTAL_ANALYST)

    def test_blank_override_falls_back_to_shared_model(self):
        settings = LLMSettings.from_environment(
            {
                "JARVIS_LLM_MODEL": "provider/shared-model",
                "JARVIS_BULL_LLM_MODEL": "   ",
            }
        )

        self.assertIsNone(settings.bull_model)
        self.assertEqual(
            settings.for_role(LLMRole.BULL).model,
            "provider/shared-model",
        )

    def test_models_are_trimmed(self):
        settings = LLMSettings.from_environment(
            {"JARVIS_LLM_MODEL": "  provider/model  "}
        )

        self.assertEqual(settings.shared_model, "provider/model")

    def test_rejects_missing_model_configuration(self):
        with self.assertRaises(ValidationError):
            LLMSettings.from_environment({})

    def test_rejects_incomplete_role_configuration_without_shared_model(self):
        with self.assertRaisesRegex(ValidationError, "bear, judge"):
            LLMSettings.from_environment(
                {"JARVIS_BULL_LLM_MODEL": "provider/bull-model"}
            )

    def test_reads_role_parameters_from_environment_strings(self):
        settings = LLMSettings.from_environment(
            {
                "JARVIS_LLM_MODEL": "provider/model",
                "JARVIS_BULL_LLM_TEMPERATURE": "0.6",
                "JARVIS_BEAR_LLM_TEMPERATURE": "0.2",
                "JARVIS_JUDGE_LLM_TEMPERATURE": "0",
                "JARVIS_BULL_LLM_MAX_TOKENS": "901",
                "JARVIS_BEAR_LLM_MAX_TOKENS": "902",
                "JARVIS_JUDGE_LLM_MAX_TOKENS": "903",
            }
        )

        bull = settings.for_role(LLMRole.BULL)
        bear = settings.for_role(LLMRole.BEAR)
        judge = settings.for_role(LLMRole.JUDGE)
        self.assertEqual((bull.temperature, bull.max_tokens), (0.6, 901))
        self.assertEqual((bear.temperature, bear.max_tokens), (0.2, 902))
        self.assertEqual((judge.temperature, judge.max_tokens), (0.0, 903))

    def test_uses_role_appropriate_defaults(self):
        settings = LLMSettings.from_environment(
            {"JARVIS_LLM_MODEL": "provider/model"}
        )

        self.assertEqual(
            settings.for_role(LLMRole.BULL),
            LLMRoleSettings(
                role=LLMRole.BULL,
                model="provider/model",
                temperature=0.4,
                max_tokens=800,
            ),
        )
        self.assertEqual(
            settings.for_role(LLMRole.JUDGE),
            LLMRoleSettings(
                role=LLMRole.JUDGE,
                model="provider/model",
                temperature=0.0,
                max_tokens=600,
            ),
        )
        self.assertEqual(
            settings.for_role(LLMRole.JARVIS).max_tokens,
            5000,
        )
        self.assertEqual(
            settings.for_role(LLMRole.FUNDAMENTAL_ANALYST),
            LLMRoleSettings(
                role=LLMRole.FUNDAMENTAL_ANALYST,
                model="provider/model",
                temperature=0.0,
                max_tokens=2500,
            ),
        )
        self.assertEqual(
            settings.for_role(LLMRole.CORPORATE_FILINGS_ANALYST),
            LLMRoleSettings(
                role=LLMRole.CORPORATE_FILINGS_ANALYST,
                model="provider/model",
                temperature=0.0,
                max_tokens=4000,
            ),
        )
        self.assertEqual(
            settings.for_role(LLMRole.TICKER_RESOLVER),
            LLMRoleSettings(
                role=LLMRole.TICKER_RESOLVER,
                model="provider/model",
                temperature=0.0,
                max_tokens=300,
            ),
        )

    def test_rejects_invalid_temperature_for_each_role(self):
        for variable, value in (
            ("JARVIS_BULL_LLM_TEMPERATURE", "-0.1"),
            ("JARVIS_BEAR_LLM_TEMPERATURE", "2.1"),
            ("JARVIS_JUDGE_LLM_TEMPERATURE", "nan"),
            ("JARVIS_FUNDAMENTAL_ANALYST_LLM_TEMPERATURE", "-0.1"),
            (
                "JARVIS_CORPORATE_FILINGS_ANALYST_LLM_TEMPERATURE",
                "2.1",
            ),
        ):
            with self.subTest(variable=variable, value=value):
                with self.assertRaises(ValidationError):
                    LLMSettings.from_environment(
                        {
                            "JARVIS_LLM_MODEL": "provider/model",
                            variable: value,
                        }
                    )

    def test_rejects_non_positive_token_limit_for_each_role(self):
        for variable in (
            "JARVIS_BULL_LLM_MAX_TOKENS",
            "JARVIS_BEAR_LLM_MAX_TOKENS",
            "JARVIS_JUDGE_LLM_MAX_TOKENS",
            "JARVIS_FUNDAMENTAL_ANALYST_LLM_MAX_TOKENS",
            "JARVIS_CORPORATE_FILINGS_ANALYST_LLM_MAX_TOKENS",
        ):
            with self.subTest(variable=variable):
                with self.assertRaises(ValidationError):
                    LLMSettings.from_environment(
                        {
                            "JARVIS_LLM_MODEL": "provider/model",
                            variable: "0",
                        }
                    )

    def test_rejects_unvalidated_role_argument(self):
        settings = LLMSettings.from_environment(
            {"JARVIS_LLM_MODEL": "provider/model"}
        )

        with self.assertRaisesRegex(ValueError, "validated LLMRole"):
            settings.for_role("bull")

    def test_configuration_is_immutable(self):
        settings = LLMSettings.from_environment(
            {"JARVIS_LLM_MODEL": "provider/model"}
        )

        with self.assertRaises(ValidationError):
            settings.shared_model = "provider/other-model"

    def test_provider_credentials_are_ignored_and_not_represented(self):
        settings = LLMSettings.from_environment(
            {
                "JARVIS_LLM_MODEL": "provider/model",
                "OPENAI_API_KEY": "must-not-appear",
                "ANTHROPIC_API_KEY": "also-must-not-appear",
            }
        )

        representation = repr(settings)
        self.assertNotIn("must-not-appear", representation)
        self.assertNotIn("also-must-not-appear", representation)
        self.assertNotIn("OPENAI_API_KEY", settings.model_fields_set)
        self.assertNotIn("ANTHROPIC_API_KEY", settings.model_fields_set)

    def test_get_llm_settings_wraps_environment_validation_failure(self):
        get_llm_settings.cache_clear()

        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(LLMConfigurationError) as context:
                get_llm_settings()

        self.assertIsInstance(context.exception.__cause__, ValidationError)


if __name__ == "__main__":
    unittest.main()
