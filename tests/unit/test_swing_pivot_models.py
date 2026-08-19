import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.models.price_action import (
    SwingPivot,
    SwingPivotDetectionResult,
    SwingPivotType,
)


class SwingPivotModelTests(unittest.TestCase):
    def setUp(self):
        self.pivot_at = datetime(2026, 8, 5, tzinfo=UTC)
        self.confirmed_at = self.pivot_at + timedelta(days=2)

    def build_pivot(self, **overrides):
        values = {
            "pivot_type": SwingPivotType.HIGH,
            "pivot_at": self.pivot_at,
            "confirmed_at": self.confirmed_at,
            "price": 105.0,
            "left_strength": 2,
            "right_strength": 2,
        }
        values.update(overrides)

        return SwingPivot(**values)

    def test_builds_confirmed_swing_pivot(self):
        pivot = self.build_pivot()

        self.assertEqual(pivot.pivot_type, SwingPivotType.HIGH)
        self.assertEqual(pivot.pivot_at, self.pivot_at)
        self.assertEqual(pivot.confirmed_at, self.confirmed_at)
        self.assertEqual(pivot.price, 105.0)

    def test_rejects_timestamp_without_timezone(self):
        with self.assertRaisesRegex(
            ValidationError,
            "timestamps must include timezone information",
        ):
            self.build_pivot(pivot_at=datetime(2026, 8, 5))

    def test_rejects_confirmation_at_or_before_pivot(self):
        for confirmed_at in (
            self.pivot_at,
            self.pivot_at - timedelta(days=1),
        ):
            with self.subTest(confirmed_at=confirmed_at):
                with self.assertRaisesRegex(
                    ValidationError,
                    "confirmation must occur after the pivot",
                ):
                    self.build_pivot(confirmed_at=confirmed_at)

    def test_rejects_invalid_price(self):
        for price in (-0.1, float("nan"), float("inf")):
            with self.subTest(price=price):
                with self.assertRaises(ValidationError):
                    self.build_pivot(price=price)

    def test_rejects_invalid_strength(self):
        invalid_values = (True, 2.5, "2", 0, -1)

        for strength in invalid_values:
            with self.subTest(strength=strength):
                with self.assertRaises(ValidationError):
                    self.build_pivot(left_strength=strength)


class SwingPivotDetectionResultModelTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 5, tzinfo=UTC)
        self.first_pivot = self.build_pivot(
            pivot_type=SwingPivotType.HIGH,
            pivot_at=self.first_timestamp,
            confirmed_at=self.first_timestamp + timedelta(days=2),
            price=105.0,
        )

    def build_pivot(self, **overrides):
        values = {
            "pivot_type": SwingPivotType.LOW,
            "pivot_at": self.first_timestamp + timedelta(days=2),
            "confirmed_at": self.first_timestamp + timedelta(days=4),
            "price": 95.0,
            "left_strength": 2,
            "right_strength": 2,
        }
        values.update(overrides)

        return SwingPivot(**values)

    def build_result(self, **overrides):
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "source": "angel_one",
            "source_retrieved_at": datetime(
                2026,
                8,
                19,
                tzinfo=UTC,
            ),
            "left_strength": 2,
            "right_strength": 2,
            "pivots": [self.first_pivot],
        }
        values.update(overrides)

        return SwingPivotDetectionResult(**values)

    def test_allows_detection_result_without_pivots(self):
        result = self.build_result(pivots=[])

        self.assertEqual(result.pivots, [])

    def test_rejects_pivot_with_different_strength(self):
        mismatched = self.build_pivot(left_strength=3)

        with self.assertRaisesRegex(
            ValidationError,
            "must use result strengths",
        ):
            self.build_result(pivots=[mismatched])

    def test_rejects_duplicate_pivots(self):
        with self.assertRaisesRegex(
            ValidationError,
            "detected swing pivots must be unique",
        ):
            self.build_result(
                pivots=[self.first_pivot, self.first_pivot]
            )

    def test_rejects_pivots_out_of_order(self):
        later_pivot = self.build_pivot()

        with self.assertRaisesRegex(
            ValidationError,
            "must be in ascending order",
        ):
            self.build_result(pivots=[later_pivot, self.first_pivot])

    def test_rejects_invalid_result_strength(self):
        with self.assertRaises(ValidationError):
            self.build_result(left_strength=True)

    def test_rejects_retrieval_time_without_timezone(self):
        with self.assertRaisesRegex(
            ValidationError,
            "retrieval time must include timezone information",
        ):
            self.build_result(
                source_retrieved_at=datetime(2026, 8, 19)
            )


if __name__ == "__main__":
    unittest.main()
