import unittest
from dataclasses import FrozenInstanceError

from app.exceptions import (
    ApplicationError,
    ExternalServiceError,
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMError,
    LLMFailureContext,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMResponseValidationError,
)


class LLMExceptionHierarchyTests(unittest.TestCase):
    def test_every_typed_failure_is_an_application_llm_error(self):
        failure_types = (
            LLMConfigurationError,
            LLMAuthenticationError,
            LLMProviderUnavailableError,
            LLMRateLimitError,
            LLMResponseValidationError,
        )

        for failure_type in failure_types:
            with self.subTest(failure_type=failure_type.__name__):
                error = failure_type("Safe failure")
                self.assertIsInstance(error, LLMError)
                self.assertIsInstance(error, ExternalServiceError)
                self.assertIsInstance(error, ApplicationError)

    def test_existing_message_only_construction_remains_compatible(self):
        error = LLMResponseValidationError("Invalid structured response")

        self.assertEqual(str(error), "Invalid structured response")
        self.assertFalse(error.context.retryable)
        self.assertIsNone(error.context.role)

    def test_unavailable_and_rate_limit_failures_are_retryable_by_default(self):
        unavailable = LLMProviderUnavailableError("Provider unavailable")
        rate_limited = LLMRateLimitError("Provider throttled request")

        self.assertTrue(unavailable.context.retryable)
        self.assertTrue(rate_limited.context.retryable)

    def test_configuration_authentication_and_validation_are_not_retryable(self):
        failures = (
            LLMConfigurationError("Configuration missing"),
            LLMAuthenticationError("Authentication rejected"),
            LLMResponseValidationError("Response invalid"),
        )

        self.assertTrue(
            all(not failure.context.retryable for failure in failures)
        )

    def test_explicit_retryability_can_override_default(self):
        error = LLMProviderUnavailableError(
            "Provider disabled",
            retryable=False,
        )

        self.assertFalse(error.context.retryable)


class LLMFailureContextTests(unittest.TestCase):
    def test_error_exposes_only_sanitized_operational_context(self):
        error = LLMAuthenticationError(
            "LLM provider rejected its configured credential",
            role="  bull  ",
            provider="  test-provider  ",
            model="  test-model  ",
            operation_id="  operation-123  ",
        )

        self.assertEqual(error.context.role, "bull")
        self.assertEqual(error.context.provider, "test-provider")
        self.assertEqual(error.context.model, "test-model")
        self.assertEqual(error.context.operation_id, "operation-123")
        self.assertFalse(error.context.retryable)

    def test_context_is_immutable(self):
        context = LLMFailureContext(role="judge")

        with self.assertRaises(FrozenInstanceError):
            context.role = "bull"

    def test_context_rejects_blank_identifiers(self):
        for field_name in ("role", "provider", "model", "operation_id"):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValueError):
                    LLMFailureContext(**{field_name: "   "})

    def test_context_rejects_non_string_identifiers(self):
        with self.assertRaises(TypeError):
            LLMFailureContext(role=123)

    def test_context_rejects_non_boolean_retryability(self):
        with self.assertRaises(TypeError):
            LLMFailureContext(retryable="yes")

    def test_raw_provider_failure_is_chained_but_not_rendered(self):
        provider_secret = "api-key-must-not-leak"

        try:
            raise RuntimeError(
                f"authorization failed for token {provider_secret}"
            )
        except RuntimeError as cause:
            try:
                raise LLMAuthenticationError(
                    "LLM provider rejected its configured credential",
                    role="bear",
                    provider="test-provider",
                    model="test-model",
                ) from cause
            except LLMAuthenticationError as error:
                captured = error

        self.assertIsInstance(captured.__cause__, RuntimeError)
        self.assertNotIn(provider_secret, str(captured))
        self.assertNotIn(provider_secret, repr(captured))
        self.assertNotIn(provider_secret, repr(captured.context))


if __name__ == "__main__":
    unittest.main()
