import unittest
from datetime import UTC, datetime, timedelta, timezone

from pydantic import ValidationError

from app.analytics.signal_profiles import (
    DEFAULT_SWING_CATEGORY_WEIGHTS,
    build_swing_trading_signal_profile,
)
from app.models.signals import (
    SignalCategory,
    SignalContribution,
    SignalDirection,
    SignalStrength,
    SwingTradingSignalProfile,
    SwingTradingStance,
    TechnicalSignalEvidence,
    TechnicalSignalSnapshot,
)


class SwingTradingSignalProfileTests(unittest.TestCase):
    def setUp(self):
        self.evaluated_at = datetime(2026, 8, 20, tzinfo=UTC)
        self.retrieved_at = self.evaluated_at + timedelta(hours=1)

    def evidence(
        self,
        evidence_id,
        category,
        direction,
        strength=SignalStrength.STRONG,
        *,
        available_at=None,
    ):
        timestamp = available_at or self.evaluated_at
        return TechnicalSignalEvidence(
            evidence_id=evidence_id,
            name=evidence_id.replace("_", " ").title(),
            category=category,
            direction=direction,
            strength=strength,
            source="test.signal",
            explanation="Deterministic test evidence.",
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

    def one_per_category(self, direction, strength=SignalStrength.STRONG):
        return [
            self.evidence(
                f"{category.value}_signal",
                category,
                direction,
                strength,
            )
            for category in SignalCategory
        ]

    def test_all_aligned_strong_bullish_evidence_is_strong_setup(self):
        snapshot = self.snapshot(
            self.one_per_category(SignalDirection.BULLISH)
        )

        profile = build_swing_trading_signal_profile(snapshot)

        self.assertEqual(
            profile.stance,
            SwingTradingStance.STRONG_BULLISH,
        )
        self.assertEqual(profile.direction, SignalDirection.BULLISH)
        self.assertAlmostEqual(profile.score, 100.0)
        self.assertAlmostEqual(profile.coverage_percentage, 100.0)
        self.assertAlmostEqual(profile.agreement_percentage, 100.0)
        self.assertAlmostEqual(profile.confidence_percentage, 100.0)
        self.assertFalse(profile.has_directional_conflict)
        self.assertEqual(profile.snapshot, snapshot)

    def test_all_aligned_strong_bearish_evidence_is_strong_setup(self):
        profile = build_swing_trading_signal_profile(
            self.snapshot(
                self.one_per_category(SignalDirection.BEARISH)
            )
        )

        self.assertEqual(
            profile.stance,
            SwingTradingStance.STRONG_BEARISH,
        )
        self.assertEqual(profile.direction, SignalDirection.BEARISH)
        self.assertAlmostEqual(profile.score, -100.0)

    def test_all_neutral_evidence_has_coverage_without_direction(self):
        profile = build_swing_trading_signal_profile(
            self.snapshot(
                self.one_per_category(SignalDirection.NEUTRAL)
            )
        )

        self.assertEqual(profile.stance, SwingTradingStance.NEUTRAL)
        self.assertEqual(profile.direction, SignalDirection.NEUTRAL)
        self.assertEqual(profile.score, 0.0)
        self.assertEqual(profile.coverage_percentage, 100.0)
        self.assertEqual(profile.agreement_percentage, 0.0)
        self.assertEqual(profile.confidence_percentage, 0.0)

    def test_insufficient_coverage_blocks_directional_stance(self):
        evidence = [
            self.evidence(
                "price_action_signal",
                SignalCategory.PRICE_ACTION,
                SignalDirection.BULLISH,
            )
        ]

        profile = build_swing_trading_signal_profile(
            self.snapshot(evidence)
        )

        self.assertEqual(
            profile.stance,
            SwingTradingStance.INSUFFICIENT_EVIDENCE,
        )
        self.assertEqual(profile.direction, SignalDirection.NEUTRAL)
        self.assertEqual(profile.score, 100.0)
        self.assertLess(profile.coverage_percentage, 60.0)
        self.assertIn("insufficient_evidence", profile.rationale)

    def test_neutral_evidence_counts_toward_category_coverage(self):
        evidence = [
            self.evidence(
                "trend",
                SignalCategory.TREND,
                SignalDirection.BULLISH,
            ),
            self.evidence(
                "momentum",
                SignalCategory.MOMENTUM,
                SignalDirection.NEUTRAL,
            ),
            self.evidence(
                "price",
                SignalCategory.PRICE_ACTION,
                SignalDirection.NEUTRAL,
            ),
            self.evidence(
                "candlestick",
                SignalCategory.CANDLESTICK,
                SignalDirection.NEUTRAL,
            ),
        ]

        profile = build_swing_trading_signal_profile(
            self.snapshot(evidence)
        )

        total_weight = sum(DEFAULT_SWING_CATEGORY_WEIGHTS.values())
        expected_coverage = (
            (1.25 + 1.0 + 1.5 + 1.0) / total_weight * 100
        )
        self.assertAlmostEqual(
            profile.coverage_percentage,
            expected_coverage,
        )
        self.assertNotEqual(
            profile.stance,
            SwingTradingStance.INSUFFICIENT_EVIDENCE,
        )

    def test_strong_threshold_boundary_is_inclusive(self):
        # A 3:2 weight ratio on the directional half of the categories
        # puts exactly 60% of covered weight behind the bullish side,
        # regardless of how many categories SignalCategory defines.
        categories = list(SignalCategory)
        midpoint = len(categories) // 2
        evidence = [
            self.evidence(
                category.value,
                category,
                (
                    SignalDirection.BULLISH
                    if index < midpoint
                    else SignalDirection.NEUTRAL
                ),
            )
            for index, category in enumerate(categories)
        ]
        weights = {
            category: (3.0 if index < midpoint else 2.0)
            for index, category in enumerate(categories)
        }

        profile = build_swing_trading_signal_profile(
            self.snapshot(evidence),
            category_weights=weights,
        )

        self.assertAlmostEqual(profile.score, 60.0)
        self.assertEqual(
            profile.stance,
            SwingTradingStance.STRONG_BULLISH,
        )

    def test_directional_threshold_boundaries_are_inclusive(self):
        # Same 3:2 weighting scheme puts 60% of covered weight behind
        # the bullish half, which nets to a score of +/-20.
        categories = list(SignalCategory)
        midpoint = len(categories) // 2
        bullish = [
            self.evidence(
                category.value,
                category,
                (
                    SignalDirection.BULLISH
                    if index < midpoint
                    else SignalDirection.BEARISH
                ),
            )
            for index, category in enumerate(categories)
        ]
        bearish = [
            item.model_copy(
                update={
                    "direction": (
                        SignalDirection.BEARISH
                        if item.direction is SignalDirection.BULLISH
                        else SignalDirection.BULLISH
                    )
                }
            )
            for item in bullish
        ]
        weights = {
            category: (3.0 if index < midpoint else 2.0)
            for index, category in enumerate(categories)
        }

        bullish_profile = build_swing_trading_signal_profile(
            self.snapshot(bullish),
            category_weights=weights,
        )
        bearish_profile = build_swing_trading_signal_profile(
            self.snapshot(bearish),
            category_weights=weights,
        )

        self.assertAlmostEqual(bullish_profile.score, 20.0)
        self.assertEqual(
            bullish_profile.stance,
            SwingTradingStance.BULLISH,
        )
        self.assertAlmostEqual(bearish_profile.score, -20.0)
        self.assertEqual(
            bearish_profile.stance,
            SwingTradingStance.BEARISH,
        )

    def test_evidence_count_does_not_multiply_category_weight(self):
        base = [
            self.evidence(
                "momentum_one",
                SignalCategory.MOMENTUM,
                SignalDirection.BULLISH,
            ),
            self.evidence(
                "price",
                SignalCategory.PRICE_ACTION,
                SignalDirection.BEARISH,
            ),
        ]
        repeated = [
            base[0],
            self.evidence(
                "momentum_two",
                SignalCategory.MOMENTUM,
                SignalDirection.BULLISH,
            ),
            self.evidence(
                "momentum_three",
                SignalCategory.MOMENTUM,
                SignalDirection.BULLISH,
            ),
            base[1],
        ]

        one_profile = build_swing_trading_signal_profile(
            self.snapshot(base),
            minimum_coverage_percentage=0,
        )
        repeated_profile = build_swing_trading_signal_profile(
            self.snapshot(repeated),
            minimum_coverage_percentage=0,
        )

        self.assertAlmostEqual(one_profile.score, repeated_profile.score)
        self.assertEqual(
            one_profile.category_scores,
            repeated_profile.category_scores,
        )

    def test_neutral_evidence_does_not_dilute_directional_category(self):
        evidence = [
            self.evidence(
                "momentum_directional",
                SignalCategory.MOMENTUM,
                SignalDirection.BULLISH,
            ),
            self.evidence(
                "momentum_neutral",
                SignalCategory.MOMENTUM,
                SignalDirection.NEUTRAL,
                SignalStrength.WEAK,
            ),
        ]

        profile = build_swing_trading_signal_profile(
            self.snapshot(evidence),
            minimum_coverage_percentage=0,
        )

        self.assertEqual(profile.category_scores["momentum"], 100.0)

    def test_opposing_evidence_in_one_category_cancels(self):
        evidence = [
            self.evidence(
                "momentum_bull",
                SignalCategory.MOMENTUM,
                SignalDirection.BULLISH,
            ),
            self.evidence(
                "momentum_bear",
                SignalCategory.MOMENTUM,
                SignalDirection.BEARISH,
            ),
        ]

        profile = build_swing_trading_signal_profile(
            self.snapshot(evidence),
            minimum_coverage_percentage=0,
        )

        self.assertEqual(profile.category_scores["momentum"], 0.0)
        self.assertEqual(profile.score, 0.0)
        self.assertTrue(profile.has_directional_conflict)

    def test_strength_values_are_auditable_in_contributions(self):
        evidence = [
            self.evidence(
                "weak_bull",
                SignalCategory.TREND,
                SignalDirection.BULLISH,
                SignalStrength.WEAK,
            ),
            self.evidence(
                "moderate_bear",
                SignalCategory.MOMENTUM,
                SignalDirection.BEARISH,
                SignalStrength.MODERATE,
            ),
            self.evidence(
                "strong_neutral",
                SignalCategory.VOLATILITY,
                SignalDirection.NEUTRAL,
                SignalStrength.STRONG,
            ),
        ]

        profile = build_swing_trading_signal_profile(
            self.snapshot(evidence),
            minimum_coverage_percentage=0,
        )

        self.assertAlmostEqual(profile.contributions[0].signed_value, 1 / 3)
        self.assertAlmostEqual(profile.contributions[1].signed_value, -2 / 3)
        self.assertEqual(profile.contributions[2].signed_value, 0.0)

    def test_custom_category_weights_change_profile_deterministically(self):
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
            ),
            self.evidence(
                "volume",
                SignalCategory.VOLUME,
                SignalDirection.NEUTRAL,
            ),
        ]
        snapshot = self.snapshot(evidence)

        default = build_swing_trading_signal_profile(snapshot)
        price_weighted = build_swing_trading_signal_profile(
            snapshot,
            category_weights={SignalCategory.PRICE_ACTION: 4.0},
        )

        self.assertGreater(default.score, price_weighted.score)
        self.assertEqual(
            price_weighted.category_weights["price_action"],
            4.0,
        )

    def test_default_weights_are_preserved_in_profile(self):
        profile = build_swing_trading_signal_profile(self.snapshot())

        self.assertEqual(
            profile.category_weights,
            {
                category.value: weight
                for category, weight in (
                    DEFAULT_SWING_CATEGORY_WEIGHTS.items()
                )
            },
        )

    def test_empty_snapshot_is_explicitly_insufficient(self):
        profile = build_swing_trading_signal_profile(self.snapshot())

        self.assertEqual(
            profile.stance,
            SwingTradingStance.INSUFFICIENT_EVIDENCE,
        )
        self.assertEqual(profile.covered_categories, [])
        self.assertEqual(profile.contributions, [])
        self.assertEqual(profile.coverage_percentage, 0.0)
        self.assertEqual(profile.confidence_percentage, 0.0)

    def test_profile_rationale_is_explainable_not_recommendation(self):
        profile = build_swing_trading_signal_profile(
            self.snapshot(
                self.one_per_category(SignalDirection.BULLISH)
            )
        )

        self.assertIn("Bullish categories", profile.rationale)
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
                SignalCategory.MOMENTUM,
                SignalDirection.BULLISH,
            ),
        ]

        with self.assertRaisesRegex(
            ValueError,
            "must share one availability time",
        ):
            build_swing_trading_signal_profile(self.snapshot(evidence))

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
                SignalCategory.MOMENTUM,
                SignalDirection.BULLISH,
            ),
        ]

        profile = build_swing_trading_signal_profile(
            self.snapshot(evidence),
            require_synchronized_evidence=False,
            minimum_coverage_percentage=0,
        )

        self.assertFalse(profile.synchronized_evidence_required)

    def test_equivalent_cross_timezone_availability_is_synchronized(self):
        ist = timezone(timedelta(hours=5, minutes=30))
        evidence = [
            self.evidence(
                "utc",
                SignalCategory.TREND,
                SignalDirection.BULLISH,
            ),
            self.evidence(
                "ist",
                SignalCategory.MOMENTUM,
                SignalDirection.BULLISH,
                available_at=self.evaluated_at.astimezone(ist),
            ),
        ]

        profile = build_swing_trading_signal_profile(
            self.snapshot(evidence),
            minimum_coverage_percentage=0,
        )

        self.assertEqual(len(profile.contributions), 2)

    def test_rejects_future_evidence_in_forged_snapshot(self):
        future = self.evidence(
            "future",
            SignalCategory.TREND,
            SignalDirection.BULLISH,
            available_at=self.evaluated_at + timedelta(seconds=1),
        )
        forged = self.snapshot().model_copy(update={"evidence": [future]})

        with self.assertRaisesRegex(ValueError, "future evidence"):
            build_swing_trading_signal_profile(forged)

    def test_rejects_duplicate_or_unordered_forged_evidence(self):
        older = self.evidence(
            "one",
            SignalCategory.TREND,
            SignalDirection.BULLISH,
            available_at=self.evaluated_at - timedelta(days=1),
        )
        current = self.evidence(
            "two",
            SignalCategory.MOMENTUM,
            SignalDirection.BULLISH,
        )
        invalid_lists = (
            [current, older],
            [current, current],
        )

        for evidence in invalid_lists:
            with self.subTest(evidence=evidence):
                forged = self.snapshot().model_copy(
                    update={"evidence": evidence}
                )
                with self.assertRaises(ValueError):
                    build_swing_trading_signal_profile(forged)

    def test_rejects_invalid_weight_configuration(self):
        invalid_values = (True, "1", None, 0, -1, float("nan"))

        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    build_swing_trading_signal_profile(
                        self.snapshot(),
                        category_weights={"trend": value},
                    )

        with self.assertRaisesRegex(ValueError, "unknown"):
            build_swing_trading_signal_profile(
                self.snapshot(),
                category_weights={"unknown": 1.0},
            )

    def test_rejects_invalid_threshold_configuration(self):
        names = (
            "minimum_coverage_percentage",
            "directional_threshold",
            "strong_threshold",
        )
        invalid_values = (True, "1", None, -1, 101, float("inf"))

        for name in names:
            for value in invalid_values:
                with self.subTest(name=name, value=value):
                    with self.assertRaises(ValueError):
                        build_swing_trading_signal_profile(
                            self.snapshot(),
                            **{name: value},
                        )

        for directional, strong in ((20, 20), (60, 20)):
            with self.subTest(directional=directional, strong=strong):
                with self.assertRaisesRegex(
                    ValueError,
                    "strong threshold must exceed",
                ):
                    build_swing_trading_signal_profile(
                        self.snapshot(),
                        directional_threshold=directional,
                        strong_threshold=strong,
                    )

    def test_rejects_non_boolean_synchronization_setting(self):
        with self.assertRaisesRegex(ValueError, "must be a boolean"):
            build_swing_trading_signal_profile(
                self.snapshot(),
                require_synchronized_evidence=1,
            )


class SwingTradingProfileModelTests(unittest.TestCase):
    def test_rejects_boolean_numeric_contribution(self):
        with self.assertRaisesRegex(ValidationError, "must be finite"):
            SignalContribution(
                evidence_id="test",
                category=SignalCategory.TREND,
                direction=SignalDirection.BULLISH,
                strength=SignalStrength.STRONG,
                strength_value=True,
                signed_value=1.0,
            )

    def test_rejects_contribution_inconsistent_with_direction(self):
        with self.assertRaisesRegex(
            ValidationError,
            "signed value does not match",
        ):
            SignalContribution(
                evidence_id="test",
                category=SignalCategory.TREND,
                direction=SignalDirection.BEARISH,
                strength=SignalStrength.STRONG,
                strength_value=1.0,
                signed_value=1.0,
            )

    def test_rejects_tampered_profile_score(self):
        timestamp = datetime(2026, 8, 20, tzinfo=UTC)
        evidence = TechnicalSignalEvidence(
            evidence_id="trend",
            name="Trend",
            category=SignalCategory.TREND,
            direction=SignalDirection.BULLISH,
            strength=SignalStrength.STRONG,
            source="test",
            explanation="Test evidence.",
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
        profile = build_swing_trading_signal_profile(
            snapshot,
            minimum_coverage_percentage=0,
        )
        values = profile.model_dump(exclude_computed_fields=True)
        values["score"] = 0.0

        with self.assertRaisesRegex(
            ValidationError,
            "score does not match",
        ):
            SwingTradingSignalProfile(**values)


if __name__ == "__main__":
    unittest.main()
