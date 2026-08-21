import unittest
from datetime import UTC, datetime, timedelta
from math import sin
from unittest.mock import patch

from pydantic import ValidationError

from app.analytics.swing_evaluator import (
    UnifiedSwingEvaluator,
    UnifiedSwingEvaluatorConfig,
    analyze_unified_swing_profiles_over_history,
    evaluate_unified_swing_profile,
)
from app.exceptions import (
    IndicatorCalculationError,
    InsufficientDataError,
)
from app.models.historical_analysis import (
    HistoricalAnalysisFailureKind,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import SignalCategory


class UnifiedSwingEvaluatorTests(unittest.TestCase):
    def setUp(self):
        self.base_time = datetime(2026, 1, 1, tzinfo=UTC)
        self.retrieved_at = self.base_time + timedelta(days=100)

    def market(self, count=60, **overrides):
        candles = []
        for index in range(count):
            close = 100 + index * 0.3 + sin(index / 2)
            candles.append(
                Candle(
                    timestamp=self.base_time + timedelta(days=index),
                    open=close - 0.2,
                    high=close + 1.0,
                    low=close - 1.0,
                    close=close,
                    volume=100_000 + index * 1_000,
                )
            )
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "candles": candles,
            "retrieved_at": self.retrieved_at,
            "source": "test_market",
        }
        values.update(overrides)
        return HistoricalCandleSeries(**values)

    def test_default_configuration_has_a_deterministic_warmup(self):
        config = UnifiedSwingEvaluatorConfig()

        self.assertEqual(config.minimum_candles, 50)
        self.assertEqual(
            config.category_weights,
            {
                SignalCategory.TREND: 1.25,
                SignalCategory.MOMENTUM: 1.0,
                SignalCategory.VOLATILITY: 0.75,
                SignalCategory.VOLUME: 1.0,
                SignalCategory.PRICE_ACTION: 1.5,
                SignalCategory.CANDLESTICK: 1.0,
            },
        )

    def test_calculates_every_indicator_and_price_action_evidence(self):
        series = self.market()

        profile = evaluate_unified_swing_profile(series)

        self.assertEqual(
            profile.snapshot.evaluated_at,
            series.candles[-1].timestamp,
        )
        self.assertEqual(len(profile.snapshot.evidence), 12)
        self.assertEqual(profile.coverage_percentage, 100)
        self.assertEqual(
            {item.category for item in profile.snapshot.evidence},
            set(SignalCategory),
        )
        self.assertEqual(
            {item.source for item in profile.snapshot.evidence},
            {
                "trend_signals.moving_average_alignment",
                "trend_signals.adx_directional_strength",
                "momentum_signals.rsi_mean_reversion",
                "momentum_signals.macd_momentum",
                "momentum_signals.stochastic_zone_crossover",
                "volatility_signals.bollinger_price_and_bandwidth",
                "volatility_signals.atr_regime_and_risk_distance",
                "volume_signals.obv_price_confirmation",
                "candlestick_signals.candlestick_pattern",
                "price_action_signals.fair_value_gap_context",
                "price_action_signals.support_resistance_lifecycle",
                "price_action_signals.market_structure",
            },
        )

    def test_all_evidence_is_synchronized_to_the_prefix_end(self):
        series = self.market()

        profile = evaluate_unified_swing_profile(series)

        expected_time = series.candles[-1].timestamp
        self.assertEqual(
            {item.available_at for item in profile.snapshot.evidence},
            {expected_time},
        )
        self.assertTrue(profile.synchronized_evidence_required)

    def test_indicator_and_price_action_parameters_are_auditable(self):
        config = UnifiedSwingEvaluatorConfig(
            fast_ema_period=10,
            slow_sma_period=30,
            pivot_left_strength=3,
            pivot_right_strength=2,
            support_resistance_tolerance_percentage=0.75,
            support_resistance_strong_touch_count=4,
            fvg_minimum_gap_percentage=0.2,
            fvg_minimum_atr_multiple=0.25,
            fvg_max_age_candles=15,
        )

        profile = evaluate_unified_swing_profile(
            self.market(),
            config=config,
        )
        by_source = {
            item.source: item
            for item in profile.snapshot.evidence
        }

        moving_average = by_source[
            "trend_signals.moving_average_alignment"
        ]
        self.assertEqual(moving_average.observed_values["fast_period"], 10)
        self.assertEqual(moving_average.observed_values["slow_period"], 30)
        self.assertEqual(
            moving_average.observed_values["fast_indicator"],
            "EMA",
        )
        self.assertEqual(
            moving_average.observed_values["slow_indicator"],
            "SMA",
        )

        fair_value_gap = by_source[
            "price_action_signals.fair_value_gap_context"
        ]
        self.assertEqual(
            fair_value_gap.parameters["minimum_gap_percentage"],
            0.2,
        )
        self.assertEqual(
            fair_value_gap.parameters["minimum_atr_multiple"],
            0.25,
        )
        self.assertEqual(fair_value_gap.parameters["max_age_candles"], 15)

        support_resistance = by_source[
            "price_action_signals.support_resistance_lifecycle"
        ]
        self.assertEqual(
            support_resistance.parameters["zone_tolerance_percentage"],
            0.75,
        )
        self.assertEqual(
            support_resistance.parameters["pivot_left_strength"],
            3,
        )
        self.assertEqual(
            support_resistance.parameters["strong_touch_count"],
            4,
        )

    def test_custom_profile_weights_and_thresholds_are_applied(self):
        config = UnifiedSwingEvaluatorConfig(
            trend_weight=2.0,
            momentum_weight=1.5,
            volatility_weight=1.0,
            volume_weight=1.25,
            price_action_weight=2.5,
            minimum_coverage_percentage=75.0,
            directional_threshold=25.0,
            strong_threshold=70.0,
        )

        profile = evaluate_unified_swing_profile(
            self.market(),
            config=config,
        )

        self.assertEqual(profile.category_weights["trend"], 2.0)
        self.assertEqual(profile.category_weights["price_action"], 2.5)
        self.assertEqual(profile.minimum_coverage_percentage, 75.0)
        self.assertEqual(profile.directional_threshold, 25.0)
        self.assertEqual(profile.strong_threshold, 70.0)

    def test_rejects_insufficient_candles_before_calculation(self):
        with self.assertRaisesRegex(
            InsufficientDataError,
            "at least 50 candles; received 49",
        ):
            evaluate_unified_swing_profile(self.market(49))

    def test_rejects_unordered_or_post_retrieval_candles(self):
        candles = self.market().candles
        unordered = list(candles)
        unordered[10], unordered[11] = unordered[11], unordered[10]
        after_retrieval = list(candles)
        after_retrieval[-1] = after_retrieval[-1].model_copy(
            update={"timestamp": self.retrieved_at + timedelta(days=1)}
        )

        cases = (
            (unordered, "unique timestamps"),
            (after_retrieval, "after source retrieval"),
        )
        for candle_list, pattern in cases:
            with self.subTest(pattern=pattern):
                with self.assertRaisesRegex(ValueError, pattern):
                    evaluate_unified_swing_profile(
                        self.market(candles=candle_list)
                    )

    def test_config_rejects_invalid_relationships_and_values(self):
        cases = (
            ({"fast_ema_period": 50}, "fast EMA"),
            ({"macd_fast_period": 26}, "MACD fast"),
            ({"strong_threshold": 20.0}, "strong threshold"),
            (
                {"support_resistance_minimum_touches": 4},
                "strong zone touch count",
            ),
            ({"pivot_left_strength": 0}, "greater than or equal"),
            ({"fast_ema_period": True}, "valid integer"),
            ({"trend_weight": float("nan")}, "finite number"),
            ({"evaluator_id": "invalid id"}, "string_pattern_mismatch"),
        )
        for values, pattern in cases:
            with self.subTest(values=values):
                with self.assertRaisesRegex(ValidationError, pattern):
                    UnifiedSwingEvaluatorConfig(**values)

    def test_rejects_unvalidated_config_object(self):
        with self.assertRaisesRegex(ValueError, "validated evaluator"):
            UnifiedSwingEvaluator({"fast_ema_period": 10})

    def test_configuration_is_immutable(self):
        config = UnifiedSwingEvaluatorConfig()

        with self.assertRaises(ValidationError):
            config.fast_ema_period = 10

    def test_history_wrapper_uses_automatic_minimum_warmup(self):
        result = analyze_unified_swing_profiles_over_history(
            self.market(54)
        )

        self.assertEqual(result.analysis_version, "jarvis.unified_swing.v1")
        self.assertEqual(
            result.evaluator_name,
            "jarvis.agent_orchestrator.v1",
        )
        self.assertEqual(result.warmup_candles, 49)
        self.assertEqual(
            [point.candle_index for point in result.points],
            [49, 50, 51, 52, 53],
        )
        self.assertEqual(result.failed_evaluation_count, 0)

    def test_explicit_early_history_records_warmup_failures(self):
        result = analyze_unified_swing_profiles_over_history(
            self.market(51),
            warmup_candles=47,
        )

        self.assertEqual(
            [failure.candle_index for failure in result.failures],
            [47, 48],
        )
        self.assertTrue(
            all(
                failure.kind
                is HistoricalAnalysisFailureKind.INSUFFICIENT_DATA
                for failure in result.failures
            )
        )
        self.assertEqual(
            [point.candle_index for point in result.points],
            [49, 50],
        )

    def test_indicator_failure_is_recorded_by_historical_engine(self):
        with patch(
            "app.analytics.swing_evaluator.calculate_rsi",
            side_effect=IndicatorCalculationError("RSI unavailable"),
        ):
            result = analyze_unified_swing_profiles_over_history(
                self.market(50)
            )

        self.assertEqual(result.successful_evaluation_count, 0)
        self.assertEqual(result.failed_evaluation_count, 1)
        self.assertEqual(
            result.failures[0].kind,
            HistoricalAnalysisFailureKind.INDICATOR_CALCULATION,
        )
        self.assertEqual(result.failures[0].message, "RSI unavailable")

    def test_appended_future_candles_do_not_change_prior_profiles(self):
        full_market = self.market(55)
        base_market = full_market.model_copy(
            update={"candles": full_market.candles[:52]}
        )

        base = analyze_unified_swing_profiles_over_history(base_market)
        extended = analyze_unified_swing_profiles_over_history(full_market)

        self.assertEqual(extended.points[:3], base.points)


if __name__ == "__main__":
    unittest.main()
