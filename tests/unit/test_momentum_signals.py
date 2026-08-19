import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.indicators import calculate_rsi
from app.analytics.momentum_signals import (
    generate_rsi_mean_reversion_signal,
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


class RelativeStrengthIndexSignalTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.timestamps = [
            self.first_timestamp + timedelta(days=day)
            for day in range(3)
        ]

    def build_series(
        self,
        values=(45.0, 55.0, 25.0),
        timestamps=None,
        **overrides,
    ):
        values_by_field = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "indicator": "RSI",
            "price_field": PriceField.CLOSE,
            "parameters": {"period": 14},
            "points": [
                IndicatorPoint(timestamp=timestamp, value=value)
                for timestamp, value in zip(
                    timestamps or self.timestamps,
                    values,
                )
            ],
        }
        values_by_field.update(overrides)
        return IndicatorSeries(**values_by_field)

    def signal_for(self, value, **overrides):
        series = self.build_series(values=(50.0, 50.0, value))
        return generate_rsi_mean_reversion_signal(series, **overrides)

    def test_generates_moderate_bullish_oversold_evidence(self):
        evidence = self.signal_for(25.0)

        self.assertEqual(evidence.category, SignalCategory.MOMENTUM)
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(
            evidence.evidence_id,
            "rsi_condition.rsi14.close",
        )
        self.assertEqual(evidence.observed_at, self.timestamps[-1])
        self.assertEqual(evidence.available_at, self.timestamps[-1])
        self.assertEqual(evidence.observed_values["rsi"], 25.0)
        self.assertEqual(evidence.observed_values["condition"], "oversold")
        self.assertEqual(
            evidence.parameters["interpretation"],
            "mean_reversion",
        )
        self.assertIn("not confirmation of a reversal", evidence.explanation)

    def test_generates_strong_bullish_extreme_oversold_evidence(self):
        evidence = self.signal_for(15.0)

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["condition"],
            "extremely oversold",
        )

    def test_generates_moderate_bearish_overbought_evidence(self):
        evidence = self.signal_for(75.0)

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.observed_values["condition"],
            "overbought",
        )

    def test_generates_strong_bearish_extreme_overbought_evidence(self):
        evidence = self.signal_for(85.0)

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["condition"],
            "extremely overbought",
        )

    def test_generates_weak_neutral_evidence_inside_middle_range(self):
        evidence = self.signal_for(50.0)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(evidence.observed_values["condition"], "neutral")
        self.assertIn("neutral range", evidence.explanation)

    def test_threshold_boundaries_are_classified_consistently(self):
        expectations = (
            (20.0, SignalDirection.BULLISH, SignalStrength.STRONG),
            (30.0, SignalDirection.BULLISH, SignalStrength.MODERATE),
            (30.0001, SignalDirection.NEUTRAL, SignalStrength.WEAK),
            (69.9999, SignalDirection.NEUTRAL, SignalStrength.WEAK),
            (70.0, SignalDirection.BEARISH, SignalStrength.MODERATE),
            (80.0, SignalDirection.BEARISH, SignalStrength.STRONG),
        )

        for value, direction, strength in expectations:
            with self.subTest(value=value):
                evidence = self.signal_for(value)
                self.assertEqual(evidence.direction, direction)
                self.assertEqual(evidence.strength, strength)

    def test_accepts_zero_and_one_hundred_rsi_boundaries(self):
        zero = self.signal_for(0.0)
        hundred = self.signal_for(100.0)

        self.assertEqual(zero.direction, SignalDirection.BULLISH)
        self.assertEqual(zero.strength, SignalStrength.STRONG)
        self.assertEqual(hundred.direction, SignalDirection.BEARISH)
        self.assertEqual(hundred.strength, SignalStrength.STRONG)

    def test_supports_custom_thresholds(self):
        evidence = self.signal_for(
            35.0,
            extreme_oversold_threshold=25.0,
            oversold_threshold=40.0,
            overbought_threshold=60.0,
            extreme_overbought_threshold=75.0,
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(evidence.parameters["oversold_threshold"], 40.0)

    def test_uses_latest_available_indicator_point(self):
        series = self.build_series(values=(25.0, 50.0, 75.0))

        evidence = generate_rsi_mean_reversion_signal(series)

        self.assertEqual(evidence.observed_at, self.timestamps[-1])
        self.assertEqual(evidence.direction, SignalDirection.BEARISH)

    def test_as_of_excludes_later_indicator_points(self):
        series = self.build_series(values=(25.0, 50.0, 75.0))

        evidence = generate_rsi_mean_reversion_signal(
            series,
            as_of=self.timestamps[0],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[0])
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)

    def test_as_of_after_latest_point_uses_latest_available_data(self):
        series = self.build_series(values=(25.0, 50.0, 75.0))

        evidence = generate_rsi_mean_reversion_signal(
            series,
            as_of=self.timestamps[-1] + timedelta(days=10),
        )

        self.assertEqual(evidence.observed_at, self.timestamps[-1])

    def test_rejects_as_of_before_first_indicator_point(self):
        series = self.build_series()

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires an available indicator point",
        ):
            generate_rsi_mean_reversion_signal(
                series,
                as_of=self.first_timestamp - timedelta(seconds=1),
            )

    def test_rejects_invalid_as_of_time(self):
        series = self.build_series()
        invalid_times = (datetime(2026, 8, 1), "2026-08-01", 1)

        for as_of in invalid_times:
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    generate_rsi_mean_reversion_signal(
                        series,
                        as_of=as_of,
                    )

    def test_rejects_non_rsi_indicator(self):
        series = self.build_series(indicator="ADX")

        with self.assertRaisesRegex(
            ValueError,
            "requires an RSI indicator series",
        ):
            generate_rsi_mean_reversion_signal(series)

    def test_accepts_case_insensitive_rsi_indicator_name(self):
        series = self.build_series(indicator="rsi")

        evidence = generate_rsi_mean_reversion_signal(series)

        self.assertEqual(evidence.observed_values["rsi"], 25.0)

    def test_rejects_multi_field_indicator_disguised_as_rsi(self):
        series = self.build_series(
            price_field=None,
            input_fields=(PriceField.HIGH, PriceField.LOW),
        )

        with self.assertRaisesRegex(
            ValueError,
            "requires one price field",
        ):
            generate_rsi_mean_reversion_signal(series)

    def test_rejects_missing_or_invalid_period(self):
        series = self.build_series()
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
                invalid = series.model_copy(
                    update={"parameters": parameters}
                )
                with self.assertRaises(ValueError):
                    generate_rsi_mean_reversion_signal(invalid)

    def test_rejects_rsi_value_outside_bounded_range(self):
        series = self.build_series()

        for value in (-0.0001, 100.0001):
            with self.subTest(value=value):
                point = series.points[-1].model_copy(
                    update={"value": value}
                )
                invalid = series.model_copy(
                    update={"points": series.points[:-1] + [point]}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "must be between 0 and 100",
                ):
                    generate_rsi_mean_reversion_signal(invalid)

    def test_rejects_invalid_threshold_values_for_every_band(self):
        series = self.build_series()
        threshold_fields = (
            "extreme_oversold_threshold",
            "oversold_threshold",
            "overbought_threshold",
            "extreme_overbought_threshold",
        )
        invalid_values = (
            True,
            "30",
            None,
            -0.1,
            100.1,
            float("nan"),
            float("inf"),
        )

        for field_name in threshold_fields:
            for value in invalid_values:
                with self.subTest(field_name=field_name, value=value):
                    with self.assertRaises(ValueError):
                        generate_rsi_mean_reversion_signal(
                            series,
                            **{field_name: value},
                        )

    def test_rejects_equal_or_reversed_threshold_order(self):
        series = self.build_series()
        invalid_configurations = (
            {"extreme_oversold_threshold": 30.0},
            {"oversold_threshold": 70.0},
            {"overbought_threshold": 30.0},
            {"extreme_overbought_threshold": 70.0},
        )

        for configuration in invalid_configurations:
            with self.subTest(configuration=configuration):
                with self.assertRaisesRegex(
                    ValueError,
                    "thresholds must satisfy",
                ):
                    generate_rsi_mean_reversion_signal(
                        series,
                        **configuration,
                    )

    def test_accepts_actual_talib_rsi_output(self):
        candles = [
            Candle(
                timestamp=self.first_timestamp + timedelta(days=index),
                open=float(100 + index),
                high=float(101 + index),
                low=float(99 + index),
                close=float(100 + index),
                volume=1_000 + index,
            )
            for index in range(20)
        ]
        market_series = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=candles,
            retrieved_at=candles[-1].timestamp,
        )
        rsi = calculate_rsi(market_series)

        evidence = generate_rsi_mean_reversion_signal(rsi)

        self.assertEqual(evidence.observed_values["rsi"], 100.0)
        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(evidence.observed_at, candles[-1].timestamp)


if __name__ == "__main__":
    unittest.main()
