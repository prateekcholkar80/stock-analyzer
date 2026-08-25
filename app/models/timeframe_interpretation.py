from datetime import datetime
from enum import StrEnum
from math import isclose, isfinite
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.multi_timeframe_evidence import (
    SwingAnalysisTimeframe,
    timeframe_interval,
)
from app.models.technical import TechnicalModel
from app.models.technical_setup import (
    SwingSetupSide,
    TimeframeSwingSetup,
)
from app.models.trade_decision import (
    MarketCondition,
    NoTradeReason,
    TradeDecision,
    TradeDecisionOutcome,
)


class TimeframeAlignment(StrEnum):
    ALIGNED_BULLISH = "aligned_bullish"
    ALIGNED_BEARISH = "aligned_bearish"
    ALIGNED_NEUTRAL = "aligned_neutral"
    MIXED = "mixed"
    CONFLICTED = "conflicted"
    INSUFFICIENT = "insufficient"


class TacticalReadiness(StrEnum):
    READY = "ready"
    DEVELOPING = "developing"
    BLOCKED = "blocked"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT = "insufficient"


class StructuralRisk(StrEnum):
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    PROHIBITIVE = "prohibitive"
    UNKNOWN = "unknown"


class RewardRiskFeasibility(StrEnum):
    FEASIBLE = "feasible"
    BLOCKED_BY_STRUCTURE = "blocked_by_structure"
    INSUFFICIENT_DATA = "insufficient_data"
    NOT_EVALUATED = "not_evaluated"


class TimeframeMarketInterpretation(TechnicalModel):
    """One evidence-linked strategic or tactical timeframe conclusion."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.timeframe_interpretation.v1"] = (
        "jarvis.timeframe_interpretation.v1"
    )
    timeframe: SwingAnalysisTimeframe
    interval: str = Field(min_length=1)
    evaluated_at: datetime
    market_condition: MarketCondition
    bullish_setup: TimeframeSwingSetup
    bearish_setup: TimeframeSwingSetup
    decisive_evidence_ids: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)

    @field_validator("evaluated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timeframe interpretation requires timezone")
        return value

    @field_validator("decisive_evidence_ids")
    @classmethod
    def validate_evidence_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("interpretation evidence IDs cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("interpretation evidence IDs must be unique")
        return values

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("timeframe interpretation rationale cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_interpretation(self) -> Self:
        if self.interval != timeframe_interval(self.timeframe):
            raise ValueError("timeframe interpretation interval is incorrect")
        for setup, side in (
            (self.bullish_setup, SwingSetupSide.BULLISH),
            (self.bearish_setup, SwingSetupSide.BEARISH),
        ):
            if (
                setup.side is not side
                or setup.timeframe is not self.timeframe
                or setup.interval != self.interval
                or setup.evaluated_at != self.evaluated_at
            ):
                raise ValueError(
                    "timeframe interpretation setup assignment is incorrect"
                )
        prefix = f"{self.timeframe.value}:"
        if any(
            not evidence_id.startswith(prefix)
            for evidence_id in self.decisive_evidence_ids
        ):
            raise ValueError(
                "interpretation evidence IDs must match its timeframe"
            )
        if (
            self.market_condition is not MarketCondition.INSUFFICIENT
            and not self.decisive_evidence_ids
        ):
            raise ValueError(
                "evidenced timeframe conditions require decisive evidence"
            )
        return self


class RewardRiskTargetInterpretation(TechnicalModel):
    """Auditable feasibility of one theoretical upside target."""

    model_config = ConfigDict(frozen=True, strict=True)

    reward_to_risk: Literal[2.0, 3.0]
    feasibility: RewardRiskFeasibility
    target_price: float | None = Field(default=None, gt=0)
    blocking_evidence_ids: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)

    @field_validator("target_price", mode="before")
    @classmethod
    def require_finite_target(
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
            raise ValueError("reward-risk target must be finite")
        return float(value)

    @field_validator("blocking_evidence_ids")
    @classmethod
    def validate_blocking_evidence(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(
            not value.startswith(("daily:", "weekly:"))
            for value in values
        ):
            raise ValueError(
                "blocking evidence IDs must be qualified by timeframe"
            )
        if len(values) != len(set(values)):
            raise ValueError("blocking evidence IDs must be unique")
        return values

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reward-risk rationale cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_feasibility(self) -> Self:
        if self.feasibility is RewardRiskFeasibility.FEASIBLE:
            if self.target_price is None or self.blocking_evidence_ids:
                raise ValueError(
                    "feasible target requires a price without blocking evidence"
                )
        elif self.feasibility is RewardRiskFeasibility.BLOCKED_BY_STRUCTURE:
            if self.target_price is None or not self.blocking_evidence_ids:
                raise ValueError(
                    "blocked target requires price and blocking evidence"
                )
        elif self.target_price is not None or self.blocking_evidence_ids:
            raise ValueError(
                "unevaluated target cannot claim price or blocking evidence"
            )
        return self


class SwingRiskRewardInterpretation(TechnicalModel):
    """Theoretical long-side 2R/3R feasibility used by BUY/NO-TRADE policy."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.swing_risk_reward.v1"] = (
        "jarvis.swing_risk_reward.v1"
    )
    reference_entry: float | None = Field(default=None, gt=0)
    stop_loss: float | None = Field(default=None, gt=0)
    risk_per_unit: float | None = Field(default=None, gt=0)
    target_2r: RewardRiskTargetInterpretation
    target_3r: RewardRiskTargetInterpretation

    @field_validator(
        "reference_entry",
        "stop_loss",
        "risk_per_unit",
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
            raise ValueError("reward-risk metrics must be finite")
        return float(value)

    @model_validator(mode="after")
    def validate_risk_reward(self) -> Self:
        if self.target_2r.reward_to_risk != 2.0:
            raise ValueError("2R assessment must use a 2.0 multiple")
        if self.target_3r.reward_to_risk != 3.0:
            raise ValueError("3R assessment must use a 3.0 multiple")

        prices = (self.reference_entry, self.stop_loss, self.risk_per_unit)
        has_prices = tuple(value is not None for value in prices)
        if any(has_prices) and not all(has_prices):
            raise ValueError(
                "reward-risk entry, stop and risk must appear together"
            )
        evaluated = {
            RewardRiskFeasibility.FEASIBLE,
            RewardRiskFeasibility.BLOCKED_BY_STRUCTURE,
        }
        has_evaluated_target = (
            self.target_2r.feasibility in evaluated
            or self.target_3r.feasibility in evaluated
        )
        if has_evaluated_target != all(has_prices):
            raise ValueError(
                "evaluated targets require complete entry and stop pricing"
            )
        if all(has_prices):
            if self.stop_loss >= self.reference_entry:
                raise ValueError("swing interpretation stop must be below entry")
            expected_risk = self.reference_entry - self.stop_loss
            if not isclose(self.risk_per_unit, expected_risk):
                raise ValueError(
                    "swing interpretation risk must equal entry minus stop"
                )
            for target in (self.target_2r, self.target_3r):
                if target.target_price is None:
                    continue
                expected_target = (
                    self.reference_entry
                    + self.risk_per_unit * target.reward_to_risk
                )
                if not isclose(target.target_price, expected_target):
                    raise ValueError(
                        "reward-risk target price does not match its multiple"
                    )

        two_r = self.target_2r.feasibility
        three_r = self.target_3r.feasibility
        if (
            two_r is not RewardRiskFeasibility.FEASIBLE
            and three_r is RewardRiskFeasibility.FEASIBLE
        ):
            raise ValueError("3R cannot be feasible when 2R is not feasible")
        if (
            two_r is RewardRiskFeasibility.NOT_EVALUATED
            and three_r is not RewardRiskFeasibility.NOT_EVALUATED
        ):
            raise ValueError("3R cannot be evaluated before 2R")
        if (
            two_r is RewardRiskFeasibility.INSUFFICIENT_DATA
            and three_r
            not in {
                RewardRiskFeasibility.INSUFFICIENT_DATA,
                RewardRiskFeasibility.NOT_EVALUATED,
            }
        ):
            raise ValueError("3R cannot outrun insufficient 2R evidence")
        return self


class MultiTimeframeSwingInterpretation(TechnicalModel):
    """Deterministic daily/weekly synthesis consumed by Judge and UI."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.multi_timeframe_interpretation.v1"] = (
        "jarvis.multi_timeframe_interpretation.v1"
    )
    daily: TimeframeMarketInterpretation
    weekly: TimeframeMarketInterpretation
    alignment: TimeframeAlignment
    tactical_readiness: TacticalReadiness
    structural_risk: StructuralRisk
    risk_reward: SwingRiskRewardInterpretation
    trade_decision: TradeDecisionOutcome
    decisive_evidence_ids: tuple[str, ...] = ()
    decision_change_conditions: tuple[str, ...] = Field(min_length=1)
    rationale: str = Field(min_length=1)
    interpreted_at: datetime

    @field_validator("decisive_evidence_ids")
    @classmethod
    def validate_decisive_evidence(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(
            not value.startswith(("daily:", "weekly:"))
            for value in values
        ):
            raise ValueError("decisive evidence must be timeframe-qualified")
        if len(values) != len(set(values)):
            raise ValueError("decisive evidence IDs must be unique")
        return values

    @field_validator("decision_change_conditions")
    @classmethod
    def validate_change_conditions(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value.strip() for value in values):
            raise ValueError("decision-change conditions cannot be blank")
        if len(values) != len(set(values)):
            raise ValueError("decision-change conditions must be unique")
        return values

    @field_validator("rationale")
    @classmethod
    def reject_blank_rationale(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("multi-timeframe rationale cannot be blank")
        return value

    @field_validator("interpreted_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("multi-timeframe interpretation requires timezone")
        return value

    @model_validator(mode="after")
    def validate_interpretation(self) -> Self:
        if self.daily.timeframe is not SwingAnalysisTimeframe.DAILY:
            raise ValueError("daily interpretation must be assigned to daily")
        if self.weekly.timeframe is not SwingAnalysisTimeframe.WEEKLY:
            raise ValueError("weekly interpretation must be assigned to weekly")
        if self.interpreted_at < max(
            self.daily.evaluated_at,
            self.weekly.evaluated_at,
        ):
            raise ValueError(
                "combined interpretation cannot precede timeframe evaluation"
            )

        expected_alignment = derive_timeframe_alignment(
            self.daily.market_condition,
            self.weekly.market_condition,
        )
        if self.alignment is not expected_alignment:
            raise ValueError(
                "timeframe alignment does not match daily and weekly conditions"
            )
        expected_condition = derive_combined_market_condition(
            self.daily.market_condition,
            self.weekly.market_condition,
        )
        if self.trade_decision.market_condition is not expected_condition:
            raise ValueError(
                "trade decision condition does not match timeframe conditions"
            )

        available_evidence = set(self.daily.decisive_evidence_ids) | set(
            self.weekly.decisive_evidence_ids
        )
        if not set(self.decisive_evidence_ids) <= available_evidence:
            raise ValueError(
                "combined interpretation cites evidence outside its timeframes"
            )
        if (
            expected_condition is not MarketCondition.INSUFFICIENT
            and not self.decisive_evidence_ids
        ):
            raise ValueError(
                "combined evidenced condition requires decisive evidence"
            )

        if expected_condition is not MarketCondition.BULLISH:
            permitted = {
                RewardRiskFeasibility.NOT_EVALUATED,
                RewardRiskFeasibility.INSUFFICIENT_DATA,
            }
            if (
                self.risk_reward.target_2r.feasibility not in permitted
                or self.risk_reward.target_3r.feasibility not in permitted
            ):
                raise ValueError(
                    "non-bullish interpretation cannot expose trade targets"
                )

        if self.trade_decision.decision is TradeDecision.BUY:
            self._validate_buy_policy()
        else:
            self._validate_no_trade_policy(expected_condition)
        return self

    def _validate_buy_policy(self) -> None:
        if (
            self.alignment is not TimeframeAlignment.ALIGNED_BULLISH
            or self.tactical_readiness is not TacticalReadiness.READY
            or self.structural_risk
            not in {StructuralRisk.LOW, StructuralRisk.MODERATE}
            or self.risk_reward.target_2r.feasibility
            is not RewardRiskFeasibility.FEASIBLE
        ):
            raise ValueError(
                "BUY requires aligned bullish timeframes, ready trigger, "
                "acceptable structural risk and feasible 2R"
            )

    def _validate_no_trade_policy(
        self,
        condition: MarketCondition,
    ) -> None:
        if condition is not MarketCondition.BULLISH:
            return
        reasons = set(self.trade_decision.no_trade_reasons)
        blockers_present = False
        if self.tactical_readiness is not TacticalReadiness.READY:
            blockers_present = True
            if not reasons & {
                NoTradeReason.DAILY_TRIGGER_INCOMPLETE,
                NoTradeReason.BUY_SETUP_INVALIDATED,
                NoTradeReason.JUDGE_REJECTED_BULLISH_CASE,
            }:
                raise ValueError(
                    "unready bullish setup requires a tactical no-trade reason"
                )
        if self.structural_risk in {
            StructuralRisk.HIGH,
            StructuralRisk.PROHIBITIVE,
        }:
            blockers_present = True
            if not reasons & {
                NoTradeReason.ACTIVE_BEARISH_BREAK,
                NoTradeReason.STRUCTURAL_STOP_UNAVAILABLE,
                NoTradeReason.BUY_SETUP_INVALIDATED,
            }:
                raise ValueError(
                    "unsafe bullish structure requires a structural reason"
                )
        target_feasibility = self.risk_reward.target_2r.feasibility
        if (
            target_feasibility is RewardRiskFeasibility.BLOCKED_BY_STRUCTURE
            or (
                self.tactical_readiness is TacticalReadiness.READY
                and target_feasibility is not RewardRiskFeasibility.FEASIBLE
            )
        ):
            blockers_present = True
            if not reasons & {
                NoTradeReason.MINIMUM_TARGET_BLOCKED,
                NoTradeReason.INSUFFICIENT_REWARD_TO_RISK,
                NoTradeReason.STRUCTURAL_STOP_UNAVAILABLE,
            }:
                raise ValueError(
                    "infeasible bullish 2R requires a reward-risk reason"
                )
        if not blockers_present:
            raise ValueError(
                "aligned actionable bullish evidence cannot conclude NO_TRADE"
            )


def derive_timeframe_alignment(
    daily: MarketCondition,
    weekly: MarketCondition,
) -> TimeframeAlignment:
    """Derive alignment mechanically; no model or agent opinion is involved."""

    conditions = {daily, weekly}
    if MarketCondition.INSUFFICIENT in conditions:
        return TimeframeAlignment.INSUFFICIENT
    if MarketCondition.CONFLICTED in conditions:
        return TimeframeAlignment.CONFLICTED
    if daily is weekly is MarketCondition.BULLISH:
        return TimeframeAlignment.ALIGNED_BULLISH
    if daily is weekly is MarketCondition.BEARISH:
        return TimeframeAlignment.ALIGNED_BEARISH
    if daily is weekly is MarketCondition.NEUTRAL:
        return TimeframeAlignment.ALIGNED_NEUTRAL
    return TimeframeAlignment.MIXED


def derive_combined_market_condition(
    daily: MarketCondition,
    weekly: MarketCondition,
) -> MarketCondition:
    """Collapse two timeframe conditions for the existing decision contract."""

    if MarketCondition.INSUFFICIENT in {daily, weekly}:
        return MarketCondition.INSUFFICIENT
    if daily is weekly:
        return daily
    return MarketCondition.CONFLICTED
