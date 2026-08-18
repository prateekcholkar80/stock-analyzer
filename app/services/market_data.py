from datetime import datetime

from pydantic import ValidationError

from app.exceptions import (
    ClientNotInitializedError,
    DataValidationError,
)
from app.gateways.market_data import (
    MarketDataGateway,
    MarketResponse,
)
from app.models.market import Candle


class MarketDataService:
    def __init__(self, gateway: MarketDataGateway):
        self.gateway = gateway
        self.initialized = False

    def initialize(self) -> None:
        self.gateway.initialize()
        self.initialized = True

    def _require_gateway(self) -> MarketDataGateway:
        if not self.initialized:
            raise ClientNotInitializedError(
                "Market data service is not initialized"
            )

        return self.gateway

    def get_ltp(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
    ) -> MarketResponse:
        gateway = self._require_gateway()

        return gateway.get_ltp(
            exchange=exchange,
            symbol_token=symbol_token,
            symbol=symbol,
        )

    def get_historical_candles(
        self,
        exchange: str,
        symbol_token: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> MarketResponse:
        gateway = self._require_gateway()

        return gateway.get_historical_candles(
            exchange=exchange,
            symbol_token=symbol_token,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )

    @staticmethod
    def convert_to_candles(candle_data) -> list[Candle]:
        try:
            return [
                Candle(
                    timestamp=datetime.fromisoformat(item[0]),
                    open=item[1],
                    high=item[2],
                    low=item[3],
                    close=item[4],
                    volume=item[5],
                )
                for item in candle_data
            ]
        except (
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise DataValidationError(
                "Historical candle data has an invalid format"
            ) from exc