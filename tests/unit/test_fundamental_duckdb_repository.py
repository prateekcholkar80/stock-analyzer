from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import tempfile
import unittest

import duckdb

from app.exceptions import StorageConflictError, StorageError
from app.gateways.fundamentals import FundamentalCapability
from app.models.fundamental_storage import (
    FundamentalRepositoryScope,
    FundamentalSnapshotQuery,
)
from app.storage.adapters.duckdb import DuckDBJarvisStorage
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
)
from tests.unit.test_fundamental_in_memory_repository import (
    MutableClock,
    build_entry,
)
from tests.unit.test_fundamental_storage_contracts import (
    STORED_AT,
    build_stored,
    rebuild,
)


FUNDAMENTAL_TABLES = {
    "jarvis_fundamental_snapshots",
    "jarvis_fundamental_request_statements",
    "jarvis_fundamental_request_period_types",
    "jarvis_fundamental_sources",
    "jarvis_fundamental_facts",
    "jarvis_fundamental_conflicts",
}


class DuckDBFundamentalSnapshotRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = (
            Path(self.temporary_directory.name) / "jarvis.duckdb"
        )
        self.clock = MutableClock(STORED_AT + timedelta(hours=12))
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

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def storage(self) -> DuckDBJarvisStorage:
        return DuckDBJarvisStorage(
            self.database,
            clock=self.clock,
        )

    def test_implements_contract_and_creates_version_five_schema(self):
        with self.storage() as storage:
            self.assertIsInstance(storage, FundamentalSnapshotRepository)
            self.assertEqual(storage.schema_version, 5)

        connection = duckdb.connect(str(self.database), read_only=True)
        try:
            tables = {
                row[0]
                for row in connection.execute("SHOW TABLES").fetchall()
            }
            version = connection.execute(
                "SELECT metadata_value FROM jarvis_storage_metadata "
                "WHERE metadata_key = 'schema_version'"
            ).fetchone()[0]
        finally:
            connection.close()

        self.assertEqual(version, "5")
        self.assertTrue(FUNDAMENTAL_TABLES.issubset(tables))

    def test_persists_envelope_and_normalized_evidence_relations(self):
        stored = build_stored()
        with self.storage() as storage:
            saved = storage.save_fundamental_snapshot(stored)
            parent = storage._connection.execute(
                "SELECT tenant_id, provider_connection_id, provider, "
                "capability, exchange, symbol, source_count, fact_count, "
                "conflict_count FROM jarvis_fundamental_snapshots "
                "WHERE cache_entry_id = ?",
                [stored.cache_key.cache_entry_id],
            ).fetchone()
            statements = storage._connection.execute(
                "SELECT statement FROM "
                "jarvis_fundamental_request_statements "
                "WHERE cache_entry_id = ? ORDER BY statement_index",
                [stored.cache_key.cache_entry_id],
            ).fetchall()
            periods = storage._connection.execute(
                "SELECT period_type FROM "
                "jarvis_fundamental_request_period_types "
                "WHERE cache_entry_id = ? ORDER BY period_type_index",
                [stored.cache_key.cache_entry_id],
            ).fetchall()
            source = storage._connection.execute(
                "SELECT source_id, source_type, content_fingerprint "
                "FROM jarvis_fundamental_sources"
            ).fetchone()
            fact = storage._connection.execute(
                "SELECT evidence_id, statement, line_item_id, "
                "normalized_value, currency, availability_status "
                "FROM jarvis_fundamental_facts"
            ).fetchone()

        self.assertEqual(saved, stored)
        self.assertEqual(
            parent,
            (
                "tenant.prateek",
                "provider.tijori.prateek",
                "tijori",
                "financial_statements",
                "NSE",
                "TCS-EQ",
                1,
                1,
                0,
            ),
        )
        self.assertEqual(
            statements,
            [
                ("income_statement",),
                ("balance_sheet",),
                ("cash_flow",),
            ],
        )
        self.assertEqual(periods, [("annual",), ("quarterly",)])
        self.assertEqual(source[0], "SRC-TCS-FILING-2026")
        self.assertEqual(source[1], "annual_report")
        self.assertEqual(len(source[2]), 64)
        self.assertEqual(fact[1], "income_statement")
        self.assertEqual(fact[2], "income_statement.reported_value")
        self.assertEqual(fact[3], "42.5")
        self.assertEqual(fact[4:], (None, "available"))

    def test_snapshot_and_query_survive_close_and_reopen(self):
        stored = build_stored()
        with self.storage() as storage:
            saved = storage.save_fundamental_snapshot(stored)

        with self.storage() as reopened:
            loaded = reopened.get_fundamental_snapshot(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )
            summaries = reopened.list_fundamental_snapshots(
                self.query,
                as_of=self.clock.now,
            )

        self.assertEqual(loaded, saved)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(
            summaries[0].cache_entry_id,
            stored.cache_key.cache_entry_id,
        )

    def test_idempotent_save_and_conflict_are_transactional(self):
        stored = build_stored()
        conflicting = rebuild(
            stored,
            expires_at=stored.expires_at - timedelta(days=1),
        )
        with self.storage() as storage:
            first = storage.save_fundamental_snapshot(stored)
            second = storage.save_fundamental_snapshot(stored)
            with self.assertRaises(StorageConflictError):
                storage.save_fundamental_snapshot(conflicting)
            loaded = storage.get_fundamental_snapshot(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )
            parent_count = storage._connection.execute(
                "SELECT count(*) FROM jarvis_fundamental_snapshots"
            ).fetchone()[0]
            fact_count = storage._connection.execute(
                "SELECT count(*) FROM jarvis_fundamental_facts"
            ).fetchone()[0]

        self.assertEqual(first, second)
        self.assertEqual(loaded, stored)
        self.assertEqual((parent_count, fact_count), (1, 1))

    def test_point_reads_and_deletes_enforce_caller_scope(self):
        stored = build_stored()
        other_scope = FundamentalRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )
        with self.storage() as storage:
            storage.save_fundamental_snapshot(stored)
            self.assertIsNone(
                storage.get_fundamental_snapshot(
                    stored.cache_key,
                    scope=other_scope,
                    as_of=self.clock.now,
                )
            )
            self.assertFalse(
                storage.delete_fundamental_snapshot(
                    stored.cache_key,
                    scope=other_scope,
                )
            )
            self.assertTrue(
                storage.delete_fundamental_snapshot(
                    stored.cache_key,
                    scope=self.scope,
                )
            )
            self.assertFalse(
                storage.delete_fundamental_snapshot(
                    stored.cache_key,
                    scope=self.scope,
                )
            )
            counts = tuple(
                storage._connection.execute(
                    f"SELECT count(*) FROM {table}"
                ).fetchone()[0]
                for table in FUNDAMENTAL_TABLES
            )

        self.assertEqual(counts, (0,) * len(FUNDAMENTAL_TABLES))

    def test_scoped_query_filters_orders_and_paginates_in_sql(self):
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
        with self.storage() as storage:
            for stored in (older, newer, other_tenant):
                storage.save_fundamental_snapshot(stored)

            summaries = storage.list_fundamental_snapshots(
                self.query,
                as_of=self.clock.now,
            )
            paged = storage.list_fundamental_snapshots(
                rebuild(self.query, limit=1, offset=1),
                as_of=self.clock.now,
            )
            filtered = storage.list_fundamental_snapshots(
                rebuild(
                    self.query,
                    exchange="nse",
                    symbol="tcs-eq",
                    capabilities=(
                        FundamentalCapability.FINANCIAL_STATEMENTS,
                    ),
                    retrieved_from=newer.retrieved_at,
                    retrieved_to=newer.retrieved_at,
                ),
                as_of=self.clock.now,
            )
            no_match = storage.list_fundamental_snapshots(
                rebuild(
                    self.query,
                    capabilities=(
                        FundamentalCapability.SHAREHOLDING_HISTORY,
                    ),
                ),
                as_of=self.clock.now,
            )

        self.assertEqual(
            tuple(item.cache_entry_id for item in summaries),
            (
                newer.cache_key.cache_entry_id,
                older.cache_key.cache_entry_id,
            ),
        )
        self.assertEqual(
            paged[0].cache_entry_id,
            older.cache_key.cache_entry_id,
        )
        self.assertEqual(
            tuple(item.cache_entry_id for item in filtered),
            (newer.cache_key.cache_entry_id,),
        )
        self.assertEqual(no_match, ())

    def test_historical_read_cannot_reveal_future_stored_row(self):
        stored = build_stored()
        with self.storage() as storage:
            storage.save_fundamental_snapshot(stored)
            before_storage = stored.stored_at - timedelta(microseconds=1)
            self.assertIsNone(
                storage.get_fundamental_snapshot(
                    stored.cache_key,
                    scope=self.scope,
                    as_of=before_storage,
                )
            )
            self.assertEqual(
                storage.list_fundamental_snapshots(
                    self.query,
                    as_of=before_storage,
                ),
                (),
            )

    def test_startup_purges_expired_parent_and_child_rows(self):
        stored = build_entry(retention=timedelta(hours=1))
        self.clock.now = stored.stored_at
        with self.storage() as storage:
            storage.save_fundamental_snapshot(stored)

        self.clock.now = stored.expires_at
        with self.storage() as reopened:
            counts = tuple(
                reopened._connection.execute(
                    f"SELECT count(*) FROM {table}"
                ).fetchone()[0]
                for table in FUNDAMENTAL_TABLES
            )
            self.assertIsNone(
                reopened.get_fundamental_snapshot(
                    stored.cache_key,
                    scope=self.scope,
                    as_of=self.clock.now,
                )
            )

        self.assertEqual(counts, (0,) * len(FUNDAMENTAL_TABLES))

    def test_explicit_expiry_purge_and_refresh_same_semantic_key(self):
        old = build_entry(retention=timedelta(days=1))
        self.clock.now = old.stored_at
        with self.storage() as storage:
            storage.save_fundamental_snapshot(old)
            self.assertEqual(
                storage.purge_expired_fundamental_snapshots(
                    as_of=old.expires_at - timedelta(microseconds=1)
                ),
                0,
            )
            self.assertEqual(
                storage.purge_expired_fundamental_snapshots(
                    as_of=old.expires_at
                ),
                1,
            )

            later = timedelta(days=2)
            refreshed = build_entry(
                requested_at=REQUESTED_AT + later,
                completed_at=COMPLETED_AT + later,
            )
            self.assertEqual(refreshed.cache_key, old.cache_key)
            self.clock.now = refreshed.stored_at
            storage.save_fundamental_snapshot(refreshed)
            self.assertEqual(
                storage.get_fundamental_snapshot(
                    refreshed.cache_key,
                    scope=self.scope,
                    as_of=self.clock.now,
                ),
                refreshed,
            )

    def test_save_opportunistically_replaces_an_expired_semantic_key(self):
        old = build_entry(retention=timedelta(days=1))
        self.clock.now = old.stored_at
        with self.storage() as storage:
            storage.save_fundamental_snapshot(old)

            later = timedelta(days=2)
            refreshed = build_entry(
                requested_at=REQUESTED_AT + later,
                completed_at=COMPLETED_AT + later,
            )
            self.clock.now = refreshed.stored_at
            storage.save_fundamental_snapshot(refreshed)

            summaries = storage.list_fundamental_snapshots(
                self.query,
                as_of=self.clock.now,
            )
            normalized_fact_count = storage._connection.execute(
                "SELECT count(*) FROM jarvis_fundamental_facts"
            ).fetchone()[0]

        self.assertEqual(len(summaries), 1)
        self.assertEqual(
            summaries[0].result_fingerprint,
            refreshed.result_fingerprint,
        )
        self.assertEqual(normalized_fact_count, 1)

    def test_explicit_refresh_transactionally_replaces_active_evidence(self):
        old = build_entry()
        later = timedelta(hours=2)
        refreshed = build_entry(
            requested_at=REQUESTED_AT + later,
            completed_at=COMPLETED_AT + later,
        )
        with self.storage() as storage:
            self.clock.now = old.stored_at
            storage.save_fundamental_snapshot(old)
            self.clock.now = refreshed.stored_at

            saved = storage.replace_fundamental_snapshot(
                refreshed,
                scope=self.scope,
            )
            loaded = storage.get_fundamental_snapshot(
                refreshed.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )
            relation_counts = {
                table: storage._connection.execute(
                    f"SELECT count(*) FROM {table}"
                ).fetchone()[0]
                for table in FUNDAMENTAL_TABLES
            }

        self.assertEqual(saved, refreshed)
        self.assertEqual(loaded, refreshed)
        self.assertEqual(
            relation_counts,
            {
                "jarvis_fundamental_snapshots": 1,
                "jarvis_fundamental_request_statements": len(
                    refreshed.cache_key.statements
                ),
                "jarvis_fundamental_request_period_types": len(
                    refreshed.cache_key.period_types
                ),
                "jarvis_fundamental_sources": 1,
                "jarvis_fundamental_facts": 1,
                "jarvis_fundamental_conflicts": 0,
            },
        )

    def test_failed_explicit_refresh_rolls_back_without_losing_evidence(self):
        old = build_entry()
        later = timedelta(hours=2)
        refreshed = build_entry(
            requested_at=REQUESTED_AT + later,
            completed_at=COMPLETED_AT + later,
        )
        other_scope = FundamentalRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )
        with self.storage() as storage:
            self.clock.now = old.stored_at
            storage.save_fundamental_snapshot(old)
            self.clock.now = refreshed.stored_at
            storage.replace_fundamental_snapshot(
                refreshed,
                scope=self.scope,
            )

            with self.assertRaises(StorageConflictError):
                storage.replace_fundamental_snapshot(
                    old,
                    scope=self.scope,
                )
            with self.assertRaises(StorageError):
                storage.replace_fundamental_snapshot(
                    refreshed,
                    scope=other_scope,
                )
            loaded = storage.get_fundamental_snapshot(
                refreshed.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )
            parent_count = storage._connection.execute(
                "SELECT count(*) FROM jarvis_fundamental_snapshots"
            ).fetchone()[0]

        self.assertEqual(loaded, refreshed)
        self.assertEqual(parent_count, 1)

    def test_matches_in_memory_repository_observable_contract(self):
        older = build_entry(max_periods=12)
        newer = build_entry(
            max_periods=8,
            completed_at=COMPLETED_AT + timedelta(hours=1),
        )
        memory = InMemoryFundamentalSnapshotRepository(clock=self.clock)
        with self.storage() as persistent:
            for repository in (memory, persistent):
                repository.save_fundamental_snapshot(older)
                repository.save_fundamental_snapshot(newer)

            memory_summaries = memory.list_fundamental_snapshots(
                self.query,
                as_of=self.clock.now,
            )
            duckdb_summaries = persistent.list_fundamental_snapshots(
                self.query,
                as_of=self.clock.now,
            )
            memory_loaded = memory.get_fundamental_snapshot(
                newer.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )
            duckdb_loaded = persistent.get_fundamental_snapshot(
                newer.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )
            memory_deleted = memory.delete_fundamental_snapshot(
                older.cache_key,
                scope=self.scope,
            )
            duckdb_deleted = persistent.delete_fundamental_snapshot(
                older.cache_key,
                scope=self.scope,
            )

        self.assertEqual(duckdb_summaries, memory_summaries)
        self.assertEqual(duckdb_loaded, memory_loaded)
        self.assertEqual(duckdb_deleted, memory_deleted)

    def test_rejects_expired_future_and_naive_times(self):
        expired = build_entry(retention=timedelta(hours=1))
        self.clock.now = expired.expires_at
        with self.storage() as storage:
            with self.assertRaises(StorageError):
                storage.save_fundamental_snapshot(expired)
            future = build_stored(
                stored_at=self.clock.now + timedelta(seconds=1)
            )
            with self.assertRaises(StorageError):
                storage.save_fundamental_snapshot(future)

            naive = datetime(2026, 8, 28, 14, 0)
            with self.assertRaises(ValueError):
                storage.get_fundamental_snapshot(
                    expired.cache_key,
                    scope=self.scope,
                    as_of=naive,
                )
            with self.assertRaises(ValueError):
                storage.list_fundamental_snapshots(
                    self.query,
                    as_of=naive,
                )
            with self.assertRaises(ValueError):
                storage.purge_expired_fundamental_snapshots(as_of=naive)

        with self.assertRaisesRegex(StorageError, "initialize DuckDB"):
            DuckDBJarvisStorage(
                Path(self.temporary_directory.name) / "naive.duckdb",
                clock=lambda: datetime(2026, 8, 28, 14, 0),
            )

    def test_corrupt_envelope_fails_closed_without_returning_payload(self):
        stored = build_stored()
        with self.storage() as storage:
            storage.save_fundamental_snapshot(stored)
            storage._connection.execute(
                "UPDATE jarvis_fundamental_snapshots "
                "SET payload_json = ? WHERE cache_entry_id = ?",
                ["not-json", stored.cache_key.cache_entry_id],
            )
            with self.assertRaises(StorageError):
                storage.get_fundamental_snapshot(
                    stored.cache_key,
                    scope=self.scope,
                    as_of=self.clock.now,
                )

    def test_migrates_version_three_database_without_provider_access(self):
        connection = duckdb.connect(str(self.database))
        try:
            connection.execute(
                "CREATE TABLE jarvis_storage_metadata ("
                "metadata_key VARCHAR PRIMARY KEY, "
                "metadata_value VARCHAR NOT NULL)"
            )
            connection.execute(
                "INSERT INTO jarvis_storage_metadata VALUES (?, ?)",
                ["schema_version", "3"],
            )
        finally:
            connection.close()

        with self.storage() as migrated:
            version = migrated._connection.execute(
                "SELECT metadata_value FROM jarvis_storage_metadata "
                "WHERE metadata_key = 'schema_version'"
            ).fetchone()[0]
            tables = {
                row[0]
                for row in migrated._connection.execute(
                    "SHOW TABLES"
                ).fetchall()
            }

        self.assertEqual(version, "5")
        self.assertTrue(FUNDAMENTAL_TABLES.issubset(tables))

    def test_concurrent_identical_and_conflicting_saves_are_atomic(self):
        first = build_stored()
        second = rebuild(
            first,
            expires_at=first.expires_at - timedelta(days=1),
        )
        with self.storage() as storage:
            with ThreadPoolExecutor(max_workers=12) as executor:
                identical = tuple(
                    executor.map(
                        storage.save_fundamental_snapshot,
                        (first,) * 32,
                    )
                )

            def save_conflict(stored):
                try:
                    return storage.save_fundamental_snapshot(stored)
                except StorageConflictError:
                    return None

            with ThreadPoolExecutor(max_workers=12) as executor:
                conflicts = tuple(
                    executor.map(save_conflict, (first, second) * 16)
                )
            parent_count = storage._connection.execute(
                "SELECT count(*) FROM jarvis_fundamental_snapshots"
            ).fetchone()[0]
            fact_count = storage._connection.execute(
                "SELECT count(*) FROM jarvis_fundamental_facts"
            ).fetchone()[0]

        self.assertTrue(all(saved == first for saved in identical))
        self.assertEqual(
            {saved.storage_fingerprint for saved in conflicts if saved},
            {first.storage_fingerprint},
        )
        self.assertEqual((parent_count, fact_count), (1, 1))


if __name__ == "__main__":
    unittest.main()
