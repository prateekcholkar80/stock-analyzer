import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.market_structure import analyze_market_structure
from app.models.price_action import (
    MarketStructureBias,
    MarketStructureClassification,
    SwingPivot,
    SwingPivotDetectionResult,
    SwingPivotType,
)


class MarketStructureAnalysisTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.retrieved_at = datetime(2026, 8, 30, tzinfo=UTC)
        self.pivots = [
            self.build_pivot(SwingPivotType.HIGH, 0, 2, 110.0),
            self.build_pivot(SwingPivotType.LOW, 1, 3, 100.0),
            self.build_pivot(SwingPivotType.HIGH, 4, 6, 115.0),
            self.build_pivot(SwingPivotType.LOW, 5, 7, 103.0),
            self.build_pivot(SwingPivotType.HIGH, 8, 10, 112.0),
            self.build_pivot(SwingPivotType.LOW, 9, 11, 98.0),
        ]
        self.pivot_result = self.build_result(self.pivots)

    def build_pivot(
        self,
        pivot_type,
        pivot_day,
        confirmation_day,
        price,
    ):
        return SwingPivot(
            pivot_type=pivot_type,
            pivot_at=self.first_timestamp + timedelta(days=pivot_day),
            confirmed_at=(
                self.first_timestamp + timedelta(days=confirmation_day)
            ),
            price=price,
            left_strength=2,
            right_strength=2,
        )

    def build_result(self, pivots):
        return SwingPivotDetectionResult(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            source="angel_one",
            source_retrieved_at=self.retrieved_at,
            left_strength=2,
            right_strength=2,
            pivots=pivots,
        )

    def test_classifies_higher_and_lower_market_structure(self):
        result = analyze_market_structure(self.pivot_result)

        self.assertEqual(
            [point.classification for point in result.points],
            [
                MarketStructureClassification.HIGHER_HIGH,
                MarketStructureClassification.HIGHER_LOW,
                MarketStructureClassification.LOWER_HIGH,
                MarketStructureClassification.LOWER_LOW,
            ],
        )

    def test_computes_latest_bearish_bias(self):
        result = analyze_market_structure(self.pivot_result)

        self.assertEqual(result.bias, MarketStructureBias.BEARISH)

    def test_as_of_returns_only_confirmed_bullish_structure(self):
        result = analyze_market_structure(
            self.pivot_result,
            as_of=self.pivots[3].confirmed_at,
        )

        self.assertEqual(len(result.points), 2)
        self.assertEqual(result.bias, MarketStructureBias.BULLISH)
        self.assertEqual(result.evaluated_at, self.pivots[3].confirmed_at)

    def test_returns_undetermined_until_both_comparisons_exist(self):
        result = analyze_market_structure(
            self.pivot_result,
            as_of=self.pivots[1].confirmed_at,
        )

        self.assertEqual(result.points, [])
        self.assertEqual(result.bias, MarketStructureBias.UNDETERMINED)

    def test_classifies_equal_highs_and_lows_within_tolerance(self):
        pivots = [
            self.build_pivot(SwingPivotType.HIGH, 0, 2, 100.0),
            self.build_pivot(SwingPivotType.LOW, 1, 3, 90.0),
            self.build_pivot(SwingPivotType.HIGH, 4, 6, 100.05),
            self.build_pivot(SwingPivotType.LOW, 5, 7, 89.95),
        ]

        result = analyze_market_structure(self.build_result(pivots))

        self.assertEqual(
            [point.classification for point in result.points],
            [
                MarketStructureClassification.EQUAL_HIGH,
                MarketStructureClassification.EQUAL_LOW,
            ],
        )
        self.assertEqual(result.bias, MarketStructureBias.RANGE_BOUND)

    def test_custom_zero_tolerance_requires_exact_equality(self):
        pivots = [
            self.build_pivot(SwingPivotType.HIGH, 0, 2, 100.0),
            self.build_pivot(SwingPivotType.HIGH, 4, 6, 100.05),
        ]

        result = analyze_market_structure(
            self.build_result(pivots),
            equality_tolerance_percentage=0.0,
        )

        self.assertEqual(
            result.points[0].classification,
            MarketStructureClassification.HIGHER_HIGH,
        )

    def test_preserves_source_and_configuration_metadata(self):
        result = analyze_market_structure(self.pivot_result)

        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertEqual(result.source, "angel_one")
        self.assertEqual(result.source_retrieved_at, self.retrieved_at)
        self.assertEqual(result.evaluated_at, self.retrieved_at)
        self.assertEqual(result.pivot_left_strength, 2)
        self.assertEqual(result.pivot_right_strength, 2)
        self.assertEqual(result.equality_tolerance_percentage, 0.1)

    def test_rejects_invalid_tolerance(self):
        for tolerance in (True, "0.1", None, -0.1, float("inf")):
            with self.subTest(tolerance=tolerance):
                with self.assertRaises(ValueError):
                    analyze_market_structure(
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
                    analyze_market_structure(
                        self.pivot_result,
                        as_of=as_of,
                    )


if __name__ == "__main__":
    unittest.main()
