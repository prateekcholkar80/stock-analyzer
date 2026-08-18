import unittest

from app.exceptions import (
    ApplicationError,
    AuthenticationError,
    ClientNotInitializedError,
    ExternalServiceError,
    MarketDataError,
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


if __name__ == "__main__":
    unittest.main()