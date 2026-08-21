import unittest

from pydantic import ValidationError

from app.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMResponseValidationError,
)
from app.llm.config import LLMRole
from app.models.llm import JarvisLLMFailureResponse, LLMFailureCode
from app.presentation.llm_failures import present_llm_failure


class JarvisLLMFailurePresentationTests(unittest.TestCase):
    def test_maps_every_known_failure_to_stable_user_facing_code(self):
        scenarios = (
            (LLMConfigurationError("raw"), LLMFailureCode.CONFIGURATION),
            (LLMAuthenticationError("raw"), LLMFailureCode.AUTHENTICATION),
            (
                LLMProviderUnavailableError("raw"),
                LLMFailureCode.PROVIDER_UNAVAILABLE,
            ),
            (LLMRateLimitError("raw"), LLMFailureCode.RATE_LIMITED),
            (
                LLMResponseValidationError("raw"),
                LLMFailureCode.INVALID_RESPONSE,
            ),
        )

        for error, expected_code in scenarios:
            with self.subTest(error=type(error).__name__):
                response = present_llm_failure(error)

                self.assertEqual(response.code, expected_code)
                self.assertFalse(response.analysis_available)
                self.assertTrue(response.title)
                self.assertTrue(response.display_message)
                self.assertTrue(response.spoken_message)
                self.assertTrue(response.recovery_action)
                self.assertIn(
                    "No investment conclusion was produced.",
                    response.display_message,
                )

    def test_preserves_safe_retry_role_and_operation_metadata(self):
        response = present_llm_failure(
            LLMProviderUnavailableError(
                "raw",
                role="bull",
                provider="provider",
                model="model",
                operation_id="operation-123",
                retryable=False,
            )
        )

        self.assertFalse(response.retryable)
        self.assertEqual(response.failed_role, LLMRole.BULL)
        self.assertEqual(response.operation_id, "operation-123")
        serialized = response.model_dump(mode="json")
        self.assertNotIn("provider", serialized)
        self.assertNotIn("model", serialized)

    def test_unknown_or_malformed_role_is_not_exposed(self):
        response = present_llm_failure(
            LLMAuthenticationError("raw", role="untrusted-role")
        )

        self.assertIsNone(response.failed_role)

    def test_boundary_operation_id_overrides_error_context(self):
        supplied = present_llm_failure(
            LLMConfigurationError("raw"),
            operation_id="boundary-operation",
        )
        overridden = present_llm_failure(
            LLMConfigurationError("raw", operation_id="error-operation"),
            operation_id="boundary-operation",
        )

        self.assertEqual(supplied.operation_id, "boundary-operation")
        self.assertEqual(overridden.operation_id, "boundary-operation")

    def test_rejects_invalid_boundary_operation_id(self):
        with self.assertRaisesRegex(ValueError, "boundary operation ID"):
            present_llm_failure(
                LLMConfigurationError("raw"),
                operation_id=" ",
            )

    def test_generic_llm_error_uses_safe_unknown_response(self):
        response = present_llm_failure(
            LLMError("an unmapped classified failure", retryable=True)
        )

        self.assertEqual(response.code, LLMFailureCode.UNKNOWN)
        self.assertTrue(response.retryable)
        self.assertFalse(response.analysis_available)

    def test_never_renders_raw_error_cause_provider_or_model(self):
        secrets = (
            "raw-message-secret",
            "chained-cause-secret",
            "provider-secret",
            "model-secret",
        )
        try:
            raise RuntimeError(secrets[1])
        except RuntimeError as cause:
            try:
                raise LLMAuthenticationError(
                    secrets[0],
                    provider=secrets[2],
                    model=secrets[3],
                    role="judge",
                ) from cause
            except LLMAuthenticationError as error:
                response = present_llm_failure(error)

        rendered = response.model_dump_json()
        for secret in secrets:
            with self.subTest(secret=secret):
                self.assertNotIn(secret, rendered)

    def test_response_is_immutable_and_forbids_analysis_availability(self):
        response = present_llm_failure(LLMConfigurationError("raw"))

        with self.assertRaises(ValidationError):
            response.title = "changed"
        with self.assertRaises(ValidationError):
            JarvisLLMFailureResponse(
                code=LLMFailureCode.CONFIGURATION,
                title="title",
                display_message="message",
                spoken_message="speech",
                recovery_action="action",
                retryable=False,
                analysis_available=True,
            )

    def test_rejects_non_llm_exceptions(self):
        with self.assertRaisesRegex(ValueError, "requires an LLMError"):
            present_llm_failure(RuntimeError("must not be rendered"))


if __name__ == "__main__":
    unittest.main()
