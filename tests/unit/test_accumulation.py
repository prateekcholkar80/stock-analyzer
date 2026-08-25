import unittest
from datetime import datetime, timedelta
from unittest.mock import patch
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.analytics.accumulation import (
    AccumulationDetectionConfig,
    AccumulationTimeframeProfile,
    detect_accumulation_zones,
)
from app.analytics.indicators import calculate_atr, calculate_obv
from app.models.accumulation import (
    AccumulationLifecycleState,
    LiquidityPoolSide,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.multi_timeframe_evidence import SwingAnalysisTimeframe


IST = ZoneInfo("Asia/Kolkata")


class AccumulationDetectionTests(unittest.TestCase):
    def candles(self, *, include_follow_up=True, include_sweeps=True):
        first = datetime(2026, 1, 1, 15, 30, tzinfo=IST)
        candles = []
        previous_close = 100.0

        for index in range(20):
            close = 100.0 + (index % 4) * 1.5
            candles.append(
                Candle(
                    timestamp=first + timedelta(days=index),
                    open=previous_close,
                    high=max(previous_close, close) + 3.0,
                    low=min(previous_close, close) - 3.0,
                    close=close,
                    volume=1_600,
                )
            )
            previous_close = close

        base_pattern = (
            100.8,
            104.2,
            101.2,
            103.8,
            100.9,
            104.1,
            101.1,
            104.0,
        )
        for index in range(24):
            close = base_pattern[index % len(base_pattern)]
            rising = close > previous_close
            low = 99.8 if index % 4 == 0 else min(close, previous_close) - 0.35
            high = 105.2 if index % 4 == 1 else max(close, previous_close) + 0.35
            if include_sweeps and index == 10:
                low = 98.8
                close = 101.3
            if include_sweeps and index == 14:
                high = 106.4
                close = 103.8
            candles.append(
                Candle(
                    timestamp=first + timedelta(days=20 + index),
                    open=previous_close,
                    high=max(high, previous_close, close),
                    low=min(low, previous_close, close),
                    close=close,
                    volume=(
                        2_200 + index * 15
                        if rising
                        else max(500, 1_050 - index * 12)
                    ),
                )
            )
            previous_close = close

        if include_follow_up:
            follow_up = (
                (106.1, 106.8, 104.9, 3_200),
                (105.1, 105.8, 104.7, 1_300),
                (106.0, 106.5, 105.0, 1_900),
            )
            for offset, (close, high, low, volume) in enumerate(follow_up):
                candles.append(
                    Candle(
                        timestamp=first + timedelta(days=44 + offset),
                        open=previous_close,
                        high=max(high, previous_close, close),
                        low=min(low, previous_close, close),
                        close=close,
                        volume=volume,
                    )
                )
                previous_close = close
        return candles

    @staticmethod
    def series(candles, interval="ONE_DAY"):
        return HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="11536",
            symbol="TCS-EQ",
            interval=interval,
            candles=candles,
            retrieved_at=candles[-1].timestamp + timedelta(days=1),
            source="test_market",
        )

    def test_detects_daily_accumulation_and_tracks_breakout_retest(self):
        with (
            patch(
                "app.analytics.accumulation.calculate_atr",
                wraps=calculate_atr,
            ) as atr_calculator,
            patch(
                "app.analytics.accumulation.calculate_obv",
                wraps=calculate_obv,
            ) as obv_calculator,
        ):
            result = detect_accumulation_zones(
                self.series(self.candles()),
            )

        atr_calculator.assert_called_once()
        obv_calculator.assert_called_once()
        self.assertEqual(result.timeframe, SwingAnalysisTimeframe.DAILY)
        self.assertTrue(result.zones)
        zone = result.zones[-1]
        self.assertLess(zone.lower_price, zone.upper_price)
        self.assertGreaterEqual(
            zone.metrics.lower_boundary_touch_count,
            2,
        )
        self.assertGreaterEqual(
            zone.metrics.upper_boundary_touch_count,
            2,
        )
        self.assertGreaterEqual(zone.metrics.confidence_score, 60.0)
        self.assertEqual(
            zone.current_state,
            AccumulationLifecycleState.HOLDING_AS_SUPPORT,
        )
        self.assertIsNotNone(zone.metrics.breakout_volume_multiple)

    def test_detects_sell_and_buy_side_sweeps_only_after_reclaim(self):
        result = detect_accumulation_zones(
            self.series(self.candles()),
        )
        zone = result.zones[-1]

        sides = {sweep.liquidity_side for sweep in zone.liquidity_sweeps}
        self.assertIn(LiquidityPoolSide.SELL_SIDE, sides)
        self.assertIn(LiquidityPoolSide.BUY_SIDE, sides)
        for sweep in zone.liquidity_sweeps:
            self.assertLessEqual(sweep.available_at, result.evaluated_at)
            self.assertGreaterEqual(sweep.available_at, zone.confirmed_at)
            if sweep.liquidity_side is LiquidityPoolSide.SELL_SIDE:
                self.assertLess(sweep.extreme_price, sweep.reference_price)
                self.assertGreaterEqual(
                    sweep.reclaim_close_price,
                    sweep.reference_price,
                )
            else:
                self.assertGreater(sweep.extreme_price, sweep.reference_price)
                self.assertLessEqual(
                    sweep.reclaim_close_price,
                    sweep.reference_price,
                )

    def test_is_look_ahead_safe_at_historical_evaluation_time(self):
        candles = self.candles()
        series = self.series(candles)
        before_breakout = candles[43].timestamp

        historical = detect_accumulation_zones(
            series,
            as_of=before_breakout,
        )
        complete = detect_accumulation_zones(series)

        self.assertTrue(historical.zones)
        self.assertEqual(
            historical.zones[-1].current_state,
            AccumulationLifecycleState.CONFIRMED,
        )
        self.assertEqual(
            complete.zones[-1].current_state,
            AccumulationLifecycleState.HOLDING_AS_SUPPORT,
        )
        self.assertTrue(
            all(
                event.available_at <= before_breakout
                for zone in historical.zones
                for event in zone.lifecycle
            )
        )

    def test_supports_weekly_profile_and_ist_output(self):
        candles = self.candles(include_follow_up=False)
        weekly = [
            candle.model_copy(
                update={
                    "timestamp": candle.timestamp
                    + timedelta(days=index * 6),
                }
            )
            for index, candle in enumerate(candles)
        ]
        result = detect_accumulation_zones(
            self.series(weekly, interval="ONE_WEEK"),
        )

        self.assertEqual(result.timeframe, SwingAnalysisTimeframe.WEEKLY)
        self.assertEqual(result.interval, "ONE_WEEK")
        self.assertEqual(str(result.evaluated_at.tzinfo), "Asia/Kolkata")
        self.assertTrue(result.zones)

    def test_returns_empty_analysis_for_insufficient_or_trending_data(self):
        short = self.candles(include_follow_up=False)[:10]
        insufficient = detect_accumulation_zones(self.series(short))
        self.assertEqual(insufficient.zones, ())

        first = datetime(2026, 1, 1, 15, 30, tzinfo=IST)
        trend = [
            Candle(
                timestamp=first + timedelta(days=index),
                open=100.0 + index * 2.0,
                high=101.5 + index * 2.0,
                low=99.5 + index * 2.0,
                close=101.0 + index * 2.0,
                volume=1_000 + index * 10,
            )
            for index in range(60)
        ]
        trending = detect_accumulation_zones(self.series(trend))
        self.assertEqual(trending.zones, ())

    def test_zero_volume_range_is_not_mislabelled_as_accumulation(self):
        candles = [
            candle.model_copy(update={"volume": 0})
            for candle in self.candles(include_follow_up=False)
        ]

        result = detect_accumulation_zones(self.series(candles))

        self.assertEqual(result.zones, ())

    def test_tracks_failed_breakout_and_does_not_invent_sweep(self):
        candles = self.candles()
        breakout_at = candles[44].timestamp
        failure_at = candles[45].timestamp
        candles[45] = Candle(
            timestamp=failure_at,
            open=candles[44].close,
            high=106.5,
            low=97.5,
            close=98.5,
            volume=3_500,
        )
        candles = candles[:46]

        result = detect_accumulation_zones(self.series(candles))
        zone = result.zones[-1]

        self.assertEqual(
            zone.current_state,
            AccumulationLifecycleState.FAILED_BREAKOUT,
        )
        self.assertEqual(zone.breakout_at, breakout_at)
        self.assertFalse(
            any(
                sweep.swept_at == failure_at
                and sweep.liquidity_side is LiquidityPoolSide.SELL_SIDE
                for sweep in zone.liquidity_sweeps
            )
        )

    def test_ids_and_results_are_deterministic(self):
        series = self.series(self.candles())

        first = detect_accumulation_zones(series)
        second = detect_accumulation_zones(series)

        self.assertEqual(first, second)
        self.assertEqual(
            [zone.zone_id for zone in first.zones],
            [zone.zone_id for zone in second.zones],
        )

    def test_ignores_unused_future_prices_at_historical_as_of(self):
        candles = self.candles(include_follow_up=False)
        as_of = candles[-1].timestamp
        future_timestamp = as_of + timedelta(days=1)
        candles.append(
            Candle(
                timestamp=future_timestamp,
                open=0.0,
                high=0.0,
                low=0.0,
                close=0.0,
                volume=0,
            )
        )

        result = detect_accumulation_zones(
            self.series(candles),
            as_of=as_of,
        )

        self.assertTrue(result.zones)
        self.assertTrue(
            all(
                event.available_at <= as_of
                for zone in result.zones
                for event in zone.lifecycle
            )
        )

    def test_validates_profiles_and_detector_config(self):
        with self.assertRaisesRegex(ValidationError, "cannot be below"):
            AccumulationTimeframeProfile(
                minimum_base_candles=20,
                maximum_base_candles=10,
                maximum_range_width_percentage=8.0,
                minimum_close_containment_percentage=70.0,
                minimum_boundary_touches=2,
                maximum_absolute_slope_percentage=0.2,
                minimum_confidence_score=60.0,
                maximum_zone_age_candles=30,
                maximum_zones=3,
            )
        with self.assertRaisesRegex(ValidationError, "must exceed"):
            AccumulationDetectionConfig(
                minimum_sweep_percentage=2.0,
                maximum_sweep_percentage=1.0,
            )
        with self.assertRaises(ValidationError):
            AccumulationDetectionConfig(boundary_quantile=float("nan"))

    def test_rejects_bad_interval_time_order_and_non_positive_prices(self):
        candles = self.candles(include_follow_up=False)
        with self.assertRaisesRegex(ValueError, "ONE_DAY or ONE_WEEK"):
            detect_accumulation_zones(
                self.series(candles, interval="ONE_HOUR")
            )
        with self.assertRaisesRegex(ValueError, "include timezone"):
            detect_accumulation_zones(
                self.series(candles),
                as_of=datetime(2026, 8, 1, 15, 30),
            )
        with self.assertRaisesRegex(ValueError, "follow source retrieval"):
            series = self.series(candles)
            detect_accumulation_zones(
                series,
                as_of=series.retrieved_at + timedelta(minutes=1),
            )

        unordered = candles.copy()
        unordered[2], unordered[3] = unordered[3], unordered[2]
        with self.assertRaisesRegex(ValueError, "chronological"):
            detect_accumulation_zones(self.series(unordered))

        zero = candles.copy()
        zero[0] = Candle(
            timestamp=zero[0].timestamp,
            open=0.0,
            high=0.0,
            low=0.0,
            close=0.0,
            volume=zero[0].volume,
        )
        with self.assertRaisesRegex(ValueError, "positive OHLC"):
            detect_accumulation_zones(self.series(zero))

        with self.assertRaisesRegex(ValueError, "HistoricalCandleSeries"):
            detect_accumulation_zones(object())
        with self.assertRaisesRegex(ValueError, "config must be"):
            detect_accumulation_zones(
                self.series(candles),
                config=object(),
            )


if __name__ == "__main__":
    unittest.main()
