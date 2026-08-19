import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.indicators import calculate_atr
from app.analytics.volatility_signals import (
    generate_atr_volatility_signal,
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
    IndicatorPoint,
    IndicatorSeries,
    PriceField,
)


class AverageTrueRangeSignalTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.timestamps = [
            self.first_timestamp + timedelta(days=day)
            for day in range(6)
        ]

    def build_atr_series(
        self,
        values=(1.0, 1.1, 1.2, 1.3, 1.4, 3.0),
        timestamps=None,
        **overrides,
    ):
        point_timestamps = timestamps or self.timestamps[:len(values)]
        fields = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "indicator": "ATR",
            "input_fields": (
                PriceField.HIGH,
                PriceField.LOW,
                PriceField.CLOSE,
            ),
            "parameters": {"period": 14},
            "points": [
                IndicatorPoint(timestamp=timestamp, value=value)
                for timestamp, value in zip(point_timestamps, values)
            ],
        }
        fields.update(overrides)
        return IndicatorSeries(**fields)

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
        fields = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "candles": candles,
            "retrieved_at": self.timestamps[-1],
        }
        fields.update(overrides)
        return HistoricalCandleSeries(**fields)

    def signal_for(self, atr_series=None, market_series=None, **options):
        return generate_atr_volatility_signal(
            atr_series or self.build_atr_series(),
            market_series or self.build_market_series(),
            **options,
        )

    def test_generates_strong_high_and_expanding_volatility_evidence(self):
        evidence = self.signal_for()

        self.assertEqual(evidence.category, SignalCategory.VOLATILITY)
        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(evidence.evidence_id, "atr_volatility.atr14.hlc")
        self.assertEqual(evidence.observed_values["atr"], 3.0)
        self.assertEqual(evidence.observed_values["close"], 100.0)
        self.assertEqual(
            evidence.observed_values["atr_percentage"],
            3.0,
        )
        self.assertEqual(
            evidence.observed_values["volatility_regime"],
            "high",
        )
        self.assertEqual(
            evidence.observed_values["volatility_trend"],
            "expanding",
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "high and expanding volatility",
        )
        self.assertEqual(evidence.observed_at, self.timestamps[-1])
        self.assertEqual(evidence.available_at, self.timestamps[-1])

    def test_low_volatility_is_moderate_direction_neutral_evidence(self):
        atr = self.build_atr_series(
            values=(3.0, 2.5, 2.0, 1.5, 1.0, 0.5)
        )

        evidence = self.signal_for(atr)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.observed_values["volatility_regime"],
            "low",
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "low volatility",
        )

    def test_constant_normalized_atr_is_normal_not_high_or_low(self):
        atr = self.build_atr_series(values=(1.0,) * 6)

        evidence = self.signal_for(atr)

        self.assertEqual(
            evidence.observed_values["volatility_regime"],
            "normal",
        )
        self.assertEqual(
            evidence.observed_values["volatility_trend"],
            "stable",
        )
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["condition"],
            "normal volatility",
        )

    def test_expansion_without_regime_history_is_moderate(self):
        atr = self.build_atr_series(values=(1.0, 2.0))
        market = self.build_market_series(closes=(100.0, 100.0))

        evidence = self.signal_for(atr, market)

        self.assertFalse(evidence.observed_values["regime_evaluated"])
        self.assertEqual(
            evidence.observed_values["volatility_regime"],
            "unavailable",
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "volatility expanding",
        )
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)

    def test_single_point_does_not_invent_trend_or_regime(self):
        atr = self.build_atr_series(values=(1.0,))
        market = self.build_market_series(closes=(100.0,))

        evidence = self.signal_for(atr, market)

        self.assertEqual(
            evidence.observed_values["volatility_regime"],
            "unavailable",
        )
        self.assertEqual(
            evidence.observed_values["volatility_trend"],
            "unavailable",
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "volatility regime unavailable",
        )
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertNotIn(
            "previous_atr_percentage",
            evidence.observed_values,
        )

    def test_normalizes_atr_against_each_matching_close(self):
        atr = self.build_atr_series(values=(1.0, 2.0, 3.0, 4.0, 5.0))
        market = self.build_market_series(
            closes=(100.0, 200.0, 300.0, 400.0, 500.0)
        )

        evidence = self.signal_for(atr, market)

        self.assertEqual(evidence.observed_values["atr_percentage"], 1.0)
        self.assertEqual(
            evidence.observed_values["volatility_regime"],
            "normal",
        )

    def test_calculates_configurable_reference_risk_distance(self):
        evidence = self.signal_for(risk_atr_multiplier=2.5)

        self.assertEqual(
            evidence.observed_values["reference_risk_distance"],
            7.5,
        )
        self.assertEqual(
            evidence.observed_values["reference_risk_percentage"],
            7.5,
        )
        self.assertEqual(evidence.parameters["risk_atr_multiplier"], 2.5)
        self.assertTrue(
            evidence.parameters["risk_distance_is_reference_only"]
        )
        self.assertIn("not a directional signal", evidence.explanation)
        self.assertIn("not a", evidence.explanation)
        self.assertIn("stop recommendation", evidence.explanation)

    def test_custom_regime_configuration_is_preserved(self):
        evidence = self.signal_for(
            regime_lookback=4,
            low_volatility_percentile=20.0,
            high_volatility_percentile=80.0,
            minimum_regime_points=4,
        )

        self.assertEqual(evidence.parameters["regime_lookback"], 4)
        self.assertEqual(
            evidence.parameters["low_volatility_percentile"],
            20.0,
        )
        self.assertEqual(
            evidence.parameters["high_volatility_percentile"],
            80.0,
        )
        self.assertEqual(
            evidence.observed_values["regime_observation_count"],
            4,
        )

    def test_change_tolerance_controls_expansion_classification(self):
        atr = self.build_atr_series(values=(1.0, 1.04))
        market = self.build_market_series(closes=(100.0, 100.0))

        stable = self.signal_for(
            atr,
            market,
            change_tolerance_percentage=5.0,
        )
        expanding = self.signal_for(
            atr,
            market,
            change_tolerance_percentage=3.0,
        )

        self.assertEqual(
            stable.observed_values["volatility_trend"],
            "stable",
        )
        self.assertEqual(
            expanding.observed_values["volatility_trend"],
            "expanding",
        )

    def test_zero_previous_atr_handles_expansion_without_infinity(self):
        atr = self.build_atr_series(values=(0.0, 1.0))
        market = self.build_market_series(closes=(100.0, 100.0))

        evidence = self.signal_for(atr, market)

        self.assertEqual(
            evidence.observed_values["atr_change_percentage"],
            100.0,
        )
        self.assertEqual(
            evidence.observed_values["volatility_trend"],
            "expanding",
        )

    def test_regime_percentile_boundaries_are_inclusive(self):
        low_atr = self.build_atr_series(
            values=(1.0, 2.0, 3.0, 4.0, 1.0)
        )
        high_atr = self.build_atr_series(
            values=(1.0, 2.0, 3.0, 4.0, 4.0)
        )
        market = self.build_market_series(closes=(100.0,) * 5)

        low = self.signal_for(
            low_atr,
            market,
            low_volatility_percentile=0.0,
            high_volatility_percentile=100.0,
        )
        high = self.signal_for(
            high_atr,
            market,
            low_volatility_percentile=0.0,
            high_volatility_percentile=100.0,
        )

        self.assertEqual(low.observed_values["volatility_regime"], "low")
        self.assertEqual(
            high.observed_values["volatility_regime"],
            "high",
        )

    def test_as_of_excludes_future_atr_candles_and_regime_history(self):
        atr = self.build_atr_series(
            values=(1.0, 1.1, 3.0, 0.1, 5.0, 6.0)
        )

        evidence = self.signal_for(
            atr,
            as_of=self.timestamps[2],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[2])
        self.assertEqual(evidence.observed_values["atr"], 3.0)
        self.assertEqual(
            evidence.observed_values["regime_observation_count"],
            3,
        )
        self.assertFalse(evidence.observed_values["regime_evaluated"])

    def test_as_of_ignores_invalid_unused_future_atr(self):
        atr = self.build_atr_series()
        points = list(atr.points)
        points[-1] = points[-1].model_copy(update={"value": -1.0})
        invalid = atr.model_copy(update={"points": points})

        evidence = self.signal_for(
            invalid,
            as_of=self.timestamps[4],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[4])

    def test_as_of_after_latest_point_uses_latest_available_data(self):
        evidence = self.signal_for(
            as_of=self.timestamps[-1] + timedelta(days=10)
        )

        self.assertEqual(evidence.observed_at, self.timestamps[-1])

    def test_rejects_as_of_before_first_indicator_point(self):
        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires an available indicator point",
        ):
            self.signal_for(
                as_of=self.first_timestamp - timedelta(seconds=1)
            )

    def test_rejects_invalid_as_of_time(self):
        for as_of in (datetime(2026, 8, 1), "2026-08-01", 1):
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    self.signal_for(as_of=as_of)

    def test_rejects_missing_matching_candle(self):
        market = self.build_market_series(closes=(100.0,) * 5)

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires a candle matching each indicator point",
        ):
            self.signal_for(market_series=market)

    def test_rejects_non_positive_matching_close(self):
        atr = self.build_atr_series(values=(1.0,))
        market = self.build_market_series(closes=(0.0,))

        with self.assertRaisesRegex(
            ValueError,
            "requires a positive matching close",
        ):
            self.signal_for(atr, market)

    def test_rejects_mismatched_market_identity(self):
        atr = self.build_atr_series()

        for field_name, value in (
            ("exchange", "BSE"),
            ("symbol_token", "9999"),
            ("symbol", "TCS-EQ"),
            ("interval", "ONE_HOUR"),
        ):
            with self.subTest(field_name=field_name):
                market = self.build_market_series(
                    **{field_name: value}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "same instrument and timeframe",
                ):
                    self.signal_for(atr, market)

    def test_rejects_unordered_or_duplicate_market_candles(self):
        atr = self.build_atr_series(values=(1.0, 1.0))
        invalid_timestamp_sets = (
            [self.timestamps[1], self.timestamps[0]],
            [self.timestamps[0], self.timestamps[0]],
        )

        for timestamps in invalid_timestamp_sets:
            with self.subTest(timestamps=timestamps):
                market = self.build_market_series(
                    closes=(100.0, 100.0),
                    timestamps=timestamps,
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "unique timestamps in ascending order",
                ):
                    self.signal_for(atr, market)

    def test_rejects_unordered_or_duplicate_atr_points(self):
        atr = self.build_atr_series(values=(1.0, 1.0))
        invalid_timestamp_sets = (
            [self.timestamps[1], self.timestamps[0]],
            [self.timestamps[0], self.timestamps[0]],
        )

        for timestamps in invalid_timestamp_sets:
            with self.subTest(timestamps=timestamps):
                points = [
                    IndicatorPoint(timestamp=timestamp, value=1.0)
                    for timestamp in timestamps
                ]
                invalid = atr.model_copy(update={"points": points})
                with self.assertRaisesRegex(
                    ValueError,
                    "unique timestamps in ascending order",
                ):
                    self.signal_for(invalid)

    def test_rejects_non_atr_indicator(self):
        atr = self.build_atr_series(indicator="RSI")

        with self.assertRaisesRegex(
            ValueError,
            "requires an ATR indicator series",
        ):
            self.signal_for(atr)

    def test_accepts_case_insensitive_atr_indicator_name(self):
        atr = self.build_atr_series(indicator="atr")

        evidence = self.signal_for(atr)

        self.assertEqual(evidence.category, SignalCategory.VOLATILITY)

    def test_rejects_incorrect_atr_input_fields(self):
        atr = self.build_atr_series(
            input_fields=(PriceField.HIGH, PriceField.LOW),
        )

        with self.assertRaisesRegex(
            ValueError,
            "requires high, low, and close inputs",
        ):
            self.signal_for(atr)

    def test_rejects_missing_or_invalid_period(self):
        atr = self.build_atr_series()

        for value in (None, True, 14.0, "14", 1, 0):
            with self.subTest(value=value):
                parameters = dict(atr.parameters)
                if value is None:
                    parameters.pop("period")
                else:
                    parameters["period"] = value
                invalid = atr.model_copy(update={"parameters": parameters})
                with self.assertRaises(ValueError):
                    self.signal_for(invalid)

    def test_rejects_invalid_regime_integer_configuration(self):
        invalid_configurations = (
            {"regime_lookback": True},
            {"regime_lookback": 1},
            {"regime_lookback": 5.0},
            {"minimum_regime_points": True},
            {"minimum_regime_points": 1},
            {"minimum_regime_points": "5"},
            {"regime_lookback": 4, "minimum_regime_points": 5},
        )

        for configuration in invalid_configurations:
            with self.subTest(configuration=configuration):
                with self.assertRaises(ValueError):
                    self.signal_for(**configuration)

    def test_rejects_invalid_percentile_and_tolerance_values(self):
        invalid_values = (
            True,
            "25",
            None,
            -0.1,
            100.1,
            float("nan"),
            float("inf"),
        )
        names = (
            "low_volatility_percentile",
            "high_volatility_percentile",
            "change_tolerance_percentage",
        )

        for name in names:
            for value in invalid_values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        self.signal_for(**{name: value})

    def test_rejects_equal_or_reversed_regime_percentiles(self):
        for low, high in ((25.0, 25.0), (75.0, 25.0)):
            with self.subTest(low=low, high=high):
                with self.assertRaisesRegex(
                    ValueError,
                    "low-volatility percentile must be below",
                ):
                    self.signal_for(
                        low_volatility_percentile=low,
                        high_volatility_percentile=high,
                    )

    def test_rejects_invalid_risk_multiplier(self):
        invalid_values = (
            True,
            "2",
            None,
            0,
            -1,
            float("nan"),
            float("inf"),
        )

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.signal_for(risk_atr_multiplier=value)

    def test_rejects_selected_or_historical_invalid_atr_values(self):
        atr = self.build_atr_series()

        for point_index in (2, 5):
            for value, message in (
                (-0.1, "cannot be negative"),
                (float("nan"), "must be finite"),
            ):
                with self.subTest(point_index=point_index, value=value):
                    points = list(atr.points)
                    points[point_index] = points[point_index].model_copy(
                        update={"value": value}
                    )
                    invalid = atr.model_copy(update={"points": points})
                    with self.assertRaisesRegex(ValueError, message):
                        self.signal_for(invalid)

    def test_accepts_actual_talib_atr_output(self):
        close_values = [
            100 + index * 0.35 + ((index % 4) - 1.5) * 0.9
            for index in range(30)
        ]
        candles = [
            Candle(
                timestamp=self.first_timestamp + timedelta(days=index),
                open=close,
                high=close + 1.0 + (index % 3) * 0.2,
                low=close - 0.8 - (index % 2) * 0.3,
                close=close,
                volume=1_000 + index,
            )
            for index, close in enumerate(close_values)
        ]
        market = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=candles,
            retrieved_at=candles[-1].timestamp,
        )
        atr = calculate_atr(market)

        evidence = generate_atr_volatility_signal(atr, market)

        self.assertEqual(evidence.observed_at, candles[-1].timestamp)
        self.assertAlmostEqual(
            evidence.observed_values["atr"],
            atr.points[-1].value,
        )
        self.assertAlmostEqual(
            evidence.observed_values["atr_percentage"],
            atr.points[-1].value / candles[-1].close * 100,
        )
        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)


if __name__ == "__main__":
    unittest.main()
