"""Provider-neutral, tenant-scoped fundamental evidence contracts.

These models are deliberately independent of Tijori, Angel One, an LLM, and
any database adapter. External payloads remain untrusted until they can be
represented by these contracts and pass the aggregate release checks.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import IntEnum, StrEnum
from hashlib import sha256
from re import fullmatch
from typing import Literal, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_SOURCE_ID_PATTERN = r"^SRC-[A-Z0-9][A-Z0-9_.:-]*$"
_EVIDENCE_ID_PATTERN = (
    r"^fundamental:[A-Za-z0-9][A-Za-z0-9_.:-]*$"
)
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_ISIN_PATTERN = r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"
_LINE_ITEM_ID_PATTERN = r"^[a-z][a-z0-9_.-]*$"


class FundamentalModel(BaseModel):
    """Secret-resistant base for all fundamental evidence contracts."""

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        extra="forbid",
        str_strip_whitespace=True,
        hide_input_in_errors=True,
    )


class ProviderSubscriptionTier(StrEnum):
    FREE = "free"
    PAID = "paid"
    ENTERPRISE = "enterprise"
    UNKNOWN = "unknown"


class ProviderEntitlementStatus(StrEnum):
    VERIFIED = "verified"
    UNVERIFIED = "unverified"
    EXPIRED = "expired"
    UNAVAILABLE = "unavailable"


class FundamentalSourceType(StrEnum):
    UPLOADED_FILE = "uploaded_file"
    USER_PROVIDED_EXPORT = "user_provided_export"
    CALLABLE_CONNECTED_SYSTEM = "callable_connected_system"
    EXCHANGE_FILING = "exchange_filing"
    COMPANY_FILING = "company_filing"
    ANNUAL_REPORT = "annual_report"
    EARNINGS_RELEASE = "earnings_release"
    INVESTOR_PRESENTATION = "investor_presentation"
    EARNINGS_TRANSCRIPT = "earnings_transcript"
    PROVIDER_STANDARDIZED = "provider_standardized"
    SECONDARY_AGGREGATOR = "secondary_aggregator"
    USER_PROMPT = "user_prompt"
    ASSUMPTION = "assumption"
    UNKNOWN = "unknown"


class FundamentalSourceRank(IntEnum):
    USER_GOVERNING_PACKAGE = 1
    CONNECTED_SYSTEM = 2
    PRIMARY_PUBLIC_DISCLOSURE = 3
    STANDARDIZED_PROVIDER = 4
    SECONDARY_SOURCE = 5
    USER_ASSUMPTION = 6
    ANALYST_INFERENCE_OR_UNKNOWN = 7


class FundamentalFreshnessStatus(StrEnum):
    CURRENT = "current"
    ACCEPTABLE_FOR_PERIOD = "acceptable_for_period"
    PRELIMINARY = "preliminary"
    STALE = "stale"
    UNKNOWN = "unknown"


class FundamentalValidationStatus(StrEnum):
    PENDING = "pending"
    VALIDATED = "validated"
    PARTIAL = "partial"
    REJECTED = "rejected"
    QUARANTINED = "quarantined"


class FundamentalAvailabilityStatus(StrEnum):
    AVAILABLE = "available"
    MISSING_REQUIRED_SOURCE = "missing_required_source"
    NOT_ENTITLED = "not_entitled"
    PAYWALLED = "paywalled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    MALFORMED = "malformed"
    UNKNOWN = "unknown"


class FundamentalConflictStatus(StrEnum):
    NONE = "none"
    RESOLVED = "resolved"
    UNRESOLVED = "unresolved"


class FundamentalConflictType(StrEnum):
    TIMING = "timing"
    DEFINITION = "definition"
    SCALE = "scale"
    CURRENCY = "currency"
    RESTATEMENT = "restatement"
    PRO_FORMA = "pro_forma"
    REPORTED_VS_ADJUSTED = "reported_vs_adjusted"
    PROVIDER_STANDARDIZATION = "provider_standardization"
    MAPPING = "mapping"
    UNKNOWN = "unknown"


class FundamentalEvidenceLabel(StrEnum):
    FACT_SOURCE_REPORTED = "fact_source_reported"
    FACT_PROVIDER_STANDARDIZED = "fact_provider_standardized"
    DERIVED_CALCULATION = "derived_calculation"
    ISSUER_MANAGEMENT_CLAIM = "issuer_management_claim"
    MANAGEMENT_ADJUSTED = "management_adjusted"
    ANALYST_ADJUSTED = "analyst_adjusted"
    ANALYST_INTERPRETATION = "analyst_interpretation"
    ASSUMPTION_USER_PROVIDED = "assumption_user_provided"
    ASSUMPTION_INFERRED = "assumption_inferred"
    ESTIMATE_CONSENSUS = "estimate_consensus"
    STALE_SOURCE = "stale_source"
    CONTRADICTED_SOURCE = "contradicted_source"
    MISSING_REQUIRED_SOURCE = "missing_required_source"
    UNKNOWN = "unknown"


class FundamentalEvidenceConfidence(StrEnum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class FundamentalStatement(StrEnum):
    COMPANY_PROFILE = "company_profile"
    VALUATION = "valuation"
    OWNERSHIP = "ownership"
    INCOME_STATEMENT = "income_statement"
    BALANCE_SHEET = "balance_sheet"
    CASH_FLOW = "cash_flow"
    KPI_SCHEDULE = "kpi_schedule"
    SEGMENT = "segment"
    EQUITY_RISK_DEBT_LIQUIDITY_CONTEXT = (
        "equity_risk_debt_liquidity_context"
    )
    SHARE_COUNT = "share_count"
    WORKING_CAPITAL = "working_capital"
    CAPITAL_ALLOCATION = "capital_allocation"
    CONSENSUS_ESTIMATE = "consensus_estimate"
    ADJUSTMENT = "adjustment"


class FundamentalPeriodType(StrEnum):
    ANNUAL = "annual"
    QUARTERLY = "quarterly"
    MONTHLY = "monthly"
    YTD = "ytd"
    LTM = "ltm"
    FORECAST = "forecast"
    BUDGET = "budget"
    PRO_FORMA = "pro_forma"
    SCENARIO = "scenario"


class FundamentalValueKind(StrEnum):
    MONETARY = "monetary"
    PER_SHARE = "per_share"
    PERCENTAGE = "percentage"
    BASIS_POINTS = "basis_points"
    RATIO = "ratio"
    COUNT = "count"
    OTHER = "other"


class FundamentalNormalizationMethod(StrEnum):
    AS_REPORTED = "as_reported"
    SCALED_OR_SIGN_NORMALIZED = "scaled_or_sign_normalized"
    CALCULATED = "calculated"
    MAPPED = "mapped"
    CURRENCY_CONVERTED = "currency_converted"


class FundamentalEvidencePosture(StrEnum):
    DECISION_GRADE = "decision_grade"
    RESEARCH_GRADE = "research_grade"
    PRELIMINARY = "preliminary"
    ASSUMPTION_LED = "assumption_led"
    NOT_SUPPORTABLE = "not_supportable"


_EXPECTED_SOURCE_RANK = {
    FundamentalSourceType.UPLOADED_FILE:
        FundamentalSourceRank.USER_GOVERNING_PACKAGE,
    FundamentalSourceType.USER_PROVIDED_EXPORT:
        FundamentalSourceRank.USER_GOVERNING_PACKAGE,
    FundamentalSourceType.CALLABLE_CONNECTED_SYSTEM:
        FundamentalSourceRank.CONNECTED_SYSTEM,
    FundamentalSourceType.EXCHANGE_FILING:
        FundamentalSourceRank.PRIMARY_PUBLIC_DISCLOSURE,
    FundamentalSourceType.COMPANY_FILING:
        FundamentalSourceRank.PRIMARY_PUBLIC_DISCLOSURE,
    FundamentalSourceType.ANNUAL_REPORT:
        FundamentalSourceRank.PRIMARY_PUBLIC_DISCLOSURE,
    FundamentalSourceType.EARNINGS_RELEASE:
        FundamentalSourceRank.PRIMARY_PUBLIC_DISCLOSURE,
    FundamentalSourceType.INVESTOR_PRESENTATION:
        FundamentalSourceRank.PRIMARY_PUBLIC_DISCLOSURE,
    FundamentalSourceType.EARNINGS_TRANSCRIPT:
        FundamentalSourceRank.PRIMARY_PUBLIC_DISCLOSURE,
    FundamentalSourceType.PROVIDER_STANDARDIZED:
        FundamentalSourceRank.STANDARDIZED_PROVIDER,
    FundamentalSourceType.SECONDARY_AGGREGATOR:
        FundamentalSourceRank.SECONDARY_SOURCE,
    FundamentalSourceType.USER_PROMPT:
        FundamentalSourceRank.USER_ASSUMPTION,
    FundamentalSourceType.ASSUMPTION:
        FundamentalSourceRank.USER_ASSUMPTION,
    FundamentalSourceType.UNKNOWN:
        FundamentalSourceRank.ANALYST_INFERENCE_OR_UNKNOWN,
}


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


def _normalize_unique_identifiers(
    values: tuple[str, ...],
    field_name: str,
) -> tuple[str, ...]:
    if any(not value for value in values):
        raise ValueError(f"{field_name} cannot contain blank values")
    folded = tuple(value.casefold() for value in values)
    if len(folded) != len(set(folded)):
        raise ValueError(f"{field_name} must be unique")
    return values


class ProviderConnectionScope(FundamentalModel):
    """One user's isolated, non-secret external-provider connection."""

    schema_version: Literal["jarvis.provider_connection_scope.v1"] = (
        "jarvis.provider_connection_scope.v1"
    )
    tenant_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    provider_connection_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    provider: str = Field(
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )
    account_reference_hash: str | None = Field(
        default=None,
        pattern=_FINGERPRINT_PATTERN,
    )
    subscription_tier: ProviderSubscriptionTier = (
        ProviderSubscriptionTier.UNKNOWN
    )
    entitlement_status: ProviderEntitlementStatus = (
        ProviderEntitlementStatus.UNVERIFIED
    )
    capabilities: tuple[str, ...] = ()
    entitlement_checked_at: datetime | None = None

    @field_validator("capabilities")
    @classmethod
    def require_unique_capabilities(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        for value in values:
            if (
                len(value) > 120
                or not value
                or fullmatch(_ID_PATTERN, value) is None
            ):
                raise ValueError("provider capabilities must be bounded")
        return _normalize_unique_identifiers(values, "provider capabilities")

    @field_validator("entitlement_checked_at")
    @classmethod
    def require_checked_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None:
            return _require_timezone(value, "entitlement check time")
        return value

    @model_validator(mode="after")
    def validate_entitlement(self) -> Self:
        if (
            self.entitlement_status is ProviderEntitlementStatus.VERIFIED
            and self.entitlement_checked_at is None
        ):
            raise ValueError(
                "verified provider entitlement requires its check time"
            )
        return self


class FundamentalIssuerIdentity(FundamentalModel):
    """Listed-company identity independent of any one data provider."""

    exchange: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=1, max_length=100)
    legal_name: str = Field(min_length=1, max_length=300)
    isin: str | None = Field(default=None, pattern=_ISIN_PATTERN)
    provider_company_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    provider_slug: str | None = Field(
        default=None,
        min_length=1,
        max_length=240,
    )

    @field_validator("exchange", "symbol", "isin", mode="before")
    @classmethod
    def normalize_market_identity(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None


class FundamentalEvidenceSource(FundamentalModel):
    """Citation-ready source inventory record with freshness metadata."""

    source_id: str = Field(
        min_length=5,
        max_length=128,
        pattern=_SOURCE_ID_PATTERN,
    )
    source_name: str = Field(min_length=1, max_length=300)
    source_type: FundamentalSourceType
    source_rank: FundamentalSourceRank
    owner_or_provider: str = Field(min_length=1, max_length=160)
    location: str = Field(min_length=1, max_length=2_000)
    period_covered: str = Field(min_length=1, max_length=200)
    as_of_date: date | None
    published_at: datetime | None = None
    retrieved_at: datetime
    freshness_status: FundamentalFreshnessStatus
    content_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    parser_name: str = Field(min_length=1, max_length=100)
    parser_version: str = Field(
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )
    provider_schema_version: str = Field(
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )
    validation_status: FundamentalValidationStatus
    notes: tuple[str, ...] = ()

    @field_validator("published_at", "retrieved_at")
    @classmethod
    def require_source_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None:
            return _require_timezone(value, "source timestamp")
        return value

    @field_validator("notes")
    @classmethod
    def normalize_notes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("source notes must be non-blank and bounded")
        return values

    @model_validator(mode="after")
    def validate_source(self) -> Self:
        if self.source_id == "SRC-UNSPECIFIED":
            raise ValueError(
                "SRC-UNSPECIFIED is a blocker placeholder, not a source"
            )
        if self.source_rank is not _EXPECTED_SOURCE_RANK[self.source_type]:
            raise ValueError("source rank does not match source type")
        if (
            self.published_at is not None
            and self.published_at > self.retrieved_at
        ):
            raise ValueError("source cannot be retrieved before publication")
        if (
            self.as_of_date is not None
            and self.as_of_date > self.retrieved_at.date()
        ):
            raise ValueError("source as-of date cannot follow retrieval date")
        if self.as_of_date is None and (
            self.freshness_status is not FundamentalFreshnessStatus.UNKNOWN
        ):
            raise ValueError(
                "source without an as-of date must have unknown freshness"
            )
        if (
            self.validation_status is FundamentalValidationStatus.VALIDATED
            and (
                self.as_of_date is None
                or self.freshness_status
                is FundamentalFreshnessStatus.UNKNOWN
                or self.source_type is FundamentalSourceType.UNKNOWN
            )
        ):
            raise ValueError(
                "validated source requires dated, classifiable evidence"
            )
        return self


class FundamentalReportingPeriod(FundamentalModel):
    """Explicit fiscal period; dates are never inferred from the label."""

    label: str = Field(min_length=1, max_length=80)
    period_type: FundamentalPeriodType
    start_date: date | None = None
    end_date: date | None = None

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("reporting period start cannot follow its end")
        return self


class FundamentalSourceReference(FundamentalModel):
    """Tamper-evident reference from one fact to one source payload."""

    source_id: str = Field(
        min_length=5,
        max_length=128,
        pattern=_SOURCE_ID_PATTERN,
    )
    content_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @model_validator(mode="after")
    def reject_unspecified_source(self) -> Self:
        if self.source_id == "SRC-UNSPECIFIED":
            raise ValueError("lineage cannot reference an unspecified source")
        return self


class FundamentalEvidenceLineage(FundamentalModel):
    """Source and calculation chain for one immutable evidence fact."""

    source_references: tuple[FundamentalSourceReference, ...] = ()
    parent_evidence_ids: tuple[str, ...] = ()
    transformation_id: str = Field(
        min_length=1,
        max_length=120,
        pattern=_ID_PATTERN,
    )
    transformation_version: str = Field(
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )
    formula: str | None = Field(default=None, min_length=1, max_length=1_000)

    @field_validator("source_references")
    @classmethod
    def require_unique_source_references(
        cls,
        values: tuple[FundamentalSourceReference, ...],
    ) -> tuple[FundamentalSourceReference, ...]:
        ids = tuple(value.source_id for value in values)
        if len(ids) != len(set(ids)):
            raise ValueError("lineage source references must be unique")
        return values

    @field_validator("parent_evidence_ids")
    @classmethod
    def require_unique_parents(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        for value in values:
            if (
                len(value) > 200
                or not value
                or fullmatch(_EVIDENCE_ID_PATTERN, value) is None
            ):
                raise ValueError("parent evidence IDs must be bounded")
        return _normalize_unique_identifiers(values, "parent evidence IDs")


class NormalizedFundamentalFact(FundamentalModel):
    """One normalized numeric fact or explicit unavailable-value marker."""

    evidence_id: str = Field(
        min_length=13,
        max_length=200,
        pattern=_EVIDENCE_ID_PATTERN,
    )
    statement: FundamentalStatement
    line_item_original: str = Field(min_length=1, max_length=300)
    line_item_standard: str = Field(min_length=1, max_length=300)
    line_item_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_LINE_ITEM_ID_PATTERN,
    )
    period: FundamentalReportingPeriod
    value_kind: FundamentalValueKind
    source_value: str | None = Field(default=None, min_length=1, max_length=500)
    normalized_value: Decimal | None = None
    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    source_unit: str | None = Field(default=None, min_length=1, max_length=40)
    normalized_unit: str | None = Field(
        default=None,
        min_length=1,
        max_length=40,
    )
    normalization_method: FundamentalNormalizationMethod
    source_location: str = Field(min_length=1, max_length=2_000)
    evidence_label: FundamentalEvidenceLabel
    confidence: FundamentalEvidenceConfidence
    availability_status: FundamentalAvailabilityStatus
    freshness_status: FundamentalFreshnessStatus
    validation_status: FundamentalValidationStatus
    conflict_status: FundamentalConflictStatus = (
        FundamentalConflictStatus.NONE
    )
    lineage: FundamentalEvidenceLineage
    normalization_note: str | None = Field(
        default=None,
        min_length=1,
        max_length=1_000,
    )

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @field_validator("normalized_value")
    @classmethod
    def require_finite_value(
        cls,
        value: Decimal | None,
    ) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("normalized fundamental value must be finite")
        return value

    @model_validator(mode="after")
    def validate_fact(self) -> Self:
        available = (
            self.availability_status
            is FundamentalAvailabilityStatus.AVAILABLE
        )
        if available:
            if (
                self.source_value is None
                or self.normalized_value is None
                or self.source_unit is None
                or self.normalized_unit is None
                or not self.lineage.source_references
            ):
                raise ValueError(
                    "available fact requires value, units, and source lineage"
                )
        elif self.normalized_value is not None:
            raise ValueError("unavailable fact cannot carry a normalized value")

        if self.value_kind in {
            FundamentalValueKind.MONETARY,
            FundamentalValueKind.PER_SHARE,
        } and available and self.currency is None:
            raise ValueError("monetary facts require a currency")

        if (
            self.validation_status is FundamentalValidationStatus.VALIDATED
            and not available
        ):
            raise ValueError("only available facts may be validated")

        is_derived = (
            self.evidence_label
            is FundamentalEvidenceLabel.DERIVED_CALCULATION
            or self.normalization_method
            is FundamentalNormalizationMethod.CALCULATED
        )
        if is_derived and (
            not self.lineage.parent_evidence_ids
            or self.lineage.formula is None
        ):
            raise ValueError(
                "derived calculations require parent evidence and a formula"
            )
        if (
            self.evidence_label
            is FundamentalEvidenceLabel.FACT_SOURCE_REPORTED
            and self.lineage.parent_evidence_ids
        ):
            raise ValueError("source-reported fact cannot have parent evidence")

        if (
            self.evidence_label
            is FundamentalEvidenceLabel.MISSING_REQUIRED_SOURCE
        ):
            if (
                self.availability_status
                is not FundamentalAvailabilityStatus.MISSING_REQUIRED_SOURCE
                or self.confidence is not FundamentalEvidenceConfidence.LOW
                or self.lineage.source_references
            ):
                raise ValueError(
                    "missing-source evidence must remain unavailable, low "
                    "confidence, and unsourced"
                )
        if (
            self.evidence_label is FundamentalEvidenceLabel.STALE_SOURCE
            and self.freshness_status
            is not FundamentalFreshnessStatus.STALE
        ):
            raise ValueError("stale-source label requires stale freshness")
        if (
            self.evidence_label
            is FundamentalEvidenceLabel.CONTRADICTED_SOURCE
            and self.conflict_status
            is not FundamentalConflictStatus.UNRESOLVED
        ):
            raise ValueError(
                "contradicted-source label requires unresolved conflict"
            )
        return self


class FundamentalConflictRecord(FundamentalModel):
    """Preserved disagreement between facts; values are never averaged away."""

    conflict_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    conflict_type: FundamentalConflictType
    evidence_ids: tuple[str, ...] = Field(min_length=2, max_length=20)
    status: FundamentalConflictStatus
    material: bool = True
    working_evidence_id: str | None = Field(
        default=None,
        min_length=13,
        max_length=200,
        pattern=_EVIDENCE_ID_PATTERN,
    )
    resolution_basis: str | None = Field(
        default=None,
        min_length=1,
        max_length=1_000,
    )

    @field_validator("evidence_ids")
    @classmethod
    def require_unique_evidence_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        return _normalize_unique_identifiers(values, "conflict evidence IDs")

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        if self.status is FundamentalConflictStatus.NONE:
            raise ValueError("a conflict record cannot have status none")
        if self.status is FundamentalConflictStatus.RESOLVED:
            if (
                self.working_evidence_id is None
                or self.resolution_basis is None
                or self.working_evidence_id not in self.evidence_ids
            ):
                raise ValueError(
                    "resolved conflict requires an explained working fact"
                )
        elif (
            self.working_evidence_id is not None
            or self.resolution_basis is not None
        ):
            raise ValueError(
                "unresolved conflict cannot select a working fact"
            )
        return self


class FundamentalEvidenceSnapshot(FundamentalModel):
    """Validated tenant-isolated evidence package released downstream."""

    schema_version: Literal["jarvis.fundamental_evidence.v1"] = (
        "jarvis.fundamental_evidence.v1"
    )
    snapshot_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_ID_PATTERN,
    )
    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity
    sources: tuple[FundamentalEvidenceSource, ...] = Field(min_length=1)
    facts: tuple[NormalizedFundamentalFact, ...] = Field(min_length=1)
    conflicts: tuple[FundamentalConflictRecord, ...] = ()
    evidence_posture: FundamentalEvidencePosture
    validation_status: FundamentalValidationStatus
    assembled_at: datetime
    limitations: tuple[str, ...] = ()

    @field_validator("assembled_at")
    @classmethod
    def require_assembled_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, "snapshot assembly time")

    @field_validator("limitations")
    @classmethod
    def normalize_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("snapshot limitations must be non-blank and bounded")
        return values

    @model_validator(mode="after")
    def validate_snapshot(self) -> Self:
        source_by_id = {source.source_id: source for source in self.sources}
        if len(source_by_id) != len(self.sources):
            raise ValueError("snapshot source IDs must be unique")

        fact_by_id = {fact.evidence_id: fact for fact in self.facts}
        if len(fact_by_id) != len(self.facts):
            raise ValueError("snapshot evidence IDs must be unique")

        if any(
            source.retrieved_at > self.assembled_at
            for source in self.sources
        ):
            raise ValueError("snapshot cannot predate source retrieval")

        for fact in self.facts:
            self._validate_fact_lineage(fact, source_by_id, fact_by_id)
            self._validate_source_label(fact, source_by_id)

        self._reject_lineage_cycles(fact_by_id)
        self._validate_conflicts(fact_by_id)
        self._validate_release_posture(source_by_id, fact_by_id)
        return self

    def _validate_fact_lineage(
        self,
        fact: NormalizedFundamentalFact,
        source_by_id: dict[str, FundamentalEvidenceSource],
        fact_by_id: dict[str, NormalizedFundamentalFact],
    ) -> None:
        for reference in fact.lineage.source_references:
            source = source_by_id.get(reference.source_id)
            if source is None:
                raise ValueError("fact lineage references an unknown source")
            if reference.content_fingerprint != source.content_fingerprint:
                raise ValueError("fact lineage source fingerprint is incorrect")
            if (
                fact.validation_status
                is FundamentalValidationStatus.VALIDATED
                and source.validation_status
                is not FundamentalValidationStatus.VALIDATED
            ):
                raise ValueError(
                    "validated fact requires validated source lineage"
                )
            if (
                fact.freshness_status
                is FundamentalFreshnessStatus.CURRENT
                and source.freshness_status
                is not FundamentalFreshnessStatus.CURRENT
            ):
                raise ValueError(
                    "current fact cannot upgrade non-current source evidence"
                )
            if (
                fact.freshness_status
                is FundamentalFreshnessStatus.ACCEPTABLE_FOR_PERIOD
                and source.freshness_status
                in {
                    FundamentalFreshnessStatus.STALE,
                    FundamentalFreshnessStatus.UNKNOWN,
                }
            ):
                raise ValueError(
                    "period-acceptable fact cannot upgrade weak source evidence"
                )
        for parent_id in fact.lineage.parent_evidence_ids:
            if parent_id == fact.evidence_id:
                raise ValueError("fact cannot derive from itself")
            if parent_id not in fact_by_id:
                raise ValueError("fact lineage references unknown evidence")

    def _validate_source_label(
        self,
        fact: NormalizedFundamentalFact,
        source_by_id: dict[str, FundamentalEvidenceSource],
    ) -> None:
        source_types = {
            source_by_id[reference.source_id].source_type
            for reference in fact.lineage.source_references
        }
        primary_types = {
            FundamentalSourceType.UPLOADED_FILE,
            FundamentalSourceType.USER_PROVIDED_EXPORT,
            FundamentalSourceType.CALLABLE_CONNECTED_SYSTEM,
            FundamentalSourceType.EXCHANGE_FILING,
            FundamentalSourceType.COMPANY_FILING,
            FundamentalSourceType.ANNUAL_REPORT,
            FundamentalSourceType.EARNINGS_RELEASE,
            FundamentalSourceType.INVESTOR_PRESENTATION,
            FundamentalSourceType.EARNINGS_TRANSCRIPT,
        }
        if (
            fact.evidence_label
            is FundamentalEvidenceLabel.FACT_SOURCE_REPORTED
            and (
                not source_types
                or not source_types.issubset(primary_types)
            )
        ):
            raise ValueError(
                "secondary/provider data cannot masquerade as source-reported"
            )
        if (
            fact.evidence_label
            is FundamentalEvidenceLabel.FACT_PROVIDER_STANDARDIZED
            and not source_types.intersection(
                {
                    FundamentalSourceType.PROVIDER_STANDARDIZED,
                    FundamentalSourceType.SECONDARY_AGGREGATOR,
                    FundamentalSourceType.USER_PROVIDED_EXPORT,
                    FundamentalSourceType.CALLABLE_CONNECTED_SYSTEM,
                }
            )
        ):
            raise ValueError(
                "provider-standardized fact requires provider evidence"
            )
        if (
            fact.evidence_label
            is FundamentalEvidenceLabel.ISSUER_MANAGEMENT_CLAIM
            and not source_types
            .intersection(
                {
                    FundamentalSourceType.COMPANY_FILING,
                    FundamentalSourceType.ANNUAL_REPORT,
                    FundamentalSourceType.EARNINGS_RELEASE,
                    FundamentalSourceType.INVESTOR_PRESENTATION,
                    FundamentalSourceType.EARNINGS_TRANSCRIPT,
                    FundamentalSourceType.UPLOADED_FILE,
                }
            )
        ):
            raise ValueError(
                "issuer claim requires issuer-controlled source evidence"
            )

    def _reject_lineage_cycles(
        self,
        fact_by_id: dict[str, NormalizedFundamentalFact],
    ) -> None:
        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(evidence_id: str) -> None:
            if evidence_id in visiting:
                raise ValueError("fundamental evidence lineage contains a cycle")
            if evidence_id in visited:
                return
            visiting.add(evidence_id)
            for parent_id in fact_by_id[evidence_id].lineage.parent_evidence_ids:
                visit(parent_id)
            visiting.remove(evidence_id)
            visited.add(evidence_id)

        for evidence_id in fact_by_id:
            visit(evidence_id)

    def _validate_conflicts(
        self,
        fact_by_id: dict[str, NormalizedFundamentalFact],
    ) -> None:
        conflict_ids = [conflict.conflict_id for conflict in self.conflicts]
        if len(conflict_ids) != len(set(conflict_ids)):
            raise ValueError("snapshot conflict IDs must be unique")

        recorded: dict[str, FundamentalConflictStatus] = {}
        for conflict in self.conflicts:
            conflict_facts = []
            for evidence_id in conflict.evidence_ids:
                fact = fact_by_id.get(evidence_id)
                if fact is None:
                    raise ValueError("conflict references unknown evidence")
                conflict_facts.append(fact)
                prior = recorded.get(evidence_id)
                if prior is not None and prior is not conflict.status:
                    raise ValueError(
                        "fact cannot have incompatible conflict states"
                    )
                recorded[evidence_id] = conflict.status
                if fact.conflict_status is not conflict.status:
                    raise ValueError(
                        "fact conflict status must match its conflict record"
                    )
            conflict_keys = {
                (
                    fact.statement,
                    fact.line_item_id,
                    fact.period,
                )
                for fact in conflict_facts
            }
            if len(conflict_keys) != 1:
                raise ValueError(
                    "one conflict must compare the same metric and period"
                )

        for fact in self.facts:
            if (
                fact.conflict_status is not FundamentalConflictStatus.NONE
                and fact.evidence_id not in recorded
            ):
                raise ValueError("conflicted fact requires a conflict record")

    def _has_primary_support(
        self,
        fact: NormalizedFundamentalFact,
        source_by_id: dict[str, FundamentalEvidenceSource],
        fact_by_id: dict[str, NormalizedFundamentalFact],
        visited: set[str] | None = None,
    ) -> bool:
        """Return whether a fact traces to governing or primary evidence."""

        if any(
            source_by_id[reference.source_id].source_rank
            <= FundamentalSourceRank.PRIMARY_PUBLIC_DISCLOSURE
            for reference in fact.lineage.source_references
        ):
            return True

        checked = set() if visited is None else visited
        if fact.evidence_id in checked:
            return False
        checked.add(fact.evidence_id)
        return any(
            self._has_primary_support(
                fact_by_id[parent_id],
                source_by_id,
                fact_by_id,
                checked,
            )
            for parent_id in fact.lineage.parent_evidence_ids
        )

    def _validate_release_posture(
        self,
        source_by_id: dict[str, FundamentalEvidenceSource],
        fact_by_id: dict[str, NormalizedFundamentalFact],
    ) -> None:
        if self.validation_status is FundamentalValidationStatus.VALIDATED:
            if any(
                fact.validation_status
                is not FundamentalValidationStatus.VALIDATED
                for fact in self.facts
            ):
                raise ValueError(
                    "validated snapshot requires every fact to be validated"
                )

        if self.evidence_posture is FundamentalEvidencePosture.DECISION_GRADE:
            if self.validation_status is not FundamentalValidationStatus.VALIDATED:
                raise ValueError(
                    "decision-grade evidence must be fully validated"
                )
            if any(
                source.validation_status
                is not FundamentalValidationStatus.VALIDATED
                or source.freshness_status
                not in {
                    FundamentalFreshnessStatus.CURRENT,
                    FundamentalFreshnessStatus.ACCEPTABLE_FOR_PERIOD,
                }
                for source in self.sources
            ):
                raise ValueError(
                    "decision-grade sources must be validated and current"
                )
            if any(
                conflict.status is FundamentalConflictStatus.UNRESOLVED
                for conflict in self.conflicts
            ) or any(
                fact.conflict_status is FundamentalConflictStatus.UNRESOLVED
                or fact.availability_status
                is not FundamentalAvailabilityStatus.AVAILABLE
                or fact.freshness_status
                not in {
                    FundamentalFreshnessStatus.CURRENT,
                    FundamentalFreshnessStatus.ACCEPTABLE_FOR_PERIOD,
                }
                or fact.evidence_label
                in {
                    FundamentalEvidenceLabel.MISSING_REQUIRED_SOURCE,
                    FundamentalEvidenceLabel.CONTRADICTED_SOURCE,
                    FundamentalEvidenceLabel.STALE_SOURCE,
                    FundamentalEvidenceLabel.UNKNOWN,
                }
                for fact in self.facts
            ):
                raise ValueError(
                    "decision-grade evidence cannot be missing, stale, or "
                    "conflicted"
                )
            if any(
                not self._has_primary_support(
                    fact,
                    source_by_id,
                    fact_by_id,
                )
                for fact in self.facts
            ):
                raise ValueError(
                    "decision-grade facts must trace to governing or primary "
                    "evidence; provider data cannot be the sole source"
                )

    @computed_field
    @property
    def snapshot_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"snapshot_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()
