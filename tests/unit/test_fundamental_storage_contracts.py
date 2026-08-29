from datetime import datetime, timedelta
import unittest

from pydantic import ValidationError

from app.gateways.fundamentals import (
    FundamentalCapability,
    FundamentalCompanyOverviewRequest,
    FundamentalCompanySearchRequest,
    FundamentalEvidenceRetrieval,
    FundamentalFinancialsRequest,
    FundamentalRetrievalStatus,
    FundamentalShareholdingRequest,
)
from app.models.fundamental_storage import (
    FUNDAMENTAL_MAX_RETENTION,
    FundamentalRepositoryScope,
    FundamentalSnapshotCacheKey,
    FundamentalSnapshotQuery,
    StoredFundamentalSnapshot,
    fundamental_snapshot_summary,
    stored_fundamental_snapshot,
)
from app.models.fundamentals import (
    FundamentalPeriodType,
    FundamentalStatement,
)
from app.storage.fundamental_repositories import (
    FundamentalSnapshotRepository,
)
from tests.unit.test_fundamental_gateway import (
    COMPLETED_AT,
    IST,
    REQUESTED_AT,
    build_connection,
    build_issuer,
    build_snapshot,
    response_values,
)


STORED_AT = COMPLETED_AT + timedelta(seconds=1)


def rebuild(model, **overrides):
    values = model.model_dump(exclude_computed_fields=True)
    values.update(overrides)
    return type(model)(**values)


def build_financial_request(**overrides) -> FundamentalFinancialsRequest:
    values = {
        "request_id": "request.financials.tcs.cache",
        "operation_id": "operation.financials.tcs",
        "connection": build_connection(),
        "requested_at": REQUESTED_AT,
        "issuer": build_issuer(),
        "as_of_date": REQUESTED_AT.date(),
        "statements": (
            FundamentalStatement.INCOME_STATEMENT,
            FundamentalStatement.BALANCE_SHEET,
            FundamentalStatement.CASH_FLOW,
        ),
        "period_types": (
            FundamentalPeriodType.ANNUAL,
            FundamentalPeriodType.QUARTERLY,
        ),
        "max_periods": 12,
    }
    values.update(overrides)
    return FundamentalFinancialsRequest(**values)


def build_retrieval(
    request: FundamentalFinancialsRequest | None = None,
    **overrides,
) -> FundamentalEvidenceRetrieval:
    request = request or build_financial_request()
    values = response_values(
        request,
        capability=request.capability,
        issuer=request.issuer,
        status=FundamentalRetrievalStatus.COMPLETED,
        snapshot=build_snapshot(
            connection=request.connection,
            issuer=request.issuer,
        ),
    )
    values.update(overrides)
    return FundamentalEvidenceRetrieval(**values)


def build_stored(**overrides) -> StoredFundamentalSnapshot:
    request = overrides.pop("request", build_financial_request())
    retrieval = overrides.pop("retrieval", build_retrieval(request))
    stored = stored_fundamental_snapshot(
        request,
        retrieval,
        stored_at=overrides.pop("stored_at", STORED_AT),
        retention=overrides.pop(
            "retention",
            FUNDAMENTAL_MAX_RETENTION,
        ),
    )
    return rebuild(stored, **overrides) if overrides else stored


class FundamentalSnapshotCacheKeyTests(unittest.TestCase):
    def test_financial_key_preserves_semantic_scope(self):
        request = build_financial_request()
        key = FundamentalSnapshotCacheKey.from_request(request)

        self.assertEqual(key.tenant_id, "tenant.prateek")
        self.assertEqual(
            key.provider_connection_id,
            "provider.tijori.prateek",
        )
        self.assertEqual(
            key.capability,
            FundamentalCapability.FINANCIAL_STATEMENTS,
        )
        self.assertEqual(key.statements, request.statements)
        self.assertEqual(key.period_types, request.period_types)
        self.assertEqual(key.max_periods, 12)
        self.assertRegex(key.cache_key_fingerprint, r"^[a-f0-9]{64}$")
        self.assertEqual(
            key.cache_entry_id,
            f"fundamental:{key.cache_key_fingerprint}",
        )
        self.assertEqual(
            key.repository_scope,
            FundamentalRepositoryScope(
                tenant_id="tenant.prateek",
                provider_connection_id="provider.tijori.prateek",
                provider="tijori",
            ),
        )

    def test_execution_identity_and_time_do_not_change_semantic_key(self):
        first = build_financial_request()
        second = build_financial_request(
            request_id="request.financials.tcs.later",
            operation_id="operation.financials.tcs.later",
            requested_at=REQUESTED_AT + timedelta(hours=1),
        )

        self.assertNotEqual(
            first.request_fingerprint,
            second.request_fingerprint,
        )
        self.assertEqual(
            FundamentalSnapshotCacheKey.from_request(first),
            FundamentalSnapshotCacheKey.from_request(second),
        )

    def test_tenant_connection_and_query_scope_change_key(self):
        base = FundamentalSnapshotCacheKey.from_request(
            build_financial_request()
        )
        another_tenant = FundamentalSnapshotCacheKey.from_request(
            build_financial_request(
                connection=build_connection(
                    tenant_id="tenant.other",
                    provider_connection_id="provider.tijori.other",
                )
            )
        )
        shorter_history = FundamentalSnapshotCacheKey.from_request(
            build_financial_request(max_periods=8)
        )

        self.assertNotEqual(
            base.cache_key_fingerprint,
            another_tenant.cache_key_fingerprint,
        )
        self.assertNotEqual(
            base.cache_key_fingerprint,
            shorter_history.cache_key_fingerprint,
        )

    def test_overview_key_has_no_history_scope(self):
        request = FundamentalCompanyOverviewRequest(
            request_id="request.overview.tcs.cache",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            issuer=build_issuer(),
        )
        key = FundamentalSnapshotCacheKey.from_request(request)

        self.assertEqual(key.statements, ())
        self.assertEqual(key.period_types, ())
        self.assertIsNone(key.max_periods)
        self.assertIsNone(key.quarters)

    def test_shareholding_key_preserves_quarter_and_pledge_scope(self):
        request = FundamentalShareholdingRequest(
            request_id="request.shareholding.tcs.cache",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            issuer=build_issuer(),
            quarters=8,
            include_promoter_pledge=True,
        )
        key = FundamentalSnapshotCacheKey.from_request(request)

        self.assertEqual(key.quarters, 8)
        self.assertTrue(key.include_promoter_pledge)
        self.assertEqual(key.statements, ())

    def test_key_rejects_non_evidence_capability(self):
        with self.assertRaises(ValidationError):
            FundamentalSnapshotCacheKey(
                tenant_id="tenant.prateek",
                provider_connection_id="provider.tijori.prateek",
                provider="tijori",
                capability=FundamentalCapability.COMPANY_SEARCH,
                issuer=build_issuer(),
            )

        search_request = FundamentalCompanySearchRequest(
            request_id="request.search.tcs.cache",
            connection=build_connection(),
            requested_at=REQUESTED_AT,
            query="TCS",
        )
        with self.assertRaises(TypeError):
            FundamentalSnapshotCacheKey.from_request(search_request)

    def test_key_rejects_parameters_from_another_capability(self):
        overview = FundamentalSnapshotCacheKey.from_request(
            FundamentalCompanyOverviewRequest(
                request_id="request.overview.tcs.cache",
                connection=build_connection(),
                requested_at=REQUESTED_AT,
                issuer=build_issuer(),
            )
        )
        with self.assertRaises(ValidationError):
            rebuild(overview, quarters=8, include_promoter_pledge=True)

        financial = FundamentalSnapshotCacheKey.from_request(
            build_financial_request()
        )
        with self.assertRaises(ValidationError):
            rebuild(financial, statements=())

    def test_key_rejects_duplicate_statement_or_period_scope(self):
        key = FundamentalSnapshotCacheKey.from_request(
            build_financial_request()
        )
        with self.assertRaises(ValidationError):
            rebuild(
                key,
                statements=(
                    FundamentalStatement.CASH_FLOW,
                    FundamentalStatement.CASH_FLOW,
                ),
            )
        with self.assertRaises(ValidationError):
            rebuild(
                key,
                statements=(FundamentalStatement.OWNERSHIP,),
            )
        with self.assertRaises(ValidationError):
            rebuild(
                key,
                period_types=(FundamentalPeriodType.FORECAST,),
            )
        with self.assertRaises(ValidationError):
            rebuild(
                key,
                period_types=(
                    FundamentalPeriodType.ANNUAL,
                    FundamentalPeriodType.ANNUAL,
                ),
            )


class StoredFundamentalSnapshotTests(unittest.TestCase):
    def test_builds_ten_day_immutable_cache_entry(self):
        stored = build_stored()

        self.assertEqual(
            stored.expires_at - stored.retrieved_at,
            timedelta(days=10),
        )
        self.assertEqual(
            stored.request_fingerprint,
            stored.request.request_fingerprint,
        )
        self.assertRegex(stored.storage_fingerprint, r"^[a-f0-9]{64}$")

        with self.assertRaises(ValidationError):
            stored.expires_at = stored.expires_at + timedelta(days=1)

    def test_allows_shorter_retention_but_rejects_invalid_retention(self):
        shorter = build_stored(retention=timedelta(days=1))
        self.assertEqual(
            shorter.expires_at - shorter.retrieved_at,
            timedelta(days=1),
        )

        for retention in (
            timedelta(0),
            timedelta(seconds=-1),
            timedelta(days=10, seconds=1),
        ):
            with self.subTest(retention=retention):
                with self.assertRaises(ValueError):
                    build_stored(retention=retention)

    def test_expiry_is_inclusive_and_requires_aware_read_time(self):
        stored = build_stored()

        self.assertFalse(
            stored.is_expired(as_of=stored.expires_at - timedelta(microseconds=1))
        )
        self.assertTrue(stored.is_expired(as_of=stored.expires_at))
        with self.assertRaises(ValueError):
            stored.is_expired(as_of=datetime(2026, 9, 7, 14, 0))

    def test_rejects_cache_key_that_does_not_match_request(self):
        stored = build_stored()
        wrong_key = FundamentalSnapshotCacheKey.from_request(
            build_financial_request(max_periods=8)
        )

        with self.assertRaises(ValidationError):
            rebuild(stored, cache_key=wrong_key)

    def test_rejects_response_from_another_request_or_tenant(self):
        request = build_financial_request()
        retrieval = build_retrieval(request)
        another_request = build_financial_request(
            request_id="request.financials.other",
        )

        with self.assertRaises(ValueError):
            stored_fundamental_snapshot(
                another_request,
                retrieval,
                stored_at=STORED_AT,
            )

        other_connection = build_connection(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
        )
        other_request = build_financial_request(connection=other_connection)
        with self.assertRaises(ValueError):
            stored_fundamental_snapshot(
                other_request,
                retrieval,
                stored_at=STORED_AT,
            )

    def test_rejects_mismatched_stored_fingerprints(self):
        stored = build_stored()

        for field_name in (
            "request_fingerprint",
            "result_fingerprint",
            "snapshot_fingerprint",
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaises(ValidationError):
                    rebuild(stored, **{field_name: "f" * 64})

    def test_rejects_failed_retrieval(self):
        request = build_financial_request()
        failed = FundamentalEvidenceRetrieval(
            **response_values(
                request,
                capability=request.capability,
                issuer=request.issuer,
                status=FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
                snapshot=None,
                limitations=("Provider could not complete the request.",),
            )
        )

        with self.assertRaises(ValueError):
            stored_fundamental_snapshot(
                request,
                failed,
                stored_at=STORED_AT,
            )

    def test_rejects_incorrect_retrieval_expiry_and_storage_times(self):
        stored = build_stored()

        with self.assertRaises(ValidationError):
            rebuild(
                stored,
                retrieved_at=stored.retrieved_at + timedelta(seconds=1),
            )
        with self.assertRaises(ValidationError):
            rebuild(stored, expires_at=stored.retrieved_at)
        with self.assertRaises(ValidationError):
            rebuild(
                stored,
                expires_at=(
                    stored.retrieved_at + timedelta(days=10, seconds=1)
                ),
            )
        with self.assertRaises(ValidationError):
            rebuild(
                stored,
                stored_at=stored.retrieval.completed_at - timedelta(seconds=1),
            )
        with self.assertRaises(ValidationError):
            rebuild(stored, stored_at=stored.expires_at)

    def test_summary_is_payload_free_and_preserves_audit_counts(self):
        stored = build_stored()
        summary = fundamental_snapshot_summary(stored)

        self.assertEqual(summary.tenant_id, "tenant.prateek")
        self.assertEqual(summary.source_count, 1)
        self.assertEqual(summary.fact_count, 1)
        self.assertEqual(summary.conflict_count, 0)
        self.assertFalse(hasattr(summary, "snapshot"))
        self.assertFalse(hasattr(summary, "retrieval"))

        with self.assertRaises(ValidationError):
            rebuild(summary, expires_at=summary.retrieved_at)
        with self.assertRaises(ValidationError):
            rebuild(
                summary,
                retrieval_status=FundamentalRetrievalStatus.NOT_ENTITLED,
            )

    def test_storage_fingerprint_changes_with_retention_metadata(self):
        ten_days = build_stored()
        one_day = build_stored(retention=timedelta(days=1))

        self.assertNotEqual(
            ten_days.storage_fingerprint,
            one_day.storage_fingerprint,
        )


class FundamentalSnapshotQueryTests(unittest.TestCase):
    def test_query_requires_tenant_and_provider_connection_scope(self):
        query = FundamentalSnapshotQuery(
            tenant_id="tenant.prateek",
            provider_connection_id="provider.tijori.prateek",
            provider="tijori",
        )

        self.assertEqual(query.tenant_id, "tenant.prateek")
        with self.assertRaises(ValidationError):
            FundamentalSnapshotQuery(
                provider_connection_id="provider.tijori.prateek",
                provider="tijori",
            )

    def test_query_normalizes_identity_and_validates_capabilities(self):
        query = FundamentalSnapshotQuery(
            tenant_id="tenant.prateek",
            provider_connection_id="provider.tijori.prateek",
            provider="tijori",
            exchange="nse",
            symbol="tcs-eq",
            capabilities=(FundamentalCapability.FINANCIAL_STATEMENTS,),
        )
        self.assertEqual(query.exchange, "NSE")
        self.assertEqual(query.symbol, "TCS-EQ")

        with self.assertRaises(ValidationError):
            rebuild(
                query,
                capabilities=(FundamentalCapability.COMPANY_SEARCH,),
            )
        with self.assertRaises(ValidationError):
            rebuild(
                query,
                capabilities=(
                    FundamentalCapability.FINANCIAL_STATEMENTS,
                    FundamentalCapability.FINANCIAL_STATEMENTS,
                ),
            )

    def test_query_rejects_exchange_without_symbol(self):
        with self.assertRaises(ValidationError):
            FundamentalSnapshotQuery(
                tenant_id="tenant.prateek",
                provider_connection_id="provider.tijori.prateek",
                provider="tijori",
                exchange="NSE",
            )

    def test_query_rejects_naive_reversed_or_non_integer_paging(self):
        values = {
            "tenant_id": "tenant.prateek",
            "provider_connection_id": "provider.tijori.prateek",
            "provider": "tijori",
        }
        with self.assertRaises(ValidationError):
            FundamentalSnapshotQuery(
                **values,
                retrieved_from=datetime(2026, 8, 28, 14, 0),
            )
        with self.assertRaises(ValidationError):
            FundamentalSnapshotQuery(
                **values,
                retrieved_from=REQUESTED_AT,
                retrieved_to=REQUESTED_AT - timedelta(seconds=1),
            )
        with self.assertRaises(ValidationError):
            FundamentalSnapshotQuery(**values, limit=True)


class FakeFundamentalSnapshotRepository:
    @property
    def adapter_name(self):
        return "fake-fundamental-storage"

    def save_fundamental_snapshot(self, stored):
        return stored

    def get_fundamental_snapshot(self, key, *, scope, as_of):
        return None

    def list_fundamental_snapshots(self, query, *, as_of):
        return ()

    def delete_fundamental_snapshot(self, key, *, scope):
        return False

    def purge_expired_fundamental_snapshots(self, *, as_of):
        return 0


class IncompleteFundamentalSnapshotRepository:
    def save_fundamental_snapshot(self, stored):
        return stored


class FundamentalSnapshotRepositoryProtocolTests(unittest.TestCase):
    def test_complete_fake_satisfies_runtime_protocol(self):
        self.assertIsInstance(
            FakeFundamentalSnapshotRepository(),
            FundamentalSnapshotRepository,
        )

    def test_incomplete_object_does_not_satisfy_runtime_protocol(self):
        self.assertNotIsInstance(
            IncompleteFundamentalSnapshotRepository(),
            FundamentalSnapshotRepository,
        )

    def test_repository_surface_has_no_unscoped_list_or_raw_payload_method(self):
        methods = set(dir(FundamentalSnapshotRepository))

        self.assertNotIn("list_all", methods)
        self.assertNotIn("save_raw_payload", methods)
        self.assertNotIn("save_session", methods)
        self.assertIn("purge_expired_fundamental_snapshots", methods)


if __name__ == "__main__":
    unittest.main()
