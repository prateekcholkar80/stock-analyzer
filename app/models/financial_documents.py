"""Provider-neutral structured financial-tab document contracts."""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.fundamentals import (
    FundamentalAvailabilityStatus,
    FundamentalIssuerIdentity,
    FundamentalModel,
    FundamentalPeriodType,
    FundamentalValidationStatus,
    FundamentalValueKind,
    ProviderConnectionScope,
)


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_ROW_KEY_PATTERN = r"^[a-z][a-z0-9_.:-]*$"


class FinancialDocumentType(StrEnum):
    """Supported structured documents within a provider financial tab."""

    GROWTH_TABLE = "growth_table"
    BALANCE_SHEET = "balance_sheet"
    PROFIT_AND_LOSS = "profit_and_loss"
    CASH_FLOW = "cash_flow"
    RATIOS = "ratios"
    QUARTERLY_RESULTS = "quarterly_results"


class FinancialReportingBasis(StrEnum):
    """Provider-confirmed accounting perimeter for one document."""

    CONSOLIDATED = "consolidated"
    STANDALONE = "standalone"
    NOT_APPLICABLE = "not_applicable"


class FinancialDocumentRowKind(StrEnum):
    SECTION = "section"
    TOTAL = "total"
    SUBTOTAL = "subtotal"
    COMPONENT = "component"
    METRIC = "metric"


class FinancialDocumentPeriod(FundamentalModel):
    """One provider-displayed reporting period or undated comparison horizon."""

    period_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )
    source_label: str = Field(min_length=1, max_length=80)
    period_type: FundamentalPeriodType | None = None
    start_date: date | None = None
    end_date: date | None = None
    display_order: int = Field(ge=0, le=200)

    @model_validator(mode="after")
    def validate_period(self) -> Self:
        if self.start_date is not None and self.end_date is None:
            raise ValueError("financial document period start requires an end")
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.start_date > self.end_date
        ):
            raise ValueError("financial document period start cannot follow end")
        return self


class FinancialDocumentCell(FundamentalModel):
    """One exact table cell with missing values kept distinct from zero."""

    period_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )
    source_value: str | None = Field(default=None, min_length=1, max_length=500)
    normalized_value: Decimal | None = None
    source_unit: str | None = Field(default=None, min_length=1, max_length=80)
    normalized_unit: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
    )
    yoy_change: str | None = Field(default=None, min_length=1, max_length=80)
    percentage_of_parent: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
    )
    availability_status: FundamentalAvailabilityStatus

    @field_validator("normalized_value")
    @classmethod
    def require_finite_value(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("financial document values must be finite")
        return value

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        available = (
            self.availability_status
            is FundamentalAvailabilityStatus.AVAILABLE
        )
        supplied = self.source_value is not None or self.normalized_value is not None
        if available and (
            self.source_value is None
            or self.normalized_value is None
            or self.source_unit is None
            or self.normalized_unit is None
        ):
            raise ValueError("available financial cell requires value and units")
        if not available and supplied:
            raise ValueError("unavailable financial cell cannot release a value")
        if not available and (
            self.yoy_change is not None
            or self.percentage_of_parent is not None
        ):
            raise ValueError(
                "unavailable financial cell cannot release auxiliary values"
            )
        return self


class FinancialDocumentRow(FundamentalModel):
    """One provider row whose hierarchy is learned from the rendered table."""

    row_key: str = Field(
        min_length=1,
        max_length=200,
        pattern=_ROW_KEY_PATTERN,
    )
    original_label: str = Field(min_length=1, max_length=300)
    standardized_label: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=_ROW_KEY_PATTERN,
    )
    parent_row_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=_ROW_KEY_PATTERN,
    )
    depth: int = Field(ge=0, le=20)
    row_kind: FinancialDocumentRowKind
    value_kind: FundamentalValueKind
    display_order: int = Field(ge=0, le=2_000)
    cells: tuple[FinancialDocumentCell, ...] = Field(
        min_length=1,
        max_length=200,
    )

    @field_validator("cells")
    @classmethod
    def require_unique_periods(
        cls,
        values: tuple[FinancialDocumentCell, ...],
    ) -> tuple[FinancialDocumentCell, ...]:
        period_keys = tuple(value.period_key for value in values)
        if len(period_keys) != len(set(period_keys)):
            raise ValueError("financial row period cells must be unique")
        return values

    @model_validator(mode="after")
    def validate_parent_shape(self) -> Self:
        if self.depth == 0 and self.parent_row_key is not None:
            raise ValueError("root financial row cannot have a parent")
        if self.depth > 0 and self.parent_row_key is None:
            raise ValueError("nested financial row requires a parent")
        if self.parent_row_key == self.row_key:
            raise ValueError("financial row cannot parent itself")
        return self


class StructuredFinancialDocument(FundamentalModel):
    """Complete JSON document for one company, scenario, and basis."""

    schema_version: Literal["jarvis.financial_document.v1"] = (
        "jarvis.financial_document.v1"
    )
    document_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=_ID_PATTERN,
    )
    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity
    document_type: FinancialDocumentType
    reporting_basis: FinancialReportingBasis
    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    source_unit: str = Field(min_length=1, max_length=80)
    skipped_period_labels: tuple[str, ...] = Field(
        default=(),
        max_length=200,
    )
    periods: tuple[FinancialDocumentPeriod, ...] = Field(
        min_length=1,
        max_length=200,
    )
    rows: tuple[FinancialDocumentRow, ...] = Field(
        min_length=1,
        max_length=2_000,
    )
    source_location: str = Field(min_length=1, max_length=2_000)
    retrieved_at: datetime
    expires_at: datetime
    all_sections_expanded: bool
    validation_status: FundamentalValidationStatus
    limitations: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator("currency", mode="before")
    @classmethod
    def normalize_currency(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @field_validator("retrieved_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("financial document timestamps require timezone")
        return value

    @field_validator("limitations")
    @classmethod
    def require_bounded_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("financial document limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("financial document limitations must be unique")
        return values

    @field_validator("skipped_period_labels")
    @classmethod
    def require_bounded_skipped_period_labels(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 80 for value in values):
            raise ValueError("skipped financial periods must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("skipped financial periods must be unique")
        return values

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        if self.expires_at <= self.retrieved_at:
            raise ValueError("financial document expiry must follow retrieval")
        if not self.all_sections_expanded:
            raise ValueError("active financial document must be fully expanded")
        if self.validation_status is not FundamentalValidationStatus.VALIDATED:
            raise ValueError("active financial document must be validated")

        period_keys = tuple(period.period_key for period in self.periods)
        if len(period_keys) != len(set(period_keys)):
            raise ValueError("financial document periods must be unique")
        if len({period.display_order for period in self.periods}) != len(
            self.periods
        ):
            raise ValueError("financial document period order must be unique")

        rows_by_key = {row.row_key: row for row in self.rows}
        if len(rows_by_key) != len(self.rows):
            raise ValueError("financial document row keys must be unique")
        if len({row.display_order for row in self.rows}) != len(self.rows):
            raise ValueError("financial document row order must be unique")
        expected_periods = set(period_keys)
        for row in self.rows:
            if {cell.period_key for cell in row.cells} != expected_periods:
                raise ValueError("every financial row must cover every period")
            if row.parent_row_key is None:
                continue
            parent = rows_by_key.get(row.parent_row_key)
            if parent is None:
                raise ValueError("financial row parent must exist")
            if parent.display_order >= row.display_order:
                raise ValueError("financial row parent must precede its child")
            if parent.depth != row.depth - 1:
                raise ValueError("financial row hierarchy depth is inconsistent")
        return self

    @computed_field
    @property
    def document_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"document_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


class PeerComparisonMetric(FundamentalModel):
    """Provider-neutral definition of one peer-comparison column."""

    metric_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    source_label: str = Field(min_length=1, max_length=120)
    standardized_label: str = Field(min_length=1, max_length=120)
    value_kind: FundamentalValueKind
    source_unit: str = Field(min_length=1, max_length=80)
    normalized_unit: str = Field(min_length=1, max_length=80)
    currency: str | None = Field(
        default=None,
        min_length=3,
        max_length=3,
        pattern=r"^[A-Z]{3}$",
    )
    display_order: int = Field(ge=0, le=29)

    @model_validator(mode="after")
    def validate_metric_semantics(self) -> Self:
        monetary = self.value_kind is FundamentalValueKind.MONETARY
        if monetary != (self.currency is not None):
            raise ValueError(
                "peer monetary metrics require currency exclusively"
            )
        if self.value_kind not in {
            FundamentalValueKind.MONETARY,
            FundamentalValueKind.PERCENTAGE,
            FundamentalValueKind.RATIO,
        }:
            raise ValueError("peer metric value kind is unsupported")
        return self


class PeerComparisonCell(FundamentalModel):
    """One normalized peer value, retaining provider text and missingness."""

    metric_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    source_value: str | None = Field(default=None, min_length=1, max_length=120)
    normalized_value: Decimal | None = None
    availability_status: FundamentalAvailabilityStatus

    @field_validator("normalized_value")
    @classmethod
    def require_finite_value(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("peer comparison values must be finite")
        return value

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        available = (
            self.availability_status
            is FundamentalAvailabilityStatus.AVAILABLE
        )
        if available != (
            self.source_value is not None
            and self.normalized_value is not None
        ):
            raise ValueError(
                "peer comparison availability contradicts its values"
            )
        return self


class PeerComparisonCompany(FundamentalModel):
    """One subject or peer company in provider display order."""

    peer_key: str = Field(
        min_length=1,
        max_length=250,
        pattern=r"^peer_[a-z0-9_]+$",
    )
    legal_name: str = Field(min_length=1, max_length=300)
    provider_slug: str = Field(
        min_length=1,
        max_length=240,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
    )
    is_subject: bool
    display_order: int = Field(ge=0, le=49)
    cells: tuple[PeerComparisonCell, ...] = Field(
        min_length=1,
        max_length=30,
    )


class StructuredPeerComparisonDocument(FundamentalModel):
    """Validated cross-sectional peer matrix independent of its provider."""

    schema_version: Literal["jarvis.peer_comparison.v1"] = (
        "jarvis.peer_comparison.v1"
    )
    document_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=_ID_PATTERN,
    )
    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity
    observation_date: date
    metrics: tuple[PeerComparisonMetric, ...] = Field(
        min_length=1,
        max_length=30,
    )
    peers: tuple[PeerComparisonCompany, ...] = Field(
        min_length=1,
        max_length=50,
    )
    source_location: str = Field(min_length=1, max_length=2_000)
    retrieved_at: datetime
    expires_at: datetime
    validation_status: FundamentalValidationStatus
    limitations: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator("retrieved_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("peer comparison timestamps require timezone")
        return value

    @field_validator("limitations")
    @classmethod
    def require_bounded_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("peer comparison limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("peer comparison limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        if self.expires_at <= self.retrieved_at:
            raise ValueError("peer comparison expiry must follow retrieval")
        if self.validation_status is not FundamentalValidationStatus.VALIDATED:
            raise ValueError("active peer comparison must be validated")

        metric_keys = tuple(metric.metric_key for metric in self.metrics)
        if len(metric_keys) != len(set(metric_keys)):
            raise ValueError("peer comparison metric keys must be unique")
        if tuple(metric.display_order for metric in self.metrics) != tuple(
            range(len(self.metrics))
        ):
            raise ValueError("peer comparison metrics must be contiguous")

        peer_keys = tuple(peer.peer_key for peer in self.peers)
        peer_slugs = tuple(peer.provider_slug for peer in self.peers)
        if (
            len(peer_keys) != len(set(peer_keys))
            or len(peer_slugs) != len(set(peer_slugs))
        ):
            raise ValueError("peer comparison companies must be unique")
        if tuple(peer.display_order for peer in self.peers) != tuple(
            range(len(self.peers))
        ):
            raise ValueError("peer comparison companies must be contiguous")
        if sum(peer.is_subject for peer in self.peers) != 1:
            raise ValueError("peer comparison must identify one subject")
        subject = next(peer for peer in self.peers if peer.is_subject)
        if (
            self.issuer.provider_slug is None
            or subject.provider_slug != self.issuer.provider_slug
        ):
            raise ValueError("peer comparison subject must match issuer")
        if any(
            tuple(cell.metric_key for cell in peer.cells) != metric_keys
            for peer in self.peers
        ):
            raise ValueError(
                "peer comparison companies must cover metrics in order"
            )
        return self

    @computed_field
    @property
    def document_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"document_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


class BenchmarkingSection(StrEnum):
    """Provider-neutral sections present in the Financial benchmark matrix."""

    OPERATIONAL_METRICS = "operational_metrics"
    FINANCIALS = "financials"
    SHAREHOLDINGS = "shareholdings"


class BenchmarkingCompany(FundamentalModel):
    """One subject or peer column in provider display order."""

    company_key: str = Field(
        min_length=1,
        max_length=250,
        pattern=r"^company_[a-z0-9_]+$",
    )
    legal_name: str = Field(min_length=1, max_length=300)
    provider_slug: str = Field(
        min_length=1,
        max_length=240,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
    )
    is_subject: bool
    display_order: int = Field(ge=0, le=29)

    @model_validator(mode="after")
    def validate_company_key(self) -> Self:
        expected = f"company_{self.provider_slug.replace('-', '_')}"
        if self.company_key != expected:
            raise ValueError(
                "benchmarking company key must match provider slug"
            )
        return self


class BenchmarkingCell(FundamentalModel):
    """One normalized company value retaining exact provider text."""

    company_key: str = Field(
        min_length=1,
        max_length=250,
        pattern=r"^company_[a-z0-9_]+$",
    )
    source_value: str | None = Field(default=None, min_length=1, max_length=160)
    normalized_value: Decimal | None = None
    availability_status: FundamentalAvailabilityStatus
    is_best: bool

    @field_validator("normalized_value")
    @classmethod
    def require_finite_value(cls, value: Decimal | None) -> Decimal | None:
        if value is not None and not value.is_finite():
            raise ValueError("benchmarking values must be finite")
        return value

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        available = (
            self.availability_status
            is FundamentalAvailabilityStatus.AVAILABLE
        )
        if available != (
            self.source_value is not None
            and self.normalized_value is not None
        ):
            raise ValueError(
                "benchmarking availability contradicts its values"
            )
        if not available and self.is_best:
            raise ValueError("missing benchmarking value cannot be best")
        return self


class BenchmarkingRow(FundamentalModel):
    """One dynamic, hierarchical Benchmarking metric or section."""

    row_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    parent_row_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    original_label: str = Field(min_length=1, max_length=300)
    depth: int = Field(ge=0, le=10)
    row_kind: Literal["section", "metric"]
    section: BenchmarkingSection
    value_kind: FundamentalValueKind
    source_unit: str = Field(min_length=1, max_length=80)
    normalized_unit: str = Field(min_length=1, max_length=80)
    provider_hidden: bool
    display_order: int = Field(ge=0, le=299)
    cells: tuple[BenchmarkingCell, ...] = Field(
        min_length=2,
        max_length=30,
    )

    @model_validator(mode="after")
    def validate_row(self) -> Self:
        if self.depth == 0 and self.parent_row_key is not None:
            raise ValueError("root benchmarking row cannot have a parent")
        if self.depth > 0 and self.parent_row_key is None:
            raise ValueError("nested benchmarking row requires a parent")
        if self.parent_row_key == self.row_key:
            raise ValueError("benchmarking row cannot parent itself")
        if self.row_kind == "section" and (
            self.value_kind is not FundamentalValueKind.OTHER
            or self.source_unit != "not applicable"
            or self.normalized_unit != "not applicable"
        ):
            raise ValueError(
                "benchmarking sections require not-applicable value semantics"
            )
        return self


class StructuredBenchmarkingFinancialsDocument(FundamentalModel):
    """Provider-neutral complete Financial benchmarking matrix."""

    schema_version: Literal["jarvis.benchmarking_financials.v1"] = (
        "jarvis.benchmarking_financials.v1"
    )
    document_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=_ID_PATTERN,
    )
    connection: ProviderConnectionScope
    issuer: FundamentalIssuerIdentity
    observation_date: date
    reporting_basis: Literal["not_applicable"] = "not_applicable"
    companies: tuple[BenchmarkingCompany, ...] = Field(
        min_length=2,
        max_length=30,
    )
    rows: tuple[BenchmarkingRow, ...] = Field(
        min_length=1,
        max_length=300,
    )
    source_location: str = Field(min_length=1, max_length=2_000)
    retrieved_at: datetime
    expires_at: datetime
    all_rows_captured: bool
    validation_status: FundamentalValidationStatus
    limitations: tuple[str, ...] = Field(default=(), max_length=100)

    @field_validator("retrieved_at", "expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("benchmarking timestamps require timezone")
        return value

    @field_validator("limitations")
    @classmethod
    def require_bounded_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("benchmarking limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("benchmarking limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_document(self) -> Self:
        if self.expires_at <= self.retrieved_at:
            raise ValueError("benchmarking expiry must follow retrieval")
        if not self.all_rows_captured:
            raise ValueError("active benchmarking must include every row")
        if self.validation_status is not FundamentalValidationStatus.VALIDATED:
            raise ValueError("active benchmarking must be validated")

        company_keys = tuple(company.company_key for company in self.companies)
        company_slugs = tuple(
            company.provider_slug for company in self.companies
        )
        if (
            len(company_keys) != len(set(company_keys))
            or len(company_slugs) != len(set(company_slugs))
        ):
            raise ValueError("benchmarking companies must be unique")
        if tuple(company.display_order for company in self.companies) != tuple(
            range(len(self.companies))
        ):
            raise ValueError("benchmarking companies must be contiguous")
        if sum(company.is_subject for company in self.companies) != 1:
            raise ValueError("benchmarking must identify one subject")
        subject = next(
            company for company in self.companies if company.is_subject
        )
        if (
            self.issuer.provider_slug is None
            or subject.provider_slug != self.issuer.provider_slug
        ):
            raise ValueError("benchmarking subject must match issuer")

        row_keys: set[str] = set()
        row_depths: dict[str, int] = {}
        for expected_order, row in enumerate(self.rows):
            if row.row_key in row_keys:
                raise ValueError("benchmarking row keys must be unique")
            if row.display_order != expected_order:
                raise ValueError("benchmarking rows must be contiguous")
            if row.parent_row_key is not None:
                parent_depth = row_depths.get(row.parent_row_key)
                if parent_depth is None or parent_depth + 1 != row.depth:
                    raise ValueError("benchmarking hierarchy is inconsistent")
            if tuple(cell.company_key for cell in row.cells) != company_keys:
                raise ValueError(
                    "benchmarking rows must cover companies in order"
                )
            row_keys.add(row.row_key)
            row_depths[row.row_key] = row.depth
        return self

    @computed_field
    @property
    def document_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"document_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()
