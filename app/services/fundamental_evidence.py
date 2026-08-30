"""Cache-aware coordination for provider-neutral fundamental evidence."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Literal, Self

from pydantic import ConfigDict, model_validator

from app.gateways.fundamentals import (
    FundamentalCompanyOverviewRequest,
    FundamentalEvidenceGateway,
    FundamentalEvidenceRetrieval,
    FundamentalFinancialsRequest,
    FundamentalIssuerResolutionRequest,
    FundamentalIssuerResolutionResult,
    FundamentalRetrievalStatus,
    FundamentalShareholdingRequest,
    validate_fundamental_response_binding,
)
from app.models.fundamental_storage import (
    FUNDAMENTAL_MAX_RETENTION,
    FundamentalSnapshotQuery,
    FundamentalSnapshotRequest,
    StoredFundamentalSnapshot,
    stored_fundamental_snapshot,
)
from app.models.fundamentals import FundamentalIssuerIdentity
from app.models.technical import TechnicalModel
from app.services.fundamental_cache_policy import (
    FundamentalCacheDecision,
    FundamentalCachePolicy,
    FundamentalLoadAction,
)
from app.storage.fundamental_repositories import (
    FundamentalSnapshotRepository,
)


class FundamentalEvidenceSource(StrEnum):
    CACHE = "CACHE"
    PROVIDER = "PROVIDER"


class FundamentalEvidenceLoadResult(TechnicalModel):
    """Evidence returned from cache or a validated provider execution."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal["jarvis.fundamental_evidence_load.v1"] = (
        "jarvis.fundamental_evidence_load.v1"
    )
    source: FundamentalEvidenceSource
    decision: FundamentalCacheDecision
    retrieval: FundamentalEvidenceRetrieval
    stored_snapshot: StoredFundamentalSnapshot | None = None

    @model_validator(mode="after")
    def validate_source_chain(self) -> Self:
        successful = self.retrieval.status in {
            FundamentalRetrievalStatus.COMPLETED,
            FundamentalRetrievalStatus.PARTIAL,
        }
        if self.source is FundamentalEvidenceSource.CACHE:
            if (
                self.decision.action is not FundamentalLoadAction.USE_CACHE
                or self.stored_snapshot is None
                or self.stored_snapshot.retrieval != self.retrieval
            ):
                raise ValueError("cached evidence load is not chain-bound")
        elif self.decision.action is not FundamentalLoadAction.RETRIEVE_PROVIDER:
            raise ValueError("provider evidence requires a provider decision")
        elif successful != (self.stored_snapshot is not None):
            raise ValueError(
                "only successful provider evidence may have a stored snapshot"
            )
        elif (
            self.stored_snapshot is not None
            and self.stored_snapshot.retrieval != self.retrieval
        ):
            raise ValueError("stored provider evidence is not chain-bound")
        return self


class FundamentalIssuerLoadResult(TechnicalModel):
    """Issuer identity reused from fresh cache or resolved by the provider."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal["jarvis.fundamental_issuer_load.v1"] = (
        "jarvis.fundamental_issuer_load.v1"
    )
    source: FundamentalEvidenceSource
    issuer: FundamentalIssuerIdentity | None = None
    provider_resolution: FundamentalIssuerResolutionResult | None = None

    @model_validator(mode="after")
    def validate_resolution_chain(self) -> Self:
        if self.source is FundamentalEvidenceSource.CACHE:
            if self.issuer is None or self.provider_resolution is not None:
                raise ValueError("cached issuer resolution is not chain-bound")
        elif self.provider_resolution is None:
            raise ValueError("provider issuer resolution requires its result")
        elif self.issuer != self.provider_resolution.issuer:
            raise ValueError("provider issuer identity is not chain-bound")
        return self


class FundamentalEvidenceCoordinator:
    """Reuse valid evidence or retrieve and persist it when required."""

    def __init__(
        self,
        *,
        gateway: FundamentalEvidenceGateway,
        repository: FundamentalSnapshotRepository,
        clock: Callable[[], datetime] | None = None,
        retention: timedelta = FUNDAMENTAL_MAX_RETENTION,
    ) -> None:
        if not isinstance(gateway, FundamentalEvidenceGateway):
            raise TypeError("fundamental coordinator requires a gateway")
        if not isinstance(repository, FundamentalSnapshotRepository):
            raise TypeError("fundamental coordinator requires a repository")
        if retention <= timedelta(0) or retention > FUNDAMENTAL_MAX_RETENTION:
            raise ValueError("fundamental coordinator retention is invalid")
        self._gateway = gateway
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._retention = retention
        self._cache_policy = FundamentalCachePolicy(
            repository,
            clock=self._clock,
        )

    def load(
        self,
        request: FundamentalSnapshotRequest,
        *,
        refresh_requested: bool = False,
    ) -> FundamentalEvidenceLoadResult:
        decision = self._cache_policy.decide(
            request,
            refresh_requested=refresh_requested,
        )
        if decision.action is FundamentalLoadAction.USE_CACHE:
            cached = decision.cached_snapshot
            if cached is None:  # Defensive guard beyond the decision model.
                raise RuntimeError("cache decision omitted cached evidence")
            return FundamentalEvidenceLoadResult(
                source=FundamentalEvidenceSource.CACHE,
                decision=decision,
                retrieval=cached.retrieval,
                stored_snapshot=cached,
            )

        retrieval = self._retrieve(request)
        validate_fundamental_response_binding(request, retrieval)
        successful = retrieval.status in {
            FundamentalRetrievalStatus.COMPLETED,
            FundamentalRetrievalStatus.PARTIAL,
        }
        persisted = None
        if successful:
            candidate = stored_fundamental_snapshot(
                request,
                retrieval,
                stored_at=self._clock(),
                retention=self._retention,
            )
            if decision.reason == "explicit_refresh":
                persisted = self._repository.replace_fundamental_snapshot(
                    candidate,
                    scope=decision.cache_key.repository_scope,
                )
            else:
                persisted = self._repository.save_fundamental_snapshot(
                    candidate
                )

        return FundamentalEvidenceLoadResult(
            source=FundamentalEvidenceSource.PROVIDER,
            decision=decision,
            retrieval=retrieval,
            stored_snapshot=persisted,
        )

    def resolve_issuer(
        self,
        request: FundamentalIssuerResolutionRequest,
        *,
        refresh_requested: bool = False,
    ) -> FundamentalIssuerLoadResult:
        """Reuse a fresh scoped identity or resolve it through the provider."""

        if not isinstance(request, FundamentalIssuerResolutionRequest):
            raise TypeError(
                "fundamental issuer resolution requires a resolution request"
            )
        if not isinstance(refresh_requested, bool):
            raise TypeError("fundamental refresh flag must be boolean")
        locator = request.locator
        if (
            not refresh_requested
            and locator.symbol is not None
            and locator.exchange is not None
        ):
            summaries = self._repository.list_fundamental_snapshots(
                FundamentalSnapshotQuery(
                    tenant_id=request.connection.tenant_id,
                    provider_connection_id=(
                        request.connection.provider_connection_id
                    ),
                    provider=request.connection.provider,
                    exchange=locator.exchange,
                    symbol=locator.symbol,
                    limit=100,
                ),
                as_of=self._clock(),
            )
            issuers = {
                summary.issuer.model_dump_json(): summary.issuer
                for summary in summaries
            }
            if len(issuers) == 1:
                return FundamentalIssuerLoadResult(
                    source=FundamentalEvidenceSource.CACHE,
                    issuer=next(iter(issuers.values())),
                )
        result = self._gateway.resolve_issuer(request=request)
        if not isinstance(result, FundamentalIssuerResolutionResult):
            raise ValueError("fundamental gateway returned invalid issuer data")
        validate_fundamental_response_binding(request, result)
        return FundamentalIssuerLoadResult(
            source=FundamentalEvidenceSource.PROVIDER,
            issuer=result.issuer,
            provider_resolution=result,
        )

    def _retrieve(
        self,
        request: FundamentalSnapshotRequest,
    ) -> FundamentalEvidenceRetrieval:
        if isinstance(request, FundamentalCompanyOverviewRequest):
            return self._gateway.retrieve_company_overview(request=request)
        if isinstance(request, FundamentalFinancialsRequest):
            return self._gateway.retrieve_financials(request=request)
        if isinstance(request, FundamentalShareholdingRequest):
            return self._gateway.retrieve_shareholding(request=request)
        raise TypeError("fundamental coordinator received an unsupported request")
