import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.support_resistance import (
    detect_support_resistance_zones,
)
from app.models.price_action import (
    PriceZoneType,
    SwingPivot,
    SwingPivotDetectionResult,
    SwingPivotType,
)


class SupportResistanceDetectionTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.retrieved_at = datetime(2026, 8, 30, tzinfo=UTC)
        self.pivots = [
            self.build_pivot(SwingPivotType.LOW, 0, 2, 100.0),
            self.build_pivot(SwingPivotType.HIGH, 1, 3, 110.0),
            self.build_pivot(SwingPivotType.LOW, 3, 5, 100.3),
            self.build_pivot(SwingPivotType.HIGH, 4, 6, 110.4),
            self.build_pivot(SwingPivotType.LOW, 6, 8, 100.45),
            self.build_pivot(SwingPivotType.LOW, 9, 11, 105.0),
            self.build_pivot(SwingPivotType.HIGH, 10, 12, 115.0),
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

    def test_clusters_support_and_resistance_separately(self):
        result = detect_support_resistance_zones(self.pivot_result)

        self.assertEqual(
            [zone.zone_type for zone in result.zones],
            [PriceZoneType.SUPPORT, PriceZoneType.RESISTANCE],
        )
        support, resistance = result.zones
        self.assertEqual(support.lower_price, 100.0)
        self.assertEqual(support.upper_price, 100.45)
        self.assertAlmostEqual(support.center_price, 100.25)
        self.assertEqual(support.touch_count, 3)
        self.assertEqual(resistance.lower_price, 110.0)
        self.assertEqual(resistance.upper_price, 110.4)
        self.assertEqual(resistance.touch_count, 2)

    def test_confirms_zone_when_minimum_touch_is_confirmed(self):
        result = detect_support_resistance_zones(self.pivot_result)
        support, resistance = result.zones

        self.assertEqual(
            support.confirmed_at,
            self.pivots[2].confirmed_at,
        )
        self.assertEqual(
            resistance.confirmed_at,
            self.pivots[3].confirmed_at,
        )

    def test_as_of_excludes_unconfirmed_future_pivots(self):
        result = detect_support_resistance_zones(
            self.pivot_result,
            as_of=self.pivots[2].confirmed_at,
        )

        self.assertEqual(len(result.zones), 1)
        support = result.zones[0]
        self.assertEqual(support.touch_count, 2)
        self.assertEqual(support.lower_price, 100.0)
        self.assertEqual(support.upper_price, 100.3)
        self.assertEqual(result.evaluated_at, self.pivots[2].confirmed_at)

    def test_supports_custom_tolerance_and_minimum_touches(self):
        result = detect_support_resistance_zones(
            self.pivot_result,
            tolerance_percentage=5.0,
            minimum_touches=3,
        )

        self.assertEqual(len(result.zones), 2)
        self.assertEqual(
            [zone.touch_count for zone in result.zones],
            [4, 3],
        )

    def test_filters_clusters_below_minimum_touches(self):
        result = detect_support_resistance_zones(
            self.pivot_result,
            tolerance_percentage=0.1,
        )

        self.assertEqual(result.zones, [])

    def test_preserves_source_and_configuration_metadata(self):
        result = detect_support_resistance_zones(self.pivot_result)

        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertEqual(result.source, "angel_one")
        self.assertEqual(result.source_retrieved_at, self.retrieved_at)
        self.assertEqual(result.evaluated_at, self.retrieved_at)
        self.assertEqual(result.pivot_left_strength, 2)
        self.assertEqual(result.pivot_right_strength, 2)
        self.assertEqual(result.tolerance_percentage, 0.5)
        self.assertEqual(result.minimum_touches, 2)

    def test_returns_empty_result_without_pivots(self):
        result = detect_support_resistance_zones(
            self.build_result([])
        )

        self.assertEqual(result.zones, [])

    def test_rejects_invalid_tolerance(self):
        for tolerance in (True, "0.5", None, -0.1, float("inf")):
            with self.subTest(tolerance=tolerance):
                with self.assertRaises(ValueError):
                    detect_support_resistance_zones(
                        self.pivot_result,
                        tolerance_percentage=tolerance,
                    )

    def test_rejects_invalid_minimum_touches(self):
        for minimum_touches in (True, 2.5, "2", 1, 0, -1):
            with self.subTest(minimum_touches=minimum_touches):
                with self.assertRaises(ValueError):
                    detect_support_resistance_zones(
                        self.pivot_result,
                        minimum_touches=minimum_touches,
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
                    detect_support_resistance_zones(
                        self.pivot_result,
                        as_of=as_of,
                    )


if __name__ == "__main__":
    unittest.main()
