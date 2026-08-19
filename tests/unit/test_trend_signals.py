import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.indicators import calculate_ema
from app.analytics.trend_signals import (
    generate_moving_average_alignment_signal,
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


class MovingAverageAlignmentSignalTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.timestamps = [
            self.first_timestamp + timedelta(days=day)
            for day in range(3)
        ]

    def build_series(
        self,
        *,
        indicator,
        period,
        values,
        timestamps=None,
        **overrides,
    ):
        values_by_field = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "indicator": indicator,
            "price_field": PriceField.CLOSE,
            "parameters": {"period": period},
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

    def build_pair(
        self,
        *,
        fast_value=102.0,
        slow_value=100.0,
        fast_indicator="EMA",
        slow_indicator="EMA",
    ):
        fast = self.build_series(
            indicator=fast_indicator,
            period=20,
            values=(99.0, 100.0, fast_value),
        )
        slow = self.build_series(
            indicator=slow_indicator,
            period=50,
            values=(100.0, 100.0, slow_value),
        )
        return fast, slow

    def test_generates_strong_bullish_alignment_evidence(self):
        fast, slow = self.build_pair()

        evidence = generate_moving_average_alignment_signal(fast, slow)

        self.assertEqual(evidence.category, SignalCategory.TREND)
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(
            evidence.evidence_id,
            "ma_alignment.ema20.ema50.close",
        )
        self.assertEqual(evidence.observed_at, self.timestamps[-1])
        self.assertEqual(evidence.available_at, self.timestamps[-1])
        self.assertEqual(evidence.observed_values["fast_value"], 102.0)
        self.assertEqual(evidence.observed_values["slow_value"], 100.0)
        self.assertEqual(evidence.observed_values["relationship"], "above")
        self.assertEqual(
            evidence.observed_values["separation_percentage"],
            2.0,
        )

    def test_accepts_actual_talib_moving_average_outputs(self):
        candles = [
            Candle(
                timestamp=self.first_timestamp + timedelta(days=index),
                open=float(100 + index),
                high=float(101 + index),
                low=float(99 + index),
                close=float(100 + index),
                volume=1_000 + index,
            )
            for index in range(60)
        ]
        market_series = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=candles,
            retrieved_at=candles[-1].timestamp,
        )
        fast = calculate_ema(market_series, period=20)
        slow = calculate_ema(market_series, period=50)

        evidence = generate_moving_average_alignment_signal(fast, slow)

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.observed_at, candles[-1].timestamp)
        self.assertEqual(evidence.observed_values["fast_period"], 20)
        self.assertEqual(evidence.observed_values["slow_period"], 50)

    def test_generates_bearish_alignment_evidence(self):
        fast, slow = self.build_pair(fast_value=98.0)

        evidence = generate_moving_average_alignment_signal(fast, slow)

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(evidence.observed_values["relationship"], "below")

    def test_classifies_values_inside_tolerance_as_neutral(self):
        fast, slow = self.build_pair(fast_value=100.04)

        evidence = generate_moving_average_alignment_signal(fast, slow)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["relationship"],
            "aligned with",
        )

    def test_tolerance_boundary_is_inclusive(self):
        fast, slow = self.build_pair(fast_value=100.05)

        evidence = generate_moving_average_alignment_signal(fast, slow)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)

    def test_classifies_directional_strength_thresholds(self):
        expectations = (
            (100.1, SignalStrength.WEAK),
            (100.5, SignalStrength.MODERATE),
            (101.49, SignalStrength.MODERATE),
            (101.5, SignalStrength.STRONG),
        )

        for fast_value, expected_strength in expectations:
            with self.subTest(fast_value=fast_value):
                fast, slow = self.build_pair(fast_value=fast_value)
                evidence = generate_moving_average_alignment_signal(
                    fast,
                    slow,
                )
                self.assertEqual(evidence.strength, expected_strength)

    def test_supports_mixed_sma_and_ema_series(self):
        fast, slow = self.build_pair(
            fast_indicator="EMA",
            slow_indicator="SMA",
        )

        evidence = generate_moving_average_alignment_signal(fast, slow)

        self.assertEqual(
            evidence.evidence_id,
            "ma_alignment.ema20.sma50.close",
        )
        self.assertEqual(evidence.observed_values["fast_indicator"], "EMA")
        self.assertEqual(evidence.observed_values["slow_indicator"], "SMA")

    def test_uses_latest_common_available_timestamp(self):
        fast = self.build_series(
            indicator="EMA",
            period=20,
            values=(99.0, 101.0, 105.0),
        )
        slow = self.build_series(
            indicator="EMA",
            period=50,
            values=(100.0, 100.0),
            timestamps=self.timestamps[:2],
        )

        evidence = generate_moving_average_alignment_signal(fast, slow)

        self.assertEqual(evidence.observed_at, self.timestamps[1])
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.observed_values["fast_value"], 101.0)

    def test_as_of_excludes_later_indicator_points(self):
        fast = self.build_series(
            indicator="EMA",
            period=20,
            values=(99.0, 101.0, 105.0),
        )
        slow = self.build_series(
            indicator="EMA",
            period=50,
            values=(100.0, 100.0, 100.0),
        )

        evidence = generate_moving_average_alignment_signal(
            fast,
            slow,
            as_of=self.timestamps[0],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[0])
        self.assertEqual(evidence.direction, SignalDirection.BEARISH)

    def test_as_of_after_latest_point_uses_latest_available_data(self):
        fast, slow = self.build_pair()

        evidence = generate_moving_average_alignment_signal(
            fast,
            slow,
            as_of=self.timestamps[-1] + timedelta(days=10),
        )

        self.assertEqual(evidence.observed_at, self.timestamps[-1])

    def test_rejects_invalid_as_of_time(self):
        fast, slow = self.build_pair()
        invalid_times = (datetime(2026, 8, 1), "2026-08-01", 1)

        for as_of in invalid_times:
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    generate_moving_average_alignment_signal(
                        fast,
                        slow,
                        as_of=as_of,
                    )

    def test_rejects_as_of_before_common_indicator_data(self):
        fast, slow = self.build_pair()

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires a common available point",
        ):
            generate_moving_average_alignment_signal(
                fast,
                slow,
                as_of=self.first_timestamp - timedelta(seconds=1),
            )

    def test_rejects_series_without_common_timestamp(self):
        fast = self.build_series(
            indicator="EMA",
            period=20,
            values=(101.0, 102.0),
            timestamps=self.timestamps[:2],
        )
        slow = self.build_series(
            indicator="EMA",
            period=50,
            values=(100.0,),
            timestamps=[self.timestamps[2]],
        )

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires a common available point",
        ):
            generate_moving_average_alignment_signal(fast, slow)

    def test_rejects_non_moving_average_indicator(self):
        fast, slow = self.build_pair()
        invalid_fast = fast.model_copy(update={"indicator": "RSI"})

        with self.assertRaisesRegex(
            ValueError,
            "fast series must be an SMA or EMA",
        ):
            generate_moving_average_alignment_signal(invalid_fast, slow)

    def test_rejects_multi_field_indicator_disguised_as_average(self):
        fast, slow = self.build_pair()
        invalid_fast = self.build_series(
            indicator="EMA",
            period=20,
            values=(99.0, 100.0, 102.0),
            price_field=None,
            input_fields=(PriceField.HIGH, PriceField.LOW),
        )

        with self.assertRaisesRegex(
            ValueError,
            "requires one price field",
        ):
            generate_moving_average_alignment_signal(invalid_fast, slow)

    def test_rejects_missing_or_invalid_period(self):
        fast, slow = self.build_pair()
        invalid_parameters = (
            {},
            {"period": True},
            {"period": 20.0},
            {"period": "20"},
            {"period": 1},
            {"period": 0},
        )

        for parameters in invalid_parameters:
            with self.subTest(parameters=parameters):
                invalid_fast = fast.model_copy(
                    update={"parameters": parameters}
                )
                with self.assertRaises(ValueError):
                    generate_moving_average_alignment_signal(
                        invalid_fast,
                        slow,
                    )

    def test_rejects_fast_period_not_below_slow_period(self):
        fast, slow = self.build_pair()

        for period in (50, 60):
            with self.subTest(period=period):
                invalid_fast = fast.model_copy(
                    update={"parameters": {"period": period}}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "fast moving-average period must be below",
                ):
                    generate_moving_average_alignment_signal(
                        invalid_fast,
                        slow,
                    )

    def test_rejects_mismatched_series_identity(self):
        fast, slow = self.build_pair()
        mismatches = (
            {"exchange": "BSE"},
            {"symbol_token": "9999"},
            {"symbol": "TCS-EQ"},
            {"interval": "ONE_WEEK"},
            {"price_field": PriceField.OPEN},
        )

        for update in mismatches:
            with self.subTest(update=update):
                mismatched_slow = slow.model_copy(update=update)
                with self.assertRaisesRegex(
                    ValueError,
                    "same instrument, timeframe, and price field",
                ):
                    generate_moving_average_alignment_signal(
                        fast,
                        mismatched_slow,
                    )

    def test_rejects_negative_moving_average_value(self):
        fast, slow = self.build_pair()
        invalid_point = fast.points[-1].model_copy(update={"value": -1.0})
        invalid_fast = fast.model_copy(
            update={"points": fast.points[:-1] + [invalid_point]}
        )

        with self.assertRaisesRegex(
            ValueError,
            "values cannot be negative",
        ):
            generate_moving_average_alignment_signal(invalid_fast, slow)

    def test_handles_zero_reference_without_division_error(self):
        fast, slow = self.build_pair(fast_value=0.0, slow_value=0.0)

        neutral = generate_moving_average_alignment_signal(fast, slow)

        self.assertEqual(neutral.direction, SignalDirection.NEUTRAL)
        self.assertEqual(
            neutral.observed_values["separation_percentage"],
            0.0,
        )

        fast, slow = self.build_pair(fast_value=1.0, slow_value=0.0)
        bullish = generate_moving_average_alignment_signal(fast, slow)

        self.assertEqual(bullish.direction, SignalDirection.BULLISH)
        self.assertEqual(bullish.strength, SignalStrength.STRONG)
        self.assertEqual(
            bullish.observed_values["separation_percentage"],
            100.0,
        )

    def test_supports_custom_threshold_configuration(self):
        fast, slow = self.build_pair(fast_value=101.0)

        evidence = generate_moving_average_alignment_signal(
            fast,
            slow,
            equality_tolerance_percentage=0.1,
            moderate_threshold_percentage=1.5,
            strong_threshold_percentage=3.0,
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.parameters["moderate_threshold_percentage"],
            1.5,
        )

    def test_rejects_invalid_threshold_values(self):
        fast, slow = self.build_pair()
        invalid_values = (
            True,
            "0.5",
            None,
            -0.1,
            float("nan"),
            float("inf"),
        )

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    generate_moving_average_alignment_signal(
                        fast,
                        slow,
                        equality_tolerance_percentage=value,
                    )

    def test_rejects_overlapping_threshold_ranges(self):
        fast, slow = self.build_pair()

        with self.assertRaisesRegex(
            ValueError,
            "moderate threshold must exceed equality tolerance",
        ):
            generate_moving_average_alignment_signal(
                fast,
                slow,
                equality_tolerance_percentage=0.5,
                moderate_threshold_percentage=0.5,
            )

        with self.assertRaisesRegex(
            ValueError,
            "strong threshold must exceed moderate threshold",
        ):
            generate_moving_average_alignment_signal(
                fast,
                slow,
                moderate_threshold_percentage=1.5,
                strong_threshold_percentage=1.5,
            )


if __name__ == "__main__":
    unittest.main()
