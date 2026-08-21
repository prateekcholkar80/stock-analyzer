from datetime import datetime
from enum import StrEnum
from math import isclose, isfinite
from typing import Self

from pydantic import (
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.models.technical import TechnicalModel


SignalValue = bool | int | float | str


class SignalDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    NEUTRAL = "neutral"


class SignalCategory(StrEnum):
    TREND = "trend"
    MOMENTUM = "momentum"
    VOLATILITY = "volatility"
    VOLUME = "volume"
    PRICE_ACTION = "price_action"
    CANDLESTICK = "candlestick"


class SignalStrength(StrEnum):
    WEAK = "weak"
    MODERATE = "moderate"
    STRONG = "strong"


class SignalProvenance(StrEnum):
    DETERMINISTIC = "deterministic"


class SwingTradingStance(StrEnum):
    STRONG_BULLISH = "strong_bullish_setup"
    BULLISH = "bullish_setup"
    NEUTRAL = "neutral"
    BEARISH = "bearish_setup"
    STRONG_BEARISH = "strong_bearish_setup"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class LongTermTechnicalStance(StrEnum):
    STRONG_BULLISH = "strong_bullish_holding_structure"
    BULLISH = "bullish_holding_structure"
    NEUTRAL = "neutral_consolidating"
    BEARISH = "bearish_deterioration"
    STRONG_BEARISH = "strong_bearish_deterioration"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


LONG_TERM_TECHNICAL_INTERVALS = frozenset(
    {
        "ONE_DAY",
        "DAY",
        "DAILY",
        "1D",
        "ONE_WEEK",
        "WEEK",
        "WEEKLY",
        "1W",
        "ONE_MONTH",
        "MONTH",
        "MONTHLY",
        "1MO",
    }
)


def _validate_values(
    values: dict[str, SignalValue],
    *,
    field_label: str,
) -> dict[str, SignalValue]:
    for name, value in values.items():
        if not name.strip():
            raise ValueError(f"{field_label} names cannot be blank")

        if isinstance(value, float) and not isfinite(value):
            raise ValueError(
                f"numeric {field_label} values must be finite"
            )

        if isinstance(value, str) and not value.strip():
            raise ValueError(f"text {field_label} values cannot be blank")

    return values


class TechnicalSignalEvidence(TechnicalModel):
    evidence_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    name: str = Field(min_length=1)
    category: SignalCategory
    direction: SignalDirection
    strength: SignalStrength
    source: str = Field(min_length=1)
    explanation: str = Field(min_length=1)
    observed_at: datetime
    available_at: datetime
    observed_values: dict[str, SignalValue] = Field(min_length=1)
    parameters: dict[str, SignalValue] = Field(default_factory=dict)
    provenance: SignalProvenance = SignalProvenance.DETERMINISTIC

    @field_validator("name", "source", "explanation")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("signal evidence text cannot be blank")

        return value

    @field_validator("observed_at", "available_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "signal evidence timestamps must include timezone "
                "information"
            )

        return value

    @field_validator("observed_values")
    @classmethod
    def validate_observed_values(
        cls,
        values: dict[str, SignalValue],
    ) -> dict[str, SignalValue]:
        return _validate_values(
            values,
            field_label="observed-value",
        )

    @field_validator("parameters")
    @classmethod
    def validate_parameters(
        cls,
        values: dict[str, SignalValue],
    ) -> dict[str, SignalValue]:
        return _validate_values(
            values,
            field_label="signal-parameter",
        )

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.available_at < self.observed_at:
            raise ValueError(
                "signal availability cannot precede observation"
            )

        return self


class TechnicalSignalSnapshot(TechnicalModel):
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_retrieved_at: datetime
    evaluated_at: datetime
    evidence: list[TechnicalSignalEvidence] = Field(default_factory=list)

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "source",
    )
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("technical-signal metadata cannot be blank")

        return value

    @field_validator("source_retrieved_at", "evaluated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "technical-signal timestamps must include timezone "
                "information"
            )

        return value

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.evaluated_at > self.source_retrieved_at:
            raise ValueError(
                "signal evaluation cannot follow source retrieval"
            )

        evidence_ids: set[str] = set()
        previous_availability: datetime | None = None
        for item in self.evidence:
            if item.available_at > self.evaluated_at:
                raise ValueError(
                    "signal snapshot cannot contain future evidence"
                )

            if (
                previous_availability is not None
                and item.available_at < previous_availability
            ):
                raise ValueError(
                    "signal evidence must be ordered by availability"
                )

            normalized_id = item.evidence_id.casefold()
            if normalized_id in evidence_ids:
                raise ValueError(
                    "signal evidence identifiers must be unique"
                )

            evidence_ids.add(normalized_id)
            previous_availability = item.available_at

        return self

    @computed_field
    @property
    def bullish_count(self) -> int:
        return self._count_direction(SignalDirection.BULLISH)

    @computed_field
    @property
    def bearish_count(self) -> int:
        return self._count_direction(SignalDirection.BEARISH)

    @computed_field
    @property
    def neutral_count(self) -> int:
        return self._count_direction(SignalDirection.NEUTRAL)

    @computed_field
    @property
    def has_directional_conflict(self) -> bool:
        return self.bullish_count > 0 and self.bearish_count > 0

    def _count_direction(self, direction: SignalDirection) -> int:
        return sum(
            item.direction is direction
            for item in self.evidence
        )


class SignalContribution(TechnicalModel):
    evidence_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    category: SignalCategory
    direction: SignalDirection
    strength: SignalStrength
    strength_value: float = Field(ge=0, le=1)
    signed_value: float = Field(ge=-1, le=1)

    @field_validator("strength_value", "signed_value", mode="before")
    @classmethod
    def require_finite_score(cls, value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            raise ValueError("signal contribution values must be finite")
        return float(value)

    @model_validator(mode="after")
    def validate_contribution(self) -> Self:
        expected_strength = {
            SignalStrength.WEAK: 1 / 3,
            SignalStrength.MODERATE: 2 / 3,
            SignalStrength.STRONG: 1.0,
        }[self.strength]
        if not isclose(
            self.strength_value,
            expected_strength,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "signal contribution strength value does not match "
                "strength"
            )

        multiplier = {
            SignalDirection.BULLISH: 1.0,
            SignalDirection.BEARISH: -1.0,
            SignalDirection.NEUTRAL: 0.0,
        }[self.direction]
        expected_signed_value = expected_strength * multiplier
        if not isclose(
            self.signed_value,
            expected_signed_value,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "signal contribution signed value does not match "
                "direction and strength"
            )
        return self


class SwingTradingSignalProfile(TechnicalModel):
    profile_id: str = Field(
        default="swing_trading",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    snapshot: TechnicalSignalSnapshot
    stance: SwingTradingStance
    score: float = Field(ge=-100, le=100)
    confidence_percentage: float = Field(ge=0, le=100)
    coverage_percentage: float = Field(ge=0, le=100)
    agreement_percentage: float = Field(ge=0, le=100)
    minimum_coverage_percentage: float = Field(ge=0, le=100)
    directional_threshold: float = Field(gt=0, le=100)
    strong_threshold: float = Field(gt=0, le=100)
    synchronized_evidence_required: bool
    category_weights: dict[str, float]
    category_scores: dict[str, float]
    covered_categories: list[SignalCategory]
    contributions: list[SignalContribution]
    rationale: str = Field(min_length=1)

    @field_validator(
        "score",
        "confidence_percentage",
        "coverage_percentage",
        "agreement_percentage",
        "minimum_coverage_percentage",
        "directional_threshold",
        "strong_threshold",
        mode="before",
    )
    @classmethod
    def require_finite_metric(cls, value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            raise ValueError("swing-profile metrics must be finite")
        return float(value)

    @field_validator("synchronized_evidence_required", mode="before")
    @classmethod
    def require_boolean_synchronization(cls, value: bool) -> bool:
        if not isinstance(value, bool):
            raise ValueError(
                "swing-profile synchronization setting must be a "
                "boolean"
            )
        return value

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("swing-profile rationale cannot be blank")
        return value

    @field_validator("category_weights", mode="before")
    @classmethod
    def validate_category_weights(
        cls,
        values: dict[str, float],
    ) -> dict[str, float]:
        expected = {category.value for category in SignalCategory}
        if set(values) != expected:
            raise ValueError(
                "swing-profile weights must cover every signal category"
            )
        if any(
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
            or value <= 0
            for value in values.values()
        ):
            raise ValueError(
                "swing-profile category weights must be positive finite "
                "numbers"
            )
        return {name: float(value) for name, value in values.items()}

    @field_validator("category_scores", mode="before")
    @classmethod
    def validate_category_scores(
        cls,
        values: dict[str, float],
    ) -> dict[str, float]:
        expected = {category.value for category in SignalCategory}
        if set(values) != expected:
            raise ValueError(
                "swing-profile scores must cover every signal category"
            )
        normalized = {}
        for name, value in values.items():
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(float(value))
                or not -100 <= value <= 100
            ):
                raise ValueError(
                    "swing-profile category scores must be finite and "
                    "between -100 and 100"
                )
            normalized[name] = float(value)
        return normalized

    @model_validator(mode="after")
    def validate_profile(self) -> Self:
        if self.strong_threshold <= self.directional_threshold:
            raise ValueError(
                "swing-profile strong threshold must exceed directional "
                "threshold"
            )

        expected_covered = [
            category
            for category in SignalCategory
            if any(
                evidence.category is category
                for evidence in self.snapshot.evidence
            )
        ]
        if self.covered_categories != expected_covered:
            raise ValueError(
                "swing-profile covered categories do not match evidence"
            )

        if len(self.contributions) != len(self.snapshot.evidence):
            raise ValueError(
                "swing-profile requires one contribution per evidence"
            )
        for evidence, contribution in zip(
            self.snapshot.evidence,
            self.contributions,
        ):
            if (
                contribution.evidence_id != evidence.evidence_id
                or contribution.category is not evidence.category
                or contribution.direction is not evidence.direction
                or contribution.strength is not evidence.strength
            ):
                raise ValueError(
                    "swing-profile contributions must match snapshot "
                    "evidence"
                )

        expected_category_scores = self._expected_category_scores()
        for category in SignalCategory:
            if not isclose(
                self.category_scores[category.value],
                expected_category_scores[category.value],
                rel_tol=1e-9,
                abs_tol=1e-9,
            ):
                raise ValueError(
                    "technical-profile category scores do not match "
                    "evidence contributions"
                )

        covered_weight = sum(
            self.category_weights[category.value]
            for category in self.covered_categories
        )
        total_weight = sum(self.category_weights.values())
        expected_coverage = covered_weight / total_weight * 100
        if not isclose(
            self.coverage_percentage,
            expected_coverage,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "swing-profile coverage does not match category weights"
            )

        weighted_values = [
            self.category_scores[category.value]
            * self.category_weights[category.value]
            for category in self.covered_categories
        ]
        expected_score = (
            sum(weighted_values) / covered_weight
            if covered_weight
            else 0.0
        )
        if not isclose(
            self.score,
            expected_score,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "swing-profile score does not match category scores"
            )

        directional_weight = sum(abs(value) for value in weighted_values)
        expected_agreement = (
            abs(sum(weighted_values)) / directional_weight * 100
            if directional_weight
            else 0.0
        )
        if not isclose(
            self.agreement_percentage,
            expected_agreement,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "swing-profile agreement does not match category scores"
            )

        expected_confidence = (
            abs(expected_score) * expected_coverage / 100
        )
        if not isclose(
            self.confidence_percentage,
            expected_confidence,
            rel_tol=1e-9,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "swing-profile confidence does not match score and "
                "coverage"
            )

        expected_stance = self._classify_stance(
            expected_score,
            expected_coverage,
        )
        if self.stance is not expected_stance:
            raise ValueError(
                "swing-profile stance does not match calculated metrics"
            )
        return self

    @computed_field
    @property
    def direction(self) -> SignalDirection:
        return self._stance_direction()

    @computed_field
    @property
    def has_directional_conflict(self) -> bool:
        return self.snapshot.has_directional_conflict

    def _expected_category_scores(self) -> dict[str, float]:
        scores = {}
        for category in SignalCategory:
            directional_values = [
                contribution.signed_value
                for contribution in self.contributions
                if (
                    contribution.category is category
                    and contribution.direction
                    is not SignalDirection.NEUTRAL
                )
            ]
            scores[category.value] = (
                sum(directional_values)
                / len(directional_values)
                * 100
                if directional_values
                else 0.0
            )
        return scores

    def _classify_stance(
        self,
        score: float,
        coverage: float,
    ) -> SwingTradingStance:
        return classify_swing_trading_stance(
            score,
            coverage,
            self.minimum_coverage_percentage,
            self.directional_threshold,
            self.strong_threshold,
        )

    def _stance_direction(self) -> SignalDirection:
        if self.stance in (
            SwingTradingStance.STRONG_BULLISH,
            SwingTradingStance.BULLISH,
        ):
            return SignalDirection.BULLISH
        if self.stance in (
            SwingTradingStance.STRONG_BEARISH,
            SwingTradingStance.BEARISH,
        ):
            return SignalDirection.BEARISH
        return SignalDirection.NEUTRAL


class LongTermTechnicalProfile(SwingTradingSignalProfile):
    profile_id: str = Field(
        default="long_term_technical",
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    stance: LongTermTechnicalStance
    evidence_relevance_weights: dict[str, float]

    @field_validator("evidence_relevance_weights", mode="before")
    @classmethod
    def validate_evidence_relevance_weights(
        cls,
        values: dict[str, float],
    ) -> dict[str, float]:
        if not isinstance(values, dict):
            raise ValueError(
                "long-term evidence relevance weights must be a mapping"
            )
        normalized = {}
        for evidence_id, value in values.items():
            if not isinstance(evidence_id, str) or not evidence_id.strip():
                raise ValueError(
                    "long-term relevance identifiers cannot be blank"
                )
            if (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not isfinite(float(value))
                or not 0 < value <= 1
            ):
                raise ValueError(
                    "long-term evidence relevance must be above 0 and "
                    "at most 1"
                )
            normalized[evidence_id] = float(value)
        return normalized

    @model_validator(mode="after")
    def validate_long_term_profile(self) -> Self:
        if (
            self.snapshot.interval.strip().upper()
            not in LONG_TERM_TECHNICAL_INTERVALS
        ):
            raise ValueError(
                "long-term profile requires daily, weekly, or monthly "
                "evidence"
            )
        evidence_ids = {
            evidence.evidence_id
            for evidence in self.snapshot.evidence
        }
        if set(self.evidence_relevance_weights) != evidence_ids:
            raise ValueError(
                "long-term relevance weights must match snapshot "
                "evidence"
            )
        return self

    def _expected_category_scores(self) -> dict[str, float]:
        evidence_ids = {
            contribution.evidence_id
            for contribution in self.contributions
        }
        if set(self.evidence_relevance_weights) != evidence_ids:
            raise ValueError(
                "long-term relevance weights must match evidence "
                "contributions"
            )
        scores = {}
        for category in SignalCategory:
            directional = [
                contribution
                for contribution in self.contributions
                if (
                    contribution.category is category
                    and contribution.direction
                    is not SignalDirection.NEUTRAL
                )
            ]
            if not directional:
                scores[category.value] = 0.0
                continue

            relevance = [
                self.evidence_relevance_weights[
                    contribution.evidence_id
                ]
                for contribution in directional
            ]
            weighted_sum = sum(
                contribution.signed_value * weight
                for contribution, weight in zip(
                    directional,
                    relevance,
                )
            )
            scores[category.value] = (
                weighted_sum
                / sum(relevance)
                * max(relevance)
                * 100
            )
        return scores

    def _classify_stance(
        self,
        score: float,
        coverage: float,
    ) -> LongTermTechnicalStance:
        return classify_long_term_technical_stance(
            score,
            coverage,
            self.minimum_coverage_percentage,
            self.directional_threshold,
            self.strong_threshold,
        )

    def _stance_direction(self) -> SignalDirection:
        if self.stance in (
            LongTermTechnicalStance.STRONG_BULLISH,
            LongTermTechnicalStance.BULLISH,
        ):
            return SignalDirection.BULLISH
        if self.stance in (
            LongTermTechnicalStance.STRONG_BEARISH,
            LongTermTechnicalStance.BEARISH,
        ):
            return SignalDirection.BEARISH
        return SignalDirection.NEUTRAL


def classify_swing_trading_stance(
    score: float,
    coverage: float,
    minimum_coverage: float,
    directional_threshold: float,
    strong_threshold: float,
) -> SwingTradingStance:
    if coverage < minimum_coverage:
        return SwingTradingStance.INSUFFICIENT_EVIDENCE
    if score >= strong_threshold:
        return SwingTradingStance.STRONG_BULLISH
    if score >= directional_threshold:
        return SwingTradingStance.BULLISH
    if score <= -strong_threshold:
        return SwingTradingStance.STRONG_BEARISH
    if score <= -directional_threshold:
        return SwingTradingStance.BEARISH
    return SwingTradingStance.NEUTRAL


def classify_long_term_technical_stance(
    score: float,
    coverage: float,
    minimum_coverage: float,
    directional_threshold: float,
    strong_threshold: float,
) -> LongTermTechnicalStance:
    if coverage < minimum_coverage:
        return LongTermTechnicalStance.INSUFFICIENT_EVIDENCE
    if score >= strong_threshold:
        return LongTermTechnicalStance.STRONG_BULLISH
    if score >= directional_threshold:
        return LongTermTechnicalStance.BULLISH
    if score <= -strong_threshold:
        return LongTermTechnicalStance.STRONG_BEARISH
    if score <= -directional_threshold:
        return LongTermTechnicalStance.BEARISH
    return LongTermTechnicalStance.NEUTRAL
