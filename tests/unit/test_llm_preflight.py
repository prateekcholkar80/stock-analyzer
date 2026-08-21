import unittest
from datetime import datetime

from pydantic import ValidationError

from app.exceptions import LLMConfigurationError
from app.llm.config import LLMRole, LLMSettings
from app.llm.factory import LLMGatewayFactory
from app.llm.preflight import LLMPreflightValidator
from app.logging_config import operation_context
from app.models.llm import LLMPreflightResult, LLMRolePreflight


class FakeGateway:
    def __init__(self, settings):
        self.settings = settings

    @property
    def configuration_fingerprint(self):
        return "a" * 64

    def generate(self, *, system, messages, response_model):
        raise AssertionError("generation is not part of local preflight")


class RecordingBuilder:
    def __init__(self):
        self.roles = []

    def __call__(self, settings):
        self.roles.append(settings.role)
        return FakeGateway(settings)


def _provider_resolver(*, model):
    provider = model.split("/", 1)[0]
    return model, provider, None, None


class LLMPreflightValidatorTests(unittest.TestCase):
    def _factory(self, environment, builder=None):
        return LLMGatewayFactory(
            LLMSettings.from_environment(environment),
            builder or RecordingBuilder(),
        )

    def test_validates_complete_panel_and_builds_every_gateway(self):
        builder = RecordingBuilder()
        credential_calls = []

        def api_key_resolver(provider, dynamic_api_key):
            credential_calls.append((provider, dynamic_api_key))
            return "secret-never-returned"

        validator = LLMPreflightValidator(
            self._factory(
                {"JARVIS_LLM_MODEL": "openai/test-model"},
                builder,
            ),
            provider_resolver=_provider_resolver,
            api_key_resolver=api_key_resolver,
        )

        result = validator.validate_full_debate()

        self.assertTrue(result.ready)
        self.assertEqual(
            tuple(item.role for item in result.roles),
            (LLMRole.BULL, LLMRole.BEAR, LLMRole.JUDGE),
        )
        self.assertTrue(
            all(item.structured_gateway_ready for item in result.roles)
        )
        self.assertTrue(all(item.credential_ready for item in result.roles))
        self.assertTrue(all(item.credential_required for item in result.roles))
        self.assertEqual(credential_calls, [("openai", None)])
        self.assertEqual(
            builder.roles,
            [LLMRole.BULL, LLMRole.BEAR, LLMRole.JUDGE],
        )
        self.assertIsNotNone(result.checked_at.utcoffset())

    def test_checks_each_distinct_provider_once(self):
        credential_calls = []

        def api_key_resolver(provider, dynamic_api_key):
            credential_calls.append(provider)
            return f"{provider}-secret"

        validator = LLMPreflightValidator(
            self._factory(
                {
                    "JARVIS_BULL_LLM_MODEL": "openai/bull-model",
                    "JARVIS_BEAR_LLM_MODEL": "anthropic/bear-model",
                    "JARVIS_JUDGE_LLM_MODEL": "openai/judge-model",
                }
            ),
            provider_resolver=_provider_resolver,
            api_key_resolver=api_key_resolver,
        )

        result = validator.validate_full_debate()

        self.assertEqual(credential_calls, ["openai", "anthropic"])
        self.assertEqual(
            [item.provider for item in result.roles],
            ["openai", "anthropic", "openai"],
        )

    def test_missing_credential_fails_before_any_gateway_is_built(self):
        builder = RecordingBuilder()
        validator = LLMPreflightValidator(
            self._factory(
                {"JARVIS_LLM_MODEL": "openai/test-model"},
                builder,
            ),
            provider_resolver=_provider_resolver,
            api_key_resolver=lambda provider, dynamic: None,
        )

        with operation_context("preflight-operation"):
            with self.assertRaises(LLMConfigurationError) as context:
                validator.validate_full_debate()

        self.assertEqual(context.exception.context.role, "bull")
        self.assertEqual(context.exception.context.provider, "openai")
        self.assertEqual(
            context.exception.context.operation_id,
            "preflight-operation",
        )
        self.assertEqual(builder.roles, [])

    def test_blank_credential_is_treated_as_missing(self):
        validator = LLMPreflightValidator(
            self._factory(
                {"JARVIS_LLM_MODEL": "anthropic/test-model"}
            ),
            provider_resolver=_provider_resolver,
            api_key_resolver=lambda provider, dynamic: "   ",
        )

        with self.assertRaises(LLMConfigurationError):
            validator.validate_full_debate()

    def test_known_local_provider_requires_no_key(self):
        credential_calls = []
        validator = LLMPreflightValidator(
            self._factory(
                {"JARVIS_LLM_MODEL": "ollama/qwen-test"}
            ),
            provider_resolver=_provider_resolver,
            api_key_resolver=lambda provider, dynamic: credential_calls.append(
                provider
            ),
        )

        result = validator.validate_full_debate()

        self.assertEqual(credential_calls, [])
        self.assertTrue(
            all(not item.credential_required for item in result.roles)
        )
        self.assertTrue(all(item.credential_ready for item in result.roles))

    def test_explicit_custom_local_provider_can_be_keyless(self):
        validator = LLMPreflightValidator(
            self._factory(
                {"JARVIS_LLM_MODEL": "jarvis_local/model"}
            ),
            provider_resolver=_provider_resolver,
            api_key_resolver=lambda provider, dynamic: None,
            keyless_providers={" JARVIS_LOCAL "},
        )

        result = validator.validate_full_debate()

        self.assertEqual(result.roles[0].provider, "jarvis_local")
        self.assertFalse(result.roles[0].credential_required)

    def test_provider_resolution_failure_is_sanitized(self):
        secret = "resolver-secret-must-not-leak"

        def failing_resolver(*, model):
            raise RuntimeError(secret)

        validator = LLMPreflightValidator(
            self._factory(
                {"JARVIS_LLM_MODEL": "provider/model"}
            ),
            provider_resolver=failing_resolver,
            api_key_resolver=lambda provider, dynamic: "key",
        )

        with self.assertRaises(LLMConfigurationError) as context:
            validator.validate_full_debate()

        self.assertIsInstance(context.exception.__cause__, RuntimeError)
        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, repr(context.exception.context))

    def test_credential_inspection_failure_is_sanitized(self):
        secret = "credential-resolver-secret"

        def failing_key_resolver(provider, dynamic_api_key):
            raise RuntimeError(secret)

        validator = LLMPreflightValidator(
            self._factory(
                {"JARVIS_LLM_MODEL": "provider/model"}
            ),
            provider_resolver=_provider_resolver,
            api_key_resolver=failing_key_resolver,
        )

        with self.assertRaises(LLMConfigurationError) as context:
            validator.validate_full_debate()

        self.assertIsInstance(context.exception.__cause__, RuntimeError)
        self.assertNotIn(secret, str(context.exception))
        self.assertNotIn(secret, repr(context.exception.context))

    def test_result_contains_no_credential_value(self):
        secret = "must-not-appear-in-result"
        validator = LLMPreflightValidator(
            self._factory(
                {"JARVIS_LLM_MODEL": "openai/model"}
            ),
            provider_resolver=_provider_resolver,
            api_key_resolver=lambda provider, dynamic: secret,
        )

        result = validator.validate_full_debate()

        self.assertNotIn(secret, repr(result))
        self.assertNotIn(secret, result.model_dump_json())

    def test_factory_failure_is_preserved_and_no_result_is_returned(self):
        def failing_builder(settings):
            raise LLMConfigurationError(
                "Safe gateway failure",
                role=settings.role.value,
                model=settings.model,
            )

        validator = LLMPreflightValidator(
            self._factory(
                {"JARVIS_LLM_MODEL": "provider/model"},
                failing_builder,
            ),
            provider_resolver=_provider_resolver,
            api_key_resolver=lambda provider, dynamic: "key",
        )

        with self.assertRaisesRegex(
            LLMConfigurationError,
            "Safe gateway failure",
        ):
            validator.validate_full_debate()

    def test_rejects_invalid_constructor_dependencies(self):
        factory = self._factory(
            {"JARVIS_LLM_MODEL": "provider/model"}
        )

        with self.assertRaises(ValueError):
            LLMPreflightValidator("invalid-factory")
        with self.assertRaises(ValueError):
            LLMPreflightValidator(factory, provider_resolver=None)
        with self.assertRaises(ValueError):
            LLMPreflightValidator(factory, api_key_resolver=None)
        with self.assertRaises(ValueError):
            LLMPreflightValidator(factory, keyless_providers="ollama")
        with self.assertRaises(ValueError):
            LLMPreflightValidator(factory, keyless_providers={" "})


class LLMPreflightModelTests(unittest.TestCase):
    def _role(self, role):
        return LLMRolePreflight(
            role=role,
            provider="provider",
            model="provider/model",
            credential_required=True,
            credential_ready=True,
            structured_gateway_ready=True,
        )

    def test_result_rejects_incomplete_duplicate_or_unready_panel(self):
        valid_roles = tuple(self._role(role) for role in LLMRole)
        cases = (
            valid_roles[:2],
            (valid_roles[0], valid_roles[0], valid_roles[2]),
            (
                valid_roles[0].model_copy(
                    update={"credential_ready": False}
                ),
                valid_roles[1],
                valid_roles[2],
            ),
        )

        for roles in cases:
            with self.subTest(roles=roles):
                with self.assertRaises(ValidationError):
                    LLMPreflightResult(
                        configuration_fingerprint="a" * 64,
                        roles=roles,
                        checked_at=datetime.now().astimezone(),
                        ready=True,
                    )

    def test_result_rejects_naive_timestamp(self):
        roles = tuple(self._role(role) for role in LLMRole)

        with self.assertRaises(ValidationError):
            LLMPreflightResult(
                configuration_fingerprint="a" * 64,
                roles=roles,
                checked_at=datetime.now(),
                ready=True,
            )

    def test_result_rejects_invalid_fingerprint(self):
        roles = tuple(self._role(role) for role in LLMRole)

        with self.assertRaises(ValidationError):
            LLMPreflightResult(
                configuration_fingerprint="invalid",
                roles=roles,
                checked_at=datetime.now().astimezone(),
                ready=True,
            )


if __name__ == "__main__":
    unittest.main()
