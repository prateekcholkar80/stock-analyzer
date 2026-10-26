import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Event, RLock
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
_BROKER_MAX_DAYS_PER_REQUEST = {
    "ONE_MINUTE": 30,
    "THREE_MINUTE": 60,
    "FIVE_MINUTE": 100,
    "TEN_MINUTE": 100,
    "FIFTEEN_MINUTE": 200,
    "THIRTY_MINUTE": 200,
    "ONE_HOUR": 400,
    "ONE_DAY": 2_000,
}
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

    max_days_per_chunk: int | None = Field(default=None, ge=1)
    inter_request_delay_seconds: float = Field(default=1.0, ge=0)
    # 3,653 days is at least ten calendar years across leap-year layouts.
    default_lookback_days: int = Field(default=3_653, ge=1)
    # Seven calendar days normally cover five recent trading sessions
    # without requiring an exchange-holiday calendar at this layer.
    correction_overlap_days: int = Field(default=7, ge=0, le=30)


@dataclass
class _MarketPullFlight:
    completed: Event
    result: RollingFetchReceipt | None = None
    error: Exception | None = None


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
        self._flight_lock = RLock()
        self._inflight: dict[tuple[str, ...], _MarketPullFlight] = {}

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
        flight_key = (
            exchange.strip().upper(),
            symbol_token.strip(),
            symbol.strip().upper(),
            interval.strip().upper(),
            to_date.astimezone(UTC).isoformat() if to_date else "latest",
        )
        with self._flight_lock:
            flight = self._inflight.get(flight_key)
            is_leader = flight is None
            if flight is None:
                flight = _MarketPullFlight(completed=Event())
                self._inflight[flight_key] = flight

        if not is_leader:
            flight.completed.wait()
            if flight.error is not None:
                raise flight.error
            if flight.result is None:
                raise RuntimeError(
                    "completed market pull did not publish a result"
                )
            return flight.result.model_copy(deep=True)

        try:
            result = self._execute_once(
                exchange=exchange,
                symbol_token=symbol_token,
                symbol=symbol,
                interval=interval,
                to_date=to_date,
            )
        except Exception as exc:
            flight.error = exc
            raise
        else:
            flight.result = result
            return result
        finally:
            with self._flight_lock:
                self._inflight.pop(flight_key, None)
                flight.completed.set()

    def _execute_once(
        self,
        *,
        exchange: str,
        symbol_token: str,
        symbol: str,
        interval: str,
        to_date: datetime | None,
    ) -> RollingFetchReceipt:
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
        target_history_start = effective_to_date - timedelta(
            days=self.config.default_lookback_days
        )
        request_ranges = _history_request_ranges(
            previous_series=previous_series,
            resumed_from=resumed_from,
            target_history_start=target_history_start,
            effective_to_date=effective_to_date,
            correction_overlap_days=self.config.correction_overlap_days,
        )

        max_days_per_chunk = _resolved_max_days_per_chunk(
            interval,
            self.config.max_days_per_chunk,
        )
        windows = [
            window
            for request_start, request_end in request_ranges
            for window in _chunk_windows(
                request_start,
                request_end,
                max_days_per_chunk,
            )
        ]

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
            history_requested_from, history_requested_to = (
                _updated_history_coverage(
                    previous_series=previous_series,
                    target_history_start=target_history_start,
                    effective_to_date=effective_to_date,
                )
            )
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
                history_requested_from=history_requested_from,
                history_requested_to=history_requested_to,
            )
            stored = self._archive.archive_market_series(merged_series)

        if unchanged and previous_series is not None:
            coverage_from, coverage_to = _updated_history_coverage(
                previous_series=previous_series,
                target_history_start=target_history_start,
                effective_to_date=effective_to_date,
            )
            coverage_changed = (
                previous_series.history_requested_from != coverage_from
                or previous_series.history_requested_to != coverage_to
            )
            if coverage_changed:
                merged_series = previous_series.model_copy(
                    update={
                        "retrieved_at": checked_at,
                        "history_requested_from": coverage_from,
                        "history_requested_to": coverage_to,
                    }
                )
                stored = self._archive.archive_market_series(merged_series)
                unchanged = False

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
            requested_from=(
                min(start for start, _ in request_ranges)
                if request_ranges
                else None
            ),
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
            if summary.symbol == symbol
        ]
        if not candidates:
            return None, None

        latest_summary = max(
            candidates,
            key=lambda summary: (
                summary.last_candle_at or summary.stored_at,
                summary.stored_at,
            ),
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


def _history_request_ranges(
    *,
    previous_series: HistoricalCandleSeries | None,
    resumed_from: datetime | None,
    target_history_start: datetime,
    effective_to_date: datetime,
    correction_overlap_days: int,
) -> tuple[tuple[datetime, datetime], ...]:
    if previous_series is None:
        return ((target_history_start, effective_to_date),)

    ranges: list[tuple[datetime, datetime]] = []
    known_coverage_start = previous_series.history_requested_from
    if known_coverage_start is None:
        known_coverage_start = (
            previous_series.candles[0].timestamp
            if previous_series.candles
            else effective_to_date
        )
    if target_history_start < known_coverage_start:
        ranges.append((target_history_start, known_coverage_start))

    recent_anchor = resumed_from or previous_series.history_requested_to
    if recent_anchor is None:
        return ((target_history_start, effective_to_date),)
    recent_start = recent_anchor - timedelta(days=correction_overlap_days)
    ranges.append((recent_start, effective_to_date))
    return _merge_request_ranges(ranges)


def _merge_request_ranges(
    ranges: list[tuple[datetime, datetime]],
) -> tuple[tuple[datetime, datetime], ...]:
    ordered = sorted(
        (start, end) for start, end in ranges if start < end
    )
    merged: list[tuple[datetime, datetime]] = []
    for start, end in ordered:
        if not merged or start > merged[-1][1]:
            merged.append((start, end))
            continue
        previous_start, previous_end = merged[-1]
        merged[-1] = (previous_start, max(previous_end, end))
    return tuple(merged)


def _updated_history_coverage(
    *,
    previous_series: HistoricalCandleSeries | None,
    target_history_start: datetime,
    effective_to_date: datetime,
) -> tuple[datetime, datetime]:
    if (
        previous_series is None
        or previous_series.history_requested_from is None
        or previous_series.history_requested_to is None
    ):
        return target_history_start, effective_to_date
    if target_history_start < previous_series.history_requested_from:
        return (
            target_history_start,
            max(effective_to_date, previous_series.history_requested_to),
        )
    return (
        previous_series.history_requested_from,
        previous_series.history_requested_to,
    )


def _resolved_max_days_per_chunk(
    interval: str,
    configured_max_days: int | None,
) -> int:
    broker_max_days = _BROKER_MAX_DAYS_PER_REQUEST.get(interval.upper())
    if broker_max_days is None:
        raise ValueError(
            "rolling fetch interval has no configured broker range policy"
        )
    if configured_max_days is None:
        return broker_max_days
    return min(configured_max_days, broker_max_days)


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
