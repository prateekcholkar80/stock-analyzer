from typing import Any, Protocol


MarketResponse = dict[str, Any]


class MarketDataGateway(Protocol):
    def initialize(self) -> None:
        """Initialize and authenticate the market-data integration."""
        ...

    def get_ltp(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
    ) -> MarketResponse:
        """Retrieve the current quote for an instrument."""
        ...

    def get_historical_candles(
        self,
        exchange: str,
        symbol_token: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> MarketResponse:
        """Retrieve historical candle data for an instrument."""
        ...