from datetime import datetime
from math import isfinite

from app.models.price_action import (
    MarketStructureAnalysisResult,
    MarketStructureClassification,
    MarketStructurePoint,
    SwingPivot,
    SwingPivotDetectionResult,
    SwingPivotType,
)


def analyze_market_structure(
    pivot_result: SwingPivotDetectionResult,
    *,
    equality_tolerance_percentage: float = 0.1,
    as_of: datetime | None = None,
) -> MarketStructureAnalysisResult:
    """Classify confirmed pivots relative to prior same-type pivots."""
    normalized_tolerance = _validate_tolerance(
        equality_tolerance_percentage
    )
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
    previous_pivots: dict[SwingPivotType, SwingPivot] = {}
    points: list[MarketStructurePoint] = []

    for pivot in confirmed_pivots:
        reference_pivot = previous_pivots.get(pivot.pivot_type)
        if reference_pivot is not None:
            points.append(
                MarketStructurePoint(
                    classification=_classify_pivot(
                        pivot,
                        reference_pivot,
                        normalized_tolerance,
                    ),
                    pivot=pivot,
                    reference_pivot=reference_pivot,
                    equality_tolerance_percentage=(
                        normalized_tolerance
                    ),
                )
            )

        previous_pivots[pivot.pivot_type] = pivot

    return MarketStructureAnalysisResult(
        exchange=pivot_result.exchange,
        symbol_token=pivot_result.symbol_token,
        symbol=pivot_result.symbol,
        interval=pivot_result.interval,
        source=pivot_result.source,
        source_retrieved_at=pivot_result.source_retrieved_at,
        evaluated_at=evaluated_at,
        pivot_left_strength=pivot_result.left_strength,
        pivot_right_strength=pivot_result.right_strength,
        equality_tolerance_percentage=normalized_tolerance,
        points=points,
    )


def _classify_pivot(
    pivot: SwingPivot,
    reference_pivot: SwingPivot,
    tolerance_percentage: float,
) -> MarketStructureClassification:
    if reference_pivot.price == 0:
        equal = pivot.price == 0
    else:
        difference_percentage = (
            abs(pivot.price - reference_pivot.price)
            / reference_pivot.price
            * 100
        )
        equal = difference_percentage <= tolerance_percentage

    if pivot.pivot_type is SwingPivotType.HIGH:
        if equal:
            return MarketStructureClassification.EQUAL_HIGH
        if pivot.price > reference_pivot.price:
            return MarketStructureClassification.HIGHER_HIGH
        return MarketStructureClassification.LOWER_HIGH

    if equal:
        return MarketStructureClassification.EQUAL_LOW
    if pivot.price > reference_pivot.price:
        return MarketStructureClassification.HIGHER_LOW
    return MarketStructureClassification.LOWER_LOW


def _validate_tolerance(value: float) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("market-structure tolerance must be a number")

    normalized_value = float(value)
    if not isfinite(normalized_value):
        raise ValueError("market-structure tolerance must be finite")

    if normalized_value < 0:
        raise ValueError("market-structure tolerance cannot be negative")

    return normalized_value


def _validate_evaluation_time(
    evaluated_at: datetime,
    source_retrieved_at: datetime,
) -> None:
    if not isinstance(evaluated_at, datetime):
        raise ValueError("market-structure evaluation must be a datetime")

    if (
        evaluated_at.tzinfo is None
        or evaluated_at.utcoffset() is None
    ):
        raise ValueError(
            "market-structure evaluation time must include timezone "
            "information"
        )

    if evaluated_at > source_retrieved_at:
        raise ValueError(
            "market-structure evaluation cannot follow source retrieval"
        )
