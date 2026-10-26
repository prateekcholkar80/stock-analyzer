from datetime import timedelta
import unittest

from app.exceptions import FundamentalProviderUnavailableError
from app.gateways.fundamentals import (
    FundamentalBenchmarkingFinancialsResult,
    FundamentalCapability,
    FundamentalCompanyOverviewRequest,
    FundamentalEvidenceRetrieval,
    FundamentalFinancialDocumentPackage,
    FundamentalFinancialDocumentPackageRequest,
    FundamentalFinancialDocumentPackageResult,
    FundamentalFinancialsRequest,
    FundamentalIssuerCandidate,
    FundamentalIssuerLocator,
    FundamentalIssuerMatchKind,
    FundamentalIssuerResolutionRequest,
    FundamentalIssuerResolutionResult,
    FundamentalPeerComparisonResult,
    FundamentalResolutionStatus,
    FundamentalRetrievalStatus,
    FundamentalShareholdingRequest,
    FundamentalStructuredDocumentRequest,
    FundamentalStructuredDocumentResult,
)
from decimal import Decimal
from app.models.financial_documents import (
    FinancialDocumentType,
    FinancialReportingBasis,
)
from app.models.fundamentals import FundamentalStatement
from app.services.fundamental_evidence import (
    FundamentalEvidenceCoordinator,
    FundamentalEvidenceSource,
)
from app.storage.adapters.fundamental_in_memory import (
    InMemoryFundamentalSnapshotRepository,
)
from app.storage.adapters.benchmarking_financials_in_memory import (
    InMemoryBenchmarkingFinancialsRepository,
)
from app.storage.adapters.financial_document_in_memory import (
    InMemoryStructuredFinancialDocumentRepository,
)
from app.storage.adapters.peer_comparison_in_memory import (
    InMemoryPeerComparisonRepository,
)
from app.storage.adapters.fundamental_package_in_memory import (
    InMemoryFundamentalPackageRepository,
)
from app.storage.adapters.fundamental_filing_evidence_in_memory import (
    InMemoryFundamentalFilingEvidenceRepository,
)
from app.storage.fundamental_package_repositories import (
    FundamentalPackageRepositoryScope,
)
from tests.unit.test_fundamental_gateway import (
    ADAPTER_HASH,
    REQUESTED_AT,
    build_connection,
    build_issuer,
    build_snapshot,
)
from tests.unit.test_fundamental_in_memory_repository import (
    MutableClock,
    build_entry,
)
from tests.unit.test_fundamental_storage_contracts import (
    build_financial_request,
    build_retrieval,
)
from tests.unit.test_structured_financial_gateway import document
from tests.unit.test_peer_comparison_gateway import (
    document as peer_document,
    request as peer_request,
    result_values as peer_result_values,
)
from tests.unit.test_benchmarking_financials_gateway import (
    document as benchmarking_document,
    request as benchmarking_request,
    result_values as benchmarking_result_values,
)


class FakeFundamentalGateway:
    configuration_fingerprint = "f" * 64

    def __init__(self, *, failure: bool = False, wrong_binding: bool = False):
        self.failure = failure
        self.wrong_binding = wrong_binding
        self.calls = []

    def inspect_capabilities(self, *, connection):
        raise AssertionError("capability inspection is not expected")

    def search_companies(self, *, request):
        raise AssertionError("company search is not expected")

    def resolve_issuer(self, *, request):
        self.calls.append("resolve_issuer")
        issuer = build_issuer(
            exchange=request.locator.exchange or "NSE",
            symbol=request.locator.symbol or "TCS-EQ",
        )
        candidate = FundamentalIssuerCandidate(
            issuer=issuer,
            match_kind=FundamentalIssuerMatchKind.EXACT_SYMBOL,
            match_score=Decimal("1"),
            matched_on=("exchange", "symbol"),
            provider_record_fingerprint="c" * 64,
        )
        return FundamentalIssuerResolutionResult(
            capability=FundamentalCapability.ISSUER_RESOLUTION,
            request_id=request.request_id,
            request_fingerprint=request.request_fingerprint,
            connection=request.connection,
            requested_at=request.requested_at,
            started_at=request.requested_at,
            completed_at=request.requested_at + timedelta(seconds=2),
            provider_contract_version="tijori.contract.v1",
            adapter_fingerprint=ADAPTER_HASH,
            status=FundamentalResolutionStatus.RESOLVED,
            issuer=issuer,
            candidates=(candidate,),
        )

    def retrieve_company_overview(self, *, request):
        self.calls.append("overview")
        return self._result(request, FundamentalStatement.COMPANY_PROFILE)

    def retrieve_financials(self, *, request):
        self.calls.append("financials")
        if not self.failure and not self.wrong_binding:
            return build_retrieval(
                request,
                started_at=request.requested_at,
                completed_at=request.requested_at + timedelta(seconds=2),
            )
        return self._result(request, FundamentalStatement.INCOME_STATEMENT)

    def retrieve_shareholding(self, *, request):
        self.calls.append("shareholding")
        return self._result(request, FundamentalStatement.OWNERSHIP)

    def retrieve_structured_financial_document(self, *, request):
        self.calls.append("structured_document")
        bound_request = request
        if self.wrong_binding:
            bound_request = request.model_copy(
                update={"request_id": f"{request.request_id}.wrong"}
            )
        result_document = document(
            document_id="provider.NSE.TCS.growth_table.not_applicable",
            connection=request.connection,
            issuer=request.issuer,
            document_type=request.document_type,
            reporting_basis=request.reporting_basis,
            retrieved_at=request.requested_at + timedelta(seconds=1),
            expires_at=request.requested_at + timedelta(days=10),
        )
        if self.failure:
            return FundamentalStructuredDocumentResult(
                capability=request.capability,
                request_id=request.request_id,
                request_fingerprint=request.request_fingerprint,
                connection=request.connection,
                requested_at=request.requested_at,
                started_at=request.requested_at,
                completed_at=request.requested_at + timedelta(seconds=2),
                provider_contract_version="provider.contract.v1",
                adapter_fingerprint=ADAPTER_HASH,
                issuer=request.issuer,
                document_type=request.document_type,
                reporting_basis=request.reporting_basis,
                status=FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
                document=None,
                limitations=("Provider unavailable.",),
            )
        return FundamentalStructuredDocumentResult(
            capability=request.capability,
            request_id=bound_request.request_id,
            request_fingerprint=bound_request.request_fingerprint,
            connection=request.connection,
            requested_at=request.requested_at,
            started_at=request.requested_at,
            completed_at=request.requested_at + timedelta(seconds=2),
            provider_contract_version="provider.contract.v1",
            adapter_fingerprint=ADAPTER_HASH,
            issuer=request.issuer,
            document_type=request.document_type,
            reporting_basis=request.reporting_basis,
            status=FundamentalRetrievalStatus.COMPLETED,
            document=result_document,
        )

    def retrieve_financial_document_package(self, *, request):
        self.calls.append("financial_package")
        common = {
            "request_id": request.request_id,
            "request_fingerprint": request.request_fingerprint,
            "connection": request.connection,
            "requested_at": request.requested_at,
            "started_at": request.requested_at,
            "completed_at": request.requested_at + timedelta(seconds=2),
            "provider_contract_version": "provider.contract.v1",
            "adapter_fingerprint": ADAPTER_HASH,
            "issuer": request.issuer,
        }
        if self.failure:
            return FundamentalFinancialDocumentPackageResult(
                **common,
                status=FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
                limitations=("Provider unavailable.",),
            )
        retrieved_at = request.requested_at + timedelta(seconds=1)
        scenarios = (
            (
                FinancialDocumentType.GROWTH_TABLE,
                FinancialReportingBasis.NOT_APPLICABLE,
            ),
            *(
                (document_type, reporting_basis)
                for document_type in (
                    FinancialDocumentType.BALANCE_SHEET,
                    FinancialDocumentType.PROFIT_AND_LOSS,
                    FinancialDocumentType.CASH_FLOW,
                    FinancialDocumentType.RATIOS,
                    FinancialDocumentType.QUARTERLY_RESULTS,
                )
                for reporting_basis in (
                    FinancialReportingBasis.CONSOLIDATED,
                    FinancialReportingBasis.STANDALONE,
                )
            ),
        )
        documents = tuple(
            document(
                document_id=(
                    "provider.NSE.TCS."
                    f"{document_type.value}.{reporting_basis.value}"
                ),
                connection=request.connection,
                issuer=request.issuer,
                document_type=document_type,
                reporting_basis=reporting_basis,
                retrieved_at=retrieved_at,
                expires_at=retrieved_at + timedelta(days=10),
            )
            for document_type, reporting_basis in scenarios
        )
        package = FundamentalFinancialDocumentPackage(
            connection=request.connection,
            issuer=request.issuer,
            source_location=documents[0].source_location,
            retrieved_at=retrieved_at,
            expires_at=retrieved_at + timedelta(days=10),
            documents=documents,
        )
        return FundamentalFinancialDocumentPackageResult(
            **common,
            status=FundamentalRetrievalStatus.COMPLETED,
            package=package,
        )

    def retrieve_peer_comparison(self, *, request):
        self.calls.append("peer_comparison")
        bound_request = request
        if self.wrong_binding:
            bound_request = request.model_copy(
                update={"request_id": f"{request.request_id}.wrong"}
            )
        if self.failure:
            values = peer_result_values(request)
            values.update(
                {
                    "requested_at": request.requested_at,
                    "started_at": request.requested_at,
                    "completed_at": (
                        request.requested_at + timedelta(seconds=2)
                    ),
                    "status": FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
                    "document": None,
                    "limitations": ("Provider unavailable.",),
                }
            )
            return FundamentalPeerComparisonResult(**values)
        retrieved_at = request.requested_at + timedelta(seconds=1)
        result_document = peer_document(
            connection=request.connection,
            issuer=request.issuer,
            observation_date=request.as_of_date,
            retrieved_at=retrieved_at,
            expires_at=retrieved_at + timedelta(days=10),
        )
        values = peer_result_values(bound_request)
        values.update(
            {
                "connection": request.connection,
                "requested_at": request.requested_at,
                "started_at": request.requested_at,
                "completed_at": request.requested_at + timedelta(seconds=2),
                "issuer": request.issuer,
                "document": result_document,
            }
        )
        return FundamentalPeerComparisonResult(**values)

    def retrieve_benchmarking_financials(self, *, request):
        self.calls.append("benchmarking_financials")
        bound_request = request
        if self.wrong_binding:
            bound_request = request.model_copy(
                update={"request_id": f"{request.request_id}.wrong"}
            )
        if self.failure:
            values = benchmarking_result_values(request)
            values.update(
                {
                    "requested_at": request.requested_at,
                    "started_at": request.requested_at,
                    "completed_at": (
                        request.requested_at + timedelta(seconds=2)
                    ),
                    "status": FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
                    "document": None,
                    "limitations": ("Provider unavailable.",),
                }
            )
            return FundamentalBenchmarkingFinancialsResult(**values)
        retrieved_at = request.requested_at + timedelta(seconds=1)
        result_document = benchmarking_document(
            connection=request.connection,
            issuer=request.issuer,
            observation_date=request.as_of_date,
            retrieved_at=retrieved_at,
            expires_at=retrieved_at + timedelta(days=10),
        )
        values = benchmarking_result_values(bound_request)
        values.update(
            {
                "connection": request.connection,
                "requested_at": request.requested_at,
                "started_at": request.requested_at,
                "completed_at": request.requested_at + timedelta(seconds=2),
                "issuer": request.issuer,
                "document": result_document,
            }
        )
        return FundamentalBenchmarkingFinancialsResult(**values)

    def _result(self, request, statement):
        bound_request = request
        if self.wrong_binding:
            bound_request = request.model_copy(
                update={"request_id": f"{request.request_id}.wrong"}
            )
        common = {
            "capability": request.capability,
            "request_id": bound_request.request_id,
            "request_fingerprint": bound_request.request_fingerprint,
            "connection": request.connection,
            "requested_at": request.requested_at,
            "started_at": request.requested_at,
            "completed_at": request.requested_at + timedelta(seconds=2),
            "provider_contract_version": "tijori.contract.v1",
            "adapter_fingerprint": ADAPTER_HASH,
            "issuer": request.issuer,
        }
        if self.failure:
            return FundamentalEvidenceRetrieval(
                **common,
                status=FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
                limitations=("Provider unavailable; no evidence returned",),
            )
        return FundamentalEvidenceRetrieval(
            **common,
            status=FundamentalRetrievalStatus.COMPLETED,
            snapshot=build_snapshot(
                statement,
                connection=request.connection,
                issuer=request.issuer,
                assembled_at=request.requested_at + timedelta(seconds=1),
            ),
        )


def build_request(request_type, *, suffix="base", requested_at=REQUESTED_AT):
    return request_type(
        request_id=f"request.{suffix}",
        operation_id=f"operation.{suffix}",
        connection=build_connection(),
        requested_at=requested_at,
        issuer=build_issuer(),
        as_of_date=REQUESTED_AT.date(),
    )


class FundamentalEvidenceCoordinatorTests(unittest.TestCase):
    def setUp(self):
        self.stored = build_entry()
        self.clock = MutableClock(self.stored.stored_at + timedelta(hours=1))
        self.repository = InMemoryFundamentalSnapshotRepository(
            clock=self.clock
        )
        self.document_repository = (
            InMemoryStructuredFinancialDocumentRepository(clock=self.clock)
        )
        self.peer_repository = InMemoryPeerComparisonRepository(
            clock=self.clock
        )
        self.benchmarking_repository = (
            InMemoryBenchmarkingFinancialsRepository(clock=self.clock)
        )
        self.package_repository = InMemoryFundamentalPackageRepository(
            clock=self.clock
        )
        self.filing_evidence_repository = (
            InMemoryFundamentalFilingEvidenceRepository(clock=self.clock)
        )

    def coordinator(self, gateway):
        return FundamentalEvidenceCoordinator(
            gateway=gateway,
            repository=self.repository,
            structured_document_repository=self.document_repository,
            peer_comparison_repository=self.peer_repository,
            benchmarking_financials_repository=self.benchmarking_repository,
            package_repository=self.package_repository,
            filing_evidence_repository=self.filing_evidence_repository,
            clock=self.clock,
        )

    def complete_refresh(
        self,
        coordinator,
        *,
        sequence=1,
        refresh_requested=False,
    ):
        requested_at = REQUESTED_AT + timedelta(minutes=sequence)
        self.clock.now = requested_at + timedelta(seconds=3)
        operation_id = f"operation.package.{sequence}"
        issuer = build_issuer(
            symbol="COFORGE",
            legal_name="Coforge Ltd.",
            provider_company_id="4502",
            provider_slug="niit-technologies-limited",
        )
        common = {
            "operation_id": operation_id,
            "connection": build_connection(),
            "requested_at": requested_at,
            "issuer": issuer,
            "as_of_date": requested_at.date(),
        }
        evidence_requests = (
            FundamentalCompanyOverviewRequest(
                request_id=f"request.overview.{sequence}",
                **common,
            ),
            build_financial_request(
                request_id=f"request.financials.{sequence}",
                **common,
            ),
            FundamentalShareholdingRequest(
                request_id=f"request.shareholding.{sequence}",
                quarters=8,
                include_promoter_pledge=True,
                **common,
            ),
        )
        evidence_loads = tuple(
            coordinator.load(
                request,
                refresh_requested=refresh_requested,
            )
            for request in evidence_requests
        )
        scenarios = (
            (
                FinancialDocumentType.GROWTH_TABLE,
                FinancialReportingBasis.NOT_APPLICABLE,
            ),
            *(
                (document_type, reporting_basis)
                for document_type in (
                    FinancialDocumentType.BALANCE_SHEET,
                    FinancialDocumentType.PROFIT_AND_LOSS,
                    FinancialDocumentType.CASH_FLOW,
                    FinancialDocumentType.RATIOS,
                    FinancialDocumentType.QUARTERLY_RESULTS,
                )
                for reporting_basis in (
                    FinancialReportingBasis.CONSOLIDATED,
                    FinancialReportingBasis.STANDALONE,
                )
            ),
        )
        structured_loads = tuple(
            coordinator.load_structured_document(
                FundamentalStructuredDocumentRequest(
                    request_id=f"request.document.{sequence}.{index}",
                    document_type=document_type,
                    reporting_basis=reporting_basis,
                    **common,
                ),
                refresh_requested=refresh_requested,
            )
            for index, (document_type, reporting_basis) in enumerate(scenarios)
        )
        peer_load = coordinator.load_peer_comparison(
            FundamentalCompanyOverviewRequest(
                request_id=f"request.peers.{sequence}",
                **common,
            ),
            refresh_requested=refresh_requested,
        )
        benchmarking_load = coordinator.load_benchmarking_financials(
            FundamentalCompanyOverviewRequest(
                request_id=f"request.benchmarking.{sequence}",
                **common,
            ),
            refresh_requested=refresh_requested,
        )
        return (
            operation_id,
            evidence_loads,
            structured_loads,
            peer_load,
            benchmarking_load,
        )

    def publish_refresh(self, coordinator, refresh):
        operation_id, evidence, structured, peers, benchmarking = refresh
        return coordinator.publish_complete_package(
            operation_id=operation_id,
            evidence_loads=evidence,
            structured_document_loads=structured,
            peer_comparison_load=peers,
            benchmarking_financials_load=benchmarking,
        )

    def test_complete_refresh_is_assembled_and_atomically_published(self):
        coordinator = self.coordinator(FakeFundamentalGateway())
        package = self.publish_refresh(
            coordinator,
            self.complete_refresh(coordinator),
        )
        scope = FundamentalPackageRepositoryScope.from_package(package)

        self.assertEqual(len(package.manifest.items), 16)
        self.assertEqual(
            self.package_repository.get_active_fundamental_package(
                scope=scope,
                as_of=self.clock.now,
            ),
            package,
        )

    def test_loads_only_the_scoped_active_complete_package(self):
        coordinator = self.coordinator(FakeFundamentalGateway())
        package = self.publish_refresh(
            coordinator,
            self.complete_refresh(coordinator),
        )

        loaded = coordinator.load_active_package(
            connection=package.manifest.connection,
            issuer=package.manifest.issuer,
        )
        missing = coordinator.load_active_package(
            connection=package.manifest.connection,
            issuer=package.manifest.issuer.model_copy(
                update={"symbol": "RELIANCE"}
            ),
        )

        self.assertEqual(loaded, package)
        self.assertIsNone(missing)

    def test_active_package_read_requires_configured_repository(self):
        coordinator = FundamentalEvidenceCoordinator(
            gateway=FakeFundamentalGateway(),
            repository=self.repository,
            clock=self.clock,
        )

        with self.assertRaisesRegex(RuntimeError, "not configured"):
            coordinator.load_active_package(
                connection=build_connection(),
                issuer=build_issuer(),
            )

    def test_validated_partial_snapshot_can_publish_complete_package(self):
        coordinator = self.coordinator(FakeFundamentalGateway())
        refresh = self.complete_refresh(coordinator)
        operation_id, evidence, structured, peers, benchmarking = refresh
        partial_retrieval = evidence[0].retrieval.model_copy(
            update={"status": FundamentalRetrievalStatus.PARTIAL}
        )
        partial_snapshot = evidence[0].stored_snapshot.model_copy(
            update={"retrieval": partial_retrieval}
        )
        partial_load = evidence[0].model_copy(
            update={
                "retrieval": partial_retrieval,
                "stored_snapshot": partial_snapshot,
            }
        )

        package = coordinator.publish_complete_package(
            operation_id=operation_id,
            evidence_loads=(partial_load, *evidence[1:]),
            structured_document_loads=structured,
            peer_comparison_load=peers,
            benchmarking_financials_load=benchmarking,
        )

        self.assertEqual(
            package.snapshots[0].retrieval.status,
            FundamentalRetrievalStatus.PARTIAL,
        )

    def test_incomplete_refresh_preserves_previous_active_package(self):
        coordinator = self.coordinator(FakeFundamentalGateway())
        first = self.publish_refresh(
            coordinator,
            self.complete_refresh(coordinator),
        )
        second = self.complete_refresh(
            coordinator,
            sequence=2,
            refresh_requested=True,
        )
        operation_id, evidence, structured, peers, benchmarking = second

        with self.assertRaisesRegex(ValueError, "all sixteen datasets"):
            coordinator.publish_complete_package(
                operation_id=operation_id,
                evidence_loads=evidence,
                structured_document_loads=structured[:-1],
                peer_comparison_load=peers,
                benchmarking_financials_load=benchmarking,
            )

        scope = FundamentalPackageRepositoryScope.from_package(first)
        self.assertEqual(
            self.package_repository.get_active_fundamental_package(
                scope=scope,
                as_of=self.clock.now,
            ),
            first,
        )

    def test_cache_fallback_or_mixed_operation_cannot_publish(self):
        coordinator = self.coordinator(FakeFundamentalGateway())
        refresh = self.complete_refresh(coordinator)
        operation_id, evidence, structured, peers, benchmarking = refresh
        cached_document = structured[0].model_copy(
            update={"source": FundamentalEvidenceSource.CACHE}
        )
        with self.assertRaisesRegex(ValueError, "did not complete"):
            coordinator.publish_complete_package(
                operation_id=operation_id,
                evidence_loads=evidence,
                structured_document_loads=(
                    cached_document,
                    *structured[1:],
                ),
                peer_comparison_load=peers,
                benchmarking_financials_load=benchmarking,
            )
        with self.assertRaisesRegex(ValueError, "refresh operation"):
            coordinator.publish_complete_package(
                operation_id="operation.wrong",
                evidence_loads=evidence,
                structured_document_loads=structured,
                peer_comparison_load=peers,
                benchmarking_financials_load=benchmarking,
            )

        scope = FundamentalPackageRepositoryScope(
            connection=build_connection(),
            issuer=build_issuer(
                symbol="COFORGE",
                legal_name="Coforge Ltd.",
                provider_company_id="4502",
                provider_slug="niit-technologies-limited",
            ),
        )
        self.assertIsNone(
            self.package_repository.get_active_fundamental_package(
                scope=scope,
                as_of=self.clock.now,
            )
        )

    def test_benchmarking_financials_reuses_fresh_cached_json(self):
        gateway = FakeFundamentalGateway()
        source_request = benchmarking_request()
        self.clock.now = source_request.requested_at + timedelta(seconds=3)
        coordinator = self.coordinator(gateway)

        first = coordinator.load_benchmarking_financials(source_request)
        gateway.calls.clear()
        second_request = source_request.model_copy(
            update={
                "request_id": "request.coforge.benchmarking.second",
                "operation_id": "operation.coforge.benchmarking.second",
            }
        )
        second = coordinator.load_benchmarking_financials(second_request)

        self.assertIs(first.source, FundamentalEvidenceSource.PROVIDER)
        self.assertIs(second.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(second.stored_document, first.stored_document)
        self.assertEqual(gateway.calls, [])

    def test_benchmarking_refresh_atomically_replaces_newer_json(self):
        gateway = FakeFundamentalGateway()
        first_request = benchmarking_request()
        self.clock.now = first_request.requested_at + timedelta(seconds=3)
        coordinator = self.coordinator(gateway)
        first = coordinator.load_benchmarking_financials(first_request)
        refreshed_request = first_request.model_copy(
            update={
                "request_id": "request.coforge.benchmarking.refresh",
                "operation_id": "operation.coforge.benchmarking.refresh",
                "requested_at": first_request.requested_at
                + timedelta(hours=2),
            }
        )
        self.clock.now = refreshed_request.requested_at + timedelta(seconds=3)

        refreshed = coordinator.load_benchmarking_financials(
            refreshed_request,
            refresh_requested=True,
        )

        self.assertIs(refreshed.source, FundamentalEvidenceSource.PROVIDER)
        self.assertTrue(refreshed.refresh_requested)
        self.assertGreater(
            refreshed.stored_document.retrieved_at,
            first.stored_document.retrieved_at,
        )

    def test_failed_benchmarking_refresh_preserves_active_json(self):
        gateway = FakeFundamentalGateway()
        first_request = benchmarking_request()
        self.clock.now = first_request.requested_at + timedelta(seconds=3)
        coordinator = self.coordinator(gateway)
        active = coordinator.load_benchmarking_financials(first_request)
        gateway.failure = True
        failed_request = first_request.model_copy(
            update={
                "request_id": "request.coforge.benchmarking.failed",
                "operation_id": "operation.coforge.benchmarking.failed",
                "requested_at": first_request.requested_at
                + timedelta(hours=2),
            }
        )
        self.clock.now = failed_request.requested_at + timedelta(seconds=3)

        fallback = coordinator.load_benchmarking_financials(
            failed_request,
            refresh_requested=True,
        )
        preserved = self.benchmarking_repository.get_benchmarking_financials(
            active.stored_document.cache_key,
            scope=active.stored_document.cache_key.repository_scope,
            as_of=self.clock.now,
        )

        self.assertIs(fallback.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(fallback.stored_document, active.stored_document)
        self.assertEqual(preserved, active.stored_document)
        self.assertIs(
            fallback.refresh_failure_status,
            FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
        )

    def test_benchmarking_gateway_exception_returns_active_cached_json(self):
        source_request = benchmarking_request()
        self.clock.now = source_request.requested_at + timedelta(seconds=3)
        active = self.coordinator(
            FakeFundamentalGateway()
        ).load_benchmarking_financials(source_request)

        class UnavailableGateway(FakeFundamentalGateway):
            def retrieve_benchmarking_financials(self, *, request):
                raise FundamentalProviderUnavailableError(
                    "Benchmarking Financials provider unavailable",
                    provider="tijori",
                    provider_connection_id=(
                        request.connection.provider_connection_id
                    ),
                )

        refresh_request = source_request.model_copy(
            update={
                "request_id": "request.coforge.benchmarking.exception",
                "operation_id": "operation.coforge.benchmarking.exception",
                "requested_at": source_request.requested_at
                + timedelta(hours=2),
            }
        )
        self.clock.now = refresh_request.requested_at + timedelta(seconds=3)

        fallback = self.coordinator(
            UnavailableGateway()
        ).load_benchmarking_financials(
            refresh_request,
            refresh_requested=True,
        )

        self.assertIs(fallback.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(fallback.stored_document, active.stored_document)
        self.assertIs(
            fallback.refresh_failure_status,
            FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
        )

    def test_peer_comparison_reuses_fresh_cached_json(self):
        gateway = FakeFundamentalGateway()
        source_request = peer_request()
        self.clock.now = source_request.requested_at + timedelta(seconds=3)
        coordinator = self.coordinator(gateway)

        first = coordinator.load_peer_comparison(source_request)
        gateway.calls.clear()
        second_request = source_request.model_copy(
            update={
                "request_id": "request.coforge.peer-comparison.second",
                "operation_id": "operation.coforge.fundamentals.second",
            }
        )
        second = coordinator.load_peer_comparison(second_request)

        self.assertIs(first.source, FundamentalEvidenceSource.PROVIDER)
        self.assertIs(second.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(second.stored_document, first.stored_document)
        self.assertEqual(gateway.calls, [])

    def test_peer_comparison_refresh_atomically_replaces_newer_json(self):
        gateway = FakeFundamentalGateway()
        first_request = peer_request()
        self.clock.now = first_request.requested_at + timedelta(seconds=3)
        coordinator = self.coordinator(gateway)
        first = coordinator.load_peer_comparison(first_request)
        refreshed_request = first_request.model_copy(
            update={
                "request_id": "request.coforge.peer-comparison.refresh",
                "operation_id": "operation.coforge.fundamentals.refresh",
                "requested_at": first_request.requested_at + timedelta(hours=2),
            }
        )
        self.clock.now = refreshed_request.requested_at + timedelta(seconds=3)

        refreshed = coordinator.load_peer_comparison(
            refreshed_request,
            refresh_requested=True,
        )

        self.assertIs(refreshed.source, FundamentalEvidenceSource.PROVIDER)
        self.assertTrue(refreshed.refresh_requested)
        self.assertGreater(
            refreshed.stored_document.retrieved_at,
            first.stored_document.retrieved_at,
        )

    def test_failed_peer_refresh_returns_and_preserves_active_json(self):
        gateway = FakeFundamentalGateway()
        first_request = peer_request()
        self.clock.now = first_request.requested_at + timedelta(seconds=3)
        coordinator = self.coordinator(gateway)
        active = coordinator.load_peer_comparison(first_request)
        gateway.failure = True
        failed_request = first_request.model_copy(
            update={
                "request_id": "request.coforge.peer-comparison.failed",
                "operation_id": "operation.coforge.fundamentals.failed",
                "requested_at": first_request.requested_at + timedelta(hours=2),
            }
        )
        self.clock.now = failed_request.requested_at + timedelta(seconds=3)

        fallback = coordinator.load_peer_comparison(
            failed_request,
            refresh_requested=True,
        )
        preserved = self.peer_repository.get_peer_comparison(
            active.stored_document.cache_key,
            scope=active.stored_document.cache_key.repository_scope,
            as_of=self.clock.now,
        )

        self.assertIs(fallback.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(fallback.stored_document, active.stored_document)
        self.assertEqual(preserved, active.stored_document)
        self.assertIs(
            fallback.refresh_failure_status,
            FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
        )

    def test_peer_gateway_exception_also_returns_active_cached_json(self):
        source_request = peer_request()
        self.clock.now = source_request.requested_at + timedelta(seconds=3)
        active = self.coordinator(FakeFundamentalGateway()).load_peer_comparison(
            source_request
        )

        class UnavailableGateway(FakeFundamentalGateway):
            def retrieve_peer_comparison(self, *, request):
                raise FundamentalProviderUnavailableError(
                    "Peer Comparison provider unavailable",
                    provider="tijori",
                    provider_connection_id=(
                        request.connection.provider_connection_id
                    ),
                )

        refresh_request = source_request.model_copy(
            update={
                "request_id": "request.coforge.peer-comparison.exception",
                "operation_id": "operation.coforge.fundamentals.exception",
                "requested_at": source_request.requested_at + timedelta(hours=2),
            }
        )
        self.clock.now = refresh_request.requested_at + timedelta(seconds=3)

        fallback = self.coordinator(UnavailableGateway()).load_peer_comparison(
            refresh_request,
            refresh_requested=True,
        )

        self.assertIs(fallback.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(fallback.stored_document, active.stored_document)
        self.assertIs(
            fallback.refresh_failure_status,
            FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
        )

    def test_fresh_cache_hit_never_calls_provider(self):
        gateway = FakeFundamentalGateway()
        self.repository.save_fundamental_snapshot(self.stored)

        result = self.coordinator(gateway).load(self.stored.request)

        self.assertEqual(result.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(result.stored_snapshot, self.stored)
        self.assertEqual(gateway.calls, [])

    def test_resolves_provider_issuer_with_strict_response_binding(self):
        gateway = FakeFundamentalGateway()
        request = FundamentalIssuerResolutionRequest(
            request_id="request.resolve.tcs",
            operation_id="operation.resolve.tcs",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            locator=FundamentalIssuerLocator(
                exchange="NSE",
                symbol="TCS-EQ",
            ),
        )

        result = self.coordinator(gateway).resolve_issuer(request)

        self.assertEqual(gateway.calls, ["resolve_issuer"])
        self.assertIs(result.source, FundamentalEvidenceSource.PROVIDER)
        self.assertIs(
            result.provider_resolution.status,
            FundamentalResolutionStatus.RESOLVED,
        )
        self.assertEqual(result.issuer.legal_name, "Tata Consultancy Services Limited")

    def test_loads_growth_table_through_neutral_structured_gateway(self):
        gateway = FakeFundamentalGateway()
        request = FundamentalStructuredDocumentRequest(
            request_id="request.growth-table.tcs",
            operation_id="operation.growth-table.tcs",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            issuer=build_issuer(),
            document_type=FinancialDocumentType.GROWTH_TABLE,
            reporting_basis=FinancialReportingBasis.NOT_APPLICABLE,
        )

        loaded = self.coordinator(gateway).load_structured_document(request)

        self.assertEqual(gateway.calls, ["structured_document"])
        self.assertIs(
            loaded.result.document_type,
            FinancialDocumentType.GROWTH_TABLE,
        )
        self.assertEqual(loaded.result.document.issuer, request.issuer)
        self.assertIs(loaded.source, FundamentalEvidenceSource.PROVIDER)
        self.assertIsNotNone(loaded.stored_document)

    def test_refreshes_all_structured_documents_through_one_package_call(self):
        gateway = FakeFundamentalGateway()
        request = FundamentalFinancialDocumentPackageRequest(
            request_id="request.financial-package.tcs",
            operation_id="operation.financial-package.tcs",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            issuer=build_issuer(),
            as_of_date=REQUESTED_AT.date(),
        )

        loaded = self.coordinator(gateway).refresh_structured_document_package(
            request
        )

        self.assertEqual(gateway.calls, ["financial_package"])
        self.assertEqual(len(loaded), 11)
        self.assertTrue(all(item.refresh_requested for item in loaded))
        self.assertTrue(all(item.stored_document is not None for item in loaded))
        self.assertEqual(
            len({item.stored_document.retrieved_at for item in loaded}),
            1,
        )

    def test_failed_package_refresh_preserves_all_active_documents(self):
        gateway = FakeFundamentalGateway()
        coordinator = self.coordinator(gateway)
        first_request = FundamentalFinancialDocumentPackageRequest(
            request_id="request.financial-package.first",
            operation_id="operation.financial-package.first",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            issuer=build_issuer(),
        )
        active = coordinator.refresh_structured_document_package(first_request)
        gateway.failure = True
        later_request = first_request.model_copy(
            update={
                "request_id": "request.financial-package.failed",
                "operation_id": "operation.financial-package.failed",
                "requested_at": REQUESTED_AT + timedelta(hours=2),
            }
        )
        self.clock.now = later_request.requested_at + timedelta(seconds=3)

        with self.assertRaises(ValueError):
            coordinator.refresh_structured_document_package(later_request)

        for item in active:
            preserved = self.document_repository.get_structured_financial_document(
                item.stored_document.cache_key,
                scope=item.stored_document.cache_key.repository_scope,
                as_of=self.clock.now,
            )
            self.assertEqual(preserved, item.stored_document)

    def test_structured_load_reuses_fresh_cache_without_provider_call(self):
        gateway = FakeFundamentalGateway()
        request = FundamentalStructuredDocumentRequest(
            request_id="request.growth-table.cache.first",
            operation_id="operation.growth-table.cache.first",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            issuer=build_issuer(),
            document_type=FinancialDocumentType.GROWTH_TABLE,
            reporting_basis=FinancialReportingBasis.NOT_APPLICABLE,
        )
        coordinator = self.coordinator(gateway)
        first = coordinator.load_structured_document(request)
        gateway.calls.clear()
        second_request = request.model_copy(
            update={
                "request_id": "request.growth-table.cache.second",
                "operation_id": "operation.growth-table.cache.second",
            }
        )

        second = coordinator.load_structured_document(second_request)

        self.assertIs(first.source, FundamentalEvidenceSource.PROVIDER)
        self.assertIs(second.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(gateway.calls, [])
        self.assertEqual(second.stored_document, first.stored_document)

    def test_structured_refresh_atomically_replaces_newer_document(self):
        gateway = FakeFundamentalGateway()
        first_request = FundamentalStructuredDocumentRequest(
            request_id="request.growth-table.refresh.first",
            operation_id="operation.growth-table.refresh.first",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            issuer=build_issuer(),
            document_type=FinancialDocumentType.GROWTH_TABLE,
            reporting_basis=FinancialReportingBasis.NOT_APPLICABLE,
        )
        coordinator = self.coordinator(gateway)
        first = coordinator.load_structured_document(first_request)
        later = timedelta(hours=2)
        refreshed_request = FundamentalStructuredDocumentRequest(
            **{
                **first_request.model_dump(exclude_computed_fields=True),
                "request_id": "request.growth-table.refresh.second",
                "operation_id": "operation.growth-table.refresh.second",
                "requested_at": REQUESTED_AT + later,
            }
        )
        self.clock.now = refreshed_request.requested_at + timedelta(seconds=3)

        refreshed = coordinator.load_structured_document(
            refreshed_request,
            refresh_requested=True,
        )

        self.assertIs(refreshed.source, FundamentalEvidenceSource.PROVIDER)
        self.assertTrue(refreshed.refresh_requested)
        self.assertGreater(
            refreshed.stored_document.retrieved_at,
            first.stored_document.retrieved_at,
        )

    def test_failed_structured_refresh_preserves_active_cached_document(self):
        gateway = FakeFundamentalGateway()
        first_request = FundamentalStructuredDocumentRequest(
            request_id="request.growth-table.failure.first",
            operation_id="operation.growth-table.failure.first",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            issuer=build_issuer(),
            document_type=FinancialDocumentType.GROWTH_TABLE,
            reporting_basis=FinancialReportingBasis.NOT_APPLICABLE,
        )
        coordinator = self.coordinator(gateway)
        active = coordinator.load_structured_document(first_request)
        gateway.failure = True
        later = timedelta(hours=2)
        failed_request = FundamentalStructuredDocumentRequest(
            **{
                **first_request.model_dump(exclude_computed_fields=True),
                "request_id": "request.growth-table.failure.second",
                "operation_id": "operation.growth-table.failure.second",
                "requested_at": REQUESTED_AT + later,
            }
        )
        self.clock.now = failed_request.requested_at + timedelta(seconds=3)

        failed = coordinator.load_structured_document(
            failed_request,
            refresh_requested=True,
        )
        preserved = self.document_repository.get_structured_financial_document(
            active.stored_document.cache_key,
            scope=active.stored_document.cache_key.repository_scope,
            as_of=self.clock.now,
        )

        self.assertIs(
            failed.result.status,
            FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
        )
        self.assertIsNone(failed.stored_document)
        self.assertEqual(preserved, active.stored_document)

    def test_structured_load_rejects_misbound_or_unconfigured_gateway(self):
        request = FundamentalStructuredDocumentRequest(
            request_id="request.growth-table.invalid",
            operation_id="operation.growth-table.invalid",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            issuer=build_issuer(),
            document_type=FinancialDocumentType.GROWTH_TABLE,
            reporting_basis=FinancialReportingBasis.NOT_APPLICABLE,
        )
        with self.assertRaises(ValueError):
            self.coordinator(
                FakeFundamentalGateway(wrong_binding=True)
            ).load_structured_document(request)

        class LegacyOnlyGateway(FakeFundamentalGateway):
            retrieve_structured_financial_document = None

        with self.assertRaises(RuntimeError):
            self.coordinator(LegacyOnlyGateway()).load_structured_document(
                request
            )

    def test_reuses_fresh_cached_issuer_unless_refresh_is_explicit(self):
        gateway = FakeFundamentalGateway()
        self.repository.save_fundamental_snapshot(self.stored)
        request = FundamentalIssuerResolutionRequest(
            request_id="request.resolve.cached.tcs",
            operation_id="operation.resolve.cached.tcs",
            connection=self.stored.request.connection,
            requested_at=REQUESTED_AT,
            locator=FundamentalIssuerLocator(
                exchange="NSE",
                symbol="TCS-EQ",
            ),
        )

        cached = self.coordinator(gateway).resolve_issuer(request)
        refreshed = self.coordinator(gateway).resolve_issuer(
            request,
            refresh_requested=True,
        )

        self.assertIs(cached.source, FundamentalEvidenceSource.CACHE)
        self.assertEqual(cached.issuer, self.stored.cache_key.issuer)
        self.assertEqual(gateway.calls, ["resolve_issuer"])
        self.assertIs(refreshed.source, FundamentalEvidenceSource.PROVIDER)

    def test_cache_miss_dispatches_each_request_and_persists_evidence(self):
        cases = (
            (FundamentalCompanyOverviewRequest, "overview"),
            (FundamentalFinancialsRequest, "financials"),
            (FundamentalShareholdingRequest, "shareholding"),
        )
        for index, (request_type, expected_call) in enumerate(cases):
            with self.subTest(request_type=request_type.__name__):
                repository = InMemoryFundamentalSnapshotRepository(
                    clock=self.clock
                )
                gateway = FakeFundamentalGateway()
                coordinator = FundamentalEvidenceCoordinator(
                    gateway=gateway,
                    repository=repository,
                    clock=self.clock,
                )
                request = build_request(request_type, suffix=f"case.{index}")

                result = coordinator.load(request)

                self.assertEqual(
                    result.source,
                    FundamentalEvidenceSource.PROVIDER,
                )
                self.assertEqual(gateway.calls, [expected_call])
                self.assertIsNotNone(result.stored_snapshot)
                self.assertEqual(
                    repository.get_fundamental_snapshot(
                        result.decision.cache_key,
                        scope=result.decision.cache_key.repository_scope,
                        as_of=self.clock.now,
                    ),
                    result.stored_snapshot,
                )

    def test_failed_provider_result_is_not_cached(self):
        gateway = FakeFundamentalGateway(failure=True)
        request = build_request(FundamentalFinancialsRequest)

        result = self.coordinator(gateway).load(request)

        self.assertEqual(result.source, FundamentalEvidenceSource.PROVIDER)
        self.assertEqual(
            result.retrieval.status,
            FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
        )
        self.assertIsNone(result.stored_snapshot)
        self.assertIsNone(
            self.repository.get_fundamental_snapshot(
                result.decision.cache_key,
                scope=result.decision.cache_key.repository_scope,
                as_of=self.clock.now,
            )
        )

    def test_explicit_refresh_atomically_replaces_active_evidence(self):
        self.repository.save_fundamental_snapshot(self.stored)
        later = timedelta(hours=2)
        request = build_request(
            FundamentalFinancialsRequest,
            suffix="refresh",
            requested_at=REQUESTED_AT + later,
        )
        self.clock.now = request.requested_at + timedelta(seconds=3)
        gateway = FakeFundamentalGateway()

        result = self.coordinator(gateway).load(
            request,
            refresh_requested=True,
        )

        self.assertEqual(gateway.calls, ["financials"])
        self.assertEqual(result.decision.reason, "explicit_refresh")
        self.assertNotEqual(result.stored_snapshot, self.stored)
        self.assertEqual(
            self.repository.get_fundamental_snapshot(
                result.decision.cache_key,
                scope=result.decision.cache_key.repository_scope,
                as_of=self.clock.now,
            ),
            result.stored_snapshot,
        )

    def test_failed_explicit_refresh_preserves_existing_cache(self):
        self.repository.save_fundamental_snapshot(self.stored)
        later = timedelta(hours=2)
        request = build_request(
            FundamentalFinancialsRequest,
            suffix="refresh.failure",
            requested_at=REQUESTED_AT + later,
        )
        self.clock.now = request.requested_at + timedelta(seconds=3)

        result = self.coordinator(
            FakeFundamentalGateway(failure=True)
        ).load(request, refresh_requested=True)

        self.assertIsNone(result.stored_snapshot)
        self.assertEqual(
            self.repository.get_fundamental_snapshot(
                self.stored.cache_key,
                scope=self.stored.cache_key.repository_scope,
                as_of=self.clock.now,
            ),
            self.stored,
        )

    def test_misbound_provider_response_is_rejected_before_persistence(self):
        request = build_request(FundamentalFinancialsRequest)
        gateway = FakeFundamentalGateway(wrong_binding=True)

        with self.assertRaises(ValueError):
            self.coordinator(gateway).load(request)

        self.assertIsNone(
            self.repository.get_fundamental_snapshot(
                self.stored.cache_key,
                scope=self.stored.cache_key.repository_scope,
                as_of=self.clock.now,
            )
        )

    def test_rejects_invalid_dependencies_and_retention(self):
        gateway = FakeFundamentalGateway()
        with self.assertRaises(TypeError):
            FundamentalEvidenceCoordinator(
                gateway="invalid",
                repository=self.repository,
            )
        with self.assertRaises(TypeError):
            FundamentalEvidenceCoordinator(
                gateway=gateway,
                repository="invalid",
            )
        with self.assertRaises(ValueError):
            FundamentalEvidenceCoordinator(
                gateway=gateway,
                repository=self.repository,
                retention=timedelta(days=11),
            )
        with self.assertRaises(TypeError):
            FundamentalEvidenceCoordinator(
                gateway=gateway,
                repository=self.repository,
                structured_document_repository="invalid",
            )
        with self.assertRaisesRegex(TypeError, "filing evidence"):
            FundamentalEvidenceCoordinator(
                gateway=gateway,
                repository=self.repository,
                filing_evidence_repository="invalid",
            )

    def test_accepts_provider_neutral_filing_evidence_repository(self):
        coordinator = self.coordinator(FakeFundamentalGateway())

        self.assertIs(
            coordinator._filing_evidence_repository,
            self.filing_evidence_repository,
        )


if __name__ == "__main__":
    unittest.main()
