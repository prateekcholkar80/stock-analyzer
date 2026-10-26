"""Immutable contracts for deterministic mandatory fundamental assessments.

The models in this module carry released findings from deterministic checks.
They do not calculate financial-health, earnings-quality, or forensic rules,
and they deliberately contain no provider, database, or LLM dependency.
"""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_metrics import (
    FundamentalMetricCitation,
    FundamentalMetricUnit,
)
from app.models.fundamentals import FundamentalIssuerIdentity, FundamentalModel


_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_POLICY_ID_PATTERN = r"^[a-z][a-z0-9_.-]*$"
_SEMVER_PATTERN = r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$"


class FundamentalAssessmentDomain(StrEnum):
    """Mandatory, independently reported fundamental-check domains."""

    FINANCIAL_HEALTH = "financial_health"
    EARNINGS_QUALITY = "earnings_quality"
    FORENSIC_RISK = "forensic_risk"


class FundamentalFindingStatus(StrEnum):
    """Deterministic outcome of one configured rule."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    NA = "na"


class FundamentalFindingSeverity(StrEnum):
    """Potential consequence, separate from a rule's outcome status."""

    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


class FundamentalReviewState(StrEnum):
    """Whether a finding must be reviewed before downstream reliance."""

    NOT_REQUIRED = "not_required"
    REQUIRED = "required"


class FundamentalFindingNAReason(StrEnum):
    """Why a mandatory rule released an explicit ``NA`` result."""

    NOT_APPLICABLE = "not_applicable"
    MISSING_EVIDENCE = "missing_evidence"
    CONFLICTING_EVIDENCE = "conflicting_evidence"
    UNSUPPORTED_POLICY = "unsupported_policy"


class FundamentalAssessmentCoverageStatus(StrEnum):
    """Evaluation coverage after legitimate non-applicability is excluded."""

    COMPLETE = "complete"
    PARTIAL = "partial"
    INSUFFICIENT = "insufficient"
    NOT_APPLICABLE = "not_applicable"


class FundamentalAssessmentStance(StrEnum):
    """Bounded assessment posture, never an investment recommendation."""

    SUPPORTIVE = "supportive"
    MIXED = "mixed"
    ADVERSE = "adverse"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class FundamentalThresholdOperator(StrEnum):
    """Supported threshold semantics for deterministic rules."""

    LESS_THAN = "less_than"
    LESS_THAN_OR_EQUAL = "less_than_or_equal"
    GREATER_THAN = "greater_than"
    GREATER_THAN_OR_EQUAL = "greater_than_or_equal"
    EQUAL = "equal"
    BETWEEN_INCLUSIVE = "between_inclusive"
    OUTSIDE_INCLUSIVE = "outside_inclusive"
    BOOLEAN = "boolean"
    COMPOSITE = "composite"


class FundamentalAssessmentThreshold(FundamentalModel):
    """Versioned, unit-safe threshold or composite rule definition."""

    threshold_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    threshold_version: str = Field(pattern=_SEMVER_PATTERN)
    operator: FundamentalThresholdOperator
    lower_bound: Decimal | None = None
    upper_bound: Decimal | None = None
    unit: FundamentalMetricUnit | None = None
    description: str = Field(min_length=1, max_length=500)

    @field_validator("lower_bound", "upper_bound")
    @classmethod
    def require_finite_bound(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("assessment threshold bounds must be finite")
        return value

    @model_validator(mode="after")
    def validate_threshold(self) -> Self:
        single_bound = {
            FundamentalThresholdOperator.LESS_THAN,
            FundamentalThresholdOperator.LESS_THAN_OR_EQUAL,
            FundamentalThresholdOperator.GREATER_THAN,
            FundamentalThresholdOperator.GREATER_THAN_OR_EQUAL,
            FundamentalThresholdOperator.EQUAL,
        }
        ranged = {
            FundamentalThresholdOperator.BETWEEN_INCLUSIVE,
            FundamentalThresholdOperator.OUTSIDE_INCLUSIVE,
        }
        non_numeric = {
            FundamentalThresholdOperator.BOOLEAN,
            FundamentalThresholdOperator.COMPOSITE,
        }

        if self.operator in single_bound:
            if self.lower_bound is None or self.upper_bound is not None:
                raise ValueError(
                    "single-bound threshold requires only its lower bound"
                )
            if self.unit is None:
                raise ValueError("numeric threshold requires an explicit unit")
        elif self.operator in ranged:
            if self.lower_bound is None or self.upper_bound is None:
                raise ValueError("range threshold requires both bounds")
            if self.lower_bound > self.upper_bound:
                raise ValueError("threshold lower bound cannot exceed upper bound")
            if self.unit is None:
                raise ValueError("numeric threshold requires an explicit unit")
        elif self.operator in non_numeric and (
            self.lower_bound is not None
            or self.upper_bound is not None
            or self.unit is not None
        ):
            raise ValueError(
                "boolean and composite thresholds cannot carry numeric bounds"
            )
        return self


class FundamentalFindingObservation(FundamentalModel):
    """Exact normalized metrics and source paths observed by one rule."""

    metric_keys: tuple[str, ...] = Field(min_length=1, max_length=64)
    reporting_bases: tuple[FinancialReportingBasis, ...] = Field(
        min_length=1,
        max_length=len(FinancialReportingBasis),
    )
    value: Decimal | None = None
    unit: FundamentalMetricUnit | None = None
    citations: tuple[FundamentalMetricCitation, ...] = Field(
        min_length=1,
        max_length=128,
    )

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("assessment observation value must be finite")
        return value

    @field_validator("metric_keys")
    @classmethod
    def require_canonical_metric_keys(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 320 for value in values):
            raise ValueError("assessment metric keys must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("assessment metric keys must be unique")
        if values != tuple(sorted(values)):
            raise ValueError("assessment metric keys must use canonical order")
        return values

    @model_validator(mode="after")
    def validate_observation(self) -> Self:
        if (self.value is None) != (self.unit is None):
            raise ValueError(
                "numeric assessment observation requires value and unit together"
            )
        if len(self.reporting_bases) != len(set(self.reporting_bases)):
            raise ValueError("assessment reporting bases must be unique")
        expected_basis_order = tuple(
            basis
            for basis in FinancialReportingBasis
            if basis in self.reporting_bases
        )
        if self.reporting_bases != expected_basis_order:
            raise ValueError(
                "assessment reporting bases must use canonical order"
            )
        citation_keys = tuple(citation.citation_key for citation in self.citations)
        if len(citation_keys) != len(set(citation_keys)):
            raise ValueError("assessment citations must be unique")
        if citation_keys != tuple(sorted(citation_keys)):
            raise ValueError("assessment citations must use canonical order")
        return self

    @computed_field
    @property
    def observation_fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"observation_fingerprint"},
        )
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


class FundamentalAssessmentFinding(FundamentalModel):
    """One immutable mandatory-check result with rule and source lineage."""

    finding_id: str = Field(min_length=1, max_length=400)
    domain: FundamentalAssessmentDomain
    rule_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    rule_version: str = Field(pattern=_SEMVER_PATTERN)
    status: FundamentalFindingStatus
    severity: FundamentalFindingSeverity
    review_state: FundamentalReviewState
    na_reason: FundamentalFindingNAReason | None = None
    title: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1_000)
    threshold: FundamentalAssessmentThreshold
    observation: FundamentalFindingObservation | None = None
    limitations: tuple[str, ...] = Field(default=(), max_length=32)

    @field_validator("limitations")
    @classmethod
    def require_bounded_unique_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("assessment limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("assessment limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_finding(self) -> Self:
        expected_id = (
            f"fundamental.finding:{self.domain.value}:{self.rule_id}"
        )
        if self.finding_id != expected_id:
            raise ValueError("finding ID must be stable from domain and rule ID")

        allowed_severity = {
            FundamentalFindingStatus.PASS: {FundamentalFindingSeverity.NONE},
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
            FundamentalFindingStatus.NA: {FundamentalFindingSeverity.NONE},
        }
        if self.severity not in allowed_severity[self.status]:
            raise ValueError("finding status contradicts severity")

        if self.status is FundamentalFindingStatus.PASS:
            if self.review_state is not FundamentalReviewState.NOT_REQUIRED:
                raise ValueError("passing finding cannot require review")
        elif self.status in {
            FundamentalFindingStatus.WARN,
            FundamentalFindingStatus.FAIL,
        }:
            if self.review_state is not FundamentalReviewState.REQUIRED:
                raise ValueError("warning and failing findings require review")

        if self.status is FundamentalFindingStatus.NA:
            if self.na_reason is None:
                raise ValueError("NA finding requires an explicit reason")
            if not self.limitations:
                raise ValueError("NA finding requires a stated limitation")
            if (
                self.na_reason is FundamentalFindingNAReason.NOT_APPLICABLE
                and self.review_state is not FundamentalReviewState.NOT_REQUIRED
            ):
                raise ValueError(
                    "legitimately not-applicable finding cannot require review"
                )
            if (
                self.na_reason is not FundamentalFindingNAReason.NOT_APPLICABLE
                and self.review_state is not FundamentalReviewState.REQUIRED
            ):
                raise ValueError(
                    "unavailable mandatory evidence requires review"
                )
        elif self.na_reason is not None:
            raise ValueError("non-NA finding cannot carry an NA reason")

        if self.status is not FundamentalFindingStatus.NA and (
            self.observation is None
        ):
            raise ValueError("evaluated finding requires cited observations")
        return self

    @computed_field
    @property
    def finding_fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"finding_fingerprint"},
        )
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


class FundamentalDomainAssessment(FundamentalModel):
    """Complete configured rule surface for one mandatory domain."""

    schema_version: Literal["jarvis.fundamental_domain_assessment.v1"] = (
        "jarvis.fundamental_domain_assessment.v1"
    )
    domain: FundamentalAssessmentDomain
    policy_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    policy_version: str = Field(pattern=_SEMVER_PATTERN)
    expected_rule_ids: tuple[str, ...] = Field(min_length=1, max_length=128)
    findings: tuple[FundamentalAssessmentFinding, ...] = Field(
        min_length=1,
        max_length=128,
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("expected_rule_ids")
    @classmethod
    def require_canonical_rule_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(
            not value or len(value) > 160
            for value in values
        ):
            raise ValueError("assessment rule IDs must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("assessment rule IDs must be unique")
        if values != tuple(sorted(values)):
            raise ValueError("assessment rule IDs must use canonical order")
        return values

    @field_validator("limitations")
    @classmethod
    def require_bounded_unique_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("domain limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("domain limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_domain_assessment(self) -> Self:
        if any(finding.domain is not self.domain for finding in self.findings):
            raise ValueError("domain assessment cannot mix finding domains")
        finding_rule_ids = tuple(finding.rule_id for finding in self.findings)
        if finding_rule_ids != self.expected_rule_ids:
            raise ValueError(
                "domain findings must cover every expected rule in canonical order"
            )
        finding_ids = tuple(finding.finding_id for finding in self.findings)
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("domain finding IDs must be unique")
        return self

    @computed_field
    @property
    def configured_rule_count(self) -> int:
        return len(self.expected_rule_ids)

    @computed_field
    @property
    def evaluated_rule_count(self) -> int:
        return sum(
            finding.status is not FundamentalFindingStatus.NA
            for finding in self.findings
        )

    @computed_field
    @property
    def not_applicable_rule_count(self) -> int:
        return sum(
            finding.status is FundamentalFindingStatus.NA
            and finding.na_reason is FundamentalFindingNAReason.NOT_APPLICABLE
            for finding in self.findings
        )

    @computed_field
    @property
    def unavailable_rule_count(self) -> int:
        return sum(
            finding.status is FundamentalFindingStatus.NA
            and finding.na_reason is not FundamentalFindingNAReason.NOT_APPLICABLE
            for finding in self.findings
        )

    @computed_field
    @property
    def applicable_rule_count(self) -> int:
        return self.configured_rule_count - self.not_applicable_rule_count

    @computed_field
    @property
    def coverage_ratio(self) -> Decimal | None:
        if self.applicable_rule_count == 0:
            return None
        return Decimal(self.evaluated_rule_count) / Decimal(
            self.applicable_rule_count
        )

    @computed_field
    @property
    def coverage_status(self) -> FundamentalAssessmentCoverageStatus:
        if self.applicable_rule_count == 0:
            return FundamentalAssessmentCoverageStatus.NOT_APPLICABLE
        if self.unavailable_rule_count == 0:
            return FundamentalAssessmentCoverageStatus.COMPLETE
        if self.evaluated_rule_count == 0:
            return FundamentalAssessmentCoverageStatus.INSUFFICIENT
        return FundamentalAssessmentCoverageStatus.PARTIAL

    @computed_field
    @property
    def stance(self) -> FundamentalAssessmentStance:
        statuses = {finding.status for finding in self.findings}
        if FundamentalFindingStatus.FAIL in statuses:
            return FundamentalAssessmentStance.ADVERSE
        if self.unavailable_rule_count:
            return FundamentalAssessmentStance.INSUFFICIENT_EVIDENCE
        if FundamentalFindingStatus.WARN in statuses:
            return FundamentalAssessmentStance.MIXED
        if FundamentalFindingStatus.PASS in statuses:
            return FundamentalAssessmentStance.SUPPORTIVE
        return FundamentalAssessmentStance.INSUFFICIENT_EVIDENCE

    @computed_field
    @property
    def review_required(self) -> bool:
        return any(
            finding.review_state is FundamentalReviewState.REQUIRED
            for finding in self.findings
        )

    @computed_field
    @property
    def assessment_fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"assessment_fingerprint"},
        )
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


class FundamentalMandatoryAssessment(FundamentalModel):
    """All mandatory domains bound to one active normalized package."""

    schema_version: Literal["jarvis.fundamental_mandatory_assessment.v1"] = (
        "jarvis.fundamental_mandatory_assessment.v1"
    )
    package_id: str = Field(min_length=1, max_length=240)
    manifest_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    normalized_evidence_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    issuer: FundamentalIssuerIdentity
    evidence_normalized_at: datetime
    package_expires_at: datetime
    assessed_at: datetime
    policy_bundle_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    policy_bundle_version: str = Field(pattern=_SEMVER_PATTERN)
    domains: tuple[FundamentalDomainAssessment, ...] = Field(
        min_length=len(FundamentalAssessmentDomain),
        max_length=len(FundamentalAssessmentDomain),
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator(
        "evidence_normalized_at",
        "package_expires_at",
        "assessed_at",
    )
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("mandatory assessment timestamps require timezone")
        return value

    @field_validator("limitations")
    @classmethod
    def require_bounded_unique_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("mandatory assessment limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("mandatory assessment limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        if self.package_id != f"fundamental_package:{self.manifest_fingerprint}":
            raise ValueError("assessment package ID must match its manifest")
        if not (
            self.evidence_normalized_at
            <= self.assessed_at
            < self.package_expires_at
        ):
            raise ValueError(
                "assessment must use normalized evidence from an active package"
            )
        expected_domains = tuple(FundamentalAssessmentDomain)
        actual_domains = tuple(item.domain for item in self.domains)
        if actual_domains != expected_domains:
            raise ValueError(
                "mandatory assessment requires every domain in canonical order"
            )
        finding_ids = tuple(
            finding.finding_id
            for domain in self.domains
            for finding in domain.findings
        )
        if len(finding_ids) != len(set(finding_ids)):
            raise ValueError("mandatory assessment finding IDs must be unique")
        return self

    @computed_field
    @property
    def coverage_status(self) -> FundamentalAssessmentCoverageStatus:
        statuses = {domain.coverage_status for domain in self.domains}
        if FundamentalAssessmentCoverageStatus.INSUFFICIENT in statuses:
            return FundamentalAssessmentCoverageStatus.INSUFFICIENT
        if FundamentalAssessmentCoverageStatus.PARTIAL in statuses:
            return FundamentalAssessmentCoverageStatus.PARTIAL
        if statuses == {FundamentalAssessmentCoverageStatus.NOT_APPLICABLE}:
            return FundamentalAssessmentCoverageStatus.NOT_APPLICABLE
        return FundamentalAssessmentCoverageStatus.COMPLETE

    @computed_field
    @property
    def stance(self) -> FundamentalAssessmentStance:
        stances = {domain.stance for domain in self.domains}
        if FundamentalAssessmentStance.ADVERSE in stances:
            return FundamentalAssessmentStance.ADVERSE
        if FundamentalAssessmentStance.INSUFFICIENT_EVIDENCE in stances:
            return FundamentalAssessmentStance.INSUFFICIENT_EVIDENCE
        if FundamentalAssessmentStance.MIXED in stances:
            return FundamentalAssessmentStance.MIXED
        return FundamentalAssessmentStance.SUPPORTIVE

    @computed_field
    @property
    def review_required(self) -> bool:
        return any(domain.review_required for domain in self.domains)

    @computed_field
    @property
    def finding_count(self) -> int:
        return sum(len(domain.findings) for domain in self.domains)

    @computed_field
    @property
    def assessment_fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"assessment_fingerprint"},
        )
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()
