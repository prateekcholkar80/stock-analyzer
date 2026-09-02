from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

import duckdb

from app.exceptions import StorageConflictError, StorageError
from app.models.benchmarking_financials_storage import (
    StoredBenchmarkingFinancialsDocument,
)
from app.models.financial_document_storage import (
    StructuredDocumentRepositoryScope,
)
from app.storage.adapters.duckdb import DuckDBJarvisStorage
from app.storage.benchmarking_financials_repositories import (
    BenchmarkingFinancialsRepository,
)
from tests.unit.test_benchmarking_financials_gateway import COMPLETED_AT
from tests.unit.test_benchmarking_financials_in_memory_repository import (
    MutableClock,
    build_entry,
)


class DuckDBBenchmarkingFinancialsRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "jarvis.duckdb"
        self.clock = MutableClock(COMPLETED_AT + timedelta(hours=1))
        self.scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant-a",
            provider_connection_id="tijori-a",
            provider="tijori",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def storage(self) -> DuckDBJarvisStorage:
        return DuckDBJarvisStorage(self.database, clock=self.clock)

    def test_implements_port_and_creates_indexed_table(self):
        with self.storage() as storage:
            self.assertIsInstance(storage, BenchmarkingFinancialsRepository)
            self.assertEqual(storage.schema_version, 5)

        connection = duckdb.connect(str(self.database), read_only=True)
        try:
            tables = {
                row[0] for row in connection.execute("SHOW TABLES").fetchall()
            }
            indexes = {
                row[0]
                for row in connection.execute(
                    "SELECT index_name FROM duckdb_indexes() "
                    "WHERE table_name = 'jarvis_benchmarking_financials'"
                ).fetchall()
            }
        finally:
            connection.close()

        self.assertIn("jarvis_benchmarking_financials", tables)
        self.assertTrue(
            {
                "jarvis_benchmarking_financials_scope_lookup",
                "jarvis_benchmarking_financials_fingerprint_lookup",
                "jarvis_benchmarking_financials_expiry_lookup",
            }.issubset(indexes)
        )

    def test_complete_json_and_metadata_survive_reopen(self):
        stored = build_entry()
        with self.storage() as storage:
            saved = storage.save_benchmarking_financials(stored)
            row = storage._connection.execute(
                "SELECT tenant_id, symbol, document_fingerprint, "
                "observation_date, company_count, row_count, payload_json "
                "FROM jarvis_benchmarking_financials"
            ).fetchone()

        decoded = StoredBenchmarkingFinancialsDocument.model_validate_json(
            row[6]
        )
        with self.storage() as reopened:
            loaded = reopened.get_benchmarking_financials(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )

        self.assertEqual(saved, stored)
        self.assertEqual(loaded, stored)
        self.assertEqual(decoded, stored)
        self.assertEqual(row[:2], ("tenant-a", "COFORGE"))
        self.assertEqual(row[2], stored.document_fingerprint)
        self.assertEqual(row[3], stored.result.document.observation_date)
        self.assertEqual(row[4:6], (2, 2))

    def test_idempotent_save_and_conflict_are_transactional(self):
        stored = build_entry()
        conflicting = stored.model_copy(
            update={"expires_at": stored.expires_at - timedelta(days=1)}
        )
        with self.storage() as storage:
            first = storage.save_benchmarking_financials(stored)
            second = storage.save_benchmarking_financials(stored)
            with self.assertRaises(StorageConflictError):
                storage.save_benchmarking_financials(conflicting)
            loaded = storage.get_benchmarking_financials(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )

        self.assertEqual(first, second)
        self.assertEqual(loaded, stored)

    def test_atomic_refresh_and_scope_failure_preserve_active_json(self):
        old = build_entry()
        refreshed = build_entry(offset=timedelta(hours=2))
        other_scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )
        with self.storage() as storage:
            self.clock.now = old.stored_at
            storage.save_benchmarking_financials(old)
            self.clock.now = refreshed.stored_at
            storage.replace_benchmarking_financials(
                refreshed,
                scope=self.scope,
            )
            with self.assertRaises(StorageConflictError):
                storage.replace_benchmarking_financials(
                    old,
                    scope=self.scope,
                )
            with self.assertRaises(StorageError):
                storage.replace_benchmarking_financials(
                    refreshed,
                    scope=other_scope,
                )
            loaded = storage.get_benchmarking_financials(
                refreshed.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )

        self.assertEqual(loaded, refreshed)

    def test_reference_reads_and_deletes_are_tenant_isolated(self):
        stored = build_entry()
        other_scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )
        with self.storage() as storage:
            storage.save_benchmarking_financials(stored)
            self.assertIsNone(
                storage.get_benchmarking_financials_by_cache_entry_id(
                    stored.cache_key.cache_entry_id,
                    scope=other_scope,
                    as_of=self.clock.now,
                )
            )
            self.assertEqual(
                storage.get_benchmarking_financials_by_cache_entry_id(
                    stored.cache_key.cache_entry_id,
                    scope=self.scope,
                    as_of=self.clock.now,
                ),
                stored,
            )
            with self.assertRaises(ValueError):
                storage.get_benchmarking_financials_by_cache_entry_id(
                    "benchmarking_financials:not-a-fingerprint",
                    scope=self.scope,
                    as_of=self.clock.now,
                )
            self.assertFalse(
                storage.delete_benchmarking_financials(
                    stored.cache_key,
                    scope=other_scope,
                )
            )
            self.assertTrue(
                storage.delete_benchmarking_financials(
                    stored.cache_key,
                    scope=self.scope,
                )
            )

    def test_expiry_is_inclusive_and_physically_purged(self):
        stored = build_entry(retention=timedelta(hours=1))
        self.clock.now = stored.stored_at
        with self.storage() as storage:
            storage.save_benchmarking_financials(stored)
            self.assertIsNotNone(
                storage.get_benchmarking_financials(
                    stored.cache_key,
                    scope=self.scope,
                    as_of=stored.expires_at - timedelta(microseconds=1),
                )
            )
            self.assertIsNone(
                storage.get_benchmarking_financials(
                    stored.cache_key,
                    scope=self.scope,
                    as_of=stored.expires_at,
                )
            )
            row_count = storage._connection.execute(
                "SELECT count(*) FROM jarvis_benchmarking_financials"
            ).fetchone()[0]

        self.assertEqual(row_count, 0)


if __name__ == "__main__":
    unittest.main()
