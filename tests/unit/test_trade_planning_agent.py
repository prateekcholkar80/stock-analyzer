import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.agents.technical_swing_agent import market_series_fingerprint
from app.agents.trade_planning_agent import (
    SUPPORT_RESISTANCE_SIGNAL_SOURCE,
    TradePlanningAgent,
    TradePlanningAgentConfig,
)
from app.analytics.risk_reward import ATR_SIGNAL_SOURCE
from app.analytics.signal_profiles import (
    build_swing_trading_signal_profile,
)
from app.exceptions import AgentSubmissionRejectedError
from app.models.agentic import (
    AgenticSwingAnalysisResult,
    JarvisJudgeDecision,
    JarvisJudgeVerdict,
    TechnicalSwingAgentSubmission,
    TradePlanningDisposition,
    TradePlanningReason,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalStrength,
    TechnicalSignalEvidence,
    TechnicalSignalSnapshot,
)
from app.models.trade_setup import (
    StopLossMethod,
    TradeEntryMethod,
    TradeSetupStatus,
)
from app.orchestration.agent_orchestrator import (
    AgentOrchestrator,
    JarvisTradePlanJudge,
)


class TradePlanningAgentTests(unittest.TestCase):
    def setUp(self):
        self.base_time = datetime(2026, 8, 1, tzinfo=UTC)
        self.evaluated_at = self.base_time + timedelta(days=10)
        self.retrieved_at = self.base_time + timedelta(days=12)

    def market(self, *, highs=None, lows=None, closes=None, **overrides):
        highs = highs or {}
        lows = lows or {}
        closes = closes or {}
        candles = []
        for day in range(11):
            close = float(closes.get(day, 100.0))
            candles.append(
                Candle(
                    timestamp=self.base_time + timedelta(days=day),
                    open=close,
                    high=max(close, float(highs.get(day, close + 1))),
                    low=min(close, float(lows.get(day, close - 1))),
                    close=close,
                    volume=1_000 + day,
                )
            )
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "candles": candles,
            "retrieved_at": self.retrieved_at,
            "source": "angel_one",
        }
        values.update(overrides)
        return HistoricalCandleSeries(**values)

    def profile(
        self,
        series,
        direction=SignalDirection.BULLISH,
        *,
        include_atr=True,
    ):
        evidence = []
        for category in SignalCategory:
            source = f"test.{category.value}"
            values = {"value": 1.0}
            parameters = {}
            evidence_direction = direction
            evidence_id = f"{category.value}_signal"
            if category is SignalCategory.VOLATILITY and include_atr:
                source = ATR_SIGNAL_SOURCE
                values = {"atr": 2.0, "close": 100.0}
                evidence_direction = SignalDirection.NEUTRAL
                evidence_id = "atr_volatility.atr14.hlc"
            if category is SignalCategory.PRICE_ACTION:
                source = SUPPORT_RESISTANCE_SIGNAL_SOURCE
                parameters = {
                    "pivot_left_strength": 1,
                    "pivot_right_strength": 1,
                    "zone_tolerance_percentage": 0.5,
                    "minimum_touches": 2,
                }
            evidence.append(
                TechnicalSignalEvidence(
                    evidence_id=evidence_id,
                    name=f"{category.value} evidence",
                    category=category,
                    direction=evidence_direction,
                    strength=SignalStrength.STRONG,
                    source=source,
                    explanation="Deterministic planning test evidence.",
                    observed_at=self.evaluated_at,
                    available_at=self.evaluated_at,
                    observed_values=values,
                    parameters=parameters,
                )
            )
        snapshot = TechnicalSignalSnapshot(
            exchange=series.exchange,
            symbol_token=series.symbol_token,
            symbol=series.symbol,
            interval=series.interval,
            source=series.source,
            source_retrieved_at=series.retrieved_at,
            evaluated_at=self.evaluated_at,
            evidence=evidence,
        )
        return build_swing_trading_signal_profile(snapshot)

    def technical_result(
        self,
        series,
        direction=SignalDirection.BULLISH,
        *,
        include_atr=True,
        accepted=True,
    ):
        profile = self.profile(
            series,
            direction,
            include_atr=include_atr,
        )
        submission = TechnicalSwingAgentSubmission(
            submission_id="technical-submission-1",
            agent_id="jarvis.technical_swing_agent.v1",
            evaluator_id="jarvis.unified_swing.v1",
            input_fingerprint=market_series_fingerprint(series),
            configuration_fingerprint="1" * 64,
            exchange=series.exchange,
            symbol_token=series.symbol_token,
            symbol=series.symbol,
            interval=series.interval,
            source=series.source,
            source_retrieved_at=series.retrieved_at,
            evaluated_at=self.evaluated_at,
            input_candle_count=len(series.candles),
            profile=profile,
        )
        decision = JarvisJudgeDecision(
            decision_id="technical-decision-1",
            judge_id="jarvis.swing_judge.v1",
            submission_id=submission.submission_id,
            verdict=(
                JarvisJudgeVerdict.ACCEPTED
                if accepted
                else JarvisJudgeVerdict.REJECTED
            ),
            decided_at=self.evaluated_at,
            passed_checks=("test_technical_review",),
            reasons=() if accepted else ("technical evidence rejected",),
        )
        return AgenticSwingAnalysisResult(
            orchestrator_id="jarvis.agent_orchestrator.v1",
            submission=submission,
            decision=decision,
        )

    def agent_and_judge(self, config=None):
        agent = TradePlanningAgent(config)
        judge = JarvisTradePlanJudge(
            expected_agent_id=agent.agent_id,
            expected_config=agent.config,
        )
        return agent, judge

    def test_directional_profile_builds_actionable_two_and_three_r_plan(self):
        series = self.market()
        technical = self.technical_result(series)
        agent = TradePlanningAgent()

        submission = agent.execute(technical, series)

        self.assertIs(
            submission.disposition,
            TradePlanningDisposition.ACTIONABLE,
        )
        self.assertIs(
            submission.reason,
            TradePlanningReason.DIRECTIONAL_SETUP,
        )
        self.assertIsNotNone(submission.plan)
        self.assertIs(
            submission.plan.entry_method,
            TradeEntryMethod.LATEST_COMPLETED_CLOSE,
        )
        self.assertIs(
            submission.plan.stop_loss_method,
            StopLossMethod.ATR_FALLBACK,
        )
        self.assertEqual(submission.plan.evaluation.entry_price, 100)
        self.assertEqual(submission.plan.evaluation.stop_loss_price, 96)
        self.assertEqual(
            submission.plan.evaluation.minimum_target.target_price,
            108,
        )
        self.assertEqual(
            submission.plan.evaluation.preferred_target.target_price,
            112,
        )

    def test_neutral_profile_produces_explicit_no_trade(self):
        series = self.market()
        technical = self.technical_result(
            series,
            SignalDirection.NEUTRAL,
        )

        submission = TradePlanningAgent().execute(technical, series)

        self.assertIs(
            submission.disposition,
            TradePlanningDisposition.NO_TRADE,
        )
        self.assertIs(
            submission.reason,
            TradePlanningReason.NON_DIRECTIONAL_PROFILE,
        )
        self.assertIsNone(submission.plan)

    def test_missing_stop_evidence_produces_no_trade(self):
        series = self.market()
        technical = self.technical_result(
            series,
            include_atr=False,
        )

        submission = TradePlanningAgent().execute(technical, series)

        self.assertIs(
            submission.reason,
            TradePlanningReason.INSUFFICIENT_STOP_EVIDENCE,
        )
        self.assertIsNone(submission.plan)

    def test_structure_blocking_two_r_produces_no_trade(self):
        series = self.market(highs={1: 105, 3: 105})
        technical = self.technical_result(series)

        submission = TradePlanningAgent().execute(technical, series)

        self.assertIs(
            submission.reason,
            TradePlanningReason.MINIMUM_TARGET_BLOCKED,
        )
        self.assertIsNotNone(submission.plan)
        self.assertIs(
            submission.plan.evaluation.status,
            TradeSetupStatus.REJECTED,
        )

    def test_jarvis_judge_accepts_actionable_and_no_trade_conclusions(self):
        series = self.market()
        agent, judge = self.agent_and_judge()
        directional = agent.execute(self.technical_result(series), series)
        neutral = agent.execute(
            self.technical_result(series, SignalDirection.NEUTRAL),
            series,
        )

        directional_decision = judge.review(
            directional,
            self.technical_result(series),
            series,
        )
        neutral_technical = self.technical_result(
            series,
            SignalDirection.NEUTRAL,
        )
        neutral_decision = judge.review(
            neutral,
            neutral_technical,
            series,
        )

        self.assertTrue(directional_decision.accepted)
        self.assertTrue(neutral_decision.accepted)
        self.assertIn(
            "trade_plan_risk_configuration",
            directional_decision.passed_checks,
        )
        self.assertIn(
            "explicit_no_trade_outcome",
            neutral_decision.passed_checks,
        )

    def test_orchestrator_returns_only_an_approved_actionable_intent(self):
        series = self.market()
        directional = self.technical_result(series)
        neutral = self.technical_result(
            series,
            SignalDirection.NEUTRAL,
        )
        orchestrator = AgentOrchestrator()

        result = orchestrator.run_trade_planning(directional, series)
        actionable = orchestrator.require_approved_trade_intent(
            directional,
            series,
        )
        no_trade = orchestrator.require_approved_trade_intent(
            neutral,
            series,
        )

        self.assertTrue(result.decision.accepted)
        self.assertIsNotNone(result.approved_trade_intent)
        self.assertIsNotNone(actionable)
        self.assertIsNone(no_trade)

    def test_rejects_unapproved_or_different_technical_assignment(self):
        series = self.market()
        unapproved = self.technical_result(series, accepted=False)
        different_series = self.market(symbol="TCS-EQ")
        approved = self.technical_result(series)
        agent = TradePlanningAgent()

        with self.assertRaisesRegex(
            AgentSubmissionRejectedError,
            "Jarvis-approved",
        ):
            agent.execute(unapproved, series)
        with self.assertRaisesRegex(
            AgentSubmissionRejectedError,
            "market prefix",
        ):
            agent.execute(approved, different_series)

    def test_judge_rejects_tampered_planner_configuration(self):
        series = self.market()
        technical = self.technical_result(series)
        agent, judge = self.agent_and_judge()
        submission = agent.execute(technical, series).model_copy(
            update={"configuration_fingerprint": "0" * 64}
        )

        decision = judge.review(submission, technical, series)

        self.assertFalse(decision.accepted)
        self.assertIn(
            "submission used an unexpected trade-planner configuration",
            decision.reasons,
        )

    def test_config_is_immutable_and_rejects_invalid_values(self):
        config = TradePlanningAgentConfig()
        with self.assertRaises(ValidationError):
            config.minimum_reward_to_risk = 1.0

        cases = (
            {"minimum_reward_to_risk": 0.0},
            {"preferred_reward_to_risk": 2.0},
            {"minimum_buffer_percentage": 101.0},
            {"fallback_stop_atr_multiplier": True},
        )
        for values in cases:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    TradePlanningAgentConfig(**values)

    def test_submission_model_rejects_actionable_without_plan(self):
        series = self.market()
        submission = TradePlanningAgent().execute(
            self.technical_result(series),
            series,
        )
        values = submission.model_dump(exclude_computed_fields=True)
        values["plan"] = None

        with self.assertRaisesRegex(ValidationError, "require a plan"):
            type(submission).model_validate(values)


if __name__ == "__main__":
    unittest.main()
