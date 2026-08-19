from datetime import datetime
from enum import StrEnum
from hashlib import sha256
from math import isclose, isfinite
from typing import Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.agentic import (
    TradePlanningDisposition,
    TradePlanningReason,
)
from app.models.execution import (
    ExecutionSimulationConfig,
    HistoricalExecutionOutcome,
    SelectedExecutionTarget,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import SignalDirection, SwingTradingStance
from app.models.technical import TechnicalModel
from app.models.trade_setup import TradeDirection


class WalkForwardEvaluationOutcome(StrEnum):
    TECHNICAL_FAILURE = "technical_failure"
    TECHNICAL_REJECTED = "technical_rejected"
    PLANNING_REJECTED = "planning_rejected"
    EXECUTION_REJECTED = "execution_rejected"
    NO_TRADE = "no_trade"
    EXECUTION_NOT_ENTERED = "execution_not_entered"
    TRADE_CLOSED = "trade_closed"
    TRADE_OPEN = "trade_open"
    POSITION_ALREADY_OPEN = "position_already_open"
    CAPITAL_DEPLETED = "capital_depleted"


class WalkForwardBacktestConfig(TechnicalModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    backtest_id: str = Field(
        default="jarvis.walk_forward.v1",
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    warmup_candles: int | None = Field(default=None, ge=0)
    evaluation_stride: int = Field(default=1, ge=1)
    start_at: datetime | None = None
    end_at: datetime | None = None
    execution: ExecutionSimulationConfig = Field(
        default_factory=ExecutionSimulationConfig
    )

    @field_validator("warmup_candles", "evaluation_stride", mode="before")
    @classmethod
    def require_integer_settings(cls, value: int | None) -> int | None:
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("walk-forward candle settings must be integers")
        return value

    @field_validator("start_at", "end_at")
    @classmethod
    def require_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError(
                "walk-forward bounds must include timezone information"
            )
        return value

    @model_validator(mode="after")
    def validate_bounds(self) -> Self:
        if (
            self.start_at is not None
            and self.end_at is not None
            and self.start_at > self.end_at
        ):
            raise ValueError(
                "walk-forward start cannot follow its end"
            )
        return self


class WalkForwardEvaluationRecord(TechnicalModel):
    candle_index: int = Field(ge=0)
    candle: Candle
    outcome: WalkForwardEvaluationOutcome
    capital_before: float
    capital_after: float | None = None
    technical_submission_id: str | None = Field(default=None, min_length=1)
    technical_decision_id: str | None = Field(default=None, min_length=1)
    signal_direction: SignalDirection | None = None
    signal_stance: SwingTradingStance | None = None
    signal_score: float | None = None
    planning_submission_id: str | None = Field(default=None, min_length=1)
    planning_decision_id: str | None = Field(default=None, min_length=1)
    planning_disposition: TradePlanningDisposition | None = None
    planning_reason: TradePlanningReason | None = None
    execution_submission_id: str | None = Field(default=None, min_length=1)
    execution_decision_id: str | None = Field(default=None, min_length=1)
    execution_outcome: HistoricalExecutionOutcome | None = None
    active_trade_id: str | None = Field(default=None, min_length=1)
    message: str = Field(min_length=1)

    @field_validator("candle_index", mode="before")
    @classmethod
    def require_integer_index(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("walk-forward candle index must be an integer")
        return value

    @field_validator(
        "capital_before",
        "capital_after",
        "signal_score",
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
            raise ValueError("walk-forward record metrics must be finite")
        return float(value)

    @field_validator("message")
    @classmethod
    def reject_blank_message(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("walk-forward record message cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_record(self) -> Self:
        if self.outcome is WalkForwardEvaluationOutcome.POSITION_ALREADY_OPEN:
            if self.active_trade_id is None:
                raise ValueError(
                    "open-position skip must reference its active trade"
                )
            if self.technical_submission_id is not None:
                raise ValueError(
                    "open-position skip cannot contain new analysis"
                )
        if self.outcome is WalkForwardEvaluationOutcome.CAPITAL_DEPLETED:
            if self.capital_before > 0:
                raise ValueError(
                    "capital-depleted record requires non-positive capital"
                )
        if self.planning_submission_id is not None and (
            self.technical_submission_id is None
        ):
            raise ValueError(
                "trade-planning record requires technical analysis"
            )
        if self.execution_submission_id is not None and (
            self.planning_submission_id is None
        ):
            raise ValueError(
                "execution record requires a trade-planning submission"
            )
        return self


class WalkForwardTradeRecord(TechnicalModel):
    trade_id: str = Field(min_length=1)
    evaluation_index: int = Field(ge=0)
    planning_submission_id: str = Field(min_length=1)
    execution_submission_id: str = Field(min_length=1)
    direction: TradeDirection
    signal_stance: SwingTradingStance
    signal_score: float
    outcome: HistoricalExecutionOutcome
    selected_target: SelectedExecutionTarget
    entry_index: int = Field(ge=0)
    entry_at: datetime
    entry_price: float = Field(gt=0)
    stop_loss_price: float = Field(gt=0)
    target_price: float = Field(gt=0)
    quantity: int = Field(gt=0)
    exit_index: int | None = Field(default=None, ge=0)
    exit_at: datetime | None = None
    exit_price: float | None = Field(default=None, gt=0)
    capital_before: float = Field(gt=0)
    capital_after: float | None = None
    gross_pnl: float | None = None
    entry_fee: float = Field(ge=0)
    exit_fee: float = Field(ge=0)
    total_costs: float = Field(ge=0)
    net_pnl: float | None = None
    realized_r_multiple: float | None = None
    bars_held: int = Field(ge=1)

    @field_validator(
        "evaluation_index",
        "entry_index",
        "exit_index",
        "quantity",
        "bars_held",
        mode="before",
    )
    @classmethod
    def require_integer_metric(cls, value: int | None) -> int | None:
        if value is None:
            return value
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("walk-forward trade counts must be integers")
        return value

    @field_validator("entry_at", "exit_at")
    @classmethod
    def require_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError(
                "walk-forward trade times must include timezone information"
            )
        return value

    @field_validator(
        "entry_price",
        "stop_loss_price",
        "target_price",
        "exit_price",
        "capital_before",
        "capital_after",
        "gross_pnl",
        "entry_fee",
        "exit_fee",
        "total_costs",
        "net_pnl",
        "realized_r_multiple",
        "signal_score",
        mode="before",
    )
    @classmethod
    def require_finite_trade_metric(
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
            raise ValueError("walk-forward trade metrics must be finite")
        return float(value)

    @model_validator(mode="after")
    def validate_trade(self) -> Self:
        if self.entry_index <= self.evaluation_index:
            raise ValueError(
                "walk-forward entry must follow its evaluation candle"
            )
        is_open = self.outcome is HistoricalExecutionOutcome.OPEN
        exit_values = (
            self.exit_index,
            self.exit_at,
            self.exit_price,
            self.capital_after,
            self.gross_pnl,
            self.net_pnl,
            self.realized_r_multiple,
        )
        if is_open:
            if any(value is not None for value in exit_values):
                raise ValueError(
                    "open walk-forward trade cannot contain an exit"
                )
        else:
            if any(value is None for value in exit_values):
                raise ValueError(
                    "closed walk-forward trade requires complete exit data"
                )
            if self.exit_index < self.entry_index:
                raise ValueError(
                    "walk-forward exit cannot precede entry"
                )
            if self.exit_at < self.entry_at:
                raise ValueError(
                    "walk-forward exit time cannot precede entry"
                )
            if not isclose(
                self.capital_after,
                self.capital_before + self.net_pnl,
                abs_tol=1e-9,
            ):
                raise ValueError(
                    "walk-forward capital must include trade net P&L"
                )
        if not isclose(
            self.total_costs,
            self.entry_fee + self.exit_fee,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "walk-forward costs must equal entry and exit fees"
            )
        return self


class BacktestSegmentPerformance(TechnicalModel):
    entered_trades: int = Field(ge=0)
    closed_trades: int = Field(ge=0)
    winning_trades: int = Field(ge=0)
    losing_trades: int = Field(ge=0)
    breakeven_trades: int = Field(ge=0)
    win_rate_percentage: float = Field(ge=0, le=100)
    net_pnl: float
    total_costs: float = Field(ge=0)
    average_realized_r: float | None = None

    @field_validator(
        "entered_trades",
        "closed_trades",
        "winning_trades",
        "losing_trades",
        "breakeven_trades",
        mode="before",
    )
    @classmethod
    def require_integer_counts(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("segment performance counts must be integers")
        return value

    @field_validator(
        "win_rate_percentage",
        "net_pnl",
        "total_costs",
        "average_realized_r",
        mode="before",
    )
    @classmethod
    def require_finite_metrics(
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
            raise ValueError("segment performance metrics must be finite")
        return float(value)

    @model_validator(mode="after")
    def validate_counts(self) -> Self:
        if (
            self.winning_trades
            + self.losing_trades
            + self.breakeven_trades
            != self.closed_trades
        ):
            raise ValueError(
                "segment closed trades must equal outcome counts"
            )
        return self


class BacktestEquityPoint(TechnicalModel):
    candle_index: int = Field(ge=0)
    timestamp: datetime
    close: float = Field(ge=0)
    equity: float
    running_peak: float
    drawdown_amount: float = Field(ge=0)
    drawdown_percentage: float = Field(ge=0)
    active_trade_id: str | None = Field(default=None, min_length=1)

    @field_validator("candle_index", mode="before")
    @classmethod
    def require_integer_index(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("equity candle index must be an integer")
        return value

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("equity timestamp must include timezone")
        return value

    @field_validator(
        "close",
        "equity",
        "running_peak",
        "drawdown_amount",
        "drawdown_percentage",
        mode="before",
    )
    @classmethod
    def require_finite_equity_metric(cls, value: float) -> float:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not isfinite(float(value))
        ):
            raise ValueError("backtest equity metrics must be finite")
        return float(value)

    @model_validator(mode="after")
    def validate_drawdown(self) -> Self:
        expected_amount = max(0.0, self.running_peak - self.equity)
        expected_percentage = (
            expected_amount / self.running_peak * 100
            if self.running_peak > 0
            else 0.0
        )
        if not isclose(
            self.drawdown_amount,
            expected_amount,
            abs_tol=1e-9,
        ) or not isclose(
            self.drawdown_percentage,
            expected_percentage,
            abs_tol=1e-9,
        ):
            raise ValueError("equity drawdown must derive from its peak")
        return self


class WalkForwardPerformance(TechnicalModel):
    interval: str = Field(min_length=1)
    initial_capital: float = Field(gt=0)
    final_equity: float
    net_profit: float
    total_return_percentage: float
    attempted_evaluations: int = Field(ge=0)
    technical_failures: int = Field(ge=0)
    no_trade_evaluations: int = Field(ge=0)
    skipped_open_position_evaluations: int = Field(ge=0)
    entered_trades: int = Field(ge=0)
    closed_trades: int = Field(ge=0)
    open_trades: int = Field(ge=0)
    winning_trades: int = Field(ge=0)
    losing_trades: int = Field(ge=0)
    breakeven_trades: int = Field(ge=0)
    win_rate_percentage: float = Field(ge=0, le=100)
    gross_profit: float = Field(ge=0)
    gross_loss: float = Field(ge=0)
    profit_factor: float | None = Field(default=None, ge=0)
    expectancy_per_closed_trade: float | None = None
    average_win: float | None = Field(default=None, ge=0)
    average_loss: float | None = Field(default=None, le=0)
    payoff_ratio: float | None = Field(default=None, ge=0)
    total_costs: float = Field(ge=0)
    average_realized_r: float | None = None
    maximum_drawdown_amount: float = Field(ge=0)
    maximum_drawdown_percentage: float = Field(ge=0)
    average_bars_held: float | None = Field(default=None, ge=0)
    exposure_percentage: float = Field(ge=0, le=100)
    maximum_consecutive_losses: int = Field(ge=0)
    direction_breakdown: dict[TradeDirection, BacktestSegmentPerformance]
    stance_breakdown: dict[
        SwingTradingStance,
        BacktestSegmentPerformance,
    ]

    @field_validator(
        "attempted_evaluations",
        "technical_failures",
        "no_trade_evaluations",
        "skipped_open_position_evaluations",
        "entered_trades",
        "closed_trades",
        "open_trades",
        "winning_trades",
        "losing_trades",
        "breakeven_trades",
        "maximum_consecutive_losses",
        mode="before",
    )
    @classmethod
    def require_integer_counts(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("backtest performance counts must be integers")
        return value

    @field_validator(
        "initial_capital",
        "final_equity",
        "net_profit",
        "total_return_percentage",
        "win_rate_percentage",
        "gross_profit",
        "gross_loss",
        "profit_factor",
        "expectancy_per_closed_trade",
        "average_win",
        "average_loss",
        "payoff_ratio",
        "total_costs",
        "average_realized_r",
        "maximum_drawdown_amount",
        "maximum_drawdown_percentage",
        "average_bars_held",
        "exposure_percentage",
        mode="before",
    )
    @classmethod
    def require_finite_performance_metric(
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
            raise ValueError("backtest performance metrics must be finite")
        return float(value)

    @model_validator(mode="after")
    def validate_counts_and_return(self) -> Self:
        if self.closed_trades + self.open_trades != self.entered_trades:
            raise ValueError(
                "entered trades must equal closed plus open trades"
            )
        if (
            self.winning_trades
            + self.losing_trades
            + self.breakeven_trades
            != self.closed_trades
        ):
            raise ValueError(
                "closed trades must equal win, loss, and breakeven counts"
            )
        if not isclose(
            self.net_profit,
            self.final_equity - self.initial_capital,
            abs_tol=1e-9,
        ):
            raise ValueError("backtest net profit must derive from equity")
        expected_return = self.net_profit / self.initial_capital * 100
        if not isclose(
            self.total_return_percentage,
            expected_return,
            abs_tol=1e-9,
        ):
            raise ValueError("backtest return must derive from net profit")
        return self


class WalkForwardBacktestResult(TechnicalModel):
    backtest_id: str = Field(min_length=1)
    engine_id: str = Field(
        min_length=1,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    configuration_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    market_series: HistoricalCandleSeries
    config: WalkForwardBacktestConfig
    resolved_warmup_candles: int = Field(ge=0)
    evaluations: list[WalkForwardEvaluationRecord]
    trades: list[WalkForwardTradeRecord]
    equity_curve: list[BacktestEquityPoint]
    performance: WalkForwardPerformance

    @field_validator("resolved_warmup_candles", mode="before")
    @classmethod
    def require_integer_warmup(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("resolved warmup must be an integer")
        return value

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        if self.backtest_id != self.config.backtest_id:
            raise ValueError(
                "walk-forward identifier must match its configuration"
            )
        expected_fingerprint = sha256(
            self.config.model_dump_json().encode("utf-8")
        ).hexdigest()
        if self.configuration_fingerprint != expected_fingerprint:
            raise ValueError(
                "walk-forward configuration fingerprint does not match"
            )
        expected_indices = [
            index
            for index, candle in enumerate(self.market_series.candles)
            if index >= self.resolved_warmup_candles
            and (
                self.config.start_at is None
                or candle.timestamp >= self.config.start_at
            )
            and (
                self.config.end_at is None
                or candle.timestamp <= self.config.end_at
            )
        ][::self.config.evaluation_stride]
        if [item.candle_index for item in self.evaluations] != (
            expected_indices
        ):
            raise ValueError(
                "walk-forward result requires every scheduled evaluation"
            )
        for record in self.evaluations:
            if record.candle != self.market_series.candles[
                record.candle_index
            ]:
                raise ValueError(
                    "walk-forward evaluation candle must match history"
                )
        if len(self.equity_curve) != len(self.market_series.candles):
            raise ValueError(
                "walk-forward equity curve requires every market candle"
            )
        for index, point in enumerate(self.equity_curve):
            candle = self.market_series.candles[index]
            if (
                point.candle_index != index
                or point.timestamp != candle.timestamp
                or not isclose(point.close, candle.close, abs_tol=1e-9)
            ):
                raise ValueError(
                    "walk-forward equity point must match market history"
                )
        for previous, current in zip(self.trades, self.trades[1:]):
            previous_end = previous.exit_index
            if previous_end is None or current.entry_index <= previous_end:
                raise ValueError(
                    "walk-forward trades cannot overlap"
                )
        evaluation_by_index = {
            record.candle_index: record for record in self.evaluations
        }
        for trade in self.trades:
            evaluation = evaluation_by_index.get(trade.evaluation_index)
            if evaluation is None or evaluation.active_trade_id != (
                trade.trade_id
            ):
                raise ValueError(
                    "walk-forward trade must reference its evaluation"
                )
            if (
                self.market_series.candles[trade.entry_index].timestamp
                != trade.entry_at
            ):
                raise ValueError(
                    "walk-forward trade entry must match market history"
                )
            if trade.exit_index is not None and (
                self.market_series.candles[trade.exit_index].timestamp
                != trade.exit_at
            ):
                raise ValueError(
                    "walk-forward trade exit must match market history"
                )
        return self
