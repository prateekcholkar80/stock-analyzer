from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

import duckdb

from app.exceptions import StorageConflictError, StorageError
from app.models.financial_document_storage import (
    StructuredDocumentRepositoryScope,
)
from app.storage.adapters.duckdb import DuckDBJarvisStorage
from app.storage.financial_document_repositories import (
    StructuredFinancialDocumentRepository,
)
from tests.unit.test_financial_document_in_memory_repository import (
    MutableClock,
    build_entry,
)
from tests.unit.test_financial_document_storage import COMPLETED_AT


class DuckDBStructuredFinancialDocumentRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "jarvis.duckdb"
        self.clock = MutableClock(COMPLETED_AT + timedelta(hours=1))
        self.scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant.prateek",
            provider_connection_id="provider.tijori.prateek",
            provider="tijori",
        )

    def tearDown(self) -> None:
        self.temporary_directory.cleanup()

    def storage(self):
        return DuckDBJarvisStorage(self.database, clock=self.clock)

    def test_implements_port_and_creates_schema_version_five_table(self):
        with self.storage() as storage:
            self.assertIsInstance(
                storage,
                StructuredFinancialDocumentRepository,
            )
            self.assertEqual(storage.schema_version, 5)
        connection = duckdb.connect(str(self.database), read_only=True)
        try:
            tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
            version = connection.execute(
                "SELECT metadata_value FROM jarvis_storage_metadata "
                "WHERE metadata_key = 'schema_version'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(version, "5")
        self.assertIn("jarvis_structured_financial_documents", tables)

    def test_migrates_version_four_database_without_provider_access(self):
        connection = duckdb.connect(str(self.database))
        try:
            connection.execute(
                "CREATE TABLE jarvis_storage_metadata ("
                "metadata_key VARCHAR PRIMARY KEY, "
                "metadata_value VARCHAR NOT NULL)"
            )
            connection.execute(
                "INSERT INTO jarvis_storage_metadata VALUES (?, ?)",
                ["schema_version", "4"],
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
                for row in migrated._connection.execute("SHOW TABLES").fetchall()
            }

        self.assertEqual(version, "5")
        self.assertIn("jarvis_structured_financial_documents", tables)

    def test_json_document_survives_close_and_reopen(self):
        stored = build_entry()
        with self.storage() as storage:
            saved = storage.save_structured_financial_document(stored)
            row = storage._connection.execute(
                "SELECT document_type, reporting_basis, period_count, row_count "
                "FROM jarvis_structured_financial_documents"
            ).fetchone()
        with self.storage() as reopened:
            loaded = reopened.get_structured_financial_document(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )
        self.assertEqual(saved, stored)
        self.assertEqual(loaded, stored)
        self.assertEqual(row, ("growth_table", "not_applicable", 1, 1))

    def test_browser_reference_read_is_scoped_validated_and_unexpired(self):
        stored = build_entry(retention=timedelta(hours=2))
        other_scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )
        self.clock.now = stored.stored_at
        with self.storage() as storage:
            storage.save_structured_financial_document(stored)
            loaded = storage.get_structured_financial_document_by_cache_entry_id(
                stored.cache_key.cache_entry_id,
                scope=self.scope,
                as_of=self.clock.now,
            )
            isolated = storage.get_structured_financial_document_by_cache_entry_id(
                stored.cache_key.cache_entry_id,
                scope=other_scope,
                as_of=self.clock.now,
            )
            expired = storage.get_structured_financial_document_by_cache_entry_id(
                stored.cache_key.cache_entry_id,
                scope=self.scope,
                as_of=stored.expires_at,
            )
            with self.assertRaises(ValueError):
                storage.get_structured_financial_document_by_cache_entry_id(
                    "financial_document:not-a-fingerprint",
                    scope=self.scope,
                    as_of=self.clock.now,
                )

        self.assertEqual(loaded, stored)
        self.assertIsNone(isolated)
        self.assertIsNone(expired)

    def test_idempotent_save_and_conflict_are_transactional(self):
        stored = build_entry()
        conflicting = stored.model_copy(
            update={"expires_at": stored.expires_at - timedelta(days=1)}
        )
        with self.storage() as storage:
            first = storage.save_structured_financial_document(stored)
            second = storage.save_structured_financial_document(stored)
            with self.assertRaises(StorageConflictError):
                storage.save_structured_financial_document(conflicting)
            loaded = storage.get_structured_financial_document(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )
        self.assertEqual(first, second)
        self.assertEqual(loaded, stored)

    def test_atomic_refresh_and_scope_failure_preserve_active_row(self):
        old = build_entry()
        refreshed = build_entry(offset=timedelta(hours=2))
        other_scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )
        with self.storage() as storage:
            self.clock.now = old.stored_at
            storage.save_structured_financial_document(old)
            self.clock.now = refreshed.stored_at
            storage.replace_structured_financial_document(
                refreshed,
                scope=self.scope,
            )
            with self.assertRaises(StorageConflictError):
                storage.replace_structured_financial_document(old, scope=self.scope)
            with self.assertRaises(StorageError):
                storage.replace_structured_financial_document(
                    refreshed,
                    scope=other_scope,
                )
            loaded = storage.get_structured_financial_document(
                refreshed.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )
        self.assertEqual(loaded, refreshed)

    def test_scoped_delete_and_automatic_expiry(self):
        stored = build_entry(retention=timedelta(hours=1))
        other_scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )
        self.clock.now = stored.stored_at
        with self.storage() as storage:
            storage.save_structured_financial_document(stored)
            self.assertFalse(
                storage.delete_structured_financial_document(
                    stored.cache_key,
                    scope=other_scope,
                )
            )
            self.assertIsNone(
                storage.get_structured_financial_document(
                    stored.cache_key,
                    scope=self.scope,
                    as_of=stored.expires_at,
                )
            )
            self.assertEqual(
                storage.purge_expired_structured_financial_documents(
                    as_of=stored.expires_at
                ),
                0,
            )


if __name__ == "__main__":
    unittest.main()
