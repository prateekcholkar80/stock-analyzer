from collections.abc import Mapping
from datetime import datetime
from math import isfinite

from app.models.signals import (
    LONG_TERM_TECHNICAL_INTERVALS,
    LongTermTechnicalProfile,
    SignalCategory,
    SignalContribution,
    SignalDirection,
    SignalStrength,
    SwingTradingSignalProfile,
    TechnicalSignalEvidence,
    TechnicalSignalSnapshot,
    classify_long_term_technical_stance,
    classify_swing_trading_stance,
)


DEFAULT_SWING_CATEGORY_WEIGHTS = {
    SignalCategory.TREND: 1.25,
    SignalCategory.MOMENTUM: 1.0,
    SignalCategory.VOLATILITY: 0.75,
    SignalCategory.VOLUME: 1.0,
    SignalCategory.PRICE_ACTION: 1.5,
}

DEFAULT_LONG_TERM_CATEGORY_WEIGHTS = {
    SignalCategory.TREND: 1.75,
    SignalCategory.MOMENTUM: 0.75,
    SignalCategory.VOLATILITY: 0.75,
    SignalCategory.VOLUME: 1.25,
    SignalCategory.PRICE_ACTION: 1.5,
}

DEFAULT_LONG_TERM_SOURCE_RELEVANCE = {
    "momentum_signals.rsi_mean_reversion": 0.65,
    "momentum_signals.macd_momentum": 0.85,
    "momentum_signals.stochastic_zone_crossover": 0.5,
    "price_action_signals.fair_value_gap_context": 0.35,
    "volatility_signals.bollinger_price_and_bandwidth": 0.6,
}

STRENGTH_VALUES = {
    SignalStrength.WEAK: 1 / 3,
    SignalStrength.MODERATE: 2 / 3,
    SignalStrength.STRONG: 1.0,
}

DIRECTION_MULTIPLIERS = {
    SignalDirection.BULLISH: 1.0,
    SignalDirection.BEARISH: -1.0,
    SignalDirection.NEUTRAL: 0.0,
}


def build_swing_trading_signal_profile(
    snapshot: TechnicalSignalSnapshot,
    *,
    category_weights: Mapping[SignalCategory | str, float] | None = None,
    minimum_coverage_percentage: float = 60.0,
    directional_threshold: float = 20.0,
    strong_threshold: float = 60.0,
    require_synchronized_evidence: bool = True,
) -> SwingTradingSignalProfile:
    """Aggregate deterministic evidence into a swing-trading profile."""
    _validate_snapshot(snapshot, profile_label="swing profile")
    if not isinstance(require_synchronized_evidence, bool):
        raise ValueError(
            "swing-profile synchronization setting must be a boolean"
        )
    if require_synchronized_evidence:
        _validate_synchronized_evidence(snapshot.evidence)

    weights = _normalize_category_weights(category_weights)
    minimum_coverage = _validate_percentage(
        "swing-profile minimum coverage",
        minimum_coverage_percentage,
        allow_zero=True,
    )
    directional = _validate_percentage(
        "swing-profile directional threshold",
        directional_threshold,
        allow_zero=False,
    )
    strong = _validate_percentage(
        "swing-profile strong threshold",
        strong_threshold,
        allow_zero=False,
    )
    if strong <= directional:
        raise ValueError(
            "swing-profile strong threshold must exceed directional "
            "threshold"
        )

    contributions = [
        _build_contribution(evidence)
        for evidence in snapshot.evidence
    ]
    covered_categories = [
        category
        for category in SignalCategory
        if any(
            evidence.category is category
            for evidence in snapshot.evidence
        )
    ]
    category_scores = {
        category.value: _category_score(contributions, category)
        for category in SignalCategory
    }

    covered_weight = sum(
        weights[category.value]
        for category in covered_categories
    )
    total_weight = sum(weights.values())
    coverage = covered_weight / total_weight * 100
    weighted_scores = [
        category_scores[category.value] * weights[category.value]
        for category in covered_categories
    ]
    score = (
        sum(weighted_scores) / covered_weight
        if covered_weight
        else 0.0
    )
    directional_weight = sum(abs(value) for value in weighted_scores)
    agreement = (
        abs(sum(weighted_scores)) / directional_weight * 100
        if directional_weight
        else 0.0
    )
    confidence = abs(score) * coverage / 100
    stance = classify_swing_trading_stance(
        score,
        coverage,
        minimum_coverage,
        directional,
        strong,
    )

    return SwingTradingSignalProfile(
        snapshot=snapshot,
        stance=stance,
        score=score,
        confidence_percentage=confidence,
        coverage_percentage=coverage,
        agreement_percentage=agreement,
        minimum_coverage_percentage=minimum_coverage,
        directional_threshold=directional,
        strong_threshold=strong,
        synchronized_evidence_required=(
            require_synchronized_evidence
        ),
        category_weights=weights,
        category_scores=category_scores,
        covered_categories=covered_categories,
        contributions=contributions,
        rationale=_build_rationale(
            stance.value,
            score,
            coverage,
            agreement,
            category_scores,
            covered_categories,
        ),
    )


def build_long_term_technical_profile(
    snapshot: TechnicalSignalSnapshot,
    *,
    category_weights: Mapping[SignalCategory | str, float] | None = None,
    source_relevance_weights: Mapping[str, float] | None = None,
    minimum_coverage_percentage: float = 70.0,
    directional_threshold: float = 20.0,
    strong_threshold: float = 60.0,
    require_synchronized_evidence: bool = True,
) -> LongTermTechnicalProfile:
    """Aggregate daily-or-higher evidence for a holding horizon."""
    _validate_snapshot(snapshot, profile_label="long-term profile")
    if (
        snapshot.interval.strip().upper()
        not in LONG_TERM_TECHNICAL_INTERVALS
    ):
        raise ValueError(
            "long-term profile requires daily, weekly, or monthly "
            "evidence"
        )
    if not isinstance(require_synchronized_evidence, bool):
        raise ValueError(
            "long-term profile synchronization setting must be a "
            "boolean"
        )
    if require_synchronized_evidence:
        _validate_synchronized_evidence(
            snapshot.evidence,
            profile_label="long-term profile",
        )

    weights = _normalize_long_term_category_weights(category_weights)
    source_relevance = _normalize_source_relevance(
        source_relevance_weights
    )
    minimum_coverage = _validate_percentage(
        "long-term profile minimum coverage",
        minimum_coverage_percentage,
        allow_zero=True,
    )
    directional = _validate_percentage(
        "long-term profile directional threshold",
        directional_threshold,
        allow_zero=False,
    )
    strong = _validate_percentage(
        "long-term profile strong threshold",
        strong_threshold,
        allow_zero=False,
    )
    if strong <= directional:
        raise ValueError(
            "long-term profile strong threshold must exceed "
            "directional threshold"
        )

    contributions = [
        _build_contribution(evidence)
        for evidence in snapshot.evidence
    ]
    relevance_by_evidence = {
        evidence.evidence_id: source_relevance.get(
            evidence.source,
            1.0,
        )
        for evidence in snapshot.evidence
    }
    covered_categories = [
        category
        for category in SignalCategory
        if any(
            evidence.category is category
            for evidence in snapshot.evidence
        )
    ]
    category_scores = {
        category.value: _long_term_category_score(
            contributions,
            category,
            relevance_by_evidence,
        )
        for category in SignalCategory
    }

    covered_weight = sum(
        weights[category.value]
        for category in covered_categories
    )
    total_weight = sum(weights.values())
    coverage = covered_weight / total_weight * 100
    weighted_scores = [
        category_scores[category.value] * weights[category.value]
        for category in covered_categories
    ]
    score = (
        sum(weighted_scores) / covered_weight
        if covered_weight
        else 0.0
    )
    directional_weight = sum(abs(value) for value in weighted_scores)
    agreement = (
        abs(sum(weighted_scores)) / directional_weight * 100
        if directional_weight
        else 0.0
    )
    confidence = abs(score) * coverage / 100
    stance = classify_long_term_technical_stance(
        score,
        coverage,
        minimum_coverage,
        directional,
        strong,
    )

    return LongTermTechnicalProfile(
        snapshot=snapshot,
        stance=stance,
        score=score,
        confidence_percentage=confidence,
        coverage_percentage=coverage,
        agreement_percentage=agreement,
        minimum_coverage_percentage=minimum_coverage,
        directional_threshold=directional,
        strong_threshold=strong,
        synchronized_evidence_required=(
            require_synchronized_evidence
        ),
        category_weights=weights,
        category_scores=category_scores,
        covered_categories=covered_categories,
        contributions=contributions,
        evidence_relevance_weights=relevance_by_evidence,
        rationale=_build_long_term_rationale(
            stance.value,
            score,
            coverage,
            agreement,
            category_scores,
            covered_categories,
            snapshot.interval,
        ),
    )
def _validate_snapshot(
    snapshot: TechnicalSignalSnapshot,
    *,
    profile_label: str,
) -> None:
    if not isinstance(snapshot, TechnicalSignalSnapshot):
        raise ValueError(
            f"{profile_label} requires a technical signal snapshot"
        )
    if snapshot.evaluated_at > snapshot.source_retrieved_at:
        raise ValueError(
            f"{profile_label} evaluation cannot follow source retrieval"
        )

    evidence_ids: set[str] = set()
    previous_availability: datetime | None = None
    for evidence in snapshot.evidence:
        if evidence.available_at > snapshot.evaluated_at:
            raise ValueError(
                f"{profile_label} cannot contain future evidence"
            )
        if (
            previous_availability is not None
            and evidence.available_at < previous_availability
        ):
            raise ValueError(
                f"{profile_label} evidence must be ordered by "
                "availability"
            )
        normalized_id = evidence.evidence_id.casefold()
        if normalized_id in evidence_ids:
            raise ValueError(
                f"{profile_label} evidence identifiers must be unique"
            )
        evidence_ids.add(normalized_id)
        previous_availability = evidence.available_at


def _validate_synchronized_evidence(
    evidence: list[TechnicalSignalEvidence],
    *,
    profile_label: str = "swing-profile",
) -> None:
    availability_times = {
        item.available_at
        for item in evidence
    }
    if len(availability_times) > 1:
        raise ValueError(
            f"{profile_label} evidence must share one availability time"
        )


def _normalize_category_weights(
    overrides: Mapping[SignalCategory | str, float] | None,
) -> dict[str, float]:
    normalized = {
        category.value: weight
        for category, weight in DEFAULT_SWING_CATEGORY_WEIGHTS.items()
    }
    if overrides is None:
        return normalized
    if not isinstance(overrides, Mapping):
        raise ValueError(
            "swing-profile category weights must be a mapping"
        )

    seen: set[SignalCategory] = set()
    for raw_category, raw_weight in overrides.items():
        try:
            category = (
                raw_category
                if isinstance(raw_category, SignalCategory)
                else SignalCategory(str(raw_category).strip().lower())
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"unknown swing-profile category: {raw_category}"
            ) from exc
        if category in seen:
            raise ValueError(
                "swing-profile category weights cannot contain "
                "duplicate categories"
            )
        seen.add(category)
        normalized[category.value] = _validate_positive_weight(
            category,
            raw_weight,
        )
    return normalized


def _validate_positive_weight(
    category: SignalCategory,
    value: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"swing-profile {category.value} weight must be a number"
        )
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(
            f"swing-profile {category.value} weight must be positive "
            "and finite"
        )
    return normalized


def _normalize_long_term_category_weights(
    overrides: Mapping[SignalCategory | str, float] | None,
) -> dict[str, float]:
    normalized = {
        category.value: weight
        for category, weight in (
            DEFAULT_LONG_TERM_CATEGORY_WEIGHTS.items()
        )
    }
    if overrides is None:
        return normalized
    if not isinstance(overrides, Mapping):
        raise ValueError(
            "long-term category weights must be a mapping"
        )

    for raw_category, raw_weight in overrides.items():
        try:
            category = (
                raw_category
                if isinstance(raw_category, SignalCategory)
                else SignalCategory(str(raw_category).strip().lower())
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"unknown long-term profile category: {raw_category}"
            ) from exc
        normalized[category.value] = _validate_long_term_weight(
            category,
            raw_weight,
        )
    return normalized


def _validate_long_term_weight(
    category: SignalCategory,
    value: float,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(
            f"long-term {category.value} weight must be a number"
        )
    normalized = float(value)
    if not isfinite(normalized) or normalized <= 0:
        raise ValueError(
            f"long-term {category.value} weight must be positive and "
            "finite"
        )
    return normalized


def _normalize_source_relevance(
    overrides: Mapping[str, float] | None,
) -> dict[str, float]:
    normalized = dict(DEFAULT_LONG_TERM_SOURCE_RELEVANCE)
    if overrides is None:
        return normalized
    if not isinstance(overrides, Mapping):
        raise ValueError(
            "long-term source relevance weights must be a mapping"
        )

    for source, value in overrides.items():
        if not isinstance(source, str) or not source.strip():
            raise ValueError(
                "long-term relevance source names cannot be blank"
            )
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(
                f"long-term relevance for {source} must be a number"
            )
        relevance = float(value)
        if not isfinite(relevance) or not 0 < relevance <= 1:
            raise ValueError(
                f"long-term relevance for {source} must be above 0 "
                "and at most 1"
            )
        normalized[source] = relevance
    return normalized


def _validate_percentage(
    name: str,
    value: float,
    *,
    allow_zero: bool,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be a number")
    normalized = float(value)
    if not isfinite(normalized):
        raise ValueError(f"{name} must be finite")
    minimum = 0.0 if allow_zero else 0.0
    if normalized < minimum or (not allow_zero and normalized == 0):
        qualifier = "between 0 and 100" if allow_zero else "above 0"
        raise ValueError(f"{name} must be {qualifier}")
    if normalized > 100:
        raise ValueError(f"{name} cannot exceed 100")
    return normalized


def _build_contribution(
    evidence: TechnicalSignalEvidence,
) -> SignalContribution:
    strength_value = STRENGTH_VALUES[evidence.strength]
    signed_value = (
        strength_value * DIRECTION_MULTIPLIERS[evidence.direction]
    )
    return SignalContribution(
        evidence_id=evidence.evidence_id,
        category=evidence.category,
        direction=evidence.direction,
        strength=evidence.strength,
        strength_value=strength_value,
        signed_value=signed_value,
    )


def _category_score(
    contributions: list[SignalContribution],
    category: SignalCategory,
) -> float:
    directional_values = [
        contribution.signed_value
        for contribution in contributions
        if (
            contribution.category is category
            and contribution.direction is not SignalDirection.NEUTRAL
        )
    ]
    if not directional_values:
        return 0.0
    return sum(directional_values) / len(directional_values) * 100


def _long_term_category_score(
    contributions: list[SignalContribution],
    category: SignalCategory,
    relevance_by_evidence: dict[str, float],
) -> float:
    directional = [
        contribution
        for contribution in contributions
        if (
            contribution.category is category
            and contribution.direction is not SignalDirection.NEUTRAL
        )
    ]
    if not directional:
        return 0.0

    relevance = [
        relevance_by_evidence[contribution.evidence_id]
        for contribution in directional
    ]
    weighted_sum = sum(
        contribution.signed_value * weight
        for contribution, weight in zip(directional, relevance)
    )
    return (
        weighted_sum
        / sum(relevance)
        * max(relevance)
        * 100
    )


def _build_rationale(
    stance: str,
    score: float,
    coverage: float,
    agreement: float,
    category_scores: dict[str, float],
    covered_categories: list[SignalCategory],
) -> str:
    bullish = [
        category.value
        for category in covered_categories
        if category_scores[category.value] > 0
    ]
    bearish = [
        category.value
        for category in covered_categories
        if category_scores[category.value] < 0
    ]
    neutral = [
        category.value
        for category in covered_categories
        if category_scores[category.value] == 0
    ]
    return (
        f"Swing profile is {stance} with score {score:.6f}, coverage "
        f"{coverage:.6f}%, and directional agreement "
        f"{agreement:.6f}%. Bullish categories: "
        f"{_category_list(bullish)}; bearish categories: "
        f"{_category_list(bearish)}; neutral categories: "
        f"{_category_list(neutral)}. This deterministic technical "
        "profile is not a trade recommendation."
    )


def _category_list(categories: list[str]) -> str:
    return ",".join(categories) if categories else "none"


def _build_long_term_rationale(
    stance: str,
    score: float,
    coverage: float,
    agreement: float,
    category_scores: dict[str, float],
    covered_categories: list[SignalCategory],
    interval: str,
) -> str:
    bullish = [
        category.value
        for category in covered_categories
        if category_scores[category.value] > 0
    ]
    bearish = [
        category.value
        for category in covered_categories
        if category_scores[category.value] < 0
    ]
    neutral = [
        category.value
        for category in covered_categories
        if category_scores[category.value] == 0
    ]
    return (
        f"Long-term {interval} technical profile is {stance} with "
        f"score {score:.6f}, coverage {coverage:.6f}%, and directional "
        f"agreement {agreement:.6f}%. Bullish categories: "
        f"{_category_list(bullish)}; bearish categories: "
        f"{_category_list(bearish)}; neutral categories: "
        f"{_category_list(neutral)}. Short-lived evidence is horizon-"
        "adjusted. This deterministic holding-structure profile is not "
        "a trade recommendation."
    )
