import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.support_resistance_lifecycle import (
    track_support_resistance_lifecycle,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.price_action import (
    PriceZoneBreakDirection,
    PriceZoneLifecycleStatus,
    PriceZoneType,
    SupportResistanceDetectionResult,
    SupportResistanceZone,
    SwingPivot,
    SwingPivotType,
)


class SupportResistanceLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.retrieved_at = datetime(2026, 8, 20, tzinfo=UTC)

    def candle(
        self,
        day,
        *,
        open_price,
        high,
        low,
        close,
    ):
        return Candle(
            timestamp=self.first_timestamp + timedelta(days=day),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=1_000 + day,
        )

    def resistance_fixture(self, future=()):
        candles = [
            self.candle(
                0,
                open_price=106,
                high=110,
                low=105,
                close=107,
            ),
            self.candle(
                1,
                open_price=107,
                high=108,
                low=106,
                close=107,
            ),
            self.candle(
                2,
                open_price=107,
                high=109,
                low=106,
                close=108,
            ),
            self.candle(
                3,
                open_price=108,
                high=110.4,
                low=106,
                close=108,
            ),
            self.candle(
                4,
                open_price=108,
                high=109,
                low=107,
                close=108,
            ),
            self.candle(
                5,
                open_price=108,
                high=110,
                low=107,
                close=109,
            ),
        ]
        pivots = [
            self.pivot(
                SwingPivotType.HIGH,
                candles,
                pivot_day=0,
                confirmation_day=2,
            ),
            self.pivot(
                SwingPivotType.HIGH,
                candles,
                pivot_day=3,
                confirmation_day=5,
            ),
        ]
        zone = SupportResistanceZone(
            zone_type=PriceZoneType.RESISTANCE,
            lower_price=110,
            upper_price=110.4,
            center_price=110.2,
            confirmed_at=candles[5].timestamp,
            pivots=pivots,
        )
        return self.build_inputs(candles + list(future), zone)

    def support_fixture(self, future=()):
        candles = [
            self.candle(
                0,
                open_price=104,
                high=106,
                low=100,
                close=103,
            ),
            self.candle(
                1,
                open_price=103,
                high=105,
                low=101,
                close=103,
            ),
            self.candle(
                2,
                open_price=103,
                high=104,
                low=101,
                close=102,
            ),
            self.candle(
                3,
                open_price=103,
                high=105,
                low=100.4,
                close=102,
            ),
            self.candle(
                4,
                open_price=102,
                high=104,
                low=101,
                close=102,
            ),
            self.candle(
                5,
                open_price=102,
                high=104,
                low=100.5,
                close=101,
            ),
        ]
        pivots = [
            self.pivot(
                SwingPivotType.LOW,
                candles,
                pivot_day=0,
                confirmation_day=2,
            ),
            self.pivot(
                SwingPivotType.LOW,
                candles,
                pivot_day=3,
                confirmation_day=5,
            ),
        ]
        zone = SupportResistanceZone(
            zone_type=PriceZoneType.SUPPORT,
            lower_price=100,
            upper_price=100.4,
            center_price=100.2,
            confirmed_at=candles[5].timestamp,
            pivots=pivots,
        )
        return self.build_inputs(candles + list(future), zone)

    def pivot(
        self,
        pivot_type,
        candles,
        *,
        pivot_day,
        confirmation_day,
    ):
        candle = candles[pivot_day]
        price = (
            candle.high
            if pivot_type is SwingPivotType.HIGH
            else candle.low
        )
        return SwingPivot(
            pivot_type=pivot_type,
            pivot_at=candle.timestamp,
            confirmed_at=candles[confirmation_day].timestamp,
            price=price,
            left_strength=2,
            right_strength=2,
        )

    def build_inputs(self, candles, zone):
        series = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=candles,
            retrieved_at=self.retrieved_at,
        )
        zones = SupportResistanceDetectionResult(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            source="angel_one",
            source_retrieved_at=self.retrieved_at,
            evaluated_at=self.first_timestamp + timedelta(days=5),
            pivot_left_strength=2,
            pivot_right_strength=2,
            tolerance_percentage=0.5,
            minimum_touches=2,
            zones=[zone],
        )
        return series, zones

    def resistance_progression(self):
        return [
            self.candle(
                6,
                open_price=109,
                high=112,
                low=108,
                close=111,
            ),
            self.candle(
                7,
                open_price=111,
                high=112,
                low=110.8,
                close=111.5,
            ),
            self.candle(
                8,
                open_price=111,
                high=111.5,
                low=110.2,
                close=110.6,
            ),
            self.candle(
                9,
                open_price=110.6,
                high=112,
                low=110.5,
                close=111,
            ),
        ]

    def support_progression(self):
        return [
            self.candle(
                6,
                open_price=101,
                high=101.5,
                low=99,
                close=99.5,
            ),
            self.candle(
                7,
                open_price=99.5,
                high=99.8,
                low=98.5,
                close=99,
            ),
            self.candle(
                8,
                open_price=99,
                high=100.2,
                low=98.8,
                close=99.8,
            ),
            self.candle(
                9,
                open_price=99.8,
                high=99.9,
                low=98.5,
                close=99,
            ),
        ]

    def test_tracks_resistance_break_retest_and_role_reversal(self):
        future = self.resistance_progression()
        series, zones = self.resistance_fixture(future)

        result = track_support_resistance_lifecycle(series, zones)
        lifecycle = result.lifecycles[0]

        self.assertEqual(
            lifecycle.status,
            PriceZoneLifecycleStatus.ROLE_REVERSED,
        )
        self.assertEqual(
            lifecycle.break_direction,
            PriceZoneBreakDirection.BULLISH,
        )
        self.assertEqual(lifecycle.broken_at, future[0].timestamp)
        self.assertEqual(lifecycle.previous_close, 109)
        self.assertEqual(lifecycle.break_close_price, 111)
        self.assertEqual(lifecycle.retested_at, future[2].timestamp)
        self.assertEqual(
            lifecycle.reversal_confirmed_at,
            future[3].timestamp,
        )
        self.assertEqual(
            lifecycle.reversed_zone_type,
            PriceZoneType.SUPPORT,
        )
        self.assertAlmostEqual(lifecycle.break_distance, 0.6)

    def test_tracks_support_break_retest_and_role_reversal(self):
        future = self.support_progression()
        series, zones = self.support_fixture(future)

        result = track_support_resistance_lifecycle(series, zones)
        lifecycle = result.lifecycles[0]

        self.assertEqual(
            lifecycle.status,
            PriceZoneLifecycleStatus.ROLE_REVERSED,
        )
        self.assertEqual(
            lifecycle.break_direction,
            PriceZoneBreakDirection.BEARISH,
        )
        self.assertEqual(
            lifecycle.reversed_zone_type,
            PriceZoneType.RESISTANCE,
        )

    def test_as_of_exposes_each_state_without_future_information(self):
        future = self.resistance_progression()
        series, zones = self.resistance_fixture(future)
        expectations = (
            (5, PriceZoneLifecycleStatus.ACTIVE),
            (6, PriceZoneLifecycleStatus.BROKEN),
            (7, PriceZoneLifecycleStatus.BROKEN),
            (8, PriceZoneLifecycleStatus.RETESTED),
            (9, PriceZoneLifecycleStatus.ROLE_REVERSED),
        )

        for day, expected_status in expectations:
            with self.subTest(day=day):
                result = track_support_resistance_lifecycle(
                    series,
                    zones,
                    as_of=self.first_timestamp + timedelta(days=day),
                )
                self.assertEqual(
                    result.lifecycles[0].status,
                    expected_status,
                )

    def test_freezes_zone_boundaries_before_later_pivot_touches(self):
        future = [
            self.candle(
                6,
                open_price=109,
                high=111,
                low=108.5,
                close=110.6,
            ),
            self.candle(
                7,
                open_price=110.6,
                high=110.8,
                low=110.5,
                close=110.6,
            ),
            self.candle(
                8,
                open_price=110.6,
                high=110.7,
                low=110.5,
                close=110.6,
            ),
            self.candle(
                9,
                open_price=110.6,
                high=110.7,
                low=110.5,
                close=110.6,
            ),
        ]
        series, zones = self.resistance_fixture(future)
        original_zone = zones.zones[0]
        later_pivot = SwingPivot(
            pivot_type=SwingPivotType.HIGH,
            pivot_at=future[1].timestamp,
            confirmed_at=future[3].timestamp,
            price=future[1].high,
            left_strength=2,
            right_strength=2,
        )
        mature_zone = SupportResistanceZone(
            zone_type=PriceZoneType.RESISTANCE,
            lower_price=110,
            upper_price=110.8,
            center_price=(110 + 110.4 + 110.8) / 3,
            confirmed_at=original_zone.confirmed_at,
            pivots=original_zone.pivots + [later_pivot],
        )
        zones = SupportResistanceDetectionResult(
            exchange=zones.exchange,
            symbol_token=zones.symbol_token,
            symbol=zones.symbol,
            interval=zones.interval,
            source=zones.source,
            source_retrieved_at=zones.source_retrieved_at,
            evaluated_at=future[3].timestamp,
            pivot_left_strength=2,
            pivot_right_strength=2,
            tolerance_percentage=1.0,
            minimum_touches=2,
            zones=[mature_zone],
        )

        result = track_support_resistance_lifecycle(series, zones)
        lifecycle = result.lifecycles[0]

        self.assertEqual(mature_zone.upper_price, 110.8)
        self.assertEqual(lifecycle.zone.upper_price, 110.4)
        self.assertEqual(lifecycle.zone.touch_count, 2)
        self.assertEqual(
            lifecycle.status,
            PriceZoneLifecycleStatus.BROKEN,
        )
        self.assertEqual(lifecycle.broken_at, future[0].timestamp)

    def test_wick_and_close_at_boundary_do_not_break_zone(self):
        wick_only = self.candle(
            6,
            open_price=109,
            high=112,
            low=108,
            close=110.4,
        )
        series, zones = self.resistance_fixture([wick_only])

        result = track_support_resistance_lifecycle(series, zones)

        self.assertEqual(
            result.lifecycles[0].status,
            PriceZoneLifecycleStatus.ACTIVE,
        )

    def test_break_candle_cannot_also_count_as_retest(self):
        break_candle = self.resistance_progression()[0]
        series, zones = self.resistance_fixture([break_candle])

        result = track_support_resistance_lifecycle(series, zones)
        lifecycle = result.lifecycles[0]

        self.assertEqual(
            lifecycle.status,
            PriceZoneLifecycleStatus.BROKEN,
        )
        self.assertIsNone(lifecycle.retested_at)

    def test_non_overlapping_candle_does_not_count_as_retest(self):
        future = self.resistance_progression()[:2]
        series, zones = self.resistance_fixture(future)

        result = track_support_resistance_lifecycle(series, zones)

        self.assertEqual(
            result.lifecycles[0].status,
            PriceZoneLifecycleStatus.BROKEN,
        )

    def test_retest_requires_later_reversal_confirmation(self):
        future = self.resistance_progression()[:3]
        series, zones = self.resistance_fixture(future)

        result = track_support_resistance_lifecycle(series, zones)
        lifecycle = result.lifecycles[0]

        self.assertEqual(
            lifecycle.status,
            PriceZoneLifecycleStatus.RETESTED,
        )
        self.assertIsNone(lifecycle.reversal_confirmed_at)
        self.assertIsNone(lifecycle.reversed_zone_type)

    def test_marks_break_failed_before_retest(self):
        break_candle = self.resistance_progression()[0]
        failure = self.candle(
            7,
            open_price=111,
            high=111.2,
            low=109,
            close=109.5,
        )
        series, zones = self.resistance_fixture(
            [break_candle, failure]
        )

        result = track_support_resistance_lifecycle(series, zones)
        lifecycle = result.lifecycles[0]

        self.assertEqual(
            lifecycle.status,
            PriceZoneLifecycleStatus.FAILED_BREAK,
        )
        self.assertEqual(lifecycle.failed_at, failure.timestamp)
        self.assertIsNone(lifecycle.retested_at)

    def test_marks_break_failed_after_retest(self):
        future = self.resistance_progression()[:3]
        failure = self.candle(
            9,
            open_price=110.6,
            high=110.8,
            low=109,
            close=109.5,
        )
        series, zones = self.resistance_fixture(future + [failure])

        result = track_support_resistance_lifecycle(series, zones)
        lifecycle = result.lifecycles[0]

        self.assertEqual(
            lifecycle.status,
            PriceZoneLifecycleStatus.FAILED_BREAK,
        )
        self.assertEqual(lifecycle.retested_at, future[2].timestamp)
        self.assertEqual(lifecycle.failed_at, failure.timestamp)

    def test_does_not_record_break_that_precedes_zone_confirmation(self):
        series, zones = self.resistance_fixture()
        candles = list(series.candles)
        candles[2] = self.candle(
            2,
            open_price=108,
            high=112,
            low=107,
            close=111,
        )
        series = series.model_copy(update={"candles": candles})

        result = track_support_resistance_lifecycle(series, zones)

        self.assertEqual(
            result.lifecycles[0].status,
            PriceZoneLifecycleStatus.ACTIVE,
        )

    def test_can_break_on_zone_confirmation_candle(self):
        series, zones = self.resistance_fixture()
        candles = list(series.candles)
        candles[5] = self.candle(
            5,
            open_price=108,
            high=112,
            low=107,
            close=111,
        )
        series = series.model_copy(update={"candles": candles})

        result = track_support_resistance_lifecycle(series, zones)

        self.assertEqual(
            result.lifecycles[0].status,
            PriceZoneLifecycleStatus.BROKEN,
        )
        self.assertEqual(
            result.lifecycles[0].broken_at,
            candles[5].timestamp,
        )

    def test_preserves_source_and_zone_configuration_metadata(self):
        series, zones = self.resistance_fixture()

        result = track_support_resistance_lifecycle(series, zones)

        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(result.symbol_token, "2885")
        self.assertEqual(result.symbol, "RELIANCE-EQ")
        self.assertEqual(result.interval, "ONE_DAY")
        self.assertEqual(result.source, "angel_one")
        self.assertEqual(result.source_retrieved_at, self.retrieved_at)
        self.assertEqual(
            result.zone_detection_evaluated_at,
            zones.evaluated_at,
        )
        self.assertEqual(result.pivot_left_strength, 2)
        self.assertEqual(result.pivot_right_strength, 2)
        self.assertEqual(result.tolerance_percentage, 0.5)
        self.assertEqual(result.minimum_touches, 2)

    def test_returns_empty_result_without_zones(self):
        series, zones = self.resistance_fixture()
        zones = zones.model_copy(update={"zones": []})

        result = track_support_resistance_lifecycle(series, zones)

        self.assertEqual(result.lifecycles, [])

    def test_rejects_market_series_for_different_instrument(self):
        series, zones = self.resistance_fixture()
        series = series.model_copy(update={"symbol_token": "9999"})

        with self.assertRaisesRegex(
            ValueError,
            "must describe the same source",
        ):
            track_support_resistance_lifecycle(series, zones)

    def test_rejects_candles_out_of_order(self):
        series, zones = self.resistance_fixture()
        candles = list(series.candles)
        candles[1], candles[2] = candles[2], candles[1]
        series = series.model_copy(update={"candles": candles})

        with self.assertRaisesRegex(
            ValueError,
            "unique timestamps",
        ):
            track_support_resistance_lifecycle(series, zones)

    def test_rejects_zone_pivot_price_not_matching_candle(self):
        series, zones = self.resistance_fixture()
        candles = list(series.candles)
        candles[0] = candles[0].model_copy(update={"high": 109.9})
        series = series.model_copy(update={"candles": candles})

        with self.assertRaisesRegex(
            ValueError,
            "price does not match market candle",
        ):
            track_support_resistance_lifecycle(series, zones)

    def test_rejects_zone_pivot_absent_from_series(self):
        series, zones = self.resistance_fixture()
        series = series.model_copy(update={"candles": series.candles[1:]})

        with self.assertRaisesRegex(
            ValueError,
            "zone-pivot timestamp is absent",
        ):
            track_support_resistance_lifecycle(series, zones)

    def test_rejects_zone_confirmation_absent_from_series(self):
        series, zones = self.resistance_fixture()
        candles = [
            candle
            for candle in series.candles
            if candle.timestamp != zones.zones[0].confirmed_at
        ]
        series = series.model_copy(update={"candles": candles})

        with self.assertRaisesRegex(
            ValueError,
            "confirmation timestamp is absent",
        ):
            track_support_resistance_lifecycle(series, zones)

    def test_rejects_invalid_as_of_time(self):
        series, zones = self.resistance_fixture()
        invalid_times = (
            datetime(2026, 8, 10),
            self.retrieved_at + timedelta(days=1),
            "2026-08-10",
        )

        for as_of in invalid_times:
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    track_support_resistance_lifecycle(
                        series,
                        zones,
                        as_of=as_of,
                    )

    def test_rejects_evaluation_before_zone_detection(self):
        series, zones = self.resistance_fixture()

        with self.assertRaisesRegex(
            ValueError,
            "cannot precede zone detection",
        ):
            track_support_resistance_lifecycle(
                series,
                zones,
                as_of=zones.evaluated_at - timedelta(days=1),
            )


if __name__ == "__main__":
    unittest.main()
