from datetime import datetime
from enum import StrEnum
from math import isclose, isfinite
from typing import Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.market import Candle, HistoricalCandleSeries
from app.models.technical import TechnicalModel
from app.models.trade_setup import TradeDirection


class ExecutionTargetPolicy(StrEnum):
    BEST_FEASIBLE = "best_feasible"
    MINIMUM = "minimum"
    PREFERRED = "preferred"


class SelectedExecutionTarget(StrEnum):
    MINIMUM = "minimum"
    PREFERRED = "preferred"


class SameCandleExitPolicy(StrEnum):
    STOP_FIRST = "stop_first"
    TARGET_FIRST = "target_first"


class HistoricalExecutionOutcome(StrEnum):
    NO_TRADE_INTENT = "no_trade_intent"
    TARGET_BLOCKED = "target_blocked"
    NO_FUTURE_CANDLE = "no_future_candle"
    INVALIDATED_AT_ENTRY = "invalidated_at_entry"
    INSUFFICIENT_CAPITAL = "insufficient_capital"
    STOP_LOSS = "stop_loss"
    MINIMUM_TARGET = "minimum_target"
    PREFERRED_TARGET = "preferred_target"
    END_OF_DATA = "end_of_data"
    OPEN = "open"


class ExecutionSimulationConfig(TechnicalModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    initial_capital: float = Field(default=100_000.0, gt=0)
    risk_per_trade_percentage: float = Field(default=1.0, gt=0, le=100)
    maximum_position_percentage: float = Field(
        default=25.0,
        gt=0,
        le=100,
    )
    slippage_basis_points: float = Field(default=5.0, ge=0, lt=10_000)
    commission_basis_points: float = Field(
        default=3.0,
        ge=0,
        lt=10_000,
    )
    fixed_fee_per_order: float = Field(default=0.0, ge=0)
    target_policy: ExecutionTargetPolicy = (
        ExecutionTargetPolicy.BEST_FEASIBLE
    )
    same_candle_exit_policy: SameCandleExitPolicy = (
        SameCandleExitPolicy.STOP_FIRST
    )
    close_open_position_at_end: bool = True


class HistoricalTradeExecution(TechnicalModel):
    market_series: HistoricalCandleSeries
    config: ExecutionSimulationConfig
    planned_at: datetime
    outcome: HistoricalExecutionOutcome
    direction: TradeDirection | None = None
    planned_entry_reference: float | None = Field(default=None, gt=0)
    stop_loss_price: float | None = Field(default=None, gt=0)
    selected_target: SelectedExecutionTarget | None = None
    target_price: float | None = Field(default=None, gt=0)
    risk_budget: float = Field(ge=0)
    allocation_budget: float = Field(ge=0)
    entry_candle: Candle | None = None
    entry_order_price: float | None = Field(default=None, gt=0)
    entry_fill_price: float | None = Field(default=None, gt=0)
    risk_per_unit: float | None = Field(default=None, gt=0)
    quantity: int = Field(default=0, ge=0)
    position_notional: float = Field(default=0.0, ge=0)
    entry_fee: float = Field(default=0.0, ge=0)
    exit_candle: Candle | None = None
    exit_fill_price: float | None = Field(default=None, gt=0)
    exit_fee: float = Field(default=0.0, ge=0)
    gross_pnl: float | None = None
    total_costs: float = Field(default=0.0, ge=0)
    net_pnl: float | None = None
    ending_capital: float | None = None
    realized_r_multiple: float | None = None
    bars_held: int = Field(default=0, ge=0)
    rationale: str = Field(min_length=1)

    @field_validator("planned_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "historical execution time must include timezone "
                "information"
            )
        return value

    @field_validator(
        "planned_entry_reference",
        "stop_loss_price",
        "target_price",
        "risk_budget",
        "allocation_budget",
        "entry_order_price",
        "entry_fill_price",
        "risk_per_unit",
        "position_notional",
        "entry_fee",
        "exit_fill_price",
        "exit_fee",
        "gross_pnl",
        "total_costs",
        "net_pnl",
        "ending_capital",
        "realized_r_multiple",
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
            raise ValueError("historical execution metrics must be finite")
        return float(value)

    @field_validator("quantity", "bars_held", mode="before")
    @classmethod
    def require_integer_metric(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("execution counts must be integers")
        return value

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("execution rationale cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_execution(self) -> Self:
        self._validate_budgets()
        self._validate_market_timing()
        self._validate_plan_fields()
        self._validate_position()
        self._validate_outcome()
        return self

    def _validate_budgets(self) -> None:
        expected_risk = (
            self.config.initial_capital
            * self.config.risk_per_trade_percentage
            / 100
        )
        expected_allocation = (
            self.config.initial_capital
            * self.config.maximum_position_percentage
            / 100
        )
        if not isclose(self.risk_budget, expected_risk, abs_tol=1e-9):
            raise ValueError("execution risk budget does not match config")
        if not isclose(
            self.allocation_budget,
            expected_allocation,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "execution allocation budget does not match config"
            )

    def _validate_market_timing(self) -> None:
        if self.planned_at > self.market_series.retrieved_at:
            raise ValueError(
                "historical execution cannot precede the planning time"
            )
        previous = None
        for candle in self.market_series.candles:
            if previous is not None and candle.timestamp <= previous:
                raise ValueError(
                    "execution candles must have unique timestamps in "
                    "ascending order"
                )
            if candle.timestamp > self.market_series.retrieved_at:
                raise ValueError(
                    "execution cannot use candles after source retrieval"
                )
            previous = candle.timestamp
        if self.entry_candle is not None:
            if self.entry_candle not in self.market_series.candles:
                raise ValueError(
                    "execution entry candle must belong to market series"
                )
            if self.entry_candle.timestamp <= self.planned_at:
                raise ValueError(
                    "execution entry must follow the planning time"
                )
            first_eligible = next(
                (
                    candle
                    for candle in self.market_series.candles
                    if candle.timestamp > self.planned_at
                ),
                None,
            )
            if self.entry_candle != first_eligible:
                raise ValueError(
                    "execution must use the next eligible candle"
                )
            if self.entry_order_price is None:
                raise ValueError(
                    "execution entry candle requires an order price"
                )
        elif self.entry_order_price is not None:
            raise ValueError(
                "execution order price requires an entry candle"
            )
        if self.exit_candle is not None:
            if self.exit_candle not in self.market_series.candles:
                raise ValueError(
                    "execution exit candle must belong to market series"
                )
            if self.entry_candle is None or (
                self.exit_candle.timestamp < self.entry_candle.timestamp
            ):
                raise ValueError("execution exit cannot precede entry")

    def _validate_plan_fields(self) -> None:
        plan_values = (
            self.direction,
            self.planned_entry_reference,
            self.stop_loss_price,
            self.selected_target,
            self.target_price,
        )
        if self.outcome is HistoricalExecutionOutcome.NO_TRADE_INTENT:
            if any(value is not None for value in plan_values):
                raise ValueError(
                    "no-trade execution cannot contain plan fields"
                )
            return
        if any(value is None for value in plan_values):
            raise ValueError(
                "planned executions require direction, stop, and target"
            )
        if self.direction is TradeDirection.LONG:
            if not self.stop_loss_price < self.planned_entry_reference:
                raise ValueError("long execution stop must be below plan")
            if not self.target_price > self.planned_entry_reference:
                raise ValueError("long execution target must be above plan")
        else:
            if not self.stop_loss_price > self.planned_entry_reference:
                raise ValueError("short execution stop must be above plan")
            if not self.target_price < self.planned_entry_reference:
                raise ValueError("short execution target must be below plan")

    def _validate_position(self) -> None:
        if self.quantity == 0:
            if any(
                value is not None
                for value in (
                    self.entry_fill_price,
                    self.risk_per_unit,
                    self.exit_candle,
                    self.exit_fill_price,
                    self.gross_pnl,
                    self.net_pnl,
                    self.realized_r_multiple,
                )
            ):
                raise ValueError(
                    "non-entered execution cannot contain position fills"
                )
            if any(
                value != 0
                for value in (
                    self.position_notional,
                    self.entry_fee,
                    self.exit_fee,
                    self.total_costs,
                    self.bars_held,
                )
            ):
                raise ValueError(
                    "non-entered execution cannot contain position costs"
                )
            if not isclose(
                self.ending_capital or 0,
                self.config.initial_capital,
                abs_tol=1e-9,
            ):
                raise ValueError(
                    "non-entered execution must preserve initial capital"
                )
            return

        if self.entry_candle is None or self.entry_fill_price is None:
            raise ValueError("entered execution requires an entry fill")
        if self.entry_order_price is None or not isclose(
            self.entry_order_price,
            self.entry_fill_price,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "entered execution order price must match its fill"
            )
        if self.risk_per_unit is None or self.risk_per_unit <= 0:
            raise ValueError("entered execution requires positive unit risk")
        expected_notional = self.entry_fill_price * self.quantity
        if not isclose(
            self.position_notional,
            expected_notional,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "execution notional must equal entry price times quantity"
            )
        expected_entry_fee = (
            self.position_notional
            * self.config.commission_basis_points
            / 10_000
            + self.config.fixed_fee_per_order
        )
        if not isclose(
            self.entry_fee,
            expected_entry_fee,
            abs_tol=1e-9,
        ):
            raise ValueError("execution entry fee does not match config")
        if self.bars_held < 1:
            raise ValueError("entered execution must hold at least one bar")

    def _validate_outcome(self) -> None:
        non_entries = {
            HistoricalExecutionOutcome.NO_TRADE_INTENT,
            HistoricalExecutionOutcome.TARGET_BLOCKED,
            HistoricalExecutionOutcome.NO_FUTURE_CANDLE,
            HistoricalExecutionOutcome.INVALIDATED_AT_ENTRY,
            HistoricalExecutionOutcome.INSUFFICIENT_CAPITAL,
        }
        if self.outcome in non_entries:
            if self.quantity != 0:
                raise ValueError("non-entry outcome cannot hold a position")
            return

        if self.quantity <= 0:
            raise ValueError("entered outcome requires positive quantity")
        if self.outcome is HistoricalExecutionOutcome.OPEN:
            if self.exit_candle is not None or self.exit_fill_price is not None:
                raise ValueError("open execution cannot contain an exit")
            if any(
                value is not None
                for value in (
                    self.gross_pnl,
                    self.net_pnl,
                    self.ending_capital,
                    self.realized_r_multiple,
                )
            ):
                raise ValueError(
                    "open execution cannot report realized performance"
                )
            if not isclose(
                self.total_costs,
                self.entry_fee,
                abs_tol=1e-9,
            ):
                raise ValueError("open execution costs must equal entry fee")
            return

        if self.exit_candle is None or self.exit_fill_price is None:
            raise ValueError("closed execution requires an exit fill")
        if any(
            value is None
            for value in (
                self.gross_pnl,
                self.net_pnl,
                self.ending_capital,
                self.realized_r_multiple,
            )
        ):
            raise ValueError(
                "closed execution requires realized performance"
            )
        expected_costs = self.entry_fee + self.exit_fee
        if not isclose(self.total_costs, expected_costs, abs_tol=1e-9):
            raise ValueError("execution costs must equal entry and exit fees")
        expected_net = self.gross_pnl - self.total_costs
        if not isclose(self.net_pnl, expected_net, abs_tol=1e-9):
            raise ValueError("net P&L must equal gross P&L less costs")
        expected_exit_fee = (
            self.exit_fill_price
            * self.quantity
            * self.config.commission_basis_points
            / 10_000
            + self.config.fixed_fee_per_order
        )
        if not isclose(self.exit_fee, expected_exit_fee, abs_tol=1e-9):
            raise ValueError("execution exit fee does not match config")
        direction_sign = 1 if self.direction is TradeDirection.LONG else -1
        expected_gross = (
            (self.exit_fill_price - self.entry_fill_price)
            * self.quantity
            * direction_sign
        )
        if not isclose(self.gross_pnl, expected_gross, abs_tol=1e-9):
            raise ValueError("gross P&L does not match execution fills")
        if not isclose(
            self.ending_capital,
            self.config.initial_capital + self.net_pnl,
            abs_tol=1e-9,
        ):
            raise ValueError("ending capital must include net P&L")
        expected_r = self.net_pnl / (
            self.risk_per_unit * self.quantity
        )
        if not isclose(
            self.realized_r_multiple,
            expected_r,
            abs_tol=1e-9,
        ):
            raise ValueError(
                "realized R-multiple must derive from net P&L and risk"
            )
        entry_index = self.market_series.candles.index(self.entry_candle)
        exit_index = self.market_series.candles.index(self.exit_candle)
        if self.bars_held != exit_index - entry_index + 1:
            raise ValueError(
                "execution holding period does not match its candles"
            )
