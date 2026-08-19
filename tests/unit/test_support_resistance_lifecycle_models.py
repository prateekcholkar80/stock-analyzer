import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.models.price_action import (
    PriceZoneBreakDirection,
    PriceZoneLifecycleStatus,
    PriceZoneType,
    SupportResistanceLifecycle,
    SupportResistanceLifecycleResult,
    SupportResistanceZone,
    SwingPivot,
    SwingPivotType,
)


class SupportResistanceLifecycleModelTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.zone = self.build_zone(PriceZoneType.RESISTANCE)

    def build_zone(self, zone_type):
        pivot_type = (
            SwingPivotType.HIGH
            if zone_type is PriceZoneType.RESISTANCE
            else SwingPivotType.LOW
        )
        prices = (
            (110.0, 110.4)
            if zone_type is PriceZoneType.RESISTANCE
            else (100.0, 100.4)
        )
        pivots = [
            SwingPivot(
                pivot_type=pivot_type,
                pivot_at=self.first_timestamp + timedelta(days=day),
                confirmed_at=(
                    self.first_timestamp
                    + timedelta(days=confirmation_day)
                ),
                price=price,
                left_strength=2,
                right_strength=2,
            )
            for day, confirmation_day, price in (
                (0, 2, prices[0]),
                (3, 5, prices[1]),
            )
        ]
        return SupportResistanceZone(
            zone_type=zone_type,
            lower_price=prices[0],
            upper_price=prices[1],
            center_price=sum(prices) / 2,
            confirmed_at=pivots[1].confirmed_at,
            pivots=pivots,
        )

    def build_resistance_lifecycle(self, **overrides):
        values = {
            "zone": self.zone,
            "status": PriceZoneLifecycleStatus.BROKEN,
            "broken_at": self.first_timestamp + timedelta(days=6),
            "break_direction": PriceZoneBreakDirection.BULLISH,
            "previous_close": 109.0,
            "break_close_price": 111.0,
        }
        values.update(overrides)

        return SupportResistanceLifecycle(**values)

    def test_builds_active_lifecycle_without_events(self):
        lifecycle = SupportResistanceLifecycle(
            zone=self.zone,
            status=PriceZoneLifecycleStatus.ACTIVE,
        )

        self.assertIsNone(lifecycle.broken_at)
        self.assertIsNone(lifecycle.break_distance)
        self.assertIsNone(lifecycle.break_percentage)
        self.assertIsNone(lifecycle.reversed_zone_type)

    def test_builds_bullish_break_with_computed_distance(self):
        lifecycle = self.build_resistance_lifecycle()

        self.assertAlmostEqual(lifecycle.break_distance, 0.6)
        self.assertAlmostEqual(
            lifecycle.break_percentage,
            0.6 / 110.4 * 100,
        )

    def test_builds_bearish_support_break(self):
        support = self.build_zone(PriceZoneType.SUPPORT)

        lifecycle = self.build_resistance_lifecycle(
            zone=support,
            break_direction=PriceZoneBreakDirection.BEARISH,
            previous_close=101.0,
            break_close_price=99.5,
        )

        self.assertEqual(
            lifecycle.break_direction,
            PriceZoneBreakDirection.BEARISH,
        )
        self.assertEqual(lifecycle.break_distance, 0.5)

    def test_role_reversal_exposes_opposite_zone_type(self):
        lifecycle = self.build_resistance_lifecycle(
            status=PriceZoneLifecycleStatus.ROLE_REVERSED,
            retested_at=self.first_timestamp + timedelta(days=7),
            reversal_confirmed_at=(
                self.first_timestamp + timedelta(days=8)
            ),
        )

        self.assertEqual(
            lifecycle.reversed_zone_type,
            PriceZoneType.SUPPORT,
        )

    def test_active_status_rejects_lifecycle_events(self):
        with self.assertRaisesRegex(
            ValidationError,
            "active zones cannot contain break information",
        ):
            self.build_resistance_lifecycle(
                status=PriceZoneLifecycleStatus.ACTIVE
            )

    def test_non_active_status_requires_complete_break_information(self):
        for missing_field in (
            "broken_at",
            "break_direction",
            "previous_close",
            "break_close_price",
        ):
            with self.subTest(missing_field=missing_field):
                with self.assertRaisesRegex(
                    ValidationError,
                    "complete break information",
                ):
                    self.build_resistance_lifecycle(
                        **{missing_field: None}
                    )

    def test_rejects_break_before_zone_confirmation(self):
        with self.assertRaisesRegex(
            ValidationError,
            "cannot precede zone confirmation",
        ):
            self.build_resistance_lifecycle(
                broken_at=self.first_timestamp + timedelta(days=4)
            )

    def test_rejects_wrong_break_direction_for_zone_type(self):
        with self.assertRaisesRegex(
            ValidationError,
            "resistance zones require a bullish break",
        ):
            self.build_resistance_lifecycle(
                break_direction=PriceZoneBreakDirection.BEARISH
            )

    def test_rejects_close_that_does_not_cross_zone_boundary(self):
        invalid_closes = (
            {"previous_close": 111.0, "break_close_price": 112.0},
            {"previous_close": 109.0, "break_close_price": 110.4},
        )

        for overrides in invalid_closes:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(
                    ValidationError,
                    "close crossing above",
                ):
                    self.build_resistance_lifecycle(**overrides)

    def test_rejects_naive_event_timestamp(self):
        with self.assertRaisesRegex(
            ValidationError,
            "must include timezone information",
        ):
            self.build_resistance_lifecycle(
                broken_at=datetime(2026, 8, 7)
            )

    def test_retest_must_follow_break(self):
        with self.assertRaisesRegex(
            ValidationError,
            "retest must follow the break",
        ):
            self.build_resistance_lifecycle(
                status=PriceZoneLifecycleStatus.RETESTED,
                retested_at=self.first_timestamp + timedelta(days=6),
            )

    def test_role_reversal_requires_retest_and_later_confirmation(self):
        invalid_values = (
            {"retested_at": None, "reversal_confirmed_at": None},
            {
                "retested_at": self.first_timestamp
                + timedelta(days=7),
                "reversal_confirmed_at": self.first_timestamp
                + timedelta(days=7),
            },
        )

        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    self.build_resistance_lifecycle(
                        status=PriceZoneLifecycleStatus.ROLE_REVERSED,
                        **values,
                    )

    def test_failed_status_requires_later_failure_time(self):
        with self.assertRaisesRegex(
            ValidationError,
            "requires a failure time",
        ):
            self.build_resistance_lifecycle(
                status=PriceZoneLifecycleStatus.FAILED_BREAK
            )

        with self.assertRaisesRegex(
            ValidationError,
            "must follow the break",
        ):
            self.build_resistance_lifecycle(
                status=PriceZoneLifecycleStatus.FAILED_BREAK,
                failed_at=self.first_timestamp + timedelta(days=6),
            )

    def test_terminal_states_are_mutually_exclusive(self):
        with self.assertRaisesRegex(
            ValidationError,
            "cannot also have a failed break",
        ):
            self.build_resistance_lifecycle(
                status=PriceZoneLifecycleStatus.ROLE_REVERSED,
                retested_at=self.first_timestamp + timedelta(days=7),
                reversal_confirmed_at=(
                    self.first_timestamp + timedelta(days=8)
                ),
                failed_at=self.first_timestamp + timedelta(days=9),
            )


class SupportResistanceLifecycleResultModelTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.retrieved_at = datetime(2026, 8, 20, tzinfo=UTC)
        pivots = [
            SwingPivot(
                pivot_type=SwingPivotType.HIGH,
                pivot_at=self.first_timestamp + timedelta(days=day),
                confirmed_at=(
                    self.first_timestamp
                    + timedelta(days=confirmation_day)
                ),
                price=price,
                left_strength=2,
                right_strength=2,
            )
            for day, confirmation_day, price in (
                (0, 2, 110.0),
                (3, 5, 110.4),
            )
        ]
        self.zone = SupportResistanceZone(
            zone_type=PriceZoneType.RESISTANCE,
            lower_price=110.0,
            upper_price=110.4,
            center_price=110.2,
            confirmed_at=pivots[1].confirmed_at,
            pivots=pivots,
        )
        self.lifecycle = SupportResistanceLifecycle(
            zone=self.zone,
            status=PriceZoneLifecycleStatus.BROKEN,
            broken_at=self.first_timestamp + timedelta(days=6),
            break_direction=PriceZoneBreakDirection.BULLISH,
            previous_close=109.0,
            break_close_price=111.0,
        )

    def build_result(self, **overrides):
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "source": "angel_one",
            "source_retrieved_at": self.retrieved_at,
            "zone_detection_evaluated_at": (
                self.first_timestamp + timedelta(days=5)
            ),
            "evaluated_at": self.first_timestamp + timedelta(days=6),
            "pivot_left_strength": 2,
            "pivot_right_strength": 2,
            "tolerance_percentage": 0.5,
            "minimum_touches": 2,
            "lifecycles": [self.lifecycle],
        }
        values.update(overrides)

        return SupportResistanceLifecycleResult(**values)

    def test_builds_result_and_allows_empty_lifecycle_list(self):
        result = self.build_result(lifecycles=[])

        self.assertEqual(result.lifecycles, [])

    def test_rejects_evaluation_before_zone_detection(self):
        with self.assertRaisesRegex(
            ValidationError,
            "cannot precede zone detection",
        ):
            self.build_result(
                evaluated_at=self.first_timestamp + timedelta(days=4)
            )

    def test_rejects_evaluation_after_source_retrieval(self):
        with self.assertRaisesRegex(
            ValidationError,
            "cannot follow source retrieval",
        ):
            self.build_result(
                evaluated_at=self.retrieved_at + timedelta(days=1)
            )

    def test_rejects_future_lifecycle_event(self):
        with self.assertRaisesRegex(
            ValidationError,
            "cannot contain future events",
        ):
            self.build_result(
                evaluated_at=self.first_timestamp + timedelta(days=5)
            )

    def test_rejects_pivot_unavailable_during_zone_detection(self):
        with self.assertRaisesRegex(
            ValidationError,
            "pivots unavailable during zone detection",
        ):
            self.build_result(
                zone_detection_evaluated_at=(
                    self.first_timestamp + timedelta(days=4)
                ),
                evaluated_at=self.first_timestamp + timedelta(days=6),
            )

    def test_rejects_mismatched_strengths_and_tolerance(self):
        with self.assertRaisesRegex(
            ValidationError,
            "must use result strengths",
        ):
            self.build_result(pivot_left_strength=3)

        with self.assertRaisesRegex(
            ValidationError,
            "exceeds result tolerance",
        ):
            self.build_result(tolerance_percentage=0.1)

    def test_rejects_duplicate_zone_lifecycles(self):
        with self.assertRaisesRegex(
            ValidationError,
            "zone lifecycles must be unique",
        ):
            self.build_result(
                lifecycles=[self.lifecycle, self.lifecycle]
            )


if __name__ == "__main__":
    unittest.main()
