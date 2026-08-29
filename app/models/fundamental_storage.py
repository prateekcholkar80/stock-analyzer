"""Database-neutral cache contracts for validated fundamental evidence."""

from datetime import date, datetime, timedelta
from hashlib import sha256
from typing import Annotated, Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.gateways.fundamentals import (
    FundamentalCapability,
    FundamentalCompanyOverviewRequest,
    FundamentalEvidenceRetrieval,
    FundamentalFinancialsRequest,
    FundamentalRetrievalStatus,
    FundamentalShareholdingRequest,
    validate_fundamental_response_binding,
)
from app.models.fundamentals import (
    FundamentalEvidencePosture,
    FundamentalIssuerIdentity,
    FundamentalModel,
    FundamentalPeriodType,
    FundamentalStatement,
    FundamentalValidationStatus,
)


FUNDAMENTAL_MAX_RETENTION = timedelta(days=10)

_ID_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_EVIDENCE_CAPABILITIES = {
    FundamentalCapability.COMPANY_OVERVIEW,
    FundamentalCapability.FINANCIAL_STATEMENTS,
    FundamentalCapability.SHAREHOLDING_HISTORY,
}
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


def _require_timezone(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} must include timezone information")
    return value


FundamentalSnapshotRequest = Annotated[
    FundamentalCompanyOverviewRequest
    | FundamentalFinancialsRequest
    | FundamentalShareholdingRequest,
    Field(discriminator="capability"),
]


class FundamentalRepositoryScope(FundamentalModel):
    """Mandatory caller scope for tenant-isolated cache operations."""

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


class FundamentalSnapshotCacheKey(FundamentalModel):
    """Semantic query key; execution IDs and request time are excluded."""

    schema_version: Literal["jarvis.fundamental_cache_key.v1"] = (
        "jarvis.fundamental_cache_key.v1"
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
    capability: FundamentalCapability
    issuer: FundamentalIssuerIdentity
    as_of_date: date | None = None
    statements: tuple[FundamentalStatement, ...] = ()
    period_types: tuple[FundamentalPeriodType, ...] = ()
    max_periods: int | None = Field(default=None, ge=1, le=40)
    quarters: int | None = Field(default=None, ge=1, le=40)
    include_promoter_pledge: bool | None = None

    @field_validator("statements")
    @classmethod
    def require_unique_statements(
        cls,
        values: tuple[FundamentalStatement, ...],
    ) -> tuple[FundamentalStatement, ...]:
        if len(values) != len(set(values)):
            raise ValueError("cache-key statements must be unique")
        return values

    @field_validator("period_types")
    @classmethod
    def require_unique_period_types(
        cls,
        values: tuple[FundamentalPeriodType, ...],
    ) -> tuple[FundamentalPeriodType, ...]:
        if len(values) != len(set(values)):
            raise ValueError("cache-key period types must be unique")
        return values

    @model_validator(mode="after")
    def validate_capability_scope(self) -> Self:
        if self.capability not in _EVIDENCE_CAPABILITIES:
            raise ValueError("cache key requires an evidence capability")

        if self.capability is FundamentalCapability.COMPANY_OVERVIEW:
            if (
                self.statements
                or self.period_types
                or self.max_periods is not None
                or self.quarters is not None
                or self.include_promoter_pledge is not None
            ):
                raise ValueError(
                    "overview cache key cannot contain history parameters"
                )
        elif self.capability is FundamentalCapability.FINANCIAL_STATEMENTS:
            if (
                not self.statements
                or not self.period_types
                or self.max_periods is None
                or self.quarters is not None
                or self.include_promoter_pledge is not None
            ):
                raise ValueError(
                    "financial cache key requires only statement-period scope"
                )
            if not set(self.statements).issubset(_FINANCIAL_STATEMENTS):
                raise ValueError(
                    "financial cache key contains an invalid statement"
                )
            if not set(self.period_types).issubset(_REPORTED_PERIOD_TYPES):
                raise ValueError(
                    "financial cache key accepts reported periods only"
                )
        elif (
            self.statements
            or self.period_types
            or self.max_periods is not None
            or self.quarters is None
            or self.include_promoter_pledge is None
        ):
            raise ValueError(
                "shareholding cache key requires only quarter/pledge scope"
            )
        return self

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
        return f"fundamental:{self.cache_key_fingerprint}"

    @computed_field
    @property
    def repository_scope(self) -> FundamentalRepositoryScope:
        return FundamentalRepositoryScope(
            tenant_id=self.tenant_id,
            provider_connection_id=self.provider_connection_id,
            provider=self.provider,
        )

    @classmethod
    def from_request(
        cls,
        request: FundamentalSnapshotRequest,
    ) -> "FundamentalSnapshotCacheKey":
        if not isinstance(
            request,
            (
                FundamentalCompanyOverviewRequest,
                FundamentalFinancialsRequest,
                FundamentalShareholdingRequest,
            ),
        ):
            raise TypeError(
                "fundamental cache key requires an evidence request"
            )
        connection = request.connection
        common = {
            "tenant_id": connection.tenant_id,
            "provider_connection_id": connection.provider_connection_id,
            "provider": connection.provider,
            "capability": request.capability,
            "issuer": request.issuer,
            "as_of_date": request.as_of_date,
        }
        if isinstance(request, FundamentalFinancialsRequest):
            return cls(
                **common,
                statements=request.statements,
                period_types=request.period_types,
                max_periods=request.max_periods,
            )
        if isinstance(request, FundamentalShareholdingRequest):
            return cls(
                **common,
                quarters=request.quarters,
                include_promoter_pledge=request.include_promoter_pledge,
            )
        return cls(**common)


class StoredFundamentalSnapshot(FundamentalModel):
    """Immutable accepted retrieval with bounded cache-retention metadata."""

    schema_version: Literal["jarvis.stored_fundamental_snapshot.v1"] = (
        "jarvis.stored_fundamental_snapshot.v1"
    )
    cache_key: FundamentalSnapshotCacheKey
    request: FundamentalSnapshotRequest
    retrieval: FundamentalEvidenceRetrieval
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    result_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    snapshot_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    retrieved_at: datetime
    expires_at: datetime
    stored_at: datetime

    @field_validator("retrieved_at", "expires_at", "stored_at")
    @classmethod
    def require_storage_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, "fundamental storage timestamp")

    @model_validator(mode="after")
    def validate_storage_chain(self) -> Self:
        expected_key = FundamentalSnapshotCacheKey.from_request(self.request)
        if self.cache_key != expected_key:
            raise ValueError("fundamental cache key must match its request")
        validate_fundamental_response_binding(self.request, self.retrieval)

        if self.request_fingerprint != self.request.request_fingerprint:
            raise ValueError("stored request fingerprint is incorrect")
        if self.result_fingerprint != self.retrieval.result_fingerprint:
            raise ValueError("stored result fingerprint is incorrect")

        snapshot = self.retrieval.snapshot
        if snapshot is None:
            raise ValueError("only successful evidence snapshots may be cached")
        if self.snapshot_fingerprint != snapshot.snapshot_fingerprint:
            raise ValueError("stored snapshot fingerprint is incorrect")
        if self.retrieval.status not in {
            FundamentalRetrievalStatus.COMPLETED,
            FundamentalRetrievalStatus.PARTIAL,
        }:
            raise ValueError("failed fundamental retrieval cannot be cached")
        if snapshot.validation_status in {
            FundamentalValidationStatus.PENDING,
            FundamentalValidationStatus.REJECTED,
            FundamentalValidationStatus.QUARANTINED,
        }:
            raise ValueError("unaccepted fundamental snapshot cannot be cached")

        if self.retrieved_at != self.retrieval.completed_at:
            raise ValueError(
                "cache retrieval time must match gateway completion time"
            )
        if self.expires_at <= self.retrieved_at:
            raise ValueError("fundamental cache expiry must follow retrieval")
        if self.expires_at > self.retrieved_at + FUNDAMENTAL_MAX_RETENTION:
            raise ValueError(
                "fundamental cache retention cannot exceed ten days"
            )
        if self.stored_at < self.retrieval.completed_at:
            raise ValueError("cache storage cannot predate retrieval")
        if self.stored_at >= self.expires_at:
            raise ValueError("an already-expired snapshot cannot be cached")
        return self

    @computed_field
    @property
    def storage_fingerprint(self) -> str:
        payload = self.model_dump_json(exclude={"storage_fingerprint"})
        return sha256(payload.encode("utf-8")).hexdigest()

    def is_expired(self, *, as_of: datetime) -> bool:
        _require_timezone(as_of, "fundamental cache read time")
        return as_of >= self.expires_at


class FundamentalSnapshotSummary(FundamentalModel):
    """Payload-free metadata returned by scoped cache listings."""

    cache_entry_id: str = Field(
        min_length=1,
        max_length=200,
        pattern=_ID_PATTERN,
    )
    cache_key_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    tenant_id: str = Field(min_length=1, max_length=128, pattern=_ID_PATTERN)
    provider_connection_id: str = Field(
        min_length=1,
        max_length=128,
        pattern=_ID_PATTERN,
    )
    provider: str = Field(min_length=1, max_length=80, pattern=_ID_PATTERN)
    capability: FundamentalCapability
    issuer: FundamentalIssuerIdentity
    request_id: str = Field(min_length=1, max_length=160, pattern=_ID_PATTERN)
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    result_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    snapshot_id: str = Field(min_length=1, max_length=160, pattern=_ID_PATTERN)
    snapshot_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    retrieval_status: FundamentalRetrievalStatus
    evidence_posture: FundamentalEvidencePosture
    validation_status: FundamentalValidationStatus
    retrieved_at: datetime
    expires_at: datetime
    stored_at: datetime
    source_count: int = Field(ge=1)
    fact_count: int = Field(ge=1)
    conflict_count: int = Field(ge=0)

    @field_validator("retrieved_at", "expires_at", "stored_at")
    @classmethod
    def require_summary_timezone(cls, value: datetime) -> datetime:
        return _require_timezone(value, "fundamental summary timestamp")

    @model_validator(mode="after")
    def validate_summary(self) -> Self:
        if self.capability not in _EVIDENCE_CAPABILITIES:
            raise ValueError("summary requires an evidence capability")
        if self.retrieval_status not in {
            FundamentalRetrievalStatus.COMPLETED,
            FundamentalRetrievalStatus.PARTIAL,
        }:
            raise ValueError("summary cannot represent a failed retrieval")
        if self.validation_status in {
            FundamentalValidationStatus.PENDING,
            FundamentalValidationStatus.REJECTED,
            FundamentalValidationStatus.QUARANTINED,
        }:
            raise ValueError("summary cannot represent rejected evidence")
        if self.expires_at <= self.retrieved_at:
            raise ValueError("summary expiry must follow retrieval")
        if self.stored_at < self.retrieved_at:
            raise ValueError("summary storage cannot predate retrieval")
        if self.stored_at >= self.expires_at:
            raise ValueError("summary cannot represent an expired-at-save row")
        return self


class FundamentalSnapshotQuery(FundamentalModel):
    """Mandatory tenant/provider scope for non-expired metadata listings."""

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
    exchange: str | None = Field(default=None, min_length=1, max_length=32)
    symbol: str | None = Field(default=None, min_length=1, max_length=100)
    capabilities: tuple[FundamentalCapability, ...] = ()
    retrieved_from: datetime | None = None
    retrieved_to: datetime | None = None
    limit: int = Field(default=100, ge=1, le=1_000)
    offset: int = Field(default=0, ge=0)

    @field_validator("exchange", "symbol", mode="before")
    @classmethod
    def normalize_market_identity(cls, value: str | None) -> str | None:
        return value.upper() if value is not None else None

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(
        cls,
        values: tuple[FundamentalCapability, ...],
    ) -> tuple[FundamentalCapability, ...]:
        if len(values) != len(set(values)):
            raise ValueError("query capabilities must be unique")
        if not set(values).issubset(_EVIDENCE_CAPABILITIES):
            raise ValueError("query supports evidence capabilities only")
        return values

    @field_validator("retrieved_from", "retrieved_to")
    @classmethod
    def require_query_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None:
            return _require_timezone(value, "fundamental query timestamp")
        return value

    @field_validator("limit", "offset", mode="before")
    @classmethod
    def require_integer_paging(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("fundamental query pagination must be integers")
        return value

    @model_validator(mode="after")
    def validate_query(self) -> Self:
        if (
            self.retrieved_from is not None
            and self.retrieved_to is not None
            and self.retrieved_from > self.retrieved_to
        ):
            raise ValueError("query start cannot follow its end")
        if self.exchange is not None and self.symbol is None:
            raise ValueError("exchange filter requires a symbol")
        return self

    @computed_field
    @property
    def repository_scope(self) -> FundamentalRepositoryScope:
        return FundamentalRepositoryScope(
            tenant_id=self.tenant_id,
            provider_connection_id=self.provider_connection_id,
            provider=self.provider,
        )


def stored_fundamental_snapshot(
    request: FundamentalSnapshotRequest,
    retrieval: FundamentalEvidenceRetrieval,
    *,
    stored_at: datetime,
    retention: timedelta = FUNDAMENTAL_MAX_RETENTION,
) -> StoredFundamentalSnapshot:
    """Build a chain-bound cache entry with bounded retention."""

    if retention <= timedelta(0):
        raise ValueError("fundamental retention must be positive")
    if retention > FUNDAMENTAL_MAX_RETENTION:
        raise ValueError("fundamental retention cannot exceed ten days")
    snapshot = retrieval.snapshot
    if snapshot is None:
        raise ValueError("only successful evidence snapshots may be cached")
    return StoredFundamentalSnapshot(
        cache_key=FundamentalSnapshotCacheKey.from_request(request),
        request=request,
        retrieval=retrieval,
        request_fingerprint=request.request_fingerprint,
        result_fingerprint=retrieval.result_fingerprint,
        snapshot_fingerprint=snapshot.snapshot_fingerprint,
        retrieved_at=retrieval.completed_at,
        expires_at=retrieval.completed_at + retention,
        stored_at=stored_at,
    )


def fundamental_snapshot_summary(
    stored: StoredFundamentalSnapshot,
) -> FundamentalSnapshotSummary:
    snapshot = stored.retrieval.snapshot
    if snapshot is None:
        raise ValueError("stored fundamental snapshot is missing its payload")
    key = stored.cache_key
    return FundamentalSnapshotSummary(
        cache_entry_id=key.cache_entry_id,
        cache_key_fingerprint=key.cache_key_fingerprint,
        tenant_id=key.tenant_id,
        provider_connection_id=key.provider_connection_id,
        provider=key.provider,
        capability=key.capability,
        issuer=key.issuer,
        request_id=stored.request.request_id,
        request_fingerprint=stored.request_fingerprint,
        result_fingerprint=stored.result_fingerprint,
        snapshot_id=snapshot.snapshot_id,
        snapshot_fingerprint=stored.snapshot_fingerprint,
        retrieval_status=stored.retrieval.status,
        evidence_posture=snapshot.evidence_posture,
        validation_status=snapshot.validation_status,
        retrieved_at=stored.retrieved_at,
        expires_at=stored.expires_at,
        stored_at=stored.stored_at,
        source_count=len(snapshot.sources),
        fact_count=len(snapshot.facts),
        conflict_count=len(snapshot.conflicts),
    )
