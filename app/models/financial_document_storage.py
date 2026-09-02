"""Database-neutral cache contracts for structured financial documents."""

from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.gateways.fundamentals import (
    FundamentalRetrievalStatus,
    FundamentalStructuredDocumentRequest,
    FundamentalStructuredDocumentResult,
    validate_fundamental_response_binding,
)
from app.models.financial_documents import (
    FinancialDocumentType,
    FinancialReportingBasis,
)
from app.models.fundamentals import (
    FundamentalIssuerIdentity,
    FundamentalModel,
)


STRUCTURED_DOCUMENT_MAX_RETENTION = timedelta(days=10)
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


class StructuredDocumentRepositoryScope(FundamentalModel):
    """Mandatory tenant/provider boundary for every document operation."""

    tenant_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    provider_connection_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    provider: str = Field(min_length=1, max_length=80, pattern=_ID_PATTERN)


class StructuredDocumentCacheKey(FundamentalModel):
    """Semantic identity for one company, document, and reporting basis."""

    schema_version: Literal["jarvis.structured_document_cache_key.v1"] = (
        "jarvis.structured_document_cache_key.v1"
    )
    tenant_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    provider_connection_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    provider: str = Field(min_length=1, max_length=80, pattern=_ID_PATTERN)
    issuer: FundamentalIssuerIdentity
    as_of_date: date | None = None
    document_type: FinancialDocumentType
    reporting_basis: FinancialReportingBasis

    @computed_field
    @property
    def cache_key_fingerprint(self) -> str:
        payload = self.model_dump_json(
            exclude={"cache_key_fingerprint", "cache_entry_id"}
        )
        return sha256(payload.encode("utf-8")).hexdigest()

    @computed_field
    @property
    def cache_entry_id(self) -> str:
        return f"financial_document:{self.cache_key_fingerprint}"

    @computed_field
    @property
    def repository_scope(self) -> StructuredDocumentRepositoryScope:
        return StructuredDocumentRepositoryScope(
            tenant_id=self.tenant_id,
            provider_connection_id=self.provider_connection_id,
            provider=self.provider,
        )

    @classmethod
    def from_request(
        cls,
        request: FundamentalStructuredDocumentRequest,
    ) -> "StructuredDocumentCacheKey":
        if not isinstance(request, FundamentalStructuredDocumentRequest):
            raise TypeError(
                "structured document cache key requires its request contract"
            )
        connection = request.connection
        return cls(
            tenant_id=connection.tenant_id,
            provider_connection_id=connection.provider_connection_id,
            provider=connection.provider,
            issuer=request.issuer,
            as_of_date=request.as_of_date,
            document_type=request.document_type,
            reporting_basis=request.reporting_basis,
        )


class StoredStructuredFinancialDocument(FundamentalModel):
    """Immutable, chain-bound structured document cache entry."""

    schema_version: Literal["jarvis.stored_structured_document.v1"] = (
        "jarvis.stored_structured_document.v1"
    )
    cache_key: StructuredDocumentCacheKey
    request: FundamentalStructuredDocumentRequest
    result: FundamentalStructuredDocumentResult
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    result_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    document_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    retrieved_at: datetime
    expires_at: datetime
    stored_at: datetime

    @field_validator("retrieved_at", "expires_at", "stored_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, "structured document cache timestamp")

    @model_validator(mode="after")
    def validate_storage_chain(self) -> Self:
        if self.cache_key != StructuredDocumentCacheKey.from_request(self.request):
            raise ValueError("structured document cache key must match request")
        validate_fundamental_response_binding(self.request, self.result)
        if self.result.status is not FundamentalRetrievalStatus.COMPLETED:
            raise ValueError("failed structured document result cannot be cached")
        document = self.result.document
        if document is None:
            raise ValueError("stored structured result requires its document")
        if self.request_fingerprint != self.request.request_fingerprint:
            raise ValueError("stored structured request fingerprint is incorrect")
        if self.result_fingerprint != self.result.result_fingerprint:
            raise ValueError("stored structured result fingerprint is incorrect")
        if self.document_fingerprint != document.document_fingerprint:
            raise ValueError("stored document fingerprint is incorrect")
        if self.retrieved_at != document.retrieved_at:
            raise ValueError("cache retrieval time must match document retrieval")
        if self.expires_at <= self.retrieved_at:
            raise ValueError("structured document expiry must follow retrieval")
        if self.expires_at > document.expires_at:
            raise ValueError("cache cannot outlive the provider document")
        if self.expires_at > self.retrieved_at + STRUCTURED_DOCUMENT_MAX_RETENTION:
            raise ValueError("structured document retention cannot exceed ten days")
        if self.stored_at < self.result.completed_at:
            raise ValueError("structured document storage cannot predate completion")
        if self.stored_at >= self.expires_at:
            raise ValueError("an already-expired document cannot be cached")
        return self

    @computed_field
    @property
    def storage_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"storage_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()

    def is_expired(self, *, as_of: datetime) -> bool:
        _require_timezone(as_of, "structured document cache read time")
        return as_of >= self.expires_at


def stored_structured_financial_document(
    request: FundamentalStructuredDocumentRequest,
    result: FundamentalStructuredDocumentResult,
    *,
    stored_at: datetime,
    retention: timedelta = STRUCTURED_DOCUMENT_MAX_RETENTION,
) -> StoredStructuredFinancialDocument:
    """Build one validated entry without extending provider freshness."""

    if retention <= timedelta(0):
        raise ValueError("structured document retention must be positive")
    if retention > STRUCTURED_DOCUMENT_MAX_RETENTION:
        raise ValueError("structured document retention cannot exceed ten days")
    document = result.document
    if result.status is not FundamentalRetrievalStatus.COMPLETED or document is None:
        raise ValueError("only completed structured documents may be cached")
    expires_at = min(
        document.expires_at,
        document.retrieved_at + retention,
    )
    return StoredStructuredFinancialDocument(
        cache_key=StructuredDocumentCacheKey.from_request(request),
        request=request,
        result=result,
        request_fingerprint=request.request_fingerprint,
        result_fingerprint=result.result_fingerprint,
        document_fingerprint=document.document_fingerprint,
        retrieved_at=document.retrieved_at,
        expires_at=expires_at,
        stored_at=stored_at,
    )
