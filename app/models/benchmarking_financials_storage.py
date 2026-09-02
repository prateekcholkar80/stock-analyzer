"""Database-neutral cache contracts for Benchmarking Financials documents."""

from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.gateways.fundamentals import (
    FundamentalBenchmarkingFinancialsResult,
    FundamentalCompanyOverviewRequest,
    FundamentalRetrievalStatus,
    validate_fundamental_response_binding,
)
from app.models.financial_document_storage import (
    StructuredDocumentRepositoryScope,
)
from app.models.fundamentals import (
    FundamentalIssuerIdentity,
    FundamentalModel,
)


BENCHMARKING_FINANCIALS_MAX_RETENTION = timedelta(days=10)
_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


class BenchmarkingFinancialsCacheKey(FundamentalModel):
    """Semantic identity for one tenant-scoped Financial benchmark matrix."""

    schema_version: Literal[
        "jarvis.benchmarking_financials_cache_key.v1"
    ] = "jarvis.benchmarking_financials_cache_key.v1"
    tenant_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    provider_connection_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    provider: str = Field(min_length=1, max_length=80, pattern=_ID_PATTERN)
    issuer: FundamentalIssuerIdentity
    as_of_date: date | None = None
    document_type: Literal["benchmarking_financials"] = (
        "benchmarking_financials"
    )

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
        return f"benchmarking_financials:{self.cache_key_fingerprint}"

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
        request: FundamentalCompanyOverviewRequest,
    ) -> "BenchmarkingFinancialsCacheKey":
        if not isinstance(request, FundamentalCompanyOverviewRequest):
            raise TypeError(
                "Benchmarking Financials cache key requires an overview request"
            )
        connection = request.connection
        return cls(
            tenant_id=connection.tenant_id,
            provider_connection_id=connection.provider_connection_id,
            provider=connection.provider,
            issuer=request.issuer,
            as_of_date=request.as_of_date,
        )


class StoredBenchmarkingFinancialsDocument(FundamentalModel):
    """Immutable request/result/document-bound Financial benchmark entry."""

    schema_version: Literal[
        "jarvis.stored_benchmarking_financials.v1"
    ] = "jarvis.stored_benchmarking_financials.v1"
    cache_key: BenchmarkingFinancialsCacheKey
    request: FundamentalCompanyOverviewRequest
    result: FundamentalBenchmarkingFinancialsResult
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    result_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    document_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    retrieved_at: datetime
    expires_at: datetime
    stored_at: datetime

    @field_validator("retrieved_at", "expires_at", "stored_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(
            value,
            "Benchmarking Financials cache timestamp",
        )

    @model_validator(mode="after")
    def validate_storage_chain(self) -> Self:
        expected_key = BenchmarkingFinancialsCacheKey.from_request(
            self.request
        )
        if self.cache_key != expected_key:
            raise ValueError(
                "Benchmarking Financials cache key must match request"
            )
        validate_fundamental_response_binding(self.request, self.result)
        if self.result.status is not FundamentalRetrievalStatus.COMPLETED:
            raise ValueError(
                "failed Benchmarking Financials result cannot be cached"
            )
        document = self.result.document
        if document is None:
            raise ValueError(
                "stored Benchmarking Financials requires its document"
            )
        if self.request_fingerprint != self.request.request_fingerprint:
            raise ValueError(
                "stored Benchmarking Financials request fingerprint is incorrect"
            )
        if self.result_fingerprint != self.result.result_fingerprint:
            raise ValueError(
                "stored Benchmarking Financials result fingerprint is incorrect"
            )
        if self.document_fingerprint != document.document_fingerprint:
            raise ValueError(
                "stored Benchmarking Financials document fingerprint is incorrect"
            )
        if self.retrieved_at != document.retrieved_at:
            raise ValueError(
                "Benchmarking Financials retrieval time must match its document"
            )
        if self.expires_at <= self.retrieved_at:
            raise ValueError(
                "Benchmarking Financials expiry must follow retrieval"
            )
        if self.expires_at > document.expires_at:
            raise ValueError(
                "Benchmarking Financials cache cannot outlive its document"
            )
        if (
            self.expires_at
            > self.retrieved_at + BENCHMARKING_FINANCIALS_MAX_RETENTION
        ):
            raise ValueError(
                "Benchmarking Financials retention cannot exceed ten days"
            )
        if self.stored_at < self.result.completed_at:
            raise ValueError(
                "Benchmarking Financials storage cannot predate completion"
            )
        if self.stored_at >= self.expires_at:
            raise ValueError(
                "an expired Benchmarking Financials document cannot be cached"
            )
        return self

    @computed_field
    @property
    def storage_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"storage_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()

    def is_expired(self, *, as_of: datetime) -> bool:
        _require_timezone(as_of, "Benchmarking Financials cache read time")
        return as_of >= self.expires_at


def stored_benchmarking_financials_document(
    request: FundamentalCompanyOverviewRequest,
    result: FundamentalBenchmarkingFinancialsResult,
    *,
    stored_at: datetime,
    retention: timedelta = BENCHMARKING_FINANCIALS_MAX_RETENTION,
) -> StoredBenchmarkingFinancialsDocument:
    """Build a validated cache entry without extending provider freshness."""

    if retention <= timedelta(0):
        raise ValueError("Benchmarking Financials retention must be positive")
    if retention > BENCHMARKING_FINANCIALS_MAX_RETENTION:
        raise ValueError(
            "Benchmarking Financials retention cannot exceed ten days"
        )
    document = result.document
    if (
        result.status is not FundamentalRetrievalStatus.COMPLETED
        or document is None
    ):
        raise ValueError(
            "only completed Benchmarking Financials may be cached"
        )
    expires_at = min(
        document.expires_at,
        document.retrieved_at + retention,
    )
    return StoredBenchmarkingFinancialsDocument(
        cache_key=BenchmarkingFinancialsCacheKey.from_request(request),
        request=request,
        result=result,
        request_fingerprint=request.request_fingerprint,
        result_fingerprint=result.result_fingerprint,
        document_fingerprint=document.document_fingerprint,
        retrieved_at=document.retrieved_at,
        expires_at=expires_at,
        stored_at=stored_at,
    )
