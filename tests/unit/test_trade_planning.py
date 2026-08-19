import unittest
from datetime import UTC, datetime, timedelta
from math import nan

from pydantic import ValidationError

from app.analytics.risk_reward import (
    ATR_SIGNAL_SOURCE,
    build_swing_trade_plan,
)
from app.analytics.signal_profiles import (
    build_swing_trading_signal_profile,
)
from app.analytics.support_resistance_lifecycle import (
    track_support_resistance_lifecycle,
)
from app.exceptions import InsufficientDataError
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
    SignalStrength,
    TechnicalSignalEvidence,
    TechnicalSignalSnapshot,
)
from app.models.trade_setup import (
    StopLossMethod,
    TradeEntryMethod,
    TradeSetupStatus,
)


class SwingTradePlanningTests(unittest.TestCase):
    def setUp(self):
        self.base_time = datetime(2026, 8, 1, tzinfo=UTC)
        self.evaluated_at = self.base_time + timedelta(days=10)
        self.retrieved_at = self.base_time + timedelta(days=12)

    def market(
        self,
        *,
        lows=None,
        highs=None,
        closes=None,
        future_close=None,
        candles=None,
        **overrides,
    ):
        if candles is None:
            lows = lows or {}
            highs = highs or {}
            closes = closes or {}
            candles = []
            for day in range(11):
                close = float(closes.get(day, 100.0))
                default_low = max(0.0, close - 1)
                candles.append(
                    Candle(
                        timestamp=self.base_time + timedelta(days=day),
                        open=close,
                        high=max(close, float(highs.get(day, close + 1))),
                        low=min(close, float(lows.get(day, default_low))),
                        close=close,
                        volume=1_000 + day,
                    )
                )
            if future_close is not None:
                candles.append(
                    Candle(
                        timestamp=self.base_time + timedelta(days=11),
                        open=future_close,
                        high=future_close + 1,
                        low=future_close - 1,
                        close=future_close,
                        volume=2_000,
                    )
                )

        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "candles": candles,
            "retrieved_at": self.retrieved_at,
            "source": "angel_one",
        }
        values.update(overrides)
        return HistoricalCandleSeries(**values)

    def profile(
        self,
        direction=SignalDirection.BULLISH,
        *,
        include_atr=True,
        atr=2.0,
        extra_atr=False,
        **overrides,
    ):
        evidence = []
        for category in SignalCategory:
            source = "test.signal"
            observed_values = {"value": 1.0}
            evidence_direction = direction
            evidence_id = f"{category.value}_signal"
            if category is SignalCategory.VOLATILITY and include_atr:
                source = ATR_SIGNAL_SOURCE
                observed_values = {"atr": atr, "close": 100.0}
                evidence_direction = SignalDirection.NEUTRAL
                evidence_id = "atr_volatility.atr14.hlc"
            evidence.append(
                TechnicalSignalEvidence(
                    evidence_id=evidence_id,
                    name=f"{category.value} signal",
                    category=category,
                    direction=evidence_direction,
                    strength=SignalStrength.STRONG,
                    source=source,
                    explanation="Deterministic test evidence.",
                    observed_at=self.evaluated_at,
                    available_at=self.evaluated_at,
                    observed_values=observed_values,
                )
            )
        if extra_atr:
            evidence.append(
                TechnicalSignalEvidence(
                    evidence_id="atr_volatility.atr21.hlc",
                    name="ATR 21 signal",
                    category=SignalCategory.VOLATILITY,
                    direction=SignalDirection.NEUTRAL,
                    strength=SignalStrength.STRONG,
                    source=ATR_SIGNAL_SOURCE,
                    explanation="Second deterministic ATR evidence.",
                    observed_at=self.evaluated_at,
                    available_at=self.evaluated_at,
                    observed_values={"atr": 3.0, "close": 100.0},
                )
            )

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
        pivots = []
        for day in (offset, offset + 2):
            pivots.append(
                SwingPivot(
                    pivot_type=pivot_type,
                    pivot_at=self.base_time + timedelta(days=day),
                    confirmed_at=(
                        self.base_time + timedelta(days=day + 1)
                    ),
                    price=price,
                    left_strength=1,
                    right_strength=1,
                )
            )
        return SupportResistanceZone(
            zone_type=zone_type,
            lower_price=price,
            upper_price=price,
            center_price=price,
            confirmed_at=pivots[1].confirmed_at,
            pivots=pivots,
        )

    def lifecycles(self, series, zones=()):
        detection = SupportResistanceDetectionResult(
            exchange=series.exchange,
            symbol_token=series.symbol_token,
            symbol=series.symbol,
            interval=series.interval,
            source=series.source,
            source_retrieved_at=series.retrieved_at,
            evaluated_at=self.base_time + timedelta(days=6),
            pivot_left_strength=1,
            pivot_right_strength=1,
            tolerance_percentage=0.5,
            minimum_touches=2,
            zones=list(zones),
        )
        return track_support_resistance_lifecycle(
            series,
            detection,
            as_of=self.evaluated_at,
        )

    def build(self, *, profile=None, series=None, zones=(), **settings):
        series = series or self.market()
        profile = profile or self.profile()
        return build_swing_trade_plan(
            profile,
            series,
            self.lifecycles(series, zones),
            **settings,
        )

    def test_long_plan_uses_close_and_buffered_support_stop(self):
        series = self.market(lows={0: 95, 2: 95})
        support = self.zone(PriceZoneType.SUPPORT, 95)

        plan = self.build(series=series, zones=[support])

        self.assertEqual(
            plan.entry_method,
            TradeEntryMethod.LATEST_COMPLETED_CLOSE,
        )
        self.assertEqual(plan.evaluation.entry_price, 100.0)
        self.assertEqual(
            plan.stop_loss_method,
            StopLossMethod.STRUCTURAL_INVALIDATION,
        )
        self.assertEqual(plan.structural_invalidation_price, 95.0)
        self.assertEqual(plan.stop_buffer, 0.5)
        self.assertEqual(plan.evaluation.stop_loss_price, 94.5)
        self.assertEqual(plan.evaluation.risk_per_unit, 5.5)
        self.assertEqual(plan.evaluation.minimum_target.target_price, 111)
        self.assertEqual(
            plan.evaluation.preferred_target.target_price,
            116.5,
        )

    def test_short_plan_uses_buffered_resistance_stop(self):
        series = self.market(highs={0: 105, 2: 105})
        resistance = self.zone(PriceZoneType.RESISTANCE, 105)

        plan = self.build(
            profile=self.profile(SignalDirection.BEARISH),
            series=series,
            zones=[resistance],
        )

        self.assertEqual(plan.evaluation.entry_price, 100)
        self.assertEqual(plan.evaluation.stop_loss_price, 105.5)
        self.assertEqual(plan.evaluation.minimum_target.target_price, 89)

    def test_no_protective_zone_uses_two_atr_fallback(self):
        plan = self.build()

        self.assertEqual(
            plan.stop_loss_method,
            StopLossMethod.ATR_FALLBACK,
        )
        self.assertIsNone(plan.protective_lifecycle)
        self.assertEqual(plan.stop_buffer, 4.0)
        self.assertEqual(plan.evaluation.stop_loss_price, 96.0)

    def test_no_zone_and_no_atr_refuses_to_invent_stop(self):
        with self.assertRaisesRegex(
            InsufficientDataError,
            "confirmed protective zone or positive ATR",
        ):
            self.build(profile=self.profile(include_atr=False))

    def test_structural_stop_without_atr_uses_percentage_buffer(self):
        series = self.market(lows={0: 95, 2: 95})

        plan = self.build(
            profile=self.profile(include_atr=False),
            series=series,
            zones=[self.zone(PriceZoneType.SUPPORT, 95)],
        )

        self.assertAlmostEqual(plan.stop_buffer, 0.095)
        self.assertAlmostEqual(plan.evaluation.stop_loss_price, 94.905)
        self.assertIsNone(plan.atr_value)

    def test_larger_minimum_percentage_buffer_wins_over_atr(self):
        series = self.market(lows={0: 95, 2: 95})

        plan = self.build(
            series=series,
            zones=[self.zone(PriceZoneType.SUPPORT, 95)],
            minimum_buffer_percentage=1.0,
        )

        self.assertEqual(plan.stop_buffer, 0.95)
        self.assertEqual(plan.evaluation.stop_loss_price, 94.05)

    def test_nearest_confirmed_protective_zone_is_selected(self):
        series = self.market(
            lows={0: 90, 2: 90, 3: 95, 5: 95},
        )
        far_support = self.zone(PriceZoneType.SUPPORT, 90)
        near_support = self.zone(
            PriceZoneType.SUPPORT,
            95,
            offset=3,
        )

        plan = self.build(
            series=series,
            zones=[far_support, near_support],
        )

        self.assertEqual(
            plan.protective_lifecycle.zone.center_price,
            95,
        )

    def test_zone_boundary_at_entry_is_valid_protection(self):
        series = self.market(lows={0: 100, 2: 100})

        plan = self.build(
            series=series,
            zones=[self.zone(PriceZoneType.SUPPORT, 100)],
        )

        self.assertEqual(
            plan.stop_loss_method,
            StopLossMethod.STRUCTURAL_INVALIDATION,
        )
        self.assertEqual(plan.evaluation.stop_loss_price, 99.5)

    def test_role_reversed_resistance_becomes_long_support(self):
        closes = {day: 94 for day in range(7)}
        closes.update({7: 96, 8: 96, 9: 96, 10: 100})
        series = self.market(
            highs={0: 95, 2: 95},
            lows={8: 94.5},
            closes=closes,
        )

        plan = self.build(
            series=series,
            zones=[self.zone(PriceZoneType.RESISTANCE, 95)],
        )

        self.assertEqual(
            plan.protective_lifecycle.status,
            PriceZoneLifecycleStatus.ROLE_REVERSED,
        )
        self.assertEqual(plan.evaluation.stop_loss_price, 94.5)

    def test_broken_zone_is_not_used_as_protection(self):
        closes = {day: 100 for day in range(7)}
        closes.update({7: 94, 8: 94, 9: 94, 10: 94})
        series = self.market(
            lows={0: 95, 2: 95},
            closes=closes,
        )

        plan = self.build(
            series=series,
            zones=[self.zone(PriceZoneType.SUPPORT, 95)],
        )

        self.assertEqual(
            plan.stop_loss_method,
            StopLossMethod.ATR_FALLBACK,
        )

    def test_target_feasibility_is_evaluated_after_stop_selection(self):
        series = self.market(
            lows={0: 95, 2: 95},
            highs={3: 113, 5: 113},
        )
        zones = [
            self.zone(PriceZoneType.SUPPORT, 95),
            self.zone(PriceZoneType.RESISTANCE, 113, offset=3),
        ]

        plan = self.build(series=series, zones=zones)

        self.assertEqual(plan.evaluation.status, TradeSetupStatus.MARGINAL)
        self.assertAlmostEqual(
            plan.evaluation.maximum_structural_reward_to_risk,
            13 / 5.5,
        )

    def test_future_candle_does_not_change_historical_entry_or_stop(self):
        base_plan = self.build(series=self.market())
        future_plan = self.build(series=self.market(future_close=200))

        self.assertEqual(base_plan.entry_candle, future_plan.entry_candle)
        self.assertEqual(
            base_plan.evaluation.stop_loss_price,
            future_plan.evaluation.stop_loss_price,
        )
        self.assertEqual(
            future_plan.entry_candle.timestamp,
            self.evaluated_at,
        )

    def test_no_completed_candle_is_insufficient_data(self):
        future = Candle(
            timestamp=self.evaluated_at + timedelta(days=1),
            open=100,
            high=101,
            low=99,
            close=100,
            volume=1_000,
        )
        series = self.market(candles=[future])

        with self.assertRaisesRegex(
            InsufficientDataError,
            "no completed candle",
        ):
            self.build(series=series)

    def test_rejects_unordered_or_duplicate_candles(self):
        ordered = self.market().candles
        cases = (
            [ordered[1], ordered[0]],
            [ordered[0], ordered[0]],
        )
        for candles in cases:
            with self.subTest(candles=candles):
                with self.assertRaisesRegex(
                    ValueError,
                    "unique timestamps in ascending order",
                ):
                    self.build(series=self.market(candles=candles))

    def test_rejects_market_source_mismatch(self):
        series = self.market()
        lifecycles = self.lifecycles(series)
        mismatched = series.model_copy(update={"symbol": "TCS-EQ"})

        with self.assertRaisesRegex(ValueError, "same source"):
            build_swing_trade_plan(
                self.profile(),
                mismatched,
                lifecycles,
            )

    def test_rejects_lifecycle_not_supported_by_market_history(self):
        series = self.market(lows={0: 95, 2: 95})
        lifecycles = self.lifecycles(
            series,
            [self.zone(PriceZoneType.SUPPORT, 95)],
        )
        values = lifecycles.model_dump()
        values["lifecycles"][0]["status"] = "broken"
        values["lifecycles"][0]["broken_at"] = self.evaluated_at
        values["lifecycles"][0]["break_direction"] = "bearish"
        values["lifecycles"][0]["previous_close"] = 100
        values["lifecycles"][0]["break_close_price"] = 94
        forged = type(lifecycles).model_validate(values)

        with self.assertRaisesRegex(ValueError, "do not match"):
            build_swing_trade_plan(
                self.profile(),
                series,
                forged,
            )

    def test_neutral_profile_cannot_create_a_plan(self):
        with self.assertRaisesRegex(ValueError, "directional swing profile"):
            self.build(profile=self.profile(SignalDirection.NEUTRAL))

    def test_zero_entry_close_is_rejected(self):
        series = self.market(closes={10: 0})

        with self.assertRaisesRegex(ValueError, "positive closing price"):
            self.build(series=series)

    def test_rejects_invalid_stop_configuration(self):
        settings = (
            {"structural_buffer_atr_multiplier": -1},
            {"structural_buffer_atr_multiplier": True},
            {"minimum_buffer_percentage": 0},
            {"minimum_buffer_percentage": 101},
            {"minimum_buffer_percentage": nan},
            {"fallback_stop_atr_multiplier": 0},
        )
        for setting in settings:
            with self.subTest(setting=setting):
                with self.assertRaises(ValueError):
                    self.build(**setting)

    def test_multiple_atr_evidence_items_are_ambiguous(self):
        with self.assertRaisesRegex(ValueError, "one ATR evidence item"):
            self.build(profile=self.profile(extra_atr=True))

    def test_invalid_atr_value_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "valid ATR evidence"):
            self.build(profile=self.profile(atr="invalid"))

    def test_zero_atr_works_with_structure_but_not_as_fallback(self):
        with self.assertRaises(InsufficientDataError):
            self.build(profile=self.profile(atr=0))

        series = self.market(lows={0: 95, 2: 95})
        plan = self.build(
            profile=self.profile(atr=0),
            series=series,
            zones=[self.zone(PriceZoneType.SUPPORT, 95)],
        )
        self.assertAlmostEqual(plan.stop_buffer, 0.095)

    def test_non_positive_structural_stop_is_rejected(self):
        series = self.market(lows={0: 0, 2: 0})

        with self.assertRaisesRegex(ValueError, "non-positive price"):
            self.build(
                profile=self.profile(include_atr=False),
                series=series,
                zones=[self.zone(PriceZoneType.SUPPORT, 0)],
            )

    def test_model_rejects_tampered_stop_buffer(self):
        plan = self.build()
        values = plan.model_dump()
        values["stop_buffer"] = 5.0

        with self.assertRaisesRegex(
            ValidationError,
            "stop buffer does not match",
        ):
            type(plan).model_validate(values)

    def test_model_rejects_tampered_entry_candle(self):
        plan = self.build()
        values = plan.model_dump()
        values["entry_candle"] = values["market_series"]["candles"][-2]

        with self.assertRaisesRegex(
            ValidationError,
            "latest completed candle",
        ):
            type(plan).model_validate(values)


if __name__ == "__main__":
    unittest.main()
