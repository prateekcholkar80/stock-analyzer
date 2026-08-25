from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Literal, Self

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
from app.models.signals import SignalValue
from app.models.technical import TechnicalModel


class SwingSetupSide(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class SwingSetupStepState(StrEnum):
    CONFIRMED = "confirmed"
    DEVELOPING = "developing"
    PENDING = "pending"
    CONTRADICTED = "contradicted"
    INVALIDATED = "invalidated"
    UNAVAILABLE = "unavailable"


class SwingSetupStepId(StrEnum):
    BULLISH_PRIOR_DOWNTREND = "bullish.prior_downtrend"
    BULLISH_CHANGE_OF_CHARACTER = "bullish.change_of_character"
    BULLISH_BREAK_ABOVE_RESISTANCE = (
        "bullish.break_of_structure_above_resistance"
    )
    BULLISH_VOLUME_EXPANSION = "bullish.volume_expansion"
    BULLISH_PULLBACK_INTO_VALUE = "bullish.pullback_into_value"
    BULLISH_EMA_ALIGNMENT = "bullish.ema_alignment"
    BULLISH_RSI_REGIME = "bullish.rsi_regime"
    BULLISH_HIGHER_LOW = "bullish.higher_low"
    BULLISH_HIGHER_HIGH = "bullish.higher_high"

    BEARISH_UPTREND_EXHAUSTION = "bearish.uptrend_exhaustion"
    BEARISH_LIQUIDITY_SWEEP = "bearish.liquidity_sweep"
    BEARISH_CHANGE_OF_CHARACTER = "bearish.change_of_character"
    BEARISH_BREAK_BELOW_SUPPORT = (
        "bearish.break_of_structure_below_support"
    )
    BEARISH_VOLUME_EXPANSION = "bearish.volume_expansion"
    BEARISH_FVG_RETEST = "bearish.fvg_retest"
    BEARISH_EMA_ALIGNMENT = "bearish.ema_alignment"
    BEARISH_RSI_DIVERGENCE = "bearish.rsi_divergence"


BULLISH_SETUP_SEQUENCE = (
    SwingSetupStepId.BULLISH_PRIOR_DOWNTREND,
    SwingSetupStepId.BULLISH_CHANGE_OF_CHARACTER,
    SwingSetupStepId.BULLISH_BREAK_ABOVE_RESISTANCE,
    SwingSetupStepId.BULLISH_VOLUME_EXPANSION,
    SwingSetupStepId.BULLISH_PULLBACK_INTO_VALUE,
    SwingSetupStepId.BULLISH_EMA_ALIGNMENT,
    SwingSetupStepId.BULLISH_RSI_REGIME,
    SwingSetupStepId.BULLISH_HIGHER_LOW,
    SwingSetupStepId.BULLISH_HIGHER_HIGH,
)

BEARISH_SETUP_SEQUENCE = (
    SwingSetupStepId.BEARISH_UPTREND_EXHAUSTION,
    SwingSetupStepId.BEARISH_LIQUIDITY_SWEEP,
    SwingSetupStepId.BEARISH_CHANGE_OF_CHARACTER,
    SwingSetupStepId.BEARISH_BREAK_BELOW_SUPPORT,
    SwingSetupStepId.BEARISH_VOLUME_EXPANSION,
    SwingSetupStepId.BEARISH_FVG_RETEST,
    SwingSetupStepId.BEARISH_EMA_ALIGNMENT,
    SwingSetupStepId.BEARISH_RSI_DIVERGENCE,
)

_SEQUENCES = {
    SwingSetupSide.BULLISH: BULLISH_SETUP_SEQUENCE,
    SwingSetupSide.BEARISH: BEARISH_SETUP_SEQUENCE,
}

_STEP_LABELS = {
    SwingSetupStepId.BULLISH_PRIOR_DOWNTREND: "Prior downtrend",
    SwingSetupStepId.BULLISH_CHANGE_OF_CHARACTER: "Bullish CHOCH",
    SwingSetupStepId.BULLISH_BREAK_ABOVE_RESISTANCE: (
        "BOS above resistance"
    ),
    SwingSetupStepId.BULLISH_VOLUME_EXPANSION: "Volume expansion",
    SwingSetupStepId.BULLISH_PULLBACK_INTO_VALUE: (
        "Pullback into FVG or support"
    ),
    SwingSetupStepId.BULLISH_EMA_ALIGNMENT: "EMA20 above EMA50",
    SwingSetupStepId.BULLISH_RSI_REGIME: "RSI above 50",
    SwingSetupStepId.BULLISH_HIGHER_LOW: "Confirmed higher low",
    SwingSetupStepId.BULLISH_HIGHER_HIGH: "Confirmed next higher high",
    SwingSetupStepId.BEARISH_UPTREND_EXHAUSTION: "Uptrend exhaustion",
    SwingSetupStepId.BEARISH_LIQUIDITY_SWEEP: "Liquidity sweep",
    SwingSetupStepId.BEARISH_CHANGE_OF_CHARACTER: "Bearish CHOCH",
    SwingSetupStepId.BEARISH_BREAK_BELOW_SUPPORT: "BOS below support",
    SwingSetupStepId.BEARISH_VOLUME_EXPANSION: "Volume expansion",
    SwingSetupStepId.BEARISH_FVG_RETEST: "Bearish FVG retest",
    SwingSetupStepId.BEARISH_EMA_ALIGNMENT: "EMA20 below EMA50",
    SwingSetupStepId.BEARISH_RSI_DIVERGENCE: "Bearish RSI divergence",
}


def _validate_signal_values(
    values: dict[str, SignalValue],
    *,
    label: str,
) -> dict[str, SignalValue]:
    for name, value in values.items():
        if not name.strip():
            raise ValueError(f"setup {label} names cannot be blank")
        if isinstance(value, float) and not isfinite(value):
            raise ValueError(f"setup {label} values must be finite")
        if isinstance(value, str) and not value.strip():
            raise ValueError(f"setup {label} text cannot be blank")
    return values


class SwingSetupStep(TechnicalModel):
    """One immutable, point-in-time step in a swing setup checklist."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.swing_setup_step.v1"] = (
        "jarvis.swing_setup_step.v1"
    )
    step_id: SwingSetupStepId
    side: SwingSetupSide
    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    sequence: int = Field(ge=1)
    state: SwingSetupStepState
    evidence_ids: tuple[str, ...] = ()
    observed_at: datetime | None = None
    available_at: datetime | None = None
    confirmed_at: datetime | None = None
    invalidated_at: datetime | None = None
    evaluated_at: datetime
    explanation: str = Field(min_length=1)
    observed_values: dict[str, SignalValue] = Field(default_factory=dict)
    thresholds: dict[str, SignalValue] = Field(default_factory=dict)

    @computed_field
    @property
    def label(self) -> str:
        return _STEP_LABELS[self.step_id]

    @field_validator("explanation")
    @classmethod
    def reject_blank_explanation(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("setup-step explanation cannot be blank")
        return value

    @field_validator(
        "observed_at",
        "available_at",
        "confirmed_at",
        "invalidated_at",
        "evaluated_at",
    )
    @classmethod
    def require_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError("setup-step timestamps must include timezone")
        return value

    @field_validator("evidence_ids")
    @classmethod
    def require_unique_evidence_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("setup-step evidence IDs cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("setup-step evidence IDs must be unique")
        return values

    @field_validator("observed_values")
    @classmethod
    def validate_observed_values(
        cls,
        values: dict[str, SignalValue],
    ) -> dict[str, SignalValue]:
        return _validate_signal_values(values, label="observed-value")

    @field_validator("thresholds")
    @classmethod
    def validate_thresholds(
        cls,
        values: dict[str, SignalValue],
    ) -> dict[str, SignalValue]:
        return _validate_signal_values(values, label="threshold")

    @model_validator(mode="after")
    def validate_step(self) -> Self:
        sequence = _SEQUENCES[self.side]
        if self.step_id not in sequence:
            raise ValueError("setup-step ID belongs to the other setup side")
        if self.sequence != sequence.index(self.step_id) + 1:
            raise ValueError("setup-step sequence does not match its ID")
        if self.interval != timeframe_interval(self.timeframe):
            raise ValueError("setup-step interval does not match timeframe")
        prefix = f"{self.timeframe.value}:"
        if any(not value.startswith(prefix) for value in self.evidence_ids):
            raise ValueError(
                "setup-step evidence IDs must match the assigned timeframe"
            )
        self._validate_timestamps()
        self._validate_state_requirements()
        return self

    def _validate_timestamps(self) -> None:
        if (self.observed_at is None) != (self.available_at is None):
            raise ValueError(
                "setup-step observation and availability must appear together"
            )
        if self.observed_at is not None:
            if self.observed_at > self.available_at:
                raise ValueError(
                    "setup-step availability cannot precede observation"
                )
            if self.available_at > self.evaluated_at:
                raise ValueError("setup-step cannot use future evidence")
        if self.confirmed_at is not None:
            if self.available_at is None or self.confirmed_at < self.available_at:
                raise ValueError(
                    "setup-step confirmation cannot precede availability"
                )
            if self.confirmed_at > self.evaluated_at:
                raise ValueError("setup-step confirmation cannot be in the future")
        if self.invalidated_at is not None:
            if (
                self.available_at is None
                or self.invalidated_at < self.available_at
            ):
                raise ValueError(
                    "setup-step invalidation cannot precede availability"
                )
            if (
                self.confirmed_at is not None
                and self.invalidated_at < self.confirmed_at
            ):
                raise ValueError(
                    "setup-step invalidation cannot precede confirmation"
                )
            if self.invalidated_at > self.evaluated_at:
                raise ValueError("setup-step invalidation cannot be in the future")

    def _validate_state_requirements(self) -> None:
        evidenced_states = {
            SwingSetupStepState.CONFIRMED,
            SwingSetupStepState.DEVELOPING,
            SwingSetupStepState.CONTRADICTED,
            SwingSetupStepState.INVALIDATED,
        }
        if self.state in evidenced_states and (
            not self.evidence_ids or self.available_at is None
        ):
            raise ValueError(
                "evidenced setup-step states require available evidence"
            )
        if self.state is SwingSetupStepState.CONFIRMED:
            if self.confirmed_at is None or self.invalidated_at is not None:
                raise ValueError(
                    "confirmed setup step requires confirmation only"
                )
        elif self.state is SwingSetupStepState.INVALIDATED:
            if self.invalidated_at is None:
                raise ValueError(
                    "invalidated setup step requires invalidation time"
                )
        elif self.confirmed_at is not None or self.invalidated_at is not None:
            raise ValueError(
                "unconfirmed setup-step state cannot carry terminal timestamps"
            )
        if self.state is SwingSetupStepState.UNAVAILABLE and (
            self.evidence_ids
            or self.observed_at is not None
            or self.observed_values
        ):
            raise ValueError(
                "unavailable setup step cannot claim observed evidence"
            )


class TimeframeSwingSetup(TechnicalModel):
    """Complete ordered bullish or bearish matrix for one timeframe."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.timeframe_swing_setup.v1"] = (
        "jarvis.timeframe_swing_setup.v1"
    )
    setup_id: str = Field(
        min_length=1,
        pattern=r"^(daily|weekly):(bullish|bearish)_setup$",
    )
    side: SwingSetupSide
    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    evaluated_at: datetime
    steps: tuple[SwingSetupStep, ...] = Field(min_length=8, max_length=9)

    @field_validator("evaluated_at")
    @classmethod
    def require_evaluation_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timeframe setup evaluation requires timezone")
        return value

    @model_validator(mode="after")
    def validate_complete_matrix(self) -> Self:
        expected_id = f"{self.timeframe.value}:{self.side.value}_setup"
        if self.setup_id != expected_id:
            raise ValueError("timeframe setup ID does not match its assignment")
        if self.interval != timeframe_interval(self.timeframe):
            raise ValueError("timeframe setup interval is incorrect")
        expected_sequence = _SEQUENCES[self.side]
        if tuple(step.step_id for step in self.steps) != expected_sequence:
            raise ValueError(
                "timeframe setup must contain the complete canonical sequence"
            )
        for step in self.steps:
            if (
                step.side is not self.side
                or step.timeframe is not self.timeframe
                or step.interval != self.interval
                or step.evaluated_at != self.evaluated_at
            ):
                raise ValueError(
                    "timeframe setup step does not match its parent assignment"
                )
        return self
