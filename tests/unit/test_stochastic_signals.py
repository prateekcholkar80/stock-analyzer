import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.indicators import calculate_stochastic
from app.analytics.momentum_signals import (
    generate_stochastic_momentum_signal,
)
from app.exceptions import InsufficientDataError
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalProvenance,
    SignalStrength,
)
from app.models.technical import (
    IndicatorBundle,
    IndicatorComponent,
    IndicatorPoint,
    PriceField,
)


class StochasticMomentumSignalTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.timestamps = [
            self.first_timestamp + timedelta(days=day)
            for day in range(3)
        ]

    def component(self, name, values, timestamps=None):
        return IndicatorComponent(
            name=name,
            points=[
                IndicatorPoint(timestamp=timestamp, value=value)
                for timestamp, value in zip(
                    timestamps or self.timestamps[:len(values)],
                    values,
                )
            ],
        )

    def build_bundle(
        self,
        percent_k=(45.0, 40.0, 55.0),
        percent_d=(50.0, 45.0, 50.0),
        *,
        component_names=("percent_k", "percent_d"),
        **overrides,
    ):
        values_by_name = {
            "percent_k": percent_k,
            "percent_d": percent_d,
        }
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "indicator": "STOCH",
            "input_fields": (
                PriceField.HIGH,
                PriceField.LOW,
                PriceField.CLOSE,
            ),
            "parameters": {
                "fast_k_period": 14,
                "slow_k_period": 3,
                "slow_d_period": 3,
                "moving_average_type": "SMA",
            },
            "components": [
                self.component(
                    name,
                    values_by_name[name.casefold()],
                )
                for name in component_names
            ],
        }
        values.update(overrides)
        return IndicatorBundle(**values)

    def signal_for(self, percent_k, percent_d, **options):
        return generate_stochastic_momentum_signal(
            self.build_bundle(percent_k, percent_d),
            **options,
        )

    def test_generates_strong_bullish_crossover_from_oversold(self):
        evidence = self.signal_for((10.0, 18.0), (15.0, 12.0))

        self.assertEqual(evidence.category, SignalCategory.MOMENTUM)
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(
            evidence.evidence_id,
            "stochastic_momentum.stoch14_3_3.hlc",
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "bullish crossover from oversold",
        )
        self.assertEqual(evidence.observed_values["zone"], "oversold")
        self.assertEqual(
            evidence.observed_values["crossover"],
            "bullish",
        )
        self.assertTrue(
            evidence.observed_values["crossover_confirmed"]
        )
        self.assertEqual(evidence.observed_at, self.timestamps[1])
        self.assertEqual(evidence.available_at, self.timestamps[1])
        self.assertIn("current completed point", evidence.explanation)

    def test_generates_strong_bearish_crossover_from_overbought(self):
        evidence = self.signal_for((90.0, 82.0), (85.0, 88.0))

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bearish crossover from overbought",
        )
        self.assertEqual(evidence.observed_values["zone"], "overbought")

    def test_crossovers_outside_extreme_zones_are_moderate(self):
        cases = (
            (
                (40.0, 55.0),
                (45.0, 50.0),
                SignalDirection.BULLISH,
                "bullish crossover",
            ),
            (
                (60.0, 45.0),
                (55.0, 50.0),
                SignalDirection.BEARISH,
                "bearish crossover",
            ),
        )

        for percent_k, percent_d, direction, condition in cases:
            with self.subTest(condition=condition):
                evidence = self.signal_for(percent_k, percent_d)
                self.assertEqual(evidence.direction, direction)
                self.assertEqual(evidence.strength, SignalStrength.MODERATE)
                self.assertEqual(
                    evidence.observed_values["condition"],
                    condition,
                )

    def test_oversold_without_crossover_is_weak_context(self):
        evidence = self.signal_for((12.0, 10.0), (16.0, 15.0))

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["condition"],
            "oversold without bullish crossover",
        )
        self.assertFalse(
            evidence.observed_values["crossover_confirmed"]
        )
        self.assertIn(
            "no directional crossover is confirmed",
            evidence.explanation,
        )

    def test_overbought_without_crossover_is_weak_context(self):
        evidence = self.signal_for((88.0, 90.0), (84.0, 85.0))

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["condition"],
            "overbought without bearish crossover",
        )

    def test_middle_zone_without_crossover_is_neutral(self):
        evidence = self.signal_for((45.0, 48.0), (50.0, 49.0))

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(evidence.observed_values["zone"], "neutral")
        self.assertEqual(
            evidence.observed_values["condition"],
            "neutral without crossover",
        )

    def test_equal_previous_lines_can_confirm_fresh_crossover(self):
        cases = (
            (
                (50.0, 51.0),
                (50.0, 50.0),
                SignalDirection.BULLISH,
            ),
            (
                (50.0, 49.0),
                (50.0, 50.0),
                SignalDirection.BEARISH,
            ),
        )

        for percent_k, percent_d, direction in cases:
            with self.subTest(direction=direction):
                evidence = self.signal_for(percent_k, percent_d)
                self.assertEqual(evidence.direction, direction)
                self.assertTrue(
                    evidence.observed_values["crossover_confirmed"]
                )

    def test_equal_current_lines_do_not_confirm_crossover(self):
        evidence = self.signal_for((40.0, 50.0), (45.0, 50.0))

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertFalse(
            evidence.observed_values["crossover_confirmed"]
        )

    def test_crossover_tolerance_suppresses_small_line_jitter(self):
        evidence = self.signal_for(
            (50.0, 50.4),
            (50.0, 50.0),
            crossover_tolerance=0.5,
        )

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertFalse(
            evidence.observed_values["crossover_confirmed"]
        )
        self.assertEqual(
            evidence.parameters["crossover_tolerance"],
            0.5,
        )

    def test_zone_threshold_boundaries_are_inclusive(self):
        oversold = self.signal_for((19.0, 20.0), (20.0, 19.0))
        overbought = self.signal_for((81.0, 80.0), (80.0, 81.0))

        self.assertEqual(oversold.observed_values["zone"], "oversold")
        self.assertEqual(overbought.observed_values["zone"], "overbought")

    def test_supports_custom_zone_thresholds(self):
        evidence = self.signal_for(
            (30.0, 38.0),
            (35.0, 32.0),
            oversold_threshold=40.0,
            overbought_threshold=60.0,
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(evidence.parameters["oversold_threshold"], 40.0)
        self.assertEqual(
            evidence.parameters["overbought_threshold"],
            60.0,
        )

    def test_single_point_emits_zone_context_without_fake_crossover(self):
        evidence = self.signal_for((15.0,), (10.0,))

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertNotIn(
            "previous_percent_k",
            evidence.observed_values,
        )
        self.assertFalse(
            evidence.observed_values["crossover_confirmed"]
        )

    def test_uses_latest_available_component_values(self):
        evidence = self.signal_for(
            (10.0, 40.0, 55.0),
            (15.0, 45.0, 50.0),
        )

        self.assertEqual(evidence.observed_at, self.timestamps[-1])
        self.assertEqual(evidence.observed_values["percent_k"], 55.0)
        self.assertEqual(evidence.observed_values["percent_d"], 50.0)

    def test_as_of_excludes_future_components_and_uses_prior_pair(self):
        bundle = self.build_bundle(
            percent_k=(10.0, 18.0, 90.0),
            percent_d=(15.0, 12.0, 85.0),
        )

        evidence = generate_stochastic_momentum_signal(
            bundle,
            as_of=self.timestamps[1],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[1])
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)

    def test_as_of_first_point_does_not_use_unavailable_history(self):
        bundle = self.build_bundle(
            percent_k=(10.0, 18.0, 90.0),
            percent_d=(15.0, 12.0, 85.0),
        )

        evidence = generate_stochastic_momentum_signal(
            bundle,
            as_of=self.timestamps[0],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[0])
        self.assertFalse(
            evidence.observed_values["crossover_confirmed"]
        )
        self.assertNotIn(
            "previous_percent_k",
            evidence.observed_values,
        )

    def test_as_of_ignores_invalid_unused_future_value(self):
        bundle = self.build_bundle()
        percent_k = bundle.components[0]
        points = list(percent_k.points)
        points[-1] = points[-1].model_copy(update={"value": 101.0})
        invalid_future = percent_k.model_copy(update={"points": points})
        invalid = bundle.model_copy(
            update={"components": [invalid_future, bundle.components[1]]}
        )

        evidence = generate_stochastic_momentum_signal(
            invalid,
            as_of=self.timestamps[1],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[1])

    def test_as_of_after_latest_point_uses_latest_available_data(self):
        bundle = self.build_bundle()

        evidence = generate_stochastic_momentum_signal(
            bundle,
            as_of=self.timestamps[-1] + timedelta(days=10),
        )

        self.assertEqual(evidence.observed_at, self.timestamps[-1])

    def test_rejects_as_of_before_first_component_point(self):
        bundle = self.build_bundle()

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires an available indicator point",
        ):
            generate_stochastic_momentum_signal(
                bundle,
                as_of=self.first_timestamp - timedelta(seconds=1),
            )

    def test_rejects_invalid_as_of_time(self):
        bundle = self.build_bundle()

        for as_of in (datetime(2026, 8, 1), "2026-08-01", 1):
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    generate_stochastic_momentum_signal(
                        bundle,
                        as_of=as_of,
                    )

    def test_rejects_non_stochastic_indicator(self):
        bundle = self.build_bundle(indicator="ADX")

        with self.assertRaisesRegex(
            ValueError,
            "requires a STOCH indicator bundle",
        ):
            generate_stochastic_momentum_signal(bundle)

    def test_accepts_case_insensitive_indicator_and_component_names(self):
        bundle = self.build_bundle(
            indicator="stoch",
            component_names=("Percent_K", "Percent_D"),
        )

        evidence = generate_stochastic_momentum_signal(bundle)

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)

    def test_rejects_incorrect_indicator_inputs(self):
        bundle = self.build_bundle(
            input_fields=(PriceField.HIGH, PriceField.LOW),
        )

        with self.assertRaisesRegex(
            ValueError,
            "requires high, low, and close inputs",
        ):
            generate_stochastic_momentum_signal(bundle)

    def test_rejects_missing_extra_or_duplicate_components(self):
        bundle = self.build_bundle()
        invalid_components = (
            [bundle.components[0]],
            bundle.components
            + [self.component("extra", (1.0, 1.0, 1.0))],
            bundle.components
            + [self.component("PERCENT_K", (1.0, 1.0, 1.0))],
        )

        for components in invalid_components:
            with self.subTest(component_count=len(components)):
                invalid = bundle.model_copy(
                    update={"components": components}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "requires percent_k and percent_d components",
                ):
                    generate_stochastic_momentum_signal(invalid)

    def test_rejects_components_with_mismatched_timestamps(self):
        bundle = self.build_bundle()
        shifted = self.component(
            "percent_d",
            (50.0, 45.0, 50.0),
            timestamps=[
                timestamp + timedelta(hours=1)
                for timestamp in self.timestamps
            ],
        )
        invalid = bundle.model_copy(
            update={"components": [bundle.components[0], shifted]}
        )

        with self.assertRaisesRegex(
            ValueError,
            "components must use identical timestamps",
        ):
            generate_stochastic_momentum_signal(invalid)

    def test_rejects_missing_or_invalid_periods(self):
        bundle = self.build_bundle()
        period_names = (
            "fast_k_period",
            "slow_k_period",
            "slow_d_period",
        )
        invalid_values = (None, True, 3.0, "3", 1, 0)

        for name in period_names:
            for value in invalid_values:
                with self.subTest(name=name, value=value):
                    parameters = dict(bundle.parameters)
                    if value is None:
                        parameters.pop(name)
                    else:
                        parameters[name] = value
                    invalid = bundle.model_copy(
                        update={"parameters": parameters}
                    )
                    with self.assertRaises(ValueError):
                        generate_stochastic_momentum_signal(invalid)

    def test_rejects_non_sma_smoothing_metadata(self):
        bundle = self.build_bundle()

        for moving_average_type in (None, "EMA", 0):
            with self.subTest(moving_average_type=moving_average_type):
                parameters = dict(bundle.parameters)
                if moving_average_type is None:
                    parameters.pop("moving_average_type")
                else:
                    parameters["moving_average_type"] = (
                        moving_average_type
                    )
                invalid = bundle.model_copy(
                    update={"parameters": parameters}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "requires SMA-smoothed components",
                ):
                    generate_stochastic_momentum_signal(invalid)

    def test_rejects_invalid_zone_threshold_values(self):
        bundle = self.build_bundle()
        invalid_values = (
            True,
            "20",
            None,
            -0.1,
            100.1,
            float("nan"),
            float("inf"),
        )

        for name in ("oversold_threshold", "overbought_threshold"):
            for value in invalid_values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        generate_stochastic_momentum_signal(
                            bundle,
                            **{name: value},
                        )

    def test_rejects_equal_or_reversed_zone_thresholds(self):
        bundle = self.build_bundle()

        for oversold, overbought in ((20.0, 20.0), (80.0, 20.0)):
            with self.subTest(
                oversold=oversold,
                overbought=overbought,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "oversold threshold must be below overbought",
                ):
                    generate_stochastic_momentum_signal(
                        bundle,
                        oversold_threshold=oversold,
                        overbought_threshold=overbought,
                    )

    def test_rejects_invalid_crossover_tolerance(self):
        bundle = self.build_bundle()
        invalid_values = (
            True,
            "0.1",
            None,
            -0.1,
            100.1,
            float("nan"),
            float("inf"),
        )

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    generate_stochastic_momentum_signal(
                        bundle,
                        crossover_tolerance=value,
                    )

    def test_rejects_selected_current_or_previous_values_outside_range(self):
        bundle = self.build_bundle()

        for component_index in (0, 1):
            for point_index in (1, 2):
                for value in (-0.1, 100.1):
                    with self.subTest(
                        component_index=component_index,
                        point_index=point_index,
                        value=value,
                    ):
                        component = bundle.components[component_index]
                        points = list(component.points)
                        points[point_index] = points[point_index].model_copy(
                            update={"value": value}
                        )
                        invalid_component = component.model_copy(
                            update={"points": points}
                        )
                        components = list(bundle.components)
                        components[component_index] = invalid_component
                        invalid = bundle.model_copy(
                            update={"components": components}
                        )
                        with self.assertRaisesRegex(
                            ValueError,
                            "must be between 0 and 100",
                        ):
                            generate_stochastic_momentum_signal(invalid)

    def test_accepts_actual_talib_stochastic_output(self):
        close_values = [
            100 + index * 0.4 + ((index % 6) - 2.5) * 1.1
            for index in range(36)
        ]
        candles = [
            Candle(
                timestamp=self.first_timestamp + timedelta(days=index),
                open=close,
                high=close + 1.0 + (index % 4) * 0.15,
                low=close - 0.9 - (index % 3) * 0.2,
                close=close,
                volume=1_000 + index,
            )
            for index, close in enumerate(close_values)
        ]
        market_series = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=candles,
            retrieved_at=candles[-1].timestamp,
        )
        stochastic = calculate_stochastic(market_series)

        evidence = generate_stochastic_momentum_signal(stochastic)

        component_values = {
            component.name: component.points[-1].value
            for component in stochastic.components
        }
        self.assertEqual(evidence.observed_at, candles[-1].timestamp)
        self.assertAlmostEqual(
            evidence.observed_values["percent_k"],
            component_values["percent_k"],
        )
        self.assertAlmostEqual(
            evidence.observed_values["percent_d"],
            component_values["percent_d"],
        )
        self.assertEqual(evidence.parameters["fast_k_period"], 14)
        self.assertEqual(evidence.parameters["slow_k_period"], 3)
        self.assertEqual(evidence.parameters["slow_d_period"], 3)


if __name__ == "__main__":
    unittest.main()
