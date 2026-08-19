import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.models.price_action import (
    MarketStructureBias,
    StructureBreak,
    StructureBreakDetectionResult,
    StructureBreakDirection,
    StructureBreakType,
    SwingPivot,
    SwingPivotType,
)


class StructureBreakModelTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.pivot = SwingPivot(
            pivot_type=SwingPivotType.HIGH,
            pivot_at=self.first_timestamp,
            confirmed_at=self.first_timestamp + timedelta(days=2),
            price=110.0,
            left_strength=2,
            right_strength=2,
        )

    def build_event(self, **overrides):
        values = {
            "break_type": StructureBreakType.BREAK_OF_STRUCTURE,
            "direction": StructureBreakDirection.BULLISH,
            "occurred_at": self.first_timestamp + timedelta(days=3),
            "previous_close": 109.0,
            "close_price": 111.0,
            "broken_pivot": self.pivot,
            "bias_before": MarketStructureBias.BULLISH,
        }
        values.update(overrides)

        return StructureBreak(**values)

    def test_builds_break_with_computed_measurements(self):
        event = self.build_event()

        self.assertEqual(event.break_distance, 1.0)
        self.assertAlmostEqual(event.break_percentage, 100 / 110)
        self.assertEqual(event.bias_after, MarketStructureBias.BULLISH)

    def test_choch_flips_effective_bias(self):
        event = self.build_event(
            break_type=StructureBreakType.CHANGE_OF_CHARACTER,
            bias_before=MarketStructureBias.BEARISH,
        )

        self.assertEqual(event.bias_after, MarketStructureBias.BULLISH)

    def test_rejects_direction_incompatible_with_pivot(self):
        with self.assertRaisesRegex(
            ValidationError,
            "bullish structure breaks require a swing high",
        ):
            self.build_event(
                broken_pivot=self.pivot.model_copy(
                    update={"pivot_type": SwingPivotType.LOW}
                )
            )

    def test_rejects_close_that_does_not_cross_level(self):
        with self.assertRaisesRegex(
            ValidationError,
            "close crossing above the pivot",
        ):
            self.build_event(close_price=110.0)

    def test_rejects_previous_close_already_beyond_level(self):
        with self.assertRaisesRegex(
            ValidationError,
            "close crossing above the pivot",
        ):
            self.build_event(previous_close=110.5)

    def test_rejects_break_type_incompatible_with_bias(self):
        with self.assertRaisesRegex(
            ValidationError,
            "type does not match prior bias",
        ):
            self.build_event(
                break_type=StructureBreakType.CHANGE_OF_CHARACTER
            )

    def test_rejects_event_before_pivot_confirmation(self):
        with self.assertRaisesRegex(
            ValidationError,
            "cannot precede pivot confirmation",
        ):
            self.build_event(occurred_at=self.first_timestamp)


class StructureBreakDetectionResultModelTests(unittest.TestCase):
    def setUp(self):
        self.retrieved_at = datetime(2026, 8, 20, tzinfo=UTC)
        pivot = SwingPivot(
            pivot_type=SwingPivotType.HIGH,
            pivot_at=datetime(2026, 8, 5, tzinfo=UTC),
            confirmed_at=datetime(2026, 8, 7, tzinfo=UTC),
            price=110.0,
            left_strength=2,
            right_strength=2,
        )
        self.event = StructureBreak(
            break_type=StructureBreakType.BREAK_OF_STRUCTURE,
            direction=StructureBreakDirection.BULLISH,
            occurred_at=datetime(2026, 8, 10, tzinfo=UTC),
            previous_close=109.0,
            close_price=111.0,
            broken_pivot=pivot,
            bias_before=MarketStructureBias.BULLISH,
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
            "structure_equality_tolerance_percentage": 0.1,
            "events": [self.event],
        }
        values.update(overrides)

        return StructureBreakDetectionResult(**values)

    def test_allows_result_without_events(self):
        result = self.build_result(events=[])

        self.assertEqual(result.events, [])

    def test_rejects_future_event(self):
        with self.assertRaisesRegex(
            ValidationError,
            "cannot contain future events",
        ):
            self.build_result(
                evaluated_at=self.event.occurred_at
                - timedelta(days=1)
            )

    def test_rejects_duplicate_broken_pivot(self):
        with self.assertRaisesRegex(
            ValidationError,
            "can be structurally broken once",
        ):
            self.build_result(events=[self.event, self.event])

    def test_rejects_event_with_different_pivot_strength(self):
        mismatched_pivot = self.event.broken_pivot.model_copy(
            update={"left_strength": 3}
        )
        mismatched_event = self.event.model_copy(
            update={"broken_pivot": mismatched_pivot}
        )

        with self.assertRaisesRegex(
            ValidationError,
            "pivots must use result strengths",
        ):
            self.build_result(events=[mismatched_event])


if __name__ == "__main__":
    unittest.main()
