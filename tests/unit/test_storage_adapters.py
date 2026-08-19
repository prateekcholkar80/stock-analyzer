import unittest
from datetime import datetime, timedelta
from hashlib import sha256
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.exceptions import StorageConflictError
from app.models.backtest import (
    BacktestEquityPoint,
    WalkForwardBacktestConfig,
    WalkForwardBacktestResult,
    WalkForwardPerformance,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.storage import (
    BacktestRunQuery,
    MarketSeriesQuery,
    StoredMarketSeries,
    stored_market_series,
)
from app.services.research_archive import ResearchArchiveService
from app.storage.adapters.in_memory import InMemoryJarvisStorage
from app.storage.repositories import JarvisStorageAdapter


IST = ZoneInfo("Asia/Kolkata")


def _market_series(
    *,
    symbol_token: str = "2885",
    symbol: str = "RELIANCE-EQ",
    interval: str = "ONE_DAY",
    day_offset: int = 0,
) -> HistoricalCandleSeries:
    first_at = datetime(
        2026,
        1,
        1,
        15,
        30,
        tzinfo=IST,
    ) + timedelta(days=day_offset)
    candles = [
        Candle(
            timestamp=first_at + timedelta(days=index),
            open=100.0 + index,
            high=102.0 + index,
            low=99.0 + index,
            close=101.0 + index,
            volume=1_000 + index,
        )
        for index in range(2)
    ]
    return HistoricalCandleSeries(
        exchange="NSE",
        symbol_token=symbol_token,
        symbol=symbol,
        interval=interval,
        candles=candles,
        retrieved_at=first_at + timedelta(days=3),
        source="test",
    )


def _backtest_result(
    *,
    symbol_token: str = "2885",
    symbol: str = "RELIANCE-EQ",
) -> WalkForwardBacktestResult:
    series = _market_series(symbol_token=symbol_token, symbol=symbol)
    config = WalkForwardBacktestConfig(warmup_candles=2)
    initial_capital = config.execution.initial_capital
    equity_curve = [
        BacktestEquityPoint(
            candle_index=index,
            timestamp=candle.timestamp,
            close=candle.close,
            equity=initial_capital,
            running_peak=initial_capital,
            drawdown_amount=0.0,
            drawdown_percentage=0.0,
        )
        for index, candle in enumerate(series.candles)
    ]
    performance = WalkForwardPerformance(
        interval=series.interval,
        initial_capital=initial_capital,
        final_equity=initial_capital,
        net_profit=0.0,
        total_return_percentage=0.0,
        attempted_evaluations=0,
        technical_failures=0,
        no_trade_evaluations=0,
        skipped_open_position_evaluations=0,
        entered_trades=0,
        closed_trades=0,
        open_trades=0,
        winning_trades=0,
        losing_trades=0,
        breakeven_trades=0,
        win_rate_percentage=0.0,
        gross_profit=0.0,
        gross_loss=0.0,
        total_costs=0.0,
        maximum_drawdown_amount=0.0,
        maximum_drawdown_percentage=0.0,
        exposure_percentage=0.0,
        maximum_consecutive_losses=0,
        direction_breakdown={},
        stance_breakdown={},
    )
    return WalkForwardBacktestResult(
        backtest_id=config.backtest_id,
        engine_id="jarvis.walk_forward_engine.v1",
        configuration_fingerprint=sha256(
            config.model_dump_json().encode("utf-8")
        ).hexdigest(),
        market_series=series,
        config=config,
        resolved_warmup_candles=2,
        evaluations=[],
        trades=[],
        equity_curve=equity_curve,
        performance=performance,
    )


class StorageAdapterTests(unittest.TestCase):
    def setUp(self):
        self.storage = InMemoryJarvisStorage()
        self.archive = ResearchArchiveService(self.storage)
        self.stored_at = datetime(
            2026,
            8,
            19,
            10,
            tzinfo=IST,
        )

    def test_reference_adapter_implements_database_neutral_contract(self):
        self.assertIsInstance(self.storage, JarvisStorageAdapter)
        self.assertEqual(self.archive.adapter_name, "in_memory")

    def test_archives_and_loads_market_series_without_aliasing(self):
        stored = self.archive.archive_market_series(
            _market_series(),
            stored_at=self.stored_at,
        )
        stored.series.candles.clear()

        loaded = self.archive.get_market_series(stored.dataset_id)

        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded.series.candles), 2)
        self.assertTrue(stored.dataset_id.startswith("market:"))

    def test_repeated_market_payload_is_idempotent(self):
        series = _market_series()
        first = self.archive.archive_market_series(
            series,
            stored_at=self.stored_at,
        )
        second = self.archive.archive_market_series(
            series,
            stored_at=self.stored_at + timedelta(hours=1),
        )

        self.assertEqual(first, second)
        self.assertEqual(
            len(self.archive.list_market_series()),
            1,
        )

    def test_storage_envelope_rejects_naive_time_and_tampered_digest(self):
        series = _market_series()
        valid = stored_market_series(
            series,
            stored_at=self.stored_at,
        )
        with self.assertRaises(ValidationError):
            stored_market_series(
                series,
                stored_at=self.stored_at.replace(tzinfo=None),
            )
        with self.assertRaises(ValidationError):
            StoredMarketSeries(
                dataset_id=valid.dataset_id,
                payload_fingerprint="0" * 64,
                stored_at=self.stored_at,
                series=series,
            )

    def test_rejects_conflicting_market_payload_for_same_identifier(self):
        original = stored_market_series(
            _market_series(),
            dataset_id="market-dataset-1",
            stored_at=self.stored_at,
        )
        conflicting = stored_market_series(
            _market_series(symbol_token="1333", symbol="HDFCBANK-EQ"),
            dataset_id="market-dataset-1",
            stored_at=self.stored_at,
        )
        self.storage.save_market_series(original)

        with self.assertRaises(StorageConflictError):
            self.storage.save_market_series(conflicting)

    def test_filters_orders_and_pages_market_summaries(self):
        self.archive.archive_market_series(
            _market_series(symbol_token="1333", symbol="HDFCBANK-EQ"),
            dataset_id="hdfc",
            stored_at=self.stored_at,
        )
        self.archive.archive_market_series(
            _market_series(day_offset=1),
            dataset_id="reliance-old",
            stored_at=self.stored_at + timedelta(hours=1),
        )
        self.archive.archive_market_series(
            _market_series(day_offset=2),
            dataset_id="reliance-new",
            stored_at=self.stored_at + timedelta(hours=2),
        )

        results = self.archive.list_market_series(
            MarketSeriesQuery(
                symbol_token="2885",
                limit=1,
                offset=1,
            )
        )

        self.assertEqual(
            [item.dataset_id for item in results],
            ["reliance-old"],
        )
        self.assertEqual(results[0].candle_count, 2)

    def test_archives_backtest_and_its_source_market_data(self):
        stored = self.archive.archive_backtest_run(
            _backtest_result(),
            stored_at=self.stored_at,
        )

        loaded = self.archive.get_backtest_run(stored.run_id)
        summaries = self.archive.list_backtest_runs()

        self.assertEqual(loaded, stored)
        self.assertTrue(stored.run_id.startswith("backtest:"))
        self.assertEqual(len(self.archive.list_market_series()), 1)
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].symbol, "RELIANCE-EQ")
        self.assertEqual(summaries[0].entered_trades, 0)
        self.assertEqual(summaries[0].final_equity, 100_000.0)

    def test_can_archive_backtest_without_separate_market_dataset(self):
        stored = self.archive.archive_backtest_run(
            _backtest_result(),
            stored_at=self.stored_at,
            archive_market_data=False,
        )

        self.assertIsNotNone(self.archive.get_backtest_run(stored.run_id))
        self.assertEqual(self.archive.list_market_series(), ())

    def test_rejects_conflicting_backtest_payload_for_same_run_id(self):
        self.archive.archive_backtest_run(
            _backtest_result(),
            run_id="run-1",
            stored_at=self.stored_at,
        )

        with self.assertRaises(StorageConflictError):
            self.archive.archive_backtest_run(
                _backtest_result(
                    symbol_token="1333",
                    symbol="HDFCBANK-EQ",
                ),
                run_id="run-1",
                stored_at=self.stored_at,
            )

    def test_filters_backtest_summaries_without_loading_aggregates(self):
        self.archive.archive_backtest_run(
            _backtest_result(),
            run_id="reliance-run",
            stored_at=self.stored_at,
        )
        self.archive.archive_backtest_run(
            _backtest_result(
                symbol_token="1333",
                symbol="HDFCBANK-EQ",
            ),
            run_id="hdfc-run",
            stored_at=self.stored_at + timedelta(hours=1),
        )

        results = self.archive.list_backtest_runs(
            BacktestRunQuery(symbol_token="2885")
        )

        self.assertEqual(
            [item.run_id for item in results],
            ["reliance-run"],
        )

    def test_delete_operations_are_explicit_and_idempotent(self):
        series = self.archive.archive_market_series(
            _market_series(),
            stored_at=self.stored_at,
        )
        run = self.archive.archive_backtest_run(
            _backtest_result(),
            stored_at=self.stored_at,
        )

        self.assertTrue(
            self.archive.delete_market_series(series.dataset_id)
        )
        self.assertFalse(
            self.archive.delete_market_series(series.dataset_id)
        )
        self.assertTrue(self.archive.delete_backtest_run(run.run_id))
        self.assertFalse(self.archive.delete_backtest_run(run.run_id))

    def test_rejects_invalid_query_ranges_and_boolean_paging(self):
        with self.assertRaises(ValidationError):
            MarketSeriesQuery(
                stored_from=self.stored_at + timedelta(days=1),
                stored_to=self.stored_at,
            )
        with self.assertRaises(ValidationError):
            BacktestRunQuery(limit=True)
        with self.assertRaises(ValidationError):
            BacktestRunQuery(stored_from=self.stored_at.replace(tzinfo=None))

    def test_rejects_an_incomplete_adapter(self):
        with self.assertRaisesRegex(ValueError, "complete storage adapter"):
            ResearchArchiveService(object())


if __name__ == "__main__":
    unittest.main()
