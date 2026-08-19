import unittest
from datetime import UTC, datetime, timedelta
from math import nan

from pydantic import ValidationError

from app.analytics.risk_reward import evaluate_swing_trade_setup
from app.analytics.signal_profiles import (
    build_swing_trading_signal_profile,
)
from app.models.price_action import (
    PriceZoneBreakDirection,
    PriceZoneLifecycleStatus,
    PriceZoneType,
    SupportResistanceLifecycle,
    SupportResistanceLifecycleResult,
    SupportResistanceZone,
    SwingPivot,
    SwingPivotType,
)
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalStrength,
    TechnicalSignalEvidence,
    TechnicalSignalSnapshot,
)
from app.models.trade_setup import (
    TradeDirection,
    TradeSetupStatus,
    TradeTargetFeasibility,
)


class RiskRewardEvaluationTests(unittest.TestCase):
    def setUp(self):
        self.base_time = datetime(2026, 8, 1, tzinfo=UTC)
        self.evaluated_at = self.base_time + timedelta(days=10)
        self.retrieved_at = self.base_time + timedelta(days=11)

    def profile(self, direction=SignalDirection.BULLISH, **overrides):
        evidence = [
            TechnicalSignalEvidence(
                evidence_id=f"{category.value}_signal",
                name=f"{category.value} signal",
                category=category,
                direction=direction,
                strength=SignalStrength.STRONG,
                source="test.signal",
                explanation="Deterministic test evidence.",
                observed_at=self.evaluated_at,
                available_at=self.evaluated_at,
                observed_values={"value": 1.0},
            )
            for category in SignalCategory
        ]
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "source": "angel_one",
            "source_retrieved_at": self.retrieved_at,
            "evaluated_at": self.evaluated_at,
            "evidence": evidence,
        }
        values.update(overrides)
        return build_swing_trading_signal_profile(
            TechnicalSignalSnapshot(**values)
        )

    def zone(self, zone_type, price, *, offset=0):
        pivot_type = (
            SwingPivotType.LOW
            if zone_type is PriceZoneType.SUPPORT
            else SwingPivotType.HIGH
        )
        first_at = self.base_time + timedelta(days=offset)
        second_at = self.base_time + timedelta(days=offset + 2)
        pivots = [
            SwingPivot(
                pivot_type=pivot_type,
                pivot_at=first_at,
                confirmed_at=first_at + timedelta(days=1),
                price=price,
                left_strength=1,
                right_strength=1,
            ),
            SwingPivot(
                pivot_type=pivot_type,
                pivot_at=second_at,
                confirmed_at=second_at + timedelta(days=1),
                price=price,
                left_strength=1,
                right_strength=1,
            ),
        ]
        return SupportResistanceZone(
            zone_type=zone_type,
            lower_price=price,
            upper_price=price,
            center_price=price,
            confirmed_at=pivots[1].confirmed_at,
            pivots=pivots,
        )

    def lifecycle(
        self,
        zone_type,
        price,
        *,
        status=PriceZoneLifecycleStatus.ACTIVE,
        offset=0,
    ):
        zone = self.zone(zone_type, price, offset=offset)
        if status is PriceZoneLifecycleStatus.ACTIVE:
            return SupportResistanceLifecycle(zone=zone, status=status)

        broken_at = self.base_time + timedelta(days=5 + offset)
        if zone_type is PriceZoneType.RESISTANCE:
            break_direction = PriceZoneBreakDirection.BULLISH
            previous_close = price - 1
            break_close_price = price + 1
        else:
            break_direction = PriceZoneBreakDirection.BEARISH
            previous_close = price + 1
            break_close_price = price - 1

        values = {
            "zone": zone,
            "status": status,
            "broken_at": broken_at,
            "break_direction": break_direction,
            "previous_close": previous_close,
            "break_close_price": break_close_price,
        }
        if status in (
            PriceZoneLifecycleStatus.RETESTED,
            PriceZoneLifecycleStatus.ROLE_REVERSED,
        ):
            values["retested_at"] = broken_at + timedelta(days=1)
        if status is PriceZoneLifecycleStatus.ROLE_REVERSED:
            values["reversal_confirmed_at"] = (
                broken_at + timedelta(days=2)
            )
        if status is PriceZoneLifecycleStatus.FAILED_BREAK:
            values["failed_at"] = broken_at + timedelta(days=1)
        return SupportResistanceLifecycle(**values)

    def lifecycles(self, items=(), **overrides):
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "source": "angel_one",
            "source_retrieved_at": self.retrieved_at,
            "zone_detection_evaluated_at": (
                self.base_time + timedelta(days=6)
            ),
            "evaluated_at": self.evaluated_at,
            "pivot_left_strength": 1,
            "pivot_right_strength": 1,
            "tolerance_percentage": 0.5,
            "minimum_touches": 2,
            "lifecycles": list(items),
        }
        values.update(overrides)
        return SupportResistanceLifecycleResult(**values)

    def evaluate_long(self, items=(), **overrides):
        values = {
            "entry_price": 100.0,
            "stop_loss_price": 95.0,
        }
        values.update(overrides)
        return evaluate_swing_trade_setup(
            self.profile(),
            self.lifecycles(items),
            **values,
        )

    def evaluate_short(self, items=(), **overrides):
        values = {
            "entry_price": 100.0,
            "stop_loss_price": 105.0,
        }
        values.update(overrides)
        return evaluate_swing_trade_setup(
            self.profile(SignalDirection.BEARISH),
            self.lifecycles(items),
            **values,
        )

    def test_long_setup_builds_default_two_and_three_r_targets(self):
        result = self.evaluate_long()

        self.assertEqual(result.direction, TradeDirection.LONG)
        self.assertEqual(result.status, TradeSetupStatus.VALID)
        self.assertEqual(result.risk_per_unit, 5.0)
        self.assertEqual(result.minimum_target.target_price, 110.0)
        self.assertEqual(result.preferred_target.target_price, 115.0)
        self.assertEqual(
            result.minimum_target.feasibility,
            TradeTargetFeasibility.REACHABLE,
        )
        self.assertIsNone(result.nearest_structural_barrier)
        self.assertIsNone(result.maximum_structural_reward_to_risk)

    def test_short_setup_builds_default_two_and_three_r_targets(self):
        result = self.evaluate_short()

        self.assertEqual(result.direction, TradeDirection.SHORT)
        self.assertEqual(result.status, TradeSetupStatus.VALID)
        self.assertEqual(result.minimum_target.target_price, 90.0)
        self.assertEqual(result.preferred_target.target_price, 85.0)

    def test_barrier_between_two_and_three_r_is_marginal(self):
        resistance = self.lifecycle(
            PriceZoneType.RESISTANCE,
            113.0,
        )

        result = self.evaluate_long([resistance])

        self.assertEqual(result.status, TradeSetupStatus.MARGINAL)
        self.assertEqual(
            result.minimum_target.feasibility,
            TradeTargetFeasibility.REACHABLE,
        )
        self.assertEqual(
            result.preferred_target.feasibility,
            TradeTargetFeasibility.BLOCKED_BY_STRUCTURE,
        )
        self.assertAlmostEqual(
            result.maximum_structural_reward_to_risk,
            2.6,
        )
        self.assertIn("2R target is reachable", result.rationale)

    def test_barrier_before_two_r_rejects_setup(self):
        result = self.evaluate_long(
            [self.lifecycle(PriceZoneType.RESISTANCE, 108.0)]
        )

        self.assertEqual(result.status, TradeSetupStatus.REJECTED)
        self.assertEqual(
            result.minimum_target.feasibility,
            TradeTargetFeasibility.BLOCKED_BY_STRUCTURE,
        )

    def test_target_at_barrier_is_conservatively_blocked(self):
        at_two_r = self.evaluate_long(
            [self.lifecycle(PriceZoneType.RESISTANCE, 110.0)]
        )
        at_three_r = self.evaluate_long(
            [self.lifecycle(PriceZoneType.RESISTANCE, 115.0)]
        )

        self.assertEqual(at_two_r.status, TradeSetupStatus.REJECTED)
        self.assertEqual(at_three_r.status, TradeSetupStatus.MARGINAL)

    def test_barrier_beyond_three_r_keeps_setup_valid(self):
        result = self.evaluate_long(
            [self.lifecycle(PriceZoneType.RESISTANCE, 116.0)]
        )

        self.assertEqual(result.status, TradeSetupStatus.VALID)
        self.assertAlmostEqual(
            result.maximum_structural_reward_to_risk,
            3.2,
        )

    def test_entry_inside_resistance_is_immediately_blocked(self):
        zone = self.zone(PriceZoneType.RESISTANCE, 100.0)
        result = self.evaluate_long(
            [SupportResistanceLifecycle(
                zone=zone,
                status=PriceZoneLifecycleStatus.ACTIVE,
            )]
        )

        self.assertEqual(result.status, TradeSetupStatus.REJECTED)
        self.assertEqual(
            result.maximum_structural_reward_to_risk,
            0.0,
        )

    def test_nearest_opposing_zone_is_selected(self):
        resistance_far = self.lifecycle(
            PriceZoneType.RESISTANCE,
            118.0,
            offset=1,
        )
        support = self.lifecycle(PriceZoneType.SUPPORT, 95.0)
        resistance_near = self.lifecycle(
            PriceZoneType.RESISTANCE,
            112.0,
            offset=2,
        )

        result = self.evaluate_long(
            [resistance_far, support, resistance_near]
        )

        self.assertEqual(
            result.nearest_structural_barrier.boundary_price,
            112.0,
        )

    def test_zone_behind_entry_does_not_block_target(self):
        result = self.evaluate_long(
            [self.lifecycle(PriceZoneType.RESISTANCE, 90.0)]
        )

        self.assertEqual(result.status, TradeSetupStatus.VALID)
        self.assertIsNone(result.nearest_structural_barrier)

    def test_short_setup_uses_support_as_the_barrier(self):
        result = self.evaluate_short(
            [
                self.lifecycle(PriceZoneType.RESISTANCE, 105.0),
                self.lifecycle(PriceZoneType.SUPPORT, 87.0, offset=1),
            ]
        )

        self.assertEqual(result.status, TradeSetupStatus.MARGINAL)
        self.assertEqual(
            result.nearest_structural_barrier.effective_zone_type,
            PriceZoneType.SUPPORT,
        )
        self.assertAlmostEqual(
            result.maximum_structural_reward_to_risk,
            2.6,
        )

    def test_role_reversed_support_becomes_long_resistance(self):
        lifecycle = self.lifecycle(
            PriceZoneType.SUPPORT,
            112.0,
            status=PriceZoneLifecycleStatus.ROLE_REVERSED,
        )

        result = self.evaluate_long([lifecycle])

        self.assertEqual(result.status, TradeSetupStatus.MARGINAL)
        self.assertEqual(
            result.nearest_structural_barrier.effective_zone_type,
            PriceZoneType.RESISTANCE,
        )

    def test_failed_break_restores_original_zone_role(self):
        lifecycle = self.lifecycle(
            PriceZoneType.RESISTANCE,
            108.0,
            status=PriceZoneLifecycleStatus.FAILED_BREAK,
        )

        result = self.evaluate_long([lifecycle])

        self.assertEqual(result.status, TradeSetupStatus.REJECTED)
        self.assertEqual(
            result.nearest_structural_barrier.effective_zone_type,
            PriceZoneType.RESISTANCE,
        )

    def test_broken_and_unconfirmed_retested_zones_are_not_barriers(self):
        broken = self.lifecycle(
            PriceZoneType.RESISTANCE,
            108.0,
            status=PriceZoneLifecycleStatus.BROKEN,
        )
        retested = self.lifecycle(
            PriceZoneType.RESISTANCE,
            109.0,
            status=PriceZoneLifecycleStatus.RETESTED,
            offset=1,
        )

        result = self.evaluate_long([broken, retested])

        self.assertEqual(result.status, TradeSetupStatus.VALID)
        self.assertIsNone(result.nearest_structural_barrier)

    def test_custom_reward_multiples_are_supported(self):
        result = self.evaluate_long(
            minimum_reward_to_risk=1.5,
            preferred_reward_to_risk=2.5,
        )

        self.assertEqual(result.minimum_target.target_price, 107.5)
        self.assertEqual(result.preferred_target.target_price, 112.5)

    def test_neutral_profile_cannot_create_a_trade_setup(self):
        with self.assertRaisesRegex(
            ValueError,
            "requires a directional swing profile",
        ):
            evaluate_swing_trade_setup(
                self.profile(SignalDirection.NEUTRAL),
                self.lifecycles(),
                entry_price=100,
                stop_loss_price=95,
            )

    def test_stop_must_be_on_the_invalidation_side(self):
        with self.assertRaisesRegex(ValueError, "long stop loss"):
            self.evaluate_long(stop_loss_price=100)
        with self.assertRaisesRegex(ValueError, "short stop loss"):
            self.evaluate_short(stop_loss_price=99)

    def test_rejects_non_finite_boolean_and_non_positive_prices(self):
        invalid_values = (nan, True, 0, -1)
        for value in invalid_values:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    self.evaluate_long(entry_price=value)

    def test_preferred_multiple_must_exceed_minimum(self):
        for preferred in (2.0, 1.5):
            with self.subTest(preferred=preferred):
                with self.assertRaisesRegex(
                    ValueError,
                    "preferred reward-to-risk must exceed",
                ):
                    self.evaluate_long(
                        minimum_reward_to_risk=2.0,
                        preferred_reward_to_risk=preferred,
                    )

    def test_short_target_cannot_fall_below_zero(self):
        with self.assertRaisesRegex(ValueError, "cannot fall below zero"):
            self.evaluate_short(
                entry_price=10,
                stop_loss_price=15,
            )

    def test_rejects_mismatched_source_identity(self):
        with self.assertRaisesRegex(ValueError, "same source"):
            evaluate_swing_trade_setup(
                self.profile(),
                self.lifecycles(symbol="TCS-EQ"),
                entry_price=100,
                stop_loss_price=95,
            )

    def test_rejects_unsynchronized_evaluation_times(self):
        with self.assertRaisesRegex(ValueError, "evaluation time"):
            evaluate_swing_trade_setup(
                self.profile(),
                self.lifecycles(
                    evaluated_at=self.evaluated_at - timedelta(days=1),
                ),
                entry_price=100,
                stop_loss_price=95,
            )

    def test_model_rejects_tampered_status(self):
        result = self.evaluate_long(
            [self.lifecycle(PriceZoneType.RESISTANCE, 108.0)]
        )
        values = result.model_dump()
        values["status"] = TradeSetupStatus.VALID

        with self.assertRaisesRegex(
            ValidationError,
            "status does not match target feasibility",
        ):
            type(result).model_validate(values)

    def test_model_rejects_tampered_target_price(self):
        result = self.evaluate_long()
        values = result.model_dump()
        values["minimum_target"]["target_price"] = 111.0

        with self.assertRaisesRegex(
            ValidationError,
            "profit-target prices must derive",
        ):
            type(result).model_validate(values)

    def test_model_rejects_omitted_known_structural_barrier(self):
        result = self.evaluate_long(
            [self.lifecycle(PriceZoneType.RESISTANCE, 108.0)]
        )
        values = result.model_dump()
        values["nearest_structural_barrier"] = None
        values["maximum_structural_reward_to_risk"] = None
        values["minimum_target"]["feasibility"] = (
            TradeTargetFeasibility.REACHABLE
        )
        values["preferred_target"]["feasibility"] = (
            TradeTargetFeasibility.REACHABLE
        )
        values["status"] = TradeSetupStatus.VALID

        with self.assertRaisesRegex(
            ValidationError,
            "must include its nearest structural barrier",
        ):
            type(result).model_validate(values)


if __name__ == "__main__":
    unittest.main()
