from datetime import datetime

from app.models.market import Candle, HistoricalCandleSeries
from app.models.price_action import (
    PriceZoneBreakDirection,
    PriceZoneLifecycleStatus,
    PriceZoneType,
    SupportResistanceDetectionResult,
    SupportResistanceLifecycle,
    SupportResistanceLifecycleResult,
    SupportResistanceZone,
    SwingPivotType,
)


def track_support_resistance_lifecycle(
    series: HistoricalCandleSeries,
    zone_result: SupportResistanceDetectionResult,
    *,
    as_of: datetime | None = None,
) -> SupportResistanceLifecycleResult:
    """Track close-confirmed breaks, retests, and role reversals."""
    _validate_identity(series, zone_result)
    _validate_candle_order(series)
    _validate_zones_against_series(series, zone_result)

    evaluated_at = as_of or series.retrieved_at
    _validate_evaluation_time(
        evaluated_at,
        series.retrieved_at,
        zone_result.evaluated_at,
    )

    actionable_zones = [
        _zone_at_confirmation(zone, zone_result.minimum_touches)
        for zone in zone_result.zones
    ]
    lifecycles = [
        _track_zone(series.candles, zone, evaluated_at)
        for zone in actionable_zones
    ]

    return SupportResistanceLifecycleResult(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        source=series.source,
        source_retrieved_at=series.retrieved_at,
        zone_detection_evaluated_at=zone_result.evaluated_at,
        evaluated_at=evaluated_at,
        pivot_left_strength=zone_result.pivot_left_strength,
        pivot_right_strength=zone_result.pivot_right_strength,
        tolerance_percentage=zone_result.tolerance_percentage,
        minimum_touches=zone_result.minimum_touches,
        lifecycles=lifecycles,
    )


def _zone_at_confirmation(
    zone: SupportResistanceZone,
    minimum_touches: int,
) -> SupportResistanceZone:
    confirmed_pivots = zone.pivots[:minimum_touches]
    prices = [pivot.price for pivot in confirmed_pivots]
    return SupportResistanceZone(
        zone_type=zone.zone_type,
        lower_price=min(prices),
        upper_price=max(prices),
        center_price=sum(prices) / len(prices),
        confirmed_at=zone.confirmed_at,
        pivots=confirmed_pivots,
    )


def _track_zone(
    candles: list[Candle],
    zone: SupportResistanceZone,
    evaluated_at: datetime,
) -> SupportResistanceLifecycle:
    status = PriceZoneLifecycleStatus.ACTIVE
    broken_at = None
    break_direction = None
    break_previous_close = None
    break_close_price = None
    retested_at = None
    reversal_confirmed_at = None
    failed_at = None
    previous_close = None

    for candle in candles:
        if candle.timestamp > evaluated_at:
            break

        if (
            previous_close is not None
            and candle.timestamp >= zone.confirmed_at
            and status is PriceZoneLifecycleStatus.ACTIVE
        ):
            break_direction = _detect_break(
                zone,
                previous_close,
                candle.close,
            )
            if break_direction is not None:
                status = PriceZoneLifecycleStatus.BROKEN
                broken_at = candle.timestamp
                break_previous_close = previous_close
                break_close_price = candle.close
                previous_close = candle.close
                continue

        if (
            status is PriceZoneLifecycleStatus.BROKEN
            and candle.timestamp > broken_at
        ):
            if _is_failed_break(zone, candle.close):
                status = PriceZoneLifecycleStatus.FAILED_BREAK
                failed_at = candle.timestamp
            elif _overlaps_zone(candle, zone):
                status = PriceZoneLifecycleStatus.RETESTED
                retested_at = candle.timestamp

        elif (
            status is PriceZoneLifecycleStatus.RETESTED
            and candle.timestamp > retested_at
        ):
            if _is_failed_break(zone, candle.close):
                status = PriceZoneLifecycleStatus.FAILED_BREAK
                failed_at = candle.timestamp
            elif _confirms_role_reversal(zone, candle.close):
                status = PriceZoneLifecycleStatus.ROLE_REVERSED
                reversal_confirmed_at = candle.timestamp

        previous_close = candle.close

    return SupportResistanceLifecycle(
        zone=zone,
        status=status,
        broken_at=broken_at,
        break_direction=break_direction,
        previous_close=break_previous_close,
        break_close_price=break_close_price,
        retested_at=retested_at,
        reversal_confirmed_at=reversal_confirmed_at,
        failed_at=failed_at,
    )


def _detect_break(
    zone: SupportResistanceZone,
    previous_close: float,
    close_price: float,
) -> PriceZoneBreakDirection | None:
    if (
        zone.zone_type is PriceZoneType.RESISTANCE
        and previous_close <= zone.upper_price < close_price
    ):
        return PriceZoneBreakDirection.BULLISH

    if (
        zone.zone_type is PriceZoneType.SUPPORT
        and previous_close >= zone.lower_price > close_price
    ):
        return PriceZoneBreakDirection.BEARISH

    return None


def _overlaps_zone(
    candle: Candle,
    zone: SupportResistanceZone,
) -> bool:
    return (
        candle.low <= zone.upper_price
        and candle.high >= zone.lower_price
    )


def _is_failed_break(
    zone: SupportResistanceZone,
    close_price: float,
) -> bool:
    if zone.zone_type is PriceZoneType.RESISTANCE:
        return close_price < zone.lower_price
    return close_price > zone.upper_price


def _confirms_role_reversal(
    zone: SupportResistanceZone,
    close_price: float,
) -> bool:
    if zone.zone_type is PriceZoneType.RESISTANCE:
        return close_price > zone.upper_price
    return close_price < zone.lower_price


def _validate_identity(
    series: HistoricalCandleSeries,
    zone_result: SupportResistanceDetectionResult,
) -> None:
    series_identity = (
        series.exchange,
        series.symbol_token,
        series.symbol,
        series.interval,
        series.source,
        series.retrieved_at,
    )
    zone_identity = (
        zone_result.exchange,
        zone_result.symbol_token,
        zone_result.symbol,
        zone_result.interval,
        zone_result.source,
        zone_result.source_retrieved_at,
    )
    if series_identity != zone_identity:
        raise ValueError(
            "market series and price zones must describe the same source"
        )


def _validate_candle_order(series: HistoricalCandleSeries) -> None:
    for previous, current in zip(
        series.candles,
        series.candles[1:],
    ):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "historical candles must have unique timestamps in "
                "ascending order"
            )


def _validate_zones_against_series(
    series: HistoricalCandleSeries,
    zone_result: SupportResistanceDetectionResult,
) -> None:
    candles_by_timestamp = {
        candle.timestamp: candle
        for candle in series.candles
    }

    for zone in zone_result.zones:
        if zone.confirmed_at not in candles_by_timestamp:
            raise ValueError(
                "zone confirmation timestamp is absent from market series"
            )

        for pivot in zone.pivots:
            candle = candles_by_timestamp.get(pivot.pivot_at)
            if candle is None:
                raise ValueError(
                    "zone-pivot timestamp is absent from market series"
                )

            expected_price = (
                candle.high
                if pivot.pivot_type is SwingPivotType.HIGH
                else candle.low
            )
            if pivot.price != expected_price:
                raise ValueError(
                    "zone-pivot price does not match market candle"
                )


def _validate_evaluation_time(
    evaluated_at: datetime,
    source_retrieved_at: datetime,
    zone_detection_evaluated_at: datetime,
) -> None:
    if not isinstance(evaluated_at, datetime):
        raise ValueError("zone-lifecycle evaluation must be a datetime")

    if (
        evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise ValueError(
            "zone-lifecycle evaluation time must include timezone "
            "information"
        )

    if evaluated_at > source_retrieved_at:
        raise ValueError(
            "zone-lifecycle evaluation cannot follow source retrieval"
        )

    if evaluated_at < zone_detection_evaluated_at:
        raise ValueError(
            "zone-lifecycle evaluation cannot precede zone detection"
        )
