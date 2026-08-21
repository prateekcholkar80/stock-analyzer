import unittest
from datetime import UTC, datetime, timedelta
from math import sin
from zoneinfo import ZoneInfo

from tests.unit._debate_fixtures import build_approved_technical_result

from app.agents.bear_agent import BearDebateAgent
from app.agents.bull_agent import BullDebateAgent
from app.agents.debate_judge_agent import DebateJudgeAgent
from app.agents.technical_swing_agent import TechnicalSwingAgent
from app.exceptions import AgentSubmissionRejectedError
from app.models.agentic import (
    AgenticSwingAnalysisResult,
    JarvisJudgeDecision,
    JarvisJudgeVerdict,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.storage import MarketSeriesQuery, DebateRunQuery
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.debate_orchestrator import (
    DebateOrchestrator,
    DebateOrchestratorConfig,
)
from app.services.research_archive import ResearchArchiveService
from app.storage.adapters.in_memory import InMemoryJarvisStorage
from app.use_cases.pull_rolling_market_series import (
    PullRollingMarketSeries,
    RollingFetchConfig,
)
from app.use_cases.run_end_to_end_swing_analysis import (
    RunEndToEndSwingAnalysis,
)


class StubRejectingAgentOrchestrator:
    def run_swing_analysis(self, market_series):
        result = build_approved_technical_result()
        rejected_decision = JarvisJudgeDecision(
            decision_id=result.decision.decision_id,
            judge_id=result.decision.judge_id,
            submission_id=result.submission.submission_id,
            verdict=JarvisJudgeVerdict.REJECTED,
            decided_at=result.decision.decided_at,
            passed_checks=result.decision.passed_checks,
            reasons=("test rejection",),
        )
        return AgenticSwingAnalysisResult(
            orchestrator_id=result.orchestrator_id,
            submission=result.submission,
            decision=rejected_decision,
        )


IST = ZoneInfo("Asia/Kolkata")


class FakeLLMClient:
    def __init__(self, draft_payloads):
        self.draft_payloads = list(draft_payloads)

    def complete_structured(self, *, config, system, messages, response_model):
        payload = self.draft_payloads.pop(0)
        return response_model(**payload)


def _synthetic_candles(count=60):
    candles = []
    start = datetime(2026, 1, 1, 9, 15, tzinfo=IST)
    for index in range(count):
        close = 100 + index * 0.3 + sin(index / 2)
        candles.append(
            Candle(
                timestamp=start + timedelta(hours=index),
                open=close - 0.2,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=100_000 + index * 1_000,
            )
        )
    return candles


def _synthetic_series(exchange, symbol_token, symbol, interval, count=60):
    return HistoricalCandleSeries(
        exchange=exchange,
        symbol_token=symbol_token,
        symbol=symbol,
        interval=interval,
        candles=_synthetic_candles(count),
        retrieved_at=datetime.now(UTC),
    )


def _reference_evidence_ids():
    """Real evidence ids the synthetic series will actually produce.

    Bull/Bear/Judge must cite real ids or generate_grounded() retries and
    exhausts the fake client's queued payloads, so this pre-computes them
    from the exact same synthetic series FakeGateway will hand back.
    """
    series = _synthetic_series("NSE", "2885", "RELIANCE-EQ", "ONE_HOUR")
    submission = TechnicalSwingAgent().execute(series)
    return [item.evidence_id for item in submission.profile.snapshot.evidence]


class FakeGateway:
    """Always returns the same synthetic series, regardless of the window."""

    def __init__(self, count=60):
        self._count = count
        self.calls = []

    def get_historical_series(
        self,
        *,
        exchange,
        symbol_token,
        symbol,
        interval,
        from_date,
        to_date,
        retrieved_at=None,
    ):
        self.calls.append((from_date, to_date))
        return _synthetic_series(
            exchange, symbol_token, symbol, interval, self._count
        )


def _build_use_case(archive, *, judge_winner="bullish", judge_confidence=80.0):
    evidence_ids = _reference_evidence_ids()
    bull_agent = BullDebateAgent(
        client=FakeLLMClient(
            [
                {
                    "thesis": "Trend alignment supports a bullish setup.",
                    "evidence_citations": [evidence_ids[0]],
                    "rebuts_argument_id": None,
                }
            ]
        )
    )
    bear_agent = BearDebateAgent(
        client=FakeLLMClient(
            [
                {
                    "thesis": "Momentum shows overextension risk.",
                    "evidence_citations": [evidence_ids[1]],
                    "rebuts_argument_id": None,
                }
            ]
        )
    )
    judge_agent = DebateJudgeAgent(
        client=FakeLLMClient(
            [
                {
                    "winner": judge_winner,
                    "confidence_percentage": judge_confidence,
                    "decisive_evidence_ids": [evidence_ids[0]],
                    "bull_case_summary": (
                        "Bull leaned on trend alignment."
                    ),
                    "bear_case_summary": (
                        "Bear leaned on momentum overextension."
                    ),
                    "rationale": "Trend evidence was stronger overall.",
                }
            ]
        )
    )
    debate_orchestrator = DebateOrchestrator(
        bull_agent=bull_agent,
        bear_agent=bear_agent,
        judge_agent=judge_agent,
        config=DebateOrchestratorConfig(max_rounds=1),
        archive=archive,
    )
    rolling_fetch = PullRollingMarketSeries(
        FakeGateway(),
        archive,
        config=RollingFetchConfig(default_lookback_days=3),
        sleep_fn=lambda seconds: None,
    )
    return RunEndToEndSwingAnalysis(
        rolling_fetch,
        agent_orchestrator=AgentOrchestrator(),
        debate_orchestrator=debate_orchestrator,
    )


class RunEndToEndSwingAnalysisTests(unittest.TestCase):
    def test_happy_path_chains_fetch_technical_and_debate(self):
        archive = ResearchArchiveService(InMemoryJarvisStorage())
        use_case = _build_use_case(archive)

        result = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=datetime(2026, 1, 5, tzinfo=IST),
        )

        self.assertEqual(
            result.use_case_id,
            "jarvis.run_end_to_end_swing_analysis.v1",
        )
        self.assertTrue(result.technical_result.decision.accepted)
        self.assertTrue(result.debate_result.decision.accepted)
        verdict = result.debate_result.submission.verdict
        self.assertEqual(verdict.bull_case_summary, "Bull leaned on trend alignment.")
        self.assertEqual(
            verdict.bear_case_summary,
            "Bear leaned on momentum overextension.",
        )

    def test_rejected_technical_submission_raises_and_skips_debate(self):
        archive = ResearchArchiveService(InMemoryJarvisStorage())
        use_case = _build_use_case(archive)
        use_case.agent_orchestrator = StubRejectingAgentOrchestrator()

        with self.assertRaisesRegex(
            AgentSubmissionRejectedError,
            "test rejection",
        ):
            use_case.execute(
                "NSE",
                "2885",
                "RELIANCE-EQ",
                "ONE_HOUR",
                to_date=datetime(2026, 1, 5, tzinfo=IST),
            )

        debate_summaries = archive.list_debate_runs(
            DebateRunQuery(exchange="NSE", symbol_token="2885")
        )
        self.assertEqual(len(debate_summaries), 0)

    def test_market_series_and_debate_run_are_both_archived(self):
        archive = ResearchArchiveService(InMemoryJarvisStorage())
        use_case = _build_use_case(archive)

        use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=datetime(2026, 1, 5, tzinfo=IST),
        )

        market_summaries = archive.list_market_series(
            MarketSeriesQuery(exchange="NSE", symbol_token="2885")
        )
        debate_summaries = archive.list_debate_runs(
            DebateRunQuery(exchange="NSE", symbol_token="2885")
        )
        self.assertEqual(len(market_summaries), 1)
        self.assertEqual(len(debate_summaries), 1)


if __name__ == "__main__":
    unittest.main()
