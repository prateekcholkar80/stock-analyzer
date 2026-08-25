import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.models.accumulation import (
    AccumulationEvidenceMetrics,
    AccumulationLifecycleEvent,
    AccumulationLifecycleState,
    AccumulationZone,
    LiquidityPoolSide,
    LiquiditySweep,
    LiquiditySweepBias,
    TimeframeAccumulationAnalysis,
)
from app.models.multi_timeframe_evidence import (
    SwingAnalysisTimeframe,
    timeframe_interval,
)


IST = ZoneInfo("Asia/Kolkata")


class AccumulationContractTests(unittest.TestCase):
    def setUp(self):
        self.evaluated_at = datetime(2026, 8, 24, 15, 30, tzinfo=IST)
        self.retrieved_at = self.evaluated_at + timedelta(minutes=5)
        self.base_started_at = self.evaluated_at - timedelta(days=45)
        self.base_last_observed_at = self.evaluated_at - timedelta(days=8)

    @staticmethod
    def metrics(**overrides):
        values = {
            "candle_count": 25,
            "range_width_percentage": 3.2,
            "normalized_price_slope_percentage": 0.03,
            "atr_compression_percentage": 28.0,
            "close_containment_percentage": 84.0,
            "lower_boundary_touch_count": 3,
            "upper_boundary_touch_count": 2,
            "lower_rejection_count": 2,
            "nonzero_volume_percentage": 100.0,
            "bullish_volume_share_percentage": 58.0,
            "down_volume_contraction_percentage": 18.0,
            "obv_slope": 1250.0,
            "confidence_score": 72.0,
        }
        values.update(overrides)
        return AccumulationEvidenceMetrics(**values)

    def event(
        self,
        state=AccumulationLifecycleState.FORMING,
        *,
        day_offset=-35,
        timeframe=SwingAnalysisTimeframe.DAILY,
        evidence_id=None,
        **overrides,
    ):
        observed_at = self.evaluated_at + timedelta(days=day_offset)
        values = {
            "state": state,
            "observed_at": observed_at,
            "available_at": observed_at + timedelta(hours=1),
            "evidence_ids": (
                evidence_id
                or f"{timeframe.value}:accumulation.{state.value}",
            ),
            "explanation": f"Accumulation state became {state.value}.",
        }
        values.update(overrides)
        return AccumulationLifecycleEvent(**values)

    def lifecycle(
        self,
        *states,
        timeframe=SwingAnalysisTimeframe.DAILY,
    ):
        resolved = states or (AccumulationLifecycleState.FORMING,)
        return tuple(
            self.event(
                state,
                day_offset=-35 + index * 6,
                timeframe=timeframe,
            )
            for index, state in enumerate(resolved)
        )

    def sweep(
        self,
        *,
        liquidity_side=LiquidityPoolSide.SELL_SIDE,
        timeframe=SwingAnalysisTimeframe.DAILY,
        sweep_suffix="sweep-1",
        **overrides,
    ):
        swept_at = self.evaluated_at - timedelta(days=12)
        if liquidity_side is LiquidityPoolSide.SELL_SIDE:
            reference_price = 2240.0
            extreme_price = 2225.0
            reclaim_close_price = 2250.0
        else:
            reference_price = 2315.0
            extreme_price = 2330.0
            reclaim_close_price = 2300.0
        values = {
            "sweep_id": (
                f"{timeframe.value}:liquidity_sweep:{sweep_suffix}"
            ),
            "exchange": "NSE",
            "symbol_token": "11536",
            "symbol": "TCS-EQ",
            "timeframe": timeframe,
            "interval": timeframe_interval(timeframe),
            "source": "angel_one",
            "liquidity_side": liquidity_side,
            "reference_price": reference_price,
            "extreme_price": extreme_price,
            "reclaim_close_price": reclaim_close_price,
            "swept_at": swept_at,
            "reclaimed_at": swept_at,
            "available_at": swept_at + timedelta(hours=6),
            "volume_multiple": 1.4,
            "evidence_ids": (
                f"{timeframe.value}:liquidity_sweep.{sweep_suffix}",
            ),
            "explanation": "Price breached and reclaimed known liquidity.",
        }
        values.update(overrides)
        return LiquiditySweep(**values)

    def zone(
        self,
        *,
        timeframe=SwingAnalysisTimeframe.DAILY,
        lifecycle=None,
        liquidity_sweeps=None,
        zone_suffix="base-1",
        **overrides,
    ):
        resolved_lifecycle = lifecycle or self.lifecycle(
            timeframe=timeframe,
        )
        resolved_sweeps = liquidity_sweeps or ()
        evidence_ids = tuple(
            evidence_id
            for event in resolved_lifecycle
            for evidence_id in event.evidence_ids
        ) + tuple(
            evidence_id
            for sweep in resolved_sweeps
            for evidence_id in sweep.evidence_ids
        )
        values = {
            "zone_id": (
                f"{timeframe.value}:accumulation:{zone_suffix}"
            ),
            "exchange": "NSE",
            "symbol_token": "11536",
            "symbol": "TCS-EQ",
            "timeframe": timeframe,
            "interval": timeframe_interval(timeframe),
            "source": "angel_one",
            "source_retrieved_at": self.retrieved_at,
            "evaluated_at": self.evaluated_at,
            "base_started_at": self.base_started_at,
            "base_last_observed_at": self.base_last_observed_at,
            "lower_price": 2240.0,
            "upper_price": 2315.0,
            "metrics": self.metrics(),
            "evidence_ids": evidence_ids,
            "lifecycle": resolved_lifecycle,
            "liquidity_sweeps": resolved_sweeps,
        }
        values.update(overrides)
        return AccumulationZone(**values)

    def analysis(
        self,
        *,
        timeframe=SwingAnalysisTimeframe.DAILY,
        zones=None,
        **overrides,
    ):
        values = {
            "analysis_id": (
                f"{timeframe.value}:accumulation_analysis"
            ),
            "exchange": "NSE",
            "symbol_token": "11536",
            "symbol": "TCS-EQ",
            "timeframe": timeframe,
            "interval": timeframe_interval(timeframe),
            "source": "angel_one",
            "source_retrieved_at": self.retrieved_at,
            "evaluated_at": self.evaluated_at,
            "zones": zones if zones is not None else (),
        }
        values.update(overrides)
        return TimeframeAccumulationAnalysis(**values)

    def test_builds_immutable_forming_zone_with_computed_fields(self):
        zone = self.zone()

        self.assertEqual(
            zone.current_state,
            AccumulationLifecycleState.FORMING,
        )
        self.assertAlmostEqual(zone.center_price, 2277.5)
        self.assertAlmostEqual(
            zone.width_percentage,
            (2315.0 - 2240.0) / 2240.0 * 100,
        )
        self.assertTrue(zone.is_active)
        self.assertIsNone(zone.confirmed_at)
        self.assertEqual(
            zone.model_dump()["current_state"],
            AccumulationLifecycleState.FORMING,
        )
        with self.assertRaises(ValidationError):
            zone.lower_price = 2200.0

    def test_normalizes_aware_source_timestamps_to_ist(self):
        utc_evaluated_at = self.evaluated_at.astimezone(timezone.utc)
        utc_retrieved_at = self.retrieved_at.astimezone(timezone.utc)

        analysis = self.analysis(
            evaluated_at=utc_evaluated_at,
            source_retrieved_at=utc_retrieved_at,
        )

        self.assertEqual(str(analysis.evaluated_at.tzinfo), "Asia/Kolkata")
        self.assertEqual(analysis.evaluated_at, self.evaluated_at)

    def test_supports_complete_confirmed_breakout_retest_lifecycle(self):
        lifecycle = self.lifecycle(
            AccumulationLifecycleState.FORMING,
            AccumulationLifecycleState.CONFIRMED,
            AccumulationLifecycleState.BREAKOUT,
            AccumulationLifecycleState.RETESTING,
            AccumulationLifecycleState.HOLDING_AS_SUPPORT,
        )

        zone = self.zone(lifecycle=lifecycle)

        self.assertEqual(
            zone.current_state,
            AccumulationLifecycleState.HOLDING_AS_SUPPORT,
        )
        self.assertEqual(zone.confirmed_at, lifecycle[1].available_at)
        self.assertEqual(zone.breakout_at, lifecycle[2].available_at)
        self.assertTrue(zone.is_active)

    def test_captures_bullish_and_bearish_liquidity_sweeps(self):
        sell_side = self.sweep()
        buy_side = self.sweep(
            liquidity_side=LiquidityPoolSide.BUY_SIDE,
            sweep_suffix="sweep-2",
        )
        zone = self.zone(liquidity_sweeps=(sell_side, buy_side))

        self.assertEqual(sell_side.implication, LiquiditySweepBias.BULLISH)
        self.assertEqual(buy_side.implication, LiquiditySweepBias.BEARISH)
        self.assertAlmostEqual(
            sell_side.sweep_percentage,
            (2240.0 - 2225.0) / 2240.0 * 100,
        )
        self.assertEqual(zone.sell_side_sweep_count, 1)
        self.assertEqual(zone.buy_side_sweep_count, 1)

    def test_rejects_a_breach_without_close_based_reclaim(self):
        invalid_cases = (
            {
                "liquidity_side": LiquidityPoolSide.SELL_SIDE,
                "reclaim_close_price": 2235.0,
            },
            {
                "liquidity_side": LiquidityPoolSide.BUY_SIDE,
                "reference_price": 2315.0,
                "extreme_price": 2330.0,
                "reclaim_close_price": 2320.0,
            },
            {
                "extreme_price": 2240.0,
            },
        )
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(ValidationError, "breach and reclaim"):
                    self.sweep(**overrides)

    def test_rejects_invalid_liquidity_sweep_time_and_assignment(self):
        with self.assertRaisesRegex(ValidationError, "sweep, reclaim"):
            self.sweep(
                reclaimed_at=self.evaluated_at - timedelta(days=14),
            )
        with self.assertRaisesRegex(ValidationError, "match timeframe"):
            self.sweep(interval="ONE_WEEK")

        weekly = self.sweep(timeframe=SwingAnalysisTimeframe.WEEKLY)
        with self.assertRaisesRegex(ValidationError, "match its accumulation"):
            self.zone(
                liquidity_sweeps=(weekly,),
                evidence_ids=(
                    "daily:accumulation.forming",
                    "daily:liquidity_sweep.sweep-1",
                ),
            )

        future = self.sweep(
            swept_at=self.evaluated_at + timedelta(days=1),
            reclaimed_at=self.evaluated_at + timedelta(days=1),
            available_at=self.evaluated_at + timedelta(days=1, hours=6),
        )
        with self.assertRaisesRegex(ValidationError, "historical"):
            self.zone(liquidity_sweeps=(future,))

    def test_rejects_duplicate_or_unowned_liquidity_sweep_evidence(self):
        sweep = self.sweep()
        with self.assertRaisesRegex(ValidationError, "must be unique"):
            self.zone(liquidity_sweeps=(sweep, sweep))
        with self.assertRaisesRegex(ValidationError, "belong to its zone"):
            self.zone(
                liquidity_sweeps=(sweep,),
                evidence_ids=("daily:accumulation.forming",),
            )

    def test_supports_failed_and_invalidated_terminal_paths(self):
        failed = self.zone(
            lifecycle=self.lifecycle(
                AccumulationLifecycleState.FORMING,
                AccumulationLifecycleState.CONFIRMED,
                AccumulationLifecycleState.BREAKOUT,
                AccumulationLifecycleState.FAILED_BREAKOUT,
            )
        )
        invalidated_lifecycle = self.lifecycle(
            AccumulationLifecycleState.FORMING,
            AccumulationLifecycleState.CONFIRMED,
            AccumulationLifecycleState.INVALIDATED,
        )
        invalidated = self.zone(
            lifecycle=invalidated_lifecycle,
            zone_suffix="base-2",
        )

        self.assertFalse(failed.is_active)
        self.assertFalse(invalidated.is_active)
        self.assertEqual(
            invalidated.invalidated_at,
            invalidated_lifecycle[-1].available_at,
        )

    def test_rejects_illegal_reordered_and_post_terminal_transitions(self):
        cases = (
            (
                AccumulationLifecycleState.BREAKOUT,
            ),
            (
                AccumulationLifecycleState.FORMING,
                AccumulationLifecycleState.BREAKOUT,
            ),
            (
                AccumulationLifecycleState.FORMING,
                AccumulationLifecycleState.INVALIDATED,
                AccumulationLifecycleState.CONFIRMED,
            ),
            (
                AccumulationLifecycleState.FORMING,
                AccumulationLifecycleState.CONFIRMED,
                AccumulationLifecycleState.RETESTING,
            ),
        )
        for states in cases:
            with self.subTest(states=states):
                with self.assertRaises(ValidationError):
                    self.zone(lifecycle=self.lifecycle(*states))

    def test_rejects_future_reversed_and_naive_lifecycle_timestamps(self):
        forming = self.event()
        future = self.event(
            available_at=self.evaluated_at + timedelta(minutes=1),
        )
        with self.assertRaisesRegex(ValidationError, "future evidence"):
            self.zone(lifecycle=(future,))

        with self.assertRaisesRegex(ValidationError, "precede observation"):
            self.event(
                observed_at=forming.available_at + timedelta(hours=1),
                available_at=forming.available_at,
            )

        with self.assertRaisesRegex(ValidationError, "include timezone"):
            self.event(
                observed_at=datetime(2026, 7, 20, 15, 30),
                available_at=datetime(2026, 7, 20, 16, 30),
            )

    def test_rejects_non_chronological_lifecycle_events(self):
        first = self.event()
        confirmed = self.event(
            AccumulationLifecycleState.CONFIRMED,
            observed_at=first.observed_at,
            available_at=first.available_at,
        )
        with self.assertRaisesRegex(ValidationError, "chronological"):
            self.zone(lifecycle=(first, confirmed))

    def test_rejects_invalid_boundaries_and_non_finite_metrics(self):
        for boundaries in (
            {"lower_price": 0.0},
            {"lower_price": 2400.0, "upper_price": 2300.0},
            {"upper_price": float("inf")},
        ):
            with self.subTest(boundaries=boundaries):
                with self.assertRaises(ValidationError):
                    self.zone(**boundaries)

        for metric_overrides in (
            {"confidence_score": 101.0},
            {"atr_compression_percentage": -1.0},
            {"normalized_price_slope_percentage": float("inf")},
            {"obv_slope": float("nan")},
            {"bullish_volume_share_percentage": float("inf")},
        ):
            with self.subTest(metrics=metric_overrides):
                with self.assertRaises(ValidationError):
                    self.metrics(**metric_overrides)

    def test_rejects_wrong_timeframe_interval_and_evidence_assignment(self):
        cases = (
            {"interval": "ONE_WEEK"},
            {"zone_id": "weekly:accumulation:base-1"},
            {"evidence_ids": ("weekly:accumulation.forming",)},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.zone(**overrides)

        with self.assertRaisesRegex(ValidationError, "belong to its zone"):
            self.zone(
                evidence_ids=("daily:accumulation.other",),
            )

    def test_rejects_invalid_zone_timeline_and_source_retrieval(self):
        cases = (
            {
                "base_started_at": self.base_last_observed_at
                + timedelta(days=1),
            },
            {
                "base_last_observed_at": self.evaluated_at
                + timedelta(days=1),
            },
            {
                "source_retrieved_at": self.evaluated_at
                - timedelta(minutes=1),
            },
            {"evaluated_at": datetime(2026, 8, 24, 15, 30)},
        )
        for overrides in cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.zone(**overrides)

    def test_builds_daily_and_weekly_empty_or_populated_analysis(self):
        daily_zone = self.zone()
        daily = self.analysis(zones=(daily_zone,))
        weekly = self.analysis(timeframe=SwingAnalysisTimeframe.WEEKLY)

        self.assertEqual(daily.active_zone_ids, (daily_zone.zone_id,))
        self.assertEqual(weekly.zones, ())
        self.assertEqual(weekly.active_zone_ids, ())
        self.assertEqual(weekly.interval, "ONE_WEEK")

    def test_analysis_rejects_wrong_identity_duplicate_and_zone_order(self):
        daily_zone = self.zone()
        weekly_zone = self.zone(
            timeframe=SwingAnalysisTimeframe.WEEKLY,
        )
        later_zone = self.zone(
            zone_suffix="base-2",
            base_started_at=self.base_started_at + timedelta(days=5),
        )

        invalid_cases = (
            {"analysis_id": "weekly:accumulation_analysis"},
            {"interval": "ONE_WEEK"},
            {"zones": (weekly_zone,)},
            {"zones": (daily_zone, daily_zone)},
            {"zones": (later_zone, daily_zone)},
        )
        for overrides in invalid_cases:
            with self.subTest(overrides=overrides):
                with self.assertRaises(ValidationError):
                    self.analysis(**overrides)


if __name__ == "__main__":
    unittest.main()
