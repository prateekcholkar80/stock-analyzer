from datetime import datetime, timezone

from pydantic import ValidationError

from app.exceptions import (
    ClientNotInitializedError,
    DataValidationError,
    MarketDataError,
)
from app.gateways.market_data import (
    MarketDataGateway,
    MarketResponse,
)
from app.models.market import (
    Candle,
    HistoricalCandleSeries,
    MarketQuote,
)


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

    def get_quote(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
        observed_at: datetime | None = None,
    ) -> MarketQuote:
        gateway = self._require_gateway()

        response = gateway.get_ltp(
            exchange=exchange,
            symbol_token=symbol_token,
            symbol=symbol,
        )

        return self.convert_to_quote(
            response=response,
            exchange=exchange,
            symbol_token=symbol_token,
            symbol=symbol,
            observed_at=observed_at,
        )

    @staticmethod
    def convert_to_quote(
        response: MarketResponse,
        exchange: str,
        symbol_token: str,
        symbol: str,
        observed_at: datetime | None = None,
    ) -> MarketQuote:
        if (
            not isinstance(response, dict)
            or response.get("status") is not True
        ):
            raise MarketDataError(
                "Market quote request was not successful"
            )

        data = response.get("data")

        if not isinstance(data, dict):
            raise DataValidationError(
                "Market quote response does not contain valid data"
            )

        observation_time = observed_at or datetime.now(timezone.utc)

        try:
            return MarketQuote(
                exchange=exchange,
                symbol_token=symbol_token,
                symbol=symbol,
                price=data["ltp"],
                open=data["open"],
                high=data["high"],
                low=data["low"],
                previous_close=data["close"],
                observed_at=observation_time,
            )
        except (
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise DataValidationError(
                "Market quote response has an invalid format"
            ) from exc

    def get_historical_series(
        self, exchange: str, symbol_token: str, symbol: str, interval: str, from_date: str,
        to_date: str, retrieved_at: datetime | None = None,) -> HistoricalCandleSeries:
        gateway = self._require_gateway()

        response = gateway.get_historical_candles(
            exchange=exchange,
            symbol_token=symbol_token,
            interval=interval,
            from_date=from_date,
            to_date=to_date,
        )

        return self.convert_to_historical_series(
            response=response,
            exchange=exchange,
            symbol_token=symbol_token,
            symbol=symbol,
            interval=interval,
            retrieved_at=retrieved_at,
        )
    
    @classmethod
    def convert_to_historical_series(
        cls,
        response: MarketResponse,
        exchange: str,
        symbol_token: str,
        symbol: str,
        interval: str,
        retrieved_at: datetime | None = None,
    ) -> HistoricalCandleSeries:
        if (
            not isinstance(response, dict)
            or response.get("status") is not True
        ):
            raise MarketDataError(
                "Historical market-data request was not successful"
            )

        candle_data = response.get("data")

        if not isinstance(candle_data, list):
            raise DataValidationError(
                "Historical response does not contain a candle list"
            )

        candles = cls.convert_to_candles(candle_data)
        retrieval_time = retrieved_at or datetime.now(timezone.utc)

        try:
            return HistoricalCandleSeries(
                exchange=exchange,
                symbol_token=symbol_token,
                symbol=symbol,
                interval=interval,
                candles=candles,
                retrieved_at=retrieval_time,
            )
        except ValidationError as exc:
            raise DataValidationError(
                "Historical series metadata has an invalid format"
            ) from exc

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