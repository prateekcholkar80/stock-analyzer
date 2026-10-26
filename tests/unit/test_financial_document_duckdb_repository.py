from datetime import timedelta
from pathlib import Path
import tempfile
import unittest

import duckdb

from app.exceptions import StorageConflictError, StorageError
from app.models.financial_document_storage import (
    StructuredDocumentRepositoryScope,
)
from app.models.storage import market_series_summary, stored_market_series
from app.storage.adapters.duckdb import DuckDBJarvisStorage
from app.storage.financial_document_repositories import (
    StructuredFinancialDocumentRepository,
)
from tests.unit.test_financial_document_in_memory_repository import (
    MutableClock,
    build_entry,
    build_package,
)
from tests.unit.test_financial_document_storage import COMPLETED_AT
from tests.unit.test_storage_adapters import _market_series


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

    def test_implements_port_and_creates_current_schema_table(self):
        with self.storage() as storage:
            self.assertIsInstance(
                storage,
                StructuredFinancialDocumentRepository,
            )
            self.assertEqual(storage.schema_version, 9)
            expected_version = str(storage.schema_version)
        connection = duckdb.connect(str(self.database), read_only=True)
        try:
            tables = {row[0] for row in connection.execute("SHOW TABLES").fetchall()}
            version = connection.execute(
                "SELECT metadata_value FROM jarvis_storage_metadata "
                "WHERE metadata_key = 'schema_version'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(version, expected_version)
        self.assertIn("jarvis_structured_financial_documents", tables)

    def test_migrates_version_four_database_without_provider_access(self):
        market = stored_market_series(
            _market_series(), stored_at=self.clock.now,
        )
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
            # Seed the legacy envelope directly, without initializing v9 first.
            connection.execute(
                "CREATE TABLE jarvis_market_series ("
                "dataset_id VARCHAR PRIMARY KEY, "
                "payload_fingerprint VARCHAR NOT NULL, "
                "stored_at TIMESTAMPTZ NOT NULL, exchange VARCHAR NOT NULL, "
                "symbol_token VARCHAR NOT NULL, interval VARCHAR NOT NULL, "
                "source VARCHAR NOT NULL, summary_json VARCHAR NOT NULL, "
                "payload_json VARCHAR NOT NULL, instrument_id VARCHAR, "
                "symbol VARCHAR)"
            )
            connection.execute(
                "INSERT INTO jarvis_market_series "
                "(dataset_id, payload_fingerprint, stored_at, exchange, "
                "symbol_token, interval, source, summary_json, payload_json) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    market.dataset_id,
                    market.payload_fingerprint,
                    market.stored_at,
                    market.series.exchange,
                    market.series.symbol_token,
                    market.series.interval,
                    market.series.source,
                    market_series_summary(market).model_dump_json(),
                    market.model_dump_json(),
                ],
            )
        finally:
            connection.close()

        with self.storage() as migrated:
            self.assertEqual(migrated.schema_version, 9)
            version = migrated._connection.execute(
                "SELECT metadata_value FROM jarvis_storage_metadata "
                "WHERE metadata_key = 'schema_version'"
            ).fetchone()[0]
            tables = {
                row[0]
                for row in migrated._connection.execute("SHOW TABLES").fetchall()
            }
            self.assertEqual(migrated.get_market_series(market.dataset_id), market)
            saved = migrated.save_structured_financial_document(build_entry())

        self.assertEqual(version, "9")
        self.assertIn("jarvis_structured_financial_documents", tables)
        with self.storage() as reopened:
            self.assertEqual(reopened.get_market_series(market.dataset_id), market)
            self.assertEqual(
                reopened.get_structured_financial_document(
                    saved.cache_key, scope=self.scope, as_of=self.clock.now,
                ),
                saved,
            )
            self.assertEqual(
                reopened._connection.execute(
                    "SELECT metadata_value FROM jarvis_storage_metadata "
                    "WHERE metadata_key = 'schema_version'"
                ).fetchone()[0],
                "9",
            )

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

    def test_atomic_package_replace_commits_all_or_rolls_back_all(self):
        original = build_package()
        refreshed = build_package(offset=timedelta(hours=2))
        advanced = build_package(offset=timedelta(hours=3))
        with self.storage() as storage:
            self.clock.now = original[0].stored_at
            persisted = storage.replace_structured_financial_document_package(
                original,
                scope=self.scope,
            )
            self.assertEqual(persisted, original)

            self.clock.now = advanced[-1].stored_at
            storage.replace_structured_financial_document(
                advanced[-1],
                scope=self.scope,
            )
            before_attempt = (*original[:-1], advanced[-1])
            with self.assertRaises(StorageConflictError):
                storage.replace_structured_financial_document_package(
                    refreshed,
                    scope=self.scope,
                )

            for item in before_attempt:
                self.assertEqual(
                    storage.get_structured_financial_document(
                        item.cache_key,
                        scope=self.scope,
                        as_of=self.clock.now,
                    ),
                    item,
                )

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
