import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.fair_value_gaps import (
    detect_fair_value_gaps,
    track_fair_value_gap_lifecycle,
)
from app.analytics.price_action_signals import (
    generate_fair_value_gap_signal,
)
from app.exceptions import InsufficientDataError
from app.models.market import Candle, HistoricalCandleSeries
from app.models.price_action import (
    FairValueGap,
    FairValueGapDetectionResult,
    FairValueGapDirection,
    FairValueGapStatus,
)
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalProvenance,
    SignalStrength,
)


class FairValueGapSignalTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.retrieved_at = self.first_timestamp + timedelta(days=10)

    def timestamp(self, day):
        return self.first_timestamp + timedelta(days=day)

    def candle(self, day, *, open_price, high, low, close, volume=1_000):
        return Candle(
            timestamp=self.timestamp(day),
            open=open_price,
            high=high,
            low=low,
            close=close,
            volume=volume,
        )

    def bullish_formation(self, detection_close=109.0):
        detection_high = max(110.0, detection_close)
        return [
            self.candle(
                0,
                open_price=100.0,
                high=102.0,
                low=99.0,
                close=101.0,
            ),
            self.candle(
                1,
                open_price=101.0,
                high=108.0,
                low=100.0,
                close=107.0,
            ),
            self.candle(
                2,
                open_price=max(103.0, detection_close),
                high=detection_high,
                low=103.0,
                close=detection_close,
            ),
        ]

    def bearish_formation(self, detection_close=101.0):
        return [
            self.candle(
                0,
                open_price=110.0,
                high=111.0,
                low=108.0,
                close=109.0,
            ),
            self.candle(
                1,
                open_price=109.0,
                high=110.0,
                low=101.0,
                close=102.0,
            ),
            self.candle(
                2,
                open_price=106.0,
                high=107.0,
                low=min(100.0, detection_close),
                close=detection_close,
            ),
        ]

    def build_series(self, candles, **overrides):
        fields = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "candles": candles,
            "retrieved_at": self.retrieved_at,
            "source": "angel_one",
        }
        fields.update(overrides)
        return HistoricalCandleSeries(**fields)

    def build_gap(
        self,
        *,
        direction=FairValueGapDirection.BULLISH,
        detected_day=2,
        lower=102.0,
        upper=103.0,
        atr_value=None,
        **overrides,
    ):
        fields = {
            "direction": direction,
            "first_candle_at": self.timestamp(detected_day - 2),
            "impulse_candle_at": self.timestamp(detected_day - 1),
            "detected_at": self.timestamp(detected_day),
            "lower_price": lower,
            "upper_price": upper,
            "atr_value": atr_value,
        }
        fields.update(overrides)
        return FairValueGap(**fields)

    def build_result(self, gaps=(), **overrides):
        fields = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "source": "angel_one",
            "source_retrieved_at": self.retrieved_at,
            "gaps": list(gaps),
        }
        fields.update(overrides)
        return FairValueGapDetectionResult(**fields)

    def detect(self, candles, *, atr_value=None):
        series = self.build_series(candles)
        result = detect_fair_value_gaps(series)
        if atr_value is not None:
            gap = result.gaps[0].model_copy(
                update={"atr_value": atr_value}
            )
            result = result.model_copy(update={"gaps": [gap]})
        return result, series

    def test_generates_moderate_bullish_active_gap_context(self):
        result, series = self.detect(
            self.bullish_formation(detection_close=103.0)
        )

        evidence = generate_fair_value_gap_signal(result, series)

        self.assertEqual(evidence.category, SignalCategory.PRICE_ACTION)
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "active bullish gap open",
        )
        self.assertEqual(
            evidence.observed_values["selected_gap_status"],
            "open",
        )
        self.assertEqual(
            evidence.observed_values["current_price_location"],
            "inside_gap",
        )
        self.assertEqual(
            evidence.observed_values["selected_gap_distance_percentage"],
            0.0,
        )
        self.assertIn("not entry confirmation", evidence.explanation)

    def test_generates_bearish_active_gap_context(self):
        result, series = self.detect(self.bearish_formation())

        evidence = generate_fair_value_gap_signal(result, series)

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["condition"],
            "active bearish gap open",
        )

    def test_near_large_atr_normalized_gap_is_strong(self):
        result, series = self.detect(
            self.bullish_formation(detection_close=103.0),
            atr_value=1.0,
        )

        evidence = generate_fair_value_gap_signal(result, series)

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["selected_gap_atr_multiple"],
            1.0,
        )

    def test_far_atr_significant_gap_is_moderate(self):
        result, series = self.detect(
            self.bullish_formation(),
            atr_value=2.0,
        )

        evidence = generate_fair_value_gap_signal(result, series)

        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertGreater(
            evidence.observed_values["selected_gap_distance_percentage"],
            1.0,
        )

    def test_far_gap_without_atr_context_is_weak(self):
        result, series = self.detect(self.bullish_formation())

        evidence = generate_fair_value_gap_signal(result, series)

        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertNotIn(
            "selected_gap_atr_multiple",
            evidence.observed_values,
        )

    def test_custom_strength_thresholds_are_preserved(self):
        result, series = self.detect(
            self.bullish_formation(detection_close=103.0),
            atr_value=1.0,
        )

        evidence = generate_fair_value_gap_signal(
            result,
            series,
            proximity_threshold_percentage=0.25,
            moderate_atr_multiple=1.5,
            strong_atr_multiple=2.0,
        )

        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.parameters["proximity_threshold_percentage"],
            0.25,
        )

    def test_selects_nearest_active_gap(self):
        candles = self.bullish_formation(detection_close=103.0)
        series = self.build_series(candles)
        near = self.build_gap(lower=102.0, upper=103.0)
        far = self.build_gap(lower=90.0, upper=91.0)
        result = self.build_result([far, near])

        evidence = generate_fair_value_gap_signal(result, series)

        self.assertEqual(
            evidence.observed_values["selected_gap_lower_price"],
            102.0,
        )

    def test_nearest_gap_tie_prefers_most_recent_detection(self):
        candles = [
            self.candle(
                day,
                open_price=110.0,
                high=111.0,
                low=109.0,
                close=110.0,
            )
            for day in range(5)
        ]
        series = self.build_series(candles)
        older = self.build_gap(
            detected_day=2,
            lower=100.0,
            upper=101.0,
        )
        recent = self.build_gap(
            detected_day=4,
            lower=119.0,
            upper=120.0,
        )
        result = self.build_result([older, recent])

        evidence = generate_fair_value_gap_signal(result, series)

        self.assertEqual(
            evidence.observed_values["selected_gap_detected_at"],
            self.timestamp(4).isoformat(),
        )

    def test_as_of_recomputes_final_fill_as_partial_at_prior_time(self):
        formation = self.bullish_formation()
        partial = self.candle(
            3,
            open_price=104.0,
            high=106.0,
            low=102.5,
            close=105.0,
        )
        filled = self.candle(
            4,
            open_price=103.0,
            high=105.0,
            low=101.5,
            close=102.5,
        )
        detection, _ = self.detect(formation)
        full_series = self.build_series(formation + [partial, filled])
        final_result = track_fair_value_gap_lifecycle(
            detection,
            full_series,
        )
        self.assertEqual(
            final_result.gaps[0].status,
            FairValueGapStatus.FILLED,
        )

        evidence = generate_fair_value_gap_signal(
            final_result,
            full_series,
            as_of=partial.timestamp,
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(
            evidence.observed_values["selected_gap_status"],
            "partially_filled",
        )
        self.assertEqual(
            evidence.observed_values["selected_gap_fill_percentage"],
            50.0,
        )

    def test_filled_gap_is_neutral_resolved_history(self):
        formation = self.bullish_formation()
        filled = self.candle(
            3,
            open_price=103.0,
            high=105.0,
            low=101.5,
            close=102.5,
        )
        detection, _ = self.detect(formation)
        series = self.build_series(formation + [filled])
        tracked = track_fair_value_gap_lifecycle(detection, series)

        evidence = generate_fair_value_gap_signal(tracked, series)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["condition"],
            "latest gap filled",
        )
        self.assertEqual(evidence.observed_values["filled_gap_count"], 1)
        self.assertIn("resolved history", evidence.explanation)

    def test_invalidated_gap_is_neutral_resolved_history(self):
        formation = self.bullish_formation()
        invalidating = self.candle(
            3,
            open_price=102.5,
            high=103.0,
            low=101.0,
            close=101.5,
        )
        detection, _ = self.detect(formation)
        series = self.build_series(formation + [invalidating])
        tracked = track_fair_value_gap_lifecycle(detection, series)

        evidence = generate_fair_value_gap_signal(tracked, series)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(
            evidence.observed_values["condition"],
            "latest gap invalidated",
        )
        self.assertEqual(
            evidence.observed_values["invalidated_gap_count"],
            1,
        )

    def test_expired_gap_requires_and_uses_lifecycle_maximum_age(self):
        formation = self.bullish_formation()
        future = [
            self.candle(
                3,
                open_price=105.0,
                high=107.0,
                low=104.0,
                close=106.0,
            ),
            self.candle(
                4,
                open_price=106.0,
                high=108.0,
                low=104.0,
                close=107.0,
            ),
        ]
        detection, _ = self.detect(formation)
        series = self.build_series(formation + future)
        expired = track_fair_value_gap_lifecycle(
            detection,
            series,
            max_age_candles=2,
        )

        with self.assertRaisesRegex(
            ValueError,
            "max_age_candles is required",
        ):
            generate_fair_value_gap_signal(expired, series)

        evidence = generate_fair_value_gap_signal(
            expired,
            series,
            max_age_candles=2,
        )

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(
            evidence.observed_values["selected_gap_status"],
            "expired",
        )
        self.assertEqual(evidence.observed_values["expired_gap_count"], 1)

    def test_as_of_before_expiration_keeps_gap_active(self):
        formation = self.bullish_formation()
        future = [
            self.candle(
                3,
                open_price=105.0,
                high=107.0,
                low=104.0,
                close=106.0,
            ),
            self.candle(
                4,
                open_price=106.0,
                high=108.0,
                low=104.0,
                close=107.0,
            ),
        ]
        detection, _ = self.detect(formation)
        series = self.build_series(formation + future)
        expired = track_fair_value_gap_lifecycle(
            detection,
            series,
            max_age_candles=2,
        )

        evidence = generate_fair_value_gap_signal(
            expired,
            series,
            max_age_candles=2,
            as_of=future[0].timestamp,
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(
            evidence.observed_values["selected_gap_status"],
            "open",
        )

    def test_future_detected_gap_is_not_available_at_as_of(self):
        candles = [
            self.candle(
                day,
                open_price=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
            )
            for day in range(6)
        ]
        series = self.build_series(candles)
        future_gap = self.build_gap(
            detected_day=5,
            lower=102.0,
            upper=103.0,
        )
        result = self.build_result([future_gap])

        evidence = generate_fair_value_gap_signal(
            result,
            series,
            as_of=self.timestamp(2),
        )

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.evidence_id, "fvg_context.none")
        self.assertEqual(
            evidence.observed_values["available_gap_count"],
            0,
        )

    def test_empty_detection_result_is_neutral(self):
        series = self.build_series(self.bullish_formation())
        result = self.build_result()

        evidence = generate_fair_value_gap_signal(result, series)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(evidence.evidence_id, "fvg_context.none")
        self.assertEqual(
            evidence.observed_values["condition"],
            "no available gap",
        )

    def test_evidence_identifier_is_timezone_normalized_and_valid(self):
        result, series = self.detect(
            self.bullish_formation(detection_close=103.0)
        )

        evidence = generate_fair_value_gap_signal(result, series)

        self.assertEqual(
            evidence.evidence_id,
            "fvg_context.bullish.20260803T000000000000Z",
        )

    def test_status_counts_only_include_gaps_available_at_as_of(self):
        candles = [
            self.candle(
                day,
                open_price=110.0,
                high=111.0,
                low=109.0,
                close=110.0,
            )
            for day in range(6)
        ]
        series = self.build_series(candles)
        available = self.build_gap(
            detected_day=2,
            lower=100.0,
            upper=101.0,
        )
        future = self.build_gap(
            detected_day=5,
            lower=119.0,
            upper=120.0,
        )
        result = self.build_result([available, future])

        evidence = generate_fair_value_gap_signal(
            result,
            series,
            as_of=self.timestamp(3),
        )

        self.assertEqual(
            evidence.observed_values["available_gap_count"],
            1,
        )
        self.assertEqual(evidence.observed_values["active_gap_count"], 1)

    def test_as_of_between_candles_uses_latest_completed_candle(self):
        result, series = self.detect(self.bullish_formation())
        evaluation = self.timestamp(2) + timedelta(hours=12)

        evidence = generate_fair_value_gap_signal(
            result,
            series,
            as_of=evaluation,
        )

        self.assertEqual(evidence.observed_at, self.timestamp(2))

    def test_rejects_as_of_before_first_market_candle(self):
        result, series = self.detect(self.bullish_formation())

        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires an available market candle",
        ):
            generate_fair_value_gap_signal(
                result,
                series,
                as_of=self.timestamp(0) - timedelta(seconds=1),
            )

    def test_rejects_invalid_or_future_as_of(self):
        result, series = self.detect(self.bullish_formation())
        invalid_values = (
            datetime(2026, 8, 1),
            "2026-08-01",
            1,
            self.retrieved_at + timedelta(seconds=1),
        )

        for as_of in invalid_values:
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    generate_fair_value_gap_signal(
                        result,
                        series,
                        as_of=as_of,
                    )

    def test_rejects_zero_current_close_for_proximity(self):
        zero = self.candle(
            0,
            open_price=0.0,
            high=0.0,
            low=0.0,
            close=0.0,
        )
        series = self.build_series([zero])
        result = self.build_result()

        with self.assertRaisesRegex(ValueError, "requires a positive close"):
            generate_fair_value_gap_signal(result, series)

    def test_rejects_mismatched_identity_or_source(self):
        result, series = self.detect(self.bullish_formation())

        for field_name, value in (
            ("exchange", "BSE"),
            ("symbol_token", "9999"),
            ("symbol", "TCS-EQ"),
            ("interval", "ONE_HOUR"),
            ("source", "other"),
        ):
            with self.subTest(field_name=field_name):
                mismatched = series.model_copy(
                    update={field_name: value}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "same instrument, timeframe, and source",
                ):
                    generate_fair_value_gap_signal(result, mismatched)

    def test_rejects_unordered_or_duplicate_market_candles(self):
        result, series = self.detect(self.bullish_formation())
        invalid_orders = (
            [series.candles[1], series.candles[0], series.candles[2]],
            [series.candles[0], series.candles[0], series.candles[2]],
        )

        for candles in invalid_orders:
            with self.subTest(candles=candles):
                invalid = series.model_copy(update={"candles": candles})
                with self.assertRaisesRegex(
                    ValueError,
                    "unique timestamps in ascending order",
                ):
                    generate_fair_value_gap_signal(result, invalid)

    def test_rejects_gap_detection_missing_from_market_series(self):
        result, series = self.detect(self.bullish_formation())
        missing = series.model_copy(
            update={"candles": series.candles[:2]}
        )

        with self.assertRaisesRegex(
            ValueError,
            "detection timestamp is absent",
        ):
            generate_fair_value_gap_signal(result, missing)

    def test_rejects_gap_or_lifecycle_event_after_source_retrieval(self):
        future_retrieval = self.timestamp(3)
        detected_after = self.build_gap(detected_day=4)
        result = self.build_result(
            [detected_after],
            source_retrieved_at=future_retrieval,
        )
        candles = [
            self.candle(
                day,
                open_price=110.0,
                high=111.0,
                low=109.0,
                close=110.0,
            )
            for day in range(5)
        ]
        series = self.build_series(
            candles,
            retrieved_at=future_retrieval,
        )

        with self.assertRaisesRegex(
            ValueError,
            "detection cannot follow source retrieval",
        ):
            generate_fair_value_gap_signal(result, series)

        resolved_after = self.build_gap(
            status=FairValueGapStatus.FILLED,
            fill_percentage=100.0,
            first_touched_at=self.timestamp(3),
            resolved_at=self.timestamp(4),
        )
        resolved_result = self.build_result(
            [resolved_after],
            source_retrieved_at=future_retrieval,
        )
        with self.assertRaisesRegex(
            ValueError,
            "lifecycle event cannot follow source retrieval",
        ):
            generate_fair_value_gap_signal(resolved_result, series)

    def test_rejects_invalid_numeric_thresholds(self):
        result, series = self.detect(self.bullish_formation())
        names = (
            "proximity_threshold_percentage",
            "moderate_atr_multiple",
            "strong_atr_multiple",
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
                        generate_fair_value_gap_signal(
                            result,
                            series,
                            **{name: value},
                        )

    def test_rejects_equal_or_reversed_atr_strength_thresholds(self):
        result, series = self.detect(self.bullish_formation())

        for moderate, strong in ((1.0, 1.0), (2.0, 1.0)):
            with self.subTest(moderate=moderate, strong=strong):
                with self.assertRaisesRegex(
                    ValueError,
                    "strong ATR multiple must exceed",
                ):
                    generate_fair_value_gap_signal(
                        result,
                        series,
                        moderate_atr_multiple=moderate,
                        strong_atr_multiple=strong,
                    )

    def test_rejects_invalid_maximum_age(self):
        result, series = self.detect(self.bullish_formation())

        for value in (True, 1.5, "2", 0, -1):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    generate_fair_value_gap_signal(
                        result,
                        series,
                        max_age_candles=value,
                    )


if __name__ == "__main__":
    unittest.main()
