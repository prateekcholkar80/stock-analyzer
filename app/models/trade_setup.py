from datetime import datetime
from enum import StrEnum
from math import isclose, isfinite
from typing import Self

from pydantic import Field, field_validator, model_validator

from app.models.market import Candle, HistoricalCandleSeries
from app.models.price_action import (
    PriceZoneLifecycleStatus,
    PriceZoneType,
    SupportResistanceLifecycle,
    SupportResistanceLifecycleResult,
)
from app.models.signals import (
    SignalDirection,
    SwingTradingSignalProfile,
)
from app.models.technical import TechnicalModel


class TradeDirection(StrEnum):
    LONG = "long"
    SHORT = "short"


class TradeSetupStatus(StrEnum):
    VALID = "valid"
    MARGINAL = "marginal"
    REJECTED = "rejected"


class TradeTargetFeasibility(StrEnum):
    REACHABLE = "reachable"
    BLOCKED_BY_STRUCTURE = "blocked_by_structure"


class TradeEntryMethod(StrEnum):
    LATEST_COMPLETED_CLOSE = "latest_completed_close"


class StopLossMethod(StrEnum):
    STRUCTURAL_INVALIDATION = "structural_invalidation"
    ATR_FALLBACK = "atr_fallback"


class StructuralBarrier(TechnicalModel):
    lifecycle: SupportResistanceLifecycle
    effective_zone_type: PriceZoneType
    boundary_price: float = Field(ge=0)
    distance_from_entry: float = Field(ge=0)
    reward_to_risk: float = Field(ge=0)

    @field_validator(
        "boundary_price",
        "distance_from_entry",
        "reward_to_risk",
        mode="before",
    )
    @classmethod
    def require_finite_metric(cls, value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            raise ValueError("structural-barrier metrics must be finite")
        return float(value)

    @model_validator(mode="after")
    def validate_effective_zone(self) -> Self:
        status = self.lifecycle.status
        original_type = self.lifecycle.zone.zone_type
        if status in (
            PriceZoneLifecycleStatus.ACTIVE,
            PriceZoneLifecycleStatus.FAILED_BREAK,
        ):
            expected_type = original_type
        elif status is PriceZoneLifecycleStatus.ROLE_REVERSED:
            expected_type = (
                PriceZoneType.SUPPORT
                if original_type is PriceZoneType.RESISTANCE
                else PriceZoneType.RESISTANCE
            )
        else:
            raise ValueError(
                "only active, failed-break, or role-reversed zones can "
                "be structural barriers"
            )

        if self.effective_zone_type is not expected_type:
            raise ValueError(
                "structural-barrier type does not match lifecycle status"
            )

        expected_boundary = (
            self.lifecycle.zone.lower_price
            if expected_type is PriceZoneType.RESISTANCE
            else self.lifecycle.zone.upper_price
        )
        if not isclose(
            self.boundary_price,
            expected_boundary,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "structural-barrier boundary does not match its zone"
            )
        return self


class TradeProfitTarget(TechnicalModel):
    reward_to_risk: float = Field(gt=0)
    target_price: float = Field(ge=0)
    reward_per_unit: float = Field(gt=0)
    feasibility: TradeTargetFeasibility

    @field_validator(
        "reward_to_risk",
        "target_price",
        "reward_per_unit",
        mode="before",
    )
    @classmethod
    def require_finite_metric(cls, value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            raise ValueError("profit-target metrics must be finite")
        return float(value)


class SwingTradeSetupEvaluation(TechnicalModel):
    profile: SwingTradingSignalProfile
    zone_lifecycles: SupportResistanceLifecycleResult
    direction: TradeDirection
    status: TradeSetupStatus
    entry_price: float = Field(gt=0)
    stop_loss_price: float = Field(gt=0)
    risk_per_unit: float = Field(gt=0)
    minimum_reward_to_risk: float = Field(gt=0)
    preferred_reward_to_risk: float = Field(gt=0)
    minimum_target: TradeProfitTarget
    preferred_target: TradeProfitTarget
    nearest_structural_barrier: StructuralBarrier | None = None
    maximum_structural_reward_to_risk: float | None = Field(
        default=None,
        ge=0,
    )
    rationale: str = Field(min_length=1)

    @field_validator(
        "entry_price",
        "stop_loss_price",
        "risk_per_unit",
        "minimum_reward_to_risk",
        "preferred_reward_to_risk",
        "maximum_structural_reward_to_risk",
        mode="before",
    )
    @classmethod
    def require_finite_metric(
        cls,
        value: float | None,
    ) -> float | None:
        if value is None:
            return None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            raise ValueError("trade-setup metrics must be finite")
        return float(value)

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("trade-setup rationale cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_evaluation(self) -> Self:
        self._validate_source_identity()
        self._validate_direction_and_prices()
        self._validate_targets()
        self._validate_barrier()
        self._validate_status()
        return self

    def _validate_source_identity(self) -> None:
        snapshot = self.profile.snapshot
        lifecycle_identity = (
            self.zone_lifecycles.exchange,
            self.zone_lifecycles.symbol_token,
            self.zone_lifecycles.symbol,
            self.zone_lifecycles.interval,
            self.zone_lifecycles.source,
            self.zone_lifecycles.source_retrieved_at,
        )
        snapshot_identity = (
            snapshot.exchange,
            snapshot.symbol_token,
            snapshot.symbol,
            snapshot.interval,
            snapshot.source,
            snapshot.source_retrieved_at,
        )
        if lifecycle_identity != snapshot_identity:
            raise ValueError(
                "swing profile and zone lifecycles must describe the "
                "same source"
            )
        if self.zone_lifecycles.evaluated_at != snapshot.evaluated_at:
            raise ValueError(
                "swing profile and zone lifecycles must share an "
                "evaluation time"
            )

    def _validate_direction_and_prices(self) -> None:
        expected_direction = {
            SignalDirection.BULLISH: TradeDirection.LONG,
            SignalDirection.BEARISH: TradeDirection.SHORT,
        }.get(self.profile.direction)
        if self.direction is not expected_direction:
            raise ValueError(
                "trade direction must match the directional swing profile"
            )

        if self.direction is TradeDirection.LONG:
            if self.stop_loss_price >= self.entry_price:
                raise ValueError(
                    "long stop loss must be below the entry price"
                )
        elif self.stop_loss_price <= self.entry_price:
            raise ValueError(
                "short stop loss must be above the entry price"
            )

        expected_risk = abs(self.entry_price - self.stop_loss_price)
        if not isclose(
            self.risk_per_unit,
            expected_risk,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "trade risk must equal the entry-to-stop distance"
            )

    def _validate_targets(self) -> None:
        if self.preferred_reward_to_risk <= self.minimum_reward_to_risk:
            raise ValueError(
                "preferred reward-to-risk must exceed the minimum"
            )

        target_pairs = (
            (self.minimum_target, self.minimum_reward_to_risk),
            (self.preferred_target, self.preferred_reward_to_risk),
        )
        for target, multiple in target_pairs:
            if not isclose(
                target.reward_to_risk,
                multiple,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise ValueError(
                    "profit-target multiple does not match evaluation"
                )
            expected_reward = self.risk_per_unit * multiple
            expected_price = (
                self.entry_price + expected_reward
                if self.direction is TradeDirection.LONG
                else self.entry_price - expected_reward
            )
            if not isclose(
                target.reward_per_unit,
                expected_reward,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ) or not isclose(
                target.target_price,
                expected_price,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise ValueError(
                    "profit-target prices must derive from entry and risk"
                )

    def _validate_barrier(self) -> None:
        barrier = self.nearest_structural_barrier
        expected_lifecycle = self._expected_barrier_lifecycle()
        if expected_lifecycle is None and barrier is not None:
            raise ValueError(
                "trade setup cannot contain an unrelated structural "
                "barrier"
            )
        if expected_lifecycle is not None:
            if barrier is None:
                raise ValueError(
                    "trade setup must include its nearest structural "
                    "barrier"
                )
            if barrier.lifecycle != expected_lifecycle:
                raise ValueError(
                    "trade setup barrier is not the nearest opposing "
                    "zone"
                )

        if barrier is None:
            if self.maximum_structural_reward_to_risk is not None:
                raise ValueError(
                    "structural reward limit requires a barrier"
                )
            expected_feasibility = TradeTargetFeasibility.REACHABLE
            if (
                self.minimum_target.feasibility is not expected_feasibility
                or self.preferred_target.feasibility
                is not expected_feasibility
            ):
                raise ValueError(
                    "targets without a structural barrier must be "
                    "reachable"
                )
            return

        expected_type = (
            PriceZoneType.RESISTANCE
            if self.direction is TradeDirection.LONG
            else PriceZoneType.SUPPORT
        )
        if barrier.effective_zone_type is not expected_type:
            raise ValueError(
                "nearest barrier must oppose the trade direction"
            )

        expected_distance = (
            max(0.0, barrier.boundary_price - self.entry_price)
            if self.direction is TradeDirection.LONG
            else max(0.0, self.entry_price - barrier.boundary_price)
        )
        expected_multiple = expected_distance / self.risk_per_unit
        if not isclose(
            barrier.distance_from_entry,
            expected_distance,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ) or not isclose(
            barrier.reward_to_risk,
            expected_multiple,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "structural-barrier distance must derive from entry and "
                "risk"
            )
        if self.maximum_structural_reward_to_risk is None or not isclose(
            self.maximum_structural_reward_to_risk,
            expected_multiple,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "maximum structural reward must match the nearest barrier"
            )

        for target in (self.minimum_target, self.preferred_target):
            expected = (
                TradeTargetFeasibility.REACHABLE
                if target.reward_to_risk < expected_multiple
                else TradeTargetFeasibility.BLOCKED_BY_STRUCTURE
            )
            if target.feasibility is not expected:
                raise ValueError(
                    "profit-target feasibility does not match the "
                    "structural barrier"
                )

    def _expected_barrier_lifecycle(
        self,
    ) -> SupportResistanceLifecycle | None:
        opposing_type = (
            PriceZoneType.RESISTANCE
            if self.direction is TradeDirection.LONG
            else PriceZoneType.SUPPORT
        )
        candidates: list[
            tuple[float, float, SupportResistanceLifecycle]
        ] = []
        for lifecycle in self.zone_lifecycles.lifecycles:
            effective_type = self._effective_zone_type(lifecycle)
            if effective_type is not opposing_type:
                continue

            zone = lifecycle.zone
            if self.direction is TradeDirection.LONG:
                if zone.upper_price < self.entry_price:
                    continue
                boundary = zone.lower_price
                distance = max(0.0, boundary - self.entry_price)
            else:
                if zone.lower_price > self.entry_price:
                    continue
                boundary = zone.upper_price
                distance = max(0.0, self.entry_price - boundary)
            candidates.append((distance, boundary, lifecycle))

        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (
                item[0],
                item[1],
                item[2].zone.confirmed_at,
            ),
        )[2]

    @staticmethod
    def _effective_zone_type(
        lifecycle: SupportResistanceLifecycle,
    ) -> PriceZoneType | None:
        if lifecycle.status in (
            PriceZoneLifecycleStatus.ACTIVE,
            PriceZoneLifecycleStatus.FAILED_BREAK,
        ):
            return lifecycle.zone.zone_type
        if lifecycle.status is PriceZoneLifecycleStatus.ROLE_REVERSED:
            return (
                PriceZoneType.SUPPORT
                if lifecycle.zone.zone_type is PriceZoneType.RESISTANCE
                else PriceZoneType.RESISTANCE
            )
        return None

    def _validate_status(self) -> None:
        if (
            self.minimum_target.feasibility
            is TradeTargetFeasibility.BLOCKED_BY_STRUCTURE
        ):
            expected = TradeSetupStatus.REJECTED
        elif (
            self.preferred_target.feasibility
            is TradeTargetFeasibility.BLOCKED_BY_STRUCTURE
        ):
            expected = TradeSetupStatus.MARGINAL
        else:
            expected = TradeSetupStatus.VALID

        if self.status is not expected:
            raise ValueError(
                "trade-setup status does not match target feasibility"
            )


class SwingTradePlan(TechnicalModel):
    market_series: HistoricalCandleSeries
    evaluation: SwingTradeSetupEvaluation
    entry_method: TradeEntryMethod
    entry_candle: Candle
    stop_loss_method: StopLossMethod
    protective_lifecycle: SupportResistanceLifecycle | None = None
    structural_invalidation_price: float | None = Field(
        default=None,
        ge=0,
    )
    stop_buffer: float = Field(gt=0)
    atr_value: float | None = Field(default=None, ge=0)
    atr_evidence_id: str | None = Field(
        default=None,
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    structural_buffer_atr_multiplier: float = Field(ge=0)
    minimum_buffer_percentage: float = Field(gt=0, le=100)
    fallback_stop_atr_multiplier: float = Field(gt=0)
    rationale: str = Field(min_length=1)

    @field_validator(
        "structural_invalidation_price",
        "stop_buffer",
        "atr_value",
        "structural_buffer_atr_multiplier",
        "minimum_buffer_percentage",
        "fallback_stop_atr_multiplier",
        mode="before",
    )
    @classmethod
    def require_finite_metric(
        cls,
        value: float | None,
    ) -> float | None:
        if value is None:
            return None
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            raise ValueError("swing-plan metrics must be finite")
        return float(value)

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("swing-plan rationale cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_plan(self) -> Self:
        self._validate_market_source()
        self._validate_entry()
        self._validate_atr_provenance()
        self._validate_stop_selection()
        return self

    def _validate_market_source(self) -> None:
        snapshot = self.evaluation.profile.snapshot
        market_identity = (
            self.market_series.exchange,
            self.market_series.symbol_token,
            self.market_series.symbol,
            self.market_series.interval,
            self.market_series.source,
            self.market_series.retrieved_at,
        )
        snapshot_identity = (
            snapshot.exchange,
            snapshot.symbol_token,
            snapshot.symbol,
            snapshot.interval,
            snapshot.source,
            snapshot.source_retrieved_at,
        )
        if market_identity != snapshot_identity:
            raise ValueError(
                "swing plan market series and profile must describe the "
                "same source"
            )

    def _validate_entry(self) -> None:
        evaluated_at = self.evaluation.profile.snapshot.evaluated_at
        previous_timestamp: datetime | None = None
        latest_eligible = None
        entry_present = False
        for candle in self.market_series.candles:
            if (
                previous_timestamp is not None
                and candle.timestamp <= previous_timestamp
            ):
                raise ValueError(
                    "swing-plan candles must have unique timestamps in "
                    "ascending order"
                )
            previous_timestamp = candle.timestamp
            if candle.timestamp <= evaluated_at:
                latest_eligible = candle
            if candle == self.entry_candle:
                entry_present = True

        if not entry_present:
            raise ValueError(
                "swing-plan entry candle must belong to market series"
            )
        if latest_eligible is None or self.entry_candle != latest_eligible:
            raise ValueError(
                "swing-plan entry must use the latest completed candle"
            )
        if not isclose(
            self.evaluation.entry_price,
            self.entry_candle.close,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "swing-plan entry price must equal the selected close"
            )

    def _validate_atr_provenance(self) -> None:
        if (self.atr_value is None) != (self.atr_evidence_id is None):
            raise ValueError(
                "ATR value and evidence identifier must be supplied "
                "together"
            )
        if self.atr_value is None:
            return

        matches = [
            evidence
            for evidence in self.evaluation.profile.snapshot.evidence
            if evidence.evidence_id == self.atr_evidence_id
        ]
        if len(matches) != 1:
            raise ValueError(
                "swing-plan ATR evidence must belong to the profile"
            )
        if matches[0].source != (
            "volatility_signals.atr_regime_and_risk_distance"
        ):
            raise ValueError(
                "swing-plan ATR evidence must use the ATR signal source"
            )
        observed_atr = matches[0].observed_values.get("atr")
        if (
            isinstance(observed_atr, bool)
            or not isinstance(observed_atr, (int, float))
            or not isclose(
                self.atr_value,
                float(observed_atr),
                rel_tol=1e-12,
                abs_tol=1e-12,
            )
        ):
            raise ValueError(
                "swing-plan ATR value must match its profile evidence"
            )

    def _validate_stop_selection(self) -> None:
        expected_lifecycle = self._expected_protective_lifecycle()
        if self.stop_loss_method is StopLossMethod.STRUCTURAL_INVALIDATION:
            if expected_lifecycle is None:
                raise ValueError(
                    "structural stop requires a confirmed protective zone"
                )
            if self.protective_lifecycle != expected_lifecycle:
                raise ValueError(
                    "swing plan must use the nearest protective zone"
                )
            expected_boundary = self._protective_boundary(
                expected_lifecycle
            )
            if (
                self.structural_invalidation_price is None
                or not isclose(
                    self.structural_invalidation_price,
                    expected_boundary,
                    rel_tol=1e-12,
                    abs_tol=1e-12,
                )
            ):
                raise ValueError(
                    "structural invalidation must match the protective "
                    "zone boundary"
                )
            expected_buffer = max(
                expected_boundary
                * self.minimum_buffer_percentage
                / 100,
                (self.atr_value or 0)
                * self.structural_buffer_atr_multiplier,
            )
        else:
            if expected_lifecycle is not None:
                raise ValueError(
                    "ATR fallback cannot replace a confirmed protective "
                    "zone"
                )
            if (
                self.protective_lifecycle is not None
                or self.structural_invalidation_price is not None
            ):
                raise ValueError(
                    "ATR fallback cannot contain structural stop data"
                )
            if self.atr_value is None or self.atr_value <= 0:
                raise ValueError(
                    "ATR fallback requires positive ATR evidence"
                )
            expected_buffer = max(
                self.evaluation.entry_price
                * self.minimum_buffer_percentage
                / 100,
                self.atr_value * self.fallback_stop_atr_multiplier,
            )

        if not isclose(
            self.stop_buffer,
            expected_buffer,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "swing-plan stop buffer does not match its configuration"
            )

        base_price = (
            self.structural_invalidation_price
            if self.structural_invalidation_price is not None
            else self.evaluation.entry_price
        )
        expected_stop = (
            base_price - expected_buffer
            if self.evaluation.direction is TradeDirection.LONG
            else base_price + expected_buffer
        )
        if not isclose(
            self.evaluation.stop_loss_price,
            expected_stop,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "swing-plan stop does not match its invalidation rule"
            )

    def _expected_protective_lifecycle(
        self,
    ) -> SupportResistanceLifecycle | None:
        direction = self.evaluation.direction
        protective_type = (
            PriceZoneType.SUPPORT
            if direction is TradeDirection.LONG
            else PriceZoneType.RESISTANCE
        )
        entry = self.evaluation.entry_price
        candidates: list[
            tuple[float, float, SupportResistanceLifecycle]
        ] = []
        for lifecycle in self.evaluation.zone_lifecycles.lifecycles:
            effective_type = self._effective_zone_type(lifecycle)
            if effective_type is not protective_type:
                continue
            zone = lifecycle.zone
            if direction is TradeDirection.LONG:
                if zone.lower_price > entry:
                    continue
                distance = max(0.0, entry - zone.upper_price)
                boundary = zone.lower_price
            else:
                if zone.upper_price < entry:
                    continue
                distance = max(0.0, zone.lower_price - entry)
                boundary = zone.upper_price
            candidates.append((distance, boundary, lifecycle))

        if not candidates:
            return None
        return min(
            candidates,
            key=lambda item: (
                item[0],
                abs(entry - item[1]),
                item[2].zone.confirmed_at,
            ),
        )[2]

    def _protective_boundary(
        self,
        lifecycle: SupportResistanceLifecycle,
    ) -> float:
        if self.evaluation.direction is TradeDirection.LONG:
            return lifecycle.zone.lower_price
        return lifecycle.zone.upper_price

    @staticmethod
    def _effective_zone_type(
        lifecycle: SupportResistanceLifecycle,
    ) -> PriceZoneType | None:
        if lifecycle.status in (
            PriceZoneLifecycleStatus.ACTIVE,
            PriceZoneLifecycleStatus.FAILED_BREAK,
        ):
            return lifecycle.zone.zone_type
        if lifecycle.status is PriceZoneLifecycleStatus.ROLE_REVERSED:
            return (
                PriceZoneType.SUPPORT
                if lifecycle.zone.zone_type is PriceZoneType.RESISTANCE
                else PriceZoneType.RESISTANCE
            )
        return None
