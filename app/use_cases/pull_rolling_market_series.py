import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol, runtime_checkable
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field

from app.models.market import Candle, HistoricalCandleSeries, MarketQuote
from app.models.market_refresh import IntradayCandleGap
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
_INTRADAY_INTERVAL_MINUTES = {
    "ONE_MINUTE": 1,
    "THREE_MINUTE": 3,
    "FIVE_MINUTE": 5,
    "TEN_MINUTE": 10,
    "FIFTEEN_MINUTE": 15,
    "THIRTY_MINUTE": 30,
    "ONE_HOUR": 60,
}


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
    # Seven calendar days normally cover five recent trading sessions
    # without requiring an exchange-holiday calendar at this layer.
    correction_overlap_days: int = Field(default=7, ge=0, le=30)


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
        if to_date is not None and (
            to_date.tzinfo is None or to_date.utcoffset() is None
        ):
            raise ValueError("to_date must include timezone information")
        effective_to_date = to_date or datetime.now(UTC)

        previous_stored, resumed_from = self._resume_point(
            exchange,
            symbol_token,
            symbol,
            interval,
        )
        previous_series = (
            previous_stored.series if previous_stored is not None else None
        )
        from_date = (
            resumed_from
            - timedelta(days=self.config.correction_overlap_days)
            if resumed_from is not None
            else effective_to_date
            - timedelta(days=self.config.default_lookback_days)
        )

        windows = _chunk_windows(
            from_date,
            effective_to_date,
            self.config.max_days_per_chunk,
        )

        fetched_candles: list[Candle] = []
        fetched_source: str | None = None
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
            _validate_chunk_identity(
                chunk,
                exchange=exchange,
                symbol_token=symbol_token,
                symbol=symbol,
                interval=interval,
            )
            if fetched_source is None:
                fetched_source = chunk.source
            elif chunk.source != fetched_source:
                raise ValueError(
                    "rolling fetch cannot merge chunks from different sources"
                )
            fetched_candles.extend(chunk.candles)

        if (
            previous_series is not None
            and fetched_source is not None
            and previous_series.source != fetched_source
        ):
            raise ValueError(
                "rolling fetch cannot merge stored and fetched sources"
            )

        (
            merged_candles,
            new_candle_count,
            corrected_candle_count,
            deduplicated_fetched_candle_count,
        ) = _merge_candles(
            previous_series.candles if previous_series is not None else (),
            fetched_candles,
        )
        intraday_gaps = _detect_intraday_gaps(merged_candles, interval)
        checked_at = datetime.now(UTC)
        unchanged = (
            previous_stored is not None
            and new_candle_count == 0
            and corrected_candle_count == 0
        )
        if unchanged:
            stored = previous_stored
        else:
            merged_series = HistoricalCandleSeries(
                exchange=exchange,
                symbol_token=symbol_token,
                symbol=symbol,
                interval=interval,
                candles=merged_candles,
                retrieved_at=checked_at,
                source=(
                    fetched_source
                    or (
                        previous_series.source
                        if previous_series is not None
                        else "angel_one"
                    )
                ),
            )
            stored = self._archive.archive_market_series(merged_series)

        return RollingFetchReceipt(
            use_case_id=self.use_case_id,
            adapter_name=self._archive.adapter_name,
            stored=stored,
            new_candle_count=new_candle_count,
            corrected_candle_count=corrected_candle_count,
            deduplicated_fetched_candle_count=(
                deduplicated_fetched_candle_count
            ),
            chunk_request_count=len(windows),
            resumed_from=resumed_from,
            requested_from=from_date,
            requested_to=effective_to_date,
            checked_at=checked_at,
            reused_existing_dataset=unchanged,
            intraday_gaps=intraday_gaps,
        )

    def get_latest_quote(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
    ) -> MarketQuote:
        """Return a timestamped broker quote without treating it as a candle."""
        return self._market_data_service.get_quote(
            exchange=exchange,
            symbol_token=symbol_token,
            symbol=symbol,
        )

    def _resume_point(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
        interval: str,
    ) -> tuple[StoredMarketSeries | None, datetime | None]:
        summaries = self._archive.list_market_series(
            MarketSeriesQuery(
                exchange=exchange,
                symbol_token=symbol_token,
                interval=interval,
            )
        )
        candidates = [
            summary
            for summary in summaries
            if summary.last_candle_at and summary.symbol == symbol
        ]
        if not candidates:
            return None, None

        latest_summary = max(
            candidates,
            key=lambda summary: summary.last_candle_at,
        )
        stored = self._archive.get_market_series(latest_summary.dataset_id)
        return stored, latest_summary.last_candle_at


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
) -> tuple[list[Candle], int, int, int]:
    previous_by_timestamp: dict[datetime, Candle] = {
        candle.timestamp: candle for candle in previous_candles
    }
    fetched_by_timestamp: dict[datetime, Candle] = {}
    for candle in fetched_candles:
        fetched_by_timestamp[candle.timestamp] = candle

    new_candle_count = len(
        set(fetched_by_timestamp) - set(previous_by_timestamp)
    )
    corrected_candle_count = sum(
        1
        for timestamp, candle in fetched_by_timestamp.items()
        if timestamp in previous_by_timestamp
        and previous_by_timestamp[timestamp] != candle
    )
    deduplicated_count = len(fetched_candles) - len(fetched_by_timestamp)
    merged = previous_by_timestamp | fetched_by_timestamp
    ordered = sorted(merged.values(), key=lambda candle: candle.timestamp)
    return (
        ordered,
        new_candle_count,
        corrected_candle_count,
        deduplicated_count,
    )


def _validate_chunk_identity(
    chunk: HistoricalCandleSeries,
    *,
    exchange: str,
    symbol_token: str,
    symbol: str,
    interval: str,
) -> None:
    if (
        chunk.exchange,
        chunk.symbol_token,
        chunk.symbol,
        chunk.interval,
    ) != (exchange, symbol_token, symbol, interval):
        raise ValueError(
            "rolling fetch chunk does not match the requested instrument"
        )


def _detect_intraday_gaps(
    candles: list[Candle],
    interval: str,
) -> tuple[IntradayCandleGap, ...]:
    cadence_minutes = _INTRADAY_INTERVAL_MINUTES.get(interval.upper())
    if cadence_minutes is None:
        return ()

    cadence = timedelta(minutes=cadence_minutes)
    gaps: list[IntradayCandleGap] = []
    for previous, current in zip(candles, candles[1:]):
        previous_ist = previous.timestamp.astimezone(_EXCHANGE_TIMEZONE)
        current_ist = current.timestamp.astimezone(_EXCHANGE_TIMEZONE)
        if previous_ist.date() != current_ist.date():
            continue
        elapsed = current.timestamp - previous.timestamp
        if elapsed <= cadence:
            continue
        missing_count = int(elapsed // cadence) - 1
        if missing_count < 1:
            continue
        gaps.append(
            IntradayCandleGap(
                interval=interval,
                gap_after=previous.timestamp,
                resumes_at=current.timestamp,
                cadence_minutes=cadence_minutes,
                missing_candle_count=missing_count,
            )
        )
    return tuple(gaps)
