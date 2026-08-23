import unittest
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from app.agents.bear_agent import BearDebateAgent
from app.agents.bull_agent import BullDebateAgent
from app.agents.debate_judge_agent import DebateJudgeAgent
from app.agents.trade_planning_agent import (
    TradePlanningAgent,
    TradePlanningAgentConfig,
)
from app.commands.swing_analysis import JarvisSwingAnalysisCommandHandler
from app.models.interaction import SwingAnalysisCommand
from app.models.market import Candle, HistoricalCandleSeries
from app.models.multi_timeframe_trade import (
    MultiTimeframeTradeDisposition,
    MultiTimeframeTradeReason,
)
from app.models.trade_setup import TradeDirection
from app.models.storage import (
    MultiTimeframeEndToEndSwingAnalysisResult,
    RollingFetchReceipt,
    StoredMarketSeries,
    payload_fingerprint,
)
from app.orchestration.agent_orchestrator import AgentOrchestrator
from app.orchestration.debate_orchestrator import (
    DebateOrchestrator,
    DebateOrchestratorConfig,
)
from app.use_cases.run_end_to_end_multi_timeframe_swing_analysis import (
    RunEndToEndMultiTimeframeSwingAnalysis,
)
from app.use_cases.derive_swing_timeframes import DeriveSwingTimeframes
from app.use_cases.build_multi_timeframe_long_trade_plan import (
    BuildMultiTimeframeLongTradePlan,
)
from tests.unit.test_build_multi_timeframe_evidence import (
    _cyclical_timeframes,
)
from tests.unit.test_multi_timeframe_debate import _Gateway
from tests.unit.test_run_end_to_end_swing_analysis import (
    _successful_llm_preflight,
)


class _RollingFetch:
    def __init__(self, receipt):
        self.receipt = receipt
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.receipt


def _receipt(timeframes=None):
    hourly = (timeframes or _cyclical_timeframes()).hourly
    stored = StoredMarketSeries(
        dataset_id="market:multi-timeframe-test",
        payload_fingerprint=payload_fingerprint(hourly),
        stored_at=datetime.now(UTC),
        series=hourly,
    )
    return RollingFetchReceipt(
        use_case_id="jarvis.pull_rolling_market_series.v1",
        adapter_name="test.market.adapter",
        stored=stored,
        new_candle_count=len(hourly.candles),
        chunk_request_count=1,
    )


def _use_case(*, timeframes=None, winner="bullish"):
    resolved_timeframes = timeframes or _cyclical_timeframes()
    receipt = _receipt(resolved_timeframes)
    analysis = AgentOrchestrator().run_multi_timeframe_analysis(
        resolved_timeframes
    )
    package = analysis.evidence_package
    weekly_id = package.weekly.evidence[0].qualified_evidence_id
    daily_id = package.daily.evidence[0].qualified_evidence_id
    bull = BullDebateAgent(
        _Gateway(
            [
                {
                    "thesis": "Weekly structure supports Bull.",
                    "evidence_citations": [weekly_id],
                    "rebuts_argument_id": None,
                    "timeframe_relationship": "mixed",
                }
            ],
            model="fake-bull",
            fingerprint="a" * 64,
        )
    )
    bear = BearDebateAgent(
        _Gateway(
            [
                {
                    "thesis": "Daily timing requires caution.",
                    "evidence_citations": [daily_id],
                    "rebuts_argument_id": (
                        "jarvis.bull_debate_agent.v1:"
                        f"{package.package_fingerprint}:1"
                    ),
                    "timeframe_relationship": "mixed",
                }
            ],
            model="fake-bear",
            fingerprint="b" * 64,
        )
    )
    judge = DebateJudgeAgent(
        _Gateway(
            [
                {
                    "winner": winner,
                    "confidence_percentage": 70.0,
                    "decisive_evidence_ids": [weekly_id],
                    "bull_case_summary": "Weekly structure is constructive.",
                    "bear_case_summary": "Daily timing needs confirmation.",
                    "rationale": "The bullish case is conditional.",
                }
            ],
            model="fake-judge",
            fingerprint="c" * 64,
        )
    )
    debate = DebateOrchestrator(
        bull_agent=bull,
        bear_agent=bear,
        judge_agent=judge,
        config=DebateOrchestratorConfig(max_rounds=1),
    )
    return RunEndToEndMultiTimeframeSwingAnalysis(
        _RollingFetch(receipt),
        debate_orchestrator=debate,
        llm_preflight=_successful_llm_preflight(),
    )


def _trending_timeframes():
    ist = ZoneInfo("Asia/Kolkata")
    day = datetime(2024, 1, 1, tzinfo=ist)
    candles = []
    trading_day = 0
    while trading_day < 400:
        if day.weekday() < 5:
            base = 100 + trading_day * 0.4
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
                close = base + offset * 0.03
                candles.append(
                    Candle(
                        timestamp=day.replace(hour=hour, minute=minute),
                        open=close - 0.1,
                        high=close + 0.3,
                        low=close - 0.3,
                        close=close,
                        volume=100_000 + trading_day * 1_000 + offset,
                    )
                )
            trading_day += 1
        day += timedelta(days=1)
    return DeriveSwingTimeframes().execute(
        HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_HOUR",
            candles=candles,
            retrieved_at=day + timedelta(days=1),
            source="test_market",
        )
    )


class RunEndToEndMultiTimeframeSwingAnalysisTests(unittest.TestCase):
    def test_runs_real_multi_timeframe_pipeline_and_returns_context(self):
        use_case = _use_case()

        result = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
        )

        self.assertIsInstance(
            result,
            MultiTimeframeEndToEndSwingAnalysisResult,
        )
        self.assertTrue(result.technical_review.decision.accepted)
        self.assertTrue(result.debate_result.decision.accepted)
        self.assertEqual(
            result.technical_review.evidence_package.daily.interval,
            "ONE_DAY",
        )
        self.assertEqual(
            result.technical_review.evidence_package.weekly.interval,
            "ONE_WEEK",
        )

    def test_command_response_automatically_carries_follow_up_context(self):
        use_case = _use_case()
        command = SwingAnalysisCommand(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_HOUR",
        )

        response = JarvisSwingAnalysisCommandHandler(
            lambda: use_case
        ).execute(command)

        self.assertIsNotNone(response.multi_timeframe_review)
        self.assertIsNotNone(response.multi_timeframe_debate)
        self.assertEqual(
            response.multi_timeframe_review.decision.decision_id,
            response.multi_timeframe_debate.submission.technical_decision_id,
        )

    def test_builds_actionable_long_plan_with_exact_two_r_target(self):
        result = _use_case(timeframes=_trending_timeframes()).execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
        )

        outcome = result.trade_plan_result
        plan = outcome.daily_planning_result.approved_trade_intent
        self.assertIs(
            outcome.disposition,
            MultiTimeframeTradeDisposition.ACTIONABLE,
        )
        self.assertIs(plan.evaluation.direction, TradeDirection.LONG)
        self.assertEqual(plan.evaluation.minimum_reward_to_risk, 2.0)
        self.assertAlmostEqual(
            plan.evaluation.minimum_target.target_price,
            plan.evaluation.entry_price + 2 * plan.evaluation.risk_per_unit,
        )

    def test_non_bullish_judge_produces_no_trade_and_never_short(self):
        result = _use_case(winner="bearish").execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
        )

        outcome = result.trade_plan_result
        self.assertIs(
            outcome.disposition,
            MultiTimeframeTradeDisposition.NO_TRADE,
        )
        self.assertIs(
            outcome.reason,
            MultiTimeframeTradeReason.JUDGE_NOT_BULLISH,
        )
        self.assertIsNone(outcome.daily_planning_result)

    def test_rejects_trade_chain_tampering_and_non_two_r_policy(self):
        result = _use_case().execute(
            "NSE", "2885", "RELIANCE-EQ", "ONE_HOUR"
        )
        tampered_trade = result.trade_plan_result.model_copy(
            update={"technical_package_fingerprint": "0" * 64}
        )

        with self.assertRaisesRegex(ValueError, "approved chain"):
            MultiTimeframeEndToEndSwingAnalysisResult(
                use_case_id=result.use_case_id,
                market_dataset_id=result.market_dataset_id,
                fetch=result.fetch,
                technical_review=result.technical_review,
                debate_result=result.debate_result,
                trade_plan_result=tampered_trade,
            )

        non_two_r_agent = TradePlanningAgent(
            TradePlanningAgentConfig(
                minimum_reward_to_risk=1.5,
                preferred_reward_to_risk=3.0,
            )
        )
        with self.assertRaisesRegex(ValueError, "1:2 minimum"):
            BuildMultiTimeframeLongTradePlan(
                AgentOrchestrator(trade_planning_agent=non_two_r_agent)
            )

    def test_rejects_non_hourly_input_and_missing_dependencies(self):
        with self.assertRaisesRegex(ValueError, "configured debate"):
            RunEndToEndMultiTimeframeSwingAnalysis(object())
        with self.assertRaisesRegex(ValueError, "ONE_HOUR"):
            _use_case().execute(
                "NSE",
                "2885",
                "RELIANCE-EQ",
                "ONE_DAY",
            )


if __name__ == "__main__":
    unittest.main()
