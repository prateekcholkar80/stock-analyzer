from enum import StrEnum
from math import isclose
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from app.models.agentic import AgenticTradePlanningResult
from app.models.technical import TechnicalModel
from app.models.trade_setup import TradeDirection


class MultiTimeframeTradeDisposition(StrEnum):
    ACTIONABLE = "actionable"
    NO_TRADE = "no_trade"


class MultiTimeframeTradeReason(StrEnum):
    BULLISH_VERDICT_AND_DAILY_SETUP = "bullish_verdict_and_daily_setup"
    JUDGE_NOT_BULLISH = "judge_not_bullish"
    DAILY_PROFILE_NOT_BULLISH = "daily_profile_not_bullish"
    DETERMINISTIC_PLANNER_NO_TRADE = "deterministic_planner_no_trade"


class MultiTimeframeLongTradePlanResult(TechnicalModel):
    """Long-only 2R planning outcome linked to one approved debate."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.multi_timeframe_long_trade_plan.v1"] = (
        "jarvis.multi_timeframe_long_trade_plan.v1"
    )
    policy_id: Literal["jarvis.long_only_2r_swing_policy.v1"] = (
        "jarvis.long_only_2r_swing_policy.v1"
    )
    technical_package_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    technical_decision_id: str = Field(min_length=1)
    debate_verdict_id: str = Field(min_length=1)
    disposition: MultiTimeframeTradeDisposition
    reason: MultiTimeframeTradeReason
    minimum_reward_to_risk: Literal[2.0] = 2.0
    daily_planning_result: AgenticTradePlanningResult | None = None
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_long_only_outcome(self) -> Self:
        planning = self.daily_planning_result
        if self.disposition is MultiTimeframeTradeDisposition.ACTIONABLE:
            if self.reason is not (
                MultiTimeframeTradeReason.BULLISH_VERDICT_AND_DAILY_SETUP
            ):
                raise ValueError("actionable long plan has an invalid reason")
            if planning is None or planning.approved_trade_intent is None:
                raise ValueError(
                    "actionable multi-timeframe outcome requires an approved plan"
                )
            evaluation = planning.approved_trade_intent.evaluation
            if evaluation.direction is not TradeDirection.LONG:
                raise ValueError("multi-timeframe policy permits only long plans")
            if not isclose(evaluation.minimum_reward_to_risk, 2.0):
                raise ValueError("multi-timeframe plan must use a 1:2 minimum")
            return self

        if self.reason is (
            MultiTimeframeTradeReason.BULLISH_VERDICT_AND_DAILY_SETUP
        ):
            raise ValueError("no-trade outcome cannot use actionable reason")
        if planning is not None and planning.approved_trade_intent is not None:
            raise ValueError("no-trade outcome cannot expose an approved plan")
        return self
