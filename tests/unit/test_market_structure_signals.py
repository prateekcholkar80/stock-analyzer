import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.price_action_signals import (
    generate_market_structure_signal,
)
from app.analytics.swing_pivots import detect_swing_pivots
from app.exceptions import InsufficientDataError
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalProvenance,
    SignalStrength,
)


class MarketStructureSignalTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.retrieved_at = datetime(2026, 8, 30, tzinfo=UTC)
        closes = (
            105.0,
            106.0,
            108.0,
            102.0,
            107.0,
            108.0,
            112.0,
            105.0,
            110.0,
            114.0,
            116.0,
            104.0,
            102.0,
            101.0,
            100.0,
            108.0,
            102.0,
            100.0,
            97.0,
            113.0,
        )
        highs = (
            107.0,
            108.0,
            113.0,
            104.0,
            109.0,
            110.0,
            115.0,
            107.0,
            112.0,
            115.0,
            117.0,
            106.0,
            104.0,
            103.0,
            102.0,
            112.0,
            104.0,
            102.0,
            100.0,
            114.0,
        )
        lows = (
            103.0,
            104.0,
            106.0,
            100.0,
            105.0,
            106.0,
            109.0,
            103.0,
            107.0,
            112.0,
            114.0,
            103.0,
            101.0,
            99.0,
            98.0,
            100.0,
            100.0,
            99.0,
            96.0,
            111.0,
        )
        self.candles = [
            self.candle(index, close, high, low)
            for index, (close, high, low) in enumerate(
                zip(closes, highs, lows)
            )
        ]
        self.series = self.build_series(self.candles)
        self.pivots = detect_swing_pivots(self.series)

    def timestamp(self, day):
        return self.first_timestamp + timedelta(days=day)

    def candle(self, day, close, high, low):
        return Candle(
            timestamp=self.timestamp(day),
            open=close,
            high=high,
            low=low,
            close=close,
            volume=1_000 + day,
        )

    def build_series(self, candles, **overrides):
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "source": "angel_one",
            "candles": candles,
            "retrieved_at": self.retrieved_at,
        }
        values.update(overrides)
        return HistoricalCandleSeries(**values)

    def structure_only_fixture(self, second_high, second_low):
        candles = [
            self.candle(0, 100.0, 105.0, 95.0),
            self.candle(1, 104.0, 110.0, 100.0),
            self.candle(2, 92.0, 106.0, 90.0),
            self.candle(3, 103.0, second_high, 95.0),
            self.candle(4, 92.0, 107.0, second_low),
            self.candle(5, 100.0, 108.0, 96.0),
        ]
        series = self.build_series(candles)
        return series, detect_swing_pivots(
            series,
            left_strength=1,
            right_strength=1,
        )

    def test_latest_choch_is_strong_bullish_evidence(self):
        evidence = generate_market_structure_signal(
            self.pivots,
            self.series,
        )

        self.assertEqual(evidence.category, SignalCategory.PRICE_ACTION)
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "bullish change of character",
        )
        self.assertEqual(
            evidence.observed_values["latest_break_type"],
            "change_of_character",
        )
        self.assertEqual(
            evidence.observed_values["latest_break_bias_before"],
            "bearish",
        )
        self.assertEqual(
            evidence.observed_values["latest_break_bias_after"],
            "bullish",
        )
        self.assertEqual(
            evidence.observed_values["effective_bias"],
            "bullish",
        )
        self.assertIn("not a trade recommendation", evidence.explanation)

    def test_bullish_hh_hl_structure_without_break_is_moderate(self):
        evidence = generate_market_structure_signal(
            self.pivots,
            self.series,
            as_of=self.timestamp(9),
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bullish HH/HL structure",
        )
        self.assertEqual(
            evidence.observed_values["current_structure_bias"],
            "bullish",
        )
        self.assertEqual(
            evidence.observed_values["latest_high_classification"],
            "higher_high",
        )
        self.assertEqual(
            evidence.observed_values["latest_low_classification"],
            "higher_low",
        )
        self.assertEqual(evidence.observed_values["break_event_count"], 0)

    def test_bearish_lh_ll_structure_without_break_is_moderate(self):
        series, pivots = self.structure_only_fixture(108.0, 85.0)

        evidence = generate_market_structure_signal(pivots, series)

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bearish LH/LL structure",
        )
        self.assertEqual(
            evidence.observed_values["latest_high_classification"],
            "lower_high",
        )
        self.assertEqual(
            evidence.observed_values["latest_low_classification"],
            "lower_low",
        )

    def test_equal_high_low_structure_is_range_bound_and_neutral(self):
        series, pivots = self.structure_only_fixture(110.05, 89.95)

        evidence = generate_market_structure_signal(pivots, series)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.observed_values["current_structure_bias"],
            "range_bound",
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "range-bound equal-high/equal-low structure",
        )

    def test_mixed_structure_is_neutral_and_weak(self):
        series, pivots = self.structure_only_fixture(115.0, 85.0)

        evidence = generate_market_structure_signal(pivots, series)

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["current_structure_bias"],
            "mixed",
        )
        self.assertIn("classifications conflict", evidence.explanation)

    def test_undetermined_structure_is_neutral_and_weak(self):
        evidence = generate_market_structure_signal(
            self.pivots,
            self.series,
            as_of=self.timestamp(5),
        )

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["condition"],
            "undetermined market structure",
        )
        self.assertEqual(
            evidence.observed_values["market_structure_point_count"],
            0,
        )

    def test_bullish_bos_strength_uses_break_percentage_threshold(self):
        moderate = generate_market_structure_signal(
            self.pivots,
            self.series,
            as_of=self.timestamp(10),
        )
        strong = generate_market_structure_signal(
            self.pivots,
            self.series,
            strong_break_percentage=0.8,
            as_of=self.timestamp(10),
        )

        self.assertEqual(moderate.direction, SignalDirection.BULLISH)
        self.assertEqual(moderate.strength, SignalStrength.MODERATE)
        self.assertEqual(strong.strength, SignalStrength.STRONG)
        self.assertEqual(
            strong.observed_values["latest_break_type"],
            "break_of_structure",
        )
        self.assertGreater(
            strong.observed_values["latest_break_percentage"],
            0.8,
        )

    def test_bearish_choch_is_strong_reversal_evidence(self):
        evidence = generate_market_structure_signal(
            self.pivots,
            self.series,
            as_of=self.timestamp(12),
        )

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bearish change of character",
        )
        self.assertEqual(
            evidence.observed_values["effective_bias"],
            "bearish",
        )

    def test_bearish_bos_is_strong_when_break_exceeds_threshold(self):
        evidence = generate_market_structure_signal(
            self.pivots,
            self.series,
            as_of=self.timestamp(18),
        )

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bearish break of structure",
        )
        self.assertGreaterEqual(
            evidence.observed_values["latest_break_percentage"],
            1.0,
        )

    def test_unclassified_break_is_directional_but_weak(self):
        candles = [
            self.candle(0, 100.0, 105.0, 95.0),
            self.candle(1, 105.0, 110.0, 100.0),
            self.candle(2, 106.0, 108.0, 101.0),
            self.candle(3, 111.0, 112.0, 105.0),
        ]
        series = self.build_series(candles)
        pivots = detect_swing_pivots(
            series,
            left_strength=1,
            right_strength=1,
        )

        evidence = generate_market_structure_signal(pivots, series)

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bullish unclassified break",
        )
        self.assertEqual(
            evidence.observed_values["unclassified_break_count"],
            1,
        )
        self.assertIn("unclassified and weak", evidence.explanation)

    def test_as_of_recomputes_structure_and_breaks_without_future_data(self):
        expectations = (
            (5, "undetermined", 0, SignalDirection.NEUTRAL),
            (9, "bullish", 0, SignalDirection.BULLISH),
            (10, "bullish", 1, SignalDirection.BULLISH),
            (12, "bullish", 2, SignalDirection.BEARISH),
            (18, "bearish", 3, SignalDirection.BEARISH),
            (19, "bearish", 4, SignalDirection.BULLISH),
        )

        for day, bias, event_count, direction in expectations:
            with self.subTest(day=day):
                evidence = generate_market_structure_signal(
                    self.pivots,
                    self.series,
                    as_of=self.timestamp(day),
                )
                self.assertEqual(
                    evidence.observed_values["current_structure_bias"],
                    bias,
                )
                self.assertEqual(
                    evidence.observed_values["break_event_count"],
                    event_count,
                )
                self.assertEqual(evidence.direction, direction)
                self.assertEqual(evidence.observed_at, self.timestamp(day))

    def test_future_pivots_are_not_counted_at_as_of(self):
        evidence = generate_market_structure_signal(
            self.pivots,
            self.series,
            as_of=self.timestamp(9),
        )

        self.assertEqual(evidence.observed_values["confirmed_pivot_count"], 4)
        self.assertEqual(
            evidence.observed_values["market_structure_point_count"],
            2,
        )
        self.assertNotEqual(
            evidence.observed_values["latest_high_pivot_at"],
            self.timestamp(10).isoformat(),
        )

    def test_as_of_ignores_invalid_unused_future_pivot(self):
        future = self.pivots.pivots[-1].model_copy(
            update={"price": 999.0}
        )
        forged = self.pivots.model_copy(
            update={
                "pivots": [*self.pivots.pivots[:-1], future]
            }
        )

        evidence = generate_market_structure_signal(
            forged,
            self.series,
            as_of=self.timestamp(9),
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.observed_values["confirmed_pivot_count"], 4)

    def test_close_at_broken_level_does_not_create_break(self):
        candles = list(self.candles)
        candles[10] = candles[10].model_copy(update={"close": 115.0})
        series = self.build_series(candles)
        pivots = detect_swing_pivots(series)

        evidence = generate_market_structure_signal(
            pivots,
            series,
            as_of=self.timestamp(10),
        )

        self.assertEqual(evidence.observed_values["break_event_count"], 0)
        self.assertEqual(
            evidence.observed_values["condition"],
            "bullish HH/HL structure",
        )

    def test_custom_zero_tolerance_changes_near_equal_structure(self):
        series, pivots = self.structure_only_fixture(110.05, 89.95)

        evidence = generate_market_structure_signal(
            pivots,
            series,
            equality_tolerance_percentage=0.0,
        )

        self.assertEqual(
            evidence.observed_values["current_structure_bias"],
            "mixed",
        )
        self.assertEqual(
            evidence.parameters["equality_tolerance_percentage"],
            0.0,
        )

    def test_empty_pivot_result_is_neutral(self):
        candles = self.candles[:4]
        series = self.build_series(candles)
        pivots = detect_swing_pivots(
            series,
            left_strength=2,
            right_strength=2,
        )

        evidence = generate_market_structure_signal(pivots, series)

        self.assertEqual(pivots.pivots, [])
        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(
            evidence.evidence_id,
            "market_structure.bias.undetermined",
        )

    def test_evidence_identifier_contains_break_type_direction_and_time(self):
        evidence = generate_market_structure_signal(
            self.pivots,
            self.series,
        )

        self.assertEqual(
            evidence.evidence_id,
            "market_structure.change_of_character.bullish."
            "20260820T000000000000Z",
        )

    def test_as_of_between_candles_uses_latest_completed_candle(self):
        evidence = generate_market_structure_signal(
            self.pivots,
            self.series,
            as_of=self.timestamp(10) + timedelta(hours=12),
        )

        self.assertEqual(evidence.observed_at, self.timestamp(10))
        self.assertEqual(
            evidence.observed_values["latest_break_occurred_at"],
            self.timestamp(10).isoformat(),
        )

    def test_rejects_as_of_before_first_candle(self):
        with self.assertRaisesRegex(
            InsufficientDataError,
            "requires an available market candle",
        ):
            generate_market_structure_signal(
                self.pivots,
                self.series,
                as_of=self.timestamp(0) - timedelta(seconds=1),
            )

    def test_rejects_invalid_or_future_as_of(self):
        invalid_values = (
            datetime(2026, 8, 10),
            "2026-08-10",
            1,
            self.retrieved_at + timedelta(seconds=1),
        )

        for as_of in invalid_values:
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    generate_market_structure_signal(
                        self.pivots,
                        self.series,
                        as_of=as_of,
                    )

    def test_rejects_invalid_numeric_thresholds(self):
        names = (
            "equality_tolerance_percentage",
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
                        generate_market_structure_signal(
                            self.pivots,
                            self.series,
                            **{name: value},
                        )

    def test_rejects_mismatched_market_identity_or_retrieval(self):
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
                series = self.series.model_copy(
                    update={field_name: value}
                )
                with self.assertRaisesRegex(
                    ValueError,
                    "same instrument, timeframe, source, and retrieval",
                ):
                    generate_market_structure_signal(
                        self.pivots,
                        series,
                    )

    def test_rejects_unordered_or_duplicate_market_candles(self):
        invalid_orders = (
            [self.candles[1], self.candles[0], *self.candles[2:]],
            [self.candles[0], self.candles[0], *self.candles[2:]],
        )

        for candles in invalid_orders:
            with self.subTest(candles=candles):
                series = self.series.model_copy(update={"candles": candles})
                with self.assertRaisesRegex(
                    ValueError,
                    "unique timestamps in ascending order",
                ):
                    generate_market_structure_signal(
                        self.pivots,
                        series,
                    )

    def test_rejects_pivot_result_that_disagrees_with_market_series(self):
        forged = self.pivots.model_copy(
            update={"pivots": self.pivots.pivots[:-1]}
        )

        with self.assertRaisesRegex(
            ValueError,
            "does not match market series",
        ):
            generate_market_structure_signal(forged, self.series)


if __name__ == "__main__":
    unittest.main()
