from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from app.models.market import MarketQuote
from app.tools.market_tools import get_current_price


class FakeMarketDataService:
    def __init__(self, quote):
        self.quote = quote
        self.last_request = None

    def get_quote(
        self,
        exchange,
        symbol_token,
        symbol,
    ):
        self.last_request = {
            "exchange": exchange,
            "symbol_token": symbol_token,
            "symbol": symbol,
        }

        return self.quote


class MarketToolsTests(unittest.TestCase):
    def test_returns_typed_quote_from_market_service(self):
        quote = MarketQuote(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            price=1320.0,
            open=1314.0,
            high=1320.8,
            low=1298.1,
            previous_close=1305.0,
            observed_at=datetime(
                2026,
                8,
                17,
                10,
                0,
                tzinfo=timezone.utc,
            ),
        )
        service = FakeMarketDataService(quote=quote)

        with patch(
            "app.tools.market_tools.get_market_service",
            return_value=service,
        ):
            result = get_current_price(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
            )

        self.assertIs(result, quote)
        self.assertIsInstance(result, MarketQuote)
        self.assertEqual(
            service.last_request,
            {
                "exchange": "NSE",
                "symbol_token": "2885",
                "symbol": "RELIANCE-EQ",
            },
        )


if __name__ == "__main__":
    unittest.main()