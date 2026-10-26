"""Immutable contracts for deterministic framework applicability.

These models describe whether the normalized evidence can support an
investment-framework lens. They do not select frameworks, calculate scores,
or represent the judgment, voice, or endorsement of any named investor.
"""

from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.fundamental_metrics import FundamentalMetricCitation
from app.models.fundamentals import FundamentalIssuerIdentity, FundamentalModel


_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_POLICY_ID_PATTERN = r"^[a-z][a-z0-9_.-]*$"
_REASON_ID_PATTERN = r"^[a-z][a-z0-9_.-]*$"
_SEMVER_PATTERN = r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$"
_PERCENT_QUANTUM = Decimal("0.01")


class FundamentalFramework(StrEnum):
    """Approved quantitative framework lenses in canonical order."""

    BUFFETT = "buffett"
    LYNCH = "lynch"
    JHUNJHUNWALA_STYLE = "jhunjhunwala_style"
    ONE_HUNDRED_BAGGER = "one_hundred_bagger"
    CAN_SLIM = "can_slim"
    VALUATION = "valuation"


class FundamentalFrameworkApplicabilityStatus(StrEnum):
    """Whether current evidence can safely support one framework."""

    ELIGIBLE = "eligible"
    PARTIAL = "partial"
    INELIGIBLE = "ineligible"
    UNAVAILABLE = "unavailable"


class FundamentalFrameworkEvidenceFamily(StrEnum):
    """Independent evidence families used by applicability policies."""

    PROFITABILITY = "profitability"
    GROWTH = "growth"
    CAPITAL_EFFICIENCY = "capital_efficiency"
    BALANCE_SHEET = "balance_sheet"
    CASH_CONVERSION = "cash_conversion"
    REINVESTMENT = "reinvestment"
    DILUTION = "dilution"
    VALUATION = "valuation"
    INSTITUTIONAL_SPONSORSHIP = "institutional_sponsorship"
    RELATIVE_LEADERSHIP = "relative_leadership"
    MARKET_REGIME = "market_regime"
    CATALYST = "catalyst"


class FundamentalFrameworkEvidenceStatus(StrEnum):
    """Availability state of one required evidence family."""

    AVAILABLE = "available"
    MISSING = "missing"
    CONFLICTING = "conflicting"
    UNSUPPORTED = "unsupported"


class FundamentalFrameworkReasonKind(StrEnum):
    """Deterministic reason category for one applicability result."""

    ELIGIBILITY = "eligibility"
    EVIDENCE_GAP = "evidence_gap"
    EXCLUSION = "exclusion"
    LIMITATION = "limitation"


class FundamentalFrameworkLimitationKind(StrEnum):
    """Typed boundary that may limit or exclude a framework."""

    SECTOR = "sector"
    LIFECYCLE = "lifecycle"
    EVIDENCE_BOUNDARY = "evidence_boundary"
    POLICY = "policy"


def _canonical_enum_values(values: tuple[StrEnum, ...], enum_type: type[StrEnum]):
    return tuple(member for member in enum_type if member in values)


class FundamentalFrameworkEvidenceCoverage(FundamentalModel):
    """Cited availability record for one required evidence family."""

    family: FundamentalFrameworkEvidenceFamily
    status: FundamentalFrameworkEvidenceStatus
    metric_keys: tuple[str, ...] = Field(default=(), max_length=128)
    citations: tuple[FundamentalMetricCitation, ...] = Field(
        default=(),
        max_length=256,
    )
    limitation: str | None = Field(default=None, min_length=1, max_length=500)

    @field_validator("metric_keys")
    @classmethod
    def require_canonical_metric_keys(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 320 for value in values):
            raise ValueError("framework metric keys must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("framework metric keys must be unique")
        if values != tuple(sorted(values)):
            raise ValueError("framework metric keys must use canonical order")
        return values

    @model_validator(mode="after")
    def validate_coverage(self) -> Self:
        citation_keys = tuple(item.citation_key for item in self.citations)
        if len(citation_keys) != len(set(citation_keys)):
            raise ValueError("framework evidence citations must be unique")
        if citation_keys != tuple(sorted(citation_keys)):
            raise ValueError(
                "framework evidence citations must use canonical order"
            )
        if self.status is FundamentalFrameworkEvidenceStatus.AVAILABLE:
            if not self.metric_keys or not self.citations:
                raise ValueError(
                    "available framework evidence requires metrics and citations"
                )
        elif self.status is FundamentalFrameworkEvidenceStatus.CONFLICTING:
            if not self.metric_keys or not self.citations or self.limitation is None:
                raise ValueError(
                    "conflicting framework evidence requires cited metrics and "
                    "a limitation"
                )
        elif self.metric_keys or self.citations:
            raise ValueError(
                "missing or unsupported framework evidence cannot cite metrics"
            )
        elif self.limitation is None:
            raise ValueError(
                "unavailable framework evidence requires a limitation"
            )
        return self


class FundamentalFrameworkApplicabilityReason(FundamentalModel):
    """Stable, machine-readable reason for an applicability result."""

    reason_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_REASON_ID_PATTERN,
    )
    kind: FundamentalFrameworkReasonKind
    summary: str = Field(min_length=1, max_length=500)
    evidence_families: tuple[FundamentalFrameworkEvidenceFamily, ...] = Field(
        default=(),
        max_length=len(FundamentalFrameworkEvidenceFamily),
    )

    @field_validator("evidence_families")
    @classmethod
    def require_canonical_families(
        cls,
        values: tuple[FundamentalFrameworkEvidenceFamily, ...],
    ) -> tuple[FundamentalFrameworkEvidenceFamily, ...]:
        if len(values) != len(set(values)):
            raise ValueError("reason evidence families must be unique")
        if values != _canonical_enum_values(
            values,
            FundamentalFrameworkEvidenceFamily,
        ):
            raise ValueError("reason evidence families must use canonical order")
        return values


class FundamentalFrameworkApplicabilityLimitation(FundamentalModel):
    """Sector, lifecycle, evidence, or policy boundary for one framework."""

    limitation_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_REASON_ID_PATTERN,
    )
    kind: FundamentalFrameworkLimitationKind
    summary: str = Field(min_length=1, max_length=500)
    excludes_framework: bool = False


class FundamentalFrameworkApplicability(FundamentalModel):
    """Complete deterministic applicability record for one framework."""

    result_id: str = Field(min_length=1, max_length=240)
    framework: FundamentalFramework
    status: FundamentalFrameworkApplicabilityStatus
    applicability_policy_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    applicability_policy_version: str = Field(pattern=_SEMVER_PATTERN)
    deterministic_priority: int = Field(ge=1, le=len(FundamentalFramework))
    evidence_coverage: tuple[FundamentalFrameworkEvidenceCoverage, ...] = Field(
        min_length=1,
        max_length=len(FundamentalFrameworkEvidenceFamily),
    )
    reasons: tuple[FundamentalFrameworkApplicabilityReason, ...] = Field(
        min_length=1,
        max_length=64,
    )
    limitations: tuple[FundamentalFrameworkApplicabilityLimitation, ...] = Field(
        default=(),
        max_length=32,
    )

    @model_validator(mode="after")
    def validate_applicability(self) -> Self:
        expected_id = f"fundamental.framework_applicability:{self.framework.value}"
        if self.result_id != expected_id:
            raise ValueError("framework applicability result ID is not stable")

        families = tuple(item.family for item in self.evidence_coverage)
        if len(families) != len(set(families)):
            raise ValueError("framework evidence families must be unique")
        if families != _canonical_enum_values(
            families,
            FundamentalFrameworkEvidenceFamily,
        ):
            raise ValueError("framework evidence families must use canonical order")

        reason_ids = tuple(item.reason_id for item in self.reasons)
        if len(reason_ids) != len(set(reason_ids)):
            raise ValueError("framework applicability reasons must be unique")
        if reason_ids != tuple(sorted(reason_ids)):
            raise ValueError("framework applicability reasons must be canonical")
        for reason in self.reasons:
            if not set(reason.evidence_families).issubset(families):
                raise ValueError(
                    "applicability reason references an unconfigured family"
                )

        limitation_ids = tuple(item.limitation_id for item in self.limitations)
        if len(limitation_ids) != len(set(limitation_ids)):
            raise ValueError("framework applicability limitations must be unique")
        if limitation_ids != tuple(sorted(limitation_ids)):
            raise ValueError(
                "framework applicability limitations must be canonical"
            )

        available_count = len(self.available_evidence_families)
        family_count = len(self.required_evidence_families)
        has_exclusion = any(
            item.excludes_framework for item in self.limitations
        )
        reason_kinds = {item.kind for item in self.reasons}
        if self.status is FundamentalFrameworkApplicabilityStatus.ELIGIBLE:
            if available_count != family_count or has_exclusion:
                raise ValueError(
                    "eligible framework requires complete evidence and no exclusion"
                )
            if FundamentalFrameworkReasonKind.ELIGIBILITY not in reason_kinds:
                raise ValueError("eligible framework requires an eligibility reason")
        elif self.status is FundamentalFrameworkApplicabilityStatus.PARTIAL:
            if not 0 < available_count < family_count or has_exclusion:
                raise ValueError(
                    "partial framework requires incomplete non-zero evidence"
                )
            if FundamentalFrameworkReasonKind.EVIDENCE_GAP not in reason_kinds:
                raise ValueError("partial framework requires an evidence-gap reason")
        elif self.status is FundamentalFrameworkApplicabilityStatus.UNAVAILABLE:
            if available_count != 0 or has_exclusion:
                raise ValueError(
                    "unavailable framework requires zero available evidence"
                )
            if FundamentalFrameworkReasonKind.EVIDENCE_GAP not in reason_kinds:
                raise ValueError(
                    "unavailable framework requires an evidence-gap reason"
                )
        else:
            if not has_exclusion:
                raise ValueError("ineligible framework requires an exclusion")
            if FundamentalFrameworkReasonKind.EXCLUSION not in reason_kinds:
                raise ValueError("ineligible framework requires an exclusion reason")
        return self

    @computed_field
    @property
    def required_evidence_families(
        self,
    ) -> tuple[FundamentalFrameworkEvidenceFamily, ...]:
        return tuple(item.family for item in self.evidence_coverage)

    @computed_field
    @property
    def available_evidence_families(
        self,
    ) -> tuple[FundamentalFrameworkEvidenceFamily, ...]:
        return tuple(
            item.family
            for item in self.evidence_coverage
            if item.status is FundamentalFrameworkEvidenceStatus.AVAILABLE
        )

    @computed_field
    @property
    def missing_evidence_families(
        self,
    ) -> tuple[FundamentalFrameworkEvidenceFamily, ...]:
        return tuple(
            item.family
            for item in self.evidence_coverage
            if item.status is not FundamentalFrameworkEvidenceStatus.AVAILABLE
        )

    @computed_field
    @property
    def evidence_coverage_percentage(self) -> Decimal:
        coverage = Decimal(len(self.available_evidence_families)) / Decimal(
            len(self.required_evidence_families)
        )
        return (coverage * Decimal("100")).quantize(
            _PERCENT_QUANTUM,
            rounding=ROUND_HALF_UP,
        )

    @computed_field
    @property
    def applicability_fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"applicability_fingerprint"},
        )
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


class FundamentalFrameworkApplicabilityAssessment(FundamentalModel):
    """All framework applicability results bound to one evidence package."""

    schema_version: Literal[
        "jarvis.fundamental_framework_applicability.v1"
    ] = "jarvis.fundamental_framework_applicability.v1"
    package_id: str = Field(min_length=1, max_length=240)
    manifest_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    normalized_evidence_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    mandatory_assessment_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    issuer: FundamentalIssuerIdentity
    evidence_normalized_at: datetime
    mandatory_assessed_at: datetime
    assessed_at: datetime
    package_expires_at: datetime
    applicability_policy_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    applicability_policy_version: str = Field(pattern=_SEMVER_PATTERN)
    results: tuple[FundamentalFrameworkApplicability, ...] = Field(
        min_length=len(FundamentalFramework),
        max_length=len(FundamentalFramework),
    )
    selected_primary_frameworks: tuple[FundamentalFramework, ...] = Field(
        default=(),
        max_length=3,
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator(
        "evidence_normalized_at",
        "mandatory_assessed_at",
        "assessed_at",
        "package_expires_at",
    )
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("framework applicability timestamps require timezone")
        return value

    @field_validator("limitations")
    @classmethod
    def require_bounded_unique_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("applicability limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("applicability limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        if self.package_id != f"fundamental_package:{self.manifest_fingerprint}":
            raise ValueError("applicability package ID must match its manifest")
        if not (
            self.evidence_normalized_at
            <= self.mandatory_assessed_at
            <= self.assessed_at
            < self.package_expires_at
        ):
            raise ValueError(
                "applicability must follow mandatory assessment within an "
                "active package"
            )

        frameworks = tuple(item.framework for item in self.results)
        if frameworks != tuple(FundamentalFramework):
            raise ValueError(
                "applicability assessment requires every framework in "
                "canonical order"
            )
        priorities = tuple(item.deterministic_priority for item in self.results)
        if set(priorities) != set(range(1, len(FundamentalFramework) + 1)):
            raise ValueError("framework priorities must be unique ranks 1 through 6")
        if any(
            item.applicability_policy_id != self.applicability_policy_id
            or item.applicability_policy_version
            != self.applicability_policy_version
            for item in self.results
        ):
            raise ValueError("framework results must share the aggregate policy")

        selected = self.selected_primary_frameworks
        if len(selected) != len(set(selected)):
            raise ValueError("selected primary frameworks must be unique")
        by_framework = {item.framework: item for item in self.results}
        if any(
            by_framework[framework].status
            is not FundamentalFrameworkApplicabilityStatus.ELIGIBLE
            for framework in selected
        ):
            raise ValueError("only eligible frameworks may be selected")
        expected_selected_order = tuple(
            sorted(
                selected,
                key=lambda framework: by_framework[
                    framework
                ].deterministic_priority,
            )
        )
        if selected != expected_selected_order:
            raise ValueError(
                "selected frameworks must follow deterministic priority"
            )
        return self

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
