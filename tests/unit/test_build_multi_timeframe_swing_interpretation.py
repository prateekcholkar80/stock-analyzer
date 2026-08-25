import unittest

from app.models.technical_setup import (
    SwingSetupStepId,
    SwingSetupStepState,
)
from app.models.timeframe_interpretation import (
    RewardRiskFeasibility,
    StructuralRisk,
    TacticalReadiness,
    TimeframeAlignment,
)
from app.models.trade_decision import (
    MarketCondition,
    NoTradeReason,
    TradeDecision,
)
from app.use_cases.build_multi_timeframe_swing_interpretation import (
    BuildMultiTimeframeSwingInterpretation,
)
from tests.unit.test_run_end_to_end_multi_timeframe_swing_analysis import (
    _trending_timeframes,
    _use_case,
)


class BuildMultiTimeframeSwingInterpretationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.baseline = _use_case().execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
        )
        cls.actionable = _use_case(
            timeframes=_trending_timeframes()
        ).execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
        )

    def test_baseline_exposes_both_complete_setup_matrices(self):
        interpretation = self.baseline.interpretation

        self.assertEqual(len(interpretation.daily.bullish_setup.steps), 9)
        self.assertEqual(len(interpretation.daily.bearish_setup.steps), 8)
        self.assertEqual(len(interpretation.weekly.bullish_setup.steps), 9)
        self.assertEqual(len(interpretation.weekly.bearish_setup.steps), 8)
        self.assertIs(
            self._step(
                interpretation.daily.bullish_setup,
                SwingSetupStepId.BULLISH_PRIOR_DOWNTREND,
            ).state,
            SwingSetupStepState.CONFIRMED,
        )
        self.assertIs(
            self._step(
                interpretation.daily.bullish_setup,
                SwingSetupStepId.BULLISH_CHANGE_OF_CHARACTER,
            ).state,
            SwingSetupStepState.CONTRADICTED,
        )
        self.assertIs(
            self._step(
                interpretation.daily.bearish_setup,
                SwingSetupStepId.BEARISH_CHANGE_OF_CHARACTER,
            ).state,
            SwingSetupStepState.CONFIRMED,
        )

    def test_liquidity_sweep_is_calculated_without_inventing_a_signal(self):
        bearish = self.baseline.interpretation.daily.bearish_setup

        sweep = self._step(
            bearish,
            SwingSetupStepId.BEARISH_LIQUIDITY_SWEEP,
        )
        self.assertIs(sweep.state, SwingSetupStepState.PENDING)
        self.assertEqual(sweep.evidence_ids, ())
        self.assertIn("found no", sweep.explanation)

        divergence = self._step(
            bearish,
            SwingSetupStepId.BEARISH_RSI_DIVERGENCE,
        )
        self.assertIs(
            divergence.state,
            SwingSetupStepState.UNAVAILABLE,
        )
        self.assertEqual(divergence.evidence_ids, ())
        self.assertEqual(divergence.observed_values, {})

    def test_every_evidenced_step_is_timeframe_qualified_and_lookahead_safe(self):
        interpretation = self.baseline.interpretation

        for timeframe in (interpretation.daily, interpretation.weekly):
            prefix = f"{timeframe.timeframe.value}:"
            for setup in (timeframe.bullish_setup, timeframe.bearish_setup):
                for step in setup.steps:
                    self.assertTrue(
                        all(item.startswith(prefix) for item in step.evidence_ids)
                    )
                    if step.available_at is not None:
                        self.assertLessEqual(step.available_at, step.evaluated_at)
                    if step.confirmed_at is not None:
                        self.assertLessEqual(step.confirmed_at, step.evaluated_at)

    def test_conflicting_profiles_produce_no_trade_without_targets(self):
        interpretation = self.baseline.interpretation

        self.assertIs(interpretation.daily.market_condition, MarketCondition.CONFLICTED)
        self.assertIs(interpretation.weekly.market_condition, MarketCondition.CONFLICTED)
        self.assertIs(interpretation.alignment, TimeframeAlignment.CONFLICTED)
        self.assertIs(interpretation.trade_decision.decision, TradeDecision.NO_TRADE)
        self.assertEqual(
            interpretation.trade_decision.no_trade_reasons,
            (NoTradeReason.TIMEFRAME_CONFLICT,),
        )
        self.assertIs(
            interpretation.risk_reward.target_2r.feasibility,
            RewardRiskFeasibility.NOT_EVALUATED,
        )
        self.assertIsNone(interpretation.risk_reward.reference_entry)

    def test_actionable_result_preserves_exact_planner_prices(self):
        interpretation = self.actionable.interpretation
        evaluation = (
            self.actionable.trade_plan_result.daily_planning_result
            .approved_trade_intent.evaluation
        )

        self.assertIs(interpretation.alignment, TimeframeAlignment.ALIGNED_BULLISH)
        self.assertIs(interpretation.tactical_readiness, TacticalReadiness.READY)
        self.assertIs(interpretation.structural_risk, StructuralRisk.LOW)
        self.assertIs(interpretation.trade_decision.decision, TradeDecision.BUY)
        self.assertEqual(
            interpretation.risk_reward.reference_entry,
            evaluation.entry_price,
        )
        self.assertEqual(
            interpretation.risk_reward.stop_loss,
            evaluation.stop_loss_price,
        )
        self.assertEqual(
            interpretation.risk_reward.target_2r.target_price,
            evaluation.minimum_target.target_price,
        )
        self.assertEqual(
            interpretation.risk_reward.target_3r.target_price,
            evaluation.preferred_target.target_price,
        )
        self.assertIs(
            interpretation.risk_reward.target_2r.feasibility,
            RewardRiskFeasibility.FEASIBLE,
        )

    def test_judge_cannot_rewrite_bullish_timeframe_character(self):
        result = _use_case(
            timeframes=_trending_timeframes(),
            winner="bearish",
        ).execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
        )
        interpretation = result.interpretation

        self.assertIs(interpretation.daily.market_condition, MarketCondition.BULLISH)
        self.assertIs(interpretation.weekly.market_condition, MarketCondition.BULLISH)
        self.assertIs(interpretation.trade_decision.decision, TradeDecision.NO_TRADE)
        self.assertEqual(
            interpretation.trade_decision.no_trade_reasons,
            (NoTradeReason.JUDGE_REJECTED_BULLISH_CASE,),
        )
        self.assertIs(interpretation.tactical_readiness, TacticalReadiness.BLOCKED)

    def test_rejects_trade_plan_from_another_review_chain(self):
        tampered = self.baseline.trade_plan_result.model_copy(
            update={"technical_package_fingerprint": "0" * 64}
        )

        with self.assertRaisesRegex(ValueError, "same review chain"):
            BuildMultiTimeframeSwingInterpretation().execute(
                self.baseline.technical_review,
                tampered,
            )

    @staticmethod
    def _step(setup, step_id):
        return next(step for step in setup.steps if step.step_id is step_id)


if __name__ == "__main__":
    unittest.main()
