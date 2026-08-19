from datetime import datetime
from hashlib import sha256
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from app.models.backtest import WalkForwardBacktestResult
from app.models.market import HistoricalCandleSeries


_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"


def payload_fingerprint(payload: BaseModel) -> str:
    """Return a stable SHA-256 digest for a validated domain aggregate."""
    return sha256(payload.model_dump_json().encode("utf-8")).hexdigest()


class StorageModel(BaseModel):
    """Base type for database-neutral persistence contracts."""

    model_config = ConfigDict(frozen=True)


class StoredMarketSeries(StorageModel):
    dataset_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    )
    payload_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    stored_at: datetime
    series: HistoricalCandleSeries

    @field_validator("stored_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("storage timestamps must include timezone")
        return value

    @model_validator(mode="after")
    def validate_fingerprint(self) -> Self:
        if self.payload_fingerprint != payload_fingerprint(self.series):
            raise ValueError(
                "market-series fingerprint must match its payload"
            )
        return self


class StoredBacktestRun(StorageModel):
    run_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    )
    result_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    market_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    stored_at: datetime
    result: WalkForwardBacktestResult

    @field_validator("stored_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("storage timestamps must include timezone")
        return value

    @model_validator(mode="after")
    def validate_fingerprints(self) -> Self:
        if self.result_fingerprint != payload_fingerprint(self.result):
            raise ValueError(
                "backtest fingerprint must match its result payload"
            )
        if self.market_fingerprint != payload_fingerprint(
            self.result.market_series
        ):
            raise ValueError(
                "backtest market fingerprint must match its source series"
            )
        return self


class MarketSeriesSummary(StorageModel):
    dataset_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    payload_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    stored_at: datetime
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    source: str = Field(min_length=1)
    retrieved_at: datetime
    candle_count: int = Field(ge=0)
    first_candle_at: datetime | None = None
    last_candle_at: datetime | None = None


class BacktestRunSummary(StorageModel):
    run_id: str = Field(pattern=_IDENTIFIER_PATTERN)
    result_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    market_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    stored_at: datetime
    backtest_id: str = Field(min_length=1)
    engine_id: str = Field(min_length=1)
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    candle_count: int = Field(ge=0)
    evaluation_count: int = Field(ge=0)
    entered_trades: int = Field(ge=0)
    final_equity: float
    total_return_percentage: float


class StorageQuery(StorageModel):
    stored_from: datetime | None = None
    stored_to: datetime | None = None
    limit: int = Field(default=100, ge=1, le=1_000)
    offset: int = Field(default=0, ge=0)

    @field_validator("stored_from", "stored_to")
    @classmethod
    def require_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("storage query times must include timezone")
        return value

    @field_validator("limit", "offset", mode="before")
    @classmethod
    def require_integer_paging(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("storage pagination values must be integers")
        return value

    @model_validator(mode="after")
    def validate_time_range(self) -> Self:
        if (
            self.stored_from is not None
            and self.stored_to is not None
            and self.stored_from > self.stored_to
        ):
            raise ValueError("storage query start cannot follow its end")
        return self


class MarketSeriesQuery(StorageQuery):
    exchange: str | None = Field(default=None, min_length=1)
    symbol_token: str | None = Field(default=None, min_length=1)
    interval: str | None = Field(default=None, min_length=1)
    source: str | None = Field(default=None, min_length=1)


class BacktestRunQuery(StorageQuery):
    backtest_id: str | None = Field(default=None, min_length=1)
    engine_id: str | None = Field(default=None, min_length=1)
    exchange: str | None = Field(default=None, min_length=1)
    symbol_token: str | None = Field(default=None, min_length=1)
    interval: str | None = Field(default=None, min_length=1)


def stored_market_series(
    series: HistoricalCandleSeries,
    *,
    stored_at: datetime,
    dataset_id: str | None = None,
) -> StoredMarketSeries:
    fingerprint = payload_fingerprint(series)
    return StoredMarketSeries(
        dataset_id=dataset_id or f"market:{fingerprint}",
        payload_fingerprint=fingerprint,
        stored_at=stored_at,
        series=series,
    )


def stored_backtest_run(
    result: WalkForwardBacktestResult,
    *,
    stored_at: datetime,
    run_id: str | None = None,
) -> StoredBacktestRun:
    result_fingerprint = payload_fingerprint(result)
    return StoredBacktestRun(
        run_id=run_id or f"backtest:{result_fingerprint}",
        result_fingerprint=result_fingerprint,
        market_fingerprint=payload_fingerprint(result.market_series),
        stored_at=stored_at,
        result=result,
    )


def market_series_summary(
    stored: StoredMarketSeries,
) -> MarketSeriesSummary:
    candles = stored.series.candles
    return MarketSeriesSummary(
        dataset_id=stored.dataset_id,
        payload_fingerprint=stored.payload_fingerprint,
        stored_at=stored.stored_at,
        exchange=stored.series.exchange,
        symbol_token=stored.series.symbol_token,
        symbol=stored.series.symbol,
        interval=stored.series.interval,
        source=stored.series.source,
        retrieved_at=stored.series.retrieved_at,
        candle_count=len(candles),
        first_candle_at=candles[0].timestamp if candles else None,
        last_candle_at=candles[-1].timestamp if candles else None,
    )


def backtest_run_summary(
    stored: StoredBacktestRun,
) -> BacktestRunSummary:
    result = stored.result
    series = result.market_series
    return BacktestRunSummary(
        run_id=stored.run_id,
        result_fingerprint=stored.result_fingerprint,
        market_fingerprint=stored.market_fingerprint,
        stored_at=stored.stored_at,
        backtest_id=result.backtest_id,
        engine_id=result.engine_id,
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        candle_count=len(series.candles),
        evaluation_count=len(result.evaluations),
        entered_trades=result.performance.entered_trades,
        final_equity=result.performance.final_equity,
        total_return_percentage=(
            result.performance.total_return_percentage
        ),
    )
