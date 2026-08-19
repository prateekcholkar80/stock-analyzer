from math import floor

from app.models.execution import (
    ExecutionSimulationConfig,
    ExecutionTargetPolicy,
    HistoricalExecutionOutcome,
    HistoricalTradeExecution,
    SameCandleExitPolicy,
    SelectedExecutionTarget,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.trade_setup import (
    SwingTradePlan,
    TradeDirection,
    TradeTargetFeasibility,
)


def simulate_historical_trade(
    plan: SwingTradePlan | None,
    market_series: HistoricalCandleSeries,
    *,
    planned_at,
    config: ExecutionSimulationConfig | None = None,
) -> HistoricalTradeExecution:
    """Simulate one intent without using data available at planning time."""
    settings = config or ExecutionSimulationConfig()
    if not isinstance(settings, ExecutionSimulationConfig):
        raise ValueError(
            "historical execution requires validated simulation config"
        )
    settings = ExecutionSimulationConfig.model_validate(
        settings.model_dump()
    )
    _validate_market_series(market_series)
    budgets = _budgets(settings)

    if plan is None:
        return HistoricalTradeExecution(
            market_series=market_series,
            config=settings,
            planned_at=planned_at,
            outcome=HistoricalExecutionOutcome.NO_TRADE_INTENT,
            risk_budget=budgets[0],
            allocation_budget=budgets[1],
            ending_capital=settings.initial_capital,
            rationale=(
                "No historical execution occurred because Jarvis did "
                "not approve an actionable trade intent."
            ),
        )

    _validate_plan_market(plan, market_series, planned_at)
    target = _select_target(plan, settings.target_policy)
    plan_fields = _plan_fields(plan, target)
    if target[2] is TradeTargetFeasibility.BLOCKED_BY_STRUCTURE:
        return HistoricalTradeExecution(
            market_series=market_series,
            config=settings,
            planned_at=planned_at,
            outcome=HistoricalExecutionOutcome.TARGET_BLOCKED,
            risk_budget=budgets[0],
            allocation_budget=budgets[1],
            ending_capital=settings.initial_capital,
            rationale=(
                "No historical execution occurred because the explicitly "
                "selected target was blocked by confirmed structure."
            ),
            **plan_fields,
        )

    future = [
        (index, candle)
        for index, candle in enumerate(market_series.candles)
        if candle.timestamp > planned_at
    ]
    if not future:
        return HistoricalTradeExecution(
            market_series=market_series,
            config=settings,
            planned_at=planned_at,
            outcome=HistoricalExecutionOutcome.NO_FUTURE_CANDLE,
            risk_budget=budgets[0],
            allocation_budget=budgets[1],
            ending_capital=settings.initial_capital,
            rationale=(
                "No entry was simulated because no candle exists after "
                "the planning timestamp."
            ),
            **plan_fields,
        )

    entry_index, entry_candle = future[0]
    if entry_candle.open <= 0:
        raise ValueError("historical execution requires positive open prices")
    direction = plan.evaluation.direction
    entry_price = _fill_with_slippage(
        entry_candle.open,
        direction,
        is_entry=True,
        basis_points=settings.slippage_basis_points,
    )
    stop = plan.evaluation.stop_loss_price
    target_price = target[1]
    if _entry_is_invalid(direction, entry_price, stop, target_price):
        return HistoricalTradeExecution(
            market_series=market_series,
            config=settings,
            planned_at=planned_at,
            outcome=HistoricalExecutionOutcome.INVALIDATED_AT_ENTRY,
            risk_budget=budgets[0],
            allocation_budget=budgets[1],
            entry_candle=entry_candle,
            entry_order_price=entry_price,
            ending_capital=settings.initial_capital,
            rationale=(
                "The next eligible open was already beyond the planned "
                "stop or target, so the stale intent was not entered."
            ),
            **plan_fields,
        )

    anticipated_stop_fill = _fill_with_slippage(
        stop,
        direction,
        is_entry=False,
        basis_points=settings.slippage_basis_points,
    )
    risk_per_unit = abs(entry_price - anticipated_stop_fill)
    quantity = _position_size(
        entry_price,
        risk_per_unit,
        settings,
        risk_budget=budgets[0],
        allocation_budget=budgets[1],
    )
    if quantity < 1:
        return HistoricalTradeExecution(
            market_series=market_series,
            config=settings,
            planned_at=planned_at,
            outcome=HistoricalExecutionOutcome.INSUFFICIENT_CAPITAL,
            risk_budget=budgets[0],
            allocation_budget=budgets[1],
            entry_candle=entry_candle,
            entry_order_price=entry_price,
            ending_capital=settings.initial_capital,
            rationale=(
                "Risk and allocation limits could not fund one whole "
                "unit at the next eligible open."
            ),
            **plan_fields,
        )

    entry_fee = _order_fee(entry_price * quantity, settings)
    position_fields = {
        **plan_fields,
        "entry_candle": entry_candle,
        "entry_order_price": entry_price,
        "entry_fill_price": entry_price,
        "risk_per_unit": risk_per_unit,
        "quantity": quantity,
        "position_notional": entry_price * quantity,
        "entry_fee": entry_fee,
    }

    for index, candle in future:
        exit_event = _exit_on_candle(
            candle,
            direction,
            stop,
            target_price,
            target[0],
            settings,
            check_open=index != entry_index,
        )
        if exit_event is None:
            continue
        outcome, raw_exit_price = exit_event
        exit_price = _fill_with_slippage(
            raw_exit_price,
            direction,
            is_entry=False,
            basis_points=settings.slippage_basis_points,
        )
        return _closed_execution(
            market_series,
            settings,
            planned_at,
            outcome,
            candle,
            exit_price,
            entry_index,
            index,
            risk_budget=budgets[0],
            allocation_budget=budgets[1],
            **position_fields,
        )

    last_index, last_candle = future[-1]
    if settings.close_open_position_at_end:
        if last_candle.close <= 0:
            raise ValueError(
                "end-of-data execution requires a positive closing price"
            )
        exit_price = _fill_with_slippage(
            last_candle.close,
            direction,
            is_entry=False,
            basis_points=settings.slippage_basis_points,
        )
        return _closed_execution(
            market_series,
            settings,
            planned_at,
            HistoricalExecutionOutcome.END_OF_DATA,
            last_candle,
            exit_price,
            entry_index,
            last_index,
            risk_budget=budgets[0],
            allocation_budget=budgets[1],
            **position_fields,
        )

    return HistoricalTradeExecution(
        market_series=market_series,
        config=settings,
        planned_at=planned_at,
        outcome=HistoricalExecutionOutcome.OPEN,
        risk_budget=budgets[0],
        allocation_budget=budgets[1],
        total_costs=entry_fee,
        bars_held=last_index - entry_index + 1,
        rationale=(
            "The position remained open at the end of available history; "
            "no unrealized P&L was presented as realized performance."
        ),
        **position_fields,
    )


def _validate_market_series(series: HistoricalCandleSeries) -> None:
    previous = None
    for candle in series.candles:
        if previous is not None and candle.timestamp <= previous:
            raise ValueError(
                "historical execution candles must be uniquely ordered"
            )
        if candle.timestamp > series.retrieved_at:
            raise ValueError(
                "historical execution cannot use post-retrieval candles"
            )
        previous = candle.timestamp


def _validate_plan_market(
    plan: SwingTradePlan,
    series: HistoricalCandleSeries,
    planned_at,
) -> None:
    snapshot = plan.evaluation.profile.snapshot
    if planned_at != snapshot.evaluated_at:
        raise ValueError(
            "execution planning timestamp must match the approved profile"
        )
    plan_identity = (
        plan.market_series.exchange,
        plan.market_series.symbol_token,
        plan.market_series.symbol,
        plan.market_series.interval,
        plan.market_series.source,
    )
    execution_identity = (
        series.exchange,
        series.symbol_token,
        series.symbol,
        series.interval,
        series.source,
    )
    if execution_identity != plan_identity:
        raise ValueError(
            "execution history must describe the planned instrument"
        )
    prefix = [
        candle for candle in series.candles
        if candle.timestamp <= planned_at
    ]
    if prefix != plan.market_series.candles:
        raise ValueError(
            "execution history must preserve the exact planning prefix"
        )


def _select_target(plan, policy: ExecutionTargetPolicy):
    evaluation = plan.evaluation
    minimum = evaluation.minimum_target
    preferred = evaluation.preferred_target
    if policy is ExecutionTargetPolicy.MINIMUM:
        return (
            SelectedExecutionTarget.MINIMUM,
            minimum.target_price,
            minimum.feasibility,
        )
    if policy is ExecutionTargetPolicy.PREFERRED:
        return (
            SelectedExecutionTarget.PREFERRED,
            preferred.target_price,
            preferred.feasibility,
        )
    if preferred.feasibility is TradeTargetFeasibility.REACHABLE:
        return (
            SelectedExecutionTarget.PREFERRED,
            preferred.target_price,
            preferred.feasibility,
        )
    return (
        SelectedExecutionTarget.MINIMUM,
        minimum.target_price,
        minimum.feasibility,
    )


def _plan_fields(plan, target) -> dict:
    return {
        "direction": plan.evaluation.direction,
        "planned_entry_reference": plan.evaluation.entry_price,
        "stop_loss_price": plan.evaluation.stop_loss_price,
        "selected_target": target[0],
        "target_price": target[1],
    }


def _budgets(config: ExecutionSimulationConfig) -> tuple[float, float]:
    return (
        config.initial_capital
        * config.risk_per_trade_percentage
        / 100,
        config.initial_capital
        * config.maximum_position_percentage
        / 100,
    )


def _fill_with_slippage(
    price: float,
    direction: TradeDirection,
    *,
    is_entry: bool,
    basis_points: float,
) -> float:
    fraction = basis_points / 10_000
    adverse_sign = 1 if (
        (direction is TradeDirection.LONG and is_entry)
        or (direction is TradeDirection.SHORT and not is_entry)
    ) else -1
    fill = float(price) * (1 + adverse_sign * fraction)
    if fill <= 0:
        raise ValueError("execution slippage produced a non-positive fill")
    return fill


def _entry_is_invalid(
    direction: TradeDirection,
    entry: float,
    stop: float,
    target: float,
) -> bool:
    if direction is TradeDirection.LONG:
        return entry <= stop or entry >= target
    return entry >= stop or entry <= target


def _position_size(
    entry_price: float,
    risk_per_unit: float,
    config: ExecutionSimulationConfig,
    *,
    risk_budget: float,
    allocation_budget: float,
) -> int:
    if risk_per_unit <= 0:
        return 0
    risk_limited = floor(risk_budget / risk_per_unit)
    commission_fraction = config.commission_basis_points / 10_000
    available = max(0.0, allocation_budget - config.fixed_fee_per_order)
    allocation_limited = floor(
        available / (entry_price * (1 + commission_fraction))
    )
    return max(0, min(risk_limited, allocation_limited))


def _exit_on_candle(
    candle: Candle,
    direction: TradeDirection,
    stop: float,
    target: float,
    selected_target: SelectedExecutionTarget,
    config: ExecutionSimulationConfig,
    *,
    check_open: bool,
) -> tuple[HistoricalExecutionOutcome, float] | None:
    target_outcome = (
        HistoricalExecutionOutcome.MINIMUM_TARGET
        if selected_target is SelectedExecutionTarget.MINIMUM
        else HistoricalExecutionOutcome.PREFERRED_TARGET
    )

    if check_open:
        if direction is TradeDirection.LONG:
            if candle.open <= stop:
                return HistoricalExecutionOutcome.STOP_LOSS, candle.open
            if candle.open >= target:
                return target_outcome, target
        else:
            if candle.open >= stop:
                return HistoricalExecutionOutcome.STOP_LOSS, candle.open
            if candle.open <= target:
                return target_outcome, target

    stop_touched = (
        candle.low <= stop
        if direction is TradeDirection.LONG
        else candle.high >= stop
    )
    target_touched = (
        candle.high >= target
        if direction is TradeDirection.LONG
        else candle.low <= target
    )
    if stop_touched and target_touched:
        if config.same_candle_exit_policy is SameCandleExitPolicy.STOP_FIRST:
            return HistoricalExecutionOutcome.STOP_LOSS, stop
        return target_outcome, target
    if stop_touched:
        return HistoricalExecutionOutcome.STOP_LOSS, stop
    if target_touched:
        return target_outcome, target
    return None


def _order_fee(
    notional: float,
    config: ExecutionSimulationConfig,
) -> float:
    return (
        notional * config.commission_basis_points / 10_000
        + config.fixed_fee_per_order
    )


def _closed_execution(
    market_series: HistoricalCandleSeries,
    config: ExecutionSimulationConfig,
    planned_at,
    outcome: HistoricalExecutionOutcome,
    exit_candle: Candle,
    exit_price: float,
    entry_index: int,
    exit_index: int,
    *,
    risk_budget: float,
    allocation_budget: float,
    direction: TradeDirection,
    entry_fill_price: float,
    risk_per_unit: float,
    quantity: int,
    entry_fee: float,
    **position_fields,
) -> HistoricalTradeExecution:
    exit_fee = _order_fee(exit_price * quantity, config)
    direction_sign = 1 if direction is TradeDirection.LONG else -1
    gross_pnl = (
        (exit_price - entry_fill_price) * quantity * direction_sign
    )
    total_costs = entry_fee + exit_fee
    net_pnl = gross_pnl - total_costs
    initial_risk = risk_per_unit * quantity
    realized_r = net_pnl / initial_risk
    return HistoricalTradeExecution(
        market_series=market_series,
        config=config,
        planned_at=planned_at,
        outcome=outcome,
        direction=direction,
        entry_fill_price=entry_fill_price,
        risk_per_unit=risk_per_unit,
        quantity=quantity,
        entry_fee=entry_fee,
        risk_budget=risk_budget,
        allocation_budget=allocation_budget,
        exit_candle=exit_candle,
        exit_fill_price=exit_price,
        exit_fee=exit_fee,
        gross_pnl=gross_pnl,
        total_costs=total_costs,
        net_pnl=net_pnl,
        ending_capital=config.initial_capital + net_pnl,
        realized_r_multiple=realized_r,
        bars_held=exit_index - entry_index + 1,
        rationale=_execution_rationale(outcome),
        **position_fields,
    )


def _execution_rationale(outcome: HistoricalExecutionOutcome) -> str:
    messages = {
        HistoricalExecutionOutcome.STOP_LOSS: (
            "The position exited at the stop under conservative gap and "
            "same-candle ordering rules."
        ),
        HistoricalExecutionOutcome.MINIMUM_TARGET: (
            "The position exited when the feasible minimum target was "
            "reached."
        ),
        HistoricalExecutionOutcome.PREFERRED_TARGET: (
            "The position exited when the feasible preferred target was "
            "reached."
        ),
        HistoricalExecutionOutcome.END_OF_DATA: (
            "The position was closed at the last available candle because "
            "end-of-data liquidation was enabled."
        ),
    }
    return messages[outcome]
