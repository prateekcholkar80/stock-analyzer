from dataclasses import FrozenInstanceError
import unittest

from app.exceptions import (
    ApplicationError,
    ExternalServiceError,
    FundamentalCapabilityUnavailableError,
    FundamentalGatewayAuthenticationError,
    FundamentalGatewayConfigurationError,
    FundamentalGatewayEntitlementError,
    FundamentalGatewayError,
    FundamentalGatewayFailureContext,
    FundamentalProviderRateLimitError,
    FundamentalProviderUnavailableError,
    FundamentalResponseValidationError,
)


class FundamentalGatewayExceptionTests(unittest.TestCase):
    def test_typed_failures_share_safe_external_service_hierarchy(self):
        failure_types = (
            FundamentalGatewayConfigurationError,
            FundamentalGatewayAuthenticationError,
            FundamentalGatewayEntitlementError,
            FundamentalCapabilityUnavailableError,
            FundamentalProviderUnavailableError,
            FundamentalProviderRateLimitError,
            FundamentalResponseValidationError,
        )

        for failure_type in failure_types:
            with self.subTest(failure_type=failure_type.__name__):
                error = failure_type("Fundamental provider request failed")
                self.assertIsInstance(error, FundamentalGatewayError)
                self.assertIsInstance(error, ExternalServiceError)
                self.assertIsInstance(error, ApplicationError)

    def test_only_transient_provider_failures_retry_by_default(self):
        transient = (
            FundamentalProviderUnavailableError("Provider unavailable"),
            FundamentalProviderRateLimitError("Provider throttled request"),
        )
        permanent = (
            FundamentalGatewayConfigurationError("Adapter unavailable"),
            FundamentalGatewayAuthenticationError("Session rejected"),
            FundamentalGatewayEntitlementError("Capability locked"),
            FundamentalResponseValidationError("Payload rejected"),
        )

        self.assertTrue(all(error.context.retryable for error in transient))
        self.assertTrue(
            all(not error.context.retryable for error in permanent)
        )

    def test_context_contains_only_sanitized_operational_identity(self):
        error = FundamentalGatewayAuthenticationError(
            "Provider rejected the user's scoped session",
            capability="  financial_statements  ",
            provider="  tijori  ",
            provider_connection_id="  provider.tijori.prateek  ",
            operation_id="  operation-123  ",
        )

        self.assertEqual(error.context.capability, "financial_statements")
        self.assertEqual(error.context.provider, "tijori")
        self.assertEqual(
            error.context.provider_connection_id,
            "provider.tijori.prateek",
        )
        self.assertEqual(error.context.operation_id, "operation-123")

    def test_context_is_immutable_and_rejects_invalid_metadata(self):
        context = FundamentalGatewayFailureContext(provider="tijori")
        with self.assertRaises(FrozenInstanceError):
            context.provider = "other"

        with self.assertRaises(ValueError):
            FundamentalGatewayFailureContext(capability="   ")
        with self.assertRaises(ValueError):
            FundamentalGatewayFailureContext(operation_id="x" * 161)
        with self.assertRaises(TypeError):
            FundamentalGatewayFailureContext(provider=123)
        with self.assertRaises(TypeError):
            FundamentalGatewayFailureContext(retryable="yes")

    def test_raw_provider_secret_can_be_chained_without_safe_message_leak(self):
        provider_secret = "session-cookie-must-not-leak"

        try:
            raise RuntimeError(
                f"authentication failed with cookie {provider_secret}"
            )
        except RuntimeError as cause:
            try:
                raise FundamentalGatewayAuthenticationError(
                    "Provider rejected the user's scoped session",
                    capability="company_overview",
                    provider="tijori",
                ) from cause
            except FundamentalGatewayAuthenticationError as error:
                captured = error

        self.assertIsInstance(captured.__cause__, RuntimeError)
        self.assertNotIn(provider_secret, str(captured))
        self.assertNotIn(provider_secret, repr(captured))
        self.assertNotIn(provider_secret, repr(captured.context))


if __name__ == "__main__":
    unittest.main()
