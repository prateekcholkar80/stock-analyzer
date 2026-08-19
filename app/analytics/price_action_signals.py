from datetime import UTC, datetime
from math import isfinite

from app.analytics.fair_value_gaps import (
    track_fair_value_gap_lifecycle,
)
from app.analytics.market_structure import analyze_market_structure
from app.analytics.structure_breaks import detect_structure_breaks
from app.analytics.support_resistance_lifecycle import (
    track_support_resistance_lifecycle,
)
from app.analytics.swing_pivots import detect_swing_pivots
from app.exceptions import InsufficientDataError
from app.models.market import Candle, HistoricalCandleSeries
from app.models.price_action import (
    FairValueGap,
    FairValueGapDetectionResult,
    FairValueGapDirection,
    FairValueGapStatus,
    MarketStructureAnalysisResult,
    MarketStructureBias,
    MarketStructurePoint,
    PriceZoneBreakDirection,
    PriceZoneLifecycleStatus,
    PriceZoneType,
    SupportResistanceDetectionResult,
    SupportResistanceLifecycle,
    SupportResistanceLifecycleResult,
    StructureBreak,
    StructureBreakDirection,
    StructureBreakType,
    SwingPivotDetectionResult,
    SwingPivotType,
)
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalStrength,
    TechnicalSignalEvidence,
)


ACTIVE_GAP_STATUSES = frozenset(
    {
        FairValueGapStatus.OPEN,
        FairValueGapStatus.PARTIALLY_FILLED,
    }
)


def generate_fair_value_gap_signal(
    detection_result: FairValueGapDetectionResult,
    market_series: HistoricalCandleSeries,
    *,
    proximity_threshold_percentage: float = 1.0,
    moderate_atr_multiple: float = 0.5,
    strong_atr_multiple: float = 1.0,
    max_age_candles: int | None = None,
    as_of: datetime | None = None,
) -> TechnicalSignalEvidence:
    """Generate current FVG context from lifecycle-safe evidence."""
    _validate_identity(detection_result, market_series)
    _validate_candle_order(market_series)
    _validate_result_timeline(detection_result, market_series)

    proximity = _validate_non_negative_number(
        "FVG proximity threshold",
        proximity_threshold_percentage,
    )
    moderate_multiple = _validate_non_negative_number(
        "FVG moderate ATR multiple",
        moderate_atr_multiple,
    )
    strong_multiple = _validate_non_negative_number(
        "FVG strong ATR multiple",
        strong_atr_multiple,
    )
    if strong_multiple <= moderate_multiple:
        raise ValueError(
            "FVG strong ATR multiple must exceed moderate ATR multiple"
        )
    normalized_max_age = _validate_max_age(max_age_candles)

    evaluation_time = _resolve_evaluation_time(
        detection_result,
        market_series,
        as_of,
    )
    candles = [
        candle
        for candle in market_series.candles
        if candle.timestamp <= evaluation_time
    ]
    if not candles:
        raise InsufficientDataError(
            "FVG signal requires an available market candle"
        )

    available_gaps = [
        gap
        for gap in detection_result.gaps
        if gap.detected_at <= evaluation_time
    ]
    if (
        normalized_max_age is None
        and any(
            gap.status is FairValueGapStatus.EXPIRED
            for gap in available_gaps
        )
    ):
        raise ValueError(
            "FVG max_age_candles is required to reproduce expired "
            "gap lifecycle"
        )

    truncated_series = market_series.model_copy(
        update={"candles": candles}
    )
    available_result = detection_result.model_copy(
        update={"gaps": available_gaps}
    )
    effective_result = track_fair_value_gap_lifecycle(
        available_result,
        truncated_series,
        max_age_candles=normalized_max_age,
    )

    current_candle = candles[-1]
    current_price = current_candle.close
    if current_price <= 0:
        raise ValueError(
            "FVG proximity calculation requires a positive close"
        )

    status_counts = {
        status: sum(
            gap.status is status
            for gap in effective_result.gaps
        )
        for status in FairValueGapStatus
    }
    selected_gap, distance_percentage = _select_relevant_gap(
        effective_result.gaps,
        current_price,
    )
    direction, strength, condition = _classify_gap_context(
        selected_gap,
        distance_percentage,
        proximity,
        moderate_multiple,
        strong_multiple,
    )

    observed_values = {
        "current_close": current_price,
        "available_gap_count": len(effective_result.gaps),
        "open_gap_count": status_counts[FairValueGapStatus.OPEN],
        "partially_filled_gap_count": status_counts[
            FairValueGapStatus.PARTIALLY_FILLED
        ],
        "filled_gap_count": status_counts[FairValueGapStatus.FILLED],
        "invalidated_gap_count": status_counts[
            FairValueGapStatus.INVALIDATED
        ],
        "expired_gap_count": status_counts[FairValueGapStatus.EXPIRED],
        "active_gap_count": sum(
            status_counts[status]
            for status in ACTIVE_GAP_STATUSES
        ),
        "condition": condition,
    }
    if selected_gap is not None:
        observed_values.update(
            _selected_gap_values(
                selected_gap,
                current_price,
                distance_percentage,
            )
        )

    return TechnicalSignalEvidence(
        evidence_id=_evidence_id(selected_gap),
        name=f"Fair Value Gap {condition}",
        category=SignalCategory.PRICE_ACTION,
        direction=direction,
        strength=strength,
        source="price_action_signals.fair_value_gap_context",
        explanation=_build_explanation(
            selected_gap,
            current_price,
            distance_percentage,
            condition,
            direction,
        ),
        observed_at=current_candle.timestamp,
        available_at=current_candle.timestamp,
        observed_values=observed_values,
        parameters={
            "proximity_threshold_percentage": proximity,
            "moderate_atr_multiple": moderate_multiple,
            "strong_atr_multiple": strong_multiple,
            "max_age_candles": (
                normalized_max_age
                if normalized_max_age is not None
                else "none"
            ),
            "minimum_gap_percentage": (
                detection_result.minimum_gap_percentage
            ),
            "minimum_atr_multiple": (
                detection_result.minimum_atr_multiple
                if detection_result.minimum_atr_multiple is not None
                else "none"
            ),
            "lifecycle_recomputed_as_of": True,
        },
    )


def _validate_identity(
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
            "FVG result and market series must describe the same "
            "instrument, timeframe, and source"
        )


def _validate_candle_order(series: HistoricalCandleSeries) -> None:
    for previous, current in zip(series.candles, series.candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "FVG market candles must have unique timestamps in "
                "ascending order"
            )


def _validate_result_timeline(
    result: FairValueGapDetectionResult,
    series: HistoricalCandleSeries,
) -> None:
    latest_source_time = min(
        result.source_retrieved_at,
        series.retrieved_at,
    )
    candle_timestamps = {
        candle.timestamp
        for candle in series.candles
    }
    for gap in result.gaps:
        if gap.detected_at > latest_source_time:
            raise ValueError(
                "FVG detection cannot follow source retrieval"
            )
        for event_time in (gap.first_touched_at, gap.resolved_at):
            if event_time is not None and event_time > latest_source_time:
                raise ValueError(
                    "FVG lifecycle event cannot follow source retrieval"
                )
        if gap.detected_at not in candle_timestamps:
            raise ValueError(
                "FVG detection timestamp is absent from market series"
            )


def _validate_non_negative_number(name: str, value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    if normalized < 0:
        raise ValueError(f"{name} cannot be negative")
    return normalized


def _validate_max_age(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            "FVG maximum age must be a positive integer or None"
        )
    return value


def _resolve_evaluation_time(
    result: FairValueGapDetectionResult,
    series: HistoricalCandleSeries,
    as_of: datetime | None,
) -> datetime:
    latest_source_time = min(
        result.source_retrieved_at,
        series.retrieved_at,
    )
    if as_of is None:
        return latest_source_time
    if not isinstance(as_of, datetime):
        raise ValueError("FVG signal evaluation time must be a datetime")
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValueError(
            "FVG signal evaluation time must include timezone information"
        )
    if as_of > latest_source_time:
        raise ValueError(
            "FVG signal evaluation cannot follow source retrieval"
        )
    return as_of


def _distance_to_gap_percentage(
    gap: FairValueGap,
    current_price: float,
) -> float:
    if current_price < gap.lower_price:
        distance = gap.lower_price - current_price
    elif current_price > gap.upper_price:
        distance = current_price - gap.upper_price
    else:
        distance = 0.0
    return distance / current_price * 100


def _select_relevant_gap(
    gaps: list[FairValueGap],
    current_price: float,
) -> tuple[FairValueGap | None, float]:
    active = [
        gap
        for gap in gaps
        if gap.status in ACTIVE_GAP_STATUSES
    ]
    if active:
        selected = min(
            active,
            key=lambda gap: (
                _distance_to_gap_percentage(gap, current_price),
                -gap.detected_at.timestamp(),
                gap.direction.value,
            ),
        )
        return (
            selected,
            _distance_to_gap_percentage(selected, current_price),
        )

    resolved = [
        gap
        for gap in gaps
        if gap.resolved_at is not None
    ]
    if resolved:
        selected = max(
            resolved,
            key=lambda gap: (
                gap.resolved_at,
                gap.detected_at,
            ),
        )
        return (
            selected,
            _distance_to_gap_percentage(selected, current_price),
        )
    return None, 0.0


def _classify_gap_context(
    gap: FairValueGap | None,
    distance_percentage: float,
    proximity_threshold: float,
    moderate_atr_multiple: float,
    strong_atr_multiple: float,
) -> tuple[SignalDirection, SignalStrength, str]:
    if gap is None:
        return (
            SignalDirection.NEUTRAL,
            SignalStrength.WEAK,
            "no available gap",
        )
    if gap.status not in ACTIVE_GAP_STATUSES:
        return (
            SignalDirection.NEUTRAL,
            SignalStrength.WEAK,
            f"latest gap {gap.status.value}",
        )

    direction = (
        SignalDirection.BULLISH
        if gap.direction is FairValueGapDirection.BULLISH
        else SignalDirection.BEARISH
    )
    atr_multiple = gap.atr_multiple
    is_near = distance_percentage <= proximity_threshold
    if (
        is_near
        and atr_multiple is not None
        and atr_multiple >= strong_atr_multiple
    ):
        strength = SignalStrength.STRONG
    elif (
        is_near
        or (
            atr_multiple is not None
            and atr_multiple >= moderate_atr_multiple
        )
    ):
        strength = SignalStrength.MODERATE
    else:
        strength = SignalStrength.WEAK

    return (
        direction,
        strength,
        f"active {gap.direction.value} gap {gap.status.value}",
    )


def _price_location(
    gap: FairValueGap,
    current_price: float,
) -> str:
    if current_price < gap.lower_price:
        return "below_gap"
    if current_price > gap.upper_price:
        return "above_gap"
    return "inside_gap"


def _selected_gap_values(
    gap: FairValueGap,
    current_price: float,
    distance_percentage: float,
) -> dict:
    values = {
        "selected_gap_direction": gap.direction.value,
        "selected_gap_status": gap.status.value,
        "selected_gap_detected_at": gap.detected_at.isoformat(),
        "selected_gap_lower_price": gap.lower_price,
        "selected_gap_upper_price": gap.upper_price,
        "selected_gap_size": gap.gap_size,
        "selected_gap_percentage": gap.gap_percentage,
        "selected_gap_fill_percentage": gap.fill_percentage,
        "selected_gap_distance_percentage": distance_percentage,
        "current_price_location": _price_location(gap, current_price),
    }
    if gap.atr_multiple is not None:
        values["selected_gap_atr_multiple"] = gap.atr_multiple
    return values


def _evidence_id(gap: FairValueGap | None) -> str:
    if gap is None:
        return "fvg_context.none"
    timestamp = gap.detected_at.astimezone(UTC).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    return f"fvg_context.{gap.direction.value}.{timestamp}"


def _build_explanation(
    gap: FairValueGap | None,
    current_price: float,
    distance_percentage: float,
    condition: str,
    direction: SignalDirection,
) -> str:
    if gap is None:
        return (
            "No fair value gap was available at the evaluation time; "
            "no directional FVG context is inferred."
        )
    if direction is SignalDirection.NEUTRAL:
        return (
            f"The latest available {gap.direction.value} fair value gap "
            f"is {gap.status.value}; it is retained as resolved history, "
            "not current directional evidence."
        )
    return (
        f"The selected {gap.direction.value} fair value gap from "
        f"{gap.lower_price:.6f} to {gap.upper_price:.6f} is "
        f"{gap.status.value}. Close {current_price:.6f} is "
        f"{distance_percentage:.6f}% from the zone, indicating "
        f"{condition}; this is price-action context, not entry "
        "confirmation."
    )


def generate_support_resistance_lifecycle_signal(
    lifecycle_result: SupportResistanceLifecycleResult,
    market_series: HistoricalCandleSeries,
    *,
    proximity_threshold_percentage: float = 1.0,
    strong_touch_count: int = 3,
    strong_break_percentage: float = 1.0,
    as_of: datetime | None = None,
) -> TechnicalSignalEvidence:
    """Generate lifecycle-safe support/resistance evidence.

    The supplied lifecycle is treated as source evidence, not as the
    point-in-time answer. Zone lifecycle state is recomputed at ``as_of``
    so later breaks, retests, reversals, or failures cannot leak into a
    historical evaluation.
    """
    _validate_support_resistance_identity(
        lifecycle_result,
        market_series,
    )
    _validate_support_resistance_candle_order(market_series)
    _validate_support_resistance_timeline(
        lifecycle_result,
        market_series,
    )

    proximity = _validate_non_negative_number(
        "support-resistance proximity threshold",
        proximity_threshold_percentage,
    )
    break_threshold = _validate_non_negative_number(
        "support-resistance strong break threshold",
        strong_break_percentage,
    )
    normalized_touch_count = _validate_strong_touch_count(
        strong_touch_count,
        lifecycle_result.minimum_touches,
    )
    evaluation_time = _resolve_support_resistance_evaluation_time(
        lifecycle_result,
        market_series,
        as_of,
    )
    available_candles = [
        candle
        for candle in market_series.candles
        if candle.timestamp <= evaluation_time
    ]
    if not available_candles:
        raise InsufficientDataError(
            "support-resistance signal requires an available market "
            "candle"
        )

    zone_result = _reconstruct_zone_detection(lifecycle_result)
    _validate_supplied_lifecycle(
        lifecycle_result,
        market_series,
        zone_result,
    )
    effective_result = track_support_resistance_lifecycle(
        market_series,
        zone_result,
        as_of=evaluation_time,
    )
    current_candle = available_candles[-1]
    current_price = current_candle.close
    if current_price <= 0:
        raise ValueError(
            "support-resistance proximity calculation requires a "
            "positive close"
        )

    status_counts = {
        status: sum(
            lifecycle.status is status
            for lifecycle in effective_result.lifecycles
        )
        for status in PriceZoneLifecycleStatus
    }
    selected, distance_percentage = _select_relevant_lifecycle(
        effective_result.lifecycles,
        current_price,
    )
    direction, strength, condition = _classify_zone_lifecycle(
        selected,
        distance_percentage,
        proximity,
        normalized_touch_count,
        break_threshold,
    )

    observed_values = {
        "current_close": current_price,
        "available_zone_count": len(effective_result.lifecycles),
        "active_zone_count": status_counts[
            PriceZoneLifecycleStatus.ACTIVE
        ],
        "broken_zone_count": status_counts[
            PriceZoneLifecycleStatus.BROKEN
        ],
        "retested_zone_count": status_counts[
            PriceZoneLifecycleStatus.RETESTED
        ],
        "role_reversed_zone_count": status_counts[
            PriceZoneLifecycleStatus.ROLE_REVERSED
        ],
        "failed_break_zone_count": status_counts[
            PriceZoneLifecycleStatus.FAILED_BREAK
        ],
        "condition": condition,
    }
    if selected is not None:
        observed_values.update(
            _selected_lifecycle_values(
                selected,
                current_price,
                distance_percentage,
            )
        )

    return TechnicalSignalEvidence(
        evidence_id=_support_resistance_evidence_id(selected),
        name=f"Support/Resistance {condition}",
        category=SignalCategory.PRICE_ACTION,
        direction=direction,
        strength=strength,
        source="price_action_signals.support_resistance_lifecycle",
        explanation=_build_support_resistance_explanation(
            selected,
            current_price,
            distance_percentage,
            direction,
        ),
        observed_at=current_candle.timestamp,
        available_at=current_candle.timestamp,
        observed_values=observed_values,
        parameters={
            "proximity_threshold_percentage": proximity,
            "strong_touch_count": normalized_touch_count,
            "strong_break_percentage": break_threshold,
            "zone_tolerance_percentage": (
                lifecycle_result.tolerance_percentage
            ),
            "minimum_touches": lifecycle_result.minimum_touches,
            "pivot_left_strength": (
                lifecycle_result.pivot_left_strength
            ),
            "pivot_right_strength": (
                lifecycle_result.pivot_right_strength
            ),
            "lifecycle_recomputed_as_of": True,
        },
    )


def _validate_support_resistance_identity(
    result: SupportResistanceLifecycleResult,
    series: HistoricalCandleSeries,
) -> None:
    result_identity = (
        result.exchange,
        result.symbol_token,
        result.symbol,
        result.interval,
        result.source,
        result.source_retrieved_at,
    )
    series_identity = (
        series.exchange,
        series.symbol_token,
        series.symbol,
        series.interval,
        series.source,
        series.retrieved_at,
    )
    if result_identity != series_identity:
        raise ValueError(
            "support-resistance lifecycle and market series must "
            "describe the same instrument, timeframe, source, and "
            "retrieval"
        )


def _validate_support_resistance_timeline(
    result: SupportResistanceLifecycleResult,
    series: HistoricalCandleSeries,
) -> None:
    if (
        result.zone_detection_evaluated_at > result.evaluated_at
        or result.evaluated_at > result.source_retrieved_at
    ):
        raise ValueError(
            "support-resistance lifecycle evaluation must be between "
            "zone detection and source retrieval"
        )

    candle_timestamps = {
        candle.timestamp
        for candle in series.candles
    }
    for lifecycle in result.lifecycles:
        zone = lifecycle.zone
        if zone.confirmed_at > result.zone_detection_evaluated_at:
            raise ValueError(
                "support-resistance zone confirmation cannot follow "
                "zone detection"
            )
        event_times = (
            lifecycle.broken_at,
            lifecycle.retested_at,
            lifecycle.reversal_confirmed_at,
            lifecycle.failed_at,
        )
        for event_time in event_times:
            if event_time is None:
                continue
            if event_time > result.evaluated_at:
                raise ValueError(
                    "support-resistance lifecycle event cannot follow "
                    "lifecycle evaluation"
                )
            if event_time not in candle_timestamps:
                raise ValueError(
                    "support-resistance lifecycle event timestamp is "
                    "absent from market series"
                )


def _validate_support_resistance_candle_order(
    series: HistoricalCandleSeries,
) -> None:
    for previous, current in zip(series.candles, series.candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "support-resistance market candles must have unique "
                "timestamps in ascending order"
            )


def _validate_strong_touch_count(
    value: int,
    minimum_touches: int,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(
            "support-resistance strong touch count must be an integer"
        )
    if value < minimum_touches:
        raise ValueError(
            "support-resistance strong touch count cannot be below "
            "the lifecycle minimum touches"
        )
    return value


def _resolve_support_resistance_evaluation_time(
    result: SupportResistanceLifecycleResult,
    series: HistoricalCandleSeries,
    as_of: datetime | None,
) -> datetime:
    latest_source_time = min(
        result.source_retrieved_at,
        series.retrieved_at,
    )
    evaluation_time = latest_source_time if as_of is None else as_of
    if not isinstance(evaluation_time, datetime):
        raise ValueError(
            "support-resistance signal evaluation time must be a "
            "datetime"
        )
    if (
        evaluation_time.tzinfo is None
        or evaluation_time.utcoffset() is None
    ):
        raise ValueError(
            "support-resistance signal evaluation time must include "
            "timezone information"
        )
    if evaluation_time > latest_source_time:
        raise ValueError(
            "support-resistance signal evaluation cannot follow source "
            "retrieval"
        )
    if evaluation_time < result.zone_detection_evaluated_at:
        raise ValueError(
            "support-resistance signal evaluation cannot precede zone "
            "detection"
        )
    return evaluation_time


def _reconstruct_zone_detection(
    result: SupportResistanceLifecycleResult,
) -> SupportResistanceDetectionResult:
    return SupportResistanceDetectionResult(
        exchange=result.exchange,
        symbol_token=result.symbol_token,
        symbol=result.symbol,
        interval=result.interval,
        source=result.source,
        source_retrieved_at=result.source_retrieved_at,
        evaluated_at=result.zone_detection_evaluated_at,
        pivot_left_strength=result.pivot_left_strength,
        pivot_right_strength=result.pivot_right_strength,
        tolerance_percentage=result.tolerance_percentage,
        minimum_touches=result.minimum_touches,
        zones=[
            lifecycle.zone
            for lifecycle in result.lifecycles
        ],
    )


def _validate_supplied_lifecycle(
    result: SupportResistanceLifecycleResult,
    series: HistoricalCandleSeries,
    zone_result: SupportResistanceDetectionResult,
) -> None:
    recomputed = track_support_resistance_lifecycle(
        series,
        zone_result,
        as_of=result.evaluated_at,
    )
    if recomputed.lifecycles != result.lifecycles:
        raise ValueError(
            "support-resistance lifecycle does not match market series"
        )


def _distance_to_zone_percentage(
    lifecycle: SupportResistanceLifecycle,
    current_price: float,
) -> float:
    zone = lifecycle.zone
    if current_price < zone.lower_price:
        distance = zone.lower_price - current_price
    elif current_price > zone.upper_price:
        distance = current_price - zone.upper_price
    else:
        distance = 0.0
    return distance / current_price * 100


def _lifecycle_event_time(
    lifecycle: SupportResistanceLifecycle,
) -> datetime:
    return (
        lifecycle.failed_at
        or lifecycle.reversal_confirmed_at
        or lifecycle.retested_at
        or lifecycle.broken_at
        or lifecycle.zone.confirmed_at
    )


def _lifecycle_priority(
    lifecycle: SupportResistanceLifecycle,
) -> int:
    priorities = {
        PriceZoneLifecycleStatus.ACTIVE: 1,
        PriceZoneLifecycleStatus.BROKEN: 2,
        PriceZoneLifecycleStatus.RETESTED: 3,
        PriceZoneLifecycleStatus.ROLE_REVERSED: 4,
        PriceZoneLifecycleStatus.FAILED_BREAK: 4,
    }
    return priorities[lifecycle.status]


def _select_relevant_lifecycle(
    lifecycles: list[SupportResistanceLifecycle],
    current_price: float,
) -> tuple[SupportResistanceLifecycle | None, float]:
    if not lifecycles:
        return None, 0.0

    selected = min(
        lifecycles,
        key=lambda lifecycle: (
            -_lifecycle_priority(lifecycle),
            -_lifecycle_event_time(lifecycle).timestamp(),
            _distance_to_zone_percentage(lifecycle, current_price),
            -lifecycle.zone.touch_count,
            lifecycle.zone.zone_type.value,
            lifecycle.zone.lower_price,
            lifecycle.zone.upper_price,
        ),
    )
    return selected, _distance_to_zone_percentage(
        selected,
        current_price,
    )


def _direction_from_break(
    direction: PriceZoneBreakDirection,
) -> SignalDirection:
    if direction is PriceZoneBreakDirection.BULLISH:
        return SignalDirection.BULLISH
    return SignalDirection.BEARISH


def _opposite_break_direction(
    direction: PriceZoneBreakDirection,
) -> SignalDirection:
    if direction is PriceZoneBreakDirection.BULLISH:
        return SignalDirection.BEARISH
    return SignalDirection.BULLISH


def _classify_zone_lifecycle(
    lifecycle: SupportResistanceLifecycle | None,
    distance_percentage: float,
    proximity_threshold: float,
    strong_touch_count: int,
    strong_break_percentage: float,
) -> tuple[SignalDirection, SignalStrength, str]:
    if lifecycle is None:
        return (
            SignalDirection.NEUTRAL,
            SignalStrength.WEAK,
            "no available zone",
        )

    status = lifecycle.status
    zone = lifecycle.zone
    if status is PriceZoneLifecycleStatus.ACTIVE:
        direction = (
            SignalDirection.BULLISH
            if zone.zone_type is PriceZoneType.SUPPORT
            else SignalDirection.BEARISH
        )
        is_near = distance_percentage <= proximity_threshold
        if is_near and zone.touch_count >= strong_touch_count:
            strength = SignalStrength.STRONG
        elif is_near:
            strength = SignalStrength.MODERATE
        else:
            strength = SignalStrength.WEAK
        return (
            direction,
            strength,
            f"active {zone.zone_type.value} zone",
        )

    if status is PriceZoneLifecycleStatus.FAILED_BREAK:
        return (
            _opposite_break_direction(lifecycle.break_direction),
            SignalStrength.STRONG,
            f"failed {lifecycle.break_direction.value} break",
        )

    direction = _direction_from_break(lifecycle.break_direction)
    if status is PriceZoneLifecycleStatus.ROLE_REVERSED:
        return (
            direction,
            SignalStrength.STRONG,
            f"confirmed {lifecycle.reversed_zone_type.value} role "
            "reversal",
        )
    if status is PriceZoneLifecycleStatus.RETESTED:
        return (
            direction,
            SignalStrength.STRONG,
            f"{lifecycle.break_direction.value} break retested",
        )

    break_percentage = lifecycle.break_percentage
    strength = (
        SignalStrength.STRONG
        if (
            break_percentage is not None
            and break_percentage >= strong_break_percentage
        )
        else SignalStrength.MODERATE
    )
    return (
        direction,
        strength,
        f"confirmed {lifecycle.break_direction.value} break",
    )


def _zone_price_location(
    lifecycle: SupportResistanceLifecycle,
    current_price: float,
) -> str:
    zone = lifecycle.zone
    if current_price < zone.lower_price:
        return "below_zone"
    if current_price > zone.upper_price:
        return "above_zone"
    return "inside_zone"


def _selected_lifecycle_values(
    lifecycle: SupportResistanceLifecycle,
    current_price: float,
    distance_percentage: float,
) -> dict:
    zone = lifecycle.zone
    values = {
        "selected_zone_type": zone.zone_type.value,
        "selected_zone_status": lifecycle.status.value,
        "selected_zone_confirmed_at": zone.confirmed_at.isoformat(),
        "selected_zone_lower_price": zone.lower_price,
        "selected_zone_upper_price": zone.upper_price,
        "selected_zone_center_price": zone.center_price,
        "selected_zone_touch_count": zone.touch_count,
        "selected_zone_distance_percentage": distance_percentage,
        "current_price_location": _zone_price_location(
            lifecycle,
            current_price,
        ),
    }
    if lifecycle.break_direction is not None:
        values["selected_break_direction"] = (
            lifecycle.break_direction.value
        )
        values["selected_broken_at"] = lifecycle.broken_at.isoformat()
        values["selected_break_previous_close"] = (
            lifecycle.previous_close
        )
        values["selected_break_close_price"] = (
            lifecycle.break_close_price
        )
        if lifecycle.break_percentage is not None:
            values["selected_break_percentage"] = (
                lifecycle.break_percentage
            )
    if lifecycle.retested_at is not None:
        values["selected_retested_at"] = (
            lifecycle.retested_at.isoformat()
        )
    if lifecycle.reversal_confirmed_at is not None:
        values["selected_reversal_confirmed_at"] = (
            lifecycle.reversal_confirmed_at.isoformat()
        )
        values["selected_reversed_zone_type"] = (
            lifecycle.reversed_zone_type.value
        )
    if lifecycle.failed_at is not None:
        values["selected_failed_at"] = lifecycle.failed_at.isoformat()
    return values


def _support_resistance_evidence_id(
    lifecycle: SupportResistanceLifecycle | None,
) -> str:
    if lifecycle is None:
        return "support_resistance.none"
    timestamp = lifecycle.zone.confirmed_at.astimezone(UTC).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    return (
        "support_resistance."
        f"{lifecycle.zone.zone_type.value}.{timestamp}"
    )


def _build_support_resistance_explanation(
    lifecycle: SupportResistanceLifecycle | None,
    current_price: float,
    distance_percentage: float,
    direction: SignalDirection,
) -> str:
    if lifecycle is None:
        return (
            "No confirmed support or resistance zone was available at "
            "the evaluation time; no directional zone context is "
            "inferred."
        )

    zone = lifecycle.zone
    status = lifecycle.status
    if status is PriceZoneLifecycleStatus.ACTIVE:
        return (
            f"The selected active {zone.zone_type.value} zone spans "
            f"{zone.lower_price:.6f} to {zone.upper_price:.6f}. Close "
            f"{current_price:.6f} is {distance_percentage:.6f}% from "
            f"the zone, providing {direction.value} price-location "
            "context; a touch alone is not a confirmed break or entry "
            "signal."
        )
    if status is PriceZoneLifecycleStatus.BROKEN:
        return (
            f"A close-confirmed {lifecycle.break_direction.value} break "
            f"of the prior {zone.zone_type.value} zone occurred at "
            f"{lifecycle.broken_at.isoformat()}. No qualifying retest "
            "or failure is yet confirmed; this is price-action "
            "evidence, not a trade recommendation."
        )
    if status is PriceZoneLifecycleStatus.RETESTED:
        return (
            f"The close-confirmed {lifecycle.break_direction.value} "
            f"break of the prior {zone.zone_type.value} zone was "
            f"retested at {lifecycle.retested_at.isoformat()}. The "
            "retest strengthens the break evidence, but role reversal "
            "still requires a later confirming close."
        )
    if status is PriceZoneLifecycleStatus.ROLE_REVERSED:
        return (
            f"The prior {zone.zone_type.value} zone was broken, "
            f"retested, and confirmed as {lifecycle.reversed_zone_type.value} "
            f"at {lifecycle.reversal_confirmed_at.isoformat()}. This "
            "supports deterministic lifecycle context, not a trade "
            "recommendation."
        )
    return (
        f"The attempted {lifecycle.break_direction.value} break of the "
        f"prior {zone.zone_type.value} zone failed at "
        f"{lifecycle.failed_at.isoformat()}, producing "
        f"{direction.value} failed-break evidence rather than a valid "
        "role reversal."
    )


def generate_market_structure_signal(
    pivot_result: SwingPivotDetectionResult,
    market_series: HistoricalCandleSeries,
    *,
    equality_tolerance_percentage: float = 0.1,
    strong_break_percentage: float = 1.0,
    as_of: datetime | None = None,
) -> TechnicalSignalEvidence:
    """Generate point-in-time HH/HL/LH/LL and BOS/CHOCH evidence."""
    _validate_market_structure_identity(pivot_result, market_series)
    _validate_market_structure_candle_order(market_series)

    tolerance = _validate_non_negative_number(
        "market-structure equality tolerance",
        equality_tolerance_percentage,
    )
    break_threshold = _validate_non_negative_number(
        "market-structure strong break threshold",
        strong_break_percentage,
    )
    evaluation_time = _resolve_market_structure_evaluation_time(
        pivot_result,
        market_series,
        as_of,
    )
    available_candles = [
        candle
        for candle in market_series.candles
        if candle.timestamp <= evaluation_time
    ]
    if not available_candles:
        raise InsufficientDataError(
            "market-structure signal requires an available market "
            "candle"
        )

    available_series = market_series.model_copy(
        update={"candles": available_candles}
    )
    available_pivot_result = pivot_result.model_copy(
        update={
            "pivots": [
                pivot
                for pivot in pivot_result.pivots
                if pivot.confirmed_at <= evaluation_time
            ]
        }
    )
    _validate_pivot_provenance(
        available_pivot_result,
        available_series,
    )
    structure = analyze_market_structure(
        available_pivot_result,
        equality_tolerance_percentage=tolerance,
        as_of=evaluation_time,
    )
    break_result = detect_structure_breaks(
        available_series,
        available_pivot_result,
        equality_tolerance_percentage=tolerance,
        as_of=evaluation_time,
    )
    latest_event = (
        break_result.events[-1]
        if break_result.events
        else None
    )
    direction, strength, condition = _classify_market_structure_context(
        structure.bias,
        latest_event,
        break_threshold,
    )
    current_candle = available_candles[-1]
    confirmed_pivots = available_pivot_result.pivots
    latest_high = _latest_structure_point(
        structure,
        SwingPivotType.HIGH,
    )
    latest_low = _latest_structure_point(
        structure,
        SwingPivotType.LOW,
    )
    event_counts = {
        break_type: sum(
            event.break_type is break_type
            for event in break_result.events
        )
        for break_type in StructureBreakType
    }

    observed_values = {
        "current_close": current_candle.close,
        "confirmed_pivot_count": len(confirmed_pivots),
        "market_structure_point_count": len(structure.points),
        "current_structure_bias": structure.bias.value,
        "effective_bias": _effective_structure_bias(
            structure.bias,
            latest_event,
        ).value,
        "break_event_count": len(break_result.events),
        "break_of_structure_count": event_counts[
            StructureBreakType.BREAK_OF_STRUCTURE
        ],
        "change_of_character_count": event_counts[
            StructureBreakType.CHANGE_OF_CHARACTER
        ],
        "unclassified_break_count": event_counts[
            StructureBreakType.UNCLASSIFIED
        ],
        "condition": condition,
    }
    if latest_high is not None:
        observed_values.update(
            _structure_point_values("high", latest_high)
        )
    if latest_low is not None:
        observed_values.update(
            _structure_point_values("low", latest_low)
        )
    if latest_event is not None:
        observed_values.update(
            _structure_break_values(
                latest_event,
                available_candles,
            )
        )

    return TechnicalSignalEvidence(
        evidence_id=_market_structure_evidence_id(
            structure.bias,
            latest_event,
        ),
        name=f"Market Structure {condition}",
        category=SignalCategory.PRICE_ACTION,
        direction=direction,
        strength=strength,
        source="price_action_signals.market_structure",
        explanation=_build_market_structure_explanation(
            structure,
            latest_event,
            direction,
        ),
        observed_at=current_candle.timestamp,
        available_at=current_candle.timestamp,
        observed_values=observed_values,
        parameters={
            "equality_tolerance_percentage": tolerance,
            "strong_break_percentage": break_threshold,
            "pivot_left_strength": pivot_result.left_strength,
            "pivot_right_strength": pivot_result.right_strength,
            "structure_recomputed_as_of": True,
            "breaks_recomputed_as_of": True,
            "pivot_provenance_verified": True,
        },
    )


def _validate_market_structure_identity(
    result: SwingPivotDetectionResult,
    series: HistoricalCandleSeries,
) -> None:
    result_identity = (
        result.exchange,
        result.symbol_token,
        result.symbol,
        result.interval,
        result.source,
        result.source_retrieved_at,
    )
    series_identity = (
        series.exchange,
        series.symbol_token,
        series.symbol,
        series.interval,
        series.source,
        series.retrieved_at,
    )
    if result_identity != series_identity:
        raise ValueError(
            "swing pivots and market series must describe the same "
            "instrument, timeframe, source, and retrieval"
        )


def _validate_market_structure_candle_order(
    series: HistoricalCandleSeries,
) -> None:
    for previous, current in zip(series.candles, series.candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "market-structure candles must have unique timestamps "
                "in ascending order"
            )


def _validate_pivot_provenance(
    result: SwingPivotDetectionResult,
    series: HistoricalCandleSeries,
) -> None:
    recomputed = detect_swing_pivots(
        series,
        left_strength=result.left_strength,
        right_strength=result.right_strength,
    )
    if recomputed.pivots != result.pivots:
        raise ValueError(
            "swing-pivot result does not match market series"
        )


def _resolve_market_structure_evaluation_time(
    result: SwingPivotDetectionResult,
    series: HistoricalCandleSeries,
    as_of: datetime | None,
) -> datetime:
    latest_source_time = min(
        result.source_retrieved_at,
        series.retrieved_at,
    )
    evaluation_time = latest_source_time if as_of is None else as_of
    if not isinstance(evaluation_time, datetime):
        raise ValueError(
            "market-structure signal evaluation time must be a datetime"
        )
    if (
        evaluation_time.tzinfo is None
        or evaluation_time.utcoffset() is None
    ):
        raise ValueError(
            "market-structure signal evaluation time must include "
            "timezone information"
        )
    if evaluation_time > latest_source_time:
        raise ValueError(
            "market-structure signal evaluation cannot follow source "
            "retrieval"
        )
    return evaluation_time


def _latest_structure_point(
    structure: MarketStructureAnalysisResult,
    pivot_type: SwingPivotType,
) -> MarketStructurePoint | None:
    matching = [
        point
        for point in structure.points
        if point.pivot.pivot_type is pivot_type
    ]
    return matching[-1] if matching else None


def _signal_direction_from_structure_break(
    direction: StructureBreakDirection,
) -> SignalDirection:
    if direction is StructureBreakDirection.BULLISH:
        return SignalDirection.BULLISH
    return SignalDirection.BEARISH


def _classify_market_structure_context(
    bias: MarketStructureBias,
    event: StructureBreak | None,
    strong_break_percentage: float,
) -> tuple[SignalDirection, SignalStrength, str]:
    if event is not None:
        direction = _signal_direction_from_structure_break(event.direction)
        if event.break_type is StructureBreakType.CHANGE_OF_CHARACTER:
            return (
                direction,
                SignalStrength.STRONG,
                f"{event.direction.value} change of character",
            )
        if event.break_type is StructureBreakType.UNCLASSIFIED:
            return (
                direction,
                SignalStrength.WEAK,
                f"{event.direction.value} unclassified break",
            )

        break_percentage = event.break_percentage
        strength = (
            SignalStrength.STRONG
            if (
                break_percentage is not None
                and break_percentage >= strong_break_percentage
            )
            else SignalStrength.MODERATE
        )
        return (
            direction,
            strength,
            f"{event.direction.value} break of structure",
        )

    if bias is MarketStructureBias.BULLISH:
        return (
            SignalDirection.BULLISH,
            SignalStrength.MODERATE,
            "bullish HH/HL structure",
        )
    if bias is MarketStructureBias.BEARISH:
        return (
            SignalDirection.BEARISH,
            SignalStrength.MODERATE,
            "bearish LH/LL structure",
        )
    if bias is MarketStructureBias.RANGE_BOUND:
        return (
            SignalDirection.NEUTRAL,
            SignalStrength.MODERATE,
            "range-bound equal-high/equal-low structure",
        )
    if bias is MarketStructureBias.MIXED:
        return (
            SignalDirection.NEUTRAL,
            SignalStrength.WEAK,
            "mixed market structure",
        )
    return (
        SignalDirection.NEUTRAL,
        SignalStrength.WEAK,
        "undetermined market structure",
    )


def _effective_structure_bias(
    bias: MarketStructureBias,
    event: StructureBreak | None,
) -> MarketStructureBias:
    if event is None:
        return bias
    return event.bias_after


def _structure_point_values(
    label: str,
    point: MarketStructurePoint,
) -> dict:
    values = {
        f"latest_{label}_classification": point.classification.value,
        f"latest_{label}_price": point.pivot.price,
        f"latest_{label}_pivot_at": point.pivot.pivot_at.isoformat(),
        f"latest_{label}_confirmed_at": (
            point.pivot.confirmed_at.isoformat()
        ),
        f"latest_{label}_reference_price": point.reference_pivot.price,
        f"latest_{label}_price_change": point.price_change,
    }
    if point.price_change_percentage is not None:
        values[f"latest_{label}_price_change_percentage"] = (
            point.price_change_percentage
        )
    return values


def _structure_break_values(
    event: StructureBreak,
    available_candles: list[Candle],
) -> dict:
    event_index = next(
        index
        for index, candle in enumerate(available_candles)
        if candle.timestamp == event.occurred_at
    )
    values = {
        "latest_break_type": event.break_type.value,
        "latest_break_direction": event.direction.value,
        "latest_break_occurred_at": event.occurred_at.isoformat(),
        "latest_break_previous_close": event.previous_close,
        "latest_break_close_price": event.close_price,
        "latest_break_pivot_type": event.broken_pivot.pivot_type.value,
        "latest_break_pivot_price": event.broken_pivot.price,
        "latest_break_pivot_at": event.broken_pivot.pivot_at.isoformat(),
        "latest_break_pivot_confirmed_at": (
            event.broken_pivot.confirmed_at.isoformat()
        ),
        "latest_break_bias_before": event.bias_before.value,
        "latest_break_bias_after": event.bias_after.value,
        "latest_break_distance": event.break_distance,
        "latest_break_age_candles": (
            len(available_candles) - event_index - 1
        ),
    }
    if event.break_percentage is not None:
        values["latest_break_percentage"] = event.break_percentage
    return values


def _market_structure_evidence_id(
    bias: MarketStructureBias,
    event: StructureBreak | None,
) -> str:
    if event is None:
        return f"market_structure.bias.{bias.value}"
    timestamp = event.occurred_at.astimezone(UTC).strftime(
        "%Y%m%dT%H%M%S%fZ"
    )
    return (
        f"market_structure.{event.break_type.value}."
        f"{event.direction.value}.{timestamp}"
    )


def _build_market_structure_explanation(
    structure: MarketStructureAnalysisResult,
    event: StructureBreak | None,
    direction: SignalDirection,
) -> str:
    if event is None:
        if structure.bias is MarketStructureBias.UNDETERMINED:
            return (
                "Confirmed pivots do not yet establish both high and "
                "low structure; no directional market-structure bias "
                "is inferred."
            )
        if structure.bias is MarketStructureBias.RANGE_BOUND:
            return (
                "Latest confirmed swing comparisons are equal highs "
                "and equal lows within tolerance, indicating "
                "range-bound rather than directional structure."
            )
        if structure.bias is MarketStructureBias.MIXED:
            return (
                "Latest confirmed high and low classifications conflict, "
                "so market structure is mixed and directionally neutral."
            )
        return (
            f"Latest confirmed swing comparisons establish "
            f"{structure.bias.value} market structure with no later "
            "close-confirmed structural break; this is price-action "
            "context, not a trade recommendation."
        )

    if event.break_type is StructureBreakType.CHANGE_OF_CHARACTER:
        return (
            f"Close {event.close_price:.6f} produced a confirmed "
            f"{event.direction.value} change of character through the "
            f"swing {event.broken_pivot.pivot_type.value} at "
            f"{event.broken_pivot.price:.6f}, changing effective bias "
            f"from {event.bias_before.value} to {event.bias_after.value}. "
            "This is structural evidence, not a trade recommendation."
        )
    if event.break_type is StructureBreakType.BREAK_OF_STRUCTURE:
        return (
            f"Close {event.close_price:.6f} confirmed a "
            f"{event.direction.value} break of structure through the "
            f"swing {event.broken_pivot.pivot_type.value} at "
            f"{event.broken_pivot.price:.6f}, continuing the "
            f"{direction.value} structural bias; this is not a trade "
            "recommendation."
        )
    return (
        f"Close {event.close_price:.6f} crossed the confirmed swing "
        f"{event.broken_pivot.pivot_type.value} at "
        f"{event.broken_pivot.price:.6f}, but prior structure was "
        f"{event.bias_before.value}; the {event.direction.value} break "
        "is therefore directional but unclassified and weak evidence."
    )
