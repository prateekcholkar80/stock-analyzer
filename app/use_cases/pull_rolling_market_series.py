import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field

from app.models.market import Candle, HistoricalCandleSeries
from app.models.storage import (
    MarketSeriesQuery,
    MarketSeriesSummary,
    RollingFetchReceipt,
    StoredMarketSeries,
)
from app.models.technical import TechnicalModel
from app.services.market_data import MarketDataService


_EXCHANGE_TIMEZONE = ZoneInfo("Asia/Kolkata")
_ANGEL_DATE_FORMAT = "%Y-%m-%d %H:%M"


@runtime_checkable
class MarketSeriesArchive(Protocol):
    """Narrow port for resuming from and persisting a rolling pull."""

    @property
    def adapter_name(self) -> str:
        ...

    def list_market_series(
        self,
        query: MarketSeriesQuery | None = None,
    ) -> tuple[MarketSeriesSummary, ...]:
        ...

    def get_market_series(
        self,
        dataset_id: str,
    ) -> StoredMarketSeries | None:
        ...

    def archive_market_series(
        self,
        series: HistoricalCandleSeries,
        *,
        dataset_id: str | None = None,
        stored_at: datetime | None = None,
    ) -> StoredMarketSeries:
        ...


class RollingFetchConfig(TechnicalModel):
    """Conservative, adjustable chunking/rate-limit settings.

    These day/second values are estimates, not values verified against
    Angel One's current SmartAPI documentation -- tune them to your
    account's actual per-interval range caps and request rate limits.
    """

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    max_days_per_chunk: int = Field(default=30, ge=1)
    inter_request_delay_seconds: float = Field(default=1.0, ge=0)
    default_lookback_days: int = Field(default=365, ge=1)


class PullRollingMarketSeries:
    """Fetch new candles since the last stored run, chunked and merged."""

    use_case_id = "jarvis.pull_rolling_market_series.v1"

    def __init__(
        self,
        market_data_service: MarketDataService,
        archive: MarketSeriesArchive,
        config: RollingFetchConfig | None = None,
        sleep_fn: Callable[[float], None] = time.sleep,
    ) -> None:
        if not isinstance(archive, MarketSeriesArchive):
            raise ValueError(
                "rolling fetch requires an archive implementing "
                "MarketSeriesArchive"
            )
        self._market_data_service = market_data_service
        self._archive = archive
        self.config = config or RollingFetchConfig()
        self._sleep_fn = sleep_fn

    def execute(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
        interval: str = "ONE_HOUR",
        *,
        to_date: datetime | None = None,
    ) -> RollingFetchReceipt:
        if to_date is not None and to_date.tzinfo is None:
            raise ValueError("to_date must include timezone information")
        effective_to_date = to_date or datetime.now(UTC)

        previous_series, resumed_from = self._resume_point(
            exchange,
            symbol_token,
            interval,
        )
        from_date = resumed_from or (
            effective_to_date
            - timedelta(days=self.config.default_lookback_days)
        )

        windows = _chunk_windows(
            from_date,
            effective_to_date,
            self.config.max_days_per_chunk,
        )

        fetched_candles: list[Candle] = []
        for index, (window_start, window_end) in enumerate(windows):
            if index > 0:
                self._sleep_fn(self.config.inter_request_delay_seconds)
            chunk = self._market_data_service.get_historical_series(
                exchange=exchange,
                symbol_token=symbol_token,
                symbol=symbol,
                interval=interval,
                from_date=window_start.astimezone(
                    _EXCHANGE_TIMEZONE
                ).strftime(_ANGEL_DATE_FORMAT),
                to_date=window_end.astimezone(
                    _EXCHANGE_TIMEZONE
                ).strftime(_ANGEL_DATE_FORMAT),
            )
            fetched_candles.extend(chunk.candles)

        merged_candles, new_candle_count = _merge_candles(
            previous_series.candles if previous_series is not None else (),
            fetched_candles,
        )

        merged_series = HistoricalCandleSeries(
            exchange=exchange,
            symbol_token=symbol_token,
            symbol=symbol,
            interval=interval,
            candles=merged_candles,
            retrieved_at=datetime.now(UTC),
        )
        stored = self._archive.archive_market_series(merged_series)

        return RollingFetchReceipt(
            use_case_id=self.use_case_id,
            adapter_name=self._archive.adapter_name,
            stored=stored,
            new_candle_count=new_candle_count,
            chunk_request_count=len(windows),
            resumed_from=resumed_from,
        )

    def _resume_point(
        self,
        exchange: str,
        symbol_token: str,
        interval: str,
    ) -> tuple[HistoricalCandleSeries | None, datetime | None]:
        summaries = self._archive.list_market_series(
            MarketSeriesQuery(
                exchange=exchange,
                symbol_token=symbol_token,
                interval=interval,
            )
        )
        candidates = [
            summary for summary in summaries if summary.last_candle_at
        ]
        if not candidates:
            return None, None

        latest_summary = max(
            candidates,
            key=lambda summary: summary.last_candle_at,
        )
        stored = self._archive.get_market_series(latest_summary.dataset_id)
        previous = stored.series if stored is not None else None
        return previous, latest_summary.last_candle_at


def _chunk_windows(
    from_date: datetime,
    to_date: datetime,
    max_days_per_chunk: int,
) -> list[tuple[datetime, datetime]]:
    windows: list[tuple[datetime, datetime]] = []
    step = timedelta(days=max_days_per_chunk)
    current_start = from_date
    while current_start < to_date:
        current_end = min(current_start + step, to_date)
        windows.append((current_start, current_end))
        current_start = current_end
    return windows


def _merge_candles(
    previous_candles,
    fetched_candles: list[Candle],
) -> tuple[list[Candle], int]:
    merged: dict[datetime, Candle] = {
        candle.timestamp: candle for candle in previous_candles
    }
    new_candle_count = 0
    for candle in fetched_candles:
        if candle.timestamp not in merged:
            new_candle_count += 1
        merged[candle.timestamp] = candle
    ordered = sorted(merged.values(), key=lambda candle: candle.timestamp)
    return ordered, new_candle_count
