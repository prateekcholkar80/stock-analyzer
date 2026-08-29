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

_MAX_PAYLOAD_BYTES = 1_000_000
_MAX_JSON_DEPTH = 8
_MAX_JSON_NODES = 10_000


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
