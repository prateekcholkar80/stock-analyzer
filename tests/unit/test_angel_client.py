import unittest

from app.angel.client import AngelOneClient
from app.config import Settings
from app.exceptions import (
    AuthenticationError,
    ClientNotInitializedError,
    MarketDataError,
)


class FakeSmartConnect:
    def __init__(self, api_key):
        self.api_key = api_key
        self.login_arguments = None
        self.quote_arguments = None

    def generateSession(
        self,
        client_code,
        pin,
        totp,
    ):
        self.login_arguments = {
            "client_code": client_code,
            "pin": pin,
            "totp": totp,
        }

        return {
            "status": True,
            "data": {
                "jwtToken": "test-token",
            },
        }

    def ltpData(
        self,
        exchange,
        symbol,
        symbol_token,
    ):
        self.quote_arguments = {
            "exchange": exchange,
            "symbol": symbol,
            "symbol_token": symbol_token,
        }

        return {
            "status": True,
            "data": {
                "ltp": 1320.0,
            },
        }

    def getCandleData(self, params):
        return {
            "status": True,
            "data": [],
        }


class RejectedSmartConnect(FakeSmartConnect):
    def generateSession(
        self,
        client_code,
        pin,
        totp,
    ):
        return {
            "status": False,
            "message": "Invalid credentials",
        }


class FailingQuoteSmartConnect(FakeSmartConnect):
    def ltpData(
        self,
        exchange,
        symbol,
        symbol_token,
    ):
        raise RuntimeError(
            "api_key=vendor-secret"
        )


class AngelOneClientTests(unittest.TestCase):
    def setUp(self):
        self.settings = Settings.from_environment(
            {
                "ANGEL_API_KEY": "test-api-key",
                "ANGEL_CLIENT_CODE": "test-client-code",
                "ANGEL_PIN": "1234",
                "ANGEL_TOTP_SECRET": "JBSWY3DPEHPK3PXP",
            }
        )

    def test_initializes_with_injected_sdk_factory(self):
        client = AngelOneClient(
            settings=self.settings,
            client_factory=FakeSmartConnect,
        )

        client.initialize()

        self.assertIsNotNone(client.session)
        self.assertEqual(
            client.client.api_key,
            "test-api-key",
        )

    def test_rejects_market_request_before_login(self):
        client = AngelOneClient(
            settings=self.settings,
            client_factory=FakeSmartConnect,
        )

        with self.assertRaises(ClientNotInitializedError):
            client.get_ltp(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
            )

    def test_delegates_quote_request_to_sdk(self):
        client = AngelOneClient(
            settings=self.settings,
            client_factory=FakeSmartConnect,
        )
        client.initialize()

        response = client.get_ltp(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
        )

        self.assertEqual(
            response["data"]["ltp"],
            1320.0,
        )
        self.assertEqual(
            client.client.quote_arguments,
            {
                "exchange": "NSE",
                "symbol": "RELIANCE-EQ",
                "symbol_token": "2885",
            },
        )

    def test_raises_authentication_error_when_login_is_rejected(self):
        client = AngelOneClient(
            settings=self.settings,
            client_factory=RejectedSmartConnect,
        )

        with self.assertRaises(AuthenticationError):
            client.initialize()

    def test_logs_authentication_lifecycle_without_secrets(self):
        client = AngelOneClient(
            settings=self.settings,
            client_factory=FakeSmartConnect,
        )

        with self.assertLogs(
            "jarvis.angel.client",
            level="INFO",
        ) as captured:
            client.initialize()

        events = [
            getattr(record, "event", None)
            for record in captured.records
        ]
        messages = " ".join(
            record.getMessage()
            for record in captured.records
        )

        self.assertIn(
            "angel.authentication.started",
            events,
        )
        self.assertIn(
            "angel.authentication.succeeded",
            events,
        )
        self.assertNotIn(
            "test-api-key",
            messages,
        )
        self.assertNotIn(
            "test-client-code",
            messages,
        )
        self.assertNotIn(
            "1234",
            messages,
        )

    def test_logs_quote_failure_without_vendor_message(self):
        client = AngelOneClient(
            settings=self.settings,
            client_factory=FailingQuoteSmartConnect,
        )
        client.initialize()

        with self.assertLogs(
            "jarvis.angel.client",
            level="ERROR",
        ) as captured:
            with self.assertRaises(MarketDataError):
                client.get_ltp(
                    exchange="NSE",
                    symbol_token="2885",
                    symbol="RELIANCE-EQ",
                )

        self.assertEqual(
            captured.records[0].event,
            "angel.quote.failed",
        )
        self.assertEqual(
            captured.records[0].error_type,
            "RuntimeError",
        )
        self.assertNotIn(
            "vendor-secret",
            captured.records[0].getMessage(),
        )


if __name__ == "__main__":
    unittest.main()
