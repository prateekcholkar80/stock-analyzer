import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.swing_pivots import detect_swing_pivots
from app.models.market import Candle, HistoricalCandleSeries
from app.models.price_action import SwingPivotType


class SwingPivotDetectionTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        highs = (10.0, 12.0, 15.0, 13.0, 11.0, 12.0, 13.0, 14.0, 13.0)
        lows = (8.0, 9.0, 10.0, 9.0, 5.0, 8.0, 9.0, 10.0, 9.0)
        self.candles = self.build_candles(highs, lows)
        self.series = self.build_series(self.candles)

    def build_candles(self, highs, lows):
        return [
            Candle(
                timestamp=self.first_timestamp + timedelta(days=index),
                open=(high + low) / 2,
                high=high,
                low=low,
                close=(high + low) / 2,
                volume=1_000 + index,
            )
            for index, (high, low) in enumerate(zip(highs, lows))
        ]

    def build_series(self, candles):
        return HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=candles,
            retrieved_at=datetime(2026, 8, 19, tzinfo=UTC),
        )

    def test_detects_confirmed_swing_high_and_low(self):
        result = detect_swing_pivots(self.series)

        self.assertEqual(
            [pivot.pivot_type for pivot in result.pivots],
            [SwingPivotType.HIGH, SwingPivotType.LOW],
        )
        self.assertEqual(
            [pivot.price for pivot in result.pivots],
            [15.0, 5.0],
        )
        self.assertEqual(
            [pivot.pivot_at for pivot in result.pivots],
            [self.candles[2].timestamp, self.candles[4].timestamp],
        )

    def test_records_confirmation_after_right_side_window(self):
        result = detect_swing_pivots(self.series)

        self.assertEqual(
            [pivot.confirmed_at for pivot in result.pivots],
            [self.candles[4].timestamp, self.candles[6].timestamp],
        )

    def test_preserves_source_metadata_and_strengths(self):
        result = detect_swing_pivots(self.series)

        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertEqual(result.source, "angel_one")
        self.assertEqual(result.source_retrieved_at, self.series.retrieved_at)
        self.assertEqual(result.left_strength, 2)
        self.assertEqual(result.right_strength, 2)
        for pivot in result.pivots:
            self.assertEqual(pivot.left_strength, 2)
            self.assertEqual(pivot.right_strength, 2)

    def test_supports_custom_asymmetric_strengths(self):
        highs = (10.0, 15.0, 12.0, 11.0)
        lows = (7.0, 9.0, 8.0, 7.5)
        series = self.build_series(self.build_candles(highs, lows))

        result = detect_swing_pivots(
            series,
            left_strength=1,
            right_strength=2,
        )

        self.assertEqual(len(result.pivots), 1)
        self.assertEqual(result.pivots[0].pivot_type, SwingPivotType.HIGH)
        self.assertEqual(
            result.pivots[0].pivot_at,
            series.candles[1].timestamp,
        )
        self.assertEqual(
            result.pivots[0].confirmed_at,
            series.candles[3].timestamp,
        )

    def test_does_not_emit_pivot_before_confirmation_candle_exists(self):
        incomplete = self.build_series(self.candles[:4])
        completed = self.build_series(self.candles[:5])

        incomplete_result = detect_swing_pivots(incomplete)
        completed_result = detect_swing_pivots(completed)

        self.assertEqual(incomplete_result.pivots, [])
        self.assertEqual(len(completed_result.pivots), 1)
        self.assertEqual(
            completed_result.pivots[0].pivot_type,
            SwingPivotType.HIGH,
        )

    def test_ignores_tied_neighboring_extremes(self):
        highs = (10.0, 12.0, 12.0, 11.0, 10.0)
        lows = (5.0, 6.0, 7.0, 8.0, 9.0)
        tied_series = self.build_series(
            self.build_candles(highs, lows)
        )

        result = detect_swing_pivots(tied_series)

        self.assertEqual(result.pivots, [])

    def test_returns_empty_result_when_window_cannot_be_formed(self):
        short_series = self.build_series(self.candles[:4])

        result = detect_swing_pivots(short_series)

        self.assertEqual(result.pivots, [])
        self.assertEqual(result.left_strength, 2)
        self.assertEqual(result.right_strength, 2)

    def test_rejects_invalid_strengths(self):
        invalid_arguments = (
            {"left_strength": True},
            {"left_strength": 0},
            {"left_strength": 2.5},
            {"right_strength": "2"},
            {"right_strength": -1},
        )

        for arguments in invalid_arguments:
            with self.subTest(arguments=arguments):
                with self.assertRaises(ValueError):
                    detect_swing_pivots(self.series, **arguments)

    def test_rejects_candles_out_of_order(self):
        unordered = list(self.candles)
        unordered[3], unordered[4] = unordered[4], unordered[3]

        with self.assertRaisesRegex(
            ValueError,
            "historical candles must have unique timestamps",
        ):
            detect_swing_pivots(self.build_series(unordered))


if __name__ == "__main__":
    unittest.main()
