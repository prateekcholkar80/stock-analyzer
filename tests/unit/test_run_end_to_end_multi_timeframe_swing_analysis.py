import unittest
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from app.agents.bear_agent import BearDebateAgent
from app.agents.bull_agent import BullDebateAgent
from app.agents.debate_judge_agent import DebateJudgeAgent
from app.agents.trade_planning_agent import (
    TradePlanningAgent,
    TradePlanningAgentConfig,
)
from app.analytics.cpr_policy import evaluate_cpr_trade_policy
from app.commands.swing_analysis import JarvisSwingAnalysisCommandHandler
from app.models.interaction import SwingAnalysisCommand
from app.models.market import Candle, HistoricalCandleSeries, MarketQuote
from app.models.multi_timeframe_trade import (
    MultiTimeframeTradeDisposition,
    MultiTimeframeTradeReason,
)
from app.models.trade_setup import TradeDirection
from app.models.trade_decision import (
    MarketCondition,
    NoTradeReason,
    TradeDecision,
)
from app.models.workflow import WorkflowEventState
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
from app.workflow.events import InMemoryWorkflowEventSink, WorkflowEventEmitter
from tests.unit.test_build_multi_timeframe_evidence import (
    _cyclical_timeframes,
)
from tests.unit.test_multi_timeframe_debate import _Gateway
from tests.unit.test_run_end_to_end_swing_analysis import (
    _successful_llm_preflight,
)


class _RollingFetch:
    def __init__(self, receipt, quote=None):
        self.receipt = receipt
        self.quote = quote
        self.calls = []

    def execute(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.receipt

    def get_latest_quote(self, *args):
        return self.quote


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
    latest = resolved_timeframes.hourly.candles[-1]
    quote = MarketQuote(
        exchange=resolved_timeframes.hourly.exchange,
        symbol_token=resolved_timeframes.hourly.symbol_token,
        symbol=resolved_timeframes.hourly.symbol,
        price=latest.close,
        open=latest.open,
        high=latest.high,
        low=latest.low,
        previous_close=latest.open,
        observed_at=resolved_timeframes.hourly.retrieved_at,
        source="test_market",
    )
    return RunEndToEndMultiTimeframeSwingAnalysis(
        _RollingFetch(receipt, quote),
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
        self.assertIsNotNone(result.latest_quote)
        self.assertEqual(result.latest_quote.symbol, "RELIANCE-EQ")
        self.assertEqual(
            result.technical_review.evidence_package.daily.interval,
            "ONE_DAY",
        )
        self.assertEqual(
            result.technical_review.evidence_package.weekly.interval,
            "ONE_WEEK",
        )

    def test_emits_truthful_ui_activities_for_the_complete_pipeline(self):
        sink = InMemoryWorkflowEventSink()
        emitter = WorkflowEventEmitter("operation", sink)

        _use_case().execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            event_emitter=emitter,
        )

        activities = [event.activity.activity_id for event in sink.events]
        for activity_id in (
            "market.hourly.load",
            "market.aggregate.daily",
            "market.aggregate.weekly",
            "technical.daily.evaluate",
            "technical.weekly.evaluate",
            "evidence.release.review",
            "debate.bull.argue",
            "debate.bear.argue",
            "debate.verdict.review",
            "trade.long_only.evaluate",
        ):
            self.assertIn(activity_id, activities)
        self.assertLess(
            activities.index("evidence.release.review"),
            activities.index("debate.bull.argue"),
        )
        self.assertGreater(
            activities.index("trade.long_only.evaluate"),
            activities.index("debate.verdict.review"),
        )
        trade_events = [
            event
            for event in sink.events
            if event.activity.activity_id == "trade.long_only.evaluate"
        ]
        self.assertEqual(
            [event.state for event in trade_events],
            [WorkflowEventState.STARTED, WorkflowEventState.COMPLETED],
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
        self.assertEqual(
            outcome.schema_version,
            "jarvis.multi_timeframe_trade_plan.v2",
        )
        self.assertEqual(
            outcome.policy_id,
            "jarvis.buy_eligibility_2r_swing_policy.v2",
        )
        self.assertIs(
            outcome.trade_decision.market_condition,
            MarketCondition.BULLISH,
        )
        self.assertIs(outcome.trade_decision.decision, TradeDecision.BUY)
        self.assertIn("CPR policy", outcome.rationale)
        self.assertIn("daily acceptance=True", outcome.rationale)
        self.assertIn("daily narrow expansion=bullish", outcome.rationale)
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
        self.assertIs(
            outcome.trade_decision.market_condition,
            MarketCondition.CONFLICTED,
        )
        self.assertIs(
            outcome.trade_decision.decision,
            TradeDecision.NO_TRADE,
        )
        self.assertEqual(
            outcome.trade_decision.no_trade_reasons,
            (NoTradeReason.TIMEFRAME_CONFLICT,),
        )
        self.assertIsNone(outcome.daily_planning_result)

    def test_cpr_policy_blocker_stops_trade_before_daily_planning(self):
        timeframes = _trending_timeframes()
        review = AgentOrchestrator().run_multi_timeframe_analysis(timeframes)
        assessment = evaluate_cpr_trade_policy(
            review.evidence_package.daily.cpr,
            review.evidence_package.weekly.cpr,
        ).model_copy(
            update={
                "buy_eligible": False,
                "blocking_reasons": (
                    NoTradeReason.CPR_ACCEPTANCE_INCOMPLETE,
                ),
                "rationale": "CPR policy test blocker.",
            }
        )

        with patch(
            "app.use_cases.build_multi_timeframe_long_trade_plan."
            "evaluate_cpr_trade_policy",
            return_value=assessment,
        ):
            result = _use_case(timeframes=timeframes).execute(
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
        self.assertEqual(
            outcome.trade_decision.no_trade_reasons,
            (NoTradeReason.CPR_ACCEPTANCE_INCOMPLETE,),
        )
        self.assertIsNone(outcome.daily_planning_result)

    def test_neutral_judge_produces_explicit_no_trade_decision(self):
        result = _use_case(winner="neutral").execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
        )

        decision = result.trade_plan_result.trade_decision
        self.assertIs(decision.market_condition, MarketCondition.CONFLICTED)
        self.assertIs(decision.decision, TradeDecision.NO_TRADE)
        self.assertEqual(
            decision.no_trade_reasons,
            (NoTradeReason.TIMEFRAME_CONFLICT,),
        )

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
                interpretation=result.interpretation,
            )

        tampered_decision = result.trade_plan_result.trade_decision.model_copy(
            update={"market_condition": MarketCondition.NEUTRAL}
        )
        tampered_trade = result.trade_plan_result.model_copy(
            update={"trade_decision": tampered_decision}
        )
        with self.assertRaisesRegex(
            ValueError,
            "market condition must match the deterministic daily/weekly profiles",
        ):
            MultiTimeframeEndToEndSwingAnalysisResult(
                use_case_id=result.use_case_id,
                market_dataset_id=result.market_dataset_id,
                fetch=result.fetch,
                latest_quote=result.latest_quote,
                technical_review=result.technical_review,
                debate_result=result.debate_result,
                trade_plan_result=tampered_trade,
                interpretation=result.interpretation,
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
