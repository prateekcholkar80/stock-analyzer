import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.analytics.signal_profiles import (
    DEFAULT_LONG_TERM_CATEGORY_WEIGHTS,
    build_long_term_technical_profile,
)
from app.models.signals import (
    LongTermTechnicalProfile,
    LongTermTechnicalStance,
    SignalCategory,
    SignalDirection,
    SignalStrength,
    TechnicalSignalEvidence,
    TechnicalSignalSnapshot,
)


class LongTermTechnicalProfileTests(unittest.TestCase):
    def setUp(self):
        self.evaluated_at = datetime(2026, 8, 20, tzinfo=UTC)
        self.retrieved_at = self.evaluated_at + timedelta(hours=1)

    def evidence(
        self,
        evidence_id,
        category,
        direction,
        *,
        strength=SignalStrength.STRONG,
        source="test.long_term",
        available_at=None,
    ):
        timestamp = available_at or self.evaluated_at
        return TechnicalSignalEvidence(
            evidence_id=evidence_id,
            name=evidence_id.replace("_", " ").title(),
            category=category,
            direction=direction,
            strength=strength,
            source=source,
            explanation="Deterministic long-term test evidence.",
            observed_at=timestamp,
            available_at=timestamp,
            observed_values={"value": 1.0},
        )

    def snapshot(self, evidence=(), **overrides):
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "source": "angel_one",
            "source_retrieved_at": self.retrieved_at,
            "evaluated_at": self.evaluated_at,
            "evidence": list(evidence),
        }
        values.update(overrides)
        return TechnicalSignalSnapshot(**values)

    def one_per_category(self, direction):
        return [
            self.evidence(
                category.value,
                category,
                direction,
            )
            for category in SignalCategory
        ]

    def test_aligned_bullish_evidence_is_strong_holding_structure(self):
        profile = build_long_term_technical_profile(
            self.snapshot(
                self.one_per_category(SignalDirection.BULLISH)
            )
        )

        self.assertEqual(
            profile.stance,
            LongTermTechnicalStance.STRONG_BULLISH,
        )
        self.assertEqual(profile.direction, SignalDirection.BULLISH)
        self.assertEqual(profile.profile_id, "long_term_technical")
        self.assertAlmostEqual(profile.score, 100.0)
        self.assertAlmostEqual(profile.coverage_percentage, 100.0)
        self.assertAlmostEqual(profile.confidence_percentage, 100.0)

    def test_aligned_bearish_evidence_is_strong_deterioration(self):
        profile = build_long_term_technical_profile(
            self.snapshot(
                self.one_per_category(SignalDirection.BEARISH)
            )
        )

        self.assertEqual(
            profile.stance,
            LongTermTechnicalStance.STRONG_BEARISH,
        )
        self.assertEqual(profile.direction, SignalDirection.BEARISH)
        self.assertAlmostEqual(profile.score, -100.0)

    def test_all_neutral_evidence_is_neutral_consolidating(self):
        profile = build_long_term_technical_profile(
            self.snapshot(
                self.one_per_category(SignalDirection.NEUTRAL)
            )
        )

        self.assertEqual(
            profile.stance,
            LongTermTechnicalStance.NEUTRAL,
        )
        self.assertEqual(profile.direction, SignalDirection.NEUTRAL)
        self.assertEqual(profile.score, 0.0)
        self.assertEqual(profile.coverage_percentage, 100.0)

    def test_core_thesis_categories_meet_default_coverage(self):
        evidence = [
            self.evidence(
                "trend",
                SignalCategory.TREND,
                SignalDirection.BULLISH,
            ),
            self.evidence(
                "volume",
                SignalCategory.VOLUME,
                SignalDirection.BULLISH,
            ),
            self.evidence(
                "structure",
                SignalCategory.PRICE_ACTION,
                SignalDirection.BULLISH,
                source="price_action_signals.market_structure",
            ),
        ]

        profile = build_long_term_technical_profile(
            self.snapshot(evidence)
        )

        total_weight = sum(DEFAULT_LONG_TERM_CATEGORY_WEIGHTS.values())
        covered_weight = (
            DEFAULT_LONG_TERM_CATEGORY_WEIGHTS[SignalCategory.TREND]
            + DEFAULT_LONG_TERM_CATEGORY_WEIGHTS[SignalCategory.VOLUME]
            + DEFAULT_LONG_TERM_CATEGORY_WEIGHTS[
                SignalCategory.PRICE_ACTION
            ]
        )
        self.assertAlmostEqual(
            profile.coverage_percentage,
            covered_weight / total_weight * 100,
        )
        self.assertNotEqual(
            profile.stance,
            LongTermTechnicalStance.INSUFFICIENT_EVIDENCE,
        )

    def test_missing_core_category_is_insufficient_by_default(self):
        evidence = [
            self.evidence(
                "trend",
                SignalCategory.TREND,
                SignalDirection.BULLISH,
            ),
            self.evidence(
                "structure",
                SignalCategory.PRICE_ACTION,
                SignalDirection.BULLISH,
                source="price_action_signals.market_structure",
            ),
        ]

        profile = build_long_term_technical_profile(
            self.snapshot(evidence)
        )

        self.assertEqual(
            profile.stance,
            LongTermTechnicalStance.INSUFFICIENT_EVIDENCE,
        )
        self.assertLess(profile.coverage_percentage, 70.0)

    def test_fvg_is_horizon_adjusted_when_it_is_only_price_action(self):
        fvg = self.evidence(
            "fvg",
            SignalCategory.PRICE_ACTION,
            SignalDirection.BULLISH,
            source="price_action_signals.fair_value_gap_context",
        )

        profile = build_long_term_technical_profile(
            self.snapshot([fvg]),
            minimum_coverage_percentage=0,
        )

        self.assertAlmostEqual(profile.category_scores["price_action"], 35.0)
        self.assertAlmostEqual(profile.score, 35.0)
        self.assertEqual(profile.evidence_relevance_weights["fvg"], 0.35)

    def test_market_structure_retains_full_long_term_relevance(self):
        structure = self.evidence(
            "structure",
            SignalCategory.PRICE_ACTION,
            SignalDirection.BULLISH,
            source="price_action_signals.market_structure",
        )

        profile = build_long_term_technical_profile(
            self.snapshot([structure]),
            minimum_coverage_percentage=0,
        )

        self.assertEqual(profile.category_scores["price_action"], 100.0)
        self.assertEqual(
            profile.evidence_relevance_weights["structure"],
            1.0,
        )

    def test_fvg_cannot_overpower_opposing_market_structure(self):
        evidence = [
            self.evidence(
                "structure",
                SignalCategory.PRICE_ACTION,
                SignalDirection.BEARISH,
                source="price_action_signals.market_structure",
            ),
            self.evidence(
                "fvg",
                SignalCategory.PRICE_ACTION,
                SignalDirection.BULLISH,
                source="price_action_signals.fair_value_gap_context",
            ),
        ]

        profile = build_long_term_technical_profile(
            self.snapshot(evidence),
            minimum_coverage_percentage=0,
        )

        self.assertLess(profile.category_scores["price_action"], 0)
        self.assertEqual(profile.direction, SignalDirection.BEARISH)
        self.assertTrue(profile.has_directional_conflict)

    def test_short_horizon_oscillators_receive_reduced_relevance(self):
        sources = {
            "rsi": ("momentum_signals.rsi_mean_reversion", 0.65),
            "macd": ("momentum_signals.macd_momentum", 0.85),
            "stochastic": (
                "momentum_signals.stochastic_zone_crossover",
                0.5,
            ),
        }

        for evidence_id, (source, expected) in sources.items():
            with self.subTest(evidence_id=evidence_id):
                item = self.evidence(
                    evidence_id,
                    SignalCategory.MOMENTUM,
                    SignalDirection.BULLISH,
                    source=source,
                )
                profile = build_long_term_technical_profile(
                    self.snapshot([item]),
                    minimum_coverage_percentage=0,
                )
                self.assertAlmostEqual(
                    profile.category_scores["momentum"],
                    expected * 100,
                )

    def test_custom_source_relevance_is_applied_and_preserved(self):
        fvg = self.evidence(
            "fvg",
            SignalCategory.PRICE_ACTION,
            SignalDirection.BULLISH,
            source="price_action_signals.fair_value_gap_context",
        )

        profile = build_long_term_technical_profile(
            self.snapshot([fvg]),
            source_relevance_weights={
                "price_action_signals.fair_value_gap_context": 0.8,
            },
            minimum_coverage_percentage=0,
        )

        self.assertAlmostEqual(profile.category_scores["price_action"], 80.0)
        self.assertEqual(profile.evidence_relevance_weights["fvg"], 0.8)

    def test_unknown_evidence_source_defaults_to_full_relevance(self):
        item = self.evidence(
            "custom",
            SignalCategory.TREND,
            SignalDirection.BULLISH,
            source="custom.long_term_rule",
        )

        profile = build_long_term_technical_profile(
            self.snapshot([item]),
            minimum_coverage_percentage=0,
        )

        self.assertEqual(profile.evidence_relevance_weights["custom"], 1.0)
        self.assertEqual(profile.category_scores["trend"], 100.0)

    def test_daily_weekly_and_monthly_intervals_are_supported(self):
        for interval in ("ONE_DAY", "ONE_WEEK", "ONE_MONTH"):
            with self.subTest(interval=interval):
                profile = build_long_term_technical_profile(
                    self.snapshot(interval=interval)
                )
                self.assertEqual(profile.snapshot.interval, interval)

    def test_intraday_interval_is_rejected(self):
        with self.assertRaisesRegex(
            ValueError,
            "requires daily, weekly, or monthly",
        ):
            build_long_term_technical_profile(
                self.snapshot(interval="ONE_HOUR")
            )

    def test_default_long_term_weights_prioritize_thesis_categories(self):
        profile = build_long_term_technical_profile(self.snapshot())

        self.assertEqual(
            profile.category_weights,
            {
                category.value: weight
                for category, weight in (
                    DEFAULT_LONG_TERM_CATEGORY_WEIGHTS.items()
                )
            },
        )
        self.assertGreater(
            profile.category_weights["trend"],
            profile.category_weights["momentum"],
        )
        self.assertGreater(
            profile.category_weights["volume"],
            profile.category_weights["volatility"],
        )

    def test_custom_category_weight_changes_score(self):
        evidence = [
            self.evidence(
                "trend",
                SignalCategory.TREND,
                SignalDirection.BULLISH,
            ),
            self.evidence(
                "price",
                SignalCategory.PRICE_ACTION,
                SignalDirection.BEARISH,
                source="price_action_signals.market_structure",
            ),
            self.evidence(
                "volume",
                SignalCategory.VOLUME,
                SignalDirection.NEUTRAL,
            ),
        ]
        snapshot = self.snapshot(evidence)

        default = build_long_term_technical_profile(snapshot)
        trend_weighted = build_long_term_technical_profile(
            snapshot,
            category_weights={SignalCategory.TREND: 4.0},
        )

        self.assertGreater(trend_weighted.score, default.score)

    def test_empty_snapshot_is_explicitly_insufficient(self):
        profile = build_long_term_technical_profile(self.snapshot())

        self.assertEqual(
            profile.stance,
            LongTermTechnicalStance.INSUFFICIENT_EVIDENCE,
        )
        self.assertEqual(profile.score, 0.0)
        self.assertEqual(profile.coverage_percentage, 0.0)
        self.assertEqual(profile.evidence_relevance_weights, {})

    def test_rationale_explains_horizon_adjustment(self):
        profile = build_long_term_technical_profile(self.snapshot())

        self.assertIn("Short-lived evidence", profile.rationale)
        self.assertIn("not a trade recommendation", profile.rationale)

    def test_rejects_unsynchronized_evidence_by_default(self):
        evidence = [
            self.evidence(
                "older",
                SignalCategory.TREND,
                SignalDirection.BULLISH,
                available_at=self.evaluated_at - timedelta(days=1),
            ),
            self.evidence(
                "current",
                SignalCategory.VOLUME,
                SignalDirection.BULLISH,
            ),
        ]

        with self.assertRaisesRegex(
            ValueError,
            "must share one availability time",
        ):
            build_long_term_technical_profile(self.snapshot(evidence))

    def test_can_explicitly_allow_unsynchronized_evidence(self):
        evidence = [
            self.evidence(
                "older",
                SignalCategory.TREND,
                SignalDirection.BULLISH,
                available_at=self.evaluated_at - timedelta(days=1),
            ),
            self.evidence(
                "current",
                SignalCategory.VOLUME,
                SignalDirection.BULLISH,
            ),
        ]

        profile = build_long_term_technical_profile(
            self.snapshot(evidence),
            require_synchronized_evidence=False,
            minimum_coverage_percentage=0,
        )

        self.assertFalse(profile.synchronized_evidence_required)

    def test_rejects_invalid_category_weights(self):
        invalid_values = (True, "1", None, 0, -1, float("nan"))

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    build_long_term_technical_profile(
                        self.snapshot(),
                        category_weights={"trend": value},
                    )

        with self.assertRaisesRegex(ValueError, "unknown"):
            build_long_term_technical_profile(
                self.snapshot(),
                category_weights={"unknown": 1.0},
            )

    def test_rejects_invalid_source_relevance(self):
        invalid_values = (True, "1", None, 0, -1, 1.1, float("inf"))

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    build_long_term_technical_profile(
                        self.snapshot(),
                        source_relevance_weights={"source": value},
                    )

        with self.assertRaisesRegex(ValueError, "cannot be blank"):
            build_long_term_technical_profile(
                self.snapshot(),
                source_relevance_weights={" ": 0.5},
            )

    def test_rejects_invalid_thresholds_and_synchronization_setting(self):
        for name in (
            "minimum_coverage_percentage",
            "directional_threshold",
            "strong_threshold",
        ):
            for value in (True, "1", None, -1, 101, float("nan")):
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        build_long_term_technical_profile(
                            self.snapshot(),
                            **{name: value},
                        )

        with self.assertRaisesRegex(ValueError, "must exceed"):
            build_long_term_technical_profile(
                self.snapshot(),
                directional_threshold=60,
                strong_threshold=20,
            )

        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            build_long_term_technical_profile(
                self.snapshot(),
                require_synchronized_evidence=1,
            )


class LongTermTechnicalProfileModelTests(unittest.TestCase):
    def setUp(self):
        timestamp = datetime(2026, 8, 20, tzinfo=UTC)
        evidence = TechnicalSignalEvidence(
            evidence_id="structure",
            name="Structure",
            category=SignalCategory.PRICE_ACTION,
            direction=SignalDirection.BULLISH,
            strength=SignalStrength.STRONG,
            source="price_action_signals.market_structure",
            explanation="Long-term structure evidence.",
            observed_at=timestamp,
            available_at=timestamp,
            observed_values={"value": 1.0},
        )
        snapshot = TechnicalSignalSnapshot(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            source="angel_one",
            source_retrieved_at=timestamp,
            evaluated_at=timestamp,
            evidence=[evidence],
        )
        self.profile = build_long_term_technical_profile(
            snapshot,
            minimum_coverage_percentage=0,
        )

    def values(self):
        return self.profile.model_dump(exclude_computed_fields=True)

    def test_rejects_relevance_keys_not_matching_evidence(self):
        values = self.values()
        values["evidence_relevance_weights"] = {"other": 1.0}

        with self.assertRaisesRegex(
            ValidationError,
            "must match evidence",
        ):
            LongTermTechnicalProfile(**values)

    def test_rejects_invalid_relevance_value(self):
        values = self.values()
        values["evidence_relevance_weights"] = {"structure": 0.0}

        with self.assertRaisesRegex(
            ValidationError,
            "above 0 and at most 1",
        ):
            LongTermTechnicalProfile(**values)

    def test_rejects_category_score_inconsistent_with_relevance(self):
        values = self.values()
        values["category_scores"]["price_action"] = 50.0
        values["score"] = 50.0
        values["confidence_percentage"] = (
            50.0 * self.profile.coverage_percentage / 100
        )
        values["stance"] = LongTermTechnicalStance.BULLISH

        with self.assertRaisesRegex(
            ValidationError,
            "category scores do not match",
        ):
            LongTermTechnicalProfile(**values)


if __name__ == "__main__":
    unittest.main()
