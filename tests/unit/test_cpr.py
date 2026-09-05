import unittest
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.analytics.cpr import (
    CPRWidthClassificationConfig,
    calculate_latest_cpr,
)
from app.models.analysis_timeframe import SwingAnalysisTimeframe
from app.models.cpr import (
    CPRBasis,
    CPRLifecycleState,
    CPRPricePosition,
    CPRWidthRegime,
)
from app.models.market import Candle, HistoricalCandleSeries


IST = ZoneInfo("Asia/Kolkata")


def candle(
    year,
    month,
    day,
    close,
    *,
    high=None,
    low=None,
    tz=IST,
):
    resolved_high = high if high is not None else close + 2
    resolved_low = low if low is not None else close - 2
    return Candle(
        timestamp=datetime(year, month, day, 15, 30, tzinfo=tz),
        open=close,
        high=resolved_high,
        low=resolved_low,
        close=close,
        volume=1_000,
    )


class CPRCalculationTests(unittest.TestCase):
    @staticmethod
    def series(candles, *, interval="ONE_DAY", retrieved_at=None):
        resolved_retrieval = retrieved_at or (
            candles[-1].timestamp + timedelta(minutes=5)
        )
        return HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="11536",
            symbol="TCS-EQ",
            interval=interval,
            candles=list(candles),
            retrieved_at=resolved_retrieval,
            source="angel_one",
        )

    def daily_candles(self):
        return [
            # Deliberately partial archive-boundary week: never a CPR source.
            candle(2026, 7, 29, 94.0, high=96.0, low=92.0),
            candle(2026, 7, 31, 96.0, high=98.0, low=93.0),
            # Completed source week.
            candle(2026, 8, 3, 100.0, high=104.0, low=97.0),
            candle(2026, 8, 5, 106.0, high=110.0, low=99.0),
            candle(2026, 8, 7, 108.0, high=112.0, low=101.0),
            # Current target week; its extremes must not affect the CPR.
            candle(2026, 8, 10, 115.0, high=130.0, low=113.0),
            candle(2026, 8, 12, 117.0, high=140.0, low=114.0),
        ]

    @staticmethod
    def weekly_width_series(
        historical_closes,
        *,
        source_close,
        target_close=110.0,
    ):
        start = datetime(2026, 1, 2, 15, 30, tzinfo=IST)
        closes = [95.0, *historical_closes, source_close, target_close]
        candles = [
            Candle(
                timestamp=start + timedelta(days=index * 7),
                open=close,
                high=120.0,
                low=80.0,
                close=close,
                volume=1_000,
            )
            for index, close in enumerate(closes)
        ]
        return CPRCalculationTests.series(candles)

    def test_calculates_daily_cpr_from_previous_completed_week(self):
        record = calculate_latest_cpr(
            self.series(self.daily_candles()),
            timeframe=SwingAnalysisTimeframe.DAILY,
        )

        expected_pivot = (112.0 + 97.0 + 108.0) / 3
        raw_bottom = (112.0 + 97.0) / 2
        raw_top = 2 * expected_pivot - raw_bottom
        expected_bottom = min(raw_bottom, raw_top)
        expected_top = max(raw_bottom, raw_top)

        self.assertEqual(record.basis, CPRBasis.WEEKLY)
        self.assertEqual(record.source_high, 112.0)
        self.assertEqual(record.source_low, 97.0)
        self.assertEqual(record.source_close, 108.0)
        self.assertAlmostEqual(record.pivot, expected_pivot)
        self.assertAlmostEqual(record.bottom_central, expected_bottom)
        self.assertAlmostEqual(record.top_central, expected_top)
        self.assertAlmostEqual(
            record.width_percentage,
            (expected_top - expected_bottom) / expected_pivot * 100,
        )
        self.assertEqual(record.current_price, 117.0)
        self.assertEqual(record.price_position, CPRPricePosition.ABOVE)
        self.assertEqual(record.consecutive_acceptance_candles, 2)
        self.assertEqual(
            record.width_regime,
            CPRWidthRegime.INSUFFICIENT_HISTORY,
        )
        self.assertEqual(
            record.lifecycle_state,
            CPRLifecycleState.ACCEPTED_ABOVE,
        )
        self.assertIn(
            "daily:cpr.lifecycle.accepted_above",
            record.evidence_ids,
        )

    def test_completed_trading_week_allows_an_exchange_holiday(self):
        candles = [
            candle(2026, 7, 31, 96.0, high=98.0, low=93.0),
            # Monday, Tuesday, Thursday and Friday are observed sessions.
            candle(2026, 8, 3, 100.0, high=104.0, low=97.0),
            candle(2026, 8, 4, 103.0, high=107.0, low=99.0),
            candle(2026, 8, 6, 105.0, high=111.0, low=101.0),
            candle(2026, 8, 7, 108.0, high=110.0, low=102.0),
            # A following-week candle proves the four-session week ended.
            candle(2026, 8, 10, 114.0, high=116.0, low=109.0),
        ]

        record = calculate_latest_cpr(
            self.series(candles),
            timeframe=SwingAnalysisTimeframe.DAILY,
        )

        self.assertEqual(record.source_period_started_at, candles[1].timestamp)
        self.assertEqual(record.source_period_ended_at, candles[4].timestamp)
        self.assertEqual(record.source_high, 111.0)
        self.assertEqual(record.source_low, 97.0)
        self.assertEqual(record.source_close, 108.0)
        self.assertAlmostEqual(record.pivot, (111.0 + 97.0 + 108.0) / 3)

    def test_classifies_narrow_normal_and_wide_from_prior_widths(self):
        config = CPRWidthClassificationConfig(
            lookback_periods=4,
            minimum_samples=4,
        )
        cases = (
            ([106.0, 110.0, 114.0, 118.0], 101.0, 0.0, CPRWidthRegime.NARROW),
            ([102.0, 106.0, 114.0, 118.0], 110.0, 50.0, CPRWidthRegime.NORMAL),
            ([101.0, 102.0, 103.0, 104.0], 118.0, 100.0, CPRWidthRegime.WIDE),
        )

        for historical, current, percentile, expected in cases:
            with self.subTest(expected=expected):
                record = calculate_latest_cpr(
                    self.weekly_width_series(
                        historical,
                        source_close=current,
                    ),
                    timeframe=SwingAnalysisTimeframe.DAILY,
                    width_config=config,
                )
                self.assertEqual(record.width_sample_count, 4)
                self.assertAlmostEqual(record.width_percentile, percentile)
                self.assertEqual(record.width_regime, expected)

    def test_width_ranking_excludes_boundary_source_and_target_periods(self):
        config = CPRWidthClassificationConfig(
            lookback_periods=3,
            minimum_samples=3,
        )
        series = self.weekly_width_series(
            [118.0, 114.0, 110.0, 106.0],
            source_close=101.0,
            target_close=119.0,
        )
        record = calculate_latest_cpr(
            series,
            timeframe=SwingAnalysisTimeframe.DAILY,
            width_config=config,
        )

        self.assertEqual(record.width_sample_count, 3)
        self.assertEqual(record.width_percentile, 0.0)
        self.assertEqual(record.width_regime, CPRWidthRegime.NARROW)

        changed_target = list(series.candles)
        changed_target[-1] = changed_target[-1].model_copy(
            update={"high": 200.0, "low": 10.0}
        )
        target_extremes = calculate_latest_cpr(
            self.series(changed_target),
            timeframe=SwingAnalysisTimeframe.DAILY,
            width_config=config,
        )
        self.assertEqual(
            target_extremes.width_percentile,
            record.width_percentile,
        )
        self.assertEqual(target_extremes.width_regime, record.width_regime)

    def test_width_percentile_uses_midrank_for_equal_ranges(self):
        config = CPRWidthClassificationConfig(
            lookback_periods=4,
            minimum_samples=4,
        )
        record = calculate_latest_cpr(
            self.weekly_width_series(
                [110.0, 110.0, 110.0, 110.0],
                source_close=110.0,
            ),
            timeframe=SwingAnalysisTimeframe.DAILY,
            width_config=config,
        )

        self.assertEqual(record.width_percentile, 50.0)
        self.assertEqual(record.width_regime, CPRWidthRegime.NORMAL)

    def test_counts_only_trailing_completed_closes_above_cpr(self):
        candles = self.daily_candles()[:5]
        candles.extend(
            (
                candle(2026, 8, 10, 115.0),
                candle(2026, 8, 11, 105.0),
                candle(2026, 8, 12, 114.0),
                candle(2026, 8, 13, 116.0),
            )
        )
        record = calculate_latest_cpr(
            self.series(candles),
            timeframe=SwingAnalysisTimeframe.DAILY,
        )

        self.assertEqual(record.price_position, CPRPricePosition.ABOVE)
        self.assertEqual(record.consecutive_acceptance_candles, 2)

    def test_counts_only_trailing_completed_closes_below_cpr(self):
        candles = self.daily_candles()[:5]
        candles.extend(
            (
                candle(2026, 8, 10, 105.0),
                candle(2026, 8, 11, 95.0),
                candle(2026, 8, 12, 94.0),
                candle(2026, 8, 13, 93.0),
            )
        )
        record = calculate_latest_cpr(
            self.series(candles),
            timeframe=SwingAnalysisTimeframe.DAILY,
        )

        self.assertEqual(record.price_position, CPRPricePosition.BELOW)
        self.assertEqual(record.consecutive_acceptance_candles, 3)

    def test_boundary_close_is_inside_and_has_no_acceptance(self):
        source = self.daily_candles()[:5]
        expected_pivot = (112.0 + 97.0 + 108.0) / 3
        raw_bottom = (112.0 + 97.0) / 2
        raw_top = 2 * expected_pivot - raw_bottom
        top = max(raw_bottom, raw_top)
        source.append(candle(2026, 8, 10, top, high=top + 1, low=top - 1))
        record = calculate_latest_cpr(
            self.series(source),
            timeframe=SwingAnalysisTimeframe.DAILY,
        )

        self.assertEqual(record.price_position, CPRPricePosition.INSIDE)
        self.assertEqual(record.consecutive_acceptance_candles, 0)

    def test_acceptance_respects_evaluation_cutoff(self):
        candles = self.daily_candles()[:5]
        candles.extend(
            (
                candle(2026, 8, 10, 115.0),
                candle(2026, 8, 11, 116.0),
                candle(2026, 8, 12, 105.0),
            )
        )
        record = calculate_latest_cpr(
            self.series(candles),
            timeframe=SwingAnalysisTimeframe.DAILY,
            evaluated_at=candles[-2].timestamp,
        )

        self.assertEqual(record.price_position, CPRPricePosition.ABOVE)
        self.assertEqual(record.consecutive_acceptance_candles, 2)

    def test_fingerprint_captures_acceptance_path_with_same_latest_close(self):
        prefix = self.daily_candles()[:5]
        accepted = [
            *prefix,
            candle(2026, 8, 10, 114.0),
            candle(2026, 8, 11, 115.0),
        ]
        interrupted = [
            *prefix,
            candle(2026, 8, 10, 105.0),
            candle(2026, 8, 11, 115.0),
        ]
        accepted_record = calculate_latest_cpr(
            self.series(accepted),
            timeframe=SwingAnalysisTimeframe.DAILY,
        )
        interrupted_record = calculate_latest_cpr(
            self.series(interrupted),
            timeframe=SwingAnalysisTimeframe.DAILY,
        )

        self.assertEqual(accepted_record.current_price, 115.0)
        self.assertEqual(interrupted_record.current_price, 115.0)
        self.assertEqual(accepted_record.consecutive_acceptance_candles, 2)
        self.assertEqual(interrupted_record.consecutive_acceptance_candles, 1)
        self.assertNotEqual(
            accepted_record.calculation_fingerprint,
            interrupted_record.calculation_fingerprint,
        )

    def test_classifies_completed_candle_cpr_lifecycle(self):
        cases = (
            (
                "bullish breakout",
                (candle(2026, 8, 10, 105.0), candle(2026, 8, 11, 115.0)),
                CPRLifecycleState.BULLISH_BREAKOUT,
            ),
            (
                "bearish breakdown",
                (candle(2026, 8, 10, 105.0), candle(2026, 8, 11, 95.0)),
                CPRLifecycleState.BEARISH_BREAKDOWN,
            ),
            (
                "bullish retest",
                (
                    candle(2026, 8, 10, 115.0, high=117.0, low=110.0),
                    candle(2026, 8, 11, 114.0, high=116.0, low=106.0),
                ),
                CPRLifecycleState.BULLISH_RETEST,
            ),
            (
                "bearish retest",
                (
                    candle(2026, 8, 10, 95.0, high=100.0, low=93.0),
                    candle(2026, 8, 11, 96.0, high=105.0, low=94.0),
                ),
                CPRLifecycleState.BEARISH_RETEST,
            ),
            (
                "upper rejection",
                (
                    candle(2026, 8, 10, 105.0, high=106.0, low=104.8),
                    candle(2026, 8, 11, 105.0, high=110.0, low=104.8),
                ),
                CPRLifecycleState.UPPER_REJECTION,
            ),
            (
                "lower rejection",
                (
                    candle(2026, 8, 10, 105.0, high=106.0, low=104.8),
                    candle(2026, 8, 11, 105.0, high=106.0, low=100.0),
                ),
                CPRLifecycleState.LOWER_REJECTION,
            ),
            (
                "bullish reclaim",
                (candle(2026, 8, 10, 95.0), candle(2026, 8, 11, 115.0)),
                CPRLifecycleState.BULLISH_RECLAIM,
            ),
            (
                "bearish reclaim",
                (candle(2026, 8, 10, 115.0), candle(2026, 8, 11, 95.0)),
                CPRLifecycleState.BEARISH_RECLAIM,
            ),
            (
                "failed bullish breakout",
                (candle(2026, 8, 10, 115.0), candle(2026, 8, 11, 105.0)),
                CPRLifecycleState.FAILED_BULLISH_BREAKOUT,
            ),
            (
                "failed bearish breakdown",
                (candle(2026, 8, 10, 95.0), candle(2026, 8, 11, 105.0)),
                CPRLifecycleState.FAILED_BEARISH_BREAKDOWN,
            ),
            (
                "accepted above",
                (
                    candle(2026, 8, 10, 115.0, high=117.0, low=110.0),
                    candle(2026, 8, 11, 116.0, high=118.0, low=112.0),
                ),
                CPRLifecycleState.ACCEPTED_ABOVE,
            ),
            (
                "accepted below",
                (
                    candle(2026, 8, 10, 95.0, high=100.0, low=93.0),
                    candle(2026, 8, 11, 96.0, high=100.0, low=94.0),
                ),
                CPRLifecycleState.ACCEPTED_BELOW,
            ),
            (
                "trading inside",
                (
                    candle(2026, 8, 10, 105.0, high=106.0, low=104.8),
                    candle(2026, 8, 11, 105.0, high=106.0, low=104.8),
                ),
                CPRLifecycleState.TRADING_INSIDE,
            ),
        )

        for label, target, expected in cases:
            with self.subTest(label=label):
                record = calculate_latest_cpr(
                    self.series([*self.daily_candles()[:5], *target]),
                    timeframe=SwingAnalysisTimeframe.DAILY,
                )
                self.assertEqual(record.lifecycle_state, expected)
                self.assertIn(
                    f"daily:cpr.lifecycle.{expected.value}",
                    record.evidence_ids,
                )

    def test_lifecycle_remains_untested_without_prior_target_candle(self):
        record = calculate_latest_cpr(
            self.series(
                [
                    *self.daily_candles()[:5],
                    candle(2026, 8, 10, 115.0),
                ]
            ),
            timeframe=SwingAnalysisTimeframe.DAILY,
        )

        self.assertEqual(record.lifecycle_state, CPRLifecycleState.UNTESTED)
        self.assertIn(
            "daily:cpr.lifecycle.untested",
            record.evidence_ids,
        )

    def test_rejects_invalid_width_classification_configuration(self):
        invalid = (
            {"lookback_periods": 4, "minimum_samples": 5},
            {"narrow_percentile_max": 75.0, "wide_percentile_min": 75.0},
            {"narrow_percentile_max": float("nan")},
        )

        for values in invalid:
            with self.subTest(values=values), self.assertRaises(
                ValidationError
            ):
                CPRWidthClassificationConfig(**values)

    def test_calculates_weekly_cpr_from_previous_completed_month(self):
        candles = [
            candle(2026, 6, 26, 190.0, high=195.0, low=185.0),
            candle(2026, 7, 3, 200.0, high=206.0, low=194.0),
            candle(2026, 7, 10, 205.0, high=212.0, low=198.0),
            candle(2026, 7, 31, 210.0, high=218.0, low=202.0),
            candle(2026, 8, 7, 199.0, high=225.0, low=180.0),
            candle(2026, 8, 14, 197.0, high=230.0, low=175.0),
        ]
        record = calculate_latest_cpr(
            self.series(candles, interval="ONE_WEEK"),
            timeframe=SwingAnalysisTimeframe.WEEKLY,
        )

        self.assertEqual(record.basis, CPRBasis.MONTHLY)
        self.assertEqual(record.source_high, 218.0)
        self.assertEqual(record.source_low, 194.0)
        self.assertEqual(record.source_close, 210.0)
        self.assertEqual(record.current_price, 197.0)
        self.assertEqual(record.price_position, CPRPricePosition.BELOW)
        self.assertEqual(record.analysis_id, "weekly:cpr:monthly-2026-08")

    def test_cutoff_excludes_later_candles_without_lookahead(self):
        candles = self.daily_candles()
        cutoff = candles[-2].timestamp
        record = calculate_latest_cpr(
            self.series(candles),
            timeframe=SwingAnalysisTimeframe.DAILY,
            evaluated_at=cutoff,
        )

        self.assertEqual(record.valid_to, cutoff)
        self.assertEqual(record.current_price, 115.0)
        self.assertNotEqual(record.current_price, candles[-1].close)

    def test_first_archive_group_is_never_used_as_source(self):
        with self.assertRaisesRegex(ValueError, "archive boundary"):
            calculate_latest_cpr(
                self.series(self.daily_candles()[:5]),
                timeframe=SwingAnalysisTimeframe.DAILY,
            )

    def test_rejects_empty_wrong_interval_and_unordered_series(self):
        retrieved_at = datetime(2026, 8, 14, 15, 35, tzinfo=IST)
        empty = self.series([], retrieved_at=retrieved_at)
        with self.assertRaisesRegex(ValueError, "requires candles"):
            calculate_latest_cpr(
                empty,
                timeframe=SwingAnalysisTimeframe.DAILY,
            )

        with self.assertRaisesRegex(ValueError, "interval"):
            calculate_latest_cpr(
                self.series(self.daily_candles(), interval="ONE_WEEK"),
                timeframe=SwingAnalysisTimeframe.DAILY,
            )

        unordered = self.daily_candles()
        unordered[-1], unordered[-2] = unordered[-2], unordered[-1]
        with self.assertRaisesRegex(ValueError, "ascending order"):
            calculate_latest_cpr(
                self.series(unordered),
                timeframe=SwingAnalysisTimeframe.DAILY,
            )

    def test_rejects_naive_or_post_retrieval_cutoff(self):
        series = self.series(self.daily_candles())
        with self.assertRaisesRegex(ValueError, "timezone"):
            calculate_latest_cpr(
                series,
                timeframe=SwingAnalysisTimeframe.DAILY,
                evaluated_at=datetime(2026, 8, 12, 15, 30),
            )

        with self.assertRaisesRegex(ValueError, "source retrieval"):
            calculate_latest_cpr(
                series,
                timeframe=SwingAnalysisTimeframe.DAILY,
                evaluated_at=series.retrieved_at + timedelta(seconds=1),
            )

    def test_fingerprint_is_stable_across_equivalent_timezones(self):
        candles = self.daily_candles()
        ist_record = calculate_latest_cpr(
            self.series(candles),
            timeframe=SwingAnalysisTimeframe.DAILY,
        )
        utc_candles = [
            current.model_copy(
                update={"timestamp": current.timestamp.astimezone(timezone.utc)}
            )
            for current in candles
        ]
        utc_record = calculate_latest_cpr(
            self.series(
                utc_candles,
                retrieved_at=(
                    candles[-1].timestamp + timedelta(minutes=5)
                ).astimezone(timezone.utc),
            ),
            timeframe=SwingAnalysisTimeframe.DAILY,
        )

        self.assertEqual(
            ist_record.calculation_fingerprint,
            utc_record.calculation_fingerprint,
        )
        self.assertEqual(utc_record.valid_to.tzinfo, IST)

    def test_fingerprint_changes_when_eligible_market_input_changes(self):
        candles = self.daily_candles()
        original = calculate_latest_cpr(
            self.series(candles),
            timeframe=SwingAnalysisTimeframe.DAILY,
        )
        changed = list(candles)
        changed[-1] = candle(
            2026,
            8,
            12,
            118.0,
            high=140.0,
            low=70.0,
        )
        updated = calculate_latest_cpr(
            self.series(changed),
            timeframe=SwingAnalysisTimeframe.DAILY,
        )

        self.assertNotEqual(
            original.calculation_fingerprint,
            updated.calculation_fingerprint,
        )


if __name__ == "__main__":
    unittest.main()
