"""Provider-neutral read boundary for listed-company fundamental evidence.

The gateway is deliberately capability-oriented rather than MCP-tool-oriented.
An adapter may use a local subprocess, provider SDK, uploaded export, or another
transport, but callers receive only validated identities and evidence models.
Raw provider payloads, credentials, cookies, and transport handles never cross
this contract.
"""

from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
from re import fullmatch
from typing import Literal, Protocol, Self, runtime_checkable

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.financial_documents import (
    FinancialDocumentType,
    FinancialReportingBasis,
    StructuredBenchmarkingFinancialsDocument,
    StructuredFinancialDocument,
    StructuredPeerComparisonDocument,
)
from app.models.fundamentals import (
    FundamentalEvidenceSnapshot,
    FundamentalIssuerIdentity,
    FundamentalModel,
    FundamentalPeriodType,
    FundamentalStatement,
    FundamentalValidationStatus,
    ProviderConnectionScope,
)


_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_ISIN_PATTERN = r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$"


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


def _require_unique_strings(
    values: tuple[str, ...],
    field_name: str,
) -> tuple[str, ...]:
    if any(not value for value in values):
        raise ValueError(f"{field_name} cannot contain blanks")
    folded = tuple(value.casefold() for value in values)
    if len(folded) != len(set(folded)):
        raise ValueError(f"{field_name} must be unique")
    return values


class FundamentalCapability(StrEnum):
    """Initial provider-neutral, read-only fundamental capability surface."""

    COMPANY_SEARCH = "company_search"
    ISSUER_RESOLUTION = "issuer_resolution"
    COMPANY_OVERVIEW = "company_overview"
    FINANCIAL_STATEMENTS = "financial_statements"
    SHAREHOLDING_HISTORY = "shareholding_history"


class FundamentalCapabilityStatus(StrEnum):
    AVAILABLE = "available"
    NOT_ENTITLED = "not_entitled"
    UNAVAILABLE = "unavailable"
    UNKNOWN = "unknown"


class FundamentalIssuerMatchKind(StrEnum):
    EXACT_SYMBOL = "exact_symbol"
    EXACT_ISIN = "exact_isin"
    EXACT_LEGAL_NAME = "exact_legal_name"
    NORMALIZED_NAME = "normalized_name"
    PROVIDER_ID = "provider_id"
    PROVIDER_SLUG = "provider_slug"
    FUZZY_NAME = "fuzzy_name"


class FundamentalResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"


class FundamentalRetrievalStatus(StrEnum):
    COMPLETED = "completed"
    PARTIAL = "partial"
    NOT_FOUND = "not_found"
    NOT_ENTITLED = "not_entitled"
    PAYWALLED = "paywalled"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    VALIDATION_REJECTED = "validation_rejected"


class FundamentalCapabilityDescriptor(FundamentalModel):
    """Run-specific availability of one safe provider capability."""

    capability: FundamentalCapability
    status: FundamentalCapabilityStatus
    read_only: Literal[True] = True
    idempotent: Literal[True] = True
    supports_history: bool
    max_periods: int | None = Field(default=None, ge=1, le=120)
    notes: tuple[str, ...] = ()

    @field_validator("notes")
    @classmethod
    def validate_notes(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("capability notes must be non-blank and bounded")
        return _require_unique_strings(values, "capability notes")

    @model_validator(mode="after")
    def validate_history_contract(self) -> Self:
        if not self.supports_history and self.max_periods is not None:
            raise ValueError(
                "non-historical capability cannot advertise max periods"
            )
        return self


class FundamentalCapabilityManifest(FundamentalModel):
    """Observed capability state; configuration alone is not readiness."""

    schema_version: Literal["jarvis.fundamental_capabilities.v1"] = (
        "jarvis.fundamental_capabilities.v1"
    )
    connection: ProviderConnectionScope
    capabilities: tuple[FundamentalCapabilityDescriptor, ...] = Field(
        min_length=1,
        max_length=len(FundamentalCapability),
    )
    checked_at: datetime
    provider_contract_version: str = Field(
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )
    adapter_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @field_validator("checked_at")
    @classmethod
    def validate_checked_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, "capability check time")

    @model_validator(mode="after")
    def validate_manifest(self) -> Self:
        capability_ids = tuple(
            descriptor.capability for descriptor in self.capabilities
        )
        if len(capability_ids) != len(set(capability_ids)):
            raise ValueError("capability manifest entries must be unique")
        if (
            self.connection.entitlement_checked_at is not None
            and self.checked_at < self.connection.entitlement_checked_at
        ):
            raise ValueError(
                "capability check cannot predate entitlement verification"
            )
        return self

    @computed_field
    @property
    def manifest_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"manifest_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


class FundamentalGatewayRequest(FundamentalModel):
    """Shared tenant-isolated request metadata for every gateway call."""

    schema_version: Literal["jarvis.fundamental_gateway_request.v1"] = (
        "jarvis.fundamental_gateway_request.v1"
    )
    capability: FundamentalCapability
    request_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_ID_PATTERN,
    )
    operation_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=160,
        pattern=_ID_PATTERN,
    )
    connection: ProviderConnectionScope
    requested_at: datetime

    @field_validator("requested_at")
    @classmethod
    def validate_requested_at(cls, value: datetime) -> datetime:
        return _require_timezone(value, "gateway request time")

    @computed_field
    @property
    def request_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"request_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


class FundamentalCompanySearchRequest(FundamentalGatewayRequest):
    capability: Literal[FundamentalCapability.COMPANY_SEARCH] = (
        FundamentalCapability.COMPANY_SEARCH
    )
    query: str = Field(min_length=1, max_length=200)
    exchanges: tuple[str, ...] = Field(default=(), max_length=20)
    max_results: int = Field(default=10, ge=1, le=25)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        normalized = " ".join(value.split())
        if not normalized:
            raise ValueError("company search query must not be blank")
        return normalized

    @field_validator("exchanges", mode="before")
    @classmethod
    def normalize_exchanges(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(value.upper() for value in values)

    @field_validator("exchanges")
    @classmethod
    def validate_exchanges(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(
            len(value) > 32
            or fullmatch(r"^[A-Z0-9_.:-]+$", value) is None
            for value in values
        ):
            raise ValueError("exchange filters must be bounded identifiers")
        return _require_unique_strings(values, "exchange filters")


class FundamentalIssuerLocator(FundamentalModel):
    """Provider-neutral identity hints; at least one must be supplied."""

    exchange: str | None = Field(default=None, min_length=1, max_length=32)
    symbol: str | None = Field(default=None, min_length=1, max_length=100)
    legal_name: str | None = Field(default=None, min_length=1, max_length=300)
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

    @model_validator(mode="after")
    def require_identity_hint(self) -> Self:
        if not any(
            (
                self.symbol,
                self.legal_name,
                self.isin,
                self.provider_company_id,
                self.provider_slug,
            )
        ):
            raise ValueError("issuer locator requires an identity hint")
        if self.exchange is not None and self.symbol is None:
            raise ValueError("exchange hint requires a symbol")
        return self


class FundamentalIssuerResolutionRequest(FundamentalGatewayRequest):
    capability: Literal[FundamentalCapability.ISSUER_RESOLUTION] = (
        FundamentalCapability.ISSUER_RESOLUTION
    )
    locator: FundamentalIssuerLocator
    max_candidates: int = Field(default=10, ge=2, le=25)


class FundamentalIssuerEvidenceRequest(FundamentalGatewayRequest):
    issuer: FundamentalIssuerIdentity
    as_of_date: date | None = None

    @model_validator(mode="after")
    def validate_as_of_date(self) -> Self:
        if (
            self.as_of_date is not None
            and self.as_of_date > self.requested_at.date()
        ):
            raise ValueError("requested as-of date cannot be in the future")
        return self


class FundamentalCompanyOverviewRequest(FundamentalIssuerEvidenceRequest):
    capability: Literal[FundamentalCapability.COMPANY_OVERVIEW] = (
        FundamentalCapability.COMPANY_OVERVIEW
    )


_FINANCIAL_STATEMENTS = {
    FundamentalStatement.INCOME_STATEMENT,
    FundamentalStatement.BALANCE_SHEET,
    FundamentalStatement.CASH_FLOW,
    FundamentalStatement.KPI_SCHEDULE,
    FundamentalStatement.SEGMENT,
    FundamentalStatement.EQUITY_RISK_DEBT_LIQUIDITY_CONTEXT,
    FundamentalStatement.SHARE_COUNT,
    FundamentalStatement.WORKING_CAPITAL,
    FundamentalStatement.CAPITAL_ALLOCATION,
    FundamentalStatement.ADJUSTMENT,
}
_REPORTED_PERIOD_TYPES = {
    FundamentalPeriodType.ANNUAL,
    FundamentalPeriodType.QUARTERLY,
    FundamentalPeriodType.MONTHLY,
    FundamentalPeriodType.YTD,
    FundamentalPeriodType.LTM,
}


class FundamentalFinancialsRequest(FundamentalIssuerEvidenceRequest):
    capability: Literal[FundamentalCapability.FINANCIAL_STATEMENTS] = (
        FundamentalCapability.FINANCIAL_STATEMENTS
    )
    statements: tuple[FundamentalStatement, ...] = (
        FundamentalStatement.INCOME_STATEMENT,
        FundamentalStatement.BALANCE_SHEET,
        FundamentalStatement.CASH_FLOW,
    )
    period_types: tuple[FundamentalPeriodType, ...] = (
        FundamentalPeriodType.ANNUAL,
        FundamentalPeriodType.QUARTERLY,
    )
    max_periods: int = Field(default=12, ge=1, le=40)

    @field_validator("statements")
    @classmethod
    def validate_statements(
        cls,
        values: tuple[FundamentalStatement, ...],
    ) -> tuple[FundamentalStatement, ...]:
        if not values:
            raise ValueError("financial request requires at least one statement")
        if len(values) != len(set(values)):
            raise ValueError("financial statements must be unique")
        if not set(values).issubset(_FINANCIAL_STATEMENTS):
            raise ValueError("financial request contains an invalid statement")
        return values

    @field_validator("period_types")
    @classmethod
    def validate_period_types(
        cls,
        values: tuple[FundamentalPeriodType, ...],
    ) -> tuple[FundamentalPeriodType, ...]:
        if not values:
            raise ValueError("financial request requires a period type")
        if len(values) != len(set(values)):
            raise ValueError("financial period types must be unique")
        if not set(values).issubset(_REPORTED_PERIOD_TYPES):
            raise ValueError(
                "financial retrieval accepts only reported-period types"
            )
        return values


class FundamentalStructuredDocumentRequest(FundamentalIssuerEvidenceRequest):
    """Request one complete provider-neutral financial document scenario."""

    capability: Literal[FundamentalCapability.FINANCIAL_STATEMENTS] = (
        FundamentalCapability.FINANCIAL_STATEMENTS
    )
    document_type: FinancialDocumentType
    reporting_basis: FinancialReportingBasis


class FundamentalShareholdingRequest(FundamentalIssuerEvidenceRequest):
    capability: Literal[FundamentalCapability.SHAREHOLDING_HISTORY] = (
        FundamentalCapability.SHAREHOLDING_HISTORY
    )
    quarters: int = Field(default=8, ge=1, le=40)
    include_promoter_pledge: bool = True


class FundamentalIssuerCandidate(FundamentalModel):
    """Bounded provider search match; not itself financial evidence."""

    issuer: FundamentalIssuerIdentity
    match_kind: FundamentalIssuerMatchKind
    match_score: Decimal = Field(ge=Decimal("0"), le=Decimal("1"))
    matched_on: tuple[str, ...] = Field(min_length=1, max_length=10)
    provider_record_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @field_validator("match_score")
    @classmethod
    def validate_score(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("issuer match score must be finite")
        return value

    @field_validator("matched_on")
    @classmethod
    def validate_matched_on(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(len(value) > 120 for value in values):
            raise ValueError("issuer match fields must be bounded")
        return _require_unique_strings(values, "issuer match fields")


class FundamentalGatewayResponse(FundamentalModel):
    """Shared secret-free execution metadata returned by every adapter."""

    schema_version: Literal["jarvis.fundamental_gateway_response.v1"] = (
        "jarvis.fundamental_gateway_response.v1"
    )
    capability: FundamentalCapability
    request_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_ID_PATTERN,
    )
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    connection: ProviderConnectionScope
    requested_at: datetime
    started_at: datetime
    completed_at: datetime
    provider_contract_version: str = Field(
        min_length=1,
        max_length=80,
        pattern=_ID_PATTERN,
    )
    adapter_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)

    @field_validator("requested_at", "started_at", "completed_at")
    @classmethod
    def validate_response_time(cls, value: datetime) -> datetime:
        return _require_timezone(value, "gateway response time")

    @model_validator(mode="after")
    def validate_response_order(self) -> Self:
        if self.started_at < self.requested_at:
            raise ValueError("gateway execution cannot predate its request")
        if self.completed_at < self.started_at:
            raise ValueError("gateway completion cannot predate its start")
        return self


class FundamentalCompanySearchResult(FundamentalGatewayResponse):
    capability: Literal[FundamentalCapability.COMPANY_SEARCH] = (
        FundamentalCapability.COMPANY_SEARCH
    )
    candidates: tuple[FundamentalIssuerCandidate, ...] = Field(max_length=25)

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        identities = tuple(
            (
                candidate.issuer.exchange,
                candidate.issuer.symbol,
                candidate.issuer.isin,
                candidate.issuer.provider_company_id,
                candidate.issuer.provider_slug,
            )
            for candidate in self.candidates
        )
        if len(identities) != len(set(identities)):
            raise ValueError("company search candidates must be unique")
        return self

    @computed_field
    @property
    def result_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"result_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


class FundamentalIssuerResolutionResult(FundamentalGatewayResponse):
    capability: Literal[FundamentalCapability.ISSUER_RESOLUTION] = (
        FundamentalCapability.ISSUER_RESOLUTION
    )
    status: FundamentalResolutionStatus
    issuer: FundamentalIssuerIdentity | None = None
    candidates: tuple[FundamentalIssuerCandidate, ...] = Field(max_length=25)
    limitation: str | None = Field(default=None, min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_resolution(self) -> Self:
        identities = tuple(
            (
                candidate.issuer.exchange,
                candidate.issuer.symbol,
                candidate.issuer.isin,
                candidate.issuer.provider_company_id,
                candidate.issuer.provider_slug,
            )
            for candidate in self.candidates
        )
        if len(identities) != len(set(identities)):
            raise ValueError("issuer resolution candidates must be unique")

        if self.status is FundamentalResolutionStatus.RESOLVED:
            if self.issuer is None:
                raise ValueError("resolved identity requires an issuer")
            if self.candidates and all(
                candidate.issuer != self.issuer
                for candidate in self.candidates
            ):
                raise ValueError(
                    "resolved issuer must be present in returned candidates"
                )
        elif self.issuer is not None:
            raise ValueError("unresolved identity cannot select an issuer")

        if self.status is FundamentalResolutionStatus.AMBIGUOUS:
            if len(self.candidates) < 2 or self.limitation is None:
                raise ValueError(
                    "ambiguous identity requires candidates and explanation"
                )
        elif self.status is FundamentalResolutionStatus.NOT_FOUND:
            if self.candidates or self.limitation is None:
                raise ValueError(
                    "not-found identity requires an empty explained result"
                )
        return self

    @computed_field
    @property
    def result_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"result_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


_OVERVIEW_RESULT_STATEMENTS = {
    FundamentalStatement.COMPANY_PROFILE,
    FundamentalStatement.VALUATION,
    FundamentalStatement.KPI_SCHEDULE,
    FundamentalStatement.EQUITY_RISK_DEBT_LIQUIDITY_CONTEXT,
}
_SHAREHOLDING_RESULT_STATEMENTS = {
    FundamentalStatement.OWNERSHIP,
    FundamentalStatement.SHARE_COUNT,
}


class FundamentalEvidenceRetrieval(FundamentalGatewayResponse):
    """Fail-closed retrieval result containing only validated evidence."""

    capability: FundamentalCapability
    issuer: FundamentalIssuerIdentity
    status: FundamentalRetrievalStatus
    snapshot: FundamentalEvidenceSnapshot | None = None
    limitations: tuple[str, ...] = ()

    @field_validator("limitations")
    @classmethod
    def validate_limitations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("retrieval limitations must be non-blank and bounded")
        return _require_unique_strings(values, "retrieval limitations")

    @model_validator(mode="after")
    def validate_retrieval(self) -> Self:
        evidence_capabilities = {
            FundamentalCapability.COMPANY_OVERVIEW,
            FundamentalCapability.FINANCIAL_STATEMENTS,
            FundamentalCapability.SHAREHOLDING_HISTORY,
        }
        if self.capability not in evidence_capabilities:
            raise ValueError("retrieval result requires an evidence capability")

        successful = self.status in {
            FundamentalRetrievalStatus.COMPLETED,
            FundamentalRetrievalStatus.PARTIAL,
        }
        if successful and self.snapshot is None:
            raise ValueError("successful retrieval requires an evidence snapshot")
        if not successful and self.snapshot is not None:
            raise ValueError("failed retrieval cannot release an evidence snapshot")
        if (
            self.status is FundamentalRetrievalStatus.PARTIAL
            and not self.limitations
        ):
            raise ValueError("partial retrieval requires explicit limitations")
        if not successful and not self.limitations:
            raise ValueError("failed retrieval requires a safe explanation")

        if self.snapshot is not None:
            if self.snapshot.connection != self.connection:
                raise ValueError(
                    "retrieval and snapshot connection scopes must match"
                )
            if self.snapshot.issuer != self.issuer:
                raise ValueError("retrieval and snapshot issuers must match")
            if self.completed_at < self.snapshot.assembled_at:
                raise ValueError("retrieval cannot complete before assembly")
            if (
                self.status is FundamentalRetrievalStatus.COMPLETED
                and self.snapshot.validation_status
                is not FundamentalValidationStatus.VALIDATED
            ):
                raise ValueError(
                    "completed retrieval requires a validated snapshot"
                )
            if (
                self.status is FundamentalRetrievalStatus.PARTIAL
                and self.snapshot.validation_status
                not in {
                    FundamentalValidationStatus.PARTIAL,
                    FundamentalValidationStatus.VALIDATED,
                }
            ):
                raise ValueError(
                    "partial retrieval cannot release rejected evidence"
                )
            self._validate_statement_scope()
        return self

    def _validate_statement_scope(self) -> None:
        if self.snapshot is None:
            return
        statements = {fact.statement for fact in self.snapshot.facts}
        if self.capability is FundamentalCapability.COMPANY_OVERVIEW:
            allowed = _OVERVIEW_RESULT_STATEMENTS
        elif self.capability is FundamentalCapability.FINANCIAL_STATEMENTS:
            allowed = _FINANCIAL_STATEMENTS
        else:
            allowed = _SHAREHOLDING_RESULT_STATEMENTS
        if not statements.issubset(allowed):
            raise ValueError(
                "retrieval snapshot contains facts outside its capability"
            )

    @computed_field
    @property
    def result_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"result_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


class FundamentalStructuredDocumentResult(FundamentalGatewayResponse):
    """All-or-nothing retrieval result for one structured document."""

    capability: Literal[FundamentalCapability.FINANCIAL_STATEMENTS] = (
        FundamentalCapability.FINANCIAL_STATEMENTS
    )
    issuer: FundamentalIssuerIdentity
    document_type: FinancialDocumentType
    reporting_basis: FinancialReportingBasis
    status: FundamentalRetrievalStatus
    document: StructuredFinancialDocument | None = None
    limitations: tuple[str, ...] = ()

    @field_validator("limitations")
    @classmethod
    def validate_limitations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError("document limitations must be non-blank and bounded")
        return _require_unique_strings(values, "document limitations")

    @model_validator(mode="after")
    def validate_document_result(self) -> Self:
        completed = self.status is FundamentalRetrievalStatus.COMPLETED
        if completed and self.document is None:
            raise ValueError("completed document retrieval requires a document")
        if completed and self.limitations:
            raise ValueError("completed document retrieval cannot be partial")
        if not completed and self.document is not None:
            raise ValueError("failed document retrieval cannot release a document")
        if not completed and not self.limitations:
            raise ValueError("failed document retrieval requires an explanation")
        if self.document is not None:
            if self.document.connection != self.connection:
                raise ValueError("document and result connection scopes must match")
            if self.document.issuer != self.issuer:
                raise ValueError("document and result issuers must match")
            if self.document.document_type is not self.document_type:
                raise ValueError("document and result types must match")
            if self.document.reporting_basis is not self.reporting_basis:
                raise ValueError("document and result reporting bases must match")
            if self.document.retrieved_at > self.completed_at:
                raise ValueError("document cannot be retrieved after completion")
        return self

    @computed_field
    @property
    def result_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"result_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


class FundamentalPeerComparisonResult(FundamentalGatewayResponse):
    """All-or-nothing provider-neutral Peer Comparison result."""

    capability: Literal[FundamentalCapability.COMPANY_OVERVIEW] = (
        FundamentalCapability.COMPANY_OVERVIEW
    )
    issuer: FundamentalIssuerIdentity
    status: FundamentalRetrievalStatus
    document: StructuredPeerComparisonDocument | None = None
    limitations: tuple[str, ...] = ()

    @field_validator("limitations")
    @classmethod
    def validate_limitations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError(
                "Peer Comparison limitations must be non-blank and bounded"
            )
        return _require_unique_strings(values, "Peer Comparison limitations")

    @model_validator(mode="after")
    def validate_peer_comparison_result(self) -> Self:
        completed = self.status is FundamentalRetrievalStatus.COMPLETED
        if completed and self.document is None:
            raise ValueError(
                "completed Peer Comparison requires a document"
            )
        if completed and self.limitations:
            raise ValueError(
                "completed Peer Comparison cannot contain limitations"
            )
        if not completed and self.document is not None:
            raise ValueError(
                "failed Peer Comparison cannot release a document"
            )
        if not completed and not self.limitations:
            raise ValueError(
                "failed Peer Comparison requires an explanation"
            )
        if self.document is not None:
            if self.document.connection != self.connection:
                raise ValueError(
                    "Peer Comparison connection scopes must match"
                )
            if self.document.issuer != self.issuer:
                raise ValueError("Peer Comparison issuers must match")
            if self.document.retrieved_at > self.completed_at:
                raise ValueError(
                    "Peer Comparison cannot be retrieved after completion"
                )
        return self

    @computed_field
    @property
    def result_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"result_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


class FundamentalBenchmarkingFinancialsResult(FundamentalGatewayResponse):
    """All-or-nothing provider-neutral Benchmarking Financials result."""

    capability: Literal[FundamentalCapability.COMPANY_OVERVIEW] = (
        FundamentalCapability.COMPANY_OVERVIEW
    )
    issuer: FundamentalIssuerIdentity
    status: FundamentalRetrievalStatus
    document: StructuredBenchmarkingFinancialsDocument | None = None
    limitations: tuple[str, ...] = ()

    @field_validator("limitations")
    @classmethod
    def validate_limitations(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any(not value or len(value) > 500 for value in values):
            raise ValueError(
                "Benchmarking Financials limitations must be non-blank and bounded"
            )
        return _require_unique_strings(
            values,
            "Benchmarking Financials limitations",
        )

    @model_validator(mode="after")
    def validate_benchmarking_financials_result(self) -> Self:
        completed = self.status is FundamentalRetrievalStatus.COMPLETED
        if completed and self.document is None:
            raise ValueError(
                "completed Benchmarking Financials requires a document"
            )
        if completed and self.limitations:
            raise ValueError(
                "completed Benchmarking Financials cannot contain limitations"
            )
        if not completed and self.document is not None:
            raise ValueError(
                "failed Benchmarking Financials cannot release a document"
            )
        if not completed and not self.limitations:
            raise ValueError(
                "failed Benchmarking Financials requires an explanation"
            )
        if self.document is not None:
            if self.document.connection != self.connection:
                raise ValueError(
                    "Benchmarking Financials connection scopes must match"
                )
            if self.document.issuer != self.issuer:
                raise ValueError("Benchmarking Financials issuers must match")
            if self.document.retrieved_at > self.completed_at:
                raise ValueError(
                    "Benchmarking Financials cannot be retrieved after completion"
                )
        return self

    @computed_field
    @property
    def result_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"result_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()


def validate_fundamental_response_binding(
    request: FundamentalGatewayRequest,
    response: FundamentalGatewayResponse,
) -> None:
    """Fail closed if an adapter response belongs to another request scope."""

    if response.request_id != request.request_id:
        raise ValueError("fundamental response request ID does not match")
    if response.request_fingerprint != request.request_fingerprint:
        raise ValueError("fundamental response fingerprint does not match")
    if response.connection != request.connection:
        raise ValueError("fundamental response connection scope does not match")
    if response.capability is not request.capability:
        raise ValueError("fundamental response capability does not match")
    if response.requested_at != request.requested_at:
        raise ValueError("fundamental response request time does not match")
    if isinstance(request, FundamentalStructuredDocumentRequest):
        if not isinstance(response, FundamentalStructuredDocumentResult):
            raise ValueError("structured-document request requires its result type")
        if response.issuer != request.issuer:
            raise ValueError("structured-document response issuer does not match")
        if response.document_type is not request.document_type:
            raise ValueError("structured-document response type does not match")
        if response.reporting_basis is not request.reporting_basis:
            raise ValueError(
                "structured-document response reporting basis does not match"
            )
    if isinstance(response, FundamentalPeerComparisonResult):
        if not isinstance(request, FundamentalCompanyOverviewRequest):
            raise ValueError(
                "Peer Comparison response requires an overview request"
            )
        if response.issuer != request.issuer:
            raise ValueError("Peer Comparison response issuer does not match")
    if isinstance(response, FundamentalBenchmarkingFinancialsResult):
        if not isinstance(request, FundamentalCompanyOverviewRequest):
            raise ValueError(
                "Benchmarking Financials response requires an overview request"
            )
        if response.issuer != request.issuer:
            raise ValueError(
                "Benchmarking Financials response issuer does not match"
            )


@runtime_checkable
class FundamentalEvidenceGateway(Protocol):
    """Read-only provider boundary used by Jarvis application services."""

    @property
    def configuration_fingerprint(self) -> str:
        """Return a non-secret fingerprint of the bound adapter settings."""
        ...

    def inspect_capabilities(
        self,
        *,
        connection: ProviderConnectionScope,
    ) -> FundamentalCapabilityManifest:
        """Verify the current user's scoped provider entitlements."""
        ...

    def search_companies(
        self,
        *,
        request: FundamentalCompanySearchRequest,
    ) -> FundamentalCompanySearchResult:
        ...

    def resolve_issuer(
        self,
        *,
        request: FundamentalIssuerResolutionRequest,
    ) -> FundamentalIssuerResolutionResult:
        ...

    def retrieve_company_overview(
        self,
        *,
        request: FundamentalCompanyOverviewRequest,
    ) -> FundamentalEvidenceRetrieval:
        ...

    def retrieve_financials(
        self,
        *,
        request: FundamentalFinancialsRequest,
    ) -> FundamentalEvidenceRetrieval:
        ...

    def retrieve_shareholding(
        self,
        *,
        request: FundamentalShareholdingRequest,
    ) -> FundamentalEvidenceRetrieval:
        ...


@runtime_checkable
class FundamentalStructuredDocumentGateway(Protocol):
    """Narrow boundary consumed by structured-document coordinators."""

    @property
    def configuration_fingerprint(self) -> str:
        ...

    def retrieve_structured_financial_document(
        self,
        *,
        request: FundamentalStructuredDocumentRequest,
    ) -> FundamentalStructuredDocumentResult:
        ...


@runtime_checkable
class FundamentalPeerComparisonGateway(Protocol):
    """Narrow provider-neutral boundary for Peer Comparison retrieval."""

    @property
    def configuration_fingerprint(self) -> str:
        ...

    def retrieve_peer_comparison(
        self,
        *,
        request: FundamentalCompanyOverviewRequest,
    ) -> FundamentalPeerComparisonResult:
        ...


@runtime_checkable
class FundamentalBenchmarkingFinancialsGateway(Protocol):
    """Narrow provider-neutral boundary for Financial benchmarking."""

    @property
    def configuration_fingerprint(self) -> str:
        ...

    def retrieve_benchmarking_financials(
        self,
        *,
        request: FundamentalCompanyOverviewRequest,
    ) -> FundamentalBenchmarkingFinancialsResult:
        ...
