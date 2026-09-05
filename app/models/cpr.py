from datetime import datetime
from enum import StrEnum
from math import isclose, isfinite
from typing import Literal, Self
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.analysis_timeframe import (
    SwingAnalysisTimeframe,
    timeframe_interval,
)
from app.models.technical import TechnicalModel


IST = ZoneInfo("Asia/Kolkata")
_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"


class CPRBasis(StrEnum):
    """Completed higher timeframe used to calculate a swing CPR."""

    WEEKLY = "weekly"
    MONTHLY = "monthly"


class CPRWidthRegime(StrEnum):
    """Width classification relative to prior comparable CPR ranges."""

    NARROW = "narrow"
    NORMAL = "normal"
    WIDE = "wide"
    INSUFFICIENT_HISTORY = "insufficient_history"


class CPRPricePosition(StrEnum):
    """Observed price location relative to the complete CPR band."""

    ABOVE = "above"
    INSIDE = "inside"
    BELOW = "below"


class CPRLifecycleState(StrEnum):
    """Latest deterministic interaction between price and the CPR band."""

    UNTESTED = "untested"
    TRADING_INSIDE = "trading_inside"
    BULLISH_BREAKOUT = "bullish_breakout"
    BULLISH_RECLAIM = "bullish_reclaim"
    BULLISH_RETEST = "bullish_retest"
    ACCEPTED_ABOVE = "accepted_above"
    BEARISH_BREAKDOWN = "bearish_breakdown"
    BEARISH_RECLAIM = "bearish_reclaim"
    BEARISH_RETEST = "bearish_retest"
    ACCEPTED_BELOW = "accepted_below"
    UPPER_REJECTION = "upper_rejection"
    LOWER_REJECTION = "lower_rejection"
    FAILED_BULLISH_BREAKOUT = "failed_bullish_breakout"
    FAILED_BEARISH_BREAKDOWN = "failed_bearish_breakdown"


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


class CPRAnalysisRecord(TechnicalModel):
    """One immutable, look-ahead-safe CPR observation for agent evidence."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.cpr_analysis.v1"] = (
        "jarvis.cpr_analysis.v1"
    )
    analysis_id: str = Field(
        min_length=1,
        pattern=r"^(daily|weekly):cpr:[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    basis: CPRBasis
    source: str = Field(min_length=1)
    source_period_started_at: datetime
    source_period_ended_at: datetime
    available_at: datetime
    valid_from: datetime
    valid_to: datetime
    evaluated_at: datetime
    source_retrieved_at: datetime
    source_high: float = Field(gt=0)
    source_low: float = Field(gt=0)
    source_close: float = Field(gt=0)
    pivot: float = Field(gt=0)
    bottom_central: float = Field(gt=0)
    top_central: float = Field(gt=0)
    width_percentage: float = Field(ge=0)
    width_percentile: float | None = Field(default=None, ge=0, le=100)
    width_sample_count: int = Field(ge=0)
    width_regime: CPRWidthRegime
    current_price: float = Field(gt=0)
    price_position: CPRPricePosition
    consecutive_acceptance_candles: int = Field(ge=0)
    lifecycle_state: CPRLifecycleState
    evidence_ids: tuple[str, ...] = Field(min_length=1)
    calculation_fingerprint: str = Field(
        pattern=_FINGERPRINT_PATTERN,
    )

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
            raise ValueError("CPR metadata cannot be blank")
        return value

    @field_validator(
        "source_period_started_at",
        "source_period_ended_at",
        "available_at",
        "valid_from",
        "valid_to",
        "evaluated_at",
        "source_retrieved_at",
    )
    @classmethod
    def normalize_timestamp(cls, value: datetime) -> datetime:
        return _as_ist(value, label="CPR timestamp")

    @field_validator(
        "source_high",
        "source_low",
        "source_close",
        "pivot",
        "bottom_central",
        "top_central",
        "width_percentage",
        "width_percentile",
        "current_price",
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
            label=f"CPR {info.field_name.replace('_', ' ')}",
        )

    @field_validator("evidence_ids")
    @classmethod
    def validate_evidence_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("CPR evidence IDs cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("CPR evidence IDs must be unique")
        return values

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        self._validate_identity()
        self._validate_time_order()
        self._validate_geometry()
        self._validate_width_classification()
        self._validate_price_position()
        self._validate_lifecycle()
        return self

    def _validate_identity(self) -> None:
        timeframe_prefix = f"{self.timeframe.value}:"
        if not self.analysis_id.startswith(f"{timeframe_prefix}cpr:"):
            raise ValueError("CPR analysis ID does not match its timeframe")
        if self.interval != timeframe_interval(self.timeframe):
            raise ValueError("CPR interval does not match its timeframe")
        expected_basis = (
            CPRBasis.WEEKLY
            if self.timeframe is SwingAnalysisTimeframe.DAILY
            else CPRBasis.MONTHLY
        )
        if self.basis is not expected_basis:
            raise ValueError("CPR basis does not match its swing timeframe")
        if any(
            not evidence_id.startswith(timeframe_prefix)
            for evidence_id in self.evidence_ids
        ):
            raise ValueError("CPR evidence must match its timeframe")

    def _validate_time_order(self) -> None:
        if not (
            self.source_period_started_at
            <= self.source_period_ended_at
            <= self.available_at
            <= self.valid_from
            <= self.valid_to
            <= self.evaluated_at
            <= self.source_retrieved_at
        ):
            raise ValueError(
                "CPR timestamps must preserve source, availability, validity, "
                "evaluation and retrieval order"
            )
        if self.source_period_ended_at >= self.valid_from:
            raise ValueError(
                "CPR source period must finish before its validity begins"
            )

    def _validate_geometry(self) -> None:
        if not self.source_low <= self.source_close <= self.source_high:
            raise ValueError("CPR source close must lie within source range")
        if self.source_high < self.source_low:
            raise ValueError("CPR source high cannot be below source low")

        expected_pivot = (
            self.source_high + self.source_low + self.source_close
        ) / 3
        raw_bottom = (self.source_high + self.source_low) / 2
        raw_top = 2 * expected_pivot - raw_bottom
        expected_bottom = min(raw_bottom, raw_top)
        expected_top = max(raw_bottom, raw_top)
        expected_width = (
            (expected_top - expected_bottom) / expected_pivot * 100
        )

        expected_values = (
            (self.pivot, expected_pivot, "pivot"),
            (self.bottom_central, expected_bottom, "bottom central"),
            (self.top_central, expected_top, "top central"),
            (self.width_percentage, expected_width, "width percentage"),
        )
        for observed, expected, label in expected_values:
            if not isclose(observed, expected, rel_tol=1e-9, abs_tol=1e-9):
                raise ValueError(f"CPR {label} does not match source OHLC")
        if not self.bottom_central <= self.pivot <= self.top_central:
            raise ValueError("CPR pivot must lie inside its central range")

    def _validate_width_classification(self) -> None:
        insufficient = (
            self.width_regime is CPRWidthRegime.INSUFFICIENT_HISTORY
        )
        if insufficient != (self.width_percentile is None):
            raise ValueError(
                "CPR width percentile must be absent only when history is "
                "insufficient"
            )
        if self.width_percentile is not None and self.width_sample_count == 0:
            raise ValueError(
                "CPR width percentile requires a historical sample"
            )

    def _validate_price_position(self) -> None:
        if self.current_price > self.top_central:
            expected = CPRPricePosition.ABOVE
        elif self.current_price < self.bottom_central:
            expected = CPRPricePosition.BELOW
        else:
            expected = CPRPricePosition.INSIDE
        if self.price_position is not expected:
            raise ValueError("CPR price position does not match current price")
        if (
            self.price_position is CPRPricePosition.INSIDE
            and self.consecutive_acceptance_candles != 0
        ):
            raise ValueError(
                "inside CPR price cannot retain an acceptance candle count"
            )

    def _validate_lifecycle(self) -> None:
        allowed = {
            CPRPricePosition.ABOVE: {
                CPRLifecycleState.UNTESTED,
                CPRLifecycleState.BULLISH_BREAKOUT,
                CPRLifecycleState.BULLISH_RECLAIM,
                CPRLifecycleState.BULLISH_RETEST,
                CPRLifecycleState.ACCEPTED_ABOVE,
            },
            CPRPricePosition.BELOW: {
                CPRLifecycleState.UNTESTED,
                CPRLifecycleState.BEARISH_BREAKDOWN,
                CPRLifecycleState.BEARISH_RECLAIM,
                CPRLifecycleState.BEARISH_RETEST,
                CPRLifecycleState.ACCEPTED_BELOW,
            },
            CPRPricePosition.INSIDE: {
                CPRLifecycleState.UNTESTED,
                CPRLifecycleState.TRADING_INSIDE,
                CPRLifecycleState.UPPER_REJECTION,
                CPRLifecycleState.LOWER_REJECTION,
                CPRLifecycleState.FAILED_BULLISH_BREAKOUT,
                CPRLifecycleState.FAILED_BEARISH_BREAKDOWN,
            },
        }
        if self.lifecycle_state not in allowed[self.price_position]:
            raise ValueError(
                "CPR lifecycle state does not match current price position"
            )
        if self.lifecycle_state in {
            CPRLifecycleState.ACCEPTED_ABOVE,
            CPRLifecycleState.ACCEPTED_BELOW,
            CPRLifecycleState.BULLISH_RETEST,
            CPRLifecycleState.BEARISH_RETEST,
        } and self.consecutive_acceptance_candles < 2:
            raise ValueError(
                "accepted CPR and retest states require two outside closes"
            )
        expected_evidence = (
            f"{self.timeframe.value}:cpr.lifecycle."
            f"{self.lifecycle_state.value}"
        )
        if expected_evidence not in self.evidence_ids:
            raise ValueError(
                "CPR evidence IDs must identify the lifecycle state"
            )
