import unittest

from app.exceptions import (
    ApplicationError,
    AuthenticationError,
    ClientNotInitializedError,
    ExternalServiceError,
    IndicatorCalculationError,
    InsufficientDataError,
    MarketDataError,
    STTAuthenticationError,
    STTConfigurationError,
    STTError,
    STTProviderUnavailableError,
    STTTranscriptionError,
    TechnicalAnalysisError,
    TTSAuthenticationError,
    TTSConfigurationError,
    TTSError,
    TTSProviderUnavailableError,
    TTSSynthesisError,
)


class ExceptionHierarchyTests(unittest.TestCase):
    def test_authentication_error_is_an_external_service_error(self):
        error = AuthenticationError("Authentication failed")

        self.assertIsInstance(error, ExternalServiceError)
        self.assertIsInstance(error, ApplicationError)
        self.assertEqual(str(error), "Authentication failed")

    def test_client_initialization_error_is_a_market_data_error(self):
        error = ClientNotInitializedError("Client is not initialized")

        self.assertIsInstance(error, MarketDataError)
        self.assertIsInstance(error, ExternalServiceError)
        self.assertIsInstance(error, ApplicationError)

    def test_technical_analysis_error_is_an_application_error(self):
        self.assertTrue(
            issubclass(
                TechnicalAnalysisError,
                ApplicationError,
            )
        )

    def test_insufficient_data_is_a_technical_analysis_error(self):
        self.assertTrue(
            issubclass(
                InsufficientDataError,
                TechnicalAnalysisError,
            )
        )

    def test_indicator_calculation_is_a_technical_analysis_error(self):
        self.assertTrue(
            issubclass(
                IndicatorCalculationError,
                TechnicalAnalysisError,
            )
        )

    def test_tts_error_subtypes_are_external_service_errors(self):
        for error_type in (
            TTSConfigurationError,
            TTSAuthenticationError,
            TTSProviderUnavailableError,
            TTSSynthesisError,
        ):
            with self.subTest(error_type=error_type):
                self.assertTrue(issubclass(error_type, TTSError))
                self.assertTrue(issubclass(error_type, ExternalServiceError))
                self.assertTrue(issubclass(error_type, ApplicationError))

    def test_tts_provider_unavailable_defaults_to_retryable(self):
        error = TTSProviderUnavailableError("provider is down")

        self.assertTrue(error.context.retryable)

    def test_tts_configuration_error_defaults_to_not_retryable(self):
        error = TTSConfigurationError("bad configuration")

        self.assertFalse(error.context.retryable)

    def test_tts_failure_context_carries_sanitized_identity(self):
        error = TTSAuthenticationError(
            "credential rejected",
            provider="google",
            voice="en-GB-Neural2-B",
            operation_id="operation-1",
        )

        self.assertEqual(error.context.provider, "google")
        self.assertEqual(error.context.voice, "en-GB-Neural2-B")
        self.assertEqual(error.context.operation_id, "operation-1")

    def test_stt_error_subtypes_are_external_service_errors(self):
        for error_type in (
            STTConfigurationError,
            STTAuthenticationError,
            STTProviderUnavailableError,
            STTTranscriptionError,
        ):
            with self.subTest(error_type=error_type):
                self.assertTrue(issubclass(error_type, STTError))
                self.assertTrue(issubclass(error_type, ExternalServiceError))
                self.assertTrue(issubclass(error_type, ApplicationError))

    def test_stt_provider_unavailable_defaults_to_retryable(self):
        error = STTProviderUnavailableError("provider is down")

        self.assertTrue(error.context.retryable)

    def test_stt_configuration_error_defaults_to_not_retryable(self):
        error = STTConfigurationError("bad configuration")

        self.assertFalse(error.context.retryable)

    def test_stt_failure_context_carries_sanitized_identity(self):
        error = STTAuthenticationError(
            "credential rejected",
            provider="google",
            language_code="en-IN",
            operation_id="operation-1",
        )

        self.assertEqual(error.context.provider, "google")
        self.assertEqual(error.context.language_code, "en-IN")
        self.assertEqual(error.context.operation_id, "operation-1")


if __name__ == "__main__":
    unittest.main()
