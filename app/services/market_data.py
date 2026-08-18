from datetime import datetime, timezone
from time import perf_counter

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
from app.logging_config import (
    get_logger,
    get_operation_id,
    operation_context,
)


logger = get_logger(__name__)


def _duration_ms(started_at: float) -> float:
    return round(
        (perf_counter() - started_at) * 1000,
        3,
    )


class MarketDataService:
    def __init__(self, gateway: MarketDataGateway):
        self.gateway = gateway
        self.initialized = False

    def initialize(self) -> None:
        with operation_context(get_operation_id()):
            started_at = perf_counter()

            logger.info(
                "Market data service initialization started",
                extra={
                    "event": "market.service.initialization.started",
                },
            )

            try:
                self.gateway.initialize()
            except Exception as exc:
                logger.error(
                    "Market data service initialization failed",
                    extra={
                        "event": "market.service.initialization.failed",
                        "duration_ms": _duration_ms(started_at),
                        "error_type": type(exc).__name__,
                    },
                )
                raise

            self.initialized = True

            logger.info(
                "Market data service initialization succeeded",
                extra={
                    "event": "market.service.initialization.succeeded",
                    "duration_ms": _duration_ms(started_at),
                },
            )

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
        with operation_context(get_operation_id()):
            started_at = perf_counter()

            logger.debug(
                "Market quote processing started",
                extra={
                    "event": "market.quote.started",
                    "exchange": exchange,
                    "symbol": symbol,
                    "symbol_token": symbol_token,
                },
            )

            try:
                gateway = self._require_gateway()

                response = gateway.get_ltp(
                    exchange=exchange,
                    symbol_token=symbol_token,
                    symbol=symbol,
                )

                quote = self.convert_to_quote(
                    response=response,
                    exchange=exchange,
                    symbol_token=symbol_token,
                    symbol=symbol,
                    observed_at=observed_at,
                )
            except Exception as exc:
                logger.error(
                    "Market quote processing failed",
                    extra={
                        "event": "market.quote.failed",
                        "exchange": exchange,
                        "symbol": symbol,
                        "symbol_token": symbol_token,
                        "duration_ms": _duration_ms(started_at),
                        "error_type": type(exc).__name__,
                    },
                )
                raise

            logger.debug(
                "Market quote processing succeeded",
                extra={
                    "event": "market.quote.succeeded",
                    "exchange": exchange,
                    "symbol": symbol,
                    "symbol_token": symbol_token,
                    "duration_ms": _duration_ms(started_at),
                },
            )

            return quote

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
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
        interval: str,
        from_date: str,
        to_date: str,
        retrieved_at: datetime | None = None,
    ) -> HistoricalCandleSeries:
        with operation_context(get_operation_id()):
            started_at = perf_counter()

            logger.debug(
                "Historical market-data processing started",
                extra={
                    "event": "market.history.started",
                    "exchange": exchange,
                    "symbol": symbol,
                    "symbol_token": symbol_token,
                    "interval": interval,
                    "from_date": from_date,
                    "to_date": to_date,
                },
            )

            try:
                gateway = self._require_gateway()

                response = gateway.get_historical_candles(
                    exchange=exchange,
                    symbol_token=symbol_token,
                    interval=interval,
                    from_date=from_date,
                    to_date=to_date,
                )

                series = self.convert_to_historical_series(
                    response=response,
                    exchange=exchange,
                    symbol_token=symbol_token,
                    symbol=symbol,
                    interval=interval,
                    retrieved_at=retrieved_at,
                )
            except Exception as exc:
                logger.error(
                    "Historical market-data processing failed",
                    extra={
                        "event": "market.history.failed",
                        "exchange": exchange,
                        "symbol": symbol,
                        "symbol_token": symbol_token,
                        "interval": interval,
                        "duration_ms": _duration_ms(started_at),
                        "error_type": type(exc).__name__,
                    },
                )
                raise

            logger.debug(
                "Historical market-data processing succeeded",
                extra={
                    "event": "market.history.succeeded",
                    "exchange": exchange,
                    "symbol": symbol,
                    "symbol_token": symbol_token,
                    "interval": interval,
                    "candle_count": len(series.candles),
                    "duration_ms": _duration_ms(started_at),
                },
            )

            return series

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
