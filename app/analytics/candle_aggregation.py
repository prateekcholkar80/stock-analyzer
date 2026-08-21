from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.models.market import Candle, HistoricalCandleSeries


_INTERVAL_RANK = {
    "ONE_MINUTE": 1,
    "THREE_MINUTE": 2,
    "FIVE_MINUTE": 3,
    "TEN_MINUTE": 4,
    "FIFTEEN_MINUTE": 5,
    "THIRTY_MINUTE": 6,
    "ONE_HOUR": 7,
    "ONE_DAY": 8,
    "ONE_WEEK": 9,
    "ONE_MONTH": 10,
}

_VALID_TARGETS = frozenset({"ONE_DAY", "ONE_WEEK"})


def aggregate_candles(
    series: HistoricalCandleSeries,
    *,
    target_interval: str,
    exchange_timezone: str = "Asia/Kolkata",
    session_close_hour: int = 15,
    session_close_minute: int = 30,
    week_end_weekday: int = 4,
    include_incomplete_final_bucket: bool = False,
) -> HistoricalCandleSeries:
    """Aggregate a finer-grained candle series into coarser OHLCV bars.

    Only coarsening is supported (hourly -> daily, daily -> weekly, or
    hourly -> weekly directly -- OHLC aggregation is associative, so
    either source path gives identical results). Each output candle is
    stamped at its bucket's close (its last constituent candle's
    timestamp), never its open, so a bar is never dated before all of its
    own data existed.

    The final bucket is dropped unless it is actually complete: for a
    daily bucket, its last candle's local time must have reached
    ``session_close_hour``/``session_close_minute``; for a weekly bucket,
    its last candle's local weekday must additionally be at or after
    ``week_end_weekday`` (default Friday). This is a deliberate, documented
    simplification -- it does not know about exchange holidays, so a week
    that legitimately ends early (e.g. a Friday holiday) is conservatively
    treated as incomplete rather than risk fabricating a bar early.
    ``include_incomplete_final_bucket=True`` opts out of this check.
    """
    if target_interval not in _VALID_TARGETS:
        raise ValueError(
            f"unsupported aggregation target interval: {target_interval!r}"
        )
    source_rank = _INTERVAL_RANK.get(series.interval)
    target_rank = _INTERVAL_RANK[target_interval]
    if source_rank is None or source_rank >= target_rank:
        raise ValueError(
            f"cannot aggregate {series.interval!r} candles into "
            f"{target_interval!r} -- source must be a strictly finer "
            "interval"
        )
    if not series.candles:
        raise ValueError("cannot aggregate an empty candle series")

    tz = ZoneInfo(exchange_timezone)
    buckets: dict[date, list[Candle]] = {}
    bucket_order: list[date] = []
    for candle in series.candles:
        local_time = candle.timestamp.astimezone(tz)
        key = _bucket_key(local_time, target_interval)
        if key not in buckets:
            buckets[key] = []
            bucket_order.append(key)
        buckets[key].append(candle)

    aggregated: list[Candle] = []
    last_index = len(bucket_order) - 1
    for index, key in enumerate(bucket_order):
        bucket_candles = buckets[key]
        if index == last_index and not include_incomplete_final_bucket:
            if not _is_bucket_complete(
                bucket_candles,
                target_interval,
                tz,
                session_close_hour,
                session_close_minute,
                week_end_weekday,
            ):
                continue
        aggregated.append(_aggregate_bucket(bucket_candles))

    if not aggregated:
        raise ValueError(
            "aggregation produced no complete buckets -- source series "
            "does not span one full target period"
        )

    return HistoricalCandleSeries(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=target_interval,
        candles=aggregated,
        retrieved_at=series.retrieved_at,
        source=series.source,
    )


def _bucket_key(local_time: datetime, target_interval: str) -> date:
    if target_interval == "ONE_DAY":
        return local_time.date()
    return local_time.date() - timedelta(days=local_time.weekday())


def _is_bucket_complete(
    bucket_candles: list[Candle],
    target_interval: str,
    tz: ZoneInfo,
    session_close_hour: int,
    session_close_minute: int,
    week_end_weekday: int,
) -> bool:
    last_local_time = bucket_candles[-1].timestamp.astimezone(tz)
    session_closed = (last_local_time.hour, last_local_time.minute) >= (
        session_close_hour,
        session_close_minute,
    )
    if not session_closed:
        return False
    if target_interval == "ONE_WEEK":
        return last_local_time.weekday() >= week_end_weekday
    return True


def _aggregate_bucket(candles: list[Candle]) -> Candle:
    return Candle(
        timestamp=candles[-1].timestamp,
        open=candles[0].open,
        high=max(candle.high for candle in candles),
        low=min(candle.low for candle in candles),
        close=candles[-1].close,
        volume=sum(candle.volume for candle in candles),
    )
