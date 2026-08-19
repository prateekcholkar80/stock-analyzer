import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.models.price_action import (
    FairValueGap,
    FairValueGapDetectionResult,
    FairValueGapDirection,
    FairValueGapStatus,
)


class FairValueGapModelTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)

    def build_gap(self, **overrides):
        values = {
            "direction": FairValueGapDirection.BULLISH,
            "first_candle_at": self.first_timestamp,
            "impulse_candle_at": (
                self.first_timestamp + timedelta(days=1)
            ),
            "detected_at": self.first_timestamp + timedelta(days=2),
            "lower_price": 102.0,
            "upper_price": 103.0,
            "atr_value": 2.0,
        }
        values.update(overrides)

        return FairValueGap(**values)

    def test_builds_gap_with_computed_measurements(self):
        gap = self.build_gap()

        self.assertEqual(gap.gap_size, 1.0)
        self.assertAlmostEqual(gap.gap_percentage, 100 / 102.5)
        self.assertEqual(gap.atr_multiple, 0.5)
        self.assertEqual(gap.model_dump()["gap_size"], 1.0)

    def test_allows_gap_without_atr_measurement(self):
        gap = self.build_gap(atr_value=None)

        self.assertIsNone(gap.atr_multiple)

    def test_rejects_timestamp_without_timezone(self):
        with self.assertRaisesRegex(
            ValidationError,
            "timestamps must include timezone information",
        ):
            self.build_gap(first_candle_at=datetime(2026, 8, 1))

    def test_rejects_formation_timestamps_out_of_order(self):
        with self.assertRaisesRegex(
            ValidationError,
            "formation timestamps must be in ascending order",
        ):
            self.build_gap(
                impulse_candle_at=self.first_timestamp,
            )

    def test_rejects_invalid_zone_boundaries(self):
        with self.assertRaisesRegex(
            ValidationError,
            "upper price must be greater than lower price",
        ):
            self.build_gap(lower_price=103.0, upper_price=102.0)

    def test_rejects_non_finite_numeric_value(self):
        with self.assertRaisesRegex(
            ValidationError,
            "numeric values must be finite",
        ):
            self.build_gap(atr_value=float("inf"))

    def test_builds_partially_filled_gap(self):
        touched_at = self.first_timestamp + timedelta(days=3)

        gap = self.build_gap(
            status=FairValueGapStatus.PARTIALLY_FILLED,
            fill_percentage=50,
            first_touched_at=touched_at,
        )

        self.assertEqual(gap.status, FairValueGapStatus.PARTIALLY_FILLED)
        self.assertEqual(gap.fill_percentage, 50)
        self.assertEqual(gap.first_touched_at, touched_at)

    def test_builds_filled_gap(self):
        resolved_at = self.first_timestamp + timedelta(days=3)

        gap = self.build_gap(
            status=FairValueGapStatus.FILLED,
            fill_percentage=100,
            first_touched_at=resolved_at,
            resolved_at=resolved_at,
        )

        self.assertEqual(gap.status, FairValueGapStatus.FILLED)

    def test_builds_expired_partially_filled_gap(self):
        touched_at = self.first_timestamp + timedelta(days=3)
        resolved_at = self.first_timestamp + timedelta(days=4)

        gap = self.build_gap(
            status=FairValueGapStatus.EXPIRED,
            fill_percentage=25,
            first_touched_at=touched_at,
            resolved_at=resolved_at,
        )

        self.assertEqual(gap.status, FairValueGapStatus.EXPIRED)
        self.assertEqual(gap.fill_percentage, 25)

    def test_rejects_partial_gap_without_first_touch(self):
        with self.assertRaisesRegex(
            ValidationError,
            "require an unresolved first touch",
        ):
            self.build_gap(
                status=FairValueGapStatus.PARTIALLY_FILLED,
                fill_percentage=50,
            )

    def test_rejects_resolved_gap_without_full_fill(self):
        resolved_at = self.first_timestamp + timedelta(days=3)

        with self.assertRaisesRegex(
            ValidationError,
            "require 100 percent fill",
        ):
            self.build_gap(
                status=FairValueGapStatus.FILLED,
                fill_percentage=50,
                first_touched_at=resolved_at,
                resolved_at=resolved_at,
            )

    def test_rejects_lifecycle_event_at_detection_time(self):
        detected_at = self.first_timestamp + timedelta(days=2)

        with self.assertRaisesRegex(
            ValidationError,
            "first touch must occur after detection",
        ):
            self.build_gap(first_touched_at=detected_at)


class FairValueGapDetectionResultTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.gap = FairValueGap(
            direction=FairValueGapDirection.BULLISH,
            first_candle_at=self.first_timestamp,
            impulse_candle_at=(
                self.first_timestamp + timedelta(days=1)
            ),
            detected_at=self.first_timestamp + timedelta(days=2),
            lower_price=102.0,
            upper_price=103.0,
        )

    def build_result(self, **overrides):
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "source": "angel_one",
            "source_retrieved_at": datetime(2026, 8, 19, tzinfo=UTC),
            "gaps": [self.gap],
        }
        values.update(overrides)

        return FairValueGapDetectionResult(**values)

    def test_allows_detection_result_without_gaps(self):
        result = self.build_result(gaps=[])

        self.assertEqual(result.gaps, [])

    def test_rejects_duplicate_gaps(self):
        with self.assertRaisesRegex(
            ValidationError,
            "detected fair-value gaps must be unique",
        ):
            self.build_result(gaps=[self.gap, self.gap])

    def test_rejects_gaps_out_of_detection_order(self):
        later_gap = FairValueGap(
            direction=FairValueGapDirection.BEARISH,
            first_candle_at=(
                self.first_timestamp + timedelta(days=3)
            ),
            impulse_candle_at=(
                self.first_timestamp + timedelta(days=4)
            ),
            detected_at=self.first_timestamp + timedelta(days=5),
            lower_price=100.0,
            upper_price=101.0,
        )

        with self.assertRaisesRegex(
            ValidationError,
            "must be in ascending order",
        ):
            self.build_result(gaps=[later_gap, self.gap])

    def test_rejects_non_finite_threshold(self):
        with self.assertRaisesRegex(
            ValidationError,
            "thresholds must be finite",
        ):
            self.build_result(minimum_gap_percentage=float("inf"))


if __name__ == "__main__":
    unittest.main()
