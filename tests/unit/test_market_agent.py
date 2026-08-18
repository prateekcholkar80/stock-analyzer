from datetime import datetime, timezone
import unittest
from unittest.mock import patch

from app.agents.market_agent import MarketAgent
from app.models.market import MarketQuote


class MarketAgentTests(unittest.TestCase):
    def test_returns_typed_quote_from_market_tool(self):
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
        agent = MarketAgent()

        with patch(
            "app.agents.market_agent.get_current_price",
            return_value=quote,
        ) as mocked_tool:
            result = agent.get_stock_price(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
            )

        self.assertIs(result, quote)
        self.assertIsInstance(result, MarketQuote)
        mocked_tool.assert_called_once_with(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
        )


if __name__ == "__main__":
    unittest.main()