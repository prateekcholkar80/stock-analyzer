import unittest

from app.exceptions import (
    ApplicationError,
    AuthenticationError,
    ClientNotInitializedError,
    ExternalServiceError,
    IndicatorCalculationError,
    InsufficientDataError,
    MarketDataError,
    TechnicalAnalysisError,
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


if __name__ == "__main__":
    unittest.main()
