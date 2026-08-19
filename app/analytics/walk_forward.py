from hashlib import sha256
from math import isclose

from app.agents.historical_execution_agent import (
    HistoricalExecutionAgent,
    HistoricalExecutionAgentConfig,
)
from app.exceptions import TechnicalAnalysisError
from app.models.agentic import (
    AgenticHistoricalExecutionResult,
    TradePlanningDisposition,
)
from app.models.backtest import (
    BacktestStrategyConfiguration,
    BacktestSegmentPerformance,
    BacktestEquityPoint,
    WalkForwardBacktestConfig,
    WalkForwardBacktestResult,
    WalkForwardEvaluationOutcome,
    WalkForwardEvaluationRecord,
    WalkForwardPerformance,
    WalkForwardTradeRecord,
    build_backtest_strategy_configuration,
)
from app.models.execution import (
    ExecutionSimulationConfig,
    HistoricalExecutionOutcome,
)
from app.models.market import HistoricalCandleSeries
from app.models.trade_setup import TradeDirection
from app.orchestration.agent_orchestrator import (
    AgentOrchestrator,
    JarvisHistoricalExecutionJudge,
)


class WalkForwardBacktestEngine:
    """Run the Jarvis analysis chain over successive candle prefixes."""

    engine_id = "jarvis.walk_forward_engine.v1"

    def __init__(
        self,
        orchestrator: AgentOrchestrator | None = None,
        config: WalkForwardBacktestConfig | None = None,
    ) -> None:
        self.orchestrator = orchestrator or AgentOrchestrator()
        _validate_orchestrator(self.orchestrator)
        if config is not None and not isinstance(
            config,
            WalkForwardBacktestConfig,
        ):
            raise ValueError(
                "walk-forward config must be validated configuration"
            )
        self.config = WalkForwardBacktestConfig.model_validate(
            (config or WalkForwardBacktestConfig()).model_dump()
        )
        self.strategy_configuration = _build_strategy_configuration(
            self.orchestrator,
            self.config,
        )

    @property
    def configuration_fingerprint(self) -> str:
        serialized = self.config.model_dump_json()
        return sha256(serialized.encode("utf-8")).hexdigest()

    def run(
        self,
        market_series: HistoricalCandleSeries,
    ) -> WalkForwardBacktestResult:
        series = _prepare_market_series(market_series, self.config)
        warmup = _resolved_warmup(
            self.orchestrator,
            self.config.warmup_candles,
        )
        scheduled = _scheduled_indices(series, self.config, warmup)
        evaluations = []
        trades = []
        current_capital = self.config.execution.initial_capital
        active_trade: WalkForwardTradeRecord | None = None

        for index in scheduled:
            candle = series.candles[index]
            if active_trade is not None:
                if (
                    active_trade.exit_index is None
                    or index < active_trade.exit_index
                ):
                    evaluations.append(
                        _position_open_record(
                            index,
                            candle,
                            current_capital,
                            active_trade,
                        )
                    )
                    continue
                current_capital = active_trade.capital_after
                active_trade = None

            if current_capital <= 0:
                evaluations.append(
                    WalkForwardEvaluationRecord(
                        candle_index=index,
                        candle=candle,
                        outcome=(
                            WalkForwardEvaluationOutcome.CAPITAL_DEPLETED
                        ),
                        capital_before=current_capital,
                        capital_after=current_capital,
                        message=(
                            "No analysis was run because portfolio capital "
                            "was depleted."
                        ),
                    )
                )
                continue

            prefix = series.model_copy(
                update={"candles": series.candles[:index + 1]}
            )
            try:
                technical = self.orchestrator.run_swing_analysis(prefix)
            except TechnicalAnalysisError as error:
                evaluations.append(
                    _technical_failure_record(
                        index,
                        candle,
                        current_capital,
                        error,
                    )
                )
                continue

            technical_fields = _technical_fields(technical)
            if not technical.decision.accepted:
                evaluations.append(
                    WalkForwardEvaluationRecord(
                        candle_index=index,
                        candle=candle,
                        outcome=(
                            WalkForwardEvaluationOutcome.TECHNICAL_REJECTED
                        ),
                        capital_before=current_capital,
                        capital_after=current_capital,
                        message="Jarvis rejected the technical submission.",
                        **technical_fields,
                    )
                )
                continue

            try:
                planning = self.orchestrator.run_trade_planning(
                    technical,
                    prefix,
                )
            except TechnicalAnalysisError as error:
                evaluations.append(
                    WalkForwardEvaluationRecord(
                        candle_index=index,
                        candle=candle,
                        outcome=(
                            WalkForwardEvaluationOutcome.PLANNING_REJECTED
                        ),
                        capital_before=current_capital,
                        capital_after=current_capital,
                        message=str(error) or type(error).__name__,
                        **technical_fields,
                    )
                )
                continue

            planning_fields = {
                **technical_fields,
                **_planning_fields(planning),
            }
            if not planning.decision.accepted:
                evaluations.append(
                    WalkForwardEvaluationRecord(
                        candle_index=index,
                        candle=candle,
                        outcome=(
                            WalkForwardEvaluationOutcome.PLANNING_REJECTED
                        ),
                        capital_before=current_capital,
                        capital_after=current_capital,
                        message="Jarvis rejected the trade-plan submission.",
                        **planning_fields,
                    )
                )
                continue

            execution = _run_execution(
                planning,
                series,
                self.config.execution,
                current_capital,
            )
            execution_fields = {
                **planning_fields,
                **_execution_fields(execution),
            }
            if not execution.decision.accepted:
                evaluations.append(
                    WalkForwardEvaluationRecord(
                        candle_index=index,
                        candle=candle,
                        outcome=(
                            WalkForwardEvaluationOutcome.EXECUTION_REJECTED
                        ),
                        capital_before=current_capital,
                        capital_after=current_capital,
                        message="Jarvis rejected historical execution.",
                        **execution_fields,
                    )
                )
                continue

            simulated = execution.submission.execution
            if simulated.quantity == 0:
                outcome = (
                    WalkForwardEvaluationOutcome.NO_TRADE
                    if simulated.outcome
                    is HistoricalExecutionOutcome.NO_TRADE_INTENT
                    else WalkForwardEvaluationOutcome.EXECUTION_NOT_ENTERED
                )
                evaluations.append(
                    WalkForwardEvaluationRecord(
                        candle_index=index,
                        candle=candle,
                        outcome=outcome,
                        capital_before=current_capital,
                        capital_after=current_capital,
                        message=simulated.rationale,
                        **execution_fields,
                    )
                )
                continue

            trade = _trade_record(
                index,
                series,
                planning,
                execution,
                current_capital,
            )
            trades.append(trade)
            active_trade = trade
            evaluations.append(
                WalkForwardEvaluationRecord(
                    candle_index=index,
                    candle=candle,
                    outcome=(
                        WalkForwardEvaluationOutcome.TRADE_OPEN
                        if trade.exit_index is None
                        else WalkForwardEvaluationOutcome.TRADE_CLOSED
                    ),
                    capital_before=current_capital,
                    capital_after=trade.capital_after,
                    active_trade_id=trade.trade_id,
                    message=simulated.rationale,
                    **execution_fields,
                )
            )

        equity_curve = _build_equity_curve(
            series,
            trades,
            self.config.execution.initial_capital,
        )
        performance = _performance(
            evaluations,
            trades,
            equity_curve,
            self.config.execution.initial_capital,
            len(series.candles),
            series.interval,
        )
        return WalkForwardBacktestResult(
            backtest_id=self.config.backtest_id,
            engine_id=self.engine_id,
            configuration_fingerprint=self.configuration_fingerprint,
            market_series=series,
            config=self.config,
            strategy_configuration=self.strategy_configuration,
            resolved_warmup_candles=warmup,
            evaluations=evaluations,
            trades=trades,
            equity_curve=equity_curve,
            performance=performance,
        )


def run_walk_forward_backtest(
    market_series: HistoricalCandleSeries,
    *,
    orchestrator: AgentOrchestrator | None = None,
    config: WalkForwardBacktestConfig | None = None,
) -> WalkForwardBacktestResult:
    return WalkForwardBacktestEngine(orchestrator, config).run(
        market_series
    )


def _validate_orchestrator(orchestrator) -> None:
    for method in ("run_swing_analysis", "run_trade_planning"):
        if not callable(getattr(orchestrator, method, None)):
            raise ValueError(
                f"walk-forward orchestrator must provide {method}()"
            )
    if not hasattr(orchestrator, "technical_agent"):
        raise ValueError(
            "walk-forward orchestrator must expose its technical agent"
        )


def _build_strategy_configuration(
    orchestrator,
    config: WalkForwardBacktestConfig,
) -> BacktestStrategyConfiguration | None:
    technical_agent = orchestrator.technical_agent
    technical_config = getattr(technical_agent, "config", None)
    planning_agent = getattr(orchestrator, "trade_planning_agent", None)
    planning_config = getattr(planning_agent, "config", None)
    if (
        technical_config is None
        or not callable(getattr(technical_config, "model_dump", None))
        or planning_agent is None
        or planning_config is None
        or not callable(getattr(planning_config, "model_dump", None))
    ):
        return None
    execution_config = HistoricalExecutionAgentConfig(
        simulation=config.execution
    )
    execution_fingerprint = sha256(
        execution_config.model_dump_json().encode("utf-8")
    ).hexdigest()
    category_weights = {
        category.value: weight
        for category, weight in technical_config.category_weights.items()
    }
    return build_backtest_strategy_configuration(
        technical_agent_id=technical_agent.agent_id,
        technical_evaluator_id=technical_agent.evaluator_id,
        technical_configuration_fingerprint=(
            technical_agent.configuration_fingerprint
        ),
        technical_parameters=technical_config.model_dump(mode="json"),
        trade_planning_agent_id=planning_agent.agent_id,
        trade_planner_id=planning_agent.planner_id,
        trade_planning_configuration_fingerprint=(
            planning_agent.configuration_fingerprint
        ),
        trade_planning_parameters=planning_config.model_dump(mode="json"),
        execution_engine_id=execution_config.execution_engine_id,
        execution_configuration_fingerprint=execution_fingerprint,
        execution_parameters=execution_config.model_dump(mode="json"),
        walk_forward_configuration_fingerprint=sha256(
            config.model_dump_json().encode("utf-8")
        ).hexdigest(),
        walk_forward_parameters=config.model_dump(mode="json"),
        category_weights=category_weights,
    )


def _prepare_market_series(
    market_series: HistoricalCandleSeries,
    config: WalkForwardBacktestConfig,
) -> HistoricalCandleSeries:
    if not isinstance(market_series, HistoricalCandleSeries):
        raise ValueError("walk-forward engine requires candle history")
    series = HistoricalCandleSeries.model_validate(
        market_series.model_dump()
    )
    previous = None
    for candle in series.candles:
        if previous is not None and candle.timestamp <= previous:
            raise ValueError(
                "walk-forward candles must have unique timestamps in "
                "ascending order"
            )
        if candle.timestamp > series.retrieved_at:
            raise ValueError(
                "walk-forward cannot use post-retrieval candles"
            )
        previous = candle.timestamp
    for name, value in (
        ("start", config.start_at),
        ("end", config.end_at),
    ):
        if value is not None and value > series.retrieved_at:
            raise ValueError(
                f"walk-forward {name} cannot follow source retrieval"
            )
    if config.end_at is None:
        return series
    return series.model_copy(
        update={
            "candles": [
                candle
                for candle in series.candles
                if candle.timestamp <= config.end_at
            ]
        }
    )


def _resolved_warmup(orchestrator, configured: int | None) -> int:
    if configured is not None:
        return configured
    minimum = getattr(orchestrator.technical_agent, "minimum_candles", None)
    if (
        isinstance(minimum, bool)
        or not isinstance(minimum, int)
        or minimum < 1
    ):
        raise ValueError(
            "walk-forward technical agent must expose minimum_candles"
        )
    return minimum - 1


def _scheduled_indices(
    series: HistoricalCandleSeries,
    config: WalkForwardBacktestConfig,
    warmup: int,
) -> list[int]:
    eligible = [
        index
        for index, candle in enumerate(series.candles)
        if index >= warmup
        and (
            config.start_at is None
            or candle.timestamp >= config.start_at
        )
    ]
    return eligible[::config.evaluation_stride]


def _technical_fields(result) -> dict:
    return {
        "technical_submission_id": result.submission.submission_id,
        "technical_decision_id": result.decision.decision_id,
        "technical_configuration_fingerprint": (
            result.submission.configuration_fingerprint
        ),
        "technical_profile": result.submission.profile,
        "signal_direction": result.submission.profile.direction,
        "signal_stance": result.submission.profile.stance,
        "signal_score": result.submission.profile.score,
    }


def _planning_fields(result) -> dict:
    return {
        "planning_submission_id": result.submission.submission_id,
        "planning_decision_id": result.decision.decision_id,
        "planning_configuration_fingerprint": (
            result.submission.configuration_fingerprint
        ),
        "planning_disposition": result.submission.disposition,
        "planning_reason": result.submission.reason,
    }


def _execution_fields(result) -> dict:
    return {
        "execution_submission_id": result.submission.submission_id,
        "execution_decision_id": result.decision.decision_id,
        "execution_configuration_fingerprint": (
            result.submission.configuration_fingerprint
        ),
        "execution_outcome": result.submission.execution.outcome,
    }


def _technical_failure_record(
    index,
    candle,
    capital,
    error,
) -> WalkForwardEvaluationRecord:
    return WalkForwardEvaluationRecord(
        candle_index=index,
        candle=candle,
        outcome=WalkForwardEvaluationOutcome.TECHNICAL_FAILURE,
        capital_before=capital,
        capital_after=capital,
        message=str(error) or type(error).__name__,
    )


def _position_open_record(
    index,
    candle,
    capital,
    trade,
) -> WalkForwardEvaluationRecord:
    return WalkForwardEvaluationRecord(
        candle_index=index,
        candle=candle,
        outcome=WalkForwardEvaluationOutcome.POSITION_ALREADY_OPEN,
        capital_before=capital,
        capital_after=capital,
        active_trade_id=trade.trade_id,
        message=(
            "The scheduled evaluation was skipped because the strategy "
            "already had an open position."
        ),
    )


def _run_execution(
    planning,
    series,
    base_config,
    capital,
) -> AgenticHistoricalExecutionResult:
    simulation_values = base_config.model_dump()
    simulation_values["initial_capital"] = capital
    simulation = ExecutionSimulationConfig.model_validate(
        simulation_values
    )
    agent = HistoricalExecutionAgent(
        HistoricalExecutionAgentConfig(simulation=simulation)
    )
    judge = JarvisHistoricalExecutionJudge(
        expected_agent_id=agent.agent_id,
        expected_config=agent.config,
    )
    submission = agent.execute(planning, series)
    decision = judge.review(submission, planning, series)
    return AgenticHistoricalExecutionResult(
        orchestrator_id="jarvis.agent_orchestrator.v1",
        submission=submission,
        decision=decision,
    )


def _trade_record(
    evaluation_index,
    series,
    planning,
    agentic_execution,
    capital,
) -> WalkForwardTradeRecord:
    submission = agentic_execution.submission
    execution = submission.execution
    entry_index = series.candles.index(execution.entry_candle)
    exit_index = (
        series.candles.index(execution.exit_candle)
        if execution.exit_candle is not None
        else None
    )
    return WalkForwardTradeRecord(
        trade_id=submission.submission_id,
        evaluation_index=evaluation_index,
        planning_submission_id=planning.submission.submission_id,
        execution_submission_id=submission.submission_id,
        direction=execution.direction,
        signal_stance=planning.submission.profile.stance,
        signal_score=planning.submission.profile.score,
        outcome=execution.outcome,
        selected_target=execution.selected_target,
        entry_index=entry_index,
        entry_at=execution.entry_candle.timestamp,
        entry_price=execution.entry_fill_price,
        stop_loss_price=execution.stop_loss_price,
        target_price=execution.target_price,
        quantity=execution.quantity,
        exit_index=exit_index,
        exit_at=(
            execution.exit_candle.timestamp
            if execution.exit_candle is not None
            else None
        ),
        exit_price=execution.exit_fill_price,
        capital_before=capital,
        capital_after=execution.ending_capital,
        gross_pnl=execution.gross_pnl,
        entry_fee=execution.entry_fee,
        exit_fee=execution.exit_fee,
        total_costs=execution.total_costs,
        net_pnl=execution.net_pnl,
        realized_r_multiple=execution.realized_r_multiple,
        bars_held=execution.bars_held,
    )


def _build_equity_curve(
    series,
    trades,
    initial_capital,
) -> list[BacktestEquityPoint]:
    points = []
    capital = initial_capital
    peak = initial_capital
    trade_position = 0
    for index, candle in enumerate(series.candles):
        while trade_position < len(trades):
            trade = trades[trade_position]
            if trade.exit_index is not None and trade.exit_index < index:
                capital = trade.capital_after
                trade_position += 1
                continue
            break

        active = (
            trades[trade_position]
            if trade_position < len(trades)
            else None
        )
        if active is None or index < active.entry_index:
            equity = capital
            active_trade_id = None
        elif active.exit_index is not None and index == active.exit_index:
            equity = active.capital_after
            active_trade_id = active.trade_id
        else:
            direction_sign = (
                1 if active.direction is TradeDirection.LONG else -1
            )
            unrealized = (
                (candle.close - active.entry_price)
                * active.quantity
                * direction_sign
            )
            equity = active.capital_before - active.entry_fee + unrealized
            active_trade_id = active.trade_id

        peak = max(peak, equity)
        drawdown = max(0.0, peak - equity)
        drawdown_percentage = (
            drawdown / peak * 100 if peak > 0 else 0.0
        )
        points.append(
            BacktestEquityPoint(
                candle_index=index,
                timestamp=candle.timestamp,
                close=candle.close,
                equity=equity,
                running_peak=peak,
                drawdown_amount=drawdown,
                drawdown_percentage=drawdown_percentage,
                active_trade_id=active_trade_id,
            )
        )
    return points


def _performance(
    evaluations,
    trades,
    equity_curve,
    initial_capital,
    candle_count,
    interval,
) -> WalkForwardPerformance:
    closed = [trade for trade in trades if trade.exit_index is not None]
    open_trades = [trade for trade in trades if trade.exit_index is None]
    wins = [trade for trade in closed if trade.net_pnl > 1e-12]
    losses = [trade for trade in closed if trade.net_pnl < -1e-12]
    breakeven = [
        trade for trade in closed
        if isclose(trade.net_pnl, 0.0, abs_tol=1e-12)
    ]
    gross_profit = sum(trade.net_pnl for trade in wins)
    gross_loss = abs(sum(trade.net_pnl for trade in losses))
    final_equity = (
        equity_curve[-1].equity if equity_curve else initial_capital
    )
    exposed_indices = set()
    for trade in trades:
        final_index = (
            trade.exit_index
            if trade.exit_index is not None
            else candle_count - 1
        )
        exposed_indices.update(range(trade.entry_index, final_index + 1))
    realized_r = [
        trade.realized_r_multiple
        for trade in closed
        if trade.realized_r_multiple is not None
    ]
    return WalkForwardPerformance(
        interval=interval,
        initial_capital=initial_capital,
        final_equity=final_equity,
        net_profit=final_equity - initial_capital,
        total_return_percentage=(
            (final_equity - initial_capital) / initial_capital * 100
        ),
        attempted_evaluations=sum(
            item.outcome
            not in (
                WalkForwardEvaluationOutcome.POSITION_ALREADY_OPEN,
                WalkForwardEvaluationOutcome.CAPITAL_DEPLETED,
            )
            for item in evaluations
        ),
        technical_failures=sum(
            item.outcome
            is WalkForwardEvaluationOutcome.TECHNICAL_FAILURE
            for item in evaluations
        ),
        no_trade_evaluations=sum(
            item.outcome
            in (
                WalkForwardEvaluationOutcome.NO_TRADE,
                WalkForwardEvaluationOutcome.EXECUTION_NOT_ENTERED,
            )
            for item in evaluations
        ),
        skipped_open_position_evaluations=sum(
            item.outcome
            is WalkForwardEvaluationOutcome.POSITION_ALREADY_OPEN
            for item in evaluations
        ),
        entered_trades=len(trades),
        closed_trades=len(closed),
        open_trades=len(open_trades),
        winning_trades=len(wins),
        losing_trades=len(losses),
        breakeven_trades=len(breakeven),
        win_rate_percentage=(
            len(wins) / len(closed) * 100 if closed else 0.0
        ),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=(
            gross_profit / gross_loss if gross_loss > 0 else None
        ),
        expectancy_per_closed_trade=(
            sum(trade.net_pnl for trade in closed) / len(closed)
            if closed
            else None
        ),
        average_win=(
            gross_profit / len(wins) if wins else None
        ),
        average_loss=(
            -gross_loss / len(losses) if losses else None
        ),
        payoff_ratio=(
            (gross_profit / len(wins)) / (gross_loss / len(losses))
            if wins and losses
            else None
        ),
        total_costs=sum(trade.total_costs for trade in trades),
        average_realized_r=(
            sum(realized_r) / len(realized_r) if realized_r else None
        ),
        maximum_drawdown_amount=max(
            (point.drawdown_amount for point in equity_curve),
            default=0.0,
        ),
        maximum_drawdown_percentage=max(
            (point.drawdown_percentage for point in equity_curve),
            default=0.0,
        ),
        average_bars_held=(
            sum(trade.bars_held for trade in trades) / len(trades)
            if trades
            else None
        ),
        exposure_percentage=(
            len(exposed_indices) / candle_count * 100
            if candle_count
            else 0.0
        ),
        maximum_consecutive_losses=_maximum_consecutive_losses(closed),
        direction_breakdown={
            direction: _segment_performance(
                [trade for trade in trades if trade.direction is direction]
            )
            for direction in TradeDirection
        },
        stance_breakdown={
            stance: _segment_performance(
                [
                    trade
                    for trade in trades
                    if trade.signal_stance is stance
                ]
            )
            for stance in sorted(
                {trade.signal_stance for trade in trades},
                key=lambda value: value.value,
            )
        },
    )


def _maximum_consecutive_losses(trades) -> int:
    maximum = 0
    current = 0
    for trade in trades:
        if trade.net_pnl < 0:
            current += 1
            maximum = max(maximum, current)
        else:
            current = 0
    return maximum


def _segment_performance(trades) -> BacktestSegmentPerformance:
    closed = [trade for trade in trades if trade.exit_index is not None]
    wins = [trade for trade in closed if trade.net_pnl > 1e-12]
    losses = [trade for trade in closed if trade.net_pnl < -1e-12]
    breakeven = [
        trade
        for trade in closed
        if isclose(trade.net_pnl, 0.0, abs_tol=1e-12)
    ]
    realized_r = [
        trade.realized_r_multiple
        for trade in closed
        if trade.realized_r_multiple is not None
    ]
    return BacktestSegmentPerformance(
        entered_trades=len(trades),
        closed_trades=len(closed),
        winning_trades=len(wins),
        losing_trades=len(losses),
        breakeven_trades=len(breakeven),
        win_rate_percentage=(
            len(wins) / len(closed) * 100 if closed else 0.0
        ),
        net_pnl=sum(trade.net_pnl for trade in closed),
        total_costs=sum(trade.total_costs for trade in trades),
        average_realized_r=(
            sum(realized_r) / len(realized_r) if realized_r else None
        ),
    )
