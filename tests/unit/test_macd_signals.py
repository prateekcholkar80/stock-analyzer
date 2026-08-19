import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.indicators import calculate_macd
from app.analytics.momentum_signals import generate_macd_momentum_signal
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


class MovingAverageConvergenceDivergenceSignalTests(unittest.TestCase):
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
        histograms=(-0.1, 0.05, 0.2),
        *,
        signal_values=None,
        component_names=("macd", "signal", "histogram"),
        **overrides,
    ):
        signals = signal_values or tuple(1.0 for _ in histograms)
        macd_values = tuple(
            signal + histogram
            for signal, histogram in zip(signals, histograms)
        )
        values_by_name = {
            "macd": macd_values,
            "signal": signals,
            "histogram": histograms,
        }
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "indicator": "MACD",
            "price_field": PriceField.CLOSE,
            "parameters": {
                "fast_period": 12,
                "slow_period": 26,
                "signal_period": 9,
            },
            "components": [
                self.component(name, values_by_name[name.casefold()])
                for name in component_names
            ],
        }
        values.update(overrides)
        return IndicatorBundle(**values)

    def signal_for(self, histograms, **options):
        return generate_macd_momentum_signal(
            self.build_bundle(histograms),
            **options,
        )

    def test_generates_strong_bullish_expanding_momentum(self):
        evidence = self.signal_for((0.05, 0.1, 0.2))

        self.assertEqual(evidence.category, SignalCategory.MOMENTUM)
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(
            evidence.evidence_id,
            "macd_momentum.macd12_26_9.close",
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "bullish momentum expanding",
        )
        self.assertAlmostEqual(evidence.observed_values["macd"], 1.2)
        self.assertEqual(evidence.observed_values["signal"], 1.0)
        self.assertEqual(evidence.observed_values["histogram"], 0.2)
        self.assertEqual(evidence.observed_values["histogram_change"], 0.1)
        self.assertEqual(evidence.observed_at, self.timestamps[-1])
        self.assertEqual(evidence.available_at, self.timestamps[-1])

    def test_generates_strong_bearish_expanding_momentum(self):
        evidence = self.signal_for((-0.05, -0.1, -0.2))

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bearish momentum expanding",
        )

    def test_fresh_direction_change_is_a_strong_crossover(self):
        expectations = (
            (
                (-0.1, 0.1),
                SignalDirection.BULLISH,
                "bullish crossover",
            ),
            (
                (0.1, -0.1),
                SignalDirection.BEARISH,
                "bearish crossover",
            ),
            (
                (0.0, 0.1),
                SignalDirection.BULLISH,
                "bullish crossover",
            ),
        )

        for histograms, direction, condition in expectations:
            with self.subTest(histograms=histograms):
                evidence = self.signal_for(histograms)
                self.assertEqual(evidence.direction, direction)
                self.assertEqual(evidence.strength, SignalStrength.STRONG)
                self.assertEqual(
                    evidence.observed_values["condition"],
                    condition,
                )

    def test_contracting_momentum_is_weak(self):
        expectations = (
            (
                (0.2, 0.1),
                SignalDirection.BULLISH,
                "bullish momentum contracting",
            ),
            (
                (-0.2, -0.1),
                SignalDirection.BEARISH,
                "bearish momentum contracting",
            ),
        )

        for histograms, direction, condition in expectations:
            with self.subTest(histograms=histograms):
                evidence = self.signal_for(histograms)
                self.assertEqual(evidence.direction, direction)
                self.assertEqual(evidence.strength, SignalStrength.WEAK)
                self.assertEqual(
                    evidence.observed_values["condition"],
                    condition,
                )

    def test_stable_directional_momentum_is_moderate(self):
        for histograms, direction in (
            ((0.1, 0.1), SignalDirection.BULLISH),
            ((-0.1, -0.1), SignalDirection.BEARISH),
        ):
            with self.subTest(histograms=histograms):
                evidence = self.signal_for(histograms)
                self.assertEqual(evidence.direction, direction)
                self.assertEqual(evidence.strength, SignalStrength.MODERATE)
                self.assertIn(
                    "momentum stable",
                    evidence.observed_values["condition"],
                )

    def test_single_directional_point_has_moderate_strength(self):
        evidence = self.signal_for((0.1,))

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bullish alignment",
        )
        self.assertNotIn("previous_histogram", evidence.observed_values)
        self.assertNotIn("histogram_change", evidence.observed_values)

    def test_zero_and_tolerance_boundaries_are_neutral(self):
        for histogram in (-0.01, 0.0, 0.01):
            with self.subTest(histogram=histogram):
                evidence = self.signal_for(
                    (histogram,),
                    histogram_zero_tolerance=0.01,
                )
                self.assertEqual(
                    evidence.direction,
                    SignalDirection.NEUTRAL,
                )
                self.assertEqual(evidence.strength, SignalStrength.WEAK)
                self.assertEqual(
                    evidence.observed_values["condition"],
                    "neutral alignment",
                )

    def test_custom_tolerance_controls_direction_and_parameters(self):
        evidence = self.signal_for(
            (0.005,),
            histogram_zero_tolerance=0.01,
        )

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(
            evidence.parameters["histogram_zero_tolerance"],
            0.01,
        )

    def test_uses_latest_available_component_point(self):
        evidence = self.signal_for((-0.1, 0.05, 0.2))

        self.assertEqual(evidence.observed_at, self.timestamps[-1])
        self.assertEqual(evidence.observed_values["histogram"], 0.2)

    def test_as_of_excludes_later_component_points(self):
        bundle = self.build_bundle((-0.1, 0.05, 0.2))

        evidence = generate_macd_momentum_signal(
            bundle,
            as_of=self.timestamps[1],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[1])
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bullish crossover",
        )

    def test_as_of_does_not_validate_unused_future_histogram(self):
        bundle = self.build_bundle((-0.1, 0.05, 0.2))
        histogram = bundle.components[2]
        points = list(histogram.points)
        points[2] = points[2].model_copy(update={"value": 0.9})
        invalid_future = histogram.model_copy(update={"points": points})
        bundle = bundle.model_copy(
            update={
                "components": [
                    bundle.components[0],
                    bundle.components[1],
                    invalid_future,
                ]
            }
        )

        evidence = generate_macd_momentum_signal(
            bundle,
            as_of=self.timestamps[1],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[1])
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)

    def test_as_of_after_latest_point_uses_latest_available_data(self):
        bundle = self.build_bundle((-0.1, 0.05, 0.2))

        evidence = generate_macd_momentum_signal(
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
            generate_macd_momentum_signal(
                bundle,
                as_of=self.first_timestamp - timedelta(seconds=1),
            )

    def test_rejects_invalid_as_of_time(self):
        bundle = self.build_bundle()

        for as_of in (datetime(2026, 8, 1), "2026-08-01", 1):
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    generate_macd_momentum_signal(bundle, as_of=as_of)

    def test_rejects_non_macd_indicator(self):
        bundle = self.build_bundle(indicator="BBANDS")

        with self.assertRaisesRegex(
            ValueError,
            "requires a MACD indicator bundle",
        ):
            generate_macd_momentum_signal(bundle)

    def test_accepts_case_insensitive_indicator_and_component_names(self):
        bundle = self.build_bundle(
            indicator="macd",
            component_names=("MACD", "Signal", "Histogram"),
        )

        evidence = generate_macd_momentum_signal(bundle)

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)

    def test_rejects_multi_field_bundle_disguised_as_macd(self):
        bundle = self.build_bundle(
            price_field=None,
            input_fields=(PriceField.HIGH, PriceField.LOW),
        )

        with self.assertRaisesRegex(
            ValueError,
            "requires one price field",
        ):
            generate_macd_momentum_signal(bundle)

    def test_rejects_missing_or_extra_components(self):
        invalid_names = (
            ("macd", "signal"),
            ("macd", "signal", "histogram", "extra"),
        )

        for names in invalid_names:
            with self.subTest(names=names):
                if "extra" in names:
                    bundle = self.build_bundle()
                    extra = self.component("extra", (1.0, 1.0, 1.0))
                    bundle = bundle.model_copy(
                        update={"components": bundle.components + [extra]}
                    )
                else:
                    bundle = self.build_bundle(component_names=names)

                with self.assertRaisesRegex(
                    ValueError,
                    "requires macd, signal, and histogram components",
                ):
                    generate_macd_momentum_signal(bundle)

    def test_rejects_case_insensitive_duplicate_components(self):
        bundle = self.build_bundle()
        duplicate = self.component("MACD", (0.9, 1.05, 1.2))
        invalid = bundle.model_copy(
            update={"components": bundle.components + [duplicate]}
        )

        with self.assertRaisesRegex(
            ValueError,
            "requires macd, signal, and histogram components",
        ):
            generate_macd_momentum_signal(invalid)

    def test_rejects_components_with_mismatched_timestamps(self):
        bundle = self.build_bundle()
        shifted = self.component(
            "signal",
            (1.0, 1.0, 1.0),
            timestamps=[
                timestamp + timedelta(hours=1)
                for timestamp in self.timestamps
            ],
        )
        components = [bundle.components[0], shifted, bundle.components[2]]
        invalid = bundle.model_copy(update={"components": components})

        with self.assertRaisesRegex(
            ValueError,
            "components must use identical timestamps",
        ):
            generate_macd_momentum_signal(invalid)

    def test_rejects_missing_or_invalid_periods(self):
        bundle = self.build_bundle()
        period_names = ("fast_period", "slow_period", "signal_period")
        invalid_values = (None, True, 12.0, "12", 1, 0)

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
                        generate_macd_momentum_signal(invalid)

    def test_rejects_fast_period_not_below_slow_period(self):
        bundle = self.build_bundle()

        for fast_period in (26, 27):
            with self.subTest(fast_period=fast_period):
                parameters = dict(bundle.parameters)
                parameters["fast_period"] = fast_period
                invalid = bundle.model_copy(
                    update={"parameters": parameters}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "fast period must be below slow period",
                ):
                    generate_macd_momentum_signal(invalid)

    def test_rejects_inconsistent_current_or_previous_histogram(self):
        bundle = self.build_bundle((0.1, 0.2))

        for index in (0, 1):
            with self.subTest(index=index):
                histogram = bundle.components[2]
                points = list(histogram.points)
                points[index] = points[index].model_copy(
                    update={"value": 0.9}
                )
                invalid_histogram = histogram.model_copy(
                    update={"points": points}
                )
                invalid = bundle.model_copy(
                    update={
                        "components": [
                            bundle.components[0],
                            bundle.components[1],
                            invalid_histogram,
                        ]
                    }
                )

                with self.assertRaisesRegex(
                    ValueError,
                    "histogram must equal",
                ):
                    generate_macd_momentum_signal(invalid)

    def test_rejects_invalid_histogram_tolerance(self):
        bundle = self.build_bundle()
        invalid_values = (
            True,
            "0.1",
            None,
            -0.1,
            float("nan"),
            float("inf"),
        )

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    generate_macd_momentum_signal(
                        bundle,
                        histogram_zero_tolerance=value,
                    )

    def test_accepts_actual_talib_macd_output(self):
        close_values = [
            100 + index * 0.5 + ((index % 5) - 2) * 1.2
            for index in range(40)
        ]
        candles = [
            Candle(
                timestamp=self.first_timestamp + timedelta(days=index),
                open=close,
                high=close + 0.5,
                low=close - 0.5,
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
        macd = calculate_macd(market_series)

        evidence = generate_macd_momentum_signal(macd)

        self.assertEqual(evidence.observed_at, candles[-1].timestamp)
        self.assertEqual(evidence.parameters["fast_period"], 12)
        self.assertEqual(evidence.parameters["slow_period"], 26)
        self.assertEqual(evidence.parameters["signal_period"], 9)
        self.assertAlmostEqual(
            evidence.observed_values["histogram"],
            evidence.observed_values["macd"]
            - evidence.observed_values["signal"],
        )


if __name__ == "__main__":
    unittest.main()
