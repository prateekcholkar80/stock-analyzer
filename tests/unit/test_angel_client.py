import unittest

from app.angel.client import AngelOneClient
from app.config import Settings
from app.exceptions import (
    AuthenticationError,
    ClientNotInitializedError,
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


if __name__ == "__main__":
    unittest.main()