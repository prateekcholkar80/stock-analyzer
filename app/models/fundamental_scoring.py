"""Immutable policy contracts for deterministic fundamental scorecards.

These models define how a future scoring engine is allowed to interpret
normalized financial evidence. They do not calculate company scores and they
contain no provider, database, or LLM dependency.
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_assessment import (
    FundamentalAssessmentDomain,
    FundamentalFindingSeverity,
    FundamentalFindingStatus,
)
from app.models.fundamental_frameworks import (
    FundamentalFramework,
    FundamentalFrameworkApplicabilityStatus,
    FundamentalFrameworkEvidenceFamily,
)
from app.models.fundamental_metrics import (
    FundamentalMetricCitation,
    FundamentalMetricPeriodKind,
    FundamentalMetricUnit,
)
from app.models.fundamentals import FundamentalIssuerIdentity, FundamentalModel


_POLICY_ID_PATTERN = r"^[a-z][a-z0-9_.-]*$"
_RULE_ID_PATTERN = r"^[a-z][a-z0-9_.-]*$"
_SEMVER_PATTERN = r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_SCORE_QUANTUM = Decimal("0.01")
_CONTRIBUTION_QUANTUM = Decimal("0.0001")
_MINIMUM_SCORE = Decimal("0")
_MAXIMUM_SCORE = Decimal("100")
_TOTAL_WEIGHT_BPS = 10_000


class FundamentalScoreGrade(StrEnum):
    """Deterministic interpretation band for an observed score."""

    ADVERSE = "adverse"
    WEAK = "weak"
    MIXED = "mixed"
    FAVOURABLE = "favourable"
    STRONG = "strong"


class FundamentalScorecardStance(StrEnum):
    """Preliminary fundamental stance, separate from a trade decision."""

    HEALTHY = "healthy"
    CAUTION = "caution"
    WEAK = "weak"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class FundamentalDecisionInfluenceMode(StrEnum):
    """Approved downstream authority of a fundamental scorecard."""

    SHADOW = "shadow"
    ADVISORY = "advisory"
    APPROVED_GATE = "approved_gate"


class FundamentalScoreResultStatus(StrEnum):
    """Whether one scoring layer released a numeric result."""

    CALCULATED = "calculated"
    WITHHELD = "withheld"
    NOT_APPLICABLE = "not_applicable"


class FundamentalScoreWithholdingReason(StrEnum):
    """Why a numeric score was deliberately not released."""

    MISSING_EVIDENCE = "missing_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    UNSUPPORTED_POLICY = "unsupported_policy"
    INELIGIBLE_FRAMEWORK = "ineligible_framework"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"


class FundamentalMetricAggregation(StrEnum):
    """Approved deterministic reducers for a metric observation window."""

    LATEST = "latest"
    MEAN = "mean"
    MEDIAN = "median"
    MINIMUM = "minimum"
    MAXIMUM = "maximum"


def _fingerprint(model: FundamentalModel, field_name: str) -> str:
    payload = model.model_dump(mode="json", exclude={field_name})
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return sha256(encoded).hexdigest()


def _canonical_enum_values(values: tuple[StrEnum, ...], enum_type: type[StrEnum]):
    return tuple(member for member in enum_type if member in values)


class FundamentalMetricScoreBand(FundamentalModel):
    """One total-order interval mapped to a deterministic score."""

    band_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_RULE_ID_PATTERN,
    )
    minimum_inclusive: Decimal | None = None
    maximum_exclusive: Decimal | None = None
    score: Decimal = Field(
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=2,
    )
    grade: FundamentalScoreGrade
    summary: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_interval(self) -> Self:
        if (
            self.minimum_inclusive is not None
            and self.maximum_exclusive is not None
            and self.minimum_inclusive >= self.maximum_exclusive
        ):
            raise ValueError("score-band minimum must be below its maximum")
        if self.score != self.score.quantize(_SCORE_QUANTUM):
            raise ValueError("score-band points require at most two decimals")
        return self


class FundamentalMetricScoringRule(FundamentalModel):
    """Versioned metric-to-score mapping inside one evidence family."""

    rule_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_RULE_ID_PATTERN,
    )
    rule_version: str = Field(pattern=_SEMVER_PATTERN)
    metric_ids: tuple[str, ...] = Field(min_length=1, max_length=16)
    expected_unit: FundamentalMetricUnit
    allowed_reporting_bases: tuple[FinancialReportingBasis, ...] = Field(
        min_length=1,
        max_length=len(FinancialReportingBasis),
    )
    period_kind: FundamentalMetricPeriodKind
    minimum_observations: int = Field(ge=1, le=40)
    require_consecutive_periods: bool
    aggregation: FundamentalMetricAggregation
    component_weight_bps: int = Field(ge=1, le=_TOTAL_WEIGHT_BPS)
    bands: tuple[FundamentalMetricScoreBand, ...] = Field(
        min_length=2,
        max_length=20,
    )

    @field_validator("metric_ids")
    @classmethod
    def require_canonical_metric_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(
            not value
            or len(value) > 160
            or not value[0].isalpha()
            for value in values
        ):
            raise ValueError("scoring metric IDs must be bounded identifiers")
        if len(values) != len(set(values)):
            raise ValueError("scoring metric IDs must be unique")
        if values != tuple(sorted(values)):
            raise ValueError("scoring metric IDs must use canonical order")
        return values

    @field_validator("allowed_reporting_bases")
    @classmethod
    def require_canonical_reporting_bases(
        cls,
        values: tuple[FinancialReportingBasis, ...],
    ) -> tuple[FinancialReportingBasis, ...]:
        if len(values) != len(set(values)):
            raise ValueError("allowed reporting bases must be unique")
        if values != _canonical_enum_values(values, FinancialReportingBasis):
            raise ValueError("allowed reporting bases must use canonical order")
        return values

    @model_validator(mode="after")
    def validate_bands(self) -> Self:
        if self.period_kind is FundamentalMetricPeriodKind.UNKNOWN:
            raise ValueError("metric scoring period kind cannot be unknown")
        if (
            self.period_kind is FundamentalMetricPeriodKind.POINT_IN_TIME
            and self.require_consecutive_periods
        ):
            raise ValueError(
                "point-in-time scoring observations cannot require consecutive "
                "periods"
            )
        band_ids = tuple(band.band_id for band in self.bands)
        if len(band_ids) != len(set(band_ids)):
            raise ValueError("metric scoring band IDs must be unique")
        if self.bands[0].minimum_inclusive is not None:
            raise ValueError("metric scoring bands must start at negative infinity")
        if self.bands[-1].maximum_exclusive is not None:
            raise ValueError("metric scoring bands must end at positive infinity")
        for prior, current in zip(self.bands[:-1], self.bands[1:], strict=True):
            if prior.maximum_exclusive is None:
                raise ValueError("only the final score band may be unbounded above")
            if current.minimum_inclusive != prior.maximum_exclusive:
                raise ValueError(
                    "metric scoring bands must be ordered, contiguous, and "
                    "non-overlapping"
                )
        return self

    @computed_field
    @property
    def policy_fingerprint(self) -> str:
        return _fingerprint(self, "policy_fingerprint")


class FundamentalEvidenceFamilyScoringPolicy(FundamentalModel):
    """Capped group of correlated metric rules."""

    family: FundamentalFrameworkEvidenceFamily
    framework_weight_bps: int = Field(ge=1, le=_TOTAL_WEIGHT_BPS)
    minimum_rule_coverage_bps: int = Field(ge=1, le=_TOTAL_WEIGHT_BPS)
    maximum_family_score: Decimal = Field(
        default=_MAXIMUM_SCORE,
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=2,
    )
    metric_rules: tuple[FundamentalMetricScoringRule, ...] = Field(
        min_length=1,
        max_length=64,
    )

    @model_validator(mode="after")
    def validate_family_policy(self) -> Self:
        rule_ids = tuple(rule.rule_id for rule in self.metric_rules)
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("family scoring rule IDs must be unique")
        if rule_ids != tuple(sorted(rule_ids)):
            raise ValueError("family scoring rules must use canonical order")
        if sum(rule.component_weight_bps for rule in self.metric_rules) != (
            _TOTAL_WEIGHT_BPS
        ):
            raise ValueError("family component weights must total 10000 bps")
        if self.maximum_family_score != self.maximum_family_score.quantize(
            _SCORE_QUANTUM
        ):
            raise ValueError("maximum family score requires at most two decimals")
        return self

    @computed_field
    @property
    def policy_fingerprint(self) -> str:
        return _fingerprint(self, "policy_fingerprint")


class FundamentalFrameworkScoringPolicy(FundamentalModel):
    """Complete deterministic scoring policy for one eligible framework."""

    framework: FundamentalFramework
    policy_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    policy_version: str = Field(pattern=_SEMVER_PATTERN)
    requires_eligible_applicability: Literal[True] = True
    minimum_family_coverage_bps: int = Field(
        ge=1,
        le=_TOTAL_WEIGHT_BPS,
    )
    family_policies: tuple[FundamentalEvidenceFamilyScoringPolicy, ...] = Field(
        min_length=1,
        max_length=len(FundamentalFrameworkEvidenceFamily),
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("limitations")
    @classmethod
    def require_bounded_unique_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("framework scoring limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("framework scoring limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_framework_policy(self) -> Self:
        expected_policy_id = f"jarvis.framework_score.{self.framework.value}"
        if self.policy_id != expected_policy_id:
            raise ValueError("framework scoring policy ID is not stable")
        families = tuple(policy.family for policy in self.family_policies)
        if len(families) != len(set(families)):
            raise ValueError("framework scoring families must be unique")
        if families != _canonical_enum_values(
            families,
            FundamentalFrameworkEvidenceFamily,
        ):
            raise ValueError("framework scoring families must use canonical order")
        if sum(
            policy.framework_weight_bps for policy in self.family_policies
        ) != _TOTAL_WEIGHT_BPS:
            raise ValueError("framework family weights must total 10000 bps")
        return self

    @computed_field
    @property
    def policy_fingerprint(self) -> str:
        return _fingerprint(self, "policy_fingerprint")


class FundamentalMandatoryScoreConstraint(FundamentalModel):
    """Non-offsettable stance ceiling triggered by a mandatory finding."""

    constraint_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_RULE_ID_PATTERN,
    )
    domain: FundamentalAssessmentDomain
    trigger_status: FundamentalFindingStatus
    minimum_severity: FundamentalFindingSeverity
    stance_ceiling: FundamentalScorecardStance
    review_required: Literal[True] = True
    summary: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_constraint(self) -> Self:
        allowed_severity = {
            FundamentalFindingStatus.WARN: {
                FundamentalFindingSeverity.LOW,
                FundamentalFindingSeverity.MEDIUM,
                FundamentalFindingSeverity.HIGH,
            },
            FundamentalFindingStatus.FAIL: {
                FundamentalFindingSeverity.MEDIUM,
                FundamentalFindingSeverity.HIGH,
                FundamentalFindingSeverity.CRITICAL,
            },
        }
        if self.trigger_status not in allowed_severity:
            raise ValueError("mandatory score constraint requires WARN or FAIL")
        if self.minimum_severity not in allowed_severity[self.trigger_status]:
            raise ValueError("constraint severity contradicts its finding status")
        if self.stance_ceiling not in {
            FundamentalScorecardStance.CAUTION,
            FundamentalScorecardStance.WEAK,
        }:
            raise ValueError("mandatory constraints require caution or weak ceiling")
        return self


class FundamentalScoreGradeThresholds(FundamentalModel):
    """Versioned boundaries for converting points into score grades."""

    weak_minimum: Decimal = Field(
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=2,
    )
    mixed_minimum: Decimal = Field(
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=2,
    )
    favourable_minimum: Decimal = Field(
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=2,
    )
    strong_minimum: Decimal = Field(
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=2,
    )

    @model_validator(mode="after")
    def validate_thresholds(self) -> Self:
        thresholds = (
            self.weak_minimum,
            self.mixed_minimum,
            self.favourable_minimum,
            self.strong_minimum,
        )
        if any(value != value.quantize(_SCORE_QUANTUM) for value in thresholds):
            raise ValueError("score-grade thresholds require at most two decimals")
        if thresholds != tuple(sorted(set(thresholds))):
            raise ValueError("score-grade thresholds must be strictly increasing")
        return self


class FundamentalStancePolicy(FundamentalModel):
    """Non-averaging agreement rules for the preliminary stance."""

    aggregation_method: Literal["selected_framework_count"] = (
        "selected_framework_count"
    )
    minimum_scored_frameworks: int = Field(ge=1, le=3)
    healthy_minimum_frameworks: int = Field(ge=1, le=3)
    healthy_minimum_score: Decimal = Field(
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=2,
    )
    caution_minimum_score: Decimal = Field(
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=2,
    )
    incomplete_mandatory_is_insufficient: Literal[True] = True

    @model_validator(mode="after")
    def validate_stance_policy(self) -> Self:
        if self.healthy_minimum_frameworks > self.minimum_scored_frameworks:
            raise ValueError(
                "healthy framework count cannot exceed required scored count"
            )
        if self.healthy_minimum_score <= self.caution_minimum_score:
            raise ValueError("healthy score threshold must exceed caution threshold")
        for value in (
            self.healthy_minimum_score,
            self.caution_minimum_score,
        ):
            if value != value.quantize(_SCORE_QUANTUM):
                raise ValueError("stance thresholds require at most two decimals")
        return self


class FundamentalScorePolicyBundle(FundamentalModel):
    """Complete versioned policy boundary for future score calculation."""

    schema_version: Literal["jarvis.fundamental_score_policy.v1"] = (
        "jarvis.fundamental_score_policy.v1"
    )
    policy_bundle_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    policy_bundle_version: str = Field(pattern=_SEMVER_PATTERN)
    decision_influence_mode: FundamentalDecisionInfluenceMode
    approval_reference: str | None = Field(
        default=None,
        min_length=1,
        max_length=240,
    )
    grade_thresholds: FundamentalScoreGradeThresholds
    stance_policy: FundamentalStancePolicy
    framework_policies: tuple[FundamentalFrameworkScoringPolicy, ...] = Field(
        min_length=len(FundamentalFramework),
        max_length=len(FundamentalFramework),
    )
    mandatory_constraints: tuple[FundamentalMandatoryScoreConstraint, ...] = (
        Field(min_length=1, max_length=64)
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("limitations")
    @classmethod
    def require_bounded_unique_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("score-policy limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("score-policy limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_policy_bundle(self) -> Self:
        frameworks = tuple(policy.framework for policy in self.framework_policies)
        if frameworks != tuple(FundamentalFramework):
            raise ValueError(
                "score policy requires every framework in canonical order"
            )
        policy_ids = tuple(policy.policy_id for policy in self.framework_policies)
        if len(policy_ids) != len(set(policy_ids)):
            raise ValueError("framework scoring policy IDs must be unique")

        constraint_ids = tuple(
            constraint.constraint_id for constraint in self.mandatory_constraints
        )
        if len(constraint_ids) != len(set(constraint_ids)):
            raise ValueError("mandatory score constraints must be unique")
        if constraint_ids != tuple(sorted(constraint_ids)):
            raise ValueError("mandatory score constraints must be canonical")

        if self.decision_influence_mode is FundamentalDecisionInfluenceMode.SHADOW:
            if self.approval_reference is not None:
                raise ValueError("shadow score policy cannot claim gate approval")
        elif self.approval_reference is None:
            raise ValueError(
                "advisory or gate score policy requires an approval reference"
            )
        return self

    @computed_field
    @property
    def policy_fingerprint(self) -> str:
        return _fingerprint(self, "policy_fingerprint")


def _validate_score(value: Decimal, label: str) -> None:
    if value != value.quantize(_SCORE_QUANTUM):
        raise ValueError(f"{label} requires at most two decimals")


def _validate_contribution(value: Decimal, label: str) -> None:
    if value != value.quantize(_CONTRIBUTION_QUANTUM):
        raise ValueError(f"{label} requires at most four decimals")


def _weighted_score(score: Decimal, weight_bps: int) -> Decimal:
    return (
        score * Decimal(weight_bps) / Decimal(_TOTAL_WEIGHT_BPS)
    ).quantize(_CONTRIBUTION_QUANTUM, rounding=ROUND_HALF_UP)


def _validate_metric_references(
    metric_keys: tuple[str, ...],
    citations: tuple[FundamentalMetricCitation, ...],
) -> None:
    if any(not key or len(key) > 320 for key in metric_keys):
        raise ValueError("score-result metric keys must be bounded")
    if len(metric_keys) != len(set(metric_keys)):
        raise ValueError("score-result metric keys must be unique")
    if metric_keys != tuple(sorted(metric_keys)):
        raise ValueError("score-result metric keys must use canonical order")
    citation_keys = tuple(citation.citation_key for citation in citations)
    if len(citation_keys) != len(set(citation_keys)):
        raise ValueError("score-result citations must be unique")
    if citation_keys != tuple(sorted(citation_keys)):
        raise ValueError("score-result citations must use canonical order")


class FundamentalMetricScoreResult(FundamentalModel):
    """One cited metric-rule outcome with validated weighted arithmetic."""

    result_id: str = Field(min_length=1, max_length=480)
    framework: FundamentalFramework
    family: FundamentalFrameworkEvidenceFamily
    rule_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_RULE_ID_PATTERN,
    )
    rule_version: str = Field(pattern=_SEMVER_PATTERN)
    rule_policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    status: FundamentalScoreResultStatus
    withholding_reason: FundamentalScoreWithholdingReason | None = None
    metric_keys: tuple[str, ...] = Field(default=(), max_length=32)
    citations: tuple[FundamentalMetricCitation, ...] = Field(
        default=(),
        max_length=128,
    )
    observed_value: Decimal | None = None
    band_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=_RULE_ID_PATTERN,
    )
    grade: FundamentalScoreGrade | None = None
    score: Decimal | None = Field(
        default=None,
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=2,
    )
    component_weight_bps: int = Field(ge=1, le=_TOTAL_WEIGHT_BPS)
    weighted_score: Decimal | None = Field(
        default=None,
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=4,
    )
    limitation: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_metric_result(self) -> Self:
        expected_id = (
            "fundamental.metric_score:"
            f"{self.framework.value}:{self.family.value}:{self.rule_id}"
        )
        if self.result_id != expected_id:
            raise ValueError("metric score result ID is not stable")
        _validate_metric_references(self.metric_keys, self.citations)

        calculated_fields = (
            self.observed_value,
            self.band_id,
            self.grade,
            self.score,
            self.weighted_score,
        )
        if self.status is FundamentalScoreResultStatus.CALCULATED:
            if any(value is None for value in calculated_fields):
                raise ValueError("calculated metric result is incomplete")
            if self.withholding_reason is not None:
                raise ValueError("calculated metric result cannot be withheld")
            if not self.metric_keys or not self.citations:
                raise ValueError("calculated metric result requires cited evidence")
            assert self.score is not None
            assert self.weighted_score is not None
            _validate_score(self.score, "metric score")
            _validate_contribution(self.weighted_score, "metric weighted score")
            if self.weighted_score != _weighted_score(
                self.score,
                self.component_weight_bps,
            ):
                raise ValueError("metric weighted score does not match policy")
        else:
            if any(value is not None for value in calculated_fields):
                raise ValueError("unreleased metric result cannot carry a score")
            if self.withholding_reason is None or self.limitation is None:
                raise ValueError(
                    "unreleased metric result requires reason and limitation"
                )
            is_conflict = (
                self.withholding_reason
                is FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE
            )
            if is_conflict and not (self.metric_keys and self.citations):
                raise ValueError(
                    "conflicting withheld metric requires cited evidence"
                )
            if not is_conflict and (self.metric_keys or self.citations):
                raise ValueError(
                    "only conflicting withheld metrics may retain citations"
                )
            ineligible = (
                self.withholding_reason
                is FundamentalScoreWithholdingReason.INELIGIBLE_FRAMEWORK
            )
            if ineligible != (
                self.status is FundamentalScoreResultStatus.NOT_APPLICABLE
            ):
                raise ValueError(
                    "ineligible metric reason requires not-applicable status"
                )
        return self

    @computed_field
    @property
    def result_fingerprint(self) -> str:
        return _fingerprint(self, "result_fingerprint")


class FundamentalEvidenceFamilyScoreResult(FundamentalModel):
    """Capped evidence-family result composed from metric-rule outcomes."""

    result_id: str = Field(min_length=1, max_length=320)
    framework: FundamentalFramework
    family: FundamentalFrameworkEvidenceFamily
    policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    status: FundamentalScoreResultStatus
    withholding_reason: FundamentalScoreWithholdingReason | None = None
    framework_weight_bps: int = Field(ge=1, le=_TOTAL_WEIGHT_BPS)
    minimum_rule_coverage_bps: int = Field(ge=1, le=_TOTAL_WEIGHT_BPS)
    maximum_family_score: Decimal = Field(
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=2,
    )
    metric_results: tuple[FundamentalMetricScoreResult, ...] = Field(
        min_length=1,
        max_length=64,
    )
    score: Decimal | None = Field(
        default=None,
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=2,
    )
    weighted_score: Decimal | None = Field(
        default=None,
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=4,
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("limitations")
    @classmethod
    def require_bounded_unique_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("family score limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("family score limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_family_result(self) -> Self:
        expected_id = (
            "fundamental.family_score:"
            f"{self.framework.value}:{self.family.value}"
        )
        if self.result_id != expected_id:
            raise ValueError("family score result ID is not stable")
        if self.maximum_family_score != self.maximum_family_score.quantize(
            _SCORE_QUANTUM
        ):
            raise ValueError("maximum family score requires at most two decimals")
        if any(
            metric.framework is not self.framework
            or metric.family is not self.family
            for metric in self.metric_results
        ):
            raise ValueError("family result cannot mix metric ownership")
        rule_ids = tuple(metric.rule_id for metric in self.metric_results)
        if len(rule_ids) != len(set(rule_ids)):
            raise ValueError("family result metric rules must be unique")
        if rule_ids != tuple(sorted(rule_ids)):
            raise ValueError("family result metric rules must be canonical")
        if sum(
            metric.component_weight_bps for metric in self.metric_results
        ) != _TOTAL_WEIGHT_BPS:
            raise ValueError("family result metric weights must total 10000 bps")

        calculated = tuple(
            metric
            for metric in self.metric_results
            if metric.status is FundamentalScoreResultStatus.CALCULATED
        )
        coverage_bps = sum(metric.component_weight_bps for metric in calculated)
        if self.status is FundamentalScoreResultStatus.CALCULATED:
            if self.score is None or self.weighted_score is None:
                raise ValueError("calculated family result requires scores")
            if self.withholding_reason is not None:
                raise ValueError("calculated family result cannot be withheld")
            if coverage_bps < self.minimum_rule_coverage_bps:
                raise ValueError("calculated family result has insufficient coverage")
            _validate_score(self.score, "family score")
            _validate_contribution(self.weighted_score, "family weighted score")
            raw_score = sum(
                metric.weighted_score or Decimal("0") for metric in calculated
            )
            expected_score = min(
                raw_score,
                self.maximum_family_score,
            ).quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_UP)
            if self.score != expected_score:
                raise ValueError("family score does not match metric contributions")
            if self.weighted_score != _weighted_score(
                self.score,
                self.framework_weight_bps,
            ):
                raise ValueError("family weighted score does not match policy")
        else:
            if self.score is not None or self.weighted_score is not None:
                raise ValueError("unreleased family result cannot carry scores")
            if self.withholding_reason is None or not self.limitations:
                raise ValueError(
                    "unreleased family result requires reason and limitation"
                )
            if self.status is FundamentalScoreResultStatus.NOT_APPLICABLE:
                if self.withholding_reason is not (
                    FundamentalScoreWithholdingReason.INELIGIBLE_FRAMEWORK
                ):
                    raise ValueError(
                        "not-applicable family requires ineligible framework reason"
                    )
                if any(
                    metric.status is not FundamentalScoreResultStatus.NOT_APPLICABLE
                    for metric in self.metric_results
                ):
                    raise ValueError(
                        "not-applicable family cannot contain released metrics"
                    )
            elif self.withholding_reason is (
                FundamentalScoreWithholdingReason.INELIGIBLE_FRAMEWORK
            ):
                raise ValueError(
                    "ineligible family reason requires not-applicable status"
                )
        return self

    @computed_field
    @property
    def rule_coverage_bps(self) -> int:
        return sum(
            metric.component_weight_bps
            for metric in self.metric_results
            if metric.status is FundamentalScoreResultStatus.CALCULATED
        )

    @computed_field
    @property
    def result_fingerprint(self) -> str:
        return _fingerprint(self, "result_fingerprint")


class FundamentalFrameworkScoreResult(FundamentalModel):
    """One framework score bound to its applicability and score policy."""

    result_id: str = Field(min_length=1, max_length=240)
    framework: FundamentalFramework
    status: FundamentalScoreResultStatus
    withholding_reason: FundamentalScoreWithholdingReason | None = None
    applicability_status: FundamentalFrameworkApplicabilityStatus
    applicability_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    scoring_policy_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    scoring_policy_version: str = Field(pattern=_SEMVER_PATTERN)
    scoring_policy_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    minimum_family_coverage_bps: int = Field(
        ge=1,
        le=_TOTAL_WEIGHT_BPS,
    )
    selected_primary: bool
    family_results: tuple[FundamentalEvidenceFamilyScoreResult, ...] = Field(
        min_length=1,
        max_length=len(FundamentalFrameworkEvidenceFamily),
    )
    score: Decimal | None = Field(
        default=None,
        ge=_MINIMUM_SCORE,
        le=_MAXIMUM_SCORE,
        decimal_places=2,
    )
    grade: FundamentalScoreGrade | None = None
    limitations: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("limitations")
    @classmethod
    def require_bounded_unique_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("framework score limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("framework score limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_framework_result(self) -> Self:
        expected_id = f"fundamental.framework_score:{self.framework.value}"
        if self.result_id != expected_id:
            raise ValueError("framework score result ID is not stable")
        expected_policy_id = f"jarvis.framework_score.{self.framework.value}"
        if self.scoring_policy_id != expected_policy_id:
            raise ValueError("framework result scoring policy ID is not stable")
        if any(
            family.framework is not self.framework
            for family in self.family_results
        ):
            raise ValueError("framework result cannot mix family ownership")
        families = tuple(family.family for family in self.family_results)
        if len(families) != len(set(families)):
            raise ValueError("framework result families must be unique")
        if families != _canonical_enum_values(
            families,
            FundamentalFrameworkEvidenceFamily,
        ):
            raise ValueError("framework result families must use canonical order")
        if sum(
            family.framework_weight_bps for family in self.family_results
        ) != _TOTAL_WEIGHT_BPS:
            raise ValueError("framework result family weights must total 10000 bps")

        calculated = tuple(
            family
            for family in self.family_results
            if family.status is FundamentalScoreResultStatus.CALCULATED
        )
        coverage_bps = sum(family.framework_weight_bps for family in calculated)
        if self.selected_primary and (
            self.applicability_status
            is not FundamentalFrameworkApplicabilityStatus.ELIGIBLE
        ):
            raise ValueError("only eligible frameworks may be selected")

        if self.status is FundamentalScoreResultStatus.CALCULATED:
            if (
                self.applicability_status
                is not FundamentalFrameworkApplicabilityStatus.ELIGIBLE
            ):
                raise ValueError("only eligible frameworks may release a score")
            if self.score is None or self.grade is None:
                raise ValueError("calculated framework result requires score and grade")
            if self.withholding_reason is not None:
                raise ValueError("calculated framework result cannot be withheld")
            if coverage_bps < self.minimum_family_coverage_bps:
                raise ValueError(
                    "calculated framework result has insufficient coverage"
                )
            _validate_score(self.score, "framework score")
            expected_score = sum(
                family.weighted_score or Decimal("0") for family in calculated
            ).quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_UP)
            if self.score != expected_score:
                raise ValueError(
                    "framework score does not match family contributions"
                )
        else:
            if self.score is not None or self.grade is not None:
                raise ValueError("unreleased framework result cannot carry a score")
            if self.withholding_reason is None or not self.limitations:
                raise ValueError(
                    "unreleased framework result requires reason and limitation"
                )
            if self.status is FundamentalScoreResultStatus.NOT_APPLICABLE:
                if (
                    self.applicability_status
                    is not FundamentalFrameworkApplicabilityStatus.INELIGIBLE
                    or self.withholding_reason
                    is not FundamentalScoreWithholdingReason.INELIGIBLE_FRAMEWORK
                ):
                    raise ValueError(
                        "not-applicable score requires ineligible applicability"
                    )
                if any(
                    family.status is not FundamentalScoreResultStatus.NOT_APPLICABLE
                    for family in self.family_results
                ):
                    raise ValueError(
                        "not-applicable framework cannot contain released families"
                    )
            elif self.withholding_reason is (
                FundamentalScoreWithholdingReason.INELIGIBLE_FRAMEWORK
            ):
                raise ValueError(
                    "ineligible framework reason requires not-applicable status"
                )
        return self

    @computed_field
    @property
    def family_coverage_bps(self) -> int:
        return sum(
            family.framework_weight_bps
            for family in self.family_results
            if family.status is FundamentalScoreResultStatus.CALCULATED
        )

    @computed_field
    @property
    def result_fingerprint(self) -> str:
        return _fingerprint(self, "result_fingerprint")


class FundamentalScorecardResult(FundamentalModel):
    """Complete scorecard bound to one active package and released policies."""

    schema_version: Literal["jarvis.fundamental_scorecard.v1"] = (
        "jarvis.fundamental_scorecard.v1"
    )
    package_id: str = Field(min_length=1, max_length=240)
    manifest_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    normalized_evidence_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    mandatory_assessment_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    applicability_assessment_fingerprint: str = Field(
        pattern=_FINGERPRINT_PATTERN
    )
    score_policy_bundle_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    score_policy_bundle_version: str = Field(pattern=_SEMVER_PATTERN)
    score_policy_bundle_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    issuer: FundamentalIssuerIdentity
    evidence_normalized_at: datetime
    mandatory_assessed_at: datetime
    applicability_assessed_at: datetime
    scored_at: datetime
    package_expires_at: datetime
    decision_influence_mode: FundamentalDecisionInfluenceMode
    approval_reference: str | None = Field(
        default=None,
        min_length=1,
        max_length=240,
    )
    framework_results: tuple[FundamentalFrameworkScoreResult, ...] = Field(
        min_length=len(FundamentalFramework),
        max_length=len(FundamentalFramework),
    )
    selected_primary_frameworks: tuple[FundamentalFramework, ...] = Field(
        default=(),
        max_length=3,
    )
    stance: FundamentalScorecardStance
    review_required: bool
    limitations: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator(
        "evidence_normalized_at",
        "mandatory_assessed_at",
        "applicability_assessed_at",
        "scored_at",
        "package_expires_at",
    )
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fundamental scorecard timestamps require timezone")
        return value

    @field_validator("limitations")
    @classmethod
    def require_bounded_unique_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("fundamental scorecard limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("fundamental scorecard limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_scorecard(self) -> Self:
        if self.package_id != f"fundamental_package:{self.manifest_fingerprint}":
            raise ValueError("scorecard package ID must match its manifest")
        if not (
            self.evidence_normalized_at
            <= self.mandatory_assessed_at
            <= self.applicability_assessed_at
            <= self.scored_at
            < self.package_expires_at
        ):
            raise ValueError(
                "scorecard must follow applicability within an active package"
            )
        frameworks = tuple(result.framework for result in self.framework_results)
        if frameworks != tuple(FundamentalFramework):
            raise ValueError(
                "scorecard requires every framework in canonical order"
            )
        selected = self.selected_primary_frameworks
        if len(selected) != len(set(selected)):
            raise ValueError("selected scorecard frameworks must be unique")
        selected_from_results = tuple(
            result.framework
            for result in self.framework_results
            if result.selected_primary
        )
        if selected != selected_from_results:
            raise ValueError(
                "selected frameworks must match framework-result selection"
            )
        if self.stance is FundamentalScorecardStance.HEALTHY and (
            self.review_required
        ):
            raise ValueError("healthy scorecard cannot require review")
        selected_scores = tuple(
            result
            for result in self.framework_results
            if result.selected_primary
            and result.status is FundamentalScoreResultStatus.CALCULATED
        )
        if not selected_scores and self.stance is not (
            FundamentalScorecardStance.INSUFFICIENT_EVIDENCE
        ):
            raise ValueError(
                "scorecard without selected scores requires insufficient stance"
            )
        if self.decision_influence_mode is FundamentalDecisionInfluenceMode.SHADOW:
            if self.approval_reference is not None:
                raise ValueError("shadow scorecard cannot claim gate approval")
        elif self.approval_reference is None:
            raise ValueError(
                "advisory or gate scorecard requires approval reference"
            )
        return self

    @computed_field
    @property
    def scorecard_fingerprint(self) -> str:
        return _fingerprint(self, "scorecard_fingerprint")
