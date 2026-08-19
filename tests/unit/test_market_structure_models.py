import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.models.price_action import (
    MarketStructureAnalysisResult,
    MarketStructureBias,
    MarketStructureClassification,
    MarketStructurePoint,
    SwingPivot,
    SwingPivotType,
)


class MarketStructurePointModelTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.reference = self.build_pivot(0, 2, 100.0)
        self.current = self.build_pivot(3, 5, 105.0)

    def build_pivot(self, pivot_day, confirmation_day, price, **overrides):
        values = {
            "pivot_type": SwingPivotType.HIGH,
            "pivot_at": self.first_timestamp
            + timedelta(days=pivot_day),
            "confirmed_at": self.first_timestamp
            + timedelta(days=confirmation_day),
            "price": price,
            "left_strength": 2,
            "right_strength": 2,
        }
        values.update(overrides)

        return SwingPivot(**values)

    def build_point(self, **overrides):
        values = {
            "classification": (
                MarketStructureClassification.HIGHER_HIGH
            ),
            "pivot": self.current,
            "reference_pivot": self.reference,
            "equality_tolerance_percentage": 0.1,
        }
        values.update(overrides)

        return MarketStructurePoint(**values)

    def test_builds_point_with_computed_price_change(self):
        point = self.build_point()

        self.assertEqual(point.price_change, 5.0)
        self.assertEqual(point.price_change_percentage, 5.0)

    def test_rejects_pivots_of_different_types(self):
        low_reference = self.build_pivot(
            0,
            2,
            100.0,
            pivot_type=SwingPivotType.LOW,
        )

        with self.assertRaisesRegex(
            ValidationError,
            "pivots must have the same type",
        ):
            self.build_point(reference_pivot=low_reference)

    def test_rejects_reference_that_does_not_precede_pivot(self):
        with self.assertRaisesRegex(
            ValidationError,
            "reference must precede current pivot",
        ):
            self.build_point(
                reference_pivot=self.current,
                pivot=self.reference,
            )

    def test_rejects_classification_inconsistent_with_prices(self):
        with self.assertRaisesRegex(
            ValidationError,
            "classification does not match prices",
        ):
            self.build_point(
                classification=(
                    MarketStructureClassification.LOWER_HIGH
                )
            )

    def test_classifies_price_within_tolerance_as_equal(self):
        current = self.build_pivot(3, 5, 100.05)

        point = self.build_point(
            classification=MarketStructureClassification.EQUAL_HIGH,
            pivot=current,
        )

        self.assertEqual(
            point.classification,
            MarketStructureClassification.EQUAL_HIGH,
        )


class MarketStructureAnalysisResultModelTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.retrieved_at = datetime(2026, 8, 20, tzinfo=UTC)
        self.high_point = self.build_point(
            SwingPivotType.HIGH,
            0,
            2,
            100.0,
            3,
            5,
            105.0,
            MarketStructureClassification.HIGHER_HIGH,
        )
        self.low_point = self.build_point(
            SwingPivotType.LOW,
            1,
            3,
            90.0,
            4,
            6,
            95.0,
            MarketStructureClassification.HIGHER_LOW,
        )

    def build_point(
        self,
        pivot_type,
        reference_day,
        reference_confirmation_day,
        reference_price,
        pivot_day,
        pivot_confirmation_day,
        pivot_price,
        classification,
    ):
        reference = SwingPivot(
            pivot_type=pivot_type,
            pivot_at=self.first_timestamp
            + timedelta(days=reference_day),
            confirmed_at=self.first_timestamp
            + timedelta(days=reference_confirmation_day),
            price=reference_price,
            left_strength=2,
            right_strength=2,
        )
        pivot = SwingPivot(
            pivot_type=pivot_type,
            pivot_at=self.first_timestamp + timedelta(days=pivot_day),
            confirmed_at=self.first_timestamp
            + timedelta(days=pivot_confirmation_day),
            price=pivot_price,
            left_strength=2,
            right_strength=2,
        )
        return MarketStructurePoint(
            classification=classification,
            pivot=pivot,
            reference_pivot=reference,
            equality_tolerance_percentage=0.1,
        )

    def build_result(self, **overrides):
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "source": "angel_one",
            "source_retrieved_at": self.retrieved_at,
            "evaluated_at": self.retrieved_at,
            "pivot_left_strength": 2,
            "pivot_right_strength": 2,
            "equality_tolerance_percentage": 0.1,
            "points": [self.high_point, self.low_point],
        }
        values.update(overrides)

        return MarketStructureAnalysisResult(**values)

    def test_computes_bullish_bias_from_higher_high_and_low(self):
        result = self.build_result()

        self.assertEqual(result.bias, MarketStructureBias.BULLISH)

    def test_returns_undetermined_bias_without_both_pivot_types(self):
        result = self.build_result(points=[self.high_point])

        self.assertEqual(result.bias, MarketStructureBias.UNDETERMINED)

    def test_rejects_future_pivot(self):
        with self.assertRaisesRegex(
            ValidationError,
            "cannot use future pivots",
        ):
            self.build_result(
                evaluated_at=self.high_point.reference_pivot.confirmed_at
            )

    def test_rejects_point_with_different_tolerance(self):
        with self.assertRaisesRegex(
            ValidationError,
            "points must use result tolerance",
        ):
            self.build_result(equality_tolerance_percentage=0.2)

    def test_rejects_duplicate_points(self):
        with self.assertRaisesRegex(
            ValidationError,
            "market-structure points must be unique",
        ):
            self.build_result(
                points=[self.high_point, self.high_point]
            )


if __name__ == "__main__":
    unittest.main()
