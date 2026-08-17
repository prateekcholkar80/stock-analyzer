from app.tools.market_tools import get_current_price


class MarketAgent:

    def get_stock_price(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str
    ):

        response = get_current_price(
            exchange,
            symbol_token,
            symbol
        )

        return {
            "symbol": symbol,
            "price": response["data"]["ltp"],
            "high": response["data"]["high"],
            "low": response["data"]["low"],
            "open": response["data"]["open"],
            "close": response["data"]["close"]
        }