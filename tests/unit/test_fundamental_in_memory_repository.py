from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import unittest

from app.exceptions import StorageConflictError, StorageError
from app.gateways.fundamentals import FundamentalCapability
from app.models.fundamental_storage import (
    FundamentalRepositoryScope,
    FundamentalSnapshotQuery,
    stored_fundamental_snapshot,
)
from app.storage.adapters.fundamental_in_memory import (
    InMemoryFundamentalSnapshotRepository,
)
from app.storage.fundamental_repositories import (
    FundamentalSnapshotRepository,
)
from tests.unit.test_fundamental_gateway import (
    COMPLETED_AT,
    REQUESTED_AT,
    build_connection,
    build_issuer,
)
from tests.unit.test_fundamental_storage_contracts import (
    STORED_AT,
    build_financial_request,
    build_retrieval,
    build_stored,
    rebuild,
)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def build_entry(
    *,
    max_periods: int = 12,
    requested_at: datetime = REQUESTED_AT,
    completed_at: datetime = COMPLETED_AT,
    stored_at: datetime | None = None,
    retention: timedelta = timedelta(days=10),
    connection=None,
    issuer=None,
):
    request = build_financial_request(
        request_id=(
            f"request.financials.cache.{max_periods}."
            f"{int(completed_at.timestamp())}"
        ),
        requested_at=requested_at,
        connection=connection or build_connection(),
        issuer=issuer or build_issuer(),
        max_periods=max_periods,
    )
    retrieval = build_retrieval(
        request,
        started_at=requested_at,
        completed_at=completed_at,
    )
    return stored_fundamental_snapshot(
        request,
        retrieval,
        stored_at=stored_at or completed_at + timedelta(seconds=1),
        retention=retention,
    )


class InMemoryFundamentalSnapshotRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock(STORED_AT + timedelta(hours=12))
        self.repository = InMemoryFundamentalSnapshotRepository(
            clock=self.clock
        )
        self.scope = FundamentalRepositoryScope(
            tenant_id="tenant.prateek",
            provider_connection_id="provider.tijori.prateek",
            provider="tijori",
        )
        self.query = FundamentalSnapshotQuery(
            tenant_id=self.scope.tenant_id,
            provider_connection_id=self.scope.provider_connection_id,
            provider=self.scope.provider,
        )

    def test_implements_repository_protocol_and_has_safe_adapter_name(self):
        self.assertIsInstance(
            self.repository,
            FundamentalSnapshotRepository,
        )
        self.assertEqual(
            self.repository.adapter_name,
            "in_memory_fundamentals",
        )

    def test_save_is_idempotent_and_returns_defensive_copies(self):
        stored = build_stored()

        first = self.repository.save_fundamental_snapshot(stored)
        second = self.repository.save_fundamental_snapshot(stored)
        loaded = self.repository.get_fundamental_snapshot(
            stored.cache_key,
            scope=self.scope,
            as_of=self.clock.now,
        )

        self.assertEqual(first, stored)
        self.assertEqual(second, stored)
        self.assertEqual(loaded, stored)
        self.assertIsNot(first, stored)
        self.assertIsNot(second, first)
        self.assertIsNot(loaded, first)

    def test_conflicting_content_under_same_semantic_key_is_rejected(self):
        stored = build_stored()
        different_retention = rebuild(
            stored,
            expires_at=stored.expires_at - timedelta(days=1),
        )
        self.repository.save_fundamental_snapshot(stored)

        with self.assertRaises(StorageConflictError):
            self.repository.save_fundamental_snapshot(different_retention)

        loaded = self.repository.get_fundamental_snapshot(
            stored.cache_key,
            scope=self.scope,
            as_of=self.clock.now,
        )
        self.assertEqual(loaded, stored)

    def test_save_rejects_expired_or_future_stored_entries(self):
        expired = build_entry(retention=timedelta(hours=1))
        self.clock.now = expired.expires_at
        with self.assertRaises(StorageError):
            self.repository.save_fundamental_snapshot(expired)

        future = build_stored(
            stored_at=self.clock.now + timedelta(seconds=1)
        )
        with self.assertRaises(StorageError):
            self.repository.save_fundamental_snapshot(future)

    def test_startup_load_purges_expired_and_rejects_future_rows(self):
        active = build_entry(max_periods=8)
        expired = build_entry(
            max_periods=12,
            retention=timedelta(hours=1),
        )
        startup_at = expired.expires_at
        repository = InMemoryFundamentalSnapshotRepository(
            clock=lambda: startup_at,
            initial_entries=(expired, active),
        )

        summaries = repository.list_fundamental_snapshots(
            self.query,
            as_of=startup_at,
        )
        self.assertEqual(
            tuple(item.cache_entry_id for item in summaries),
            (active.cache_key.cache_entry_id,),
        )

        with self.assertRaises(StorageError):
            InMemoryFundamentalSnapshotRepository(
                clock=lambda: STORED_AT,
                initial_entries=(
                    build_stored(stored_at=STORED_AT + timedelta(seconds=1)),
                ),
            )

    def test_expiry_is_inclusive_and_opportunistically_purged(self):
        stored = build_entry(retention=timedelta(days=1))
        self.repository.save_fundamental_snapshot(stored)

        self.assertIsNotNone(
            self.repository.get_fundamental_snapshot(
                stored.cache_key,
                scope=self.scope,
                as_of=stored.expires_at - timedelta(microseconds=1),
            )
        )
        self.assertIsNone(
            self.repository.get_fundamental_snapshot(
                stored.cache_key,
                scope=self.scope,
                as_of=stored.expires_at,
            )
        )
        self.assertEqual(
            self.repository.purge_expired_fundamental_snapshots(
                as_of=stored.expires_at,
            ),
            0,
        )

    def test_expired_key_can_be_refreshed_with_newly_retrieved_evidence(self):
        old = build_entry(retention=timedelta(days=1))
        self.clock.now = old.stored_at
        self.repository.save_fundamental_snapshot(old)

        later = timedelta(days=2)
        refreshed = build_entry(
            requested_at=REQUESTED_AT + later,
            completed_at=COMPLETED_AT + later,
        )
        self.assertEqual(refreshed.cache_key, old.cache_key)
        self.assertNotEqual(
            refreshed.storage_fingerprint,
            old.storage_fingerprint,
        )

        self.clock.now = refreshed.stored_at
        saved = self.repository.save_fundamental_snapshot(refreshed)

        self.assertEqual(saved, refreshed)
        self.assertEqual(
            self.repository.get_fundamental_snapshot(
                refreshed.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            ),
            refreshed,
        )

    def test_explicit_refresh_atomically_replaces_active_evidence(self):
        old = build_entry()
        self.clock.now = old.stored_at
        self.repository.save_fundamental_snapshot(old)
        later = timedelta(hours=2)
        refreshed = build_entry(
            requested_at=REQUESTED_AT + later,
            completed_at=COMPLETED_AT + later,
        )
        self.clock.now = refreshed.stored_at

        saved = self.repository.replace_fundamental_snapshot(
            refreshed,
            scope=self.scope,
        )

        self.assertEqual(saved, refreshed)
        self.assertEqual(
            self.repository.get_fundamental_snapshot(
                refreshed.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            ),
            refreshed,
        )

    def test_failed_explicit_refresh_preserves_active_evidence(self):
        old = build_entry()
        self.clock.now = old.stored_at
        self.repository.save_fundamental_snapshot(old)
        later = timedelta(hours=2)
        refreshed = build_entry(
            requested_at=REQUESTED_AT + later,
            completed_at=COMPLETED_AT + later,
        )
        self.clock.now = refreshed.stored_at
        self.repository.replace_fundamental_snapshot(
            refreshed,
            scope=self.scope,
        )
        other_scope = FundamentalRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )

        with self.assertRaises(StorageConflictError):
            self.repository.replace_fundamental_snapshot(
                old,
                scope=self.scope,
            )
        with self.assertRaises(StorageError):
            self.repository.replace_fundamental_snapshot(
                refreshed,
                scope=other_scope,
            )

        self.assertEqual(
            self.repository.get_fundamental_snapshot(
                refreshed.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            ),
            refreshed,
        )

    def test_point_read_and_delete_require_matching_scope(self):
        stored = build_stored()
        other_scope = FundamentalRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )
        self.repository.save_fundamental_snapshot(stored)

        self.assertIsNone(
            self.repository.get_fundamental_snapshot(
                stored.cache_key,
                scope=other_scope,
                as_of=self.clock.now,
            )
        )
        self.assertFalse(
            self.repository.delete_fundamental_snapshot(
                stored.cache_key,
                scope=other_scope,
            )
        )
        self.assertIsNotNone(
            self.repository.get_fundamental_snapshot(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )
        )

    def test_historical_read_does_not_reveal_future_stored_data(self):
        stored = build_stored()
        self.repository.save_fundamental_snapshot(stored)
        before_storage = stored.stored_at - timedelta(microseconds=1)

        self.assertIsNone(
            self.repository.get_fundamental_snapshot(
                stored.cache_key,
                scope=self.scope,
                as_of=before_storage,
            )
        )
        self.assertEqual(
            self.repository.list_fundamental_snapshots(
                self.query,
                as_of=before_storage,
            ),
            (),
        )

    def test_list_is_scoped_filtered_newest_first_and_paginated(self):
        older = build_entry(max_periods=12)
        newer = build_entry(
            max_periods=8,
            completed_at=COMPLETED_AT + timedelta(hours=1),
        )
        other_tenant = build_entry(
            max_periods=6,
            completed_at=COMPLETED_AT + timedelta(hours=2),
            connection=build_connection(
                tenant_id="tenant.other",
                provider_connection_id="provider.tijori.other",
            ),
        )
        for stored in (older, newer, other_tenant):
            self.repository.save_fundamental_snapshot(stored)

        summaries = self.repository.list_fundamental_snapshots(
            self.query,
            as_of=self.clock.now,
        )
        self.assertEqual(
            tuple(item.cache_entry_id for item in summaries),
            (
                newer.cache_key.cache_entry_id,
                older.cache_key.cache_entry_id,
            ),
        )
        self.assertNotIn(
            other_tenant.cache_key.cache_entry_id,
            {item.cache_entry_id for item in summaries},
        )

        paged = rebuild(self.query, limit=1, offset=1)
        self.assertEqual(
            self.repository.list_fundamental_snapshots(
                paged,
                as_of=self.clock.now,
            )[0].cache_entry_id,
            older.cache_key.cache_entry_id,
        )

        filtered = rebuild(
            self.query,
            exchange="nse",
            symbol="tcs-eq",
            capabilities=(FundamentalCapability.FINANCIAL_STATEMENTS,),
            retrieved_from=newer.retrieved_at,
            retrieved_to=newer.retrieved_at,
        )
        self.assertEqual(
            tuple(
                item.cache_entry_id
                for item in self.repository.list_fundamental_snapshots(
                    filtered,
                    as_of=self.clock.now,
                )
            ),
            (newer.cache_key.cache_entry_id,),
        )

        unsupported_capability = rebuild(
            self.query,
            capabilities=(FundamentalCapability.SHAREHOLDING_HISTORY,),
        )
        self.assertEqual(
            self.repository.list_fundamental_snapshots(
                unsupported_capability,
                as_of=self.clock.now,
            ),
            (),
        )

    def test_delete_and_explicit_purge_report_physical_changes(self):
        first = build_entry(max_periods=12, retention=timedelta(hours=1))
        second = build_entry(max_periods=8, retention=timedelta(hours=2))
        self.clock.now = STORED_AT
        self.repository.save_fundamental_snapshot(first)
        self.repository.save_fundamental_snapshot(second)

        self.assertEqual(
            self.repository.purge_expired_fundamental_snapshots(
                as_of=first.expires_at,
            ),
            1,
        )
        self.clock.now = first.expires_at
        self.assertTrue(
            self.repository.delete_fundamental_snapshot(
                second.cache_key,
                scope=self.scope,
            )
        )
        self.assertFalse(
            self.repository.delete_fundamental_snapshot(
                second.cache_key,
                scope=self.scope,
            )
        )

    def test_all_time_operations_reject_naive_datetimes(self):
        stored = build_stored()
        self.repository.save_fundamental_snapshot(stored)
        naive = datetime(2026, 8, 28, 14, 0)

        with self.assertRaises(ValueError):
            self.repository.get_fundamental_snapshot(
                stored.cache_key,
                scope=self.scope,
                as_of=naive,
            )
        with self.assertRaises(ValueError):
            self.repository.list_fundamental_snapshots(
                self.query,
                as_of=naive,
            )
        with self.assertRaises(ValueError):
            self.repository.purge_expired_fundamental_snapshots(as_of=naive)

        with self.assertRaises(ValueError):
            InMemoryFundamentalSnapshotRepository(
                clock=lambda: naive,
            )

    def test_concurrent_identical_saves_are_atomic_and_idempotent(self):
        stored = build_stored()
        with ThreadPoolExecutor(max_workers=16) as executor:
            results = tuple(
                executor.map(
                    self.repository.save_fundamental_snapshot,
                    (stored,) * 64,
                )
            )

        self.assertEqual(len(results), 64)
        self.assertTrue(all(result == stored for result in results))
        self.assertEqual(
            len(
                self.repository.list_fundamental_snapshots(
                    self.query,
                    as_of=self.clock.now,
                )
            ),
            1,
        )

    def test_concurrent_conflicts_never_corrupt_the_winning_entry(self):
        first = build_stored()
        second = rebuild(
            first,
            expires_at=first.expires_at - timedelta(days=1),
        )

        def save(stored):
            try:
                return self.repository.save_fundamental_snapshot(stored)
            except StorageConflictError:
                return None

        attempts = (first, second) * 32
        with ThreadPoolExecutor(max_workers=16) as executor:
            results = tuple(executor.map(save, attempts))

        successful = tuple(result for result in results if result is not None)
        self.assertGreaterEqual(len(successful), 32)
        fingerprints = {result.storage_fingerprint for result in successful}
        self.assertEqual(len(fingerprints), 1)
        loaded = self.repository.get_fundamental_snapshot(
            first.cache_key,
            scope=self.scope,
            as_of=self.clock.now,
        )
        self.assertIsNotNone(loaded)
        self.assertIn(loaded.storage_fingerprint, fingerprints)


if __name__ == "__main__":
    unittest.main()
