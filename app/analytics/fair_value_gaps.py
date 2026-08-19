from datetime import datetime
from math import isfinite

from app.models.market import Candle, HistoricalCandleSeries
from app.models.price_action import (
    FairValueGap,
    FairValueGapDetectionResult,
    FairValueGapDirection,
    FairValueGapStatus,
)
from app.models.technical import IndicatorSeries


def detect_fair_value_gaps(
    series: HistoricalCandleSeries,
    *,
    minimum_gap_percentage: float = 0.0,
    atr_series: IndicatorSeries | None = None,
    minimum_atr_multiple: float | None = None,
) -> FairValueGapDetectionResult:
    """Detect three-candle fair value gaps without look-ahead."""
    normalized_gap_percentage = _validate_non_negative_number(
        "minimum gap percentage",
        minimum_gap_percentage,
    )
    normalized_atr_multiple = None
    if minimum_atr_multiple is not None:
        normalized_atr_multiple = _validate_non_negative_number(
            "minimum ATR multiple",
            minimum_atr_multiple,
        )

    _validate_candle_order(series)
    atr_by_timestamp = _build_atr_lookup(series, atr_series)
    if normalized_atr_multiple is not None and atr_series is None:
        raise ValueError(
            "ATR series is required when minimum ATR multiple is set"
        )

    gaps: list[FairValueGap] = []
    for index in range(2, len(series.candles)):
        first = series.candles[index - 2]
        impulse = series.candles[index - 1]
        detection = series.candles[index]

        direction: FairValueGapDirection | None = None
        lower_price = 0.0
        upper_price = 0.0

        if detection.low > first.high:
            direction = FairValueGapDirection.BULLISH
            lower_price = first.high
            upper_price = detection.low
        elif detection.high < first.low:
            direction = FairValueGapDirection.BEARISH
            lower_price = detection.high
            upper_price = first.low

        if direction is None:
            continue

        gap = FairValueGap(
            direction=direction,
            first_candle_at=first.timestamp,
            impulse_candle_at=impulse.timestamp,
            detected_at=detection.timestamp,
            lower_price=lower_price,
            upper_price=upper_price,
            atr_value=atr_by_timestamp.get(detection.timestamp),
        )

        if gap.gap_percentage < normalized_gap_percentage:
            continue

        if normalized_atr_multiple is not None:
            if (
                gap.atr_multiple is None
                or gap.atr_multiple < normalized_atr_multiple
            ):
                continue

        gaps.append(gap)

    return FairValueGapDetectionResult(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        source=series.source,
        source_retrieved_at=series.retrieved_at,
        minimum_gap_percentage=normalized_gap_percentage,
        minimum_atr_multiple=normalized_atr_multiple,
        gaps=gaps,
    )


def track_fair_value_gap_lifecycle(
    detection_result: FairValueGapDetectionResult,
    series: HistoricalCandleSeries,
    *,
    max_age_candles: int | None = None,
) -> FairValueGapDetectionResult:
    """Evaluate detected gaps against candles strictly after detection."""
    if max_age_candles is not None:
        if (
            isinstance(max_age_candles, bool)
            or not isinstance(max_age_candles, int)
            or max_age_candles < 1
        ):
            raise ValueError("maximum FVG age must be a positive integer")

    _validate_candle_order(series)
    _validate_result_identity(detection_result, series)
    candle_indexes = {
        candle.timestamp: index
        for index, candle in enumerate(series.candles)
    }

    tracked_gaps: list[FairValueGap] = []
    for gap in detection_result.gaps:
        detection_index = candle_indexes.get(gap.detected_at)
        if detection_index is None:
            raise ValueError(
                "FVG detection timestamp is absent from market series"
            )

        tracked_gaps.append(
            _track_gap(
                gap,
                series.candles[detection_index + 1 :],
                max_age_candles=max_age_candles,
            )
        )

    return FairValueGapDetectionResult(
        exchange=detection_result.exchange,
        symbol_token=detection_result.symbol_token,
        symbol=detection_result.symbol,
        interval=detection_result.interval,
        source=detection_result.source,
        source_retrieved_at=series.retrieved_at,
        minimum_gap_percentage=(
            detection_result.minimum_gap_percentage
        ),
        minimum_atr_multiple=detection_result.minimum_atr_multiple,
        gaps=tracked_gaps,
    )


def _track_gap(
    gap: FairValueGap,
    future_candles: list[Candle],
    *,
    max_age_candles: int | None,
) -> FairValueGap:
    status = FairValueGapStatus.OPEN
    fill_percentage = 0.0
    first_touched_at = None
    resolved_at = None

    for age, candle in enumerate(future_candles, start=1):
        if gap.direction is FairValueGapDirection.BULLISH:
            touched = candle.low <= gap.upper_price
            penetration = gap.upper_price - max(
                candle.low,
                gap.lower_price,
            )
            invalidated = candle.close < gap.lower_price
            filled = candle.low <= gap.lower_price
        else:
            touched = candle.high >= gap.lower_price
            penetration = min(
                candle.high,
                gap.upper_price,
            ) - gap.lower_price
            invalidated = candle.close > gap.upper_price
            filled = candle.high >= gap.upper_price

        if touched and first_touched_at is None:
            first_touched_at = candle.timestamp

        candle_fill = min(
            max(penetration / gap.gap_size * 100, 0.0),
            100.0,
        )
        fill_percentage = max(fill_percentage, candle_fill)

        if invalidated:
            status = FairValueGapStatus.INVALIDATED
            fill_percentage = 100.0
            resolved_at = candle.timestamp
            break

        if filled:
            status = FairValueGapStatus.FILLED
            fill_percentage = 100.0
            resolved_at = candle.timestamp
            break

        if fill_percentage > 0:
            status = FairValueGapStatus.PARTIALLY_FILLED

        if max_age_candles is not None and age >= max_age_candles:
            status = FairValueGapStatus.EXPIRED
            resolved_at = candle.timestamp
            break

    payload = gap.model_dump(
        exclude={"gap_size", "gap_percentage", "atr_multiple"}
    )
    payload.update(
        status=status,
        fill_percentage=fill_percentage,
        first_touched_at=first_touched_at,
        resolved_at=resolved_at,
    )
    return FairValueGap.model_validate(payload)


def _validate_non_negative_number(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")

    normalized_value = float(value)
    if not isfinite(normalized_value):
        raise ValueError(f"{name} must be finite")

    if normalized_value < 0:
        raise ValueError(f"{name} cannot be negative")

    return normalized_value


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


def _validate_result_identity(
    result: FairValueGapDetectionResult,
    series: HistoricalCandleSeries,
) -> None:
    result_identity = (
        result.exchange,
        result.symbol_token,
        result.symbol,
        result.interval,
        result.source,
    )
    series_identity = (
        series.exchange,
        series.symbol_token,
        series.symbol,
        series.interval,
        series.source,
    )
    if result_identity != series_identity:
        raise ValueError(
            "FVG detection result and market series must match"
        )


def _build_atr_lookup(
    market_series: HistoricalCandleSeries,
    atr_series: IndicatorSeries | None,
) -> dict[datetime, float]:
    if atr_series is None:
        return {}

    if atr_series.indicator.casefold() != "atr":
        raise ValueError("ATR enrichment requires an ATR indicator series")

    market_identity = (
        market_series.exchange,
        market_series.symbol_token,
        market_series.symbol,
        market_series.interval,
    )
    atr_identity = (
        atr_series.exchange,
        atr_series.symbol_token,
        atr_series.symbol,
        atr_series.interval,
    )
    if atr_identity != market_identity:
        raise ValueError(
            "ATR and market series must describe the same instrument"
        )

    return {
        point.timestamp: point.value
        for point in atr_series.points
    }
