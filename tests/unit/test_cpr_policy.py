import unittest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.analytics.cpr_policy import (
    CPRDirectionalBias,
    CPRTimeframeRelationship,
    evaluate_cpr_trade_policy,
)
from app.models.analysis_timeframe import SwingAnalysisTimeframe
from app.models.cpr import (
    CPRAnalysisRecord,
    CPRBasis,
    CPRLifecycleState,
    CPRPricePosition,
    CPRWidthRegime,
)
from app.models.trade_decision import NoTradeReason


IST = ZoneInfo("Asia/Kolkata")


def record(
    timeframe,
    lifecycle,
    *,
    position,
    acceptance,
    width=CPRWidthRegime.NORMAL,
):
    source_started = datetime(2026, 7, 1, 15, 30, tzinfo=IST)
    source_ended = source_started + timedelta(days=20)
    valid_from = source_ended + timedelta(days=1)
    evaluated_at = valid_from + timedelta(days=10)
    interval = (
        "ONE_DAY"
        if timeframe is SwingAnalysisTimeframe.DAILY
        else "ONE_WEEK"
    )
    basis = (
        CPRBasis.WEEKLY
        if timeframe is SwingAnalysisTimeframe.DAILY
        else CPRBasis.MONTHLY
    )
    current_price = {
        CPRPricePosition.ABOVE: 105.0,
        CPRPricePosition.INSIDE: 100.0,
        CPRPricePosition.BELOW: 95.0,
    }[position]
    return CPRAnalysisRecord(
        analysis_id=f"{timeframe.value}:cpr:test-period",
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        timeframe=timeframe,
        interval=interval,
        basis=basis,
        source="test_market",
        source_period_started_at=source_started,
        source_period_ended_at=source_ended,
        available_at=source_ended,
        valid_from=valid_from,
        valid_to=evaluated_at,
        evaluated_at=evaluated_at,
        source_retrieved_at=evaluated_at + timedelta(minutes=5),
        source_high=110.0,
        source_low=90.0,
        source_close=100.0,
        pivot=100.0,
        bottom_central=100.0,
        top_central=100.0,
        width_percentage=0.0,
        width_percentile=20.0,
        width_sample_count=20,
        width_regime=width,
        current_price=current_price,
        price_position=position,
        consecutive_acceptance_candles=acceptance,
        lifecycle_state=lifecycle,
        evidence_ids=(
            f"{timeframe.value}:cpr.levels",
            f"{timeframe.value}:cpr.lifecycle.{lifecycle.value}",
        ),
        calculation_fingerprint="a" * 64,
    )


class CPRTradePolicyTests(unittest.TestCase):
    def accepted(self, timeframe, *, width=CPRWidthRegime.NORMAL):
        return record(
            timeframe,
            CPRLifecycleState.ACCEPTED_ABOVE,
            position=CPRPricePosition.ABOVE,
            acceptance=2,
            width=width,
        )

    def test_aligned_narrow_bullish_acceptance_confirms_without_creating_buy(self):
        assessment = evaluate_cpr_trade_policy(
            self.accepted(
                SwingAnalysisTimeframe.DAILY,
                width=CPRWidthRegime.NARROW,
            ),
            self.accepted(
                SwingAnalysisTimeframe.WEEKLY,
                width=CPRWidthRegime.NARROW,
            ),
        )

        self.assertTrue(assessment.buy_eligible)
        self.assertTrue(assessment.daily_acceptance_confirmed)
        self.assertIs(
            assessment.relationship,
            CPRTimeframeRelationship.ALIGNED_BULLISH,
        )
        self.assertIs(
            assessment.daily_narrow_expansion,
            CPRDirectionalBias.BULLISH,
        )
        self.assertIn("never creates a BUY", assessment.rationale)

    def test_first_daily_breakout_remains_unconfirmed(self):
        daily = record(
            SwingAnalysisTimeframe.DAILY,
            CPRLifecycleState.BULLISH_BREAKOUT,
            position=CPRPricePosition.ABOVE,
            acceptance=1,
        )
        assessment = evaluate_cpr_trade_policy(
            daily,
            self.accepted(SwingAnalysisTimeframe.WEEKLY),
        )

        self.assertFalse(assessment.buy_eligible)
        self.assertEqual(
            assessment.blocking_reasons,
            (NoTradeReason.CPR_ACCEPTANCE_INCOMPLETE,),
        )

    def test_failed_bullish_break_is_bearish_conflict_and_explicit_risk(self):
        daily = record(
            SwingAnalysisTimeframe.DAILY,
            CPRLifecycleState.FAILED_BULLISH_BREAKOUT,
            position=CPRPricePosition.INSIDE,
            acceptance=0,
        )
        assessment = evaluate_cpr_trade_policy(
            daily,
            self.accepted(SwingAnalysisTimeframe.WEEKLY),
        )

        self.assertTrue(assessment.failed_break_risk)
        self.assertEqual(
            assessment.relationship,
            CPRTimeframeRelationship.CONFLICTED,
        )
        self.assertIn(
            NoTradeReason.CPR_FAILED_BREAK_RISK,
            assessment.blocking_reasons,
        )
        self.assertIn(
            NoTradeReason.CPR_TIMEFRAME_CONFLICT,
            assessment.blocking_reasons,
        )

    def test_weekly_bearish_acceptance_blocks_daily_bullish_setup(self):
        weekly = record(
            SwingAnalysisTimeframe.WEEKLY,
            CPRLifecycleState.ACCEPTED_BELOW,
            position=CPRPricePosition.BELOW,
            acceptance=2,
            width=CPRWidthRegime.NARROW,
        )
        assessment = evaluate_cpr_trade_policy(
            self.accepted(SwingAnalysisTimeframe.DAILY),
            weekly,
        )

        self.assertFalse(assessment.buy_eligible)
        self.assertIs(
            assessment.weekly_narrow_expansion,
            CPRDirectionalBias.BEARISH,
        )
        self.assertIn(
            NoTradeReason.CPR_BEARISH_RISK,
            assessment.blocking_reasons,
        )

    def test_missing_cpr_is_insufficient_and_daily_acceptance_is_incomplete(self):
        assessment = evaluate_cpr_trade_policy(None, None)

        self.assertFalse(assessment.buy_eligible)
        self.assertIs(
            assessment.relationship,
            CPRTimeframeRelationship.INSUFFICIENT,
        )
        self.assertIn(
            NoTradeReason.INSUFFICIENT_DATA,
            assessment.blocking_reasons,
        )


if __name__ == "__main__":
    unittest.main()
