import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.models.price_action import (
    PriceZoneType,
    SupportResistanceDetectionResult,
    SupportResistanceZone,
    SwingPivot,
    SwingPivotType,
)


class SupportResistanceZoneModelTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.pivots = [
            self.build_pivot(0, 2, 100.0),
            self.build_pivot(3, 5, 100.4),
        ]

    def build_pivot(
        self,
        pivot_day,
        confirmation_day,
        price,
        **overrides,
    ):
        values = {
            "pivot_type": SwingPivotType.LOW,
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

    def build_zone(self, **overrides):
        values = {
            "zone_type": PriceZoneType.SUPPORT,
            "lower_price": 100.0,
            "upper_price": 100.4,
            "center_price": 100.2,
            "confirmed_at": self.pivots[1].confirmed_at,
            "pivots": self.pivots,
        }
        values.update(overrides)

        return SupportResistanceZone(**values)

    def test_builds_zone_with_computed_touch_metadata(self):
        zone = self.build_zone()

        self.assertEqual(zone.touch_count, 2)
        self.assertEqual(zone.first_touched_at, self.pivots[0].pivot_at)
        self.assertEqual(zone.last_touched_at, self.pivots[1].pivot_at)

    def test_rejects_incompatible_pivot_type(self):
        high_pivot = self.build_pivot(
            3,
            5,
            100.4,
            pivot_type=SwingPivotType.HIGH,
        )

        with self.assertRaisesRegex(
            ValidationError,
            "incompatible pivot type",
        ):
            self.build_zone(pivots=[self.pivots[0], high_pivot])

    def test_rejects_boundaries_not_derived_from_pivots(self):
        with self.assertRaisesRegex(
            ValidationError,
            "boundaries must match pivot prices",
        ):
            self.build_zone(upper_price=101.0)

    def test_rejects_center_not_equal_to_mean_pivot_price(self):
        with self.assertRaisesRegex(
            ValidationError,
            "center must equal mean pivot price",
        ):
            self.build_zone(center_price=100.1)

    def test_rejects_duplicate_pivots(self):
        with self.assertRaisesRegex(
            ValidationError,
            "support-resistance pivots must be unique",
        ):
            self.build_zone(pivots=[self.pivots[0], self.pivots[0]])

    def test_rejects_confirmation_without_timezone(self):
        with self.assertRaisesRegex(
            ValidationError,
            "confirmation time must include timezone information",
        ):
            self.build_zone(confirmed_at=datetime(2026, 8, 6))


class SupportResistanceDetectionResultModelTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.retrieved_at = datetime(2026, 8, 20, tzinfo=UTC)
        self.pivots = [
            self.build_pivot(0, 2, 100.0),
            self.build_pivot(3, 5, 100.4),
        ]
        self.zone = SupportResistanceZone(
            zone_type=PriceZoneType.SUPPORT,
            lower_price=100.0,
            upper_price=100.4,
            center_price=100.2,
            confirmed_at=self.pivots[1].confirmed_at,
            pivots=self.pivots,
        )

    def build_pivot(self, pivot_day, confirmation_day, price):
        return SwingPivot(
            pivot_type=SwingPivotType.LOW,
            pivot_at=self.first_timestamp + timedelta(days=pivot_day),
            confirmed_at=(
                self.first_timestamp + timedelta(days=confirmation_day)
            ),
            price=price,
            left_strength=2,
            right_strength=2,
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
            "tolerance_percentage": 0.5,
            "minimum_touches": 2,
            "zones": [self.zone],
        }
        values.update(overrides)

        return SupportResistanceDetectionResult(**values)

    def test_allows_result_without_zones(self):
        result = self.build_result(zones=[])

        self.assertEqual(result.zones, [])

    def test_rejects_zone_below_minimum_touches(self):
        with self.assertRaisesRegex(
            ValidationError,
            "require minimum touches",
        ):
            self.build_result(minimum_touches=3)

    def test_rejects_zone_outside_configured_tolerance(self):
        with self.assertRaisesRegex(
            ValidationError,
            "zone exceeds result tolerance",
        ):
            self.build_result(tolerance_percentage=0.1)

    def test_rejects_incorrect_zone_confirmation(self):
        incorrect_zone = self.zone.model_copy(
            update={"confirmed_at": self.pivots[0].confirmed_at}
        )

        with self.assertRaisesRegex(
            ValidationError,
            "must match the minimum-touch pivot",
        ):
            self.build_result(zones=[incorrect_zone])

    def test_rejects_future_pivot(self):
        with self.assertRaisesRegex(
            ValidationError,
            "cannot use future pivots",
        ):
            self.build_result(
                evaluated_at=self.pivots[0].confirmed_at
            )

    def test_rejects_evaluation_after_source_retrieval(self):
        with self.assertRaisesRegex(
            ValidationError,
            "evaluation cannot follow source retrieval",
        ):
            self.build_result(
                evaluated_at=self.retrieved_at + timedelta(days=1)
            )

    def test_rejects_invalid_minimum_touches(self):
        for value in (True, 2.5, "2", 1, 0):
            with self.subTest(value=value):
                with self.assertRaises(ValidationError):
                    self.build_result(minimum_touches=value)


if __name__ == "__main__":
    unittest.main()
