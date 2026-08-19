import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.price_action_signals import (
    generate_support_resistance_lifecycle_signal,
)
from app.analytics.support_resistance_lifecycle import (
    track_support_resistance_lifecycle,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.price_action import (
    PriceZoneLifecycleStatus,
    PriceZoneType,
    SupportResistanceDetectionResult,
    SupportResistanceZone,
    SwingPivot,
    SwingPivotType,
)
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalProvenance,
    SignalStrength,
)


class SupportResistanceSignalTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.retrieved_at = self.timestamp(12)

    def timestamp(self, day):
        return self.first_timestamp + timedelta(days=day)

    def candle(self, day, *, open_price, high, low, close):
        return Candle(
            timestamp=self.timestamp(day),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=1_000 + day,
        )

    def pivot(self, pivot_type, candles, pivot_day, confirmation_day):
        candle = candles[pivot_day]
        return SwingPivot(
            pivot_type=pivot_type,
            pivot_at=candle.timestamp,
            confirmed_at=candles[confirmation_day].timestamp,
            price=(
                candle.high
                if pivot_type is SwingPivotType.HIGH
                else candle.low
            ),
            left_strength=2,
            right_strength=2,
        )

    def build_inputs(self, candles, zone):
        series = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            source="angel_one",
            candles=candles,
            retrieved_at=self.retrieved_at,
        )
        zones = SupportResistanceDetectionResult(
            exchange=series.exchange,
            symbol_token=series.symbol_token,
            symbol=series.symbol,
            interval=series.interval,
            source=series.source,
            source_retrieved_at=series.retrieved_at,
            evaluated_at=self.timestamp(5),
            pivot_left_strength=2,
            pivot_right_strength=2,
            tolerance_percentage=0.5,
            minimum_touches=2,
            zones=[zone],
        )
        return series, zones

    def resistance_fixture(self, future=()):
        candles = [
            self.candle(
                0, open_price=106, high=110, low=105, close=107
            ),
            self.candle(
                1, open_price=107, high=108, low=106, close=107
            ),
            self.candle(
                2, open_price=107, high=109, low=106, close=108
            ),
            self.candle(
                3, open_price=108, high=110.4, low=106, close=108
            ),
            self.candle(
                4, open_price=108, high=109, low=107, close=108
            ),
            self.candle(
                5, open_price=108, high=110, low=107, close=109
            ),
        ]
        pivots = [
            self.pivot(SwingPivotType.HIGH, candles, 0, 2),
            self.pivot(SwingPivotType.HIGH, candles, 3, 5),
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
                0, open_price=104, high=106, low=100, close=103
            ),
            self.candle(
                1, open_price=103, high=105, low=101, close=103
            ),
            self.candle(
                2, open_price=103, high=104, low=101, close=102
            ),
            self.candle(
                3, open_price=103, high=105, low=100.4, close=102
            ),
            self.candle(
                4, open_price=102, high=104, low=101, close=102
            ),
            self.candle(
                5, open_price=102, high=104, low=100.5, close=101
            ),
        ]
        pivots = [
            self.pivot(SwingPivotType.LOW, candles, 0, 2),
            self.pivot(SwingPivotType.LOW, candles, 3, 5),
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

    def combined_fixture(self, future=()):
        candles = [
            self.candle(
                0, open_price=105, high=110, low=100, close=105
            ),
            self.candle(
                1, open_price=105, high=108, low=101, close=105
            ),
            self.candle(
                2, open_price=105, high=109, low=101, close=106
            ),
            self.candle(
                3, open_price=106, high=110.4, low=100.4, close=106
            ),
            self.candle(
                4, open_price=106, high=109, low=101, close=107
            ),
            self.candle(
                5, open_price=107, high=110, low=100.5, close=109
            ),
        ]
        support_pivots = [
            self.pivot(SwingPivotType.LOW, candles, 0, 2),
            self.pivot(SwingPivotType.LOW, candles, 3, 5),
        ]
        resistance_pivots = [
            self.pivot(SwingPivotType.HIGH, candles, 0, 2),
            self.pivot(SwingPivotType.HIGH, candles, 3, 5),
        ]
        support = SupportResistanceZone(
            zone_type=PriceZoneType.SUPPORT,
            lower_price=100,
            upper_price=100.4,
            center_price=100.2,
            confirmed_at=candles[5].timestamp,
            pivots=support_pivots,
        )
        resistance = SupportResistanceZone(
            zone_type=PriceZoneType.RESISTANCE,
            lower_price=110,
            upper_price=110.4,
            center_price=110.2,
            confirmed_at=candles[5].timestamp,
            pivots=resistance_pivots,
        )
        series, zones = self.build_inputs(
            candles + list(future),
            support,
        )
        return series, zones.model_copy(
            update={"zones": [support, resistance]}
        )

    def resistance_progression(self):
        return [
            self.candle(
                6, open_price=109, high=112, low=108, close=111
            ),
            self.candle(
                7, open_price=111, high=112, low=110.8, close=111.5
            ),
            self.candle(
                8, open_price=111, high=111.5, low=110.2, close=110.6
            ),
            self.candle(
                9, open_price=110.6, high=112, low=110.5, close=111
            ),
        ]

    def support_progression(self):
        return [
            self.candle(
                6, open_price=101, high=101.5, low=99, close=99.5
            ),
            self.candle(
                7, open_price=99.5, high=99.8, low=98.5, close=99
            ),
            self.candle(
                8, open_price=99, high=100.2, low=98.8, close=99.8
            ),
            self.candle(
                9, open_price=99.8, high=99.9, low=98.5, close=99
            ),
        ]

    def tracked(self, fixture, future=()):
        series, zones = fixture(future)
        return track_support_resistance_lifecycle(series, zones), series

    def test_active_support_is_bullish_proximity_context(self):
        result, series = self.tracked(self.support_fixture)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.category, SignalCategory.PRICE_ACTION)
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "active support zone",
        )
        self.assertEqual(
            evidence.observed_values["selected_zone_status"],
            "active",
        )
        self.assertIn("touch alone", evidence.explanation)

    def test_active_resistance_is_bearish_proximity_context(self):
        result, series = self.tracked(self.resistance_fixture)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.observed_values["selected_zone_type"],
            "resistance",
        )

    def test_far_active_zone_is_weak(self):
        far = self.candle(
            6, open_price=100, high=101, low=99, close=100
        )
        result, series = self.tracked(
            self.resistance_fixture,
            [far],
        )

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertGreater(
            evidence.observed_values[
                "selected_zone_distance_percentage"
            ],
            1.0,
        )

    def test_configured_touch_threshold_can_make_active_zone_strong(self):
        result, series = self.tracked(self.support_fixture)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
            strong_touch_count=2,
        )

        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(evidence.parameters["strong_touch_count"], 2)

    def test_multiple_active_zones_select_nearest_zone(self):
        result, series = self.tracked(self.combined_fixture)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.observed_values["active_zone_count"], 2)
        self.assertEqual(
            evidence.observed_values["selected_zone_type"],
            "resistance",
        )

    def test_lifecycle_event_takes_priority_over_active_zone(self):
        breaking = self.candle(
            6, open_price=109, high=112, low=108, close=111
        )
        result, series = self.tracked(self.combined_fixture, [breaking])

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.observed_values["active_zone_count"], 1)
        self.assertEqual(evidence.observed_values["broken_zone_count"], 1)
        self.assertEqual(
            evidence.observed_values["selected_zone_status"],
            "broken",
        )
        self.assertEqual(
            evidence.observed_values["selected_zone_type"],
            "resistance",
        )

    def test_confirmed_resistance_break_is_bullish(self):
        future = self.resistance_progression()[:1]
        result, series = self.tracked(self.resistance_fixture, future)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.observed_values["condition"],
            "confirmed bullish break",
        )
        self.assertEqual(evidence.observed_values["broken_zone_count"], 1)
        self.assertEqual(
            evidence.observed_values["selected_break_direction"],
            "bullish",
        )
        self.assertIn("No qualifying retest", evidence.explanation)

    def test_confirmed_support_break_is_bearish(self):
        future = self.support_progression()[:1]
        result, series = self.tracked(self.support_fixture, future)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(
            evidence.observed_values["condition"],
            "confirmed bearish break",
        )

    def test_large_break_is_strong_under_configured_threshold(self):
        future = self.resistance_progression()[:1]
        result, series = self.tracked(self.resistance_fixture, future)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
            strong_break_percentage=0.5,
        )

        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertGreaterEqual(
            evidence.observed_values["selected_break_percentage"],
            0.5,
        )

    def test_retested_break_is_strong_but_not_role_reversed(self):
        future = self.resistance_progression()[:3]
        result, series = self.tracked(self.resistance_fixture, future)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["selected_zone_status"],
            "retested",
        )
        self.assertEqual(evidence.observed_values["retested_zone_count"], 1)
        self.assertNotIn(
            "selected_reversal_confirmed_at",
            evidence.observed_values,
        )
        self.assertIn("still requires", evidence.explanation)

    def test_role_reversal_reports_new_support_role(self):
        future = self.resistance_progression()
        result, series = self.tracked(self.resistance_fixture, future)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["selected_zone_status"],
            "role_reversed",
        )
        self.assertEqual(
            evidence.observed_values["selected_reversed_zone_type"],
            "support",
        )
        self.assertEqual(
            evidence.observed_values["role_reversed_zone_count"],
            1,
        )

    def test_support_role_reversal_reports_new_resistance_role(self):
        future = self.support_progression()
        result, series = self.tracked(self.support_fixture, future)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(
            evidence.observed_values["selected_reversed_zone_type"],
            "resistance",
        )

    def test_failed_bullish_break_is_strong_bearish_evidence(self):
        future = self.resistance_progression()[:1] + [
            self.candle(
                7, open_price=111, high=111.2, low=109, close=109.5
            )
        ]
        result, series = self.tracked(self.resistance_fixture, future)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["condition"],
            "failed bullish break",
        )
        self.assertEqual(
            evidence.observed_values["failed_break_zone_count"],
            1,
        )
        self.assertIn("rather than a valid", evidence.explanation)

    def test_failed_bearish_break_is_strong_bullish_evidence(self):
        future = self.support_progression()[:1] + [
            self.candle(
                7, open_price=99.5, high=101.2, low=99, close=101
            )
        ]
        result, series = self.tracked(self.support_fixture, future)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(
            evidence.observed_values["condition"],
            "failed bearish break",
        )

    def test_as_of_recomputes_every_state_without_future_leakage(self):
        future = self.resistance_progression()
        result, series = self.tracked(self.resistance_fixture, future)
        expectations = (
            (5, "active", SignalDirection.BEARISH),
            (6, "broken", SignalDirection.BULLISH),
            (7, "broken", SignalDirection.BULLISH),
            (8, "retested", SignalDirection.BULLISH),
            (9, "role_reversed", SignalDirection.BULLISH),
        )

        for day, status, direction in expectations:
            with self.subTest(day=day):
                evidence = generate_support_resistance_lifecycle_signal(
                    result,
                    series,
                    as_of=self.timestamp(day),
                )
                self.assertEqual(
                    evidence.observed_values["selected_zone_status"],
                    status,
                )
                self.assertEqual(evidence.direction, direction)
                self.assertEqual(evidence.observed_at, self.timestamp(day))

    def test_as_of_between_candles_uses_latest_completed_candle(self):
        future = self.resistance_progression()
        result, series = self.tracked(self.resistance_fixture, future)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
            as_of=self.timestamp(8) + timedelta(hours=12),
        )

        self.assertEqual(evidence.observed_at, self.timestamp(8))
        self.assertEqual(
            evidence.observed_values["selected_zone_status"],
            "retested",
        )

    def test_wick_without_close_beyond_boundary_remains_active(self):
        wick = self.candle(
            6, open_price=109, high=112, low=108, close=110.4
        )
        result, series = self.tracked(self.resistance_fixture, [wick])

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(
            evidence.observed_values["selected_zone_status"],
            "active",
        )
        self.assertEqual(evidence.observed_values["broken_zone_count"], 0)

    def test_empty_lifecycle_result_is_neutral(self):
        series, zones = self.resistance_fixture()
        empty_zones = zones.model_copy(update={"zones": []})
        result = track_support_resistance_lifecycle(series, empty_zones)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(evidence.evidence_id, "support_resistance.none")
        self.assertEqual(
            evidence.observed_values["condition"],
            "no available zone",
        )

    def test_evidence_identifier_is_timezone_normalized_and_valid(self):
        result, series = self.tracked(self.resistance_fixture)

        evidence = generate_support_resistance_lifecycle_signal(
            result,
            series,
        )

        self.assertEqual(
            evidence.evidence_id,
            "support_resistance.resistance.20260806T000000000000Z",
        )

    def test_rejects_mismatched_identity_source_or_retrieval(self):
        result, series = self.tracked(self.resistance_fixture)
        mismatches = (
            ("exchange", "BSE"),
            ("symbol_token", "9999"),
            ("symbol", "TCS-EQ"),
            ("interval", "ONE_HOUR"),
            ("source", "other"),
            ("retrieved_at", self.retrieved_at - timedelta(seconds=1)),
        )

        for field_name, value in mismatches:
            with self.subTest(field_name=field_name):
                mismatched = series.model_copy(
                    update={field_name: value}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "same instrument, timeframe, source, and retrieval",
                ):
                    generate_support_resistance_lifecycle_signal(
                        result,
                        mismatched,
                    )

    def test_rejects_unordered_or_duplicate_market_candles(self):
        result, series = self.tracked(self.resistance_fixture)
        invalid_orders = (
            [series.candles[1], series.candles[0], *series.candles[2:]],
            [series.candles[0], series.candles[0], *series.candles[2:]],
        )

        for candles in invalid_orders:
            with self.subTest(candles=candles):
                invalid = series.model_copy(update={"candles": candles})
                with self.assertRaisesRegex(
                    ValueError,
                    "unique timestamps in ascending order",
                ):
                    generate_support_resistance_lifecycle_signal(
                        result,
                        invalid,
                    )

    def test_rejects_zone_pivot_missing_from_market_series(self):
        result, series = self.tracked(self.resistance_fixture)
        invalid = series.model_copy(
            update={"candles": series.candles[1:]}
        )

        with self.assertRaisesRegex(
            ValueError,
            "zone-pivot timestamp is absent",
        ):
            generate_support_resistance_lifecycle_signal(result, invalid)

    def test_rejects_lifecycle_event_missing_from_market_series(self):
        future = self.resistance_progression()
        result, series = self.tracked(self.resistance_fixture, future)
        lifecycle = result.lifecycles[0].model_copy(
            update={
                "reversal_confirmed_at": (
                    future[-1].timestamp + timedelta(hours=1)
                )
            }
        )
        invalid = result.model_copy(update={"lifecycles": [lifecycle]})

        with self.assertRaisesRegex(
            ValueError,
            "event timestamp is absent",
        ):
            generate_support_resistance_lifecycle_signal(invalid, series)

    def test_rejects_lifecycle_that_disagrees_with_market_series(self):
        future = self.resistance_progression()[:1]
        result, series = self.tracked(self.resistance_fixture, future)
        lifecycle = result.lifecycles[0].model_copy(
            update={"break_close_price": 111.2}
        )
        invalid = result.model_copy(update={"lifecycles": [lifecycle]})

        with self.assertRaisesRegex(
            ValueError,
            "does not match market series",
        ):
            generate_support_resistance_lifecycle_signal(invalid, series)

    def test_rejects_invalid_evaluation_times(self):
        result, series = self.tracked(self.resistance_fixture)
        invalid_times = (
            datetime(2026, 8, 6),
            "2026-08-06",
            self.timestamp(4),
            self.retrieved_at + timedelta(seconds=1),
        )

        for as_of in invalid_times:
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    generate_support_resistance_lifecycle_signal(
                        result,
                        series,
                        as_of=as_of,
                    )

    def test_rejects_invalid_numeric_thresholds(self):
        result, series = self.tracked(self.resistance_fixture)
        names = (
            "proximity_threshold_percentage",
            "strong_break_percentage",
        )
        invalid_values = (
            True,
            "1",
            None,
            -0.1,
            float("nan"),
            float("inf"),
        )

        for name in names:
            for value in invalid_values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        generate_support_resistance_lifecycle_signal(
                            result,
                            series,
                            **{name: value},
                        )

    def test_rejects_invalid_strong_touch_count(self):
        result, series = self.tracked(self.resistance_fixture)

        for value in (True, 2.5, "3", 1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    generate_support_resistance_lifecycle_signal(
                        result,
                        series,
                        strong_touch_count=value,
                    )

    def test_rejects_zero_current_close_for_proximity(self):
        zero = self.candle(
            6, open_price=0, high=1, low=0, close=0
        )
        result, series = self.tracked(self.support_fixture, [zero])

        with self.assertRaisesRegex(ValueError, "requires a positive close"):
            generate_support_resistance_lifecycle_signal(result, series)


if __name__ == "__main__":
    unittest.main()
