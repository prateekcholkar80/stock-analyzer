import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.analytics.walk_forward import WalkForwardBacktestEngine
from app.exceptions import StorageConflictError, StorageError
from app.models.backtest import WalkForwardBacktestConfig
from app.models.storage import (
    WalkForwardBacktestArchiveReceipt,
    stored_backtest_run,
)
from app.services.research_archive import ResearchArchiveService
from app.storage.adapters.in_memory import InMemoryJarvisStorage
from app.use_cases.run_and_archive_backtest import (
    BacktestArchiveWriter,
    RunAndArchiveWalkForwardBacktest,
    WalkForwardBacktestRunner,
    run_and_archive_walk_forward_backtest,
)
from tests.unit.test_storage_adapters import (
    _backtest_result,
    _market_series,
)


IST = ZoneInfo("Asia/Kolkata")


class _ScriptedRunner:
    def __init__(self, result=None):
        self.result = result or _backtest_result()
        self.assigned_series = []

    def run(self, market_series):
        self.assigned_series.append(market_series)
        return self.result


class _FailingRunner:
    def run(self, market_series):
        raise RuntimeError("scripted backtest failure")


class _WrongResultRunner:
    def run(self, market_series):
        return {"not": "a backtest result"}


class _FailingArchive:
    adapter_name = "failing_archive"

    def archive_backtest_run(
        self,
        result,
        *,
        run_id=None,
        stored_at=None,
        archive_market_data=True,
    ):
        raise StorageError("scripted persistence failure")


class _DifferentResultArchive:
    adapter_name = "different_result_archive"

    def __init__(self, different_result, stored_at):
        self.different_result = different_result
        self.stored_at = stored_at

    def archive_backtest_run(
        self,
        result,
        *,
        run_id=None,
        stored_at=None,
        archive_market_data=True,
    ):
        return stored_backtest_run(
            self.different_result,
            run_id=run_id,
            stored_at=stored_at or self.stored_at,
        )


class _IgnoringRunIdArchive:
    adapter_name = "ignoring_run_id_archive"

    def archive_backtest_run(
        self,
        result,
        *,
        run_id=None,
        stored_at=None,
        archive_market_data=True,
    ):
        return stored_backtest_run(
            result,
            run_id="different-run-id",
            stored_at=stored_at,
        )


class RunAndArchiveBacktestTests(unittest.TestCase):
    def setUp(self):
        self.storage = InMemoryJarvisStorage()
        self.archive = ResearchArchiveService(self.storage)
        self.runner = _ScriptedRunner()
        self.use_case = RunAndArchiveWalkForwardBacktest(
            self.archive,
            self.runner,
        )
        self.stored_at = datetime(2026, 8, 19, 16, 30, tzinfo=IST)

    def test_ports_accept_real_runner_and_archive_implementations(self):
        self.assertIsInstance(self.runner, WalkForwardBacktestRunner)
        self.assertIsInstance(self.archive, BacktestArchiveWriter)

    def test_runs_and_archives_complete_validated_result(self):
        source = _market_series()

        receipt = self.use_case.execute(
            source,
            stored_at=self.stored_at,
        )

        self.assertEqual(
            receipt.use_case_id,
            "jarvis.run_and_archive_walk_forward_backtest.v1",
        )
        self.assertEqual(receipt.adapter_name, "in_memory")
        self.assertEqual(receipt.result, self.runner.result)
        self.assertEqual(receipt.stored_at, self.stored_at)
        self.assertTrue(receipt.run_id.startswith("backtest:"))
        self.assertTrue(receipt.market_dataset_id.startswith("market:"))
        self.assertEqual(len(self.archive.list_market_series()), 1)
        self.assertEqual(len(self.archive.list_backtest_runs()), 1)
        self.assertEqual(self.runner.assigned_series, [source])
        self.assertIsNot(self.runner.assigned_series[0], source)

    def test_convenience_function_supports_explicit_run_identifier(self):
        receipt = run_and_archive_walk_forward_backtest(
            _market_series(),
            archive=self.archive,
            runner=self.runner,
            run_id="reliance-swing-run-1",
            stored_at=self.stored_at,
        )

        self.assertEqual(receipt.run_id, "reliance-swing-run-1")
        self.assertIsNotNone(
            self.archive.get_backtest_run("reliance-swing-run-1")
        )

    def test_repeated_identical_execution_is_idempotent(self):
        first = self.use_case.execute(
            _market_series(),
            stored_at=self.stored_at,
        )
        repeated = self.use_case.execute(
            _market_series(),
            stored_at=self.stored_at + timedelta(hours=1),
        )

        self.assertEqual(repeated, first)
        self.assertEqual(len(self.archive.list_market_series()), 1)
        self.assertEqual(len(self.archive.list_backtest_runs()), 1)
        self.assertEqual(len(self.runner.assigned_series), 2)

    def test_real_walk_forward_engine_runs_through_use_case(self):
        runner = WalkForwardBacktestEngine(
            config=WalkForwardBacktestConfig(warmup_candles=2)
        )
        use_case = RunAndArchiveWalkForwardBacktest(
            self.archive,
            runner,
        )

        receipt = use_case.execute(
            _market_series(),
            stored_at=self.stored_at,
        )

        self.assertEqual(receipt.result.evaluations, [])
        self.assertEqual(receipt.result.trades, [])
        self.assertEqual(len(receipt.result.equity_curve), 2)
        self.assertEqual(receipt.result.performance.entered_trades, 0)

    def test_runner_failure_does_not_write_any_archive_data(self):
        use_case = RunAndArchiveWalkForwardBacktest(
            self.archive,
            _FailingRunner(),
        )

        with self.assertRaisesRegex(RuntimeError, "backtest failure"):
            use_case.execute(
                _market_series(),
                stored_at=self.stored_at,
            )

        self.assertEqual(self.archive.list_market_series(), ())
        self.assertEqual(self.archive.list_backtest_runs(), ())

    def test_rejects_non_domain_runner_result_before_storage(self):
        use_case = RunAndArchiveWalkForwardBacktest(
            self.archive,
            _WrongResultRunner(),
        )

        with self.assertRaisesRegex(ValueError, "validated walk-forward"):
            use_case.execute(
                _market_series(),
                stored_at=self.stored_at,
            )

        self.assertEqual(self.archive.list_market_series(), ())
        self.assertEqual(self.archive.list_backtest_runs(), ())

    def test_rejects_result_for_different_market_assignment(self):
        runner = _ScriptedRunner(
            _backtest_result(
                symbol_token="1333",
                symbol="HDFCBANK-EQ",
            )
        )
        use_case = RunAndArchiveWalkForwardBacktest(
            self.archive,
            runner,
        )

        with self.assertRaisesRegex(ValueError, "exact assigned market"):
            use_case.execute(
                _market_series(),
                stored_at=self.stored_at,
            )

        self.assertEqual(self.archive.list_market_series(), ())
        self.assertEqual(self.archive.list_backtest_runs(), ())

    def test_storage_failure_is_not_hidden_or_reclassified(self):
        use_case = RunAndArchiveWalkForwardBacktest(
            _FailingArchive(),
            self.runner,
        )

        with self.assertRaisesRegex(StorageError, "persistence failure"):
            use_case.execute(
                _market_series(),
                stored_at=self.stored_at,
            )

    def test_storage_conflict_preserves_original_backtest(self):
        original = self.use_case.execute(
            _market_series(),
            run_id="shared-run",
            stored_at=self.stored_at,
        )
        conflicting = RunAndArchiveWalkForwardBacktest(
            self.archive,
            _ScriptedRunner(
                _backtest_result(
                    symbol_token="1333",
                    symbol="HDFCBANK-EQ",
                )
            ),
        )

        with self.assertRaises(StorageConflictError):
            conflicting.execute(
                _market_series(
                    symbol_token="1333",
                    symbol="HDFCBANK-EQ",
                ),
                run_id="shared-run",
                stored_at=self.stored_at,
            )

        loaded = self.archive.get_backtest_run("shared-run")
        self.assertEqual(loaded, original.backtest_run)
        self.assertEqual(len(self.archive.list_backtest_runs()), 1)

    def test_rejects_archive_writer_returning_a_different_result(self):
        archive = _DifferentResultArchive(
            _backtest_result(
                symbol_token="1333",
                symbol="HDFCBANK-EQ",
            ),
            self.stored_at,
        )
        use_case = RunAndArchiveWalkForwardBacktest(
            archive,
            self.runner,
        )

        with self.assertRaisesRegex(ValueError, "different backtest"):
            use_case.execute(
                _market_series(),
                stored_at=self.stored_at,
            )

    def test_rejects_archive_writer_ignoring_requested_run_identifier(self):
        use_case = RunAndArchiveWalkForwardBacktest(
            _IgnoringRunIdArchive(),
            self.runner,
        )

        with self.assertRaisesRegex(ValueError, "requested run identifier"):
            use_case.execute(
                _market_series(),
                run_id="required-run-id",
                stored_at=self.stored_at,
            )

    def test_receipt_rejects_tampered_market_dataset_reference(self):
        receipt = self.use_case.execute(
            _market_series(),
            stored_at=self.stored_at,
        )

        with self.assertRaises(ValidationError):
            WalkForwardBacktestArchiveReceipt(
                use_case_id=receipt.use_case_id,
                adapter_name=receipt.adapter_name,
                market_dataset_id="market:wrong",
                backtest_run=receipt.backtest_run,
            )

    def test_naive_storage_time_is_rejected_without_writing(self):
        with self.assertRaises(ValidationError):
            self.use_case.execute(
                _market_series(),
                stored_at=self.stored_at.replace(tzinfo=None),
            )

        self.assertEqual(self.archive.list_market_series(), ())
        self.assertEqual(self.archive.list_backtest_runs(), ())

    def test_rejects_invalid_input_and_incomplete_dependencies(self):
        with self.assertRaisesRegex(ValueError, "historical candles"):
            self.use_case.execute(object())
        with self.assertRaisesRegex(ValueError, "backtest runner"):
            RunAndArchiveWalkForwardBacktest(self.archive, object())
        with self.assertRaisesRegex(ValueError, "archive writer"):
            RunAndArchiveWalkForwardBacktest(object(), self.runner)


if __name__ == "__main__":
    unittest.main()
