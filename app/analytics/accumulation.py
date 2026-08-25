from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from math import isfinite
from typing import Self

import numpy as np
from pydantic import ConfigDict, Field, field_validator, model_validator

from app.analytics.indicators import calculate_atr, calculate_obv
from app.models.accumulation import (
    AccumulationEvidenceMetrics,
    AccumulationLifecycleEvent,
    AccumulationLifecycleState,
    AccumulationZone,
    LiquidityPoolSide,
    LiquiditySweep,
    TimeframeAccumulationAnalysis,
)
from app.models.analysis_timeframe import SwingAnalysisTimeframe
from app.models.market import Candle, HistoricalCandleSeries
from app.models.technical import TechnicalModel


class AccumulationTimeframeProfile(TechnicalModel):
    """Configurable structural thresholds for one candle timeframe."""

    model_config = ConfigDict(frozen=True, strict=True)

    minimum_base_candles: int = Field(ge=3)
    maximum_base_candles: int = Field(ge=3)
    maximum_range_width_percentage: float = Field(gt=0)
    minimum_close_containment_percentage: float = Field(ge=0, le=100)
    minimum_boundary_touches: int = Field(ge=1)
    maximum_absolute_slope_percentage: float = Field(ge=0)
    minimum_confidence_score: float = Field(ge=0, le=100)
    maximum_zone_age_candles: int = Field(ge=1)
    maximum_zones: int = Field(ge=1)

    @field_validator(
        "maximum_range_width_percentage",
        "minimum_close_containment_percentage",
        "maximum_absolute_slope_percentage",
        "minimum_confidence_score",
        mode="before",
    )
    @classmethod
    def require_finite_thresholds(cls, value: float, info) -> float:
        return _finite(
            value,
            label=info.field_name.replace("_", " "),
        )

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.maximum_base_candles < self.minimum_base_candles:
            raise ValueError(
                "maximum accumulation window cannot be below minimum"
            )
        return self


def _daily_profile() -> AccumulationTimeframeProfile:
    return AccumulationTimeframeProfile(
        minimum_base_candles=15,
        maximum_base_candles=45,
        maximum_range_width_percentage=8.0,
        minimum_close_containment_percentage=72.0,
        minimum_boundary_touches=2,
        maximum_absolute_slope_percentage=0.12,
        minimum_confidence_score=60.0,
        maximum_zone_age_candles=60,
        maximum_zones=5,
    )


def _weekly_profile() -> AccumulationTimeframeProfile:
    return AccumulationTimeframeProfile(
        minimum_base_candles=8,
        maximum_base_candles=26,
        maximum_range_width_percentage=18.0,
        minimum_close_containment_percentage=68.0,
        minimum_boundary_touches=2,
        maximum_absolute_slope_percentage=0.35,
        minimum_confidence_score=60.0,
        maximum_zone_age_candles=26,
        maximum_zones=3,
    )


class AccumulationDetectionConfig(TechnicalModel):
    """Deterministic, timeframe-aware accumulation detector settings."""

    model_config = ConfigDict(frozen=True, strict=True)

    daily: AccumulationTimeframeProfile = Field(
        default_factory=_daily_profile
    )
    weekly: AccumulationTimeframeProfile = Field(
        default_factory=_weekly_profile
    )
    atr_period: int = Field(default=14, ge=2)
    boundary_quantile: float = Field(default=0.20, gt=0, lt=0.5)
    touch_tolerance_percentage: float = Field(default=0.75, ge=0)
    minimum_sweep_percentage: float = Field(default=0.10, gt=0)
    maximum_sweep_percentage: float = Field(default=3.0, gt=0)
    maximum_reclaim_candles: int = Field(default=2, ge=1)
    breakout_buffer_percentage: float = Field(default=0.20, ge=0)
    retest_tolerance_percentage: float = Field(default=0.75, ge=0)
    volume_baseline_candles: int = Field(default=20, ge=1)
    minimum_nonzero_volume_percentage: float = Field(
        default=80.0,
        ge=0,
        le=100,
    )
    overlap_suppression_ratio: float = Field(default=0.50, gt=0, le=1)

    @field_validator(
        "boundary_quantile",
        "touch_tolerance_percentage",
        "minimum_sweep_percentage",
        "maximum_sweep_percentage",
        "breakout_buffer_percentage",
        "retest_tolerance_percentage",
        "minimum_nonzero_volume_percentage",
        "overlap_suppression_ratio",
        mode="before",
    )
    @classmethod
    def require_finite_thresholds(cls, value: float, info) -> float:
        return _finite(
            value,
            label=info.field_name.replace("_", " "),
        )

    @model_validator(mode="after")
    def validate_sweep_range(self) -> Self:
        if self.maximum_sweep_percentage <= self.minimum_sweep_percentage:
            raise ValueError(
                "maximum sweep percentage must exceed minimum sweep percentage"
            )
        return self

    def profile_for(
        self,
        timeframe: SwingAnalysisTimeframe,
    ) -> AccumulationTimeframeProfile:
        if timeframe is SwingAnalysisTimeframe.DAILY:
            return self.daily
        return self.weekly


@dataclass(frozen=True)
class _Candidate:
    start_index: int
    end_index: int
    lower_price: float
    upper_price: float
    metrics: AccumulationEvidenceMetrics

    @property
    def candle_count(self) -> int:
        return self.end_index - self.start_index + 1


def detect_accumulation_zones(
    series: HistoricalCandleSeries,
    *,
    config: AccumulationDetectionConfig | None = None,
    as_of: datetime | None = None,
) -> TimeframeAccumulationAnalysis:
    """Detect daily/weekly accumulation without using future candles."""
    if not isinstance(series, HistoricalCandleSeries):
        raise ValueError(
            "accumulation detector series must be HistoricalCandleSeries"
        )
    resolved = (
        config
        if config is not None
        else AccumulationDetectionConfig()
    )
    if not isinstance(resolved, AccumulationDetectionConfig):
        raise ValueError(
            "accumulation detector config must be AccumulationDetectionConfig"
        )
    timeframe = _timeframe(series.interval)
    evaluated_at = as_of or series.retrieved_at
    _validate_evaluation_time(evaluated_at, series.retrieved_at)
    _validate_candle_order(series.candles)

    available = [
        candle for candle in series.candles
        if candle.timestamp <= evaluated_at
    ]
    _validate_positive_prices(available)
    profile = resolved.profile_for(timeframe)
    if len(available) < max(
        profile.minimum_base_candles,
        resolved.atr_period + 1,
    ):
        return _analysis(
            series,
            timeframe,
            evaluated_at,
            (),
        )

    prefix = HistoricalCandleSeries(
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        interval=series.interval,
        candles=available,
        retrieved_at=series.retrieved_at,
        source=series.source,
    )
    atr = calculate_atr(prefix, resolved.atr_period)
    obv = calculate_obv(prefix)
    atr_by_time = {point.timestamp: point.value for point in atr.points}
    obv_by_time = {point.timestamp: point.value for point in obv.points}

    candidates = _scan_candidates(
        available,
        profile,
        resolved,
        atr_by_time,
        obv_by_time,
    )
    selected = _select_distinct_candidates(
        candidates,
        profile.maximum_zones,
        resolved.overlap_suppression_ratio,
    )
    zones = tuple(
        _build_zone(
            series,
            timeframe,
            evaluated_at,
            available,
            candidate,
            profile,
            resolved,
        )
        for candidate in selected
    )
    return _analysis(series, timeframe, evaluated_at, zones)


def _scan_candidates(
    candles: list[Candle],
    profile: AccumulationTimeframeProfile,
    config: AccumulationDetectionConfig,
    atr_by_time: dict[datetime, float],
    obv_by_time: dict[datetime, float],
) -> list[_Candidate]:
    candidates: list[_Candidate] = []
    for end_index in range(
        profile.minimum_base_candles - 1,
        len(candles),
    ):
        maximum_window = min(
            profile.maximum_base_candles,
            end_index + 1,
        )
        for candle_count in range(
            profile.minimum_base_candles,
            maximum_window + 1,
        ):
            start_index = end_index - candle_count + 1
            candidate = _evaluate_candidate(
                candles,
                start_index,
                end_index,
                profile,
                config,
                atr_by_time,
                obv_by_time,
            )
            if candidate is not None:
                candidates.append(candidate)
    return candidates


def _evaluate_candidate(
    candles: list[Candle],
    start_index: int,
    end_index: int,
    profile: AccumulationTimeframeProfile,
    config: AccumulationDetectionConfig,
    atr_by_time: dict[datetime, float],
    obv_by_time: dict[datetime, float],
) -> _Candidate | None:
    window = candles[start_index:end_index + 1]
    lows = np.asarray([candle.low for candle in window], dtype=np.float64)
    highs = np.asarray([candle.high for candle in window], dtype=np.float64)
    closes = np.asarray([candle.close for candle in window], dtype=np.float64)
    lower = float(np.quantile(lows, config.boundary_quantile))
    upper = float(np.quantile(highs, 1 - config.boundary_quantile))
    if lower <= 0 or upper <= lower:
        return None

    upper_break = upper * (1 + config.breakout_buffer_percentage / 100)
    lower_break = lower * (1 - config.breakout_buffer_percentage / 100)
    if np.any(closes > upper_break) or np.any(closes < lower_break):
        return None

    width_percentage = (upper - lower) / lower * 100
    containment = float(
        np.mean((closes >= lower) & (closes <= upper)) * 100
    )
    slope = _normalized_slope(closes)
    atr_values = [
        atr_by_time[candle.timestamp]
        for candle in window
        if candle.timestamp in atr_by_time
    ]
    if not atr_values:
        return None
    mean_atr = float(np.mean(atr_values))
    touch_tolerance = max(
        lower * config.touch_tolerance_percentage / 100,
        mean_atr * 0.20,
    )
    lower_touches = sum(
        candle.low <= lower + touch_tolerance
        and candle.close >= lower
        for candle in window
    )
    upper_touches = sum(
        candle.high >= upper - touch_tolerance
        and candle.close <= upper
        for candle in window
    )
    lower_rejections = sum(
        candle.low < lower and candle.close >= lower
        for candle in window
    )
    nonzero_volume = sum(candle.volume > 0 for candle in window) / len(window) * 100
    bullish_volume_share = _bullish_volume_share(window)
    down_volume_contraction = _down_volume_contraction(window)
    obv_values = np.asarray(
        [obv_by_time[candle.timestamp] for candle in window],
        dtype=np.float64,
    )
    obv_slope = _linear_slope(obv_values)
    atr_compression = _atr_compression(
        candles,
        start_index,
        window,
        atr_by_time,
    )
    confidence = _confidence_score(
        width_percentage=width_percentage,
        containment=containment,
        lower_touches=lower_touches,
        upper_touches=upper_touches,
        lower_rejections=lower_rejections,
        slope=slope,
        atr_compression=atr_compression,
        bullish_volume_share=bullish_volume_share,
        down_volume_contraction=down_volume_contraction,
        obv_slope=obv_slope,
        profile=profile,
    )
    demand_evidence = (
        obv_slope > 0
        or bullish_volume_share >= 52.0
        or down_volume_contraction >= 10.0
        or lower_rejections >= 2
    )
    qualified = (
        width_percentage <= profile.maximum_range_width_percentage
        and containment >= profile.minimum_close_containment_percentage
        and lower_touches >= profile.minimum_boundary_touches
        and upper_touches >= profile.minimum_boundary_touches
        and abs(slope) <= profile.maximum_absolute_slope_percentage
        and nonzero_volume >= config.minimum_nonzero_volume_percentage
        and confidence >= profile.minimum_confidence_score
        and demand_evidence
    )
    if not qualified:
        return None

    return _Candidate(
        start_index=start_index,
        end_index=end_index,
        lower_price=lower,
        upper_price=upper,
        metrics=AccumulationEvidenceMetrics(
            candle_count=len(window),
            range_width_percentage=width_percentage,
            normalized_price_slope_percentage=slope,
            atr_compression_percentage=atr_compression,
            close_containment_percentage=containment,
            lower_boundary_touch_count=lower_touches,
            upper_boundary_touch_count=upper_touches,
            lower_rejection_count=lower_rejections,
            nonzero_volume_percentage=nonzero_volume,
            bullish_volume_share_percentage=bullish_volume_share,
            down_volume_contraction_percentage=down_volume_contraction,
            obv_slope=obv_slope,
            confidence_score=confidence,
        ),
    )


def _select_distinct_candidates(
    candidates: list[_Candidate],
    maximum_zones: int,
    suppression_ratio: float,
) -> list[_Candidate]:
    ranked = sorted(
        candidates,
        key=lambda item: (
            -item.metrics.confidence_score,
            -item.candle_count,
            -item.end_index,
            item.lower_price,
        ),
    )
    selected: list[_Candidate] = []
    for candidate in ranked:
        if any(
            candidate.start_index == existing.start_index
            for existing in selected
        ):
            continue
        if any(
            _same_base(candidate, existing, suppression_ratio)
            for existing in selected
        ):
            continue
        selected.append(candidate)
        if len(selected) >= maximum_zones:
            break
    return sorted(
        selected,
        key=lambda item: (
            item.start_index,
            item.end_index,
            item.lower_price,
        ),
    )


def _same_base(
    left: _Candidate,
    right: _Candidate,
    suppression_ratio: float,
) -> bool:
    time_overlap = max(
        0,
        min(left.end_index, right.end_index)
        - max(left.start_index, right.start_index)
        + 1,
    )
    time_ratio = time_overlap / min(left.candle_count, right.candle_count)
    price_overlap = max(
        0.0,
        min(left.upper_price, right.upper_price)
        - max(left.lower_price, right.lower_price),
    )
    left_width = left.upper_price - left.lower_price
    right_width = right.upper_price - right.lower_price
    price_ratio = price_overlap / min(left_width, right_width)
    return time_ratio >= suppression_ratio and price_ratio >= suppression_ratio


def _build_zone(
    series: HistoricalCandleSeries,
    timeframe: SwingAnalysisTimeframe,
    evaluated_at: datetime,
    candles: list[Candle],
    candidate: _Candidate,
    profile: AccumulationTimeframeProfile,
    config: AccumulationDetectionConfig,
) -> AccumulationZone:
    window = candles[candidate.start_index:candidate.end_index + 1]
    digest = _zone_digest(series, timeframe, window, candidate)
    forming_id = f"{timeframe.value}:accumulation.{digest}.forming"
    confirmed_id = f"{timeframe.value}:accumulation.{digest}.confirmed"
    lifecycle = [
        AccumulationLifecycleEvent(
            state=AccumulationLifecycleState.FORMING,
            observed_at=window[-2].timestamp,
            available_at=window[-2].timestamp,
            evidence_ids=(forming_id,),
            explanation=(
                "Price compression and repeated range reactions were forming."
            ),
        ),
        AccumulationLifecycleEvent(
            state=AccumulationLifecycleState.CONFIRMED,
            observed_at=window[-1].timestamp,
            available_at=window[-1].timestamp,
            evidence_ids=(confirmed_id,),
            explanation=(
                "The range met deterministic price-volume accumulation rules."
            ),
        ),
    ]
    follow_up = candles[candidate.end_index + 1:]
    metrics = candidate.metrics
    state = AccumulationLifecycleState.CONFIRMED
    for age, candle in enumerate(follow_up, start=1):
        next_state = None
        explanation = ""
        if state is AccumulationLifecycleState.CONFIRMED:
            if candle.close > candidate.upper_price * (
                1 + config.breakout_buffer_percentage / 100
            ):
                next_state = AccumulationLifecycleState.BREAKOUT
                explanation = (
                    "A completed candle closed above the accumulation ceiling."
                )
                metrics = metrics.model_copy(
                    update={
                        "breakout_volume_multiple": _volume_multiple(
                            candles,
                            candidate.end_index + age,
                            config.volume_baseline_candles,
                        )
                    }
                )
            elif candle.close < candidate.lower_price * (
                1 - config.breakout_buffer_percentage / 100
            ):
                next_state = AccumulationLifecycleState.INVALIDATED
                explanation = (
                    "A completed candle closed below the accumulation floor."
                )
            elif age > profile.maximum_zone_age_candles:
                next_state = AccumulationLifecycleState.EXPIRED
                explanation = (
                    "The confirmed range expired without a timely breakout."
                )
        elif state is AccumulationLifecycleState.BREAKOUT:
            if candle.close < candidate.lower_price:
                next_state = AccumulationLifecycleState.FAILED_BREAKOUT
                explanation = (
                    "Price closed below the base after the upside breakout."
                )
            elif _retests_ceiling(candle, candidate.upper_price, config):
                next_state = AccumulationLifecycleState.RETESTING
                explanation = (
                    "Price returned to the former accumulation ceiling."
                )
        elif state is AccumulationLifecycleState.RETESTING:
            if candle.close < candidate.lower_price:
                next_state = AccumulationLifecycleState.FAILED_BREAKOUT
                explanation = "The retest failed below the accumulation floor."
            elif candle.close >= candidate.upper_price:
                next_state = AccumulationLifecycleState.HOLDING_AS_SUPPORT
                explanation = (
                    "A later completed candle held above the retested ceiling."
                )
        elif (
            state is AccumulationLifecycleState.HOLDING_AS_SUPPORT
            and candle.close < candidate.lower_price
        ):
            next_state = AccumulationLifecycleState.INVALIDATED
            explanation = (
                "Price closed below the former accumulation floor."
            )

        if next_state is None:
            continue
        event_id = (
            f"{timeframe.value}:accumulation.{digest}.{next_state.value}"
        )
        lifecycle.append(
            AccumulationLifecycleEvent(
                state=next_state,
                observed_at=candle.timestamp,
                available_at=candle.timestamp,
                evidence_ids=(event_id,),
                explanation=explanation,
            )
        )
        state = next_state
        if state in {
            AccumulationLifecycleState.FAILED_BREAKOUT,
            AccumulationLifecycleState.INVALIDATED,
            AccumulationLifecycleState.EXPIRED,
        }:
            break

    sweeps = _liquidity_sweeps(
        series,
        timeframe,
        candles[candidate.start_index:],
        candidate.lower_price,
        candidate.upper_price,
        window[-1].timestamp,
        config,
    )
    evidence_ids = tuple(
        evidence_id
        for event in lifecycle
        for evidence_id in event.evidence_ids
    ) + tuple(
        evidence_id
        for sweep in sweeps
        for evidence_id in sweep.evidence_ids
    )
    return AccumulationZone(
        zone_id=f"{timeframe.value}:accumulation:{digest}",
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        timeframe=timeframe,
        interval=series.interval,
        source=series.source,
        source_retrieved_at=series.retrieved_at,
        evaluated_at=evaluated_at,
        base_started_at=window[0].timestamp,
        base_last_observed_at=window[-1].timestamp,
        lower_price=candidate.lower_price,
        upper_price=candidate.upper_price,
        metrics=metrics,
        evidence_ids=evidence_ids,
        lifecycle=tuple(lifecycle),
        liquidity_sweeps=sweeps,
    )


def _liquidity_sweeps(
    series: HistoricalCandleSeries,
    timeframe: SwingAnalysisTimeframe,
    candles: list[Candle],
    lower_price: float,
    upper_price: float,
    zone_available_at: datetime,
    config: AccumulationDetectionConfig,
) -> tuple[LiquiditySweep, ...]:
    sweeps: list[LiquiditySweep] = []
    occupied: set[tuple[LiquidityPoolSide, int]] = set()
    for index, candle in enumerate(candles):
        for side, reference, extreme in (
            (LiquidityPoolSide.SELL_SIDE, lower_price, candle.low),
            (LiquidityPoolSide.BUY_SIDE, upper_price, candle.high),
        ):
            penetration = abs(extreme - reference) / reference * 100
            breached = (
                extreme < reference
                if side is LiquidityPoolSide.SELL_SIDE
                else extreme > reference
            )
            if (
                not breached
                or penetration < config.minimum_sweep_percentage
                or penetration > config.maximum_sweep_percentage
            ):
                continue
            reclaim_index = _reclaim_index(
                candles,
                index,
                side,
                reference,
                config.maximum_reclaim_candles,
            )
            if reclaim_index is None or (side, reclaim_index) in occupied:
                continue
            reclaim = candles[reclaim_index]
            segment = candles[index:reclaim_index + 1]
            resolved_extreme = (
                min(item.low for item in segment)
                if side is LiquidityPoolSide.SELL_SIDE
                else max(item.high for item in segment)
            )
            resolved_penetration = (
                abs(resolved_extreme - reference) / reference * 100
            )
            if resolved_penetration > config.maximum_sweep_percentage:
                continue
            digest = _sweep_digest(
                series,
                timeframe,
                side,
                reference,
                candle.timestamp,
                reclaim.timestamp,
            )
            evidence_id = (
                f"{timeframe.value}:liquidity_sweep.{digest}"
            )
            sweeps.append(
                LiquiditySweep(
                    sweep_id=(
                        f"{timeframe.value}:liquidity_sweep:{digest}"
                    ),
                    exchange=series.exchange,
                    symbol_token=series.symbol_token,
                    symbol=series.symbol,
                    timeframe=timeframe,
                    interval=series.interval,
                    source=series.source,
                    liquidity_side=side,
                    reference_price=reference,
                    extreme_price=resolved_extreme,
                    reclaim_close_price=reclaim.close,
                    swept_at=candle.timestamp,
                    reclaimed_at=reclaim.timestamp,
                    available_at=max(reclaim.timestamp, zone_available_at),
                    volume_multiple=_volume_multiple(
                        candles,
                        reclaim_index,
                        config.volume_baseline_candles,
                    ),
                    evidence_ids=(evidence_id,),
                    explanation=(
                        f"Price swept {side.value.replace('_', ' ')} "
                        "and closed back across the reference level."
                    ),
                )
            )
            occupied.add((side, reclaim_index))
    return tuple(
        sorted(
            sweeps,
            key=lambda item: (
                item.available_at,
                item.liquidity_side.value,
                item.sweep_id,
            ),
        )
    )


def _reclaim_index(
    candles: list[Candle],
    sweep_index: int,
    side: LiquidityPoolSide,
    reference: float,
    maximum_reclaim_candles: int,
) -> int | None:
    final_index = min(
        len(candles) - 1,
        sweep_index + maximum_reclaim_candles - 1,
    )
    for index in range(sweep_index, final_index + 1):
        close = candles[index].close
        reclaimed = (
            close >= reference
            if side is LiquidityPoolSide.SELL_SIDE
            else close <= reference
        )
        if reclaimed:
            return index
    return None


def _analysis(
    series: HistoricalCandleSeries,
    timeframe: SwingAnalysisTimeframe,
    evaluated_at: datetime,
    zones: tuple[AccumulationZone, ...],
) -> TimeframeAccumulationAnalysis:
    return TimeframeAccumulationAnalysis(
        analysis_id=f"{timeframe.value}:accumulation_analysis",
        exchange=series.exchange,
        symbol_token=series.symbol_token,
        symbol=series.symbol,
        timeframe=timeframe,
        interval=series.interval,
        source=series.source,
        source_retrieved_at=series.retrieved_at,
        evaluated_at=evaluated_at,
        zones=zones,
    )


def _timeframe(interval: str) -> SwingAnalysisTimeframe:
    if interval == "ONE_DAY":
        return SwingAnalysisTimeframe.DAILY
    if interval == "ONE_WEEK":
        return SwingAnalysisTimeframe.WEEKLY
    raise ValueError(
        "accumulation detection supports ONE_DAY or ONE_WEEK series"
    )


def _validate_evaluation_time(
    evaluated_at: datetime,
    retrieved_at: datetime,
) -> None:
    if not isinstance(evaluated_at, datetime):
        raise ValueError("accumulation evaluation time must be a datetime")
    if evaluated_at.tzinfo is None or evaluated_at.utcoffset() is None:
        raise ValueError("accumulation evaluation time must include timezone")
    if evaluated_at > retrieved_at:
        raise ValueError(
            "accumulation evaluation cannot follow source retrieval"
        )


def _validate_candle_order(candles: list[Candle]) -> None:
    for previous, current in zip(candles, candles[1:]):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "accumulation candles must be unique and chronological"
            )


def _validate_positive_prices(candles: list[Candle]) -> None:
    for candle in candles:
        if min(candle.open, candle.high, candle.low, candle.close) <= 0:
            raise ValueError(
                "accumulation detection requires positive OHLC prices"
            )


def _normalized_slope(values: np.ndarray) -> float:
    mean = float(np.mean(values))
    if mean <= 0:
        return 0.0
    return _linear_slope(values) / mean * 100


def _linear_slope(values: np.ndarray) -> float:
    if values.size < 2:
        return 0.0
    return float(np.polyfit(np.arange(values.size), values, 1)[0])


def _bullish_volume_share(candles: list[Candle]) -> float:
    bullish = 0
    bearish = 0
    for previous, current in zip(candles, candles[1:]):
        if current.close > previous.close:
            bullish += current.volume
        elif current.close < previous.close:
            bearish += current.volume
    directional = bullish + bearish
    if directional == 0:
        return 50.0
    return bullish / directional * 100


def _down_volume_contraction(candles: list[Candle]) -> float:
    midpoint = len(candles) // 2
    first = _declining_volumes(candles[:midpoint + 1])
    second = _declining_volumes(candles[midpoint:])
    if not first or not second:
        return 0.0
    first_mean = float(np.mean(first))
    second_mean = float(np.mean(second))
    if first_mean <= 0:
        return 0.0
    return max(0.0, min(100.0, (first_mean - second_mean) / first_mean * 100))


def _declining_volumes(candles: list[Candle]) -> list[int]:
    return [
        current.volume
        for previous, current in zip(candles, candles[1:])
        if current.close < previous.close
    ]


def _atr_compression(
    candles: list[Candle],
    start_index: int,
    window: list[Candle],
    atr_by_time: dict[datetime, float],
) -> float:
    recent_count = max(2, len(window) // 3)
    recent = [
        atr_by_time[candle.timestamp]
        for candle in window[-recent_count:]
        if candle.timestamp in atr_by_time
    ]
    prior_start = max(0, start_index - recent_count)
    prior = [
        atr_by_time[candle.timestamp]
        for candle in candles[prior_start:start_index]
        if candle.timestamp in atr_by_time
    ]
    if not prior:
        prior = [
            atr_by_time[candle.timestamp]
            for candle in window[:recent_count]
            if candle.timestamp in atr_by_time
        ]
    if not prior or not recent:
        return 0.0
    prior_mean = float(np.mean(prior))
    recent_mean = float(np.mean(recent))
    if prior_mean <= 0:
        return 0.0
    return max(0.0, min(100.0, (prior_mean - recent_mean) / prior_mean * 100))


def _confidence_score(
    *,
    width_percentage: float,
    containment: float,
    lower_touches: int,
    upper_touches: int,
    lower_rejections: int,
    slope: float,
    atr_compression: float,
    bullish_volume_share: float,
    down_volume_contraction: float,
    obv_slope: float,
    profile: AccumulationTimeframeProfile,
) -> float:
    containment_score = min(
        1.0,
        containment / max(profile.minimum_close_containment_percentage, 1.0),
    )
    width_score = max(
        0.0,
        1 - width_percentage / profile.maximum_range_width_percentage,
    )
    touch_score = min(
        1.0,
        min(lower_touches, upper_touches)
        / profile.minimum_boundary_touches,
    )
    if profile.maximum_absolute_slope_percentage == 0:
        slope_score = float(slope == 0)
    else:
        slope_score = max(
            0.0,
            1 - abs(slope) / profile.maximum_absolute_slope_percentage,
        )
    score = (
        containment_score * 15
        + width_score * 15
        + touch_score * 15
        + slope_score * 10
        + min(1.0, atr_compression / 20.0) * 10
        + max(0.0, min(1.0, (bullish_volume_share - 45.0) / 15.0)) * 10
        + min(1.0, down_volume_contraction / 20.0) * 10
        + (10.0 if obv_slope > 0 else 0.0)
        + min(1.0, lower_rejections / 2.0) * 5
    )
    return max(0.0, min(100.0, score))


def _volume_multiple(
    candles: list[Candle],
    index: int,
    baseline_candles: int,
) -> float:
    start = max(0, index - baseline_candles)
    baseline = [candle.volume for candle in candles[start:index]]
    if not baseline:
        return 1.0
    mean = float(np.mean(baseline))
    if mean <= 0:
        return 1.0
    return candles[index].volume / mean


def _retests_ceiling(
    candle: Candle,
    ceiling: float,
    config: AccumulationDetectionConfig,
) -> bool:
    tolerance = ceiling * config.retest_tolerance_percentage / 100
    return (
        candle.low <= ceiling + tolerance
        and candle.high >= ceiling - tolerance
    )


def _zone_digest(
    series: HistoricalCandleSeries,
    timeframe: SwingAnalysisTimeframe,
    window: list[Candle],
    candidate: _Candidate,
) -> str:
    payload = "|".join(
        (
            series.exchange,
            series.symbol_token,
            series.symbol,
            timeframe.value,
            window[0].timestamp.isoformat(),
            window[-1].timestamp.isoformat(),
            f"{candidate.lower_price:.8f}",
            f"{candidate.upper_price:.8f}",
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _sweep_digest(
    series: HistoricalCandleSeries,
    timeframe: SwingAnalysisTimeframe,
    side: LiquidityPoolSide,
    reference: float,
    swept_at: datetime,
    reclaimed_at: datetime,
) -> str:
    payload = "|".join(
        (
            series.exchange,
            series.symbol_token,
            series.symbol,
            timeframe.value,
            side.value,
            f"{reference:.8f}",
            swept_at.isoformat(),
            reclaimed_at.isoformat(),
        )
    )
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def _finite(value: float, *, label: str) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
    ):
        raise ValueError(f"{label} must be a finite number")
    return float(value)
