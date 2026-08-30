from datetime import timedelta
import unittest

from app.gateways.fundamentals import (
    FundamentalCapability,
    FundamentalCompanyOverviewRequest,
    FundamentalEvidenceRetrieval,
    FundamentalFinancialsRequest,
    FundamentalIssuerCandidate,
    FundamentalIssuerLocator,
    FundamentalIssuerMatchKind,
    FundamentalIssuerResolutionRequest,
    FundamentalIssuerResolutionResult,
    FundamentalResolutionStatus,
    FundamentalRetrievalStatus,
    FundamentalShareholdingRequest,
)
from decimal import Decimal
from app.models.fundamentals import FundamentalStatement
from app.services.fundamental_evidence import (
    FundamentalEvidenceCoordinator,
    FundamentalEvidenceSource,
)
from app.storage.adapters.fundamental_in_memory import (
    InMemoryFundamentalSnapshotRepository,
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
from tests.unit.test_fundamental_storage_contracts import build_retrieval


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

    def coordinator(self, gateway):
        return FundamentalEvidenceCoordinator(
            gateway=gateway,
            repository=self.repository,
            clock=self.clock,
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


if __name__ == "__main__":
    unittest.main()
