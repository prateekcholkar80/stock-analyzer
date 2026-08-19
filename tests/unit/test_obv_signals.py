import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.indicators import calculate_obv
from app.analytics.volume_signals import generate_obv_confirmation_signal
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


class OnBalanceVolumeSignalTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.timestamps = [
            self.first_timestamp + timedelta(days=day)
            for day in range(8)
        ]

    def expected_obv(self, prices, volumes):
        values = [float(volumes[0])]
        for index in range(1, len(prices)):
            if prices[index] > prices[index - 1]:
                change = volumes[index]
            elif prices[index] < prices[index - 1]:
                change = -volumes[index]
            else:
                change = 0
            values.append(values[-1] + change)
        return tuple(values)

    def build_market_series(
        self,
        closes=(100.0, 101.0, 102.0, 103.0, 104.0, 105.0),
        volumes=(100, 100, 100, 100, 100, 100),
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
                volume=volume,
            )
            for timestamp, close, volume in zip(
                candle_timestamps,
                closes,
                volumes,
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

    def build_obv_series(
        self,
        values=(100.0, 200.0, 300.0, 400.0, 500.0, 600.0),
        timestamps=None,
        price_field=PriceField.CLOSE,
        **overrides,
    ):
        point_timestamps = timestamps or self.timestamps[:len(values)]
        fields = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "indicator": "OBV",
            "input_fields": (price_field, PriceField.VOLUME),
            "parameters": {},
            "points": [
                IndicatorPoint(timestamp=timestamp, value=value)
                for timestamp, value in zip(point_timestamps, values)
            ],
        }
        fields.update(overrides)
        return IndicatorSeries(**fields)

    def build_inputs(self, closes, volumes=None):
        normalized_volumes = volumes or tuple(100 for _ in closes)
        market = self.build_market_series(
            closes=closes,
            volumes=normalized_volumes,
        )
        obv = self.build_obv_series(
            values=self.expected_obv(closes, normalized_volumes),
        )
        return obv, market

    def signal_for(self, obv=None, market=None, **options):
        return generate_obv_confirmation_signal(
            obv or self.build_obv_series(),
            market or self.build_market_series(),
            **options,
        )

    def test_generates_moderate_bullish_price_volume_confirmation(self):
        evidence = self.signal_for()

        self.assertEqual(evidence.category, SignalCategory.VOLUME)
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(
            evidence.evidence_id,
            "obv_confirmation.obv.close.lookback5",
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "bullish price-volume confirmation",
        )
        self.assertEqual(
            evidence.observed_values["price_direction"],
            "bullish",
        )
        self.assertEqual(
            evidence.observed_values["obv_direction"],
            "bullish",
        )
        self.assertEqual(evidence.observed_at, self.timestamps[5])
        self.assertEqual(evidence.available_at, self.timestamps[5])
        self.assertFalse(evidence.observed_values["is_high_volume"])

    def test_generates_moderate_bearish_price_volume_confirmation(self):
        closes = (105.0, 104.0, 103.0, 102.0, 101.0, 100.0)
        obv, market = self.build_inputs(closes)

        evidence = self.signal_for(obv, market)

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bearish price-volume confirmation",
        )

    def test_detects_bullish_obv_divergence(self):
        closes = (100.0, 90.0, 91.0, 92.0, 93.0, 94.0)
        obv, market = self.build_inputs(closes)

        evidence = self.signal_for(obv, market)

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bullish OBV divergence",
        )
        self.assertEqual(
            evidence.observed_values["price_direction"],
            "bearish",
        )
        self.assertEqual(
            evidence.observed_values["obv_direction"],
            "bullish",
        )

    def test_detects_bearish_obv_divergence(self):
        closes = (100.0, 110.0, 109.0, 108.0, 107.0, 106.0)
        obv, market = self.build_inputs(closes)

        evidence = self.signal_for(obv, market)

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bearish OBV divergence",
        )

    def test_high_current_volume_strengthens_confirmation(self):
        closes = (100.0, 101.0, 102.0, 103.0, 104.0, 105.0)
        volumes = (100, 100, 100, 100, 100, 200)
        obv, market = self.build_inputs(closes, volumes)

        evidence = self.signal_for(obv, market)

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertTrue(evidence.observed_values["volume_evaluated"])
        self.assertTrue(evidence.observed_values["is_high_volume"])
        self.assertEqual(
            evidence.observed_values["current_volume_ratio"],
            2.0,
        )

    def test_flat_price_with_positive_obv_is_accumulation_context(self):
        closes = (100.0, 99.0, 100.0, 100.0, 100.0, 100.0)
        volumes = (100, 50, 200, 100, 100, 100)
        obv, market = self.build_inputs(closes, volumes)

        evidence = self.signal_for(obv, market)

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["condition"],
            "OBV accumulation with flat price",
        )

    def test_flat_price_with_negative_obv_is_distribution_context(self):
        closes = (100.0, 101.0, 100.0, 100.0, 100.0, 100.0)
        volumes = (100, 50, 200, 100, 100, 100)
        obv, market = self.build_inputs(closes, volumes)

        evidence = self.signal_for(obv, market)

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["condition"],
            "OBV distribution with flat price",
        )

    def test_directional_price_without_obv_flow_is_unconfirmed(self):
        closes = (100.0, 99.0, 101.0)
        volumes = (100, 100, 100)
        obv, market = self.build_inputs(closes, volumes)

        evidence = self.signal_for(obv, market)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bullish price move unconfirmed by OBV",
        )

    def test_flat_price_and_obv_are_neutral(self):
        closes = (100.0,) * 6
        obv, market = self.build_inputs(closes)

        evidence = self.signal_for(obv, market)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(
            evidence.observed_values["condition"],
            "neutral price-volume flow",
        )

    def test_single_point_does_not_invent_price_volume_trend(self):
        obv, market = self.build_inputs((100.0,))

        evidence = self.signal_for(obv, market)

        self.assertFalse(evidence.observed_values["trend_evaluated"])
        self.assertEqual(
            evidence.observed_values["condition"],
            "price-volume trend unavailable",
        )
        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)

    def test_flow_normalization_is_independent_of_obv_origin(self):
        closes = (100.0, 101.0, 102.0)
        volumes = (100, 100, 100)
        obv, market = self.build_inputs(closes, volumes)
        shifted_points = [
            point.model_copy(update={"value": point.value + 10_000})
            for point in obv.points
        ]
        shifted = obv.model_copy(update={"points": shifted_points})

        original = self.signal_for(obv, market)
        shifted_evidence = self.signal_for(shifted, market)

        self.assertEqual(
            original.observed_values["obv_flow_percentage"],
            shifted_evidence.observed_values["obv_flow_percentage"],
        )
        self.assertEqual(original.direction, shifted_evidence.direction)

    def test_custom_tolerances_control_direction_classification(self):
        closes = (100.0, 101.0)
        volumes = (100, 100)
        obv, market = self.build_inputs(closes, volumes)

        evidence = self.signal_for(
            obv,
            market,
            price_change_tolerance_percentage=2.0,
            obv_flow_tolerance_percentage=100.0,
        )

        self.assertEqual(
            evidence.observed_values["price_direction"],
            "neutral",
        )
        self.assertEqual(
            evidence.observed_values["obv_direction"],
            "neutral",
        )

    def test_custom_lookbacks_and_volume_threshold_are_preserved(self):
        evidence = self.signal_for(
            trend_lookback=3,
            volume_lookback=4,
            minimum_volume_points=4,
            high_volume_multiplier=1.25,
        )

        self.assertEqual(evidence.parameters["trend_lookback"], 3)
        self.assertEqual(evidence.parameters["volume_lookback"], 4)
        self.assertEqual(evidence.parameters["minimum_volume_points"], 4)
        self.assertEqual(
            evidence.parameters["high_volume_multiplier"],
            1.25,
        )
        self.assertEqual(
            evidence.observed_values["trend_observation_count"],
            4,
        )

    def test_insufficient_volume_history_does_not_invent_confirmation(self):
        obv, market = self.build_inputs((100.0, 101.0, 102.0))

        evidence = self.signal_for(obv, market)

        self.assertFalse(evidence.observed_values["volume_evaluated"])
        self.assertFalse(evidence.observed_values["is_high_volume"])
        self.assertIn("not evaluated", evidence.explanation)

    def test_zero_volume_baseline_handles_first_nonzero_volume(self):
        closes = (100.0, 100.0, 100.0, 100.0, 100.0, 101.0)
        volumes = (0, 0, 0, 0, 0, 100)
        obv, market = self.build_inputs(closes, volumes)

        evidence = self.signal_for(obv, market)

        self.assertTrue(evidence.observed_values["volume_evaluated"])
        self.assertTrue(evidence.observed_values["volume_baseline_zero"])
        self.assertTrue(evidence.observed_values["is_high_volume"])
        self.assertNotIn(
            "current_volume_ratio",
            evidence.observed_values,
        )

    def test_as_of_excludes_future_price_obv_and_volume(self):
        closes = (100.0, 101.0, 102.0, 90.0, 80.0, 70.0)
        volumes = (100, 100, 100, 500, 500, 500)
        obv, market = self.build_inputs(closes, volumes)

        evidence = self.signal_for(
            obv,
            market,
            as_of=self.timestamps[2],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[2])
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.observed_values["current_volume"], 100)
        self.assertEqual(
            evidence.observed_values["trend_observation_count"],
            3,
        )

    def test_as_of_ignores_invalid_unused_future_obv(self):
        obv = self.build_obv_series()
        points = list(obv.points)
        points[-1] = points[-1].model_copy(
            update={"value": float("nan")}
        )
        invalid = obv.model_copy(update={"points": points})

        evidence = self.signal_for(
            invalid,
            as_of=self.timestamps[4],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[4])

    def test_as_of_after_latest_point_uses_latest_available_data(self):
        evidence = self.signal_for(
            as_of=self.timestamps[-1] + timedelta(days=10)
        )

        self.assertEqual(evidence.observed_at, self.timestamps[5])

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
        market = self.build_market_series(
            closes=(100.0,) * 5,
            volumes=(100,) * 5,
        )

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires candles matching every selected point",
        ):
            self.signal_for(market=market)

    def test_rejects_nonconsecutive_market_window(self):
        obv = self.build_obv_series(
            values=(100.0, 200.0),
            timestamps=(self.timestamps[0], self.timestamps[2]),
        )
        market = self.build_market_series(
            closes=(100.0, 100.5, 101.0),
            volumes=(100, 100, 100),
        )

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires consecutive candles",
        ):
            self.signal_for(obv, market)

    def test_rejects_obv_transition_inconsistent_with_candles(self):
        obv = self.build_obv_series()
        points = list(obv.points)
        points[3] = points[3].model_copy(update={"value": 350.0})
        invalid = obv.model_copy(update={"points": points})

        with self.assertRaisesRegex(
            ValueError,
            "changes must match price direction and candle volume",
        ):
            self.signal_for(invalid)

    def test_rejects_non_finite_selected_obv_value(self):
        obv = self.build_obv_series()
        points = list(obv.points)
        points[2] = points[2].model_copy(
            update={"value": float("nan")}
        )
        invalid = obv.model_copy(update={"points": points})

        with self.assertRaisesRegex(ValueError, "values must be finite"):
            self.signal_for(invalid)

    def test_rejects_non_positive_reference_price(self):
        closes = (0.0, 1.0)
        volumes = (100, 100)
        obv, market = self.build_inputs(closes, volumes)

        with self.assertRaisesRegex(
            ValueError,
            "requires a positive reference price",
        ):
            self.signal_for(obv, market)

    def test_rejects_mismatched_market_identity(self):
        obv = self.build_obv_series()

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
                    self.signal_for(obv, market)

    def test_rejects_unordered_or_duplicate_candles(self):
        obv = self.build_obv_series(values=(100.0, 200.0))
        invalid_timestamp_sets = (
            (self.timestamps[1], self.timestamps[0]),
            (self.timestamps[0], self.timestamps[0]),
        )

        for timestamps in invalid_timestamp_sets:
            with self.subTest(timestamps=timestamps):
                market = self.build_market_series(
                    closes=(100.0, 101.0),
                    volumes=(100, 100),
                    timestamps=timestamps,
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "unique timestamps in ascending order",
                ):
                    self.signal_for(obv, market)

    def test_rejects_unordered_or_duplicate_obv_points(self):
        obv = self.build_obv_series(values=(100.0, 200.0))
        invalid_timestamp_sets = (
            (self.timestamps[1], self.timestamps[0]),
            (self.timestamps[0], self.timestamps[0]),
        )

        for timestamps in invalid_timestamp_sets:
            with self.subTest(timestamps=timestamps):
                points = [
                    IndicatorPoint(timestamp=timestamp, value=value)
                    for timestamp, value in zip(
                        timestamps,
                        (100.0, 200.0),
                    )
                ]
                invalid = obv.model_copy(update={"points": points})
                with self.assertRaisesRegex(
                    ValueError,
                    "unique timestamps in ascending order",
                ):
                    self.signal_for(invalid)

    def test_rejects_non_obv_indicator(self):
        obv = self.build_obv_series(indicator="ATR")

        with self.assertRaisesRegex(
            ValueError,
            "requires an OBV indicator series",
        ):
            self.signal_for(obv)

    def test_accepts_case_insensitive_obv_indicator_name(self):
        obv = self.build_obv_series(indicator="obv")

        evidence = self.signal_for(obv)

        self.assertEqual(evidence.category, SignalCategory.VOLUME)

    def test_rejects_invalid_obv_input_fields(self):
        invalid_fields = (
            (PriceField.HIGH, PriceField.LOW),
            (PriceField.VOLUME, PriceField.CLOSE),
            (PriceField.CLOSE, PriceField.VOLUME, PriceField.HIGH),
        )

        for input_fields in invalid_fields:
            with self.subTest(input_fields=input_fields):
                obv = self.build_obv_series()
                invalid = obv.model_copy(
                    update={"input_fields": input_fields}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "one OHLC field followed by volume",
                ):
                    self.signal_for(invalid)

    def test_rejects_invalid_integer_configuration(self):
        invalid_configurations = (
            {"trend_lookback": True},
            {"trend_lookback": 0},
            {"trend_lookback": 5.0},
            {"volume_lookback": True},
            {"volume_lookback": 0},
            {"minimum_volume_points": "5"},
            {"volume_lookback": 4, "minimum_volume_points": 5},
        )

        for configuration in invalid_configurations:
            with self.subTest(configuration=configuration):
                with self.assertRaises(ValueError):
                    self.signal_for(**configuration)

    def test_rejects_invalid_tolerance_configuration(self):
        invalid_values = (
            True,
            "1",
            None,
            -0.1,
            100.1,
            float("nan"),
            float("inf"),
        )

        for name in (
            "price_change_tolerance_percentage",
            "obv_flow_tolerance_percentage",
        ):
            for value in invalid_values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        self.signal_for(**{name: value})

    def test_rejects_invalid_high_volume_multiplier(self):
        invalid_values = (
            True,
            "1.5",
            None,
            0.99,
            -1,
            float("nan"),
            float("inf"),
        )

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.signal_for(high_volume_multiplier=value)

    def test_accepts_actual_talib_obv_output(self):
        closes = tuple(
            100 + index * 0.3 + ((index % 5) - 2) * 0.7
            for index in range(30)
        )
        volumes = tuple(1_000 + index * 10 for index in range(30))
        market = self.build_market_series(
            closes=closes,
            volumes=volumes,
            timestamps=[
                self.first_timestamp + timedelta(days=index)
                for index in range(30)
            ],
            retrieved_at=(
                self.first_timestamp + timedelta(days=29)
            ),
        )
        obv = calculate_obv(market)

        evidence = generate_obv_confirmation_signal(obv, market)

        self.assertEqual(
            evidence.observed_at,
            market.candles[-1].timestamp,
        )
        self.assertEqual(
            evidence.observed_values["current_obv"],
            obv.points[-1].value,
        )
        self.assertEqual(
            evidence.observed_values["current_volume"],
            market.candles[-1].volume,
        )


if __name__ == "__main__":
    unittest.main()
