from datetime import datetime
from math import isfinite

from app.analytics.support_resistance_lifecycle import (
    track_support_resistance_lifecycle,
)
from app.exceptions import InsufficientDataError
from app.models.market import Candle, HistoricalCandleSeries
from app.models.price_action import (
    PriceZoneLifecycleStatus,
    PriceZoneType,
    SupportResistanceDetectionResult,
    SupportResistanceLifecycle,
    SupportResistanceLifecycleResult,
)
from app.models.signals import (
    SignalDirection,
    SwingTradingSignalProfile,
)
from app.models.trade_setup import (
    StopLossMethod,
    StructuralBarrier,
    SwingTradePlan,
    SwingTradeSetupEvaluation,
    TradeDirection,
    TradeEntryMethod,
    TradeProfitTarget,
    TradeSetupStatus,
    TradeTargetFeasibility,
)


DEFAULT_MINIMUM_REWARD_TO_RISK = 2.0
DEFAULT_PREFERRED_REWARD_TO_RISK = 3.0
DEFAULT_STRUCTURAL_BUFFER_ATR_MULTIPLIER = 0.25
DEFAULT_MINIMUM_BUFFER_PERCENTAGE = 0.1
DEFAULT_FALLBACK_STOP_ATR_MULTIPLIER = 2.0
ATR_SIGNAL_SOURCE = "volatility_signals.atr_regime_and_risk_distance"


def build_swing_trade_plan(
    profile: SwingTradingSignalProfile,
    market_series: HistoricalCandleSeries,
    zone_lifecycles: SupportResistanceLifecycleResult,
    *,
    structural_buffer_atr_multiplier: float = (
        DEFAULT_STRUCTURAL_BUFFER_ATR_MULTIPLIER
    ),
    minimum_buffer_percentage: float = (
        DEFAULT_MINIMUM_BUFFER_PERCENTAGE
    ),
    fallback_stop_atr_multiplier: float = (
        DEFAULT_FALLBACK_STOP_ATR_MULTIPLIER
    ),
    minimum_reward_to_risk: float = DEFAULT_MINIMUM_REWARD_TO_RISK,
    preferred_reward_to_risk: float = (
        DEFAULT_PREFERRED_REWARD_TO_RISK
    ),
) -> SwingTradePlan:
    """Select a close entry and evidence-grounded invalidation stop."""
    structural_atr_multiplier = _validate_non_negative_number(
        "structural ATR-buffer multiplier",
        structural_buffer_atr_multiplier,
    )
    minimum_buffer = _validate_percentage(
        "minimum stop-buffer percentage",
        minimum_buffer_percentage,
    )
    fallback_atr_multiplier = _validate_positive_number(
        "fallback stop ATR multiplier",
        fallback_stop_atr_multiplier,
    )

    _validate_source_identity(profile, zone_lifecycles)
    _validate_market_source(profile, market_series)
    _validate_candle_order(market_series)
    _validate_lifecycle_evidence(market_series, zone_lifecycles)
    direction = _direction_from_profile(profile)
    entry_candle = _latest_completed_candle(
        market_series,
        profile.snapshot.evaluated_at,
    )
    entry_price = float(entry_candle.close)
    if entry_price <= 0:
        raise ValueError(
            "automatic trade entry requires a positive closing price"
        )

    atr_evidence_id, atr_value = _extract_atr(profile)
    protective_lifecycle = _find_nearest_protective_lifecycle(
        zone_lifecycles,
        direction,
        entry_price,
    )
    if protective_lifecycle is not None:
        structural_boundary = (
            protective_lifecycle.zone.lower_price
            if direction is TradeDirection.LONG
            else protective_lifecycle.zone.upper_price
        )
        stop_buffer = max(
            structural_boundary * minimum_buffer / 100,
            (atr_value or 0) * structural_atr_multiplier,
        )
        stop_method = StopLossMethod.STRUCTURAL_INVALIDATION
        stop_base = structural_boundary
    else:
        if atr_value is None or atr_value <= 0:
            raise InsufficientDataError(
                "automatic stop selection requires either a confirmed "
                "protective zone or positive ATR evidence"
            )
        structural_boundary = None
        stop_buffer = max(
            entry_price * minimum_buffer / 100,
            atr_value * fallback_atr_multiplier,
        )
        stop_method = StopLossMethod.ATR_FALLBACK
        stop_base = entry_price

    stop_loss_price = (
        stop_base - stop_buffer
        if direction is TradeDirection.LONG
        else stop_base + stop_buffer
    )
    if stop_loss_price <= 0:
        raise ValueError(
            "automatic stop selection produced a non-positive price"
        )

    evaluation = evaluate_swing_trade_setup(
        profile,
        zone_lifecycles,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        minimum_reward_to_risk=minimum_reward_to_risk,
        preferred_reward_to_risk=preferred_reward_to_risk,
    )
    return SwingTradePlan(
        market_series=market_series,
        evaluation=evaluation,
        entry_method=TradeEntryMethod.LATEST_COMPLETED_CLOSE,
        entry_candle=entry_candle,
        stop_loss_method=stop_method,
        protective_lifecycle=protective_lifecycle,
        structural_invalidation_price=structural_boundary,
        stop_buffer=stop_buffer,
        atr_value=atr_value,
        atr_evidence_id=atr_evidence_id,
        structural_buffer_atr_multiplier=structural_atr_multiplier,
        minimum_buffer_percentage=minimum_buffer,
        fallback_stop_atr_multiplier=fallback_atr_multiplier,
        rationale=_build_plan_rationale(
            direction,
            entry_candle,
            stop_method,
            stop_loss_price,
            protective_lifecycle,
            atr_value,
            evaluation.status,
        ),
    )


def evaluate_swing_trade_setup(
    profile: SwingTradingSignalProfile,
    zone_lifecycles: SupportResistanceLifecycleResult,
    *,
    entry_price: float,
    stop_loss_price: float,
    minimum_reward_to_risk: float = DEFAULT_MINIMUM_REWARD_TO_RISK,
    preferred_reward_to_risk: float = DEFAULT_PREFERRED_REWARD_TO_RISK,
) -> SwingTradeSetupEvaluation:
    """Evaluate 2R-to-3R feasibility against confirmed price zones."""
    entry = _validate_positive_price("entry price", entry_price)
    stop = _validate_positive_price("stop-loss price", stop_loss_price)
    minimum = _validate_reward_multiple(
        "minimum reward-to-risk",
        minimum_reward_to_risk,
    )
    preferred = _validate_reward_multiple(
        "preferred reward-to-risk",
        preferred_reward_to_risk,
    )
    if preferred <= minimum:
        raise ValueError(
            "preferred reward-to-risk must exceed the minimum"
        )

    direction = _direction_from_profile(profile)
    if direction is TradeDirection.LONG and stop >= entry:
        raise ValueError("long stop loss must be below the entry price")
    if direction is TradeDirection.SHORT and stop <= entry:
        raise ValueError("short stop loss must be above the entry price")

    risk = abs(entry - stop)
    preferred_price = _target_price(
        direction,
        entry,
        risk,
        preferred,
    )
    if preferred_price < 0:
        raise ValueError(
            "preferred short target cannot fall below zero"
        )

    _validate_source_identity(profile, zone_lifecycles)
    barrier = _find_nearest_structural_barrier(
        zone_lifecycles,
        direction,
        entry,
        risk,
    )
    structural_limit = (
        barrier.reward_to_risk if barrier is not None else None
    )

    minimum_target = _build_target(
        direction,
        entry,
        risk,
        minimum,
        structural_limit,
    )
    preferred_target = _build_target(
        direction,
        entry,
        risk,
        preferred,
        structural_limit,
    )
    status = _classify_status(minimum_target, preferred_target)

    return SwingTradeSetupEvaluation(
        profile=profile,
        zone_lifecycles=zone_lifecycles,
        direction=direction,
        status=status,
        entry_price=entry,
        stop_loss_price=stop,
        risk_per_unit=risk,
        minimum_reward_to_risk=minimum,
        preferred_reward_to_risk=preferred,
        minimum_target=minimum_target,
        preferred_target=preferred_target,
        nearest_structural_barrier=barrier,
        maximum_structural_reward_to_risk=structural_limit,
        rationale=_build_rationale(
            direction,
            status,
            minimum_target,
            preferred_target,
            barrier,
        ),
    )


def _direction_from_profile(
    profile: SwingTradingSignalProfile,
) -> TradeDirection:
    if profile.direction is SignalDirection.BULLISH:
        return TradeDirection.LONG
    if profile.direction is SignalDirection.BEARISH:
        return TradeDirection.SHORT
    raise ValueError(
        "risk-reward evaluation requires a directional swing profile"
    )


def _validate_source_identity(
    profile: SwingTradingSignalProfile,
    lifecycles: SupportResistanceLifecycleResult,
) -> None:
    snapshot = profile.snapshot
    if (
        snapshot.exchange,
        snapshot.symbol_token,
        snapshot.symbol,
        snapshot.interval,
        snapshot.source,
        snapshot.source_retrieved_at,
    ) != (
        lifecycles.exchange,
        lifecycles.symbol_token,
        lifecycles.symbol,
        lifecycles.interval,
        lifecycles.source,
        lifecycles.source_retrieved_at,
    ):
        raise ValueError(
            "swing profile and zone lifecycles must describe the same "
            "source"
        )
    if snapshot.evaluated_at != lifecycles.evaluated_at:
        raise ValueError(
            "swing profile and zone lifecycles must share an evaluation "
            "time"
        )


def _find_nearest_structural_barrier(
    lifecycles: SupportResistanceLifecycleResult,
    direction: TradeDirection,
    entry_price: float,
    risk_per_unit: float,
) -> StructuralBarrier | None:
    candidates = []
    for lifecycle in lifecycles.lifecycles:
        effective_type = _effective_zone_type(lifecycle)
        if effective_type is None:
            continue

        zone = lifecycle.zone
        if direction is TradeDirection.LONG:
            if effective_type is not PriceZoneType.RESISTANCE:
                continue
            if zone.upper_price < entry_price:
                continue
            boundary = zone.lower_price
            distance = max(0.0, boundary - entry_price)
        else:
            if effective_type is not PriceZoneType.SUPPORT:
                continue
            if zone.lower_price > entry_price:
                continue
            boundary = zone.upper_price
            distance = max(0.0, entry_price - boundary)

        candidates.append(
            StructuralBarrier(
                lifecycle=lifecycle,
                effective_zone_type=effective_type,
                boundary_price=boundary,
                distance_from_entry=distance,
                reward_to_risk=distance / risk_per_unit,
            )
        )

    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            item.distance_from_entry,
            item.boundary_price,
            item.lifecycle.zone.confirmed_at,
        ),
    )


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


def _build_target(
    direction: TradeDirection,
    entry_price: float,
    risk_per_unit: float,
    reward_to_risk: float,
    structural_limit: float | None,
) -> TradeProfitTarget:
    reward = risk_per_unit * reward_to_risk
    target_price = _target_price(
        direction,
        entry_price,
        risk_per_unit,
        reward_to_risk,
    )
    feasibility = (
        TradeTargetFeasibility.REACHABLE
        if structural_limit is None or reward_to_risk < structural_limit
        else TradeTargetFeasibility.BLOCKED_BY_STRUCTURE
    )
    return TradeProfitTarget(
        reward_to_risk=reward_to_risk,
        target_price=target_price,
        reward_per_unit=reward,
        feasibility=feasibility,
    )


def _target_price(
    direction: TradeDirection,
    entry_price: float,
    risk_per_unit: float,
    reward_to_risk: float,
) -> float:
    reward = risk_per_unit * reward_to_risk
    if direction is TradeDirection.LONG:
        return entry_price + reward
    return entry_price - reward


def _classify_status(
    minimum_target: TradeProfitTarget,
    preferred_target: TradeProfitTarget,
) -> TradeSetupStatus:
    if (
        minimum_target.feasibility
        is TradeTargetFeasibility.BLOCKED_BY_STRUCTURE
    ):
        return TradeSetupStatus.REJECTED
    if (
        preferred_target.feasibility
        is TradeTargetFeasibility.BLOCKED_BY_STRUCTURE
    ):
        return TradeSetupStatus.MARGINAL
    return TradeSetupStatus.VALID


def _build_rationale(
    direction: TradeDirection,
    status: TradeSetupStatus,
    minimum_target: TradeProfitTarget,
    preferred_target: TradeProfitTarget,
    barrier: StructuralBarrier | None,
) -> str:
    if barrier is None:
        return (
            f"{direction.value.title()} setup is valid: no confirmed "
            "structural barrier blocks the "
            f"{minimum_target.reward_to_risk:g}R "
            f"or {preferred_target.reward_to_risk:g}R target. This is a "
            "technical feasibility assessment, not a guaranteed outcome."
        )

    zone = barrier.lifecycle.zone
    zone_description = (
        f"{barrier.effective_zone_type.value} zone "
        f"{zone.lower_price:g}-{zone.upper_price:g} "
        f"({barrier.lifecycle.status.value}) at "
        f"{barrier.reward_to_risk:.2f}R"
    )
    if status is TradeSetupStatus.REJECTED:
        conclusion = (
            f"the minimum {minimum_target.reward_to_risk:g}R target is "
            "blocked"
        )
    elif status is TradeSetupStatus.MARGINAL:
        conclusion = (
            f"the {minimum_target.reward_to_risk:g}R target is reachable "
            f"but the preferred {preferred_target.reward_to_risk:g}R "
            "target is blocked"
        )
    else:
        conclusion = (
            f"both {minimum_target.reward_to_risk:g}R and "
            f"{preferred_target.reward_to_risk:g}R targets remain before "
            "the barrier"
        )
    return (
        f"{direction.value.title()} setup is {status.value}: {conclusion}; "
        f"nearest confirmed barrier is {zone_description}. This is a "
        "technical feasibility assessment, not a guaranteed outcome."
    )


def _validate_market_source(
    profile: SwingTradingSignalProfile,
    market_series: HistoricalCandleSeries,
) -> None:
    snapshot = profile.snapshot
    if (
        snapshot.exchange,
        snapshot.symbol_token,
        snapshot.symbol,
        snapshot.interval,
        snapshot.source,
        snapshot.source_retrieved_at,
    ) != (
        market_series.exchange,
        market_series.symbol_token,
        market_series.symbol,
        market_series.interval,
        market_series.source,
        market_series.retrieved_at,
    ):
        raise ValueError(
            "swing profile and market series must describe the same source"
        )


def _validate_candle_order(market_series: HistoricalCandleSeries) -> None:
    for previous, current in zip(
        market_series.candles,
        market_series.candles[1:],
    ):
        if current.timestamp <= previous.timestamp:
            raise ValueError(
                "historical candles must have unique timestamps in "
                "ascending order"
            )


def _validate_lifecycle_evidence(
    market_series: HistoricalCandleSeries,
    lifecycles: SupportResistanceLifecycleResult,
) -> None:
    zone_result = SupportResistanceDetectionResult(
        exchange=lifecycles.exchange,
        symbol_token=lifecycles.symbol_token,
        symbol=lifecycles.symbol,
        interval=lifecycles.interval,
        source=lifecycles.source,
        source_retrieved_at=lifecycles.source_retrieved_at,
        evaluated_at=lifecycles.zone_detection_evaluated_at,
        pivot_left_strength=lifecycles.pivot_left_strength,
        pivot_right_strength=lifecycles.pivot_right_strength,
        tolerance_percentage=lifecycles.tolerance_percentage,
        minimum_touches=lifecycles.minimum_touches,
        zones=[item.zone for item in lifecycles.lifecycles],
    )
    recomputed = track_support_resistance_lifecycle(
        market_series,
        zone_result,
        as_of=lifecycles.evaluated_at,
    )
    if recomputed != lifecycles:
        raise ValueError(
            "zone lifecycles do not match the supplied market history"
        )


def _latest_completed_candle(
    market_series: HistoricalCandleSeries,
    evaluated_at: datetime,
) -> Candle:
    available = [
        candle
        for candle in market_series.candles
        if candle.timestamp <= evaluated_at
    ]
    if not available:
        raise InsufficientDataError(
            "no completed candle is available at the evaluation time"
        )
    return available[-1]


def _extract_atr(
    profile: SwingTradingSignalProfile,
) -> tuple[str | None, float | None]:
    matches = [
        evidence
        for evidence in profile.snapshot.evidence
        if evidence.source == ATR_SIGNAL_SOURCE
    ]
    if len(matches) > 1:
        raise ValueError(
            "automatic stop selection requires one ATR evidence item"
        )
    if not matches:
        return None, None

    evidence = matches[0]
    value = evidence.observed_values.get("atr")
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
        or float(value) < 0
    ):
        raise ValueError(
            "automatic stop selection requires valid ATR evidence"
        )
    return evidence.evidence_id, float(value)


def _find_nearest_protective_lifecycle(
    lifecycles: SupportResistanceLifecycleResult,
    direction: TradeDirection,
    entry_price: float,
) -> SupportResistanceLifecycle | None:
    protective_type = (
        PriceZoneType.SUPPORT
        if direction is TradeDirection.LONG
        else PriceZoneType.RESISTANCE
    )
    candidates = []
    for lifecycle in lifecycles.lifecycles:
        if _effective_zone_type(lifecycle) is not protective_type:
            continue
        zone = lifecycle.zone
        if direction is TradeDirection.LONG:
            if zone.lower_price > entry_price:
                continue
            distance = max(0.0, entry_price - zone.upper_price)
            boundary = zone.lower_price
        else:
            if zone.upper_price < entry_price:
                continue
            distance = max(0.0, zone.lower_price - entry_price)
            boundary = zone.upper_price
        candidates.append((distance, boundary, lifecycle))

    if not candidates:
        return None
    return min(
        candidates,
        key=lambda item: (
            item[0],
            abs(entry_price - item[1]),
            item[2].zone.confirmed_at,
        ),
    )[2]


def _build_plan_rationale(
    direction: TradeDirection,
    entry_candle: Candle,
    stop_method: StopLossMethod,
    stop_loss_price: float,
    protective_lifecycle: SupportResistanceLifecycle | None,
    atr_value: float | None,
    status: TradeSetupStatus,
) -> str:
    if stop_method is StopLossMethod.STRUCTURAL_INVALIDATION:
        zone = protective_lifecycle.zone
        stop_basis = (
            f"nearest confirmed protective zone "
            f"{zone.lower_price:g}-{zone.upper_price:g}"
        )
        if atr_value is not None:
            stop_basis += f" with ATR {atr_value:g} buffer context"
    else:
        stop_basis = (
            f"ATR {atr_value:g} fallback because no confirmed "
            "protective zone was available"
        )

    return (
        f"{direction.value.title()} entry uses the latest completed close "
        f"of {entry_candle.close:g} at {entry_candle.timestamp.isoformat()}; "
        f"stop {stop_loss_price:g} uses {stop_basis}. The resulting "
        f"2R-to-3R feasibility is {status.value}; levels are deterministic "
        "technical references, not guaranteed executions or outcomes."
    )


def _validate_positive_price(name: str, value: float) -> float:
    normalized = _validate_number(name, value)
    if normalized <= 0:
        raise ValueError(f"{name} must be above zero")
    return normalized


def _validate_reward_multiple(name: str, value: float) -> float:
    normalized = _validate_number(name, value)
    if normalized <= 0:
        raise ValueError(f"{name} must be above zero")
    return normalized


def _validate_positive_number(name: str, value: float) -> float:
    normalized = _validate_number(name, value)
    if normalized <= 0:
        raise ValueError(f"{name} must be above zero")
    return normalized


def _validate_non_negative_number(name: str, value: float) -> float:
    normalized = _validate_number(name, value)
    if normalized < 0:
        raise ValueError(f"{name} cannot be negative")
    return normalized


def _validate_percentage(name: str, value: float) -> float:
    normalized = _validate_positive_number(name, value)
    if normalized > 100:
        raise ValueError(f"{name} cannot exceed 100")
    return normalized


def _validate_number(name: str, value: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not isfinite(float(value))
    ):
        raise ValueError(f"{name} must be a finite number")
    return float(value)
