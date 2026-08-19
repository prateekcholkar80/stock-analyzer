import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.indicators import calculate_bollinger_bands
from app.analytics.volatility_signals import (
    generate_bollinger_band_signal,
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


class BollingerBandSignalTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.timestamps = [
            self.first_timestamp + timedelta(days=day)
            for day in range(6)
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
        upper=(110.0, 109.0, 108.0, 107.0, 106.0, 105.0),
        middle=(100.0,) * 6,
        lower=(90.0, 91.0, 92.0, 93.0, 94.0, 95.0),
        *,
        component_names=("upper_band", "middle_band", "lower_band"),
        **overrides,
    ):
        values_by_name = {
            "upper_band": upper,
            "middle_band": middle,
            "lower_band": lower,
        }
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "indicator": "BBANDS",
            "price_field": PriceField.CLOSE,
            "parameters": {
                "period": 20,
                "upper_deviation": 2.0,
                "lower_deviation": 2.0,
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

    def build_market_series(
        self,
        closes=(100.0,) * 6,
        timestamps=None,
        **overrides,
    ):
        candle_timestamps = timestamps or self.timestamps[:len(closes)]
        candles = [
            Candle(
                timestamp=timestamp,
                open=close,
                high=close + 1.0,
                low=max(close - 1.0, 0.0),
                close=close,
                volume=1_000 + index,
            )
            for index, (timestamp, close) in enumerate(
                zip(candle_timestamps, closes)
            )
        ]
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "candles": candles,
            "retrieved_at": self.timestamps[-1],
        }
        values.update(overrides)
        return HistoricalCandleSeries(**values)

    def signal_for(self, bundle=None, market_series=None, **options):
        return generate_bollinger_band_signal(
            bundle or self.build_bundle(),
            market_series or self.build_market_series(),
            **options,
        )

    def two_point_inputs(self, current_price):
        bundle = self.build_bundle(
            upper=(105.0, 110.0),
            middle=(100.0, 100.0),
            lower=(95.0, 90.0),
        )
        series = self.build_market_series(
            closes=(100.0, current_price),
        )
        return bundle, series

    def test_generates_strong_bullish_expanding_breakout_evidence(self):
        bundle, series = self.two_point_inputs(111.0)

        evidence = self.signal_for(bundle, series)

        self.assertEqual(evidence.category, SignalCategory.VOLATILITY)
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(
            evidence.evidence_id,
            "bollinger_position.bbands20.close",
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "upper-band breakout with volatility expansion",
        )
        self.assertEqual(
            evidence.observed_values["band_position"],
            "above_upper_band",
        )
        self.assertEqual(
            evidence.observed_values["volatility_regime"],
            "expanding",
        )
        self.assertEqual(evidence.observed_at, self.timestamps[1])
        self.assertEqual(evidence.available_at, self.timestamps[1])
        self.assertEqual(evidence.observed_values["price"], 111.0)

    def test_generates_strong_bearish_expanding_breakdown_evidence(self):
        bundle, series = self.two_point_inputs(89.0)

        evidence = self.signal_for(bundle, series)

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["condition"],
            "lower-band breakdown with volatility expansion",
        )

    def test_breakout_without_expansion_is_moderate(self):
        bundle = self.build_bundle(
            upper=(110.0, 110.0),
            middle=(100.0, 100.0),
            lower=(90.0, 90.0),
        )
        cases = (
            (111.0, SignalDirection.BULLISH, "upper-band breakout"),
            (89.0, SignalDirection.BEARISH, "lower-band breakdown"),
        )

        for price, direction, condition in cases:
            with self.subTest(price=price):
                series = self.build_market_series(
                    closes=(100.0, price)
                )
                evidence = self.signal_for(bundle, series)
                self.assertEqual(evidence.direction, direction)
                self.assertEqual(evidence.strength, SignalStrength.MODERATE)
                self.assertEqual(
                    evidence.observed_values["condition"],
                    condition,
                )

    def test_exact_band_tests_are_weak_mean_reversion_context(self):
        bundle = self.build_bundle(
            upper=(110.0, 110.0),
            middle=(100.0, 100.0),
            lower=(90.0, 90.0),
        )
        cases = (
            (
                110.0,
                SignalDirection.BEARISH,
                "upper-band test without breakout",
            ),
            (
                90.0,
                SignalDirection.BULLISH,
                "lower-band test without breakdown",
            ),
        )

        for price, direction, condition in cases:
            with self.subTest(price=price):
                series = self.build_market_series(
                    closes=(100.0, price)
                )
                evidence = self.signal_for(bundle, series)
                self.assertEqual(evidence.direction, direction)
                self.assertEqual(evidence.strength, SignalStrength.WEAK)
                self.assertEqual(
                    evidence.observed_values["condition"],
                    condition,
                )

    def test_numerical_boundary_jitter_does_not_create_breakout(self):
        bundle = self.build_bundle(
            upper=(110.0,),
            middle=(100.0,),
            lower=(90.0,),
        )
        series = self.build_market_series(
            closes=(110.0 + 1e-11,)
        )

        evidence = self.signal_for(bundle, series)

        self.assertEqual(
            evidence.observed_values["band_position"],
            "at_upper_band",
        )
        self.assertEqual(evidence.direction, SignalDirection.BEARISH)

    def test_contained_price_without_squeeze_is_weak_neutral(self):
        bundle = self.build_bundle(
            upper=(110.0,) * 5,
            middle=(100.0,) * 5,
            lower=(90.0,) * 5,
        )
        series = self.build_market_series(closes=(100.0,) * 5)

        evidence = self.signal_for(bundle, series)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertFalse(evidence.observed_values["is_squeeze"])
        self.assertEqual(
            evidence.observed_values["condition"],
            "price contained within bands",
        )

    def test_low_relative_bandwidth_is_a_moderate_neutral_squeeze(self):
        evidence = self.signal_for()

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertTrue(evidence.observed_values["squeeze_evaluated"])
        self.assertTrue(evidence.observed_values["is_squeeze"])
        self.assertEqual(
            evidence.observed_values["condition"],
            "volatility squeeze without breakout",
        )
        self.assertIn("bandwidth squeeze is present", evidence.explanation)

    def test_insufficient_squeeze_history_does_not_invent_squeeze(self):
        bundle = self.build_bundle(
            upper=(110.0, 109.0),
            middle=(100.0, 100.0),
            lower=(90.0, 91.0),
        )
        series = self.build_market_series(closes=(100.0, 100.0))

        evidence = self.signal_for(bundle, series)

        self.assertFalse(evidence.observed_values["squeeze_evaluated"])
        self.assertFalse(evidence.observed_values["is_squeeze"])
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertIn("not evaluated", evidence.explanation)

    def test_bandwidth_regime_uses_configured_tolerance(self):
        bundle = self.build_bundle(
            upper=(105.0, 105.2),
            middle=(100.0, 100.0),
            lower=(95.0, 94.8),
        )
        series = self.build_market_series(closes=(100.0, 100.0))

        stable = self.signal_for(
            bundle,
            series,
            bandwidth_change_tolerance_percentage=5.0,
        )
        expanding = self.signal_for(
            bundle,
            series,
            bandwidth_change_tolerance_percentage=3.0,
        )

        self.assertEqual(
            stable.observed_values["volatility_regime"],
            "stable",
        )
        self.assertEqual(
            expanding.observed_values["volatility_regime"],
            "expanding",
        )

    def test_custom_squeeze_configuration_is_preserved(self):
        evidence = self.signal_for(
            squeeze_lookback=4,
            squeeze_percentile=25.0,
            minimum_squeeze_points=4,
        )

        self.assertEqual(evidence.parameters["squeeze_lookback"], 4)
        self.assertEqual(evidence.parameters["squeeze_percentile"], 25.0)
        self.assertEqual(
            evidence.parameters["minimum_squeeze_points"],
            4,
        )
        self.assertEqual(
            evidence.observed_values["squeeze_observation_count"],
            4,
        )

    def test_uses_bundle_price_field_to_select_candle_value(self):
        bundle = self.build_bundle(
            upper=(110.0,) * 6,
            middle=(100.0,) * 6,
            lower=(90.0,) * 6,
            price_field=PriceField.HIGH,
        )
        series = self.build_market_series()

        evidence = self.signal_for(bundle, series)

        self.assertEqual(evidence.observed_values["price"], 101.0)
        self.assertEqual(evidence.parameters["price_field"], "high")

    def test_as_of_excludes_future_bands_prices_and_squeeze_history(self):
        bundle = self.build_bundle(
            upper=(105.0, 110.0, 120.0),
            middle=(100.0, 100.0, 100.0),
            lower=(95.0, 90.0, 80.0),
        )
        series = self.build_market_series(
            closes=(100.0, 111.0, 79.0)
        )

        evidence = self.signal_for(
            bundle,
            series,
            as_of=self.timestamps[1],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[1])
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(
            evidence.observed_values["squeeze_observation_count"],
            2,
        )

    def test_as_of_ignores_invalid_unused_future_band(self):
        bundle = self.build_bundle(
            upper=(105.0, 110.0, 120.0),
            middle=(100.0, 100.0, 100.0),
            lower=(95.0, 90.0, 80.0),
        )
        upper = bundle.components[0]
        points = list(upper.points)
        points[-1] = points[-1].model_copy(
            update={"value": float("nan")}
        )
        invalid_upper = upper.model_copy(update={"points": points})
        invalid = bundle.model_copy(
            update={
                "components": [
                    invalid_upper,
                    bundle.components[1],
                    bundle.components[2],
                ]
            }
        )
        series = self.build_market_series(
            closes=(100.0, 111.0, 79.0)
        )

        evidence = self.signal_for(
            invalid,
            series,
            as_of=self.timestamps[1],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[1])

    def test_as_of_after_latest_point_uses_latest_available_data(self):
        evidence = self.signal_for(
            as_of=self.timestamps[-1] + timedelta(days=10)
        )

        self.assertEqual(evidence.observed_at, self.timestamps[-1])

    def test_rejects_as_of_before_first_band_point(self):
        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires an available band point",
        ):
            self.signal_for(
                as_of=self.first_timestamp - timedelta(seconds=1)
            )

    def test_rejects_invalid_as_of_time(self):
        for as_of in (datetime(2026, 8, 1), "2026-08-01", 1):
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    self.signal_for(as_of=as_of)

    def test_rejects_band_point_without_matching_candle(self):
        series = self.build_market_series(
            closes=(100.0,) * 5,
            timestamps=self.timestamps[:5],
        )

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires a candle matching the band point",
        ):
            self.signal_for(market_series=series)

    def test_rejects_mismatched_market_identity(self):
        bundle = self.build_bundle()

        for field_name, value in (
            ("exchange", "BSE"),
            ("symbol_token", "9999"),
            ("symbol", "TCS-EQ"),
            ("interval", "ONE_HOUR"),
        ):
            with self.subTest(field_name=field_name):
                series = self.build_market_series(
                    **{field_name: value}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "same instrument and timeframe",
                ):
                    self.signal_for(bundle, series)

    def test_rejects_unordered_or_duplicate_market_candles(self):
        bundle = self.build_bundle(
            upper=(110.0, 110.0),
            middle=(100.0, 100.0),
            lower=(90.0, 90.0),
        )
        invalid_timestamp_sets = (
            [self.timestamps[1], self.timestamps[0]],
            [self.timestamps[0], self.timestamps[0]],
        )

        for timestamps in invalid_timestamp_sets:
            with self.subTest(timestamps=timestamps):
                series = self.build_market_series(
                    closes=(100.0, 100.0),
                    timestamps=timestamps,
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "unique timestamps in ascending order",
                ):
                    self.signal_for(bundle, series)

    def test_rejects_non_bollinger_indicator(self):
        bundle = self.build_bundle(indicator="MACD")

        with self.assertRaisesRegex(
            ValueError,
            "requires a BBANDS indicator bundle",
        ):
            self.signal_for(bundle)

    def test_accepts_case_insensitive_indicator_and_component_names(self):
        bundle = self.build_bundle(
            indicator="bbands",
            component_names=("Upper_Band", "Middle_Band", "Lower_Band"),
        )

        evidence = self.signal_for(bundle)

        self.assertEqual(evidence.category, SignalCategory.VOLATILITY)

    def test_rejects_multi_field_bundle_disguised_as_bollinger(self):
        bundle = self.build_bundle(
            price_field=None,
            input_fields=(PriceField.HIGH, PriceField.LOW),
        )

        with self.assertRaisesRegex(
            ValueError,
            "requires one OHLC price field",
        ):
            self.signal_for(bundle)

    def test_rejects_volume_as_bollinger_price_position_input(self):
        bundle = self.build_bundle(price_field=PriceField.VOLUME)

        with self.assertRaisesRegex(
            ValueError,
            "requires one OHLC price field",
        ):
            self.signal_for(bundle)

    def test_rejects_missing_extra_or_duplicate_components(self):
        bundle = self.build_bundle()
        invalid_components = (
            bundle.components[:2],
            bundle.components
            + [self.component("extra", (1.0,) * 6)],
            bundle.components
            + [self.component("UPPER_BAND", (110.0,) * 6)],
        )

        for components in invalid_components:
            with self.subTest(component_count=len(components)):
                invalid = bundle.model_copy(
                    update={"components": components}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "requires upper_band, middle_band, and lower_band",
                ):
                    self.signal_for(invalid)

    def test_rejects_component_timestamp_mismatch(self):
        bundle = self.build_bundle()
        shifted = self.component(
            "middle_band",
            (100.0,) * 6,
            timestamps=[
                timestamp + timedelta(hours=1)
                for timestamp in self.timestamps
            ],
        )
        invalid = bundle.model_copy(
            update={
                "components": [
                    bundle.components[0],
                    shifted,
                    bundle.components[2],
                ]
            }
        )

        with self.assertRaisesRegex(
            ValueError,
            "components must use identical timestamps",
        ):
            self.signal_for(invalid)

    def test_rejects_missing_or_invalid_period(self):
        bundle = self.build_bundle()

        for value in (None, True, 20.0, "20", 1, 0):
            with self.subTest(value=value):
                parameters = dict(bundle.parameters)
                if value is None:
                    parameters.pop("period")
                else:
                    parameters["period"] = value
                invalid = bundle.model_copy(
                    update={"parameters": parameters}
                )
                with self.assertRaises(ValueError):
                    self.signal_for(invalid)

    def test_rejects_invalid_deviation_metadata(self):
        bundle = self.build_bundle()
        invalid_values = (
            None,
            True,
            "2",
            0,
            -1,
            float("nan"),
            float("inf"),
        )

        for name in ("upper_deviation", "lower_deviation"):
            for value in invalid_values:
                with self.subTest(name=name, value=value):
                    parameters = dict(bundle.parameters)
                    parameters[name] = value
                    invalid = bundle.model_copy(
                        update={"parameters": parameters}
                    )
                    with self.assertRaises(ValueError):
                        self.signal_for(invalid)

    def test_rejects_non_sma_band_metadata(self):
        bundle = self.build_bundle()

        for value in (None, "EMA", 0):
            with self.subTest(value=value):
                parameters = dict(bundle.parameters)
                if value is None:
                    parameters.pop("moving_average_type")
                else:
                    parameters["moving_average_type"] = value
                invalid = bundle.model_copy(
                    update={"parameters": parameters}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "requires SMA-based band metadata",
                ):
                    self.signal_for(invalid)

    def test_rejects_invalid_squeeze_integer_configuration(self):
        invalid_configurations = (
            {"squeeze_lookback": True},
            {"squeeze_lookback": 1},
            {"squeeze_lookback": 5.0},
            {"minimum_squeeze_points": True},
            {"minimum_squeeze_points": 1},
            {"minimum_squeeze_points": "5"},
            {"squeeze_lookback": 4, "minimum_squeeze_points": 5},
        )

        for configuration in invalid_configurations:
            with self.subTest(configuration=configuration):
                with self.assertRaises(ValueError):
                    self.signal_for(**configuration)

    def test_rejects_invalid_percentage_configuration(self):
        invalid_values = (
            True,
            "20",
            None,
            -0.1,
            100.1,
            float("nan"),
            float("inf"),
        )

        for name in (
            "squeeze_percentile",
            "bandwidth_change_tolerance_percentage",
        ):
            for value in invalid_values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        self.signal_for(**{name: value})

        with self.assertRaises(ValueError):
            self.signal_for(squeeze_percentile=0.0)

    def test_rejects_invalid_selected_or_historical_band_values(self):
        bundle = self.build_bundle()
        cases = (
            (0, 5, float("nan"), "must be finite"),
            (2, 5, -1.0, "cannot be negative"),
            (0, 5, 99.0, "upper >= middle >= lower"),
            (1, 5, 94.0, "upper >= middle >= lower"),
            (0, 2, float("inf"), "must be finite"),
        )

        for component_index, point_index, value, message in cases:
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
                with self.assertRaisesRegex(ValueError, message):
                    self.signal_for(invalid)

    def test_zero_middle_requires_zero_bandwidth(self):
        bundle = self.build_bundle(
            upper=(0.0,),
            middle=(0.0,),
            lower=(0.0,),
        )
        series = self.build_market_series(closes=(0.0,))
        zero = self.signal_for(bundle, series)

        self.assertEqual(zero.observed_values["bandwidth_percentage"], 0.0)
        self.assertEqual(
            zero.observed_values["band_position"],
            "at_middle_band",
        )
        self.assertEqual(zero.direction, SignalDirection.NEUTRAL)

        invalid = self.build_bundle(
            upper=(1.0,),
            middle=(0.0,),
            lower=(0.0,),
        )
        with self.assertRaisesRegex(
            ValueError,
            "middle band must be positive",
        ):
            self.signal_for(invalid, series)

    def test_accepts_actual_talib_bollinger_output(self):
        close_values = [
            100 + index * 0.35 + ((index % 5) - 2) * 0.8
            for index in range(40)
        ]
        candles = [
            Candle(
                timestamp=self.first_timestamp + timedelta(days=index),
                open=close,
                high=close + 1.0,
                low=close - 1.0,
                close=close,
                volume=1_000 + index,
            )
            for index, close in enumerate(close_values)
        ]
        series = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=candles,
            retrieved_at=candles[-1].timestamp,
        )
        bands = calculate_bollinger_bands(series)

        evidence = generate_bollinger_band_signal(bands, series)

        components = {
            component.name: component.points[-1].value
            for component in bands.components
        }
        self.assertEqual(evidence.observed_at, candles[-1].timestamp)
        self.assertEqual(evidence.observed_values["price"], candles[-1].close)
        self.assertAlmostEqual(
            evidence.observed_values["upper_band"],
            components["upper_band"],
        )
        self.assertAlmostEqual(
            evidence.observed_values["middle_band"],
            components["middle_band"],
        )
        self.assertAlmostEqual(
            evidence.observed_values["lower_band"],
            components["lower_band"],
        )


if __name__ == "__main__":
    unittest.main()
