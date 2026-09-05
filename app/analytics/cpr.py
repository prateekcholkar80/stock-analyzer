from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
import json
from math import isclose, isfinite
from typing import Self
from zoneinfo import ZoneInfo

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.analysis_timeframe import (
    SwingAnalysisTimeframe,
    timeframe_interval,
)
from app.models.cpr import (
    CPRAnalysisRecord,
    CPRBasis,
    CPRLifecycleState,
    CPRPricePosition,
    CPRWidthRegime,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.technical import TechnicalModel


IST = ZoneInfo("Asia/Kolkata")
_PeriodKey = tuple[int, int]


class CPRWidthClassificationConfig(TechnicalModel):
    """Point-in-time width-ranking controls for comparable CPR periods."""

    model_config = ConfigDict(frozen=True, strict=True)

    lookback_periods: int = Field(default=20, ge=3, le=260)
    minimum_samples: int = Field(default=8, ge=3, le=260)
    narrow_percentile_max: float = Field(default=25.0, ge=0, le=100)
    wide_percentile_min: float = Field(default=75.0, ge=0, le=100)

    @field_validator(
        "narrow_percentile_max",
        "wide_percentile_min",
        mode="before",
    )
    @classmethod
    def require_finite_percentiles(cls, value: float, info) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            raise ValueError(
                f"CPR {info.field_name.replace('_', ' ')} must be finite"
            )
        return float(value)

    @model_validator(mode="after")
    def validate_window_and_thresholds(self) -> Self:
        if self.minimum_samples > self.lookback_periods:
            raise ValueError(
                "CPR minimum samples cannot exceed width lookback"
            )
        if self.narrow_percentile_max >= self.wide_percentile_min:
            raise ValueError(
                "CPR narrow percentile must be below wide percentile"
            )
        return self


def calculate_latest_cpr(
    series: HistoricalCandleSeries,
    *,
    timeframe: SwingAnalysisTimeframe,
    evaluated_at: datetime | None = None,
    width_config: CPRWidthClassificationConfig | None = None,
) -> CPRAnalysisRecord:
    """Calculate the latest swing CPR from a completed prior period.

    Daily analysis derives CPR from the immediately preceding observed week;
    weekly analysis derives it from the immediately preceding observed month.
    A later target group proves that the source group has ended, while the
    first archive group is always excluded because it may start mid-period.
    Width ranking uses only earlier completed comparable periods. Acceptance
    and lifecycle interpretation use only completed target-period candles.
    """
    if series.interval != timeframe_interval(timeframe):
        raise ValueError("CPR source interval does not match timeframe")
    if not series.candles:
        raise ValueError("CPR calculation requires candles")
    _require_ordered_unique(series.candles)

    cutoff = evaluated_at or series.candles[-1].timestamp
    _require_aware(cutoff, label="CPR evaluation cutoff")
    if cutoff > series.retrieved_at:
        raise ValueError("CPR evaluation cannot follow source retrieval")

    eligible = [
        candle for candle in series.candles if candle.timestamp <= cutoff
    ]
    if not eligible:
        raise ValueError("CPR calculation has no candles at the cutoff")

    basis, key_fn = _basis_and_key(timeframe)
    resolved_width_config = width_config or CPRWidthClassificationConfig()
    groups = _group_candles(eligible, key_fn)
    ordered_keys = sorted(groups)
    if len(ordered_keys) < 3:
        raise ValueError(
            "CPR calculation requires an archive boundary, one completed "
            "source period and one target period"
        )

    source_key = ordered_keys[-2]
    target_key = ordered_keys[-1]
    source_candles = groups[source_key]
    target_candles = groups[target_key]
    source_high = max(candle.high for candle in source_candles)
    source_low = min(candle.low for candle in source_candles)
    source_close = source_candles[-1].close
    pivot = (source_high + source_low + source_close) / 3
    raw_bottom = (source_high + source_low) / 2
    raw_top = 2 * pivot - raw_bottom
    bottom = min(raw_bottom, raw_top)
    top = max(raw_bottom, raw_top)
    width_percentage = (top - bottom) / pivot * 100
    (
        width_percentile,
        width_sample_count,
        width_regime,
    ) = _classify_width(
        current_width=width_percentage,
        historical_widths=[
            _period_width(groups[key])
            for key in ordered_keys[1:-2]
        ],
        config=resolved_width_config,
    )
    current_price = target_candles[-1].close
    position = _price_position(
        current_price=current_price,
        bottom=bottom,
        top=top,
    )
    consecutive_acceptance_candles = _consecutive_acceptance_candles(
        target_candles=target_candles,
        position=position,
        bottom=bottom,
        top=top,
    )
    lifecycle_state = _lifecycle_state(
        target_candles=target_candles,
        position=position,
        consecutive_acceptance_candles=(
            consecutive_acceptance_candles
        ),
        bottom=bottom,
        top=top,
    )

    analysis_id = (
        f"{timeframe.value}:cpr:"
        f"{_period_label(basis, target_key)}"
    )
    evidence_ids = (
        f"{timeframe.value}:cpr.levels",
        f"{timeframe.value}:cpr.lifecycle.{lifecycle_state.value}",
    )
    fingerprint = _calculation_fingerprint(
        analysis_id=analysis_id,
        series=series,
        timeframe=timeframe,
        basis=basis,
        source_candles=source_candles,
        target_candles=target_candles,
        source_high=source_high,
        source_low=source_low,
        source_close=source_close,
        pivot=pivot,
        bottom=bottom,
        top=top,
        width_percentage=width_percentage,
        width_percentile=width_percentile,
        width_sample_count=width_sample_count,
        width_regime=width_regime,
        width_config=resolved_width_config,
        current_price=current_price,
        position=position,
        consecutive_acceptance_candles=(
            consecutive_acceptance_candles
        ),
        lifecycle_state=lifecycle_state,
    )

    return CPRAnalysisRecord(
        analysis_id=analysis_id,
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        timeframe=timeframe,
        interval=series.interval,
        basis=basis,
        source=series.source,
        source_period_started_at=source_candles[0].timestamp,
        source_period_ended_at=source_candles[-1].timestamp,
        available_at=source_candles[-1].timestamp,
        valid_from=target_candles[0].timestamp,
        valid_to=target_candles[-1].timestamp,
        evaluated_at=target_candles[-1].timestamp,
        source_retrieved_at=series.retrieved_at,
        source_high=source_high,
        source_low=source_low,
        source_close=source_close,
        pivot=pivot,
        bottom_central=bottom,
        top_central=top,
        width_percentage=width_percentage,
        width_percentile=width_percentile,
        width_sample_count=width_sample_count,
        width_regime=width_regime,
        current_price=current_price,
        price_position=position,
        consecutive_acceptance_candles=(
            consecutive_acceptance_candles
        ),
        lifecycle_state=lifecycle_state,
        evidence_ids=evidence_ids,
        calculation_fingerprint=fingerprint,
    )


def _require_aware(value: datetime, *, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must include timezone")


def _require_ordered_unique(candles: list[Candle]) -> None:
    for previous, current in zip(candles, candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "CPR source candles must have unique timestamps in "
                "ascending order"
            )


def _basis_and_key(
    timeframe: SwingAnalysisTimeframe,
) -> tuple[CPRBasis, Callable[[datetime], _PeriodKey]]:
    if timeframe is SwingAnalysisTimeframe.DAILY:
        return CPRBasis.WEEKLY, _week_key
    return CPRBasis.MONTHLY, _month_key


def _group_candles(
    candles: list[Candle],
    key_fn: Callable[[datetime], _PeriodKey],
) -> dict[_PeriodKey, list[Candle]]:
    groups: dict[_PeriodKey, list[Candle]] = {}
    for candle in candles:
        groups.setdefault(key_fn(candle.timestamp), []).append(candle)
    return groups


def _week_key(timestamp: datetime) -> _PeriodKey:
    local = timestamp.astimezone(IST)
    iso = local.isocalendar()
    return iso.year, iso.week


def _month_key(timestamp: datetime) -> _PeriodKey:
    local = timestamp.astimezone(IST)
    return local.year, local.month


def _period_label(basis: CPRBasis, key: _PeriodKey) -> str:
    year, period = key
    if basis is CPRBasis.WEEKLY:
        return f"weekly-{year}-W{period:02d}"
    return f"monthly-{year}-{period:02d}"


def _price_position(
    *,
    current_price: float,
    bottom: float,
    top: float,
) -> CPRPricePosition:
    if current_price > top:
        return CPRPricePosition.ABOVE
    if current_price < bottom:
        return CPRPricePosition.BELOW
    return CPRPricePosition.INSIDE


def _consecutive_acceptance_candles(
    *,
    target_candles: list[Candle],
    position: CPRPricePosition,
    bottom: float,
    top: float,
) -> int:
    if position is CPRPricePosition.INSIDE:
        return 0
    if position is CPRPricePosition.ABOVE:
        accepted = lambda close: close > top
    else:
        accepted = lambda close: close < bottom

    count = 0
    for candle in reversed(target_candles):
        if not accepted(candle.close):
            break
        count += 1
    return count


def _lifecycle_state(
    *,
    target_candles: list[Candle],
    position: CPRPricePosition,
    consecutive_acceptance_candles: int,
    bottom: float,
    top: float,
) -> CPRLifecycleState:
    if len(target_candles) < 2:
        return CPRLifecycleState.UNTESTED

    previous = target_candles[-2]
    latest = target_candles[-1]
    previous_position = _price_position(
        current_price=previous.close,
        bottom=bottom,
        top=top,
    )

    if position is CPRPricePosition.ABOVE:
        if previous_position is CPRPricePosition.BELOW:
            return CPRLifecycleState.BULLISH_RECLAIM
        if previous_position is CPRPricePosition.INSIDE:
            return CPRLifecycleState.BULLISH_BREAKOUT
        if latest.low <= top:
            return CPRLifecycleState.BULLISH_RETEST
        if consecutive_acceptance_candles >= 2:
            return CPRLifecycleState.ACCEPTED_ABOVE

    if position is CPRPricePosition.BELOW:
        if previous_position is CPRPricePosition.ABOVE:
            return CPRLifecycleState.BEARISH_RECLAIM
        if previous_position is CPRPricePosition.INSIDE:
            return CPRLifecycleState.BEARISH_BREAKDOWN
        if latest.high >= bottom:
            return CPRLifecycleState.BEARISH_RETEST
        if consecutive_acceptance_candles >= 2:
            return CPRLifecycleState.ACCEPTED_BELOW

    if position is CPRPricePosition.INSIDE:
        if previous_position is CPRPricePosition.ABOVE:
            return CPRLifecycleState.FAILED_BULLISH_BREAKOUT
        if previous_position is CPRPricePosition.BELOW:
            return CPRLifecycleState.FAILED_BEARISH_BREAKDOWN
        if latest.high > top and latest.low >= bottom:
            return CPRLifecycleState.UPPER_REJECTION
        if latest.low < bottom and latest.high <= top:
            return CPRLifecycleState.LOWER_REJECTION
        return CPRLifecycleState.TRADING_INSIDE

    return CPRLifecycleState.UNTESTED


def _period_width(candles: list[Candle]) -> float:
    high = max(candle.high for candle in candles)
    low = min(candle.low for candle in candles)
    close = candles[-1].close
    pivot = (high + low + close) / 3
    if pivot <= 0:
        raise ValueError("CPR historical pivot must be positive")
    raw_bottom = (high + low) / 2
    raw_top = 2 * pivot - raw_bottom
    return abs(raw_top - raw_bottom) / pivot * 100


def _classify_width(
    *,
    current_width: float,
    historical_widths: list[float],
    config: CPRWidthClassificationConfig,
) -> tuple[float | None, int, CPRWidthRegime]:
    window = historical_widths[-config.lookback_periods :]
    sample_count = len(window)
    if sample_count < config.minimum_samples:
        return None, sample_count, CPRWidthRegime.INSUFFICIENT_HISTORY

    below = sum(value < current_width for value in window)
    equal = sum(
        isclose(value, current_width, rel_tol=1e-12, abs_tol=1e-12)
        for value in window
    )
    percentile = (below + equal * 0.5) / sample_count * 100
    if percentile <= config.narrow_percentile_max:
        regime = CPRWidthRegime.NARROW
    elif percentile >= config.wide_percentile_min:
        regime = CPRWidthRegime.WIDE
    else:
        regime = CPRWidthRegime.NORMAL
    return percentile, sample_count, regime


def _calculation_fingerprint(
    *,
    analysis_id: str,
    series: HistoricalCandleSeries,
    timeframe: SwingAnalysisTimeframe,
    basis: CPRBasis,
    source_candles: list[Candle],
    target_candles: list[Candle],
    source_high: float,
    source_low: float,
    source_close: float,
    pivot: float,
    bottom: float,
    top: float,
    width_percentage: float,
    width_percentile: float | None,
    width_sample_count: int,
    width_regime: CPRWidthRegime,
    width_config: CPRWidthClassificationConfig,
    current_price: float,
    position: CPRPricePosition,
    consecutive_acceptance_candles: int,
    lifecycle_state: CPRLifecycleState,
) -> str:
    payload = {
        "analysis_id": analysis_id,
        "exchange": series.exchange,
        "symbol_token": series.symbol_token,
        "symbol": series.symbol,
        "interval": series.interval,
        "source": series.source,
        "timeframe": timeframe.value,
        "basis": basis.value,
        "source_period_started_at": _canonical_time(
            source_candles[0].timestamp
        ),
        "source_period_ended_at": _canonical_time(
            source_candles[-1].timestamp
        ),
        "valid_from": _canonical_time(target_candles[0].timestamp),
        "valid_to": _canonical_time(target_candles[-1].timestamp),
        "source_high": source_high,
        "source_low": source_low,
        "source_close": source_close,
        "pivot": pivot,
        "bottom_central": bottom,
        "top_central": top,
        "width_percentage": width_percentage,
        "width_percentile": width_percentile,
        "width_sample_count": width_sample_count,
        "width_regime": width_regime.value,
        "width_config": width_config.model_dump(mode="json"),
        "current_price": current_price,
        "price_position": position.value,
        "consecutive_acceptance_candles": (
            consecutive_acceptance_candles
        ),
        "lifecycle_state": lifecycle_state.value,
    }
    canonical = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return sha256(canonical).hexdigest()


def _canonical_time(value: datetime) -> str:
    return value.astimezone(IST).isoformat()
