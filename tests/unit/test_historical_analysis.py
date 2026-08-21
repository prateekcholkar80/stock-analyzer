import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.analytics.historical_analysis import (
    analyze_swing_profiles_over_history,
)
from app.analytics.signal_profiles import (
    build_swing_trading_signal_profile,
)
from app.exceptions import (
    IndicatorCalculationError,
    InsufficientDataError,
    TechnicalAnalysisError,
)
from app.models.historical_analysis import (
    HistoricalAnalysisFailureKind,
    HistoricalSwingProfileSeries,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalStrength,
    TechnicalSignalEvidence,
    TechnicalSignalSnapshot,
)


class HistoricalSwingAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.base_time = datetime(2026, 8, 1, tzinfo=UTC)
        self.retrieved_at = self.base_time + timedelta(days=10)
        self.received_prefixes = []

    def market(self, count=8, **overrides):
        candles = [
            Candle(
                timestamp=self.base_time + timedelta(days=index),
                open=100 + index,
                high=102 + index,
                low=99 + index,
                close=101 + index,
                volume=1_000 + index,
            )
            for index in range(count)
        ]
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

    def profile_for(
        self,
        series,
        *,
        direction=None,
        evaluated_at=None,
        symbol=None,
        category_weights=None,
    ):
        candle = series.candles[-1]
        timestamp = evaluated_at or candle.timestamp
        if direction is None:
            direction = (
                SignalDirection.BULLISH
                if len(series.candles) % 2
                else SignalDirection.BEARISH
            )
        evidence = [
            TechnicalSignalEvidence(
                evidence_id=f"{category.value}_signal",
                name=f"{category.value} signal",
                category=category,
                direction=direction,
                strength=SignalStrength.STRONG,
                source="test.historical",
                explanation="Point-in-time deterministic evidence.",
                observed_at=timestamp,
                available_at=timestamp,
                observed_values={"close": candle.close},
            )
            for category in SignalCategory
        ]
        snapshot = TechnicalSignalSnapshot(
            exchange=series.exchange,
            symbol_token=series.symbol_token,
            symbol=symbol or series.symbol,
            interval=series.interval,
            source=series.source,
            source_retrieved_at=series.retrieved_at,
            evaluated_at=timestamp,
            evidence=evidence,
        )
        return build_swing_trading_signal_profile(
            snapshot,
            category_weights=category_weights,
        )

    def evaluator(self, series):
        self.received_prefixes.append(series)
        return self.profile_for(series)

    def test_builds_one_point_for_each_post_warmup_candle(self):
        series = self.market()

        result = analyze_swing_profiles_over_history(
            series,
            self.evaluator,
            warmup_candles=2,
        )

        self.assertEqual(
            [point.candle_index for point in result.points],
            [2, 3, 4, 5, 6, 7],
        )
        self.assertEqual(result.attempted_evaluation_count, 6)
        self.assertEqual(result.successful_evaluation_count, 6)
        self.assertEqual(result.failed_evaluation_count, 0)
        self.assertEqual(result.success_rate_percentage, 100)
        self.assertEqual(result.points[0].available_candle_count, 3)
        self.assertEqual(result.points[0].close, 103)
        self.assertEqual(len(result.points[0].evidence_ids), 6)
        self.assertEqual(
            set(result.points[0].category_scores),
            {category.value for category in SignalCategory},
        )

    def test_evaluator_receives_only_each_historical_prefix(self):
        analyze_swing_profiles_over_history(
            self.market(6),
            self.evaluator,
            warmup_candles=2,
        )

        self.assertEqual(
            [len(prefix.candles) for prefix in self.received_prefixes],
            [3, 4, 5, 6],
        )
        for prefix in self.received_prefixes:
            self.assertEqual(
                prefix.candles[-1].timestamp,
                self.base_time + timedelta(days=len(prefix.candles) - 1),
            )

    def test_stride_and_time_bounds_define_deterministic_schedule(self):
        result = analyze_swing_profiles_over_history(
            self.market(),
            self.evaluator,
            warmup_candles=1,
            evaluation_stride=2,
            start_at=self.base_time + timedelta(days=2),
            end_at=self.base_time + timedelta(days=6),
        )

        self.assertEqual(
            [point.candle_index for point in result.points],
            [2, 4, 6],
        )

    def test_empty_or_fully_warmed_series_has_no_attempts(self):
        cases = (
            (self.market(0), 0),
            (self.market(3), 3),
            (self.market(3), 10),
        )
        for series, warmup in cases:
            with self.subTest(count=len(series.candles), warmup=warmup):
                result = analyze_swing_profiles_over_history(
                    series,
                    self.evaluator,
                    warmup_candles=warmup,
                )
                self.assertEqual(result.attempted_evaluation_count, 0)
                self.assertEqual(result.success_rate_percentage, 0)

    def test_insufficient_data_is_recorded_and_analysis_continues(self):
        def warmup_evaluator(series):
            if len(series.candles) < 4:
                raise InsufficientDataError("Four candles are required.")
            return self.profile_for(series)

        result = analyze_swing_profiles_over_history(
            self.market(6),
            warmup_evaluator,
        )

        self.assertEqual(
            [failure.candle_index for failure in result.failures],
            [0, 1, 2],
        )
        self.assertEqual(
            [point.candle_index for point in result.points],
            [3, 4, 5],
        )
        self.assertTrue(
            all(
                failure.kind
                is HistoricalAnalysisFailureKind.INSUFFICIENT_DATA
                for failure in result.failures
            )
        )
        self.assertEqual(result.success_rate_percentage, 50)

    def test_technical_failure_kinds_are_preserved(self):
        errors = (
            (
                IndicatorCalculationError("TA-Lib failed."),
                HistoricalAnalysisFailureKind.INDICATOR_CALCULATION,
            ),
            (
                TechnicalAnalysisError("Technical pipeline failed."),
                HistoricalAnalysisFailureKind.TECHNICAL_ANALYSIS,
            ),
        )
        for error, expected_kind in errors:
            with self.subTest(error=type(error).__name__):
                def failing_evaluator(series, failure=error):
                    raise failure

                result = analyze_swing_profiles_over_history(
                    self.market(1),
                    failing_evaluator,
                )
                self.assertEqual(result.failures[0].kind, expected_kind)
                self.assertEqual(
                    result.failures[0].error_type,
                    type(error).__name__,
                )
                self.assertEqual(result.failures[0].message, str(error))

    def test_empty_technical_error_gets_non_blank_message(self):
        def failing_evaluator(series):
            raise InsufficientDataError()

        result = analyze_swing_profiles_over_history(
            self.market(1),
            failing_evaluator,
        )

        self.assertEqual(
            result.failures[0].message,
            "InsufficientDataError",
        )

    def test_unexpected_programming_error_is_not_silenced(self):
        def invalid_evaluator(series):
            raise ValueError("Invalid evaluator configuration.")

        with self.assertRaisesRegex(ValueError, "Invalid evaluator"):
            analyze_swing_profiles_over_history(
                self.market(1),
                invalid_evaluator,
            )

    def test_rejects_non_profile_evaluator_result(self):
        with self.assertRaisesRegex(ValueError, "must return"):
            analyze_swing_profiles_over_history(
                self.market(1),
                lambda series: {"score": 100},
            )

    def test_rejects_profile_for_another_instrument(self):
        def mismatched_evaluator(series):
            return self.profile_for(series, symbol="TCS-EQ")

        with self.assertRaisesRegex(ValueError, "another source"):
            analyze_swing_profiles_over_history(
                self.market(1),
                mismatched_evaluator,
            )

    def test_rejects_profile_for_wrong_evaluation_date(self):
        def stale_evaluator(series):
            return self.profile_for(
                series,
                evaluated_at=series.candles[-1].timestamp - timedelta(
                    hours=1
                ),
            )

        with self.assertRaisesRegex(ValueError, "scheduled candle"):
            analyze_swing_profiles_over_history(
                self.market(1),
                stale_evaluator,
            )

    def test_revalidates_profile_returned_by_evaluator(self):
        def forged_evaluator(series):
            profile = self.profile_for(series)
            evidence = profile.snapshot.evidence[0].model_copy(
                update={
                    "available_at": (
                        profile.snapshot.evaluated_at + timedelta(days=1)
                    )
                }
            )
            snapshot = profile.snapshot.model_copy(
                update={
                    "evidence": [
                        evidence,
                        *profile.snapshot.evidence[1:],
                    ]
                }
            )
            return profile.model_copy(update={"snapshot": snapshot})

        with self.assertRaisesRegex(ValidationError, "future evidence"):
            analyze_swing_profiles_over_history(
                self.market(1),
                forged_evaluator,
            )

    def test_appending_future_candles_does_not_change_earlier_points(self):
        base = analyze_swing_profiles_over_history(
            self.market(5),
            self.evaluator,
        )
        extended = analyze_swing_profiles_over_history(
            self.market(8),
            self.evaluator,
        )

        self.assertEqual(extended.points[:5], base.points)

    def test_rejects_unordered_duplicate_or_post_retrieval_candles(self):
        candles = self.market(3).candles
        cases = (
            [candles[1], candles[0]],
            [candles[0], candles[0]],
            [
                candles[0],
                candles[1].model_copy(
                    update={
                        "timestamp": self.retrieved_at + timedelta(days=1)
                    }
                ),
            ],
        )
        patterns = (
            "unique timestamps",
            "unique timestamps",
            "after source retrieval",
        )
        for candle_list, pattern in zip(cases, patterns):
            with self.subTest(pattern=pattern):
                series = self.market(candles=candle_list)
                with self.assertRaisesRegex(ValueError, pattern):
                    analyze_swing_profiles_over_history(
                        series,
                        self.evaluator,
                    )

    def test_rejects_invalid_schedule_settings(self):
        settings = (
            ({"warmup_candles": -1}, "warmup"),
            ({"warmup_candles": True}, "warmup"),
            ({"evaluation_stride": 0}, "stride"),
            ({"evaluation_stride": 1.5}, "stride"),
            ({"start_at": "2026-08-01"}, "datetime"),
            ({"start_at": datetime(2026, 8, 1)}, "timezone"),
            (
                {
                    "start_at": self.base_time + timedelta(days=3),
                    "end_at": self.base_time + timedelta(days=2),
                },
                "start cannot follow",
            ),
            (
                {"end_at": self.retrieved_at + timedelta(days=1)},
                "cannot follow source retrieval",
            ),
        )
        for setting, pattern in settings:
            with self.subTest(setting=setting):
                with self.assertRaisesRegex(ValueError, pattern):
                    analyze_swing_profiles_over_history(
                        self.market(1),
                        self.evaluator,
                        **setting,
                    )

    def test_rejects_non_callable_evaluator(self):
        with self.assertRaisesRegex(ValueError, "must be callable"):
            analyze_swing_profiles_over_history(
                self.market(1),
                None,
            )

    def test_records_explicit_or_inferred_evaluator_name(self):
        inferred = analyze_swing_profiles_over_history(
            self.market(1),
            self.evaluator,
        )
        explicit = analyze_swing_profiles_over_history(
            self.market(1),
            self.evaluator,
            evaluator_name="jarvis.swing.v1",
            analysis_version="2026.08",
        )

        self.assertIn("evaluator", inferred.evaluator_name)
        self.assertEqual(explicit.evaluator_name, "jarvis.swing.v1")
        self.assertEqual(explicit.analysis_version, "2026.08")

    def test_rejects_configuration_changes_inside_one_run(self):
        def changing_evaluator(series):
            weight = 1.0 if len(series.candles) == 1 else 2.0
            return self.profile_for(
                series,
                category_weights={SignalCategory.TREND: weight},
            )

        with self.assertRaisesRegex(ValueError, "fixed configuration"):
            analyze_swing_profiles_over_history(
                self.market(2),
                changing_evaluator,
            )

    def test_series_model_rejects_missing_scheduled_attempt(self):
        result = analyze_swing_profiles_over_history(
            self.market(3),
            self.evaluator,
        )
        values = result.model_dump(exclude_computed_fields=True)
        values["points"] = values["points"][:-1]

        with self.assertRaisesRegex(
            ValidationError,
            "one point or failure",
        ):
            HistoricalSwingProfileSeries.model_validate(values)

    def test_series_model_rejects_tampered_candle(self):
        result = analyze_swing_profiles_over_history(
            self.market(2),
            self.evaluator,
        )
        values = result.model_dump(exclude_computed_fields=True)
        values["points"][0]["candle"] = values["market_series"][
            "candles"
        ][1]

        with self.assertRaisesRegex(
            ValidationError,
            "evaluated at its candle",
        ):
            HistoricalSwingProfileSeries.model_validate(values)

    def test_stance_counts_are_dashboard_ready(self):
        result = analyze_swing_profiles_over_history(
            self.market(4),
            self.evaluator,
        )

        self.assertEqual(
            sum(result.stance_counts.values()),
            result.successful_evaluation_count,
        )
        self.assertEqual(result.stance_counts["strong_bullish_setup"], 2)
        self.assertEqual(result.stance_counts["strong_bearish_setup"], 2)


if __name__ == "__main__":
    unittest.main()
