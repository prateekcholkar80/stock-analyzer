import unittest

from app.exceptions import ClientNotInitializedError
from app.services.market_data import MarketDataService


class FakeMarketDataGateway:
    def __init__(self):
        self.initialized = False
        self.last_quote_request = None
        self.last_history_request = None

    def initialize(self):
        self.initialized = True

    def get_ltp(
        self,
        exchange,
        symbol_token,
        symbol,
    ):
        self.last_quote_request = {
            "exchange": exchange,
            "symbol_token": symbol_token,
            "symbol": symbol,
        }

        return {
            "status": True,
            "data": {
                "ltp": 1320.0,
            },
        }

    def get_historical_candles(
        self,
        exchange,
        symbol_token,
        interval,
        from_date,
        to_date,
    ):
        self.last_history_request = {
            "exchange": exchange,
            "symbol_token": symbol_token,
            "interval": interval,
            "from_date": from_date,
            "to_date": to_date,
        }

        return {
            "status": True,
            "data": [],
        }


class MarketDataServiceTests(unittest.TestCase):
    def setUp(self):
        self.gateway = FakeMarketDataGateway()
        self.service = MarketDataService(gateway=self.gateway)

    def test_initializes_injected_gateway(self):
        self.service.initialize()

        self.assertTrue(self.gateway.initialized)
        self.assertTrue(self.service.initialized)

    def test_rejects_request_before_initialization(self):
        with self.assertRaises(ClientNotInitializedError):
            self.service.get_ltp(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
            )

    def test_delegates_quote_request_to_gateway(self):
        self.service.initialize()

        response = self.service.get_ltp(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
        )

        self.assertEqual(response["data"]["ltp"], 1320.0)
        self.assertEqual(
            self.gateway.last_quote_request,
            {
                "exchange": "NSE",
                "symbol_token": "2885",
                "symbol": "RELIANCE-EQ",
            },
        )

    def test_delegates_history_request_to_gateway(self):
        self.service.initialize()

        response = self.service.get_historical_candles(
            exchange="NSE",
            symbol_token="2885",
            interval="ONE_DAY",
            from_date="2026-08-01 09:15",
            to_date="2026-08-17 15:30",
        )

        self.assertEqual(response["data"], [])
        self.assertEqual(
            self.gateway.last_history_request,
            {
                "exchange": "NSE",
                "symbol_token": "2885",
                "interval": "ONE_DAY",
                "from_date": "2026-08-01 09:15",
                "to_date": "2026-08-17 15:30",
            },
        )


if __name__ == "__main__":
    unittest.main()