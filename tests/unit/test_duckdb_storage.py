import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import duckdb

from app.analytics.walk_forward import (
    WalkForwardBacktestEngine,
    run_walk_forward_backtest,
)
from app.exceptions import StorageConflictError, StorageError
from app.models.backtest import WalkForwardBacktestConfig
from app.models.execution import (
    ExecutionSimulationConfig,
    ExecutionTargetPolicy,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import SignalDirection
from app.models.storage import (
    BacktestRunQuery,
    MarketSeriesQuery,
    backtest_run_summary,
    market_series_summary,
    stored_backtest_run,
    stored_market_series,
)
from app.services.research_archive import ResearchArchiveService
from app.storage.adapters.duckdb import DuckDBJarvisStorage
from app.storage.repositories import JarvisStorageAdapter
from app.use_cases.run_and_archive_backtest import (
    RunAndArchiveWalkForwardBacktest,
)
from tests.unit.test_storage_adapters import (
    _backtest_result,
    _market_series,
)
from tests.unit.test_walk_forward import _ScriptedOrchestrator


IST = ZoneInfo("Asia/Kolkata")


def _detailed_backtest_result():
    base_time = datetime(2026, 1, 1, tzinfo=IST)

    def candle(day, *, high=101.0, low=99.0):
        return Candle(
            timestamp=base_time + timedelta(days=day),
            open=100.0,
            high=high,
            low=low,
            close=100.0,
            volume=1_000 + day,
        )

    candles = [candle(day) for day in range(11)]
    candles[3] = candle(3, high=109, low=99)
    candles[4] = candle(4, high=101, low=91)
    candles[5] = candle(5, high=101, low=95)
    candles[9] = candle(9, high=109, low=99)
    directions = {
        2: SignalDirection.BULLISH,
        3: SignalDirection.BEARISH,
        4: SignalDirection.BULLISH,
        6: SignalDirection.BULLISH,
    }
    series = HistoricalCandleSeries(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        interval="ONE_DAY",
        candles=candles,
        retrieved_at=base_time + timedelta(days=30),
        source="test",
    )
    execution = ExecutionSimulationConfig(
        initial_capital=100_000.0,
        risk_per_trade_percentage=1.0,
        maximum_position_percentage=100.0,
        slippage_basis_points=0.0,
        commission_basis_points=0.0,
        fixed_fee_per_order=0.0,
        target_policy=ExecutionTargetPolicy.MINIMUM,
    )
    return run_walk_forward_backtest(
        series,
        orchestrator=_ScriptedOrchestrator(directions),
        config=WalkForwardBacktestConfig(
            warmup_candles=2,
            execution=execution,
        ),
    )


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
            self.assertEqual(storage.schema_version, 2)
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

        self.assertEqual(version, ("2",))
        self.assertTrue(
            {
                "jarvis_instruments",
                "jarvis_instrument_symbols",
                "jarvis_market_series",
                "jarvis_market_candles",
                "jarvis_strategy_configurations",
                "jarvis_strategy_weights",
                "jarvis_backtest_runs",
                "jarvis_backtest_evaluations",
                "jarvis_backtest_evaluation_categories",
                "jarvis_backtest_signal_evidence",
                "jarvis_backtest_signal_contributions",
                "jarvis_backtest_trades",
                "jarvis_backtest_equity_points",
                "jarvis_backtest_performance",
                "jarvis_backtest_performance_segments",
            }.issubset(tables)
        )

    def test_normalizes_instrument_symbol_and_every_market_candle(self):
        series = _market_series()
        with self.storage() as storage:
            saved = storage.save_market_series(
                stored_market_series(series, stored_at=self.stored_at)
            )
            instrument = storage._connection.execute(
                "SELECT source, exchange, symbol_token, current_symbol "
                "FROM jarvis_instruments"
            ).fetchone()
            dataset = storage._connection.execute(
                "SELECT instrument_id, symbol FROM jarvis_market_series "
                "WHERE dataset_id = ?",
                [saved.dataset_id],
            ).fetchone()
            candles = storage._connection.execute(
                "SELECT candle_index, close, volume "
                "FROM jarvis_market_candles WHERE dataset_id = ? "
                "ORDER BY candle_index",
                [saved.dataset_id],
            ).fetchall()
            symbol_observation = storage._connection.execute(
                "SELECT symbol, dataset_id FROM jarvis_instrument_symbols"
            ).fetchone()

        self.assertEqual(
            instrument,
            ("test", "NSE", "2885", "RELIANCE-EQ"),
        )
        self.assertTrue(dataset[0].startswith("instrument:"))
        self.assertEqual(dataset[1], "RELIANCE-EQ")
        self.assertEqual(candles, [(0, 101.0, 1000), (1, 102.0, 1001)])
        self.assertEqual(
            symbol_observation,
            ("RELIANCE-EQ", saved.dataset_id),
        )

    def test_persists_complete_normalized_backtest_attributes(self):
        result = _detailed_backtest_result()
        stored = stored_backtest_run(
            result,
            run_id="detailed-run",
            stored_at=self.stored_at,
        )
        with self.storage() as storage:
            storage.save_market_series(
                stored_market_series(
                    result.market_series,
                    stored_at=self.stored_at,
                )
            )
            storage.save_backtest_run(stored)
            connection = storage._connection
            run_attributes = connection.execute(
                "SELECT market_dataset_id, instrument_id, symbol, source, "
                "resolved_warmup_candles FROM jarvis_backtest_runs "
                "WHERE run_id = ?",
                [stored.run_id],
            ).fetchone()
            counts = {
                table: connection.execute(
                    f"SELECT count(*) FROM {table} WHERE run_id = ?",
                    [stored.run_id],
                ).fetchone()[0]
                for table in (
                    "jarvis_backtest_evaluations",
                    "jarvis_backtest_evaluation_categories",
                    "jarvis_backtest_signal_evidence",
                    "jarvis_backtest_signal_contributions",
                    "jarvis_backtest_trades",
                    "jarvis_backtest_equity_points",
                    "jarvis_backtest_performance",
                    "jarvis_backtest_performance_segments",
                )
            }
            trade = connection.execute(
                "SELECT direction, outcome, entry_price, target_price, "
                "net_pnl, realized_r_multiple FROM jarvis_backtest_trades "
                "WHERE run_id = ? ORDER BY evaluation_index LIMIT 1",
                [stored.run_id],
            ).fetchone()
            performance = connection.execute(
                "SELECT entered_trades, winning_trades, net_profit, "
                "maximum_drawdown_amount FROM jarvis_backtest_performance "
                "WHERE run_id = ?",
                [stored.run_id],
            ).fetchone()

        self.assertTrue(run_attributes[0].startswith("market:"))
        self.assertTrue(run_attributes[1].startswith("instrument:"))
        self.assertEqual(run_attributes[2:], ("RELIANCE-EQ", "test", 2))
        self.assertEqual(
            counts,
            {
                "jarvis_backtest_evaluations": 9,
                "jarvis_backtest_evaluation_categories": 35,
                "jarvis_backtest_signal_evidence": 35,
                "jarvis_backtest_signal_contributions": 35,
                "jarvis_backtest_trades": 4,
                "jarvis_backtest_equity_points": 11,
                "jarvis_backtest_performance": 1,
                "jarvis_backtest_performance_segments": 4,
            },
        )
        self.assertEqual(trade[:2], ("long", "minimum_target"))
        self.assertEqual(trade[2:5], (100.0, 108.0, 2000.0))
        self.assertAlmostEqual(trade[5], 2.0)
        self.assertEqual(performance[:3], (4, 3, 5056.0))
        self.assertGreater(performance[3], 0)

    def test_persists_reproducible_strategy_snapshot_and_weights(self):
        result = WalkForwardBacktestEngine(
            config=WalkForwardBacktestConfig(warmup_candles=2)
        ).run(_market_series())
        stored = stored_backtest_run(
            result,
            stored_at=self.stored_at,
        )
        with self.storage() as storage:
            storage.save_backtest_run(stored)
            strategy = storage._connection.execute(
                "SELECT strategy_configuration_id, configuration_json "
                "FROM jarvis_strategy_configurations"
            ).fetchone()
            weights = storage._connection.execute(
                "SELECT category, weight FROM jarvis_strategy_weights "
                "ORDER BY category"
            ).fetchall()
            run_strategy_id = storage._connection.execute(
                "SELECT strategy_configuration_id "
                "FROM jarvis_backtest_runs WHERE run_id = ?",
                [stored.run_id],
            ).fetchone()[0]

        snapshot = json.loads(strategy[1])
        self.assertEqual(run_strategy_id, strategy[0])
        self.assertEqual(
            strategy[0],
            result.strategy_configuration.strategy_configuration_id,
        )
        self.assertIn("technical_parameters", snapshot)
        self.assertIn("trade_planning_parameters", snapshot)
        self.assertIn("execution_parameters", snapshot)
        self.assertIn("walk_forward_parameters", snapshot)
        self.assertEqual(
            weights,
            [
                ("momentum", 1.0),
                ("price_action", 1.5),
                ("trend", 1.25),
                ("volatility", 0.75),
                ("volume", 1.0),
            ],
        )

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

    def test_run_and_archive_use_case_persists_to_duckdb(self):
        runner = WalkForwardBacktestEngine(
            config=WalkForwardBacktestConfig(warmup_candles=2)
        )
        with self.storage() as storage:
            use_case = RunAndArchiveWalkForwardBacktest(
                ResearchArchiveService(storage),
                runner,
            )
            receipt = use_case.execute(
                _market_series(),
                stored_at=self.stored_at,
            )

        with self.storage() as reopened:
            loaded = reopened.get_backtest_run(receipt.run_id)

        self.assertEqual(loaded, receipt.backtest_run)

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

    def test_migrates_version_one_json_into_normalized_schema(self):
        market = stored_market_series(
            _market_series(),
            stored_at=self.stored_at,
        )
        backtest = stored_backtest_run(
            _backtest_result(),
            run_id="legacy-run",
            stored_at=self.stored_at,
        )
        legacy_backtest_payload = backtest.model_dump(mode="json")
        legacy_backtest_payload.pop("storage_schema_version")
        connection = duckdb.connect(str(self.database))
        try:
            connection.execute(
                "CREATE TABLE jarvis_storage_metadata ("
                "metadata_key VARCHAR PRIMARY KEY, "
                "metadata_value VARCHAR NOT NULL)"
            )
            connection.execute(
                "INSERT INTO jarvis_storage_metadata VALUES (?, ?)",
                ["schema_version", "1"],
            )
            connection.execute(
                "CREATE TABLE jarvis_market_series ("
                "dataset_id VARCHAR PRIMARY KEY, "
                "payload_fingerprint VARCHAR NOT NULL, "
                "stored_at TIMESTAMPTZ NOT NULL, exchange VARCHAR NOT NULL, "
                "symbol_token VARCHAR NOT NULL, interval VARCHAR NOT NULL, "
                "source VARCHAR NOT NULL, summary_json VARCHAR NOT NULL, "
                "payload_json VARCHAR NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE jarvis_backtest_runs ("
                "run_id VARCHAR PRIMARY KEY, "
                "result_fingerprint VARCHAR NOT NULL, "
                "market_fingerprint VARCHAR NOT NULL, "
                "stored_at TIMESTAMPTZ NOT NULL, backtest_id VARCHAR NOT NULL, "
                "engine_id VARCHAR NOT NULL, exchange VARCHAR NOT NULL, "
                "symbol_token VARCHAR NOT NULL, interval VARCHAR NOT NULL, "
                "summary_json VARCHAR NOT NULL, payload_json VARCHAR NOT NULL)"
            )
            series = market.series
            connection.execute(
                "INSERT INTO jarvis_market_series VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    market.dataset_id,
                    market.payload_fingerprint,
                    market.stored_at,
                    series.exchange,
                    series.symbol_token,
                    series.interval,
                    series.source,
                    market_series_summary(market).model_dump_json(),
                    market.model_dump_json(),
                ],
            )
            result = backtest.result
            connection.execute(
                "INSERT INTO jarvis_backtest_runs VALUES "
                "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    backtest.run_id,
                    backtest.result_fingerprint,
                    backtest.market_fingerprint,
                    backtest.stored_at,
                    result.backtest_id,
                    result.engine_id,
                    series.exchange,
                    series.symbol_token,
                    series.interval,
                    backtest_run_summary(backtest).model_dump_json(),
                    json.dumps(legacy_backtest_payload),
                ],
            )
        finally:
            connection.close()

        with self.storage() as migrated:
            version = migrated._connection.execute(
                "SELECT metadata_value FROM jarvis_storage_metadata "
                "WHERE metadata_key = 'schema_version'"
            ).fetchone()[0]
            candle_count = migrated._connection.execute(
                "SELECT count(*) FROM jarvis_market_candles"
            ).fetchone()[0]
            equity_count = migrated._connection.execute(
                "SELECT count(*) FROM jarvis_backtest_equity_points"
            ).fetchone()[0]
            loaded = migrated.get_backtest_run("legacy-run")

        self.assertEqual(version, "2")
        self.assertEqual(candle_count, 2)
        self.assertEqual(equity_count, 2)
        self.assertEqual(loaded.run_id, "legacy-run")
        self.assertEqual(loaded.storage_schema_version, 1)

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
