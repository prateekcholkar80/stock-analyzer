import threading
import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.agents.technical_swing_agent import (
    DailyTechnicalSwingAgent,
    WeeklyTechnicalSwingAgent,
)
from app.exceptions import AgentSubmissionRejectedError
from app.models.market import Candle, HistoricalCandleSeries
from app.models.timeframes import MultiTimeframeTechnicalAnalysis
from app.orchestration.timeframe_technical_orchestrator import (
    ParallelTimeframeTechnicalOrchestrator,
)
from app.use_cases.derive_swing_timeframes import DeriveSwingTimeframes


IST = ZoneInfo("Asia/Kolkata")


def _timeframes(week_count=60):
    day = datetime(2024, 1, 1, tzinfo=IST)
    candles = []
    trading_day = 0
    while trading_day < week_count * 5:
        if day.weekday() < 5:
            base = 100 + trading_day * 0.2
            for offset, (hour, minute) in enumerate(
                (
                    (9, 15),
                    (10, 15),
                    (11, 15),
                    (12, 15),
                    (13, 15),
                    (14, 15),
                    (15, 30),
                )
            ):
                close = base + offset * 0.05
                candles.append(
                    Candle(
                        timestamp=day.replace(hour=hour, minute=minute),
                        open=close - 0.1,
                        high=close + 0.5,
                        low=close - 0.5,
                        close=close,
                        volume=100_000 + trading_day * 100 + offset,
                    )
                )
            trading_day += 1
        day += timedelta(days=1)
    hourly = HistoricalCandleSeries(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        interval="ONE_HOUR",
        candles=candles,
        retrieved_at=day + timedelta(days=1),
        source="test_market",
    )
    return DeriveSwingTimeframes().execute(hourly)


class _BarrierAgent:
    def __init__(self, delegate, barrier):
        self.delegate = delegate
        self.barrier = barrier
        self.agent_id = delegate.agent_id

    @property
    def evaluator_id(self):
        return self.delegate.evaluator_id

    @property
    def configuration_fingerprint(self):
        return self.delegate.configuration_fingerprint

    def execute(self, series):
        self.barrier.wait(timeout=2)
        return self.delegate.execute(series)


class _TamperingAgent(_BarrierAgent):
    def execute(self, series):
        submission = super().execute(series)
        return submission.model_copy(
            update={"input_fingerprint": "0" * 64}
        )


class ParallelTimeframeTechnicalOrchestratorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.timeframes = _timeframes()

    def test_runs_distinct_daily_and_weekly_agents(self):
        result = ParallelTimeframeTechnicalOrchestrator().execute(
            self.timeframes
        )

        self.assertEqual(result.execution_mode, "parallel")
        self.assertEqual(result.daily_submission.interval, "ONE_DAY")
        self.assertEqual(result.weekly_submission.interval, "ONE_WEEK")
        self.assertNotEqual(
            result.daily_submission.agent_id,
            result.weekly_submission.agent_id,
        )
        self.assertEqual(result.daily_submission.evidence_count, 12)
        self.assertEqual(result.weekly_submission.evidence_count, 12)
        self.assertTrue(result.daily_validation.accepted)
        self.assertTrue(result.weekly_validation.accepted)

    def test_execution_is_concurrent_not_sequential(self):
        barrier = threading.Barrier(2)
        orchestrator = ParallelTimeframeTechnicalOrchestrator(
            daily_agent=_BarrierAgent(
                DailyTechnicalSwingAgent(),
                barrier,
            ),
            weekly_agent=_BarrierAgent(
                WeeklyTechnicalSwingAgent(),
                barrier,
            ),
        )

        result = orchestrator.execute(self.timeframes)

        self.assertEqual(barrier.n_waiting, 0)
        self.assertTrue(result.daily_validation.accepted)
        self.assertTrue(result.weekly_validation.accepted)

    def test_rejects_tampered_submission_before_release(self):
        barrier = threading.Barrier(2)
        orchestrator = ParallelTimeframeTechnicalOrchestrator(
            daily_agent=_BarrierAgent(
                DailyTechnicalSwingAgent(),
                barrier,
            ),
            weekly_agent=_TamperingAgent(
                WeeklyTechnicalSwingAgent(),
                barrier,
            ),
        )

        with self.assertRaisesRegex(
            AgentSubmissionRejectedError,
            "weekly technical submission failed validation",
        ):
            orchestrator.execute(self.timeframes)

    def test_combined_model_rejects_fingerprint_tampering(self):
        result = ParallelTimeframeTechnicalOrchestrator().execute(
            self.timeframes
        )

        with self.assertRaisesRegex(ValidationError, "combined technical"):
            MultiTimeframeTechnicalAnalysis(
                **(
                    result.model_dump(exclude_computed_fields=True)
                    | {"combined_fingerprint": "0" * 64}
                )
            )

    def test_requires_valid_timeframes_and_distinct_agents(self):
        with self.assertRaisesRegex(ValueError, "swing timeframes"):
            ParallelTimeframeTechnicalOrchestrator().execute(object())
        same = DailyTechnicalSwingAgent()
        with self.assertRaisesRegex(ValueError, "identities must differ"):
            ParallelTimeframeTechnicalOrchestrator(
                daily_agent=same,
                weekly_agent=same,
            )

    def test_specialized_agents_reject_the_other_timeframe(self):
        with self.assertRaisesRegex(ValueError, "ONE_DAY"):
            DailyTechnicalSwingAgent().execute(self.timeframes.weekly)
        with self.assertRaisesRegex(ValueError, "ONE_WEEK"):
            WeeklyTechnicalSwingAgent().execute(self.timeframes.daily)


if __name__ == "__main__":
    unittest.main()
