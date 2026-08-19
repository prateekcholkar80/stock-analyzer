import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.structure_breaks import detect_structure_breaks
from app.models.market import Candle, HistoricalCandleSeries
from app.models.price_action import (
    MarketStructureBias,
    StructureBreakDirection,
    StructureBreakType,
    SwingPivot,
    SwingPivotDetectionResult,
    SwingPivotType,
)


class StructureBreakDetectionTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.retrieved_at = datetime(2026, 8, 30, tzinfo=UTC)
        closes = (
            105.0, 106.0, 108.0, 102.0, 107.0,
            108.0, 112.0, 105.0, 110.0, 114.0,
            116.0, 104.0, 102.0, 101.0, 100.0,
            108.0, 102.0, 100.0, 97.0, 113.0,
        )
        highs = (
            107.0, 108.0, 113.0, 104.0, 109.0,
            110.0, 115.0, 107.0, 112.0, 115.0,
            117.0, 106.0, 104.0, 103.0, 102.0,
            112.0, 104.0, 102.0, 100.0, 114.0,
        )
        lows = (
            103.0, 104.0, 106.0, 100.0, 105.0,
            106.0, 109.0, 103.0, 107.0, 112.0,
            114.0, 103.0, 101.0, 99.0, 98.0,
            100.0, 100.0, 99.0, 96.0, 111.0,
        )
        self.candles = [
            Candle(
                timestamp=self.first_timestamp + timedelta(days=index),
                open=close,
                high=high,
                low=low,
                close=close,
                volume=1_000 + index,
            )
            for index, (close, high, low) in enumerate(
                zip(closes, highs, lows)
            )
        ]
        self.series = self.build_series(self.candles)
        self.pivots = [
            self.build_pivot(SwingPivotType.HIGH, 2, 4),
            self.build_pivot(SwingPivotType.LOW, 3, 5),
            self.build_pivot(SwingPivotType.HIGH, 6, 8),
            self.build_pivot(SwingPivotType.LOW, 7, 9),
            self.build_pivot(SwingPivotType.LOW, 14, 16),
            self.build_pivot(SwingPivotType.HIGH, 15, 17),
        ]
        self.pivot_result = self.build_pivot_result(self.pivots)

    def build_series(self, candles, **overrides):
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "candles": candles,
            "retrieved_at": self.retrieved_at,
        }
        values.update(overrides)

        return HistoricalCandleSeries(**values)

    def build_pivot(self, pivot_type, pivot_index, confirmation_index):
        candle = self.candles[pivot_index]
        price = (
            candle.high
            if pivot_type is SwingPivotType.HIGH
            else candle.low
        )
        return SwingPivot(
            pivot_type=pivot_type,
            pivot_at=candle.timestamp,
            confirmed_at=self.candles[confirmation_index].timestamp,
            price=price,
            left_strength=2,
            right_strength=2,
        )

    def build_pivot_result(self, pivots, **overrides):
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "source": "angel_one",
            "source_retrieved_at": self.retrieved_at,
            "left_strength": 2,
            "right_strength": 2,
            "pivots": pivots,
        }
        values.update(overrides)

        return SwingPivotDetectionResult(**values)

    def test_detects_bos_and_choch_in_both_directions(self):
        result = detect_structure_breaks(
            self.series,
            self.pivot_result,
        )

        self.assertEqual(
            [event.break_type for event in result.events],
            [
                StructureBreakType.BREAK_OF_STRUCTURE,
                StructureBreakType.CHANGE_OF_CHARACTER,
                StructureBreakType.BREAK_OF_STRUCTURE,
                StructureBreakType.CHANGE_OF_CHARACTER,
            ],
        )
        self.assertEqual(
            [event.direction for event in result.events],
            [
                StructureBreakDirection.BULLISH,
                StructureBreakDirection.BEARISH,
                StructureBreakDirection.BEARISH,
                StructureBreakDirection.BULLISH,
            ],
        )

    def test_records_close_crossing_and_prior_bias(self):
        result = detect_structure_breaks(
            self.series,
            self.pivot_result,
        )
        first = result.events[0]

        self.assertEqual(first.occurred_at, self.candles[10].timestamp)
        self.assertEqual(first.previous_close, 114.0)
        self.assertEqual(first.close_price, 116.0)
        self.assertEqual(first.broken_pivot, self.pivots[2])
        self.assertEqual(first.bias_before, MarketStructureBias.BULLISH)

    def test_does_not_repeat_break_for_same_pivot(self):
        result = detect_structure_breaks(
            self.series,
            self.pivot_result,
        )

        broken_pivots = [
            (event.broken_pivot.pivot_type, event.broken_pivot.pivot_at)
            for event in result.events
        ]
        self.assertEqual(len(broken_pivots), len(set(broken_pivots)))

    def test_wick_without_close_above_level_is_not_a_break(self):
        candles = self.candles[:10] + [
            self.candles[10].model_copy(update={"close": 114.0})
        ]
        series = self.build_series(candles)

        result = detect_structure_breaks(
            series,
            self.build_pivot_result(self.pivots[:4]),
            as_of=candles[-1].timestamp,
        )

        self.assertEqual(result.events, [])

    def test_marks_break_without_established_bias_as_unclassified(self):
        candles = self.candles[:6] + [
            self.candles[6].model_copy(update={"close": 114.0})
        ]
        series = self.build_series(candles)

        result = detect_structure_breaks(
            series,
            self.build_pivot_result(self.pivots[:2]),
            as_of=candles[-1].timestamp,
        )

        self.assertEqual(len(result.events), 1)
        self.assertEqual(
            result.events[0].break_type,
            StructureBreakType.UNCLASSIFIED,
        )
        self.assertEqual(
            result.events[0].bias_before,
            MarketStructureBias.UNDETERMINED,
        )

    def test_as_of_excludes_future_break_events(self):
        result = detect_structure_breaks(
            self.series,
            self.pivot_result,
            as_of=self.candles[10].timestamp,
        )

        self.assertEqual(len(result.events), 1)
        self.assertEqual(
            result.events[0].break_type,
            StructureBreakType.BREAK_OF_STRUCTURE,
        )
        self.assertEqual(result.evaluated_at, self.candles[10].timestamp)

    def test_preserves_source_and_configuration_metadata(self):
        result = detect_structure_breaks(
            self.series,
            self.pivot_result,
        )

        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertEqual(result.source, "angel_one")
        self.assertEqual(result.source_retrieved_at, self.retrieved_at)
        self.assertEqual(result.pivot_left_strength, 2)
        self.assertEqual(result.pivot_right_strength, 2)
        self.assertEqual(
            result.structure_equality_tolerance_percentage,
            0.1,
        )

    def test_rejects_market_series_for_different_instrument(self):
        mismatched = self.build_series(
            self.candles,
            symbol_token="9999",
        )

        with self.assertRaisesRegex(
            ValueError,
            "must describe the same source",
        ):
            detect_structure_breaks(mismatched, self.pivot_result)

    def test_rejects_pivot_price_not_matching_candle(self):
        invalid_pivot = self.pivots[0].model_copy(
            update={"price": 999.0}
        )
        invalid_result = self.build_pivot_result(
            [invalid_pivot] + self.pivots[1:]
        )

        with self.assertRaisesRegex(
            ValueError,
            "price does not match market candle",
        ):
            detect_structure_breaks(self.series, invalid_result)

    def test_rejects_candles_out_of_order(self):
        unordered = list(self.candles)
        unordered[5], unordered[6] = unordered[6], unordered[5]

        with self.assertRaisesRegex(
            ValueError,
            "historical candles must have unique timestamps",
        ):
            detect_structure_breaks(
                self.build_series(unordered),
                self.pivot_result,
            )

    def test_rejects_invalid_tolerance(self):
        for tolerance in (True, "0.1", None, -0.1, float("inf")):
            with self.subTest(tolerance=tolerance):
                with self.assertRaises(ValueError):
                    detect_structure_breaks(
                        self.series,
                        self.pivot_result,
                        equality_tolerance_percentage=tolerance,
                    )

    def test_rejects_invalid_as_of_time(self):
        invalid_times = (
            datetime(2026, 8, 10),
            self.retrieved_at + timedelta(days=1),
            "2026-08-10",
        )

        for as_of in invalid_times:
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    detect_structure_breaks(
                        self.series,
                        self.pivot_result,
                        as_of=as_of,
                    )


if __name__ == "__main__":
    unittest.main()
