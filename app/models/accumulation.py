from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import (
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.models.analysis_timeframe import (
    SwingAnalysisTimeframe,
    timeframe_interval,
)
from app.models.technical import TechnicalModel


IST = ZoneInfo("Asia/Kolkata")


class AccumulationLifecycleState(StrEnum):
    """Point-in-time state of one evidence-qualified accumulation base."""

    FORMING = "forming"
    CONFIRMED = "confirmed"
    BREAKOUT = "breakout"
    RETESTING = "retesting"
    HOLDING_AS_SUPPORT = "holding_as_support"
    FAILED_BREAKOUT = "failed_breakout"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class LiquidityPoolSide(StrEnum):
    """The resting liquidity exceeded before price reclaimed its level."""

    SELL_SIDE = "sell_side"
    BUY_SIDE = "buy_side"


class LiquiditySweepBias(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


_ALLOWED_TRANSITIONS = {
    AccumulationLifecycleState.FORMING: {
        AccumulationLifecycleState.CONFIRMED,
        AccumulationLifecycleState.INVALIDATED,
        AccumulationLifecycleState.EXPIRED,
    },
    AccumulationLifecycleState.CONFIRMED: {
        AccumulationLifecycleState.BREAKOUT,
        AccumulationLifecycleState.INVALIDATED,
        AccumulationLifecycleState.EXPIRED,
    },
    AccumulationLifecycleState.BREAKOUT: {
        AccumulationLifecycleState.RETESTING,
        AccumulationLifecycleState.FAILED_BREAKOUT,
        AccumulationLifecycleState.INVALIDATED,
        AccumulationLifecycleState.EXPIRED,
    },
    AccumulationLifecycleState.RETESTING: {
        AccumulationLifecycleState.HOLDING_AS_SUPPORT,
        AccumulationLifecycleState.FAILED_BREAKOUT,
        AccumulationLifecycleState.INVALIDATED,
        AccumulationLifecycleState.EXPIRED,
    },
    AccumulationLifecycleState.HOLDING_AS_SUPPORT: {
        AccumulationLifecycleState.INVALIDATED,
        AccumulationLifecycleState.EXPIRED,
    },
    AccumulationLifecycleState.FAILED_BREAKOUT: {
        AccumulationLifecycleState.INVALIDATED,
        AccumulationLifecycleState.EXPIRED,
    },
    AccumulationLifecycleState.INVALIDATED: set(),
    AccumulationLifecycleState.EXPIRED: set(),
}

_ACTIVE_STATES = {
    AccumulationLifecycleState.FORMING,
    AccumulationLifecycleState.CONFIRMED,
    AccumulationLifecycleState.BREAKOUT,
    AccumulationLifecycleState.RETESTING,
    AccumulationLifecycleState.HOLDING_AS_SUPPORT,
}


def _finite_number(value: float, *, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
    ):
        raise ValueError(f"{label} must be a finite number")
    return float(value)


def _as_ist(value: datetime, *, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include timezone")
    return value.astimezone(IST)


class AccumulationEvidenceMetrics(TechnicalModel):
    """Observable price-volume measurements supporting one zone snapshot."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.accumulation_metrics.v1"] = (
        "jarvis.accumulation_metrics.v1"
    )
    candle_count: int = Field(ge=2)
    range_width_percentage: float = Field(ge=0)
    normalized_price_slope_percentage: float
    atr_compression_percentage: float = Field(ge=0, le=100)
    close_containment_percentage: float = Field(ge=0, le=100)
    lower_boundary_touch_count: int = Field(ge=0)
    upper_boundary_touch_count: int = Field(ge=0)
    lower_rejection_count: int = Field(ge=0)
    nonzero_volume_percentage: float = Field(ge=0, le=100)
    bullish_volume_share_percentage: float = Field(ge=0, le=100)
    down_volume_contraction_percentage: float = Field(ge=0, le=100)
    obv_slope: float
    breakout_volume_multiple: float | None = Field(default=None, ge=0)
    confidence_score: float = Field(ge=0, le=100)

    @field_validator(
        "range_width_percentage",
        "normalized_price_slope_percentage",
        "atr_compression_percentage",
        "close_containment_percentage",
        "nonzero_volume_percentage",
        "bullish_volume_share_percentage",
        "down_volume_contraction_percentage",
        "obv_slope",
        "breakout_volume_multiple",
        "confidence_score",
        mode="before",
    )
    @classmethod
    def require_finite_metrics(
        cls,
        value: float | None,
        info,
    ) -> float | None:
        if value is None:
            return None
        return _finite_number(
            value,
            label=info.field_name.replace("_", " "),
        )


class AccumulationLifecycleEvent(TechnicalModel):
    """One look-ahead-safe state transition and its immutable evidence."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.accumulation_event.v1"] = (
        "jarvis.accumulation_event.v1"
    )
    state: AccumulationLifecycleState
    observed_at: datetime
    available_at: datetime
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    explanation: str = Field(min_length=1)

    @field_validator("observed_at", "available_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _as_ist(
            value,
            label="accumulation lifecycle timestamp",
        )

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError(
                "accumulation lifecycle evidence IDs cannot be blank"
            )
        if len(values) != len(set(values)):
            raise ValueError(
                "accumulation lifecycle evidence IDs must be unique"
            )
        return values

    @field_validator("explanation")
    @classmethod
    def reject_blank_explanation(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "accumulation lifecycle explanation cannot be blank"
            )
        return value

    @model_validator(mode="after")
    def validate_availability(self) -> Self:
        if self.available_at < self.observed_at:
            raise ValueError(
                "accumulation lifecycle availability cannot precede observation"
            )
        return self


class LiquiditySweep(TechnicalModel):
    """A confirmed breach and close-based reclaim of a known price level."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.liquidity_sweep.v1"] = (
        "jarvis.liquidity_sweep.v1"
    )
    sweep_id: str = Field(
        min_length=1,
        pattern=(
            r"^(daily|weekly):liquidity_sweep:"
            r"[A-Za-z0-9][A-Za-z0-9_.:-]*$"
        ),
    )
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    source: str = Field(min_length=1)
    liquidity_side: LiquidityPoolSide
    reference_price: float = Field(gt=0)
    extreme_price: float = Field(gt=0)
    reclaim_close_price: float = Field(gt=0)
    swept_at: datetime
    reclaimed_at: datetime
    available_at: datetime
    volume_multiple: float | None = Field(default=None, ge=0)
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    explanation: str = Field(min_length=1)

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "source",
    )
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("liquidity-sweep metadata cannot be blank")
        return value

    @field_validator("swept_at", "reclaimed_at", "available_at")
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _as_ist(value, label="liquidity-sweep timestamp")

    @field_validator(
        "reference_price",
        "extreme_price",
        "reclaim_close_price",
        "volume_multiple",
        mode="before",
    )
    @classmethod
    def require_finite_values(
        cls,
        value: float | None,
        info,
    ) -> float | None:
        if value is None:
            return None
        return _finite_number(
            value,
            label=f"liquidity-sweep {info.field_name.replace('_', ' ')}",
        )

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("liquidity-sweep evidence IDs cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("liquidity-sweep evidence IDs must be unique")
        return values

    @field_validator("explanation")
    @classmethod
    def reject_blank_explanation(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("liquidity-sweep explanation cannot be blank")
        return value

    @computed_field
    @property
    def implication(self) -> LiquiditySweepBias:
        if self.liquidity_side is LiquidityPoolSide.SELL_SIDE:
            return LiquiditySweepBias.BULLISH
        return LiquiditySweepBias.BEARISH

    @computed_field
    @property
    def sweep_percentage(self) -> float:
        return (
            abs(self.extreme_price - self.reference_price)
            / self.reference_price
            * 100
        )

    @model_validator(mode="after")
    def validate_sweep(self) -> Self:
        expected_prefix = f"{self.timeframe.value}:liquidity_sweep:"
        if not self.sweep_id.startswith(expected_prefix):
            raise ValueError(
                "liquidity-sweep ID does not match its timeframe"
            )
        if self.interval != timeframe_interval(self.timeframe):
            raise ValueError(
                "liquidity-sweep interval does not match timeframe"
            )
        evidence_prefix = f"{self.timeframe.value}:"
        if any(
            not evidence_id.startswith(evidence_prefix)
            for evidence_id in self.evidence_ids
        ):
            raise ValueError(
                "liquidity-sweep evidence must match its timeframe"
            )
        if not self.swept_at <= self.reclaimed_at <= self.available_at:
            raise ValueError(
                "liquidity-sweep timestamps must follow sweep, reclaim and "
                "availability order"
            )
        if self.liquidity_side is LiquidityPoolSide.SELL_SIDE:
            valid_geometry = (
                self.extreme_price < self.reference_price
                <= self.reclaim_close_price
            )
        else:
            valid_geometry = (
                self.extreme_price > self.reference_price
                >= self.reclaim_close_price
            )
        if not valid_geometry:
            raise ValueError(
                "liquidity sweep must breach and reclaim its reference price"
            )
        return self


class AccumulationZone(TechnicalModel):
    """An evidence-linked daily or weekly accumulation range snapshot."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.accumulation_zone.v1"] = (
        "jarvis.accumulation_zone.v1"
    )
    zone_id: str = Field(
        min_length=1,
        pattern=r"^(daily|weekly):accumulation:[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_retrieved_at: datetime
    evaluated_at: datetime
    base_started_at: datetime
    base_last_observed_at: datetime
    lower_price: float = Field(gt=0)
    upper_price: float = Field(gt=0)
    metrics: AccumulationEvidenceMetrics
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    lifecycle: tuple[AccumulationLifecycleEvent, ...] = Field(min_length=1)
    liquidity_sweeps: tuple[LiquiditySweep, ...] = ()

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "source",
    )
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("accumulation-zone metadata cannot be blank")
        return value

    @field_validator(
        "source_retrieved_at",
        "evaluated_at",
        "base_started_at",
        "base_last_observed_at",
    )
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _as_ist(value, label="accumulation-zone timestamp")

    @field_validator("lower_price", "upper_price", mode="before")
    @classmethod
    def require_finite_prices(cls, value: float, info) -> float:
        return _finite_number(
            value,
            label=f"accumulation {info.field_name.replace('_', ' ')}",
        )

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("accumulation-zone evidence IDs cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("accumulation-zone evidence IDs must be unique")
        return values

    @computed_field
    @property
    def center_price(self) -> float:
        return (self.lower_price + self.upper_price) / 2

    @computed_field
    @property
    def width_percentage(self) -> float:
        return (self.upper_price - self.lower_price) / self.lower_price * 100

    @computed_field
    @property
    def current_state(self) -> AccumulationLifecycleState:
        return self.lifecycle[-1].state

    @computed_field
    @property
    def confirmed_at(self) -> datetime | None:
        return self._state_time(AccumulationLifecycleState.CONFIRMED)

    @computed_field
    @property
    def breakout_at(self) -> datetime | None:
        return self._state_time(AccumulationLifecycleState.BREAKOUT)

    @computed_field
    @property
    def invalidated_at(self) -> datetime | None:
        return self._state_time(AccumulationLifecycleState.INVALIDATED)

    @computed_field
    @property
    def is_active(self) -> bool:
        return self.current_state in _ACTIVE_STATES

    @computed_field
    @property
    def sell_side_sweep_count(self) -> int:
        return sum(
            sweep.liquidity_side is LiquidityPoolSide.SELL_SIDE
            for sweep in self.liquidity_sweeps
        )

    @computed_field
    @property
    def buy_side_sweep_count(self) -> int:
        return sum(
            sweep.liquidity_side is LiquidityPoolSide.BUY_SIDE
            for sweep in self.liquidity_sweeps
        )

    @model_validator(mode="after")
    def validate_zone(self) -> Self:
        expected_prefix = f"{self.timeframe.value}:accumulation:"
        if not self.zone_id.startswith(expected_prefix):
            raise ValueError(
                "accumulation-zone ID does not match its timeframe"
            )
        if self.interval != timeframe_interval(self.timeframe):
            raise ValueError(
                "accumulation-zone interval does not match timeframe"
            )
        if self.upper_price <= self.lower_price:
            raise ValueError(
                "accumulation-zone upper price must exceed lower price"
            )
        if self.evaluated_at > self.source_retrieved_at:
            raise ValueError(
                "accumulation evaluation cannot follow source retrieval"
            )
        if not (
            self.base_started_at
            <= self.base_last_observed_at
            <= self.evaluated_at
        ):
            raise ValueError(
                "accumulation base timestamps must be ordered and historical"
            )

        evidence = set(self.evidence_ids)
        prefix = f"{self.timeframe.value}:"
        if any(not value.startswith(prefix) for value in evidence):
            raise ValueError(
                "accumulation-zone evidence must match its timeframe"
            )
        self._validate_lifecycle(evidence)
        self._validate_liquidity_sweeps(evidence)
        return self

    def _validate_lifecycle(self, evidence: set[str]) -> None:
        if self.lifecycle[0].state is not AccumulationLifecycleState.FORMING:
            raise ValueError(
                "accumulation lifecycle must begin with forming"
            )

        for event in self.lifecycle:
            if event.observed_at < self.base_started_at:
                raise ValueError(
                    "accumulation event cannot precede the base"
                )
            if event.available_at > self.evaluated_at:
                raise ValueError(
                    "accumulation lifecycle cannot use future evidence"
                )
            if not set(event.evidence_ids) <= evidence:
                raise ValueError(
                    "accumulation event evidence must belong to its zone"
                )

        for previous, current in zip(
            self.lifecycle,
            self.lifecycle[1:],
        ):
            if current.observed_at <= previous.observed_at:
                raise ValueError(
                    "accumulation event observations must be chronological"
                )
            if current.available_at <= previous.available_at:
                raise ValueError(
                    "accumulation event availability must be chronological"
                )
            if current.state not in _ALLOWED_TRANSITIONS[previous.state]:
                raise ValueError(
                    "invalid accumulation lifecycle state transition"
                )

    def _validate_liquidity_sweeps(self, evidence: set[str]) -> None:
        sweep_ids: set[str] = set()
        for sweep in self.liquidity_sweeps:
            identity = (
                sweep.exchange,
                sweep.symbol_token,
                sweep.symbol,
                sweep.timeframe,
                sweep.interval,
                sweep.source,
            )
            expected_identity = (
                self.exchange,
                self.symbol_token,
                self.symbol,
                self.timeframe,
                self.interval,
                self.source,
            )
            if identity != expected_identity:
                raise ValueError(
                    "liquidity sweep does not match its accumulation zone"
                )
            if sweep.sweep_id in sweep_ids:
                raise ValueError("accumulation liquidity sweeps must be unique")
            if (
                sweep.swept_at < self.base_started_at
                or sweep.available_at > self.evaluated_at
            ):
                raise ValueError(
                    "accumulation liquidity sweep must be historical to its "
                    "zone evaluation"
                )
            if not set(sweep.evidence_ids) <= evidence:
                raise ValueError(
                    "liquidity-sweep evidence must belong to its zone"
                )
            sweep_ids.add(sweep.sweep_id)

    def _state_time(
        self,
        state: AccumulationLifecycleState,
    ) -> datetime | None:
        for event in self.lifecycle:
            if event.state is state:
                return event.available_at
        return None


class TimeframeAccumulationAnalysis(TechnicalModel):
    """All accumulation zones known for one daily or weekly evaluation."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.timeframe_accumulation.v1"] = (
        "jarvis.timeframe_accumulation.v1"
    )
    analysis_id: str = Field(
        min_length=1,
        pattern=r"^(daily|weekly):accumulation_analysis$",
    )
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_retrieved_at: datetime
    evaluated_at: datetime
    zones: tuple[AccumulationZone, ...] = ()

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "source",
    )
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("accumulation-analysis metadata cannot be blank")
        return value

    @field_validator("source_retrieved_at", "evaluated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        return _as_ist(value, label="accumulation-analysis timestamp")

    @computed_field
    @property
    def active_zone_ids(self) -> tuple[str, ...]:
        return tuple(zone.zone_id for zone in self.zones if zone.is_active)

    @model_validator(mode="after")
    def validate_analysis(self) -> Self:
        expected_id = f"{self.timeframe.value}:accumulation_analysis"
        if self.analysis_id != expected_id:
            raise ValueError(
                "accumulation-analysis ID does not match timeframe"
            )
        if self.interval != timeframe_interval(self.timeframe):
            raise ValueError(
                "accumulation-analysis interval does not match timeframe"
            )
        if self.evaluated_at > self.source_retrieved_at:
            raise ValueError(
                "accumulation analysis cannot follow source retrieval"
            )

        previous_started_at = None
        zone_ids: set[str] = set()
        for zone in self.zones:
            identity = (
                zone.exchange,
                zone.symbol_token,
                zone.symbol,
                zone.timeframe,
                zone.interval,
                zone.source,
                zone.source_retrieved_at,
                zone.evaluated_at,
            )
            expected_identity = (
                self.exchange,
                self.symbol_token,
                self.symbol,
                self.timeframe,
                self.interval,
                self.source,
                self.source_retrieved_at,
                self.evaluated_at,
            )
            if identity != expected_identity:
                raise ValueError(
                    "accumulation zone does not match analysis assignment"
                )
            if zone.zone_id in zone_ids:
                raise ValueError("accumulation-analysis zone IDs must be unique")
            if (
                previous_started_at is not None
                and zone.base_started_at <= previous_started_at
            ):
                raise ValueError(
                    "accumulation zones must be ordered by base start"
                )
            zone_ids.add(zone.zone_id)
            previous_started_at = zone.base_started_at
        return self
