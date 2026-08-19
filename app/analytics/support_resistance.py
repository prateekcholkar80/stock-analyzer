from datetime import datetime
from math import isfinite

from app.models.price_action import (
    PriceZoneType,
    SupportResistanceDetectionResult,
    SupportResistanceZone,
    SwingPivot,
    SwingPivotDetectionResult,
    SwingPivotType,
)


def detect_support_resistance_zones(
    pivot_result: SwingPivotDetectionResult,
    *,
    tolerance_percentage: float = 0.5,
    minimum_touches: int = 2,
    as_of: datetime | None = None,
) -> SupportResistanceDetectionResult:
    """Cluster confirmed swing pivots into price zones."""
    normalized_tolerance = _validate_tolerance(tolerance_percentage)
    _validate_minimum_touches(minimum_touches)
    evaluated_at = as_of or pivot_result.source_retrieved_at
    _validate_evaluation_time(
        evaluated_at,
        pivot_result.source_retrieved_at,
    )

    confirmed_pivots = [
        pivot
        for pivot in pivot_result.pivots
        if pivot.confirmed_at <= evaluated_at
    ]
    zones: list[SupportResistanceZone] = []

    zone_definitions = (
        (SwingPivotType.LOW, PriceZoneType.SUPPORT),
        (SwingPivotType.HIGH, PriceZoneType.RESISTANCE),
    )
    for pivot_type, zone_type in zone_definitions:
        matching_pivots = [
            pivot
            for pivot in confirmed_pivots
            if pivot.pivot_type is pivot_type
        ]
        clusters = _cluster_pivots(
            matching_pivots,
            normalized_tolerance,
        )

        for cluster in clusters:
            if len(cluster) < minimum_touches:
                continue

            chronological_pivots = sorted(
                cluster,
                key=lambda pivot: pivot.pivot_at,
            )
            prices = [pivot.price for pivot in chronological_pivots]
            zones.append(
                SupportResistanceZone(
                    zone_type=zone_type,
                    lower_price=min(prices),
                    upper_price=max(prices),
                    center_price=sum(prices) / len(prices),
                    confirmed_at=chronological_pivots[
                        minimum_touches - 1
                    ].confirmed_at,
                    pivots=chronological_pivots,
                )
            )

    return SupportResistanceDetectionResult(
        exchange=pivot_result.exchange,
        symbol_token=pivot_result.symbol_token,
        symbol=pivot_result.symbol,
        interval=pivot_result.interval,
        source=pivot_result.source,
        source_retrieved_at=pivot_result.source_retrieved_at,
        evaluated_at=evaluated_at,
        pivot_left_strength=pivot_result.left_strength,
        pivot_right_strength=pivot_result.right_strength,
        tolerance_percentage=normalized_tolerance,
        minimum_touches=minimum_touches,
        zones=zones,
    )


def _cluster_pivots(
    pivots: list[SwingPivot],
    tolerance_percentage: float,
) -> list[list[SwingPivot]]:
    clusters: list[list[SwingPivot]] = []

    for pivot in sorted(pivots, key=lambda item: item.price):
        if not clusters or not _fits_cluster(
            clusters[-1],
            pivot,
            tolerance_percentage,
        ):
            clusters.append([pivot])
        else:
            clusters[-1].append(pivot)

    return clusters


def _fits_cluster(
    cluster: list[SwingPivot],
    pivot: SwingPivot,
    tolerance_percentage: float,
) -> bool:
    lowest_price = cluster[0].price
    if lowest_price == 0:
        return pivot.price == 0

    spread_percentage = (
        (pivot.price - lowest_price) / lowest_price * 100
    )
    return spread_percentage <= tolerance_percentage


def _validate_tolerance(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            "support-resistance tolerance must be a number"
        )

    normalized_value = float(value)
    if not isfinite(normalized_value):
        raise ValueError(
            "support-resistance tolerance must be finite"
        )

    if normalized_value < 0:
        raise ValueError(
            "support-resistance tolerance cannot be negative"
        )

    return normalized_value


def _validate_minimum_touches(value: int) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError("minimum zone touches must be an integer")

    if value < 2:
        raise ValueError("minimum zone touches must be at least 2")


def _validate_evaluation_time(
    evaluated_at: datetime,
    source_retrieved_at: datetime,
) -> None:
    if not isinstance(evaluated_at, datetime):
        raise ValueError(
            "support-resistance evaluation time must be a datetime"
        )

    if (
        evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise ValueError(
            "support-resistance evaluation time must include timezone "
            "information"
        )

    if evaluated_at > source_retrieved_at:
        raise ValueError(
            "support-resistance evaluation cannot follow source retrieval"
        )
