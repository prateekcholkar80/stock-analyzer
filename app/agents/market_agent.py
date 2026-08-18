from app.models.market import MarketQuote
from app.tools.market_tools import get_current_price


class MarketAgent:
    def get_stock_price(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
    ) -> MarketQuote:
        return get_current_price(
            exchange=exchange,
            symbol_token=symbol_token,
            symbol=symbol,
        )