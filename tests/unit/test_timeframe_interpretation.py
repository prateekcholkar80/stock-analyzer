import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.models.multi_timeframe_evidence import (
    SwingAnalysisTimeframe,
    timeframe_interval,
)
from app.models.technical_setup import (
    BEARISH_SETUP_SEQUENCE,
    BULLISH_SETUP_SEQUENCE,
    SwingSetupSide,
    SwingSetupStep,
    SwingSetupStepState,
    TimeframeSwingSetup,
)
from app.models.timeframe_interpretation import (
    MultiTimeframeSwingInterpretation,
    RewardRiskFeasibility,
    RewardRiskTargetInterpretation,
    StructuralRisk,
    SwingRiskRewardInterpretation,
    TacticalReadiness,
    TimeframeAlignment,
    TimeframeMarketInterpretation,
    derive_combined_market_condition,
    derive_timeframe_alignment,
)
from app.models.trade_decision import (
    MarketCondition,
    NoTradeReason,
    TradeDecision,
    TradeDecisionOutcome,
)


IST = ZoneInfo("Asia/Kolkata")


class TimeframeInterpretationContractTests(unittest.TestCase):
    def setUp(self):
        self.evaluated_at = datetime(2026, 8, 24, 15, 30, tzinfo=IST)
        self.interpreted_at = self.evaluated_at + timedelta(seconds=1)

    def build_setup(self, side, timeframe):
        sequence = (
            BULLISH_SETUP_SEQUENCE
            if side is SwingSetupSide.BULLISH
            else BEARISH_SETUP_SEQUENCE
        )
        steps = tuple(
            SwingSetupStep(
                step_id=step_id,
                side=side,
                timeframe=timeframe,
                interval=timeframe_interval(timeframe),
                sequence=index,
                state=SwingSetupStepState.PENDING,
                evaluated_at=self.evaluated_at,
                explanation="Awaiting deterministic confirmation.",
            )
            for index, step_id in enumerate(sequence, start=1)
        )
        return TimeframeSwingSetup(
            setup_id=f"{timeframe.value}:{side.value}_setup",
            side=side,
            timeframe=timeframe,
            interval=timeframe_interval(timeframe),
            evaluated_at=self.evaluated_at,
            steps=steps,
        )

    def build_timeframe(self, timeframe, condition, **overrides):
        evidence_ids = (
            ()
            if condition is MarketCondition.INSUFFICIENT
            else (f"{timeframe.value}:condition.evidence",)
        )
        values = {
            "timeframe": timeframe,
            "interval": timeframe_interval(timeframe),
            "evaluated_at": self.evaluated_at,
            "market_condition": condition,
            "bullish_setup": self.build_setup(
                SwingSetupSide.BULLISH,
                timeframe,
            ),
            "bearish_setup": self.build_setup(
                SwingSetupSide.BEARISH,
                timeframe,
            ),
            "decisive_evidence_ids": evidence_ids,
            "rationale": f"The {timeframe.value} condition is evidence based.",
        }
        values.update(overrides)
        return TimeframeMarketInterpretation(**values)

    @staticmethod
    def target(multiple, feasibility, **overrides):
        values = {
            "reward_to_risk": multiple,
            "feasibility": feasibility,
            "rationale": "Target feasibility follows confirmed structure.",
        }
        values.update(overrides)
        return RewardRiskTargetInterpretation(**values)

    def unevaluated_risk_reward(self):
        return SwingRiskRewardInterpretation(
            target_2r=self.target(2.0, RewardRiskFeasibility.NOT_EVALUATED),
            target_3r=self.target(3.0, RewardRiskFeasibility.NOT_EVALUATED),
        )

    def feasible_risk_reward(self):
        return SwingRiskRewardInterpretation(
            reference_entry=100.0,
            stop_loss=90.0,
            risk_per_unit=10.0,
            target_2r=self.target(
                2.0,
                RewardRiskFeasibility.FEASIBLE,
                target_price=120.0,
            ),
            target_3r=self.target(
                3.0,
                RewardRiskFeasibility.FEASIBLE,
                target_price=130.0,
            ),
        )

    def build_combined(
        self,
        daily_condition=MarketCondition.BULLISH,
        weekly_condition=MarketCondition.BULLISH,
        **overrides,
    ):
        daily = self.build_timeframe(
            SwingAnalysisTimeframe.DAILY,
            daily_condition,
        )
        weekly = self.build_timeframe(
            SwingAnalysisTimeframe.WEEKLY,
            weekly_condition,
        )
        combined = derive_combined_market_condition(
            daily_condition,
            weekly_condition,
        )
        if combined is MarketCondition.BULLISH:
            decision = TradeDecisionOutcome(
                market_condition=combined,
                decision=TradeDecision.BUY,
                rationale="Both timeframes and minimum 2R support a buy.",
            )
            risk_reward = self.feasible_risk_reward()
            readiness = TacticalReadiness.READY
            risk = StructuralRisk.MODERATE
        else:
            reason = {
                MarketCondition.BEARISH: NoTradeReason.BEARISH_CONDITION,
                MarketCondition.NEUTRAL: NoTradeReason.NEUTRAL_CONDITION,
                MarketCondition.CONFLICTED: NoTradeReason.TIMEFRAME_CONFLICT,
                MarketCondition.INSUFFICIENT: NoTradeReason.INSUFFICIENT_DATA,
            }[combined]
            decision = TradeDecisionOutcome(
                market_condition=combined,
                decision=TradeDecision.NO_TRADE,
                no_trade_reasons=(reason,),
                rationale="The timeframe condition prevents a buy.",
            )
            risk_reward = self.unevaluated_risk_reward()
            readiness = TacticalReadiness.NOT_APPLICABLE
            risk = StructuralRisk.UNKNOWN
        decisive = tuple(
            evidence_id
            for item in (daily, weekly)
            for evidence_id in item.decisive_evidence_ids
        )
        values = {
            "daily": daily,
            "weekly": weekly,
            "alignment": derive_timeframe_alignment(
                daily_condition,
                weekly_condition,
            ),
            "tactical_readiness": readiness,
            "structural_risk": risk,
            "risk_reward": risk_reward,
            "trade_decision": decision,
            "decisive_evidence_ids": decisive,
            "decision_change_conditions": (
                "Re-evaluate when daily or weekly structure changes.",
            ),
            "rationale": "The interpretation preserves both timeframe views.",
            "interpreted_at": self.interpreted_at,
        }
        values.update(overrides)
        return MultiTimeframeSwingInterpretation(**values)

    def test_derives_every_alignment_without_agent_judgment(self):
        for daily in MarketCondition:
            for weekly in MarketCondition:
                with self.subTest(daily=daily, weekly=weekly):
                    alignment = derive_timeframe_alignment(daily, weekly)
                    if MarketCondition.INSUFFICIENT in {daily, weekly}:
                        expected = TimeframeAlignment.INSUFFICIENT
                    elif MarketCondition.CONFLICTED in {daily, weekly}:
                        expected = TimeframeAlignment.CONFLICTED
                    elif daily is weekly is MarketCondition.BULLISH:
                        expected = TimeframeAlignment.ALIGNED_BULLISH
                    elif daily is weekly is MarketCondition.BEARISH:
                        expected = TimeframeAlignment.ALIGNED_BEARISH
                    elif daily is weekly is MarketCondition.NEUTRAL:
                        expected = TimeframeAlignment.ALIGNED_NEUTRAL
                    else:
                        expected = TimeframeAlignment.MIXED
                    self.assertIs(alignment, expected)

    def test_builds_valid_aligned_buy_interpretation(self):
        interpretation = self.build_combined()

        self.assertEqual(
            interpretation.alignment,
            TimeframeAlignment.ALIGNED_BULLISH,
        )
        self.assertEqual(
            interpretation.trade_decision.decision,
            TradeDecision.BUY,
        )
        self.assertEqual(
            interpretation.risk_reward.target_2r.target_price,
            120.0,
        )

    def test_builds_valid_mixed_no_trade_interpretation(self):
        interpretation = self.build_combined(
            MarketCondition.BULLISH,
            MarketCondition.BEARISH,
        )

        self.assertEqual(interpretation.alignment, TimeframeAlignment.MIXED)
        self.assertEqual(
            interpretation.trade_decision.market_condition,
            MarketCondition.CONFLICTED,
        )
        self.assertEqual(
            interpretation.trade_decision.decision,
            TradeDecision.NO_TRADE,
        )

    def test_builds_bullish_no_trade_when_two_r_is_blocked(self):
        blocking_id = "daily:zone.resistance"
        blocked = SwingRiskRewardInterpretation(
            reference_entry=100.0,
            stop_loss=90.0,
            risk_per_unit=10.0,
            target_2r=self.target(
                2.0,
                RewardRiskFeasibility.BLOCKED_BY_STRUCTURE,
                target_price=120.0,
                blocking_evidence_ids=(blocking_id,),
            ),
            target_3r=self.target(
                3.0,
                RewardRiskFeasibility.BLOCKED_BY_STRUCTURE,
                target_price=130.0,
                blocking_evidence_ids=(blocking_id,),
            ),
        )
        decision = TradeDecisionOutcome(
            market_condition=MarketCondition.BULLISH,
            decision=TradeDecision.NO_TRADE,
            no_trade_reasons=(NoTradeReason.MINIMUM_TARGET_BLOCKED,),
            rationale="Resistance blocks the minimum 2R target.",
        )

        interpretation = self.build_combined(
            risk_reward=blocked,
            trade_decision=decision,
        )

        self.assertEqual(
            interpretation.risk_reward.target_2r.feasibility,
            RewardRiskFeasibility.BLOCKED_BY_STRUCTURE,
        )

    def test_bullish_no_trade_accepts_cpr_acceptance_blocker(self):
        decision = TradeDecisionOutcome(
            market_condition=MarketCondition.BULLISH,
            decision=TradeDecision.NO_TRADE,
            no_trade_reasons=(
                NoTradeReason.CPR_ACCEPTANCE_INCOMPLETE,
            ),
            rationale="Daily CPR acceptance is incomplete.",
        )

        interpretation = self.build_combined(
            tactical_readiness=TacticalReadiness.DEVELOPING,
            trade_decision=decision,
        )

        self.assertIs(
            interpretation.trade_decision.decision,
            TradeDecision.NO_TRADE,
        )

    def test_timeframe_contract_rejects_assignment_and_evidence_errors(self):
        daily = self.build_timeframe(
            SwingAnalysisTimeframe.DAILY,
            MarketCondition.BULLISH,
        )
        cases = (
            {"interval": "ONE_WEEK"},
            {
                "bearish_setup": self.build_setup(
                    SwingSetupSide.BEARISH,
                    SwingAnalysisTimeframe.WEEKLY,
                )
            },
            {"decisive_evidence_ids": ("weekly:wrong.timeframe",)},
            {"decisive_evidence_ids": ()},
        )
        payload = daily.model_dump(exclude={"schema_version"})
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    TimeframeMarketInterpretation(**(payload | overrides))

    def test_target_contract_enforces_feasibility_evidence(self):
        invalid = (
            {
                "reward_to_risk": 2.0,
                "feasibility": RewardRiskFeasibility.FEASIBLE,
            },
            {
                "reward_to_risk": 2.0,
                "feasibility": RewardRiskFeasibility.FEASIBLE,
                "target_price": 120.0,
                "blocking_evidence_ids": ("daily:zone.block",),
            },
            {
                "reward_to_risk": 2.0,
                "feasibility": RewardRiskFeasibility.BLOCKED_BY_STRUCTURE,
                "target_price": 120.0,
            },
            {
                "reward_to_risk": 2.0,
                "feasibility": RewardRiskFeasibility.NOT_EVALUATED,
                "target_price": 120.0,
            },
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    RewardRiskTargetInterpretation(
                        rationale="Invalid target combination.",
                        **values,
                    )

    def test_risk_reward_contract_rejects_bad_pricing_and_target_order(self):
        invalid = (
            {
                "reference_entry": 100.0,
                "target_2r": self.target(
                    2.0,
                    RewardRiskFeasibility.NOT_EVALUATED,
                ),
                "target_3r": self.target(
                    3.0,
                    RewardRiskFeasibility.NOT_EVALUATED,
                ),
            },
            {
                "reference_entry": 100.0,
                "stop_loss": 101.0,
                "risk_per_unit": 1.0,
                "target_2r": self.target(
                    2.0,
                    RewardRiskFeasibility.FEASIBLE,
                    target_price=102.0,
                ),
                "target_3r": self.target(
                    3.0,
                    RewardRiskFeasibility.FEASIBLE,
                    target_price=103.0,
                ),
            },
            {
                "reference_entry": 100.0,
                "stop_loss": 90.0,
                "risk_per_unit": 9.0,
                "target_2r": self.target(
                    2.0,
                    RewardRiskFeasibility.FEASIBLE,
                    target_price=118.0,
                ),
                "target_3r": self.target(
                    3.0,
                    RewardRiskFeasibility.FEASIBLE,
                    target_price=127.0,
                ),
            },
            {
                "reference_entry": 100.0,
                "stop_loss": 90.0,
                "risk_per_unit": 10.0,
                "target_2r": self.target(
                    2.0,
                    RewardRiskFeasibility.BLOCKED_BY_STRUCTURE,
                    target_price=120.0,
                    blocking_evidence_ids=("daily:zone.block",),
                ),
                "target_3r": self.target(
                    3.0,
                    RewardRiskFeasibility.FEASIBLE,
                    target_price=130.0,
                ),
            },
        )
        for values in invalid:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    SwingRiskRewardInterpretation(**values)

    def test_buy_requires_alignment_readiness_safe_risk_and_two_r(self):
        cases = (
            {"alignment": TimeframeAlignment.MIXED},
            {"tactical_readiness": TacticalReadiness.DEVELOPING},
            {"structural_risk": StructuralRisk.HIGH},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.build_combined(**overrides)

    def test_bullish_no_trade_requires_reason_for_each_active_blocker(self):
        no_trade = TradeDecisionOutcome(
            market_condition=MarketCondition.BULLISH,
            decision=TradeDecision.NO_TRADE,
            no_trade_reasons=(NoTradeReason.DAILY_TRIGGER_INCOMPLETE,),
            rationale="The daily trigger is incomplete.",
        )
        with self.assertRaisesRegex(
            ValidationError,
            "structural reason",
        ):
            self.build_combined(
                tactical_readiness=TacticalReadiness.DEVELOPING,
                structural_risk=StructuralRisk.HIGH,
                trade_decision=no_trade,
            )

    def test_rejects_no_trade_when_aligned_bullish_has_no_blocker(self):
        no_trade = TradeDecisionOutcome(
            market_condition=MarketCondition.BULLISH,
            decision=TradeDecision.NO_TRADE,
            no_trade_reasons=(NoTradeReason.BUY_SETUP_INVALIDATED,),
            rationale="An unsupported no-trade must be rejected.",
        )

        with self.assertRaisesRegex(
            ValidationError,
            "cannot conclude NO_TRADE",
        ):
            self.build_combined(trade_decision=no_trade)

    def test_rejects_wrong_alignment_condition_evidence_and_time(self):
        conflicted_decision = TradeDecisionOutcome(
            market_condition=MarketCondition.CONFLICTED,
            decision=TradeDecision.NO_TRADE,
            no_trade_reasons=(NoTradeReason.TIMEFRAME_CONFLICT,),
            rationale="Conflicted conditions prevent a buy.",
        )
        cases = (
            {"alignment": TimeframeAlignment.MIXED},
            {"trade_decision": conflicted_decision},
            {"decisive_evidence_ids": ("daily:not.in.timeframes",)},
            {"interpreted_at": self.evaluated_at - timedelta(seconds=1)},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.build_combined(**overrides)

    def test_non_bullish_interpretation_cannot_expose_targets(self):
        with self.assertRaisesRegex(
            ValidationError,
            "cannot expose trade targets",
        ):
            self.build_combined(
                MarketCondition.BEARISH,
                MarketCondition.BEARISH,
                risk_reward=self.feasible_risk_reward(),
            )

    def test_rejects_blank_duplicate_conditions_and_is_immutable(self):
        for conditions in (("",), ("Wait for BOS.", "Wait for BOS.")):
            with self.subTest(conditions=conditions):
                with self.assertRaises(ValidationError):
                    self.build_combined(
                        decision_change_conditions=conditions,
                    )

        interpretation = self.build_combined()
        with self.assertRaises(ValidationError):
            interpretation.structural_risk = StructuralRisk.HIGH


if __name__ == "__main__":
    unittest.main()
