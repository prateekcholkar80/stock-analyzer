import unittest

from pydantic import ValidationError

from app.models.trade_decision import (
    MarketCondition,
    NoTradeReason,
    TradeDecision,
    TradeDecisionOutcome,
)
from app.models.multi_timeframe_trade import (
    MultiTimeframeLongTradePlanResult,
    MultiTimeframeTradeDisposition,
    MultiTimeframeTradeReason,
)


class TradeDecisionOutcomeTests(unittest.TestCase):
    def test_accepts_buy_only_for_bullish_market_condition(self):
        outcome = TradeDecisionOutcome(
            market_condition=MarketCondition.BULLISH,
            decision=TradeDecision.BUY,
            rationale="Bullish evidence and the buy setup are valid.",
        )

        self.assertEqual(outcome.decision, TradeDecision.BUY)
        self.assertEqual(outcome.no_trade_reasons, ())

    def test_rejects_buy_for_every_non_bullish_market_condition(self):
        for condition in (
            MarketCondition.NEUTRAL,
            MarketCondition.BEARISH,
            MarketCondition.CONFLICTED,
            MarketCondition.INSUFFICIENT,
        ):
            with self.subTest(condition=condition):
                with self.assertRaisesRegex(
                    ValidationError,
                    "buy decision requires a bullish market condition",
                ):
                    TradeDecisionOutcome(
                        market_condition=condition,
                        decision=TradeDecision.BUY,
                        rationale="This decision must be rejected.",
                    )

    def test_accepts_bullish_no_trade_when_minimum_target_is_blocked(self):
        outcome = TradeDecisionOutcome(
            market_condition=MarketCondition.BULLISH,
            decision=TradeDecision.NO_TRADE,
            no_trade_reasons=(NoTradeReason.MINIMUM_TARGET_BLOCKED,),
            rationale="Resistance blocks the minimum 1:2 target.",
        )

        self.assertEqual(outcome.market_condition, MarketCondition.BULLISH)
        self.assertEqual(outcome.decision, TradeDecision.NO_TRADE)

    def test_accepts_required_reason_for_each_non_bullish_condition(self):
        cases = (
            (
                MarketCondition.BEARISH,
                NoTradeReason.BEARISH_CONDITION,
            ),
            (
                MarketCondition.NEUTRAL,
                NoTradeReason.NEUTRAL_CONDITION,
            ),
            (
                MarketCondition.CONFLICTED,
                NoTradeReason.TIMEFRAME_CONFLICT,
            ),
            (
                MarketCondition.INSUFFICIENT,
                NoTradeReason.INSUFFICIENT_DATA,
            ),
        )
        for condition, reason in cases:
            with self.subTest(condition=condition):
                outcome = TradeDecisionOutcome(
                    market_condition=condition,
                    decision=TradeDecision.NO_TRADE,
                    no_trade_reasons=(reason,),
                    rationale=f"No trade because conditions are {condition.value}.",
                )
                self.assertEqual(outcome.no_trade_reasons, (reason,))

    def test_rejects_no_trade_without_a_reason(self):
        with self.assertRaisesRegex(
            ValidationError,
            "no-trade decision requires at least one reason",
        ):
            TradeDecisionOutcome(
                market_condition=MarketCondition.BULLISH,
                decision=TradeDecision.NO_TRADE,
                rationale="A reason is required.",
            )

    def test_rejects_no_trade_when_condition_reason_is_missing(self):
        with self.assertRaisesRegex(
            ValidationError,
            "bearish market condition requires the bearish_condition",
        ):
            TradeDecisionOutcome(
                market_condition=MarketCondition.BEARISH,
                decision=TradeDecision.NO_TRADE,
                no_trade_reasons=(
                    NoTradeReason.INSUFFICIENT_REWARD_TO_RISK,
                ),
                rationale="The condition and reason are inconsistent.",
            )

    def test_rejects_reason_for_a_different_market_condition(self):
        with self.assertRaisesRegex(
            ValidationError,
            "market-condition no-trade reason does not match",
        ):
            TradeDecisionOutcome(
                market_condition=MarketCondition.NEUTRAL,
                decision=TradeDecision.NO_TRADE,
                no_trade_reasons=(
                    NoTradeReason.NEUTRAL_CONDITION,
                    NoTradeReason.BEARISH_CONDITION,
                ),
                rationale="Conflicting condition reasons are invalid.",
            )

    def test_rejects_duplicate_no_trade_reasons(self):
        with self.assertRaisesRegex(
            ValidationError,
            "no-trade reasons must be unique",
        ):
            TradeDecisionOutcome(
                market_condition=MarketCondition.BULLISH,
                decision=TradeDecision.NO_TRADE,
                no_trade_reasons=(
                    NoTradeReason.MINIMUM_TARGET_BLOCKED,
                    NoTradeReason.MINIMUM_TARGET_BLOCKED,
                ),
                rationale="Duplicate reasons are invalid.",
            )

    def test_rejects_no_trade_reasons_on_buy_decision(self):
        with self.assertRaisesRegex(
            ValidationError,
            "buy decision cannot include no-trade reasons",
        ):
            TradeDecisionOutcome(
                market_condition=MarketCondition.BULLISH,
                decision=TradeDecision.BUY,
                no_trade_reasons=(NoTradeReason.MINIMUM_TARGET_BLOCKED,),
                rationale="Buy cannot also be blocked.",
            )

    def test_legacy_trade_plan_without_new_contract_remains_valid(self):
        plan = MultiTimeframeLongTradePlanResult(
            technical_package_fingerprint="a" * 64,
            technical_decision_id="technical-decision",
            debate_verdict_id="debate-verdict",
            disposition=MultiTimeframeTradeDisposition.NO_TRADE,
            reason=MultiTimeframeTradeReason.JUDGE_NOT_BULLISH,
            rationale="Legacy stored no-trade result.",
        )

        self.assertIsNone(plan.trade_decision)

    def test_no_trade_plan_rejects_buy_decision_contract(self):
        with self.assertRaisesRegex(
            ValidationError,
            "no-trade plan requires a NO_TRADE decision",
        ):
            MultiTimeframeLongTradePlanResult(
                technical_package_fingerprint="a" * 64,
                technical_decision_id="technical-decision",
                debate_verdict_id="debate-verdict",
                disposition=MultiTimeframeTradeDisposition.NO_TRADE,
                reason=MultiTimeframeTradeReason.JUDGE_NOT_BULLISH,
                trade_decision=TradeDecisionOutcome(
                    market_condition=MarketCondition.BULLISH,
                    decision=TradeDecision.BUY,
                    rationale="Invalid buy decision.",
                ),
                rationale="Invalid mixed outcome.",
            )


if __name__ == "__main__":
    unittest.main()
