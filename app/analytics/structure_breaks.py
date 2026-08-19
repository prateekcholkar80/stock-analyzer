from datetime import datetime
from math import isfinite

from app.analytics.market_structure import analyze_market_structure
from app.models.market import HistoricalCandleSeries
from app.models.price_action import (
    MarketStructureBias,
    MarketStructureClassification,
    MarketStructurePoint,
    StructureBreak,
    StructureBreakDetectionResult,
    StructureBreakDirection,
    StructureBreakType,
    SwingPivot,
    SwingPivotDetectionResult,
    SwingPivotType,
)


def detect_structure_breaks(
    series: HistoricalCandleSeries,
    pivot_result: SwingPivotDetectionResult,
    *,
    equality_tolerance_percentage: float = 0.1,
    as_of: datetime | None = None,
) -> StructureBreakDetectionResult:
    """Detect close-confirmed BOS and CHOCH events."""
    normalized_tolerance = _validate_tolerance(
        equality_tolerance_percentage
    )
    _validate_identity(series, pivot_result)
    _validate_candle_order(series)
    _validate_pivots_against_series(series, pivot_result)

    evaluated_at = as_of or series.retrieved_at
    _validate_evaluation_time(
        evaluated_at,
        series.retrieved_at,
    )
    structure = analyze_market_structure(
        pivot_result,
        equality_tolerance_percentage=normalized_tolerance,
        as_of=evaluated_at,
    )

    pivots_by_confirmation: dict[datetime, list[SwingPivot]] = {}
    for pivot in pivot_result.pivots:
        if pivot.confirmed_at <= evaluated_at:
            pivots_by_confirmation.setdefault(
                pivot.confirmed_at,
                [],
            ).append(pivot)

    points_by_confirmation: dict[
        datetime,
        list[MarketStructurePoint],
    ] = {}
    for point in structure.points:
        points_by_confirmation.setdefault(
            point.pivot.confirmed_at,
            [],
        ).append(point)

    active_pivots: dict[SwingPivotType, SwingPivot] = {}
    pivot_is_broken: dict[SwingPivotType, bool] = {
        SwingPivotType.HIGH: False,
        SwingPivotType.LOW: False,
    }
    confirmed_points: list[MarketStructurePoint] = []
    active_bias = MarketStructureBias.UNDETERMINED
    events: list[StructureBreak] = []
    previous_close = None

    for candle in series.candles:
        if candle.timestamp > evaluated_at:
            break

        for pivot in pivots_by_confirmation.get(candle.timestamp, []):
            active_pivots[pivot.pivot_type] = pivot
            pivot_is_broken[pivot.pivot_type] = False

        new_points = points_by_confirmation.get(candle.timestamp, [])
        if new_points:
            confirmed_points.extend(new_points)
            active_bias = _derive_bias(confirmed_points)

        if previous_close is not None:
            active_bias = _detect_candle_breaks(
                candle.timestamp,
                previous_close,
                candle.close,
                active_pivots,
                pivot_is_broken,
                active_bias,
                events,
            )

        previous_close = candle.close

    return StructureBreakDetectionResult(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        source=series.source,
        source_retrieved_at=series.retrieved_at,
        evaluated_at=evaluated_at,
        pivot_left_strength=pivot_result.left_strength,
        pivot_right_strength=pivot_result.right_strength,
        structure_equality_tolerance_percentage=normalized_tolerance,
        events=events,
    )


def _detect_candle_breaks(
    occurred_at: datetime,
    previous_close: float,
    close_price: float,
    active_pivots: dict[SwingPivotType, SwingPivot],
    pivot_is_broken: dict[SwingPivotType, bool],
    active_bias: MarketStructureBias,
    events: list[StructureBreak],
) -> MarketStructureBias:
    swing_high = active_pivots.get(SwingPivotType.HIGH)
    if (
        swing_high is not None
        and not pivot_is_broken[SwingPivotType.HIGH]
        and previous_close <= swing_high.price < close_price
    ):
        event = _build_break(
            StructureBreakDirection.BULLISH,
            occurred_at,
            previous_close,
            close_price,
            swing_high,
            active_bias,
        )
        events.append(event)
        pivot_is_broken[SwingPivotType.HIGH] = True
        active_bias = event.bias_after

    swing_low = active_pivots.get(SwingPivotType.LOW)
    if (
        swing_low is not None
        and not pivot_is_broken[SwingPivotType.LOW]
        and previous_close >= swing_low.price > close_price
    ):
        event = _build_break(
            StructureBreakDirection.BEARISH,
            occurred_at,
            previous_close,
            close_price,
            swing_low,
            active_bias,
        )
        events.append(event)
        pivot_is_broken[SwingPivotType.LOW] = True
        active_bias = event.bias_after

    return active_bias


def _build_break(
    direction: StructureBreakDirection,
    occurred_at: datetime,
    previous_close: float,
    close_price: float,
    broken_pivot: SwingPivot,
    bias_before: MarketStructureBias,
) -> StructureBreak:
    return StructureBreak(
        break_type=_classify_break(direction, bias_before),
        direction=direction,
        occurred_at=occurred_at,
        previous_close=previous_close,
        close_price=close_price,
        broken_pivot=broken_pivot,
        bias_before=bias_before,
    )


def _classify_break(
    direction: StructureBreakDirection,
    bias_before: MarketStructureBias,
) -> StructureBreakType:
    if (
        direction is StructureBreakDirection.BULLISH
        and bias_before is MarketStructureBias.BULLISH
    ) or (
        direction is StructureBreakDirection.BEARISH
        and bias_before is MarketStructureBias.BEARISH
    ):
        return StructureBreakType.BREAK_OF_STRUCTURE

    if (
        direction is StructureBreakDirection.BULLISH
        and bias_before is MarketStructureBias.BEARISH
    ) or (
        direction is StructureBreakDirection.BEARISH
        and bias_before is MarketStructureBias.BULLISH
    ):
        return StructureBreakType.CHANGE_OF_CHARACTER

    return StructureBreakType.UNCLASSIFIED


def _derive_bias(
    points: list[MarketStructurePoint],
) -> MarketStructureBias:
    latest_high = None
    latest_low = None

    for point in points:
        if point.pivot.pivot_type is SwingPivotType.HIGH:
            latest_high = point.classification
        else:
            latest_low = point.classification

    if latest_high is None or latest_low is None:
        return MarketStructureBias.UNDETERMINED

    if (
        latest_high is MarketStructureClassification.HIGHER_HIGH
        and latest_low is MarketStructureClassification.HIGHER_LOW
    ):
        return MarketStructureBias.BULLISH

    if (
        latest_high is MarketStructureClassification.LOWER_HIGH
        and latest_low is MarketStructureClassification.LOWER_LOW
    ):
        return MarketStructureBias.BEARISH

    if (
        latest_high is MarketStructureClassification.EQUAL_HIGH
        and latest_low is MarketStructureClassification.EQUAL_LOW
    ):
        return MarketStructureBias.RANGE_BOUND

    return MarketStructureBias.MIXED


def _validate_tolerance(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("structure-break tolerance must be a number")

    normalized_value = float(value)
    if not isfinite(normalized_value):
        raise ValueError("structure-break tolerance must be finite")

    if normalized_value < 0:
        raise ValueError("structure-break tolerance cannot be negative")

    return normalized_value


def _validate_identity(
    series: HistoricalCandleSeries,
    pivot_result: SwingPivotDetectionResult,
) -> None:
    series_identity = (
        series.exchange,
        series.symbol_token,
        series.symbol,
        series.interval,
        series.source,
        series.retrieved_at,
    )
    pivot_identity = (
        pivot_result.exchange,
        pivot_result.symbol_token,
        pivot_result.symbol,
        pivot_result.interval,
        pivot_result.source,
        pivot_result.source_retrieved_at,
    )
    if series_identity != pivot_identity:
        raise ValueError(
            "market series and swing pivots must describe the same source"
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


def _validate_pivots_against_series(
    series: HistoricalCandleSeries,
    pivot_result: SwingPivotDetectionResult,
) -> None:
    candles_by_timestamp = {
        candle.timestamp: candle
        for candle in series.candles
    }

    for pivot in pivot_result.pivots:
        candle = candles_by_timestamp.get(pivot.pivot_at)
        if candle is None:
            raise ValueError(
                "swing-pivot timestamp is absent from market series"
            )

        expected_price = (
            candle.high
            if pivot.pivot_type is SwingPivotType.HIGH
            else candle.low
        )
        if pivot.price != expected_price:
            raise ValueError(
                "swing-pivot price does not match market candle"
            )


def _validate_evaluation_time(
    evaluated_at: datetime,
    source_retrieved_at: datetime,
) -> None:
    if not isinstance(evaluated_at, datetime):
        raise ValueError("structure-break evaluation must be a datetime")

    if (
        evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise ValueError(
            "structure-break evaluation time must include timezone "
            "information"
        )

    if evaluated_at > source_retrieved_at:
        raise ValueError(
            "structure-break evaluation cannot follow source retrieval"
        )
