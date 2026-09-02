"""Offline-safe contracts for a future local Tijori MCP transport."""

import json
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from math import isfinite
from typing import Any, Literal, Protocol, Self, runtime_checkable
from urllib.parse import urlsplit

from pydantic import Field, computed_field, field_validator, model_validator

from app.gateways.fundamentals import (
    FundamentalIssuerMatchKind,
    FundamentalResolutionStatus,
)
from app.models.fundamentals import (
    FundamentalAvailabilityStatus,
    FundamentalModel,
    FundamentalPeriodType,
    FundamentalStatement,
    FundamentalValueKind,
    ProviderConnectionScope,
)


TIJORI_APPROVED_TOOLS = (
    "search_company",
    "resolve_company_ids",
    "get_company_overview",
    "get_financials",
    "get_shareholding",
)
TijoriToolName = Literal[
    "search_company",
    "resolve_company_ids",
    "get_company_overview",
    "get_financials",
    "get_shareholding",
]

# Fully expanded statements are bounded at 500 rows by 40 periods. Their
# labelled cell objects legitimately exceed the earlier generic envelope even
# though the document remains finite and schema validated.
_MAX_PAYLOAD_BYTES = 8_000_000
_MAX_JSON_DEPTH = 16
_MAX_JSON_NODES = 250_000


class TijoriToolStatus(StrEnum):
    SUCCESS = "success"
    NOT_FOUND = "not_found"
    NOT_ENTITLED = "not_entitled"
    PAYWALLED = "paywalled"
    AUTHENTICATION_REQUIRED = "authentication_required"
    RATE_LIMITED = "rate_limited"
    UNAVAILABLE = "unavailable"


class TijoriTransportFailureKind(StrEnum):
    CONFIGURATION = "configuration"
    AUTHENTICATION = "authentication"
    ENTITLEMENT = "entitlement"
    RATE_LIMIT = "rate_limit"
    UNAVAILABLE = "unavailable"
    PROTOCOL = "protocol"


class TijoriMcpTransportError(Exception):
    """Sanitized transport failure with no provider response text."""

    def __init__(self, kind: TijoriTransportFailureKind) -> None:
        if not isinstance(kind, TijoriTransportFailureKind):
            raise TypeError("Tijori transport failure requires a known kind")
        super().__init__("Tijori MCP transport could not complete the call")
        self.kind = kind


class TijoriMcpAdapterSettings(FundamentalModel):
    """Non-secret, pinned contract settings for the offline adapter."""

    provider: Literal["tijori"] = "tijori"
    adapter_version: Literal["jarvis.tijori_mcp_adapter.v1"] = (
        "jarvis.tijori_mcp_adapter.v1"
    )
    provider_contract_version: str = Field(
        default="tijori.synthetic_contract.v1",
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    parser_version: Literal["1.0.0"] = "1.0.0"
    approved_tools: tuple[str, ...] = TIJORI_APPROVED_TOOLS

    @field_validator("approved_tools")
    @classmethod
    def enforce_exact_allow_list(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if values != TIJORI_APPROVED_TOOLS:
            raise ValueError("Tijori adapter tool allow-list is immutable")
        return values

    @computed_field
    @property
    def configuration_fingerprint(self) -> str:
        payload = self.model_dump_json(
            exclude={"configuration_fingerprint"}
        )
        return sha256(payload.encode("utf-8")).hexdigest()


class TijoriMcpTransportInspection(FundamentalModel):
    available_tools: tuple[str, ...]
    authenticated: bool
    checked_at: datetime
    provider_contract_version: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )

    @field_validator("available_tools")
    @classmethod
    def validate_available_tools(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("Tijori transport tools must be unique")
        if not set(values).issubset(TIJORI_APPROVED_TOOLS):
            raise ValueError("Tijori transport exposed an unapproved tool")
        return values

    @field_validator("checked_at")
    @classmethod
    def require_checked_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Tijori inspection time must include timezone")
        return value


class TijoriMcpToolResult(FundamentalModel):
    tool_name: TijoriToolName
    status: TijoriToolStatus
    payload: dict[str, Any] | None = None

    @model_validator(mode="after")
    def validate_payload(self) -> Self:
        if self.status is TijoriToolStatus.SUCCESS:
            if self.payload is None:
                raise ValueError("successful Tijori tool result needs payload")
            _canonical_payload(self.payload)
        elif self.payload is not None:
            raise ValueError("failed Tijori result cannot release payload")
        return self

    @computed_field
    @property
    def payload_fingerprint(self) -> str | None:
        if self.payload is None:
            return None
        return sha256(_canonical_payload(self.payload)).hexdigest()


@runtime_checkable
class TijoriMcpTransport(Protocol):
    """Transport-only boundary; credentials and sessions remain behind it."""

    @property
    def configuration_fingerprint(self) -> str:
        ...

    def inspect(
        self,
        *,
        connection: ProviderConnectionScope,
    ) -> TijoriMcpTransportInspection:
        ...

    def call_tool(
        self,
        *,
        connection: ProviderConnectionScope,
        tool_name: TijoriToolName,
        arguments: dict[str, object],
    ) -> TijoriMcpToolResult:
        ...


class TijoriCompanyRecord(FundamentalModel):
    company_id: str = Field(min_length=1, max_length=128)
    slug: str = Field(min_length=1, max_length=240)
    legal_name: str = Field(min_length=1, max_length=300)
    exchange: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=1, max_length=100)
    isin: str | None = Field(
        default=None,
        pattern=r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$",
    )
    match_kind: FundamentalIssuerMatchKind
    match_score: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    matched_on: tuple[str, ...] = Field(min_length=1, max_length=10)

    @field_validator("exchange", "symbol", "isin", mode="before")
    @classmethod
    def normalize_market_identity(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @field_validator("matched_on")
    @classmethod
    def validate_matched_on(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(values) != len(set(values)) or any(
            not value or len(value) > 120 for value in values
        ):
            raise ValueError("Tijori match fields must be unique and bounded")
        return values

    @field_validator("match_score")
    @classmethod
    def require_finite_match_score(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Tijori match score must be finite")
        return value


class TijoriCompanySearchPayload(FundamentalModel):
    companies: tuple[TijoriCompanyRecord, ...] = Field(max_length=25)

    @model_validator(mode="after")
    def require_unique_companies(self) -> Self:
        identities = tuple(
            (
                company.company_id,
                company.exchange,
                company.symbol,
                company.isin,
            )
            for company in self.companies
        )
        if len(identities) != len(set(identities)):
            raise ValueError("Tijori search companies must be unique")
        return self


class TijoriIssuerResolutionPayload(FundamentalModel):
    status: FundamentalResolutionStatus
    companies: tuple[TijoriCompanyRecord, ...] = Field(max_length=25)
    selected_company_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        ids = tuple(company.company_id for company in self.companies)
        if len(ids) != len(set(ids)):
            raise ValueError("Tijori resolution company IDs must be unique")
        if self.status is FundamentalResolutionStatus.RESOLVED:
            if (
                self.selected_company_id is None
                or self.selected_company_id not in ids
            ):
                raise ValueError("resolved Tijori issuer needs selected ID")
        elif self.selected_company_id is not None:
            raise ValueError("unresolved Tijori issuer cannot select an ID")
        if (
            self.status is FundamentalResolutionStatus.AMBIGUOUS
            and len(self.companies) < 2
        ):
            raise ValueError("ambiguous Tijori issuer needs two candidates")
        if (
            self.status is FundamentalResolutionStatus.NOT_FOUND
            and self.companies
        ):
            raise ValueError("not-found Tijori issuer must have no candidates")
        return self


class TijoriEvidenceSourceRecord(FundamentalModel):
    provider_source_id: str = Field(min_length=1, max_length=128)
    source_name: str = Field(min_length=1, max_length=300)
    location: str = Field(min_length=1, max_length=2_000)
    period_covered: str = Field(min_length=1, max_length=200)
    as_of_date: date
    published_at: datetime | None = None

    @field_validator("location")
    @classmethod
    def require_safe_tijori_location(cls, value: str) -> str:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if (
            parsed.scheme != "https"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or not (
                host == "tijorifinance.com"
                or host.endswith(".tijorifinance.com")
            )
        ):
            raise ValueError("Tijori source location is not safe to persist")
        return value

    @field_validator("published_at")
    @classmethod
    def require_published_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("Tijori publication time requires timezone")
        return value


class TijoriEvidenceFactRecord(FundamentalModel):
    provider_fact_id: str = Field(min_length=1, max_length=160)
    provider_source_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    statement: FundamentalStatement
    line_item_original: str = Field(min_length=1, max_length=300)
    line_item_standard: str = Field(min_length=1, max_length=300)
    line_item_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[a-z][a-z0-9_.-]*$",
    )
    period_label: str = Field(min_length=1, max_length=80)
    period_type: FundamentalPeriodType
    period_start: date | None = None
    period_end: date | None = None
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
    availability_status: FundamentalAvailabilityStatus

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
            raise ValueError("Tijori normalized value must be finite")
        return value

    @model_validator(mode="after")
    def validate_fact(self) -> Self:
        if (
            self.period_start is not None
            and self.period_end is not None
            and self.period_start > self.period_end
        ):
            raise ValueError("Tijori fact period start cannot follow end")
        available = (
            self.availability_status
            is FundamentalAvailabilityStatus.AVAILABLE
        )
        if available and (
            self.provider_source_id is None
            or self.source_value is None
            or self.normalized_value is None
            or self.source_unit is None
            or self.normalized_unit is None
        ):
            raise ValueError("available Tijori fact lacks value or source")
        if not available and self.normalized_value is not None:
            raise ValueError("unavailable Tijori fact cannot contain a value")
        if (
            available
            and self.value_kind
            in {
                FundamentalValueKind.MONETARY,
                FundamentalValueKind.PER_SHARE,
            }
            and self.currency is None
        ):
            raise ValueError("monetary Tijori fact requires currency")
        return self


class TijoriEvidencePayload(FundamentalModel):
    company_id: str = Field(min_length=1, max_length=128)
    exchange: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=1, max_length=100)
    sources: tuple[TijoriEvidenceSourceRecord, ...] = Field(
        min_length=1,
        max_length=100,
    )
    facts: tuple[TijoriEvidenceFactRecord, ...] = Field(
        min_length=1,
        max_length=2_000,
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=50)

    @field_validator("exchange", "symbol", mode="before")
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        return value.upper()

    @field_validator("limitations")
    @classmethod
    def validate_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(values) != len(set(values)) or any(
            not value or len(value) > 500 for value in values
        ):
            raise ValueError("Tijori limitations must be unique and bounded")
        return values

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        source_ids = {source.provider_source_id for source in self.sources}
        if len(source_ids) != len(self.sources):
            raise ValueError("Tijori evidence source IDs must be unique")
        fact_ids = tuple(fact.provider_fact_id for fact in self.facts)
        if len(fact_ids) != len(set(fact_ids)):
            raise ValueError("Tijori evidence fact IDs must be unique")
        if any(
            fact.provider_source_id is not None
            and fact.provider_source_id not in source_ids
            for fact in self.facts
        ):
            raise ValueError("Tijori fact references an unknown source")
        return self


class TijoriFinancialDocumentIssuer(FundamentalModel):
    """Provider identity embedded in one structured financial document."""

    exchange: str = Field(min_length=1, max_length=32)
    symbol: str = Field(min_length=1, max_length=100)
    legal_name: str = Field(min_length=1, max_length=300)
    provider_company_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=128,
    )
    provider_slug: str = Field(
        min_length=1,
        max_length=240,
        pattern=r"^[a-z0-9][a-z0-9-]*$",
    )

    @field_validator("exchange", "symbol", mode="before")
    @classmethod
    def normalize_market_identity(cls, value: str) -> str:
        return value.upper()


class TijoriFinancialDocumentSource(FundamentalModel):
    provider: Literal["tijori"] = "tijori"
    location: str = Field(min_length=1, max_length=2_000)
    retrieved_at: datetime

    @field_validator("retrieved_at")
    @classmethod
    def require_retrieval_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Tijori document retrieval time requires timezone")
        return value

    @field_validator("location")
    @classmethod
    def require_safe_financials_location(cls, value: str) -> str:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or (parsed.hostname or "").lower() != "www.tijorifinance.com"
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError("Tijori financial document location is unsafe")
        return value


class TijoriFinancialDocumentColumn(FundamentalModel):
    column_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.:-]*$",
    )
    source_label: str = Field(min_length=1, max_length=80)
    display_order: int = Field(ge=0, le=200)


class TijoriFinancialDocumentCell(FundamentalModel):
    column_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.:-]*$",
    )
    source_value: str | None = Field(default=None, min_length=1, max_length=500)
    yoy_change: str | None = Field(default=None, min_length=1, max_length=80)
    percentage_of_parent: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
    )
    availability_status: Literal["available", "unknown"]

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if (self.availability_status == "available") != (
            self.source_value is not None
        ):
            raise ValueError("Tijori document cell availability contradicts value")
        if self.source_value is None and (
            self.yoy_change is not None
            or self.percentage_of_parent is not None
        ):
            raise ValueError(
                "Tijori document auxiliary values require a primary value"
            )
        return self


class TijoriFinancialDocumentRow(FundamentalModel):
    row_key: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[a-z][a-z0-9_.:-]*$",
    )
    original_label: str = Field(min_length=1, max_length=300)
    parent_row_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[a-z][a-z0-9_.:-]*$",
    )
    depth: int = Field(ge=0, le=20)
    row_kind: Literal["section", "total", "subtotal", "component", "metric"]
    display_order: int = Field(ge=0, le=499)
    values: tuple[TijoriFinancialDocumentCell, ...] = Field(
        min_length=1,
        max_length=120,
    )

    @model_validator(mode="after")
    def validate_parent_shape(self) -> Self:
        if self.depth == 0 and self.parent_row_key is not None:
            raise ValueError("Tijori root document row cannot have a parent")
        if self.depth > 0 and self.parent_row_key is None:
            raise ValueError("Tijori nested document row requires a parent")
        if self.parent_row_key == self.row_key:
            raise ValueError("Tijori document row cannot parent itself")
        return self


class TijoriFinancialDocumentExtraction(FundamentalModel):
    column_count: int = Field(ge=1, le=120)
    row_count: int = Field(ge=1, le=500)
    status: Literal["complete"] = "complete"


class TijoriGrowthTableDocument(FundamentalModel):
    """Exact fail-closed contract emitted by the Growth Table extractor."""

    schema_version: Literal["tijori.financial_document.v1"]
    document_type: Literal["growth_table"]
    reporting_basis: Literal["consolidated", "standalone", "not_applicable"]
    issuer: TijoriFinancialDocumentIssuer
    source: TijoriFinancialDocumentSource
    unit: str = Field(min_length=1, max_length=80)
    all_sections_expanded: Literal[True]
    columns: tuple[TijoriFinancialDocumentColumn, ...] = Field(
        min_length=1,
        max_length=120,
    )
    rows: tuple[TijoriFinancialDocumentRow, ...] = Field(
        min_length=1,
        max_length=500,
    )
    extraction: TijoriFinancialDocumentExtraction

    @model_validator(mode="after")
    def validate_complete_document(self) -> Self:
        expected_location = (
            "https://www.tijorifinance.com/company/"
            f"{self.issuer.provider_slug}/financials/"
        )
        if self.source.location != expected_location:
            raise ValueError("Tijori document location does not match issuer")

        column_keys = tuple(column.column_key for column in self.columns)
        if len(column_keys) != len(set(column_keys)):
            raise ValueError("Tijori document column keys must be unique")
        if tuple(column.display_order for column in self.columns) != tuple(
            range(len(self.columns))
        ):
            raise ValueError("Tijori document columns must be contiguous")

        row_keys = tuple(row.row_key for row in self.rows)
        if len(row_keys) != len(set(row_keys)):
            raise ValueError("Tijori document row keys must be unique")
        if tuple(row.display_order for row in self.rows) != tuple(
            range(len(self.rows))
        ):
            raise ValueError("Tijori document rows must be contiguous")
        if len(self.rows) * len(self.columns) > 5_000:
            raise ValueError("Tijori document exceeds the cell safety bound")

        rows_by_key = {row.row_key: row for row in self.rows}
        for row in self.rows:
            if tuple(cell.column_key for cell in row.values) != column_keys:
                raise ValueError("Tijori document row must cover columns in order")
            if row.parent_row_key is None:
                continue
            parent = rows_by_key.get(row.parent_row_key)
            if (
                parent is None
                or parent.display_order >= row.display_order
                or parent.depth != row.depth - 1
            ):
                raise ValueError("Tijori document row hierarchy is invalid")

        if (
            self.extraction.column_count != len(self.columns)
            or self.extraction.row_count != len(self.rows)
        ):
            raise ValueError("Tijori document extraction counts do not match")
        return self

    @computed_field
    @property
    def document_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"document_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


class TijoriGrowthTablePayload(FundamentalModel):
    """Successful get_financials payload for one structured Growth Table."""

    document: TijoriGrowthTableDocument


class TijoriFinancialStatementPeriod(FundamentalModel):
    """One provider-labelled statement period in original display order."""

    period_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.:-]*$",
    )
    source_label: str = Field(min_length=1, max_length=80)
    display_order: int = Field(ge=0, le=200)


class TijoriFinancialStatementCell(FundamentalModel):
    """One exact statement cell with missing values distinct from zero."""

    period_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_.:-]*$",
    )
    source_value: str | None = Field(default=None, min_length=1, max_length=500)
    yoy_change: str | None = Field(default=None, min_length=1, max_length=80)
    percentage_of_parent: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
    )
    availability_status: Literal["available", "unknown"]

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if (self.availability_status == "available") != (
            self.source_value is not None
        ):
            raise ValueError(
                "Tijori statement cell availability contradicts value"
            )
        if self.source_value is None and (
            self.yoy_change is not None
            or self.percentage_of_parent is not None
        ):
            raise ValueError(
                "Tijori statement auxiliary values require a primary value"
            )
        return self


class TijoriFinancialStatementRow(FundamentalModel):
    """One provider-defined row in a recursively expanded statement."""

    row_key: str = Field(
        min_length=1,
        max_length=200,
        pattern=r"^[a-z][a-z0-9_.:-]*$",
    )
    original_label: str = Field(min_length=1, max_length=300)
    parent_row_key: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=r"^[a-z][a-z0-9_.:-]*$",
    )
    depth: int = Field(ge=0, le=20)
    row_kind: Literal["section", "total", "subtotal", "component", "metric"]
    display_order: int = Field(ge=0, le=499)
    values: tuple[TijoriFinancialStatementCell, ...] = Field(
        min_length=1,
        max_length=120,
    )

    @model_validator(mode="after")
    def validate_parent_shape(self) -> Self:
        if self.depth == 0 and self.parent_row_key is not None:
            raise ValueError("Tijori root statement row cannot have a parent")
        if self.depth > 0 and self.parent_row_key is None:
            raise ValueError("Tijori nested statement row requires a parent")
        if self.parent_row_key == self.row_key:
            raise ValueError("Tijori statement row cannot parent itself")
        return self


class TijoriFinancialStatementExtraction(FundamentalModel):
    period_count: int = Field(ge=1, le=120)
    row_count: int = Field(ge=1, le=500)
    cell_count: int = Field(ge=1, le=20_000)
    maximum_depth: int = Field(ge=0, le=20)
    status: Literal["complete"] = "complete"


class TijoriBalanceSheetDocument(FundamentalModel):
    """Complete provider Balance Sheet for one explicit reporting basis."""

    schema_version: Literal["tijori.financial_document.v1"]
    document_type: Literal["balance_sheet"]
    reporting_basis: Literal["consolidated", "standalone"]
    issuer: TijoriFinancialDocumentIssuer
    source: TijoriFinancialDocumentSource
    source_unit: Literal["Rs. Cr."]
    normalized_unit: Literal["INR crore"]
    skipped_report_dates: tuple[str, ...] = Field(max_length=120)
    all_sections_expanded: Literal[True]
    periods: tuple[TijoriFinancialStatementPeriod, ...] = Field(
        min_length=1,
        max_length=120,
    )
    rows: tuple[TijoriFinancialStatementRow, ...] = Field(
        min_length=1,
        max_length=500,
    )
    extraction: TijoriFinancialStatementExtraction

    @field_validator("skipped_report_dates")
    @classmethod
    def validate_skipped_report_dates(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 80 for value in values):
            raise ValueError(
                "Tijori skipped report dates must be non-blank and bounded"
            )
        if len(values) != len(set(values)):
            raise ValueError("Tijori skipped report dates must be unique")
        return values

    @model_validator(mode="after")
    def validate_complete_document(self) -> Self:
        expected_location = (
            "https://www.tijorifinance.com/company/"
            f"{self.issuer.provider_slug}/financials/"
        )
        if self.source.location != expected_location:
            raise ValueError("Tijori Balance Sheet location does not match issuer")

        period_keys = tuple(period.period_key for period in self.periods)
        if len(period_keys) != len(set(period_keys)):
            raise ValueError("Tijori Balance Sheet period keys must be unique")
        if tuple(period.display_order for period in self.periods) != tuple(
            range(len(self.periods))
        ):
            raise ValueError("Tijori Balance Sheet periods must be contiguous")

        row_keys = tuple(row.row_key for row in self.rows)
        if len(row_keys) != len(set(row_keys)):
            raise ValueError("Tijori Balance Sheet row keys must be unique")
        if tuple(row.display_order for row in self.rows) != tuple(
            range(len(self.rows))
        ):
            raise ValueError("Tijori Balance Sheet rows must be contiguous")
        cell_count = len(self.rows) * len(self.periods)
        if cell_count > 20_000:
            raise ValueError("Tijori Balance Sheet exceeds the cell safety bound")

        rows_by_key = {row.row_key: row for row in self.rows}
        for row in self.rows:
            if tuple(cell.period_key for cell in row.values) != period_keys:
                raise ValueError(
                    "Tijori Balance Sheet row must cover periods in order"
                )
            if row.parent_row_key is None:
                continue
            parent = rows_by_key.get(row.parent_row_key)
            if (
                parent is None
                or parent.display_order >= row.display_order
                or parent.depth != row.depth - 1
            ):
                raise ValueError("Tijori Balance Sheet row hierarchy is invalid")

        if (
            self.extraction.period_count != len(self.periods)
            or self.extraction.row_count != len(self.rows)
            or self.extraction.cell_count != cell_count
            or self.extraction.maximum_depth
            != max(row.depth for row in self.rows)
        ):
            raise ValueError(
                "Tijori Balance Sheet extraction counts do not match"
            )
        return self

    @computed_field
    @property
    def document_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"document_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


class TijoriBalanceSheetPayload(FundamentalModel):
    """Successful get_financials payload for one complete Balance Sheet."""

    document: TijoriBalanceSheetDocument


class TijoriCashFlowDocument(TijoriBalanceSheetDocument):
    """Complete provider Cash Flow for one explicit reporting basis."""

    document_type: Literal["cash_flow"]


class TijoriCashFlowPayload(FundamentalModel):
    """Successful get_financials payload for one complete Cash Flow."""

    document: TijoriCashFlowDocument


class TijoriProfitAndLossRow(TijoriFinancialStatementRow):
    """One P&L row with its provider and normalized measurement units."""

    value_kind: Literal["monetary", "percentage", "count"]
    source_unit: Literal["Rs. Cr.", "percent", "crore shares"]
    normalized_unit: Literal["INR crore", "percent", "crore shares"]

    @model_validator(mode="after")
    def validate_value_kind_units(self) -> Self:
        expected_units = {
            "monetary": ("Rs. Cr.", "INR crore"),
            "percentage": ("percent", "percent"),
            "count": ("crore shares", "crore shares"),
        }
        if (
            self.source_unit,
            self.normalized_unit,
        ) != expected_units[self.value_kind]:
            raise ValueError("Tijori P&L row units contradict its value kind")
        return self


class TijoriProfitAndLossDocument(TijoriBalanceSheetDocument):
    """Complete provider P&L statement for one explicit reporting basis."""

    document_type: Literal["profit_and_loss"]
    source_unit: Literal["mixed"]
    normalized_unit: Literal["mixed"]
    rows: tuple[TijoriProfitAndLossRow, ...] = Field(
        min_length=1,
        max_length=500,
    )


class TijoriProfitAndLossPayload(FundamentalModel):
    """Successful get_financials payload for one complete P&L statement."""

    document: TijoriProfitAndLossDocument


class TijoriRatiosRow(TijoriFinancialStatementRow):
    """One ratio-table row with its explicit measurement semantics."""

    value_kind: Literal[
        "monetary",
        "percentage",
        "per_share",
        "ratio",
        "other",
    ]
    source_unit: Literal[
        "Rs. Cr.",
        "percent",
        "per share",
        "ratio",
        "days",
        "not applicable",
    ]
    normalized_unit: Literal[
        "INR crore",
        "percent",
        "per share",
        "ratio",
        "days",
        "not applicable",
    ]

    @model_validator(mode="after")
    def validate_value_kind_units(self) -> Self:
        exact_units = {
            "monetary": ("Rs. Cr.", "INR crore"),
            "percentage": ("percent", "percent"),
            "per_share": ("per share", "per share"),
            "ratio": ("ratio", "ratio"),
        }
        supplied = (self.source_unit, self.normalized_unit)
        if self.value_kind == "other":
            if supplied not in {
                ("days", "days"),
                ("not applicable", "not applicable"),
            }:
                raise ValueError(
                    "Tijori Ratios other-row units are unsupported"
                )
        elif supplied != exact_units[self.value_kind]:
            raise ValueError("Tijori Ratios row units contradict its value kind")
        return self


class TijoriRatiosDocument(TijoriBalanceSheetDocument):
    """Complete provider Ratios table for one reporting basis."""

    document_type: Literal["ratios"]
    source_unit: Literal["mixed"]
    normalized_unit: Literal["mixed"]
    rows: tuple[TijoriRatiosRow, ...] = Field(
        min_length=1,
        max_length=500,
    )


class TijoriRatiosPayload(FundamentalModel):
    """Successful get_financials payload for one complete Ratios table."""

    document: TijoriRatiosDocument


class TijoriQuarterlyResultsRow(TijoriFinancialStatementRow):
    """One quarterly-result row with explicit measurement semantics."""

    value_kind: Literal["monetary", "percentage", "per_share", "other"]
    source_unit: Literal[
        "Rs. Cr.",
        "percent",
        "per share",
        "not applicable",
    ]
    normalized_unit: Literal[
        "INR crore",
        "percent",
        "per share",
        "not applicable",
    ]

    @model_validator(mode="after")
    def validate_value_kind_units(self) -> Self:
        expected_units = {
            "monetary": ("Rs. Cr.", "INR crore"),
            "percentage": ("percent", "percent"),
            "per_share": ("per share", "per share"),
            "other": ("not applicable", "not applicable"),
        }
        if (
            self.source_unit,
            self.normalized_unit,
        ) != expected_units[self.value_kind]:
            raise ValueError(
                "Tijori Quarterly Results row units contradict its value kind"
            )
        return self


class TijoriQuarterlyResultsDocument(TijoriBalanceSheetDocument):
    """Complete provider Quarterly Results for one reporting basis."""

    document_type: Literal["quarterly_results"]
    source_unit: Literal["mixed"]
    normalized_unit: Literal["mixed"]
    rows: tuple[TijoriQuarterlyResultsRow, ...] = Field(
        min_length=1,
        max_length=500,
    )


class TijoriQuarterlyResultsPayload(FundamentalModel):
    """Successful get_financials payload for complete Quarterly Results."""

    document: TijoriQuarterlyResultsDocument


class TijoriPeerComparisonMetric(FundamentalModel):
    """One explicitly labelled metric in a peer-comparison matrix."""

    metric_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    standardized_label: str = Field(min_length=1, max_length=120)
    value_kind: Literal["monetary", "ratio", "percentage"]
    source_unit: Literal["INR", "INR crore", "ratio", "percent"]
    source_label: str = Field(min_length=1, max_length=120)
    display_order: int = Field(ge=0, le=29)

    @model_validator(mode="after")
    def validate_metric_units(self) -> Self:
        allowed_units = {
            "monetary": {"INR", "INR crore"},
            "ratio": {"ratio"},
            "percentage": {"percent"},
        }
        if self.source_unit not in allowed_units[self.value_kind]:
            raise ValueError(
                "Tijori Peer Comparison metric units contradict value kind"
            )
        return self


class TijoriPeerComparisonCell(FundamentalModel):
    """One peer metric value with missing values distinct from numeric zero."""

    metric_key: str = Field(
        min_length=1,
        max_length=80,
        pattern=r"^[a-z][a-z0-9_]*$",
    )
    source_value: str | None = Field(default=None, min_length=1, max_length=120)
    availability_status: Literal["available", "unknown"]

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if (self.availability_status == "available") != (
            self.source_value is not None
        ):
            raise ValueError(
                "Tijori Peer Comparison availability contradicts value"
            )
        return self


class TijoriPeerComparisonCompany(FundamentalModel):
    """One provider-labelled subject or peer in display order."""

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
    values: tuple[TijoriPeerComparisonCell, ...] = Field(
        min_length=1,
        max_length=30,
    )

    @model_validator(mode="after")
    def validate_provider_key(self) -> Self:
        expected = f"peer_{self.provider_slug.replace('-', '_')}"
        if self.peer_key != expected:
            raise ValueError(
                "Tijori Peer Comparison key does not match provider slug"
            )
        return self


class TijoriPeerComparisonExtraction(FundamentalModel):
    metric_count: int = Field(ge=1, le=30)
    peer_count: int = Field(ge=1, le=50)
    cell_count: int = Field(ge=1, le=1_500)
    status: Literal["complete"] = "complete"


class TijoriPeerComparisonDocument(FundamentalModel):
    """Complete cross-sectional Peer Comparison provider document."""

    schema_version: Literal["tijori.peer_comparison.v1"]
    document_type: Literal["peer_comparison"]
    issuer: TijoriFinancialDocumentIssuer
    source: TijoriFinancialDocumentSource
    observation_date: date
    metrics: tuple[TijoriPeerComparisonMetric, ...] = Field(
        min_length=1,
        max_length=30,
    )
    peers: tuple[TijoriPeerComparisonCompany, ...] = Field(
        min_length=1,
        max_length=50,
    )
    extraction: TijoriPeerComparisonExtraction

    @model_validator(mode="after")
    def validate_complete_document(self) -> Self:
        expected_location = (
            "https://www.tijorifinance.com/company/"
            f"{self.issuer.provider_slug}/"
        )
        if self.source.location != expected_location:
            raise ValueError(
                "Tijori Peer Comparison location does not match issuer"
            )
        if self.issuer.provider_company_id is None:
            raise ValueError(
                "Tijori Peer Comparison requires a provider company ID"
            )

        metric_keys = tuple(metric.metric_key for metric in self.metrics)
        if len(metric_keys) != len(set(metric_keys)):
            raise ValueError("Tijori Peer Comparison metric keys must be unique")
        if tuple(metric.display_order for metric in self.metrics) != tuple(
            range(len(self.metrics))
        ):
            raise ValueError(
                "Tijori Peer Comparison metrics must be contiguous"
            )

        peer_keys = tuple(peer.peer_key for peer in self.peers)
        peer_slugs = tuple(peer.provider_slug for peer in self.peers)
        if (
            len(peer_keys) != len(set(peer_keys))
            or len(peer_slugs) != len(set(peer_slugs))
        ):
            raise ValueError("Tijori Peer Comparison peers must be unique")
        if tuple(peer.display_order for peer in self.peers) != tuple(
            range(len(self.peers))
        ):
            raise ValueError("Tijori Peer Comparison peers must be contiguous")
        if sum(peer.is_subject for peer in self.peers) != 1:
            raise ValueError(
                "Tijori Peer Comparison must identify one subject"
            )
        subject = next(peer for peer in self.peers if peer.is_subject)
        if subject.provider_slug != self.issuer.provider_slug:
            raise ValueError(
                "Tijori Peer Comparison subject does not match issuer"
            )
        if any(
            tuple(cell.metric_key for cell in peer.values) != metric_keys
            for peer in self.peers
        ):
            raise ValueError(
                "Tijori Peer Comparison rows must cover metrics in order"
            )

        expected_cells = len(self.metrics) * len(self.peers)
        if (
            self.extraction.metric_count != len(self.metrics)
            or self.extraction.peer_count != len(self.peers)
            or self.extraction.cell_count != expected_cells
        ):
            raise ValueError(
                "Tijori Peer Comparison extraction counts do not match"
            )
        return self

    @computed_field
    @property
    def document_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"document_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


class TijoriPeerComparisonPayload(FundamentalModel):
    """Successful get_company_overview Peer Comparison payload."""

    document: TijoriPeerComparisonDocument


class TijoriBenchmarkingCompany(FundamentalModel):
    """One subject or peer column in the Benchmarking matrix."""

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
    def validate_provider_key(self) -> Self:
        expected = f"company_{self.provider_slug.replace('-', '_')}"
        if self.company_key != expected:
            raise ValueError(
                "Tijori Benchmarking company key does not match provider slug"
            )
        return self


class TijoriBenchmarkingCell(FundamentalModel):
    """One company value with provider best-value annotation."""

    company_key: str = Field(
        min_length=1,
        max_length=250,
        pattern=r"^company_[a-z0-9_]+$",
    )
    source_value: str | None = Field(default=None, min_length=1, max_length=160)
    availability_status: Literal["available", "unknown"]
    is_best: bool

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if (self.availability_status == "available") != (
            self.source_value is not None
        ):
            raise ValueError(
                "Tijori Benchmarking availability contradicts value"
            )
        return self


class TijoriBenchmarkingRow(FundamentalModel):
    """One hierarchical metric or expandable section from the provider table."""

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
    provider_section: Literal[
        "bch_op_metric",
        "bch_financial",
        "bch_shareholdings",
    ]
    provider_hidden: bool
    display_order: int = Field(ge=0, le=299)
    values: tuple[TijoriBenchmarkingCell, ...] = Field(
        min_length=2,
        max_length=30,
    )

    @model_validator(mode="after")
    def validate_parent_shape(self) -> Self:
        if self.depth == 0 and self.parent_row_key is not None:
            raise ValueError("Tijori Benchmarking root row cannot have a parent")
        if self.depth > 0 and self.parent_row_key is None:
            raise ValueError("Tijori Benchmarking nested row requires a parent")
        if self.parent_row_key == self.row_key:
            raise ValueError("Tijori Benchmarking row cannot parent itself")
        return self


class TijoriBenchmarkingExtraction(FundamentalModel):
    company_count: int = Field(ge=2, le=30)
    row_count: int = Field(ge=1, le=300)
    cell_count: int = Field(ge=2, le=9_000)
    maximum_depth: int = Field(ge=0, le=10)
    provider_hidden_row_count: int = Field(ge=0, le=300)
    status: Literal["complete"] = "complete"


class TijoriBenchmarkingFinancialsDocument(FundamentalModel):
    """Complete hierarchical Financial subsection of Benchmarking."""

    schema_version: Literal["tijori.benchmarking_financials.v1"]
    document_type: Literal["benchmarking_financials"]
    reporting_basis: Literal["not_applicable"]
    issuer: TijoriFinancialDocumentIssuer
    source: TijoriFinancialDocumentSource
    observation_date: date
    all_rows_captured: Literal[True]
    companies: tuple[TijoriBenchmarkingCompany, ...] = Field(
        min_length=2,
        max_length=30,
    )
    rows: tuple[TijoriBenchmarkingRow, ...] = Field(
        min_length=1,
        max_length=300,
    )
    extraction: TijoriBenchmarkingExtraction

    @model_validator(mode="after")
    def validate_complete_document(self) -> Self:
        expected_location = (
            "https://www.tijorifinance.com/company/"
            f"{self.issuer.provider_slug}/benchmarking/"
        )
        if self.source.location != expected_location:
            raise ValueError(
                "Tijori Benchmarking location does not match issuer"
            )
        if self.issuer.provider_company_id is None:
            raise ValueError(
                "Tijori Benchmarking requires a provider company ID"
            )

        company_keys = tuple(company.company_key for company in self.companies)
        company_slugs = tuple(
            company.provider_slug for company in self.companies
        )
        if (
            len(company_keys) != len(set(company_keys))
            or len(company_slugs) != len(set(company_slugs))
        ):
            raise ValueError("Tijori Benchmarking companies must be unique")
        if tuple(company.display_order for company in self.companies) != tuple(
            range(len(self.companies))
        ):
            raise ValueError("Tijori Benchmarking companies must be contiguous")
        if sum(company.is_subject for company in self.companies) != 1:
            raise ValueError("Tijori Benchmarking must identify one subject")
        subject = next(
            company for company in self.companies if company.is_subject
        )
        if subject.provider_slug != self.issuer.provider_slug:
            raise ValueError("Tijori Benchmarking subject does not match issuer")

        row_keys: set[str] = set()
        row_depths: dict[str, int] = {}
        for expected_order, row in enumerate(self.rows):
            if row.row_key in row_keys:
                raise ValueError("Tijori Benchmarking row keys must be unique")
            if row.display_order != expected_order:
                raise ValueError("Tijori Benchmarking rows must be contiguous")
            if row.parent_row_key is not None:
                parent_depth = row_depths.get(row.parent_row_key)
                if parent_depth is None or parent_depth + 1 != row.depth:
                    raise ValueError(
                        "Tijori Benchmarking row hierarchy is inconsistent"
                    )
            if tuple(cell.company_key for cell in row.values) != company_keys:
                raise ValueError(
                    "Tijori Benchmarking rows must cover companies in order"
                )
            row_keys.add(row.row_key)
            row_depths[row.row_key] = row.depth

        expected_cells = len(self.companies) * len(self.rows)
        expected_depth = max(row.depth for row in self.rows)
        expected_hidden = sum(row.provider_hidden for row in self.rows)
        if (
            self.extraction.company_count != len(self.companies)
            or self.extraction.row_count != len(self.rows)
            or self.extraction.cell_count != expected_cells
            or self.extraction.maximum_depth != expected_depth
            or self.extraction.provider_hidden_row_count != expected_hidden
        ):
            raise ValueError(
                "Tijori Benchmarking extraction counts do not match"
            )
        return self

    @computed_field
    @property
    def document_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"document_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


class TijoriBenchmarkingFinancialsPayload(FundamentalModel):
    """Successful get_company_overview Benchmarking Financials payload."""

    document: TijoriBenchmarkingFinancialsDocument


def _canonical_payload(payload: dict[str, Any]) -> bytes:
    node_count = [0]
    _validate_json_value(payload, depth=0, node_count=node_count)
    try:
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("Tijori payload must be canonical JSON") from exc
    if len(encoded) > _MAX_PAYLOAD_BYTES:
        raise ValueError("Tijori payload exceeds the offline safety limit")
    return encoded


def _validate_json_value(
    value: Any,
    *,
    depth: int,
    node_count: list[int],
) -> None:
    node_count[0] += 1
    if node_count[0] > _MAX_JSON_NODES or depth > _MAX_JSON_DEPTH:
        raise ValueError("Tijori payload structure exceeds safety limits")
    if value is None or isinstance(value, (bool, int)):
        return
    if isinstance(value, float):
        if not isfinite(value):
            raise ValueError("Tijori payload contains non-finite numbers")
        return
    if isinstance(value, str):
        if len(value) > 10_000:
            raise ValueError("Tijori payload string exceeds safety limit")
        return
    if isinstance(value, list):
        for item in value:
            _validate_json_value(
                item,
                depth=depth + 1,
                node_count=node_count,
            )
        return
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str) or not key or len(key) > 160:
                raise ValueError("Tijori payload keys must be bounded strings")
            _validate_json_value(
                item,
                depth=depth + 1,
                node_count=node_count,
            )
        return
    raise ValueError("Tijori payload contains a non-JSON value")
