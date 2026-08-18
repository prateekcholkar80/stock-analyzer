from datetime import datetime, timezone
import unittest
from unittest.mock import Mock, patch

import app.tools.market_tools as market_tools
from app.exceptions import AuthenticationError
from app.models.market import MarketQuote
from app.tools.market_tools import (
    get_current_price,
    get_market_service,
)


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
    def setUp(self):
        self.original_market_service = (
            market_tools._market_service
        )
        market_tools._market_service = None

    def tearDown(self):
        market_tools._market_service = (
            self.original_market_service
        )

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

    def test_creates_market_service_only_on_first_request(self):
        service = object()

        with patch.object(
            market_tools,
            "_create_market_service",
            return_value=service,
        ) as create_service:
            first_result = get_market_service()
            second_result = get_market_service()

        self.assertIs(first_result, service)
        self.assertIs(second_result, service)
        create_service.assert_called_once_with()

    def test_does_not_cache_failed_service_initialization(self):
        service = object()

        with patch.object(
            market_tools,
            "_create_market_service",
            side_effect=[
                AuthenticationError(
                    "Authentication unavailable"
                ),
                service,
            ],
        ) as create_service:
            with self.assertRaises(AuthenticationError):
                get_market_service()

            self.assertIsNone(
                market_tools._market_service
            )

            result = get_market_service()

        self.assertIs(result, service)
        self.assertEqual(
            create_service.call_count,
            2,
        )

    def test_created_market_service_is_initialized(self):
        gateway = object()
        service = Mock()

        with patch(
            "app.angel.client.AngelOneClient",
            return_value=gateway,
        ) as gateway_factory:
            with patch(
                "app.tools.market_tools.MarketDataService",
                return_value=service,
            ) as service_factory:
                result = market_tools._create_market_service()

        gateway_factory.assert_called_once_with()
        service_factory.assert_called_once_with(
            gateway=gateway
        )
        service.initialize.assert_called_once_with()
        self.assertIs(result, service)


if __name__ == "__main__":
    unittest.main()
