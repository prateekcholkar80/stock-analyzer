import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalProvenance,
    SignalStrength,
    TechnicalSignalEvidence,
    TechnicalSignalSnapshot,
)


class TechnicalSignalEvidenceModelTests(unittest.TestCase):
    def setUp(self):
        self.observed_at = datetime(2026, 8, 18, tzinfo=UTC)

    def build_evidence(self, **overrides):
        values = {
            "evidence_id": "ema_20_above_50",
            "name": "EMA 20 above EMA 50",
            "category": SignalCategory.TREND,
            "direction": SignalDirection.BULLISH,
            "strength": SignalStrength.MODERATE,
            "source": "talib.EMA",
            "explanation": (
                "The fast exponential moving average is above the "
                "slow average."
            ),
            "observed_at": self.observed_at,
            "available_at": self.observed_at,
            "observed_values": {
                "fast_ema": 1325.5,
                "slow_ema": 1310.2,
                "relation": "above",
            },
            "parameters": {
                "fast_period": 20,
                "slow_period": 50,
            },
        }
        values.update(overrides)

        return TechnicalSignalEvidence(**values)

    def test_builds_deterministic_signal_evidence(self):
        evidence = self.build_evidence()

        self.assertEqual(evidence.category, SignalCategory.TREND)
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(evidence.available_at, self.observed_at)

    def test_supports_delayed_price_action_confirmation(self):
        confirmed_at = self.observed_at + timedelta(days=2)

        evidence = self.build_evidence(
            evidence_id="swing_high_2026-08-18",
            name="Confirmed swing high",
            category=SignalCategory.PRICE_ACTION,
            direction=SignalDirection.BEARISH,
            source="swing_pivots",
            observed_values={"pivot_price": 1340.0},
            available_at=confirmed_at,
        )

        self.assertEqual(evidence.observed_at, self.observed_at)
        self.assertEqual(evidence.available_at, confirmed_at)

    def test_accepts_all_supported_observed_value_types(self):
        evidence = self.build_evidence(
            observed_values={
                "confirmed": True,
                "period": 20,
                "value": 1325.5,
                "relation": "above",
            }
        )

        self.assertEqual(len(evidence.observed_values), 4)

    def test_rejects_invalid_evidence_identifier(self):
        for evidence_id in ("", " ", "ema crossover", "_ema"):
            with self.subTest(evidence_id=evidence_id):
                with self.assertRaises(ValidationError):
                    self.build_evidence(evidence_id=evidence_id)

    def test_rejects_blank_descriptive_text(self):
        for field_name in ("name", "source", "explanation"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(
                    ValidationError,
                    "signal evidence text cannot be blank",
                ):
                    self.build_evidence(**{field_name: "   "})

    def test_rejects_empty_observed_values(self):
        with self.assertRaises(ValidationError):
            self.build_evidence(observed_values={})

    def test_rejects_blank_value_and_parameter_names(self):
        invalid_fields = (
            {"observed_values": {" ": 1325.5}},
            {"parameters": {" ": 20}},
        )

        for overrides in invalid_fields:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(
                    ValidationError,
                    "names cannot be blank",
                ):
                    self.build_evidence(**overrides)

    def test_rejects_blank_text_values(self):
        invalid_fields = (
            {"observed_values": {"relation": "  "}},
            {"parameters": {"method": "  "}},
        )

        for overrides in invalid_fields:
            with self.subTest(overrides=overrides):
                with self.assertRaisesRegex(
                    ValidationError,
                    "text .* values cannot be blank",
                ):
                    self.build_evidence(**overrides)

    def test_rejects_non_finite_numeric_values(self):
        for invalid_value in (
            float("nan"),
            float("inf"),
            float("-inf"),
        ):
            with self.subTest(invalid_value=invalid_value):
                with self.assertRaisesRegex(
                    ValidationError,
                    "numeric observed-value values must be finite",
                ):
                    self.build_evidence(
                        observed_values={"value": invalid_value}
                    )

                with self.assertRaisesRegex(
                    ValidationError,
                    "numeric signal-parameter values must be finite",
                ):
                    self.build_evidence(
                        parameters={"threshold": invalid_value}
                    )

    def test_rejects_timestamp_without_timezone(self):
        for field_name in ("observed_at", "available_at"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(
                    ValidationError,
                    "timestamps must include timezone information",
                ):
                    self.build_evidence(
                        **{field_name: datetime(2026, 8, 18)}
                    )

    def test_rejects_availability_before_observation(self):
        with self.assertRaisesRegex(
            ValidationError,
            "availability cannot precede observation",
        ):
            self.build_evidence(
                available_at=self.observed_at - timedelta(seconds=1)
            )

    def test_rejects_non_deterministic_provenance(self):
        with self.assertRaises(ValidationError):
            self.build_evidence(provenance="ai_generated")

    def test_evidence_is_immutable(self):
        evidence = self.build_evidence()

        with self.assertRaises(ValidationError):
            evidence.direction = SignalDirection.BEARISH


class TechnicalSignalSnapshotModelTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 18, tzinfo=UTC)
        self.retrieved_at = datetime(2026, 8, 20, tzinfo=UTC)
        self.evidence = [
            self.build_evidence(
                "ema_trend",
                SignalDirection.BULLISH,
                SignalCategory.TREND,
                day=0,
            ),
            self.build_evidence(
                "rsi_neutral",
                SignalDirection.NEUTRAL,
                SignalCategory.MOMENTUM,
                day=1,
            ),
            self.build_evidence(
                "bearish_fvg",
                SignalDirection.BEARISH,
                SignalCategory.PRICE_ACTION,
                day=2,
            ),
        ]

    def build_evidence(
        self,
        evidence_id,
        direction,
        category,
        *,
        day,
    ):
        timestamp = self.first_timestamp + timedelta(days=day)
        return TechnicalSignalEvidence(
            evidence_id=evidence_id,
            name=evidence_id.replace("_", " ").title(),
            category=category,
            direction=direction,
            strength=SignalStrength.MODERATE,
            source="test_rule",
            explanation="Deterministic test evidence.",
            observed_at=timestamp,
            available_at=timestamp,
            observed_values={"value": 1.0},
        )

    def build_snapshot(self, **overrides):
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "source": "angel_one",
            "source_retrieved_at": self.retrieved_at,
            "evaluated_at": self.retrieved_at,
            "evidence": self.evidence,
        }
        values.update(overrides)

        return TechnicalSignalSnapshot(**values)

    def test_builds_snapshot_with_auditable_direction_counts(self):
        snapshot = self.build_snapshot()

        self.assertEqual(snapshot.bullish_count, 1)
        self.assertEqual(snapshot.bearish_count, 1)
        self.assertEqual(snapshot.neutral_count, 1)
        self.assertTrue(snapshot.has_directional_conflict)

    def test_allows_empty_snapshot_during_indicator_warmup(self):
        snapshot = self.build_snapshot(evidence=[])

        self.assertEqual(snapshot.bullish_count, 0)
        self.assertEqual(snapshot.bearish_count, 0)
        self.assertEqual(snapshot.neutral_count, 0)
        self.assertFalse(snapshot.has_directional_conflict)

    def test_single_direction_does_not_report_conflict(self):
        snapshot = self.build_snapshot(evidence=[self.evidence[0]])

        self.assertFalse(snapshot.has_directional_conflict)

    def test_preserves_instrument_timeframe_and_source_metadata(self):
        snapshot = self.build_snapshot()

        self.assertEqual(snapshot.exchange, "NSE")
        self.assertEqual(snapshot.symbol_token, "2885")
        self.assertEqual(snapshot.symbol, "RELIANCE-EQ")
        self.assertEqual(snapshot.interval, "ONE_DAY")
        self.assertEqual(snapshot.source, "angel_one")
        self.assertEqual(snapshot.source_retrieved_at, self.retrieved_at)
        self.assertEqual(snapshot.evaluated_at, self.retrieved_at)

    def test_rejects_blank_metadata(self):
        metadata_fields = (
            "exchange",
            "symbol_token",
            "symbol",
            "interval",
            "source",
        )

        for field_name in metadata_fields:
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(
                    ValidationError,
                    "technical-signal metadata cannot be blank",
                ):
                    self.build_snapshot(**{field_name: "   "})

    def test_rejects_timestamp_without_timezone(self):
        for field_name in ("source_retrieved_at", "evaluated_at"):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(
                    ValidationError,
                    "timestamps must include timezone information",
                ):
                    self.build_snapshot(
                        **{field_name: datetime(2026, 8, 20)}
                    )

    def test_rejects_evaluation_after_source_retrieval(self):
        with self.assertRaisesRegex(
            ValidationError,
            "evaluation cannot follow source retrieval",
        ):
            self.build_snapshot(
                evaluated_at=self.retrieved_at + timedelta(seconds=1)
            )

    def test_rejects_evidence_unavailable_at_evaluation_time(self):
        with self.assertRaisesRegex(
            ValidationError,
            "cannot contain future evidence",
        ):
            self.build_snapshot(
                evaluated_at=self.first_timestamp + timedelta(days=1)
            )

    def test_rejects_evidence_out_of_availability_order(self):
        with self.assertRaisesRegex(
            ValidationError,
            "must be ordered by availability",
        ):
            self.build_snapshot(evidence=list(reversed(self.evidence)))

    def test_allows_same_availability_time_for_multiple_evidence(self):
        second = self.build_evidence(
            "macd_trend",
            SignalDirection.BULLISH,
            SignalCategory.MOMENTUM,
            day=0,
        )

        snapshot = self.build_snapshot(
            evidence=[self.evidence[0], second]
        )

        self.assertEqual(snapshot.bullish_count, 2)

    def test_rejects_case_insensitive_duplicate_evidence_ids(self):
        duplicate = self.build_evidence(
            "EMA_TREND",
            SignalDirection.BEARISH,
            SignalCategory.TREND,
            day=1,
        )

        with self.assertRaisesRegex(
            ValidationError,
            "identifiers must be unique",
        ):
            self.build_snapshot(
                evidence=[self.evidence[0], duplicate]
            )

    def test_snapshot_is_immutable(self):
        snapshot = self.build_snapshot()

        with self.assertRaises(ValidationError):
            snapshot.interval = "ONE_WEEK"


if __name__ == "__main__":
    unittest.main()
