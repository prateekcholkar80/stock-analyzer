import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from app.exceptions import StorageConflictError, StorageError
from app.models.storage import (
    BacktestRunQuery,
    MarketSeriesQuery,
    stored_backtest_run,
    stored_market_series,
)
from app.services.research_archive import ResearchArchiveService
from app.storage.adapters.duckdb import DuckDBJarvisStorage
from app.storage.repositories import JarvisStorageAdapter
from tests.unit.test_storage_adapters import (
    _backtest_result,
    _market_series,
)


IST = ZoneInfo("Asia/Kolkata")


class DuckDBStorageTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.database = Path(self.temporary_directory.name) / "jarvis.duckdb"
        self.stored_at = datetime(2026, 8, 19, 15, 30, tzinfo=IST)

    def tearDown(self):
        self.temporary_directory.cleanup()

    def storage(self) -> DuckDBJarvisStorage:
        return DuckDBJarvisStorage(self.database)

    def test_implements_storage_contract_and_creates_versioned_schema(self):
        with self.storage() as storage:
            self.assertIsInstance(storage, JarvisStorageAdapter)
            self.assertEqual(storage.adapter_name, "duckdb")
            self.assertEqual(storage.schema_version, 1)
            self.assertEqual(storage.database, str(self.database))

        connection = duckdb.connect(str(self.database), read_only=True)
        try:
            version = connection.execute(
                "SELECT metadata_value FROM jarvis_storage_metadata "
                "WHERE metadata_key = 'schema_version'"
            ).fetchone()
            tables = {
                row[0]
                for row in connection.execute("SHOW TABLES").fetchall()
            }
        finally:
            connection.close()

        self.assertEqual(version, ("1",))
        self.assertIn("jarvis_market_series", tables)
        self.assertIn("jarvis_backtest_runs", tables)

    def test_market_series_survives_close_and_reopen(self):
        with self.storage() as storage:
            archive = ResearchArchiveService(storage)
            saved = archive.archive_market_series(
                _market_series(),
                stored_at=self.stored_at,
            )

        with self.storage() as reopened:
            loaded = reopened.get_market_series(saved.dataset_id)

        self.assertEqual(loaded, saved)
        self.assertEqual(loaded.stored_at.utcoffset(), timedelta(hours=5.5))

    def test_backtest_and_market_aggregate_survive_restart(self):
        with self.storage() as storage:
            archive = ResearchArchiveService(storage)
            saved = archive.archive_backtest_run(
                _backtest_result(),
                stored_at=self.stored_at,
            )

        with self.storage() as reopened:
            archive = ResearchArchiveService(reopened)
            loaded = archive.get_backtest_run(saved.run_id)
            market_summaries = archive.list_market_series()
            backtest_summaries = archive.list_backtest_runs()

        self.assertEqual(loaded, saved)
        self.assertEqual(len(market_summaries), 1)
        self.assertEqual(len(backtest_summaries), 1)
        self.assertEqual(backtest_summaries[0].symbol, "RELIANCE-EQ")
        self.assertEqual(backtest_summaries[0].candle_count, 2)

    def test_repeated_save_is_idempotent_and_preserves_first_timestamp(self):
        series = _market_series()
        first = stored_market_series(
            series,
            stored_at=self.stored_at,
        )
        repeated = stored_market_series(
            series,
            stored_at=self.stored_at + timedelta(hours=1),
        )
        with self.storage() as storage:
            first_result = storage.save_market_series(first)
            repeated_result = storage.save_market_series(repeated)

        self.assertEqual(repeated_result, first_result)
        self.assertEqual(repeated_result.stored_at, self.stored_at)

    def test_conflicting_identifier_rolls_back_without_overwrite(self):
        original = stored_market_series(
            _market_series(),
            dataset_id="shared-id",
            stored_at=self.stored_at,
        )
        conflict = stored_market_series(
            _market_series(symbol_token="1333", symbol="HDFCBANK-EQ"),
            dataset_id="shared-id",
            stored_at=self.stored_at,
        )
        with self.storage() as storage:
            storage.save_market_series(original)
            with self.assertRaises(StorageConflictError):
                storage.save_market_series(conflict)
            loaded = storage.get_market_series("shared-id")

        self.assertEqual(loaded, original)

    def test_queries_use_all_filters_time_bounds_and_pagination(self):
        with self.storage() as storage:
            archive = ResearchArchiveService(storage)
            archive.archive_market_series(
                _market_series(symbol_token="1333", symbol="HDFCBANK-EQ"),
                dataset_id="hdfc",
                stored_at=self.stored_at,
            )
            archive.archive_market_series(
                _market_series(day_offset=1),
                dataset_id="reliance-old",
                stored_at=self.stored_at + timedelta(hours=1),
            )
            archive.archive_market_series(
                _market_series(day_offset=2),
                dataset_id="reliance-new",
                stored_at=self.stored_at + timedelta(hours=2),
            )

            results = archive.list_market_series(
                MarketSeriesQuery(
                    exchange="NSE",
                    symbol_token="2885",
                    interval="ONE_DAY",
                    source="test",
                    stored_from=self.stored_at + timedelta(minutes=30),
                    stored_to=self.stored_at + timedelta(hours=3),
                    limit=1,
                    offset=1,
                )
            )

        self.assertEqual(
            [summary.dataset_id for summary in results],
            ["reliance-old"],
        )

    def test_backtest_query_filters_are_applied_by_database(self):
        reliance = stored_backtest_run(
            _backtest_result(),
            run_id="reliance",
            stored_at=self.stored_at,
        )
        hdfc = stored_backtest_run(
            _backtest_result(
                symbol_token="1333",
                symbol="HDFCBANK-EQ",
            ),
            run_id="hdfc",
            stored_at=self.stored_at + timedelta(hours=1),
        )
        with self.storage() as storage:
            storage.save_backtest_run(reliance)
            storage.save_backtest_run(hdfc)
            results = storage.list_backtest_runs(
                BacktestRunQuery(
                    backtest_id=reliance.result.backtest_id,
                    engine_id=reliance.result.engine_id,
                    exchange="NSE",
                    symbol_token="2885",
                    interval="ONE_DAY",
                )
            )

        self.assertEqual([summary.run_id for summary in results], ["reliance"])

    def test_delete_result_persists_and_missing_delete_is_false(self):
        stored = stored_backtest_run(
            _backtest_result(),
            stored_at=self.stored_at,
        )
        with self.storage() as storage:
            storage.save_backtest_run(stored)
            self.assertTrue(storage.delete_backtest_run(stored.run_id))
            self.assertFalse(storage.delete_backtest_run(stored.run_id))

        with self.storage() as reopened:
            self.assertIsNone(reopened.get_backtest_run(stored.run_id))

    def test_parameterized_identifier_cannot_change_query_semantics(self):
        with self.storage() as storage:
            storage.save_market_series(
                stored_market_series(
                    _market_series(),
                    dataset_id="safe-id",
                    stored_at=self.stored_at,
                )
            )

            loaded = storage.get_market_series("x' OR 1=1 --")

        self.assertIsNone(loaded)

    def test_corrupt_payload_is_reported_as_application_storage_error(self):
        with self.storage() as storage:
            stored = storage.save_market_series(
                stored_market_series(
                    _market_series(),
                    stored_at=self.stored_at,
                )
            )

        connection = duckdb.connect(str(self.database))
        try:
            connection.execute(
                "UPDATE jarvis_market_series SET payload_json = ? "
                "WHERE dataset_id = ?",
                ["not-json", stored.dataset_id],
            )
        finally:
            connection.close()

        with self.storage() as reopened:
            with self.assertRaises(StorageError) as raised:
                reopened.get_market_series(stored.dataset_id)

        self.assertIsInstance(raised.exception.__cause__, ValueError)

    def test_rejects_unknown_schema_version_without_modifying_database(self):
        connection = duckdb.connect(str(self.database))
        try:
            connection.execute(
                "CREATE TABLE jarvis_storage_metadata ("
                "metadata_key VARCHAR PRIMARY KEY, "
                "metadata_value VARCHAR NOT NULL)"
            )
            connection.execute(
                "INSERT INTO jarvis_storage_metadata VALUES (?, ?)",
                ["schema_version", "999"],
            )
        finally:
            connection.close()

        with self.assertRaisesRegex(StorageError, "Unsupported.*999"):
            self.storage()

        connection = duckdb.connect(str(self.database), read_only=True)
        try:
            tables = {
                row[0]
                for row in connection.execute("SHOW TABLES").fetchall()
            }
        finally:
            connection.close()
        self.assertNotIn("jarvis_market_series", tables)
        self.assertNotIn("jarvis_backtest_runs", tables)

    def test_closed_adapter_rejects_operations_and_close_is_idempotent(self):
        storage = self.storage()
        storage.close()
        storage.close()

        with self.assertRaisesRegex(StorageError, "closed"):
            storage.list_market_series()

    def test_wraps_invalid_database_location(self):
        missing_parent = Path(self.temporary_directory.name) / "missing" / "db"

        with self.assertRaisesRegex(StorageError, "initialize DuckDB"):
            DuckDBJarvisStorage(missing_parent)

    def test_rejects_blank_database_name(self):
        with self.assertRaisesRegex(ValueError, "cannot be blank"):
            DuckDBJarvisStorage("   ")


if __name__ == "__main__":
    unittest.main()
