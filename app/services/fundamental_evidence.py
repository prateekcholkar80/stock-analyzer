"""Cache-aware coordination for provider-neutral fundamental evidence."""

from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from enum import StrEnum
from typing import Literal, Self

from pydantic import ConfigDict, model_validator

from app.exceptions import FundamentalGatewayError
from app.gateways.fundamentals import (
    FundamentalBenchmarkingFinancialsGateway,
    FundamentalBenchmarkingFinancialsResult,
    FundamentalCompanyOverviewRequest,
    FundamentalEvidenceGateway,
    FundamentalEvidenceRetrieval,
    FundamentalFinancialDocumentPackageGateway,
    FundamentalFinancialDocumentPackageRequest,
    FundamentalFinancialDocumentPackageResult,
    FundamentalFinancialsRequest,
    FundamentalIssuerResolutionRequest,
    FundamentalIssuerResolutionResult,
    FundamentalPeerComparisonGateway,
    FundamentalPeerComparisonResult,
    FundamentalRetrievalStatus,
    FundamentalShareholdingRequest,
    FundamentalStructuredDocumentGateway,
    FundamentalStructuredDocumentRequest,
    FundamentalStructuredDocumentResult,
    validate_fundamental_response_binding,
)
from app.models.fundamental_storage import (
    FUNDAMENTAL_MAX_RETENTION,
    FundamentalSnapshotQuery,
    FundamentalSnapshotRequest,
    StoredFundamentalSnapshot,
    stored_fundamental_snapshot,
)
from app.models.benchmarking_financials_storage import (
    BenchmarkingFinancialsCacheKey,
    StoredBenchmarkingFinancialsDocument,
    stored_benchmarking_financials_document,
)
from app.models.financial_document_storage import (
    StoredStructuredFinancialDocument,
    StructuredDocumentCacheKey,
    stored_structured_financial_document,
)
from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_package import (
    FundamentalEvidencePackage,
    FundamentalPackageDataset,
    FundamentalPackageItem,
    FundamentalPackageItemKey,
    FundamentalPackageManifest,
)
from app.models.fundamentals import (
    FundamentalIssuerIdentity,
    ProviderConnectionScope,
)
from app.models.peer_comparison_storage import (
    PeerComparisonCacheKey,
    StoredPeerComparisonDocument,
    stored_peer_comparison_document,
)
from app.models.technical import TechnicalModel
from app.services.fundamental_cache_policy import (
    FundamentalCacheDecision,
    FundamentalCachePolicy,
    FundamentalLoadAction,
)
from app.storage.fundamental_repositories import (
    FundamentalSnapshotRepository,
)
from app.storage.benchmarking_financials_repositories import (
    BenchmarkingFinancialsRepository,
)
from app.storage.financial_document_repositories import (
    StructuredFinancialDocumentRepository,
)
from app.storage.peer_comparison_repositories import PeerComparisonRepository
from app.storage.fundamental_package_repositories import (
    FundamentalPackageRepository,
    FundamentalPackageRepositoryScope,
)
from app.storage.fundamental_filing_evidence_repositories import (
    FundamentalFilingEvidenceRepository,
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


class StructuredDocumentLoadResult(TechnicalModel):
    """Structured document returned from cache or a provider execution."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal["jarvis.structured_document_load.v1"] = (
        "jarvis.structured_document_load.v1"
    )
    source: FundamentalEvidenceSource
    result: FundamentalStructuredDocumentResult
    stored_document: StoredStructuredFinancialDocument | None = None
    refresh_requested: bool = False

    @model_validator(mode="after")
    def validate_source_chain(self) -> Self:
        completed = self.result.status is FundamentalRetrievalStatus.COMPLETED
        if self.source is FundamentalEvidenceSource.CACHE:
            if self.refresh_requested:
                raise ValueError("explicit refresh cannot return cached document")
            if (
                self.stored_document is None
                or self.stored_document.result != self.result
            ):
                raise ValueError("cached structured document is not chain-bound")
        elif completed != (self.stored_document is not None):
            raise ValueError(
                "only completed provider documents may have stored content"
            )
        elif (
            self.stored_document is not None
            and self.stored_document.result != self.result
        ):
            raise ValueError("stored provider document is not chain-bound")
        return self


class PeerComparisonLoadResult(TechnicalModel):
    """Peer Comparison returned from cache or a provider execution."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal["jarvis.peer_comparison_load.v1"] = (
        "jarvis.peer_comparison_load.v1"
    )
    source: FundamentalEvidenceSource
    result: FundamentalPeerComparisonResult
    stored_document: StoredPeerComparisonDocument | None = None
    refresh_requested: bool = False
    refresh_failure_status: FundamentalRetrievalStatus | None = None

    @model_validator(mode="after")
    def validate_source_chain(self) -> Self:
        completed = self.result.status is FundamentalRetrievalStatus.COMPLETED
        if self.source is FundamentalEvidenceSource.CACHE:
            if (
                self.stored_document is None
                or self.stored_document.result != self.result
            ):
                raise ValueError("cached Peer Comparison is not chain-bound")
            if self.refresh_requested != (
                self.refresh_failure_status is not None
            ):
                raise ValueError(
                    "cached Peer Comparison refresh state is inconsistent"
                )
            if (
                self.refresh_failure_status
                is FundamentalRetrievalStatus.COMPLETED
            ):
                raise ValueError(
                    "failed Peer Comparison refresh cannot be completed"
                )
        else:
            if self.refresh_failure_status is not None:
                raise ValueError(
                    "provider Peer Comparison cannot contain fallback status"
                )
            if completed != (self.stored_document is not None):
                raise ValueError(
                    "only completed Peer Comparisons may have stored content"
                )
            if (
                self.stored_document is not None
                and self.stored_document.result != self.result
            ):
                raise ValueError(
                    "stored provider Peer Comparison is not chain-bound"
                )
        return self


class BenchmarkingFinancialsLoadResult(TechnicalModel):
    """Financial benchmark matrix returned from cache or its provider."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    schema_version: Literal["jarvis.benchmarking_financials_load.v1"] = (
        "jarvis.benchmarking_financials_load.v1"
    )
    source: FundamentalEvidenceSource
    result: FundamentalBenchmarkingFinancialsResult
    stored_document: StoredBenchmarkingFinancialsDocument | None = None
    refresh_requested: bool = False
    refresh_failure_status: FundamentalRetrievalStatus | None = None

    @model_validator(mode="after")
    def validate_source_chain(self) -> Self:
        completed = self.result.status is FundamentalRetrievalStatus.COMPLETED
        if self.source is FundamentalEvidenceSource.CACHE:
            if (
                self.stored_document is None
                or self.stored_document.result != self.result
            ):
                raise ValueError(
                    "cached Benchmarking Financials is not chain-bound"
                )
            if self.refresh_requested != (
                self.refresh_failure_status is not None
            ):
                raise ValueError(
                    "cached Benchmarking Financials refresh state is inconsistent"
                )
            if (
                self.refresh_failure_status
                is FundamentalRetrievalStatus.COMPLETED
            ):
                raise ValueError(
                    "failed Benchmarking Financials refresh cannot be completed"
                )
        else:
            if self.refresh_failure_status is not None:
                raise ValueError(
                    "provider Benchmarking Financials cannot contain fallback status"
                )
            if completed != (self.stored_document is not None):
                raise ValueError(
                    "only completed Benchmarking Financials may have stored content"
                )
            if (
                self.stored_document is not None
                and self.stored_document.result != self.result
            ):
                raise ValueError(
                    "stored provider Benchmarking Financials is not chain-bound"
                )
        return self


class FundamentalEvidenceCoordinator:
    """Reuse valid evidence or retrieve and persist it when required."""

    def __init__(
        self,
        *,
        gateway: FundamentalEvidenceGateway,
        repository: FundamentalSnapshotRepository,
        structured_document_gateway: (
            FundamentalStructuredDocumentGateway | None
        ) = None,
        financial_document_package_gateway: (
            FundamentalFinancialDocumentPackageGateway | None
        ) = None,
        structured_document_repository: (
            StructuredFinancialDocumentRepository | None
        ) = None,
        peer_comparison_gateway: FundamentalPeerComparisonGateway | None = None,
        peer_comparison_repository: PeerComparisonRepository | None = None,
        benchmarking_financials_gateway: (
            FundamentalBenchmarkingFinancialsGateway | None
        ) = None,
        benchmarking_financials_repository: (
            BenchmarkingFinancialsRepository | None
        ) = None,
        package_repository: FundamentalPackageRepository | None = None,
        filing_evidence_repository: (
            FundamentalFilingEvidenceRepository | None
        ) = None,
        clock: Callable[[], datetime] | None = None,
        retention: timedelta = FUNDAMENTAL_MAX_RETENTION,
    ) -> None:
        if not isinstance(gateway, FundamentalEvidenceGateway):
            raise TypeError("fundamental coordinator requires a gateway")
        if not isinstance(repository, FundamentalSnapshotRepository):
            raise TypeError("fundamental coordinator requires a repository")
        if structured_document_gateway is None and isinstance(
            gateway,
            FundamentalStructuredDocumentGateway,
        ):
            structured_document_gateway = gateway
        if structured_document_gateway is not None and not isinstance(
            structured_document_gateway,
            FundamentalStructuredDocumentGateway,
        ):
            raise TypeError(
                "structured document coordinator requires its gateway"
            )
        if structured_document_repository is not None and not isinstance(
            structured_document_repository,
            StructuredFinancialDocumentRepository,
        ):
            raise TypeError(
                "structured document coordinator requires its repository"
            )
        if financial_document_package_gateway is None and isinstance(
            gateway,
            FundamentalFinancialDocumentPackageGateway,
        ):
            financial_document_package_gateway = gateway
        if (
            financial_document_package_gateway is not None
            and not isinstance(
                financial_document_package_gateway,
                FundamentalFinancialDocumentPackageGateway,
            )
        ):
            raise TypeError(
                "financial package coordinator requires its gateway"
            )
        if peer_comparison_gateway is None and isinstance(
            gateway,
            FundamentalPeerComparisonGateway,
        ):
            peer_comparison_gateway = gateway
        if peer_comparison_gateway is not None and not isinstance(
            peer_comparison_gateway,
            FundamentalPeerComparisonGateway,
        ):
            raise TypeError("Peer Comparison coordinator requires its gateway")
        if peer_comparison_repository is not None and not isinstance(
            peer_comparison_repository,
            PeerComparisonRepository,
        ):
            raise TypeError(
                "Peer Comparison coordinator requires its repository"
            )
        if benchmarking_financials_gateway is None and isinstance(
            gateway,
            FundamentalBenchmarkingFinancialsGateway,
        ):
            benchmarking_financials_gateway = gateway
        if (
            benchmarking_financials_gateway is not None
            and not isinstance(
                benchmarking_financials_gateway,
                FundamentalBenchmarkingFinancialsGateway,
            )
        ):
            raise TypeError(
                "Benchmarking Financials coordinator requires its gateway"
            )
        if (
            benchmarking_financials_repository is not None
            and not isinstance(
                benchmarking_financials_repository,
                BenchmarkingFinancialsRepository,
            )
        ):
            raise TypeError(
                "Benchmarking Financials coordinator requires its repository"
            )
        if package_repository is not None and not isinstance(
            package_repository,
            FundamentalPackageRepository,
        ):
            raise TypeError("fundamental package coordinator requires its repository")
        if filing_evidence_repository is not None and not isinstance(
            filing_evidence_repository,
            FundamentalFilingEvidenceRepository,
        ):
            raise TypeError(
                "fundamental filing evidence coordinator requires its repository"
            )
        if retention <= timedelta(0) or retention > FUNDAMENTAL_MAX_RETENTION:
            raise ValueError("fundamental coordinator retention is invalid")
        self._gateway = gateway
        self._structured_document_gateway = structured_document_gateway
        self._financial_document_package_gateway = (
            financial_document_package_gateway
        )
        self._structured_document_repository = structured_document_repository
        self._peer_comparison_gateway = peer_comparison_gateway
        self._peer_comparison_repository = peer_comparison_repository
        self._benchmarking_financials_gateway = (
            benchmarking_financials_gateway
        )
        self._benchmarking_financials_repository = (
            benchmarking_financials_repository
        )
        self._package_repository = package_repository
        self._filing_evidence_repository = filing_evidence_repository
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._retention = retention
        self._cache_policy = FundamentalCachePolicy(
            repository,
            clock=self._clock,
        )

    def load_active_package(
        self,
        *,
        connection: ProviderConnectionScope,
        issuer: FundamentalIssuerIdentity,
    ) -> FundamentalEvidencePackage | None:
        """Read the current complete package inside an explicit tenant scope."""

        repository = self._package_repository
        if repository is None:
            raise RuntimeError("fundamental package cache is not configured")
        scope = FundamentalPackageRepositoryScope(
            connection=connection,
            issuer=issuer,
        )
        return repository.get_active_fundamental_package(
            scope=scope,
            as_of=self._clock(),
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

    def load_structured_document(
        self,
        request: FundamentalStructuredDocumentRequest,
        *,
        refresh_requested: bool = False,
    ) -> StructuredDocumentLoadResult:
        """Reuse a fresh document or retrieve and atomically persist it."""

        if not isinstance(request, FundamentalStructuredDocumentRequest):
            raise TypeError(
                "structured document load requires its request contract"
            )
        if not isinstance(refresh_requested, bool):
            raise TypeError("structured document refresh flag must be boolean")
        gateway = self._structured_document_gateway
        if gateway is None:
            raise RuntimeError(
                "structured document retrieval is not configured"
            )
        repository = self._structured_document_repository
        if repository is None:
            raise RuntimeError("structured document cache is not configured")
        cache_key = StructuredDocumentCacheKey.from_request(request)
        cached = repository.get_structured_financial_document(
            cache_key,
            scope=cache_key.repository_scope,
            as_of=self._clock(),
        )
        if cached is not None and not refresh_requested:
            return StructuredDocumentLoadResult(
                source=FundamentalEvidenceSource.CACHE,
                result=cached.result,
                stored_document=cached,
            )
        result = gateway.retrieve_structured_financial_document(
            request=request,
        )
        if not isinstance(result, FundamentalStructuredDocumentResult):
            raise ValueError(
                "structured document gateway returned an invalid result"
            )
        validate_fundamental_response_binding(request, result)
        persisted = None
        if result.status is FundamentalRetrievalStatus.COMPLETED:
            candidate = stored_structured_financial_document(
                request,
                result,
                stored_at=self._clock(),
                retention=self._retention,
            )
            if refresh_requested:
                persisted = repository.replace_structured_financial_document(
                    candidate,
                    scope=cache_key.repository_scope,
                )
            else:
                persisted = repository.save_structured_financial_document(
                    candidate
                )
        return StructuredDocumentLoadResult(
            source=FundamentalEvidenceSource.PROVIDER,
            result=result,
            stored_document=persisted,
            refresh_requested=refresh_requested,
        )

    def refresh_structured_document_package(
        self,
        request: FundamentalFinancialDocumentPackageRequest,
    ) -> tuple[StructuredDocumentLoadResult, ...]:
        """Retrieve and atomically cache all eleven financial documents."""

        if not isinstance(request, FundamentalFinancialDocumentPackageRequest):
            raise TypeError("financial package refresh requires its request")
        gateway = self._financial_document_package_gateway
        if gateway is None:
            raise RuntimeError("financial package retrieval is not configured")
        repository = self._structured_document_repository
        if repository is None:
            raise RuntimeError("structured document cache is not configured")

        package_result = gateway.retrieve_financial_document_package(
            request=request
        )
        if not isinstance(
            package_result,
            FundamentalFinancialDocumentPackageResult,
        ):
            raise ValueError("financial package gateway returned an invalid result")
        validate_fundamental_response_binding(request, package_result)
        package = package_result.package
        if (
            package_result.status is not FundamentalRetrievalStatus.COMPLETED
            or package is None
        ):
            raise ValueError("financial package retrieval did not complete")

        stored_at = self._clock()
        requests = []
        results = []
        candidates = []
        for document in package.documents:
            document_request = FundamentalStructuredDocumentRequest(
                request_id=request.request_id,
                operation_id=request.operation_id,
                connection=request.connection,
                requested_at=request.requested_at,
                issuer=request.issuer,
                as_of_date=request.as_of_date,
                document_type=document.document_type,
                reporting_basis=document.reporting_basis,
            )
            document_result = FundamentalStructuredDocumentResult(
                request_id=document_request.request_id,
                request_fingerprint=document_request.request_fingerprint,
                connection=document_request.connection,
                requested_at=document_request.requested_at,
                started_at=package_result.started_at,
                completed_at=package_result.completed_at,
                provider_contract_version=(
                    package_result.provider_contract_version
                ),
                adapter_fingerprint=package_result.adapter_fingerprint,
                issuer=document_request.issuer,
                document_type=document_request.document_type,
                reporting_basis=document_request.reporting_basis,
                status=FundamentalRetrievalStatus.COMPLETED,
                document=document,
            )
            validate_fundamental_response_binding(
                document_request,
                document_result,
            )
            requests.append(document_request)
            results.append(document_result)
            candidates.append(
                stored_structured_financial_document(
                    document_request,
                    document_result,
                    stored_at=stored_at,
                    retention=self._retention,
                )
            )

        scope = candidates[0].cache_key.repository_scope
        persisted = repository.replace_structured_financial_document_package(
            tuple(candidates),
            scope=scope,
        )
        if len(persisted) != len(candidates):
            raise ValueError(
                "financial package repository returned an incomplete result"
            )
        return tuple(
            StructuredDocumentLoadResult(
                source=FundamentalEvidenceSource.PROVIDER,
                result=result,
                stored_document=stored,
                refresh_requested=True,
            )
            for result, stored in zip(results, persisted, strict=True)
        )

    def load_peer_comparison(
        self,
        request: FundamentalCompanyOverviewRequest,
        *,
        refresh_requested: bool = False,
    ) -> PeerComparisonLoadResult:
        """Reuse cached peer JSON or retrieve and atomically replace it."""

        if not isinstance(request, FundamentalCompanyOverviewRequest):
            raise TypeError(
                "Peer Comparison load requires an overview request"
            )
        if not isinstance(refresh_requested, bool):
            raise TypeError("Peer Comparison refresh flag must be boolean")
        gateway = self._peer_comparison_gateway
        if gateway is None:
            raise RuntimeError("Peer Comparison retrieval is not configured")
        repository = self._peer_comparison_repository
        if repository is None:
            raise RuntimeError("Peer Comparison cache is not configured")

        cache_key = PeerComparisonCacheKey.from_request(request)
        cached = repository.get_peer_comparison(
            cache_key,
            scope=cache_key.repository_scope,
            as_of=self._clock(),
        )
        if cached is not None and not refresh_requested:
            return PeerComparisonLoadResult(
                source=FundamentalEvidenceSource.CACHE,
                result=cached.result,
                stored_document=cached,
            )

        try:
            result = gateway.retrieve_peer_comparison(request=request)
        except FundamentalGatewayError:
            if cached is None:
                raise
            return PeerComparisonLoadResult(
                source=FundamentalEvidenceSource.CACHE,
                result=cached.result,
                stored_document=cached,
                refresh_requested=True,
                refresh_failure_status=(
                    FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE
                ),
            )
        if not isinstance(result, FundamentalPeerComparisonResult):
            raise ValueError(
                "Peer Comparison gateway returned an invalid result"
            )
        validate_fundamental_response_binding(request, result)

        if result.status is not FundamentalRetrievalStatus.COMPLETED:
            if cached is not None and refresh_requested:
                return PeerComparisonLoadResult(
                    source=FundamentalEvidenceSource.CACHE,
                    result=cached.result,
                    stored_document=cached,
                    refresh_requested=True,
                    refresh_failure_status=result.status,
                )
            return PeerComparisonLoadResult(
                source=FundamentalEvidenceSource.PROVIDER,
                result=result,
                refresh_requested=refresh_requested,
            )

        candidate = stored_peer_comparison_document(
            request,
            result,
            stored_at=self._clock(),
            retention=self._retention,
        )
        if refresh_requested:
            persisted = repository.replace_peer_comparison(
                candidate,
                scope=cache_key.repository_scope,
            )
        else:
            persisted = repository.save_peer_comparison(candidate)
        return PeerComparisonLoadResult(
            source=FundamentalEvidenceSource.PROVIDER,
            result=result,
            stored_document=persisted,
            refresh_requested=refresh_requested,
        )

    def load_benchmarking_financials(
        self,
        request: FundamentalCompanyOverviewRequest,
        *,
        refresh_requested: bool = False,
    ) -> BenchmarkingFinancialsLoadResult:
        """Reuse cached benchmark JSON or retrieve and replace it atomically."""

        if not isinstance(request, FundamentalCompanyOverviewRequest):
            raise TypeError(
                "Benchmarking Financials load requires an overview request"
            )
        if not isinstance(refresh_requested, bool):
            raise TypeError(
                "Benchmarking Financials refresh flag must be boolean"
            )
        gateway = self._benchmarking_financials_gateway
        if gateway is None:
            raise RuntimeError(
                "Benchmarking Financials retrieval is not configured"
            )
        repository = self._benchmarking_financials_repository
        if repository is None:
            raise RuntimeError(
                "Benchmarking Financials cache is not configured"
            )

        cache_key = BenchmarkingFinancialsCacheKey.from_request(request)
        cached = repository.get_benchmarking_financials(
            cache_key,
            scope=cache_key.repository_scope,
            as_of=self._clock(),
        )
        if cached is not None and not refresh_requested:
            return BenchmarkingFinancialsLoadResult(
                source=FundamentalEvidenceSource.CACHE,
                result=cached.result,
                stored_document=cached,
            )

        try:
            result = gateway.retrieve_benchmarking_financials(request=request)
        except FundamentalGatewayError:
            if cached is None:
                raise
            return BenchmarkingFinancialsLoadResult(
                source=FundamentalEvidenceSource.CACHE,
                result=cached.result,
                stored_document=cached,
                refresh_requested=True,
                refresh_failure_status=(
                    FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE
                ),
            )
        if not isinstance(result, FundamentalBenchmarkingFinancialsResult):
            raise ValueError(
                "Benchmarking Financials gateway returned an invalid result"
            )
        validate_fundamental_response_binding(request, result)

        if result.status is not FundamentalRetrievalStatus.COMPLETED:
            if cached is not None and refresh_requested:
                return BenchmarkingFinancialsLoadResult(
                    source=FundamentalEvidenceSource.CACHE,
                    result=cached.result,
                    stored_document=cached,
                    refresh_requested=True,
                    refresh_failure_status=result.status,
                )
            return BenchmarkingFinancialsLoadResult(
                source=FundamentalEvidenceSource.PROVIDER,
                result=result,
                refresh_requested=refresh_requested,
            )

        candidate = stored_benchmarking_financials_document(
            request,
            result,
            stored_at=self._clock(),
            retention=self._retention,
        )
        if refresh_requested:
            persisted = repository.replace_benchmarking_financials(
                candidate,
                scope=cache_key.repository_scope,
            )
        else:
            persisted = repository.save_benchmarking_financials(candidate)
        return BenchmarkingFinancialsLoadResult(
            source=FundamentalEvidenceSource.PROVIDER,
            result=result,
            stored_document=persisted,
            refresh_requested=refresh_requested,
        )

    def publish_complete_package(
        self,
        *,
        operation_id: str,
        evidence_loads: tuple[FundamentalEvidenceLoadResult, ...],
        structured_document_loads: tuple[StructuredDocumentLoadResult, ...],
        peer_comparison_load: PeerComparisonLoadResult,
        benchmarking_financials_load: BenchmarkingFinancialsLoadResult,
    ) -> FundamentalEvidencePackage:
        """Atomically activate one refresh only after every dataset succeeds."""

        repository = self._package_repository
        if repository is None:
            raise RuntimeError("fundamental package repository is not configured")
        if len(evidence_loads) != 3 or len(structured_document_loads) != 11:
            raise ValueError(
                "fundamental package publication requires all sixteen datasets"
            )

        snapshots = tuple(
            self._require_successful_provider_artifact(
                load,
                load.stored_snapshot,
                dataset="fundamental snapshot",
            )
            for load in evidence_loads
        )
        structured_documents = tuple(
            self._require_successful_provider_artifact(
                load,
                load.stored_document,
                dataset="structured financial document",
            )
            for load in structured_document_loads
        )
        peer_comparison = self._require_successful_provider_artifact(
            peer_comparison_load,
            peer_comparison_load.stored_document,
            dataset="Peer Comparison",
        )
        benchmarking_financials = self._require_successful_provider_artifact(
            benchmarking_financials_load,
            benchmarking_financials_load.stored_document,
            dataset="Benchmarking Financials",
        )
        artifacts = (
            *snapshots,
            *structured_documents,
            peer_comparison,
            benchmarking_financials,
        )
        if any(
            artifact.request.operation_id != operation_id
            for artifact in artifacts
        ):
            raise ValueError(
                "fundamental package artifacts must belong to the refresh operation"
            )

        committed_at = self._clock()
        requested_at = min(
            artifact.request.requested_at for artifact in artifacts
        )
        items = tuple(
            self._fundamental_package_item(artifact)
            for artifact in artifacts
        )
        manifest = FundamentalPackageManifest(
            operation_id=operation_id,
            connection=artifacts[0].request.connection,
            issuer=artifacts[0].request.issuer,
            requested_at=requested_at,
            committed_at=committed_at,
            items=items,
        )
        package = FundamentalEvidencePackage(
            manifest=manifest,
            snapshots=snapshots,
            structured_documents=structured_documents,
            peer_comparison=peer_comparison,
            benchmarking_financials=benchmarking_financials,
        )
        scope = FundamentalPackageRepositoryScope.from_package(package)
        active = repository.get_active_fundamental_package(
            scope=scope,
            as_of=committed_at,
        )
        return repository.commit_fundamental_package(
            package,
            expected_active_package_id=(
                active.package_id if active is not None else None
            ),
        )

    @staticmethod
    def _require_successful_provider_artifact(load, artifact, *, dataset: str):
        result = getattr(load, "retrieval", None) or getattr(
            load,
            "result",
            None,
        )
        if (
            load.source is not FundamentalEvidenceSource.PROVIDER
            or result is None
            or result.status not in {
                FundamentalRetrievalStatus.COMPLETED,
                FundamentalRetrievalStatus.PARTIAL,
            }
            or artifact is None
        ):
            raise ValueError(
                f"{dataset} did not complete through the provider; "
                "active package was preserved"
            )
        return artifact

    @staticmethod
    def _fundamental_package_item(artifact) -> FundamentalPackageItem:
        if isinstance(artifact, StoredFundamentalSnapshot):
            dataset = FundamentalPackageDataset(
                artifact.request.capability.value
            )
            reporting_basis = FinancialReportingBasis.NOT_APPLICABLE
            content_fingerprint = artifact.snapshot_fingerprint
        elif isinstance(artifact, StoredStructuredFinancialDocument):
            dataset = FundamentalPackageDataset(
                artifact.request.document_type.value
            )
            reporting_basis = artifact.request.reporting_basis
            content_fingerprint = artifact.document_fingerprint
        elif isinstance(artifact, StoredPeerComparisonDocument):
            dataset = FundamentalPackageDataset.PEER_COMPARISON
            reporting_basis = FinancialReportingBasis.NOT_APPLICABLE
            content_fingerprint = artifact.document_fingerprint
        elif isinstance(artifact, StoredBenchmarkingFinancialsDocument):
            dataset = FundamentalPackageDataset.BENCHMARKING_FINANCIALS
            reporting_basis = FinancialReportingBasis.NOT_APPLICABLE
            content_fingerprint = artifact.document_fingerprint
        else:
            raise TypeError("unsupported fundamental package artifact")
        return FundamentalPackageItem(
            key=FundamentalPackageItemKey(
                dataset=dataset,
                reporting_basis=reporting_basis,
            ),
            cache_entry_id=artifact.cache_key.cache_entry_id,
            content_fingerprint=content_fingerprint,
            retrieved_at=artifact.retrieved_at,
            expires_at=artifact.expires_at,
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
