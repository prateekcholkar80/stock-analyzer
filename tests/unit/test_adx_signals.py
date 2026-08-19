import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.indicators import calculate_adx
from app.analytics.trend_signals import generate_adx_trend_signal
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


class AverageDirectionalIndexSignalTests(unittest.TestCase):
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
        *,
        adx=(18.0, 22.0, 30.0),
        plus_di=(20.0, 25.0, 40.0),
        minus_di=(25.0, 20.0, 15.0),
        component_names=("adx", "plus_di", "minus_di"),
        **overrides,
    ):
        values_by_name = {
            "adx": adx,
            "plus_di": plus_di,
            "minus_di": minus_di,
        }
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "indicator": "ADX",
            "input_fields": (
                PriceField.HIGH,
                PriceField.LOW,
                PriceField.CLOSE,
            ),
            "parameters": {"period": 14},
            "components": [
                self.component(name, values_by_name[name.casefold()])
                for name in component_names
            ],
        }
        values.update(overrides)
        return IndicatorBundle(**values)

    def signal_for(self, adx, plus_di, minus_di, **options):
        bundle = self.build_bundle(
            adx=(adx,),
            plus_di=(plus_di,),
            minus_di=(minus_di,),
        )
        return generate_adx_trend_signal(bundle, **options)

    def test_generates_strong_bullish_trend_evidence(self):
        evidence = self.signal_for(30.0, 40.0, 15.0)

        self.assertEqual(evidence.category, SignalCategory.TREND)
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(evidence.evidence_id, "adx_trend.adx14.hlc")
        self.assertEqual(evidence.observed_values["adx"], 30.0)
        self.assertEqual(evidence.observed_values["plus_di"], 40.0)
        self.assertEqual(evidence.observed_values["minus_di"], 15.0)
        self.assertEqual(evidence.observed_values["di_spread"], 25.0)
        self.assertEqual(
            evidence.observed_values["directional_condition"],
            "bullish direction",
        )
        self.assertEqual(
            evidence.observed_values["strength_condition"],
            "strong trend strength",
        )
        self.assertEqual(evidence.observed_at, self.timestamps[0])
        self.assertEqual(evidence.available_at, self.timestamps[0])

    def test_generates_strong_bearish_trend_evidence(self):
        evidence = self.signal_for(35.0, 15.0, 40.0)

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["directional_condition"],
            "bearish direction",
        )

    def test_high_adx_does_not_force_direction_when_di_is_balanced(self):
        evidence = self.signal_for(40.0, 25.0, 25.5)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["directional_condition"],
            "balanced direction",
        )

    def test_adx_strength_boundaries_are_inclusive(self):
        expectations = (
            (19.9999, SignalStrength.WEAK, "weak trend strength"),
            (20.0, SignalStrength.MODERATE, "emerging trend strength"),
            (24.9999, SignalStrength.MODERATE, "emerging trend strength"),
            (25.0, SignalStrength.STRONG, "strong trend strength"),
        )

        for value, strength, condition in expectations:
            with self.subTest(value=value):
                evidence = self.signal_for(value, 40.0, 15.0)
                self.assertEqual(evidence.strength, strength)
                self.assertEqual(
                    evidence.observed_values["strength_condition"],
                    condition,
                )

    def test_di_tolerance_boundary_is_directionally_neutral(self):
        for plus_di, minus_di in ((26.0, 25.0), (24.0, 25.0)):
            with self.subTest(plus_di=plus_di, minus_di=minus_di):
                evidence = self.signal_for(30.0, plus_di, minus_di)
                self.assertEqual(
                    evidence.direction,
                    SignalDirection.NEUTRAL,
                )

    def test_di_spread_beyond_tolerance_sets_direction(self):
        bullish = self.signal_for(30.0, 26.0001, 25.0)
        bearish = self.signal_for(30.0, 23.9999, 25.0)

        self.assertEqual(bullish.direction, SignalDirection.BULLISH)
        self.assertEqual(bearish.direction, SignalDirection.BEARISH)

    def test_direction_and_strength_are_independent(self):
        weak_bullish = self.signal_for(10.0, 40.0, 15.0)
        weak_bearish = self.signal_for(10.0, 15.0, 40.0)

        self.assertEqual(weak_bullish.direction, SignalDirection.BULLISH)
        self.assertEqual(weak_bullish.strength, SignalStrength.WEAK)
        self.assertEqual(weak_bearish.direction, SignalDirection.BEARISH)
        self.assertEqual(weak_bearish.strength, SignalStrength.WEAK)

    def test_supports_custom_threshold_configuration(self):
        evidence = self.signal_for(
            35.0,
            30.0,
            25.0,
            directional_tolerance=5.0,
            trend_threshold=25.0,
            strong_trend_threshold=40.0,
        )

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(evidence.parameters["directional_tolerance"], 5.0)
        self.assertEqual(evidence.parameters["trend_threshold"], 25.0)
        self.assertEqual(
            evidence.parameters["strong_trend_threshold"],
            40.0,
        )

    def test_uses_latest_available_component_values(self):
        bundle = self.build_bundle()

        evidence = generate_adx_trend_signal(bundle)

        self.assertEqual(evidence.observed_at, self.timestamps[-1])
        self.assertEqual(evidence.observed_values["adx"], 30.0)
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)

    def test_as_of_excludes_later_component_values(self):
        bundle = self.build_bundle()

        evidence = generate_adx_trend_signal(
            bundle,
            as_of=self.timestamps[0],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[0])
        self.assertEqual(evidence.observed_values["adx"], 18.0)
        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)

    def test_as_of_ignores_invalid_unused_future_value(self):
        bundle = self.build_bundle()
        adx_component = bundle.components[0]
        points = list(adx_component.points)
        points[-1] = points[-1].model_copy(update={"value": 101.0})
        invalid_future = adx_component.model_copy(update={"points": points})
        bundle = bundle.model_copy(
            update={
                "components": [
                    invalid_future,
                    bundle.components[1],
                    bundle.components[2],
                ]
            }
        )

        evidence = generate_adx_trend_signal(
            bundle,
            as_of=self.timestamps[0],
        )

        self.assertEqual(evidence.observed_values["adx"], 18.0)

    def test_as_of_after_latest_point_uses_latest_available_data(self):
        bundle = self.build_bundle()

        evidence = generate_adx_trend_signal(
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
            generate_adx_trend_signal(
                bundle,
                as_of=self.first_timestamp - timedelta(seconds=1),
            )

    def test_rejects_invalid_as_of_time(self):
        bundle = self.build_bundle()

        for as_of in (datetime(2026, 8, 1), "2026-08-01", 1):
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    generate_adx_trend_signal(bundle, as_of=as_of)

    def test_rejects_non_adx_indicator(self):
        bundle = self.build_bundle(indicator="MACD")

        with self.assertRaisesRegex(
            ValueError,
            "requires an ADX indicator bundle",
        ):
            generate_adx_trend_signal(bundle)

    def test_accepts_case_insensitive_indicator_and_component_names(self):
        bundle = self.build_bundle(
            indicator="adx",
            component_names=("ADX", "PLUS_DI", "MINUS_DI"),
        )

        evidence = generate_adx_trend_signal(bundle)

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)

    def test_rejects_incorrect_indicator_input_fields(self):
        bundle = self.build_bundle()
        invalid_inputs = (
            {
                "price_field": PriceField.CLOSE,
                "input_fields": (),
            },
            {
                "input_fields": (PriceField.HIGH, PriceField.LOW),
            },
            {
                "input_fields": (
                    PriceField.LOW,
                    PriceField.HIGH,
                    PriceField.CLOSE,
                ),
            },
        )

        for update in invalid_inputs:
            with self.subTest(update=update):
                invalid = bundle.model_copy(update=update)
                with self.assertRaisesRegex(
                    ValueError,
                    "requires high, low, and close inputs",
                ):
                    generate_adx_trend_signal(invalid)

    def test_rejects_missing_extra_or_duplicate_components(self):
        bundle = self.build_bundle()
        extra = self.component("extra", (1.0, 1.0, 1.0))
        duplicate = self.component("ADX", (18.0, 22.0, 30.0))
        invalid_component_lists = (
            bundle.components[:2],
            bundle.components + [extra],
            bundle.components + [duplicate],
        )

        for components in invalid_component_lists:
            with self.subTest(
                names=[component.name for component in components]
            ):
                invalid = bundle.model_copy(
                    update={"components": components}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "requires adx, plus_di, and minus_di components",
                ):
                    generate_adx_trend_signal(invalid)

    def test_rejects_components_with_mismatched_timestamps(self):
        bundle = self.build_bundle()
        shifted = self.component(
            "plus_di",
            (20.0, 25.0, 40.0),
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
            generate_adx_trend_signal(invalid)

    def test_rejects_missing_or_invalid_period(self):
        bundle = self.build_bundle()
        invalid_parameters = (
            {},
            {"period": True},
            {"period": 14.0},
            {"period": "14"},
            {"period": 1},
            {"period": 0},
        )

        for parameters in invalid_parameters:
            with self.subTest(parameters=parameters):
                invalid = bundle.model_copy(
                    update={"parameters": parameters}
                )
                with self.assertRaises(ValueError):
                    generate_adx_trend_signal(invalid)

    def test_rejects_selected_component_values_outside_range(self):
        bundle = self.build_bundle()

        for component_index in range(3):
            for value in (-0.1, 100.1):
                with self.subTest(
                    component_index=component_index,
                    value=value,
                ):
                    component = bundle.components[component_index]
                    points = list(component.points)
                    points[-1] = points[-1].model_copy(
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
                        "values must be between 0 and 100",
                    ):
                        generate_adx_trend_signal(invalid)

    def test_rejects_invalid_threshold_values_for_every_setting(self):
        bundle = self.build_bundle()
        fields = (
            "directional_tolerance",
            "trend_threshold",
            "strong_trend_threshold",
        )
        invalid_values = (
            True,
            "20",
            None,
            -0.1,
            100.1,
            float("nan"),
            float("inf"),
        )

        for field_name in fields:
            for value in invalid_values:
                with self.subTest(field_name=field_name, value=value):
                    with self.assertRaises(ValueError):
                        generate_adx_trend_signal(
                            bundle,
                            **{field_name: value},
                        )

    def test_rejects_equal_or_reversed_strength_thresholds(self):
        bundle = self.build_bundle()

        for trend_threshold, strong_threshold in (
            (25.0, 25.0),
            (30.0, 25.0),
        ):
            with self.subTest(
                trend_threshold=trend_threshold,
                strong_threshold=strong_threshold,
            ):
                with self.assertRaisesRegex(
                    ValueError,
                    "trend threshold must be below strong trend threshold",
                ):
                    generate_adx_trend_signal(
                        bundle,
                        trend_threshold=trend_threshold,
                        strong_trend_threshold=strong_threshold,
                    )

    def test_accepts_actual_talib_adx_output(self):
        closes = [
            100 + index * 0.45 + ((index % 6) - 2.5) * 1.1
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
            for index, close in enumerate(closes)
        ]
        market_series = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=candles,
            retrieved_at=candles[-1].timestamp,
        )
        adx = calculate_adx(market_series)

        evidence = generate_adx_trend_signal(adx)

        self.assertEqual(evidence.observed_at, candles[-1].timestamp)
        self.assertEqual(evidence.parameters["period"], 14)
        self.assertEqual(evidence.parameters["input_fields"], "high,low,close")
        self.assertGreaterEqual(evidence.observed_values["adx"], 0)
        self.assertLessEqual(evidence.observed_values["adx"], 100)


if __name__ == "__main__":
    unittest.main()
