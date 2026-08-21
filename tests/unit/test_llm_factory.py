import unittest

from app.exceptions import LLMConfigurationError
from app.llm.adapters.litellm_gateway import LiteLLMStructuredGateway
from app.llm.config import LLMRole, LLMSettings
from app.llm.factory import LLMGatewayFactory
from app.llm.gateway import StructuredLLMGateway


class FakeGateway:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configuration_fingerprint(self):
        return "a" * 64

    def generate(self, *, system, messages, response_model):
        raise AssertionError("generation is not used by factory tests")


class InvalidGateway:
    pass


class RecordingBuilder:
    def __init__(self):
        self.settings = []

    def __call__(self, settings):
        self.settings.append(settings)
        return FakeGateway(settings)


class LLMGatewayFactoryTests(unittest.TestCase):
    def test_builds_gateway_with_resolved_settings_for_each_role(self):
        settings = LLMSettings.from_environment(
            {
                "JARVIS_LLM_MODEL": "provider/shared-model",
                "JARVIS_BULL_LLM_MODEL": "provider/bull-model",
                "JARVIS_JUDGE_LLM_MODEL": "provider/judge-model",
                "JARVIS_BULL_LLM_TEMPERATURE": "0.6",
                "JARVIS_JUDGE_LLM_MAX_TOKENS": "1200",
            }
        )
        builder = RecordingBuilder()
        factory = LLMGatewayFactory(settings, builder)

        bull = factory.for_role(LLMRole.BULL)
        bear = factory.for_role(LLMRole.BEAR)
        judge = factory.for_role(LLMRole.JUDGE)

        self.assertIsInstance(bull, StructuredLLMGateway)
        self.assertEqual(bull.settings.model, "provider/bull-model")
        self.assertEqual(bull.settings.temperature, 0.6)
        self.assertEqual(bear.settings.model, "provider/shared-model")
        self.assertEqual(judge.settings.model, "provider/judge-model")
        self.assertEqual(judge.settings.max_tokens, 1200)
        self.assertEqual(
            [item.role for item in builder.settings],
            [LLMRole.BULL, LLMRole.BEAR, LLMRole.JUDGE],
        )

    def test_caches_one_gateway_per_role(self):
        builder = RecordingBuilder()
        factory = LLMGatewayFactory(
            LLMSettings.from_environment(
                {"JARVIS_LLM_MODEL": "provider/model"}
            ),
            builder,
        )

        first = factory.for_role(LLMRole.BULL)
        second = factory.for_role(LLMRole.BULL)

        self.assertIs(first, second)
        self.assertEqual(len(builder.settings), 1)

    def test_different_roles_receive_distinct_gateway_instances(self):
        factory = LLMGatewayFactory(
            LLMSettings.from_environment(
                {"JARVIS_LLM_MODEL": "provider/model"}
            ),
            RecordingBuilder(),
        )

        self.assertIsNot(
            factory.for_role(LLMRole.BULL),
            factory.for_role(LLMRole.BEAR),
        )

    def test_default_builder_creates_litellm_adapter(self):
        factory = LLMGatewayFactory(
            LLMSettings.from_environment(
                {"JARVIS_LLM_MODEL": "openai/test-model"}
            )
        )

        gateway = factory.for_role(LLMRole.JUDGE)

        self.assertIsInstance(gateway, LiteLLMStructuredGateway)
        self.assertEqual(gateway.settings.role, LLMRole.JUDGE)

    def test_rejects_invalid_factory_dependencies(self):
        settings = LLMSettings.from_environment(
            {"JARVIS_LLM_MODEL": "provider/model"}
        )

        with self.assertRaises(ValueError):
            LLMGatewayFactory("invalid-settings", RecordingBuilder())
        with self.assertRaises(ValueError):
            LLMGatewayFactory(settings, None)

    def test_rejects_unvalidated_role_without_calling_builder(self):
        builder = RecordingBuilder()
        factory = LLMGatewayFactory(
            LLMSettings.from_environment(
                {"JARVIS_LLM_MODEL": "provider/model"}
            ),
            builder,
        )

        with self.assertRaisesRegex(ValueError, "validated LLMRole"):
            factory.for_role("bull")

        self.assertEqual(builder.settings, [])

    def test_rejects_builder_result_that_does_not_match_gateway_contract(self):
        factory = LLMGatewayFactory(
            LLMSettings.from_environment(
                {"JARVIS_LLM_MODEL": "provider/model"}
            ),
            lambda settings: InvalidGateway(),
        )

        with self.assertRaises(LLMConfigurationError) as context:
            factory.for_role(LLMRole.BEAR)

        self.assertEqual(context.exception.context.role, "bear")
        self.assertEqual(
            context.exception.context.model,
            "provider/model",
        )

    def test_sanitizes_and_chains_unexpected_builder_failure(self):
        secret = "builder-secret-must-not-leak"

        def failing_builder(settings):
            raise RuntimeError(secret)

        factory = LLMGatewayFactory(
            LLMSettings.from_environment(
                {"JARVIS_LLM_MODEL": "provider/model"}
            ),
            failing_builder,
        )

        with self.assertRaises(LLMConfigurationError) as context:
            factory.for_role(LLMRole.JUDGE)

        self.assertIsInstance(context.exception.__cause__, RuntimeError)
        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, repr(context.exception.context))

    def test_preserves_typed_configuration_failure_from_builder(self):
        expected = LLMConfigurationError(
            "Safe adapter configuration failure",
            role="bull",
            model="provider/model",
        )

        def failing_builder(settings):
            raise expected

        factory = LLMGatewayFactory(
            LLMSettings.from_environment(
                {"JARVIS_LLM_MODEL": "provider/model"}
            ),
            failing_builder,
        )

        with self.assertRaises(LLMConfigurationError) as context:
            factory.for_role(LLMRole.BULL)

        self.assertIs(context.exception, expected)

    def test_configuration_fingerprint_is_stable_and_complete(self):
        environment = {
            "JARVIS_LLM_MODEL": "provider/model",
            "JARVIS_BULL_LLM_TEMPERATURE": "0.6",
            "JARVIS_JUDGE_LLM_MAX_TOKENS": "900",
        }
        first = LLMGatewayFactory(
            LLMSettings.from_environment(environment),
            RecordingBuilder(),
        )
        second = LLMGatewayFactory(
            LLMSettings.from_environment(environment),
            RecordingBuilder(),
        )

        self.assertEqual(
            first.configuration_fingerprint,
            second.configuration_fingerprint,
        )
        self.assertEqual(len(first.configuration_fingerprint), 64)

        changes = (
            {"JARVIS_LLM_MODEL": "provider/other-model"},
            {"JARVIS_BULL_LLM_TEMPERATURE": "0.7"},
            {"JARVIS_JUDGE_LLM_MAX_TOKENS": "901"},
        )
        for change in changes:
            with self.subTest(change=change):
                changed = LLMGatewayFactory(
                    LLMSettings.from_environment(
                        {**environment, **change}
                    ),
                    RecordingBuilder(),
                )
                self.assertNotEqual(
                    first.configuration_fingerprint,
                    changed.configuration_fingerprint,
                )

    def test_equivalent_resolved_role_settings_share_fingerprint(self):
        shared = LLMGatewayFactory(
            LLMSettings.from_environment(
                {"JARVIS_LLM_MODEL": "provider/model"}
            ),
            RecordingBuilder(),
        )
        explicit = LLMGatewayFactory(
            LLMSettings.from_environment(
                {
                    "JARVIS_BULL_LLM_MODEL": "provider/model",
                    "JARVIS_BEAR_LLM_MODEL": "provider/model",
                    "JARVIS_JUDGE_LLM_MODEL": "provider/model",
                }
            ),
            RecordingBuilder(),
        )

        self.assertEqual(
            shared.configuration_fingerprint,
            explicit.configuration_fingerprint,
        )

    def test_role_fingerprint_changes_only_with_resolved_role_settings(self):
        baseline = LLMGatewayFactory(
            LLMSettings.from_environment(
                {"JARVIS_LLM_MODEL": "provider/model"}
            ),
            RecordingBuilder(),
        )
        changed_judge = LLMGatewayFactory(
            LLMSettings.from_environment(
                {
                    "JARVIS_LLM_MODEL": "provider/model",
                    "JARVIS_JUDGE_LLM_MODEL": "provider/judge-model",
                }
            ),
            RecordingBuilder(),
        )

        self.assertEqual(
            baseline.role_configuration_fingerprint(LLMRole.BULL),
            changed_judge.role_configuration_fingerprint(LLMRole.BULL),
        )
        self.assertNotEqual(
            baseline.role_configuration_fingerprint(LLMRole.JUDGE),
            changed_judge.role_configuration_fingerprint(LLMRole.JUDGE),
        )


if __name__ == "__main__":
    unittest.main()
