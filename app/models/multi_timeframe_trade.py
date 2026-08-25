from enum import StrEnum
from math import isclose
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from app.models.agentic import AgenticTradePlanningResult
from app.models.technical import TechnicalModel
from app.models.trade_decision import (
    MarketCondition,
    TradeDecision,
    TradeDecisionOutcome,
)
from app.models.trade_setup import TradeDirection


class MultiTimeframeTradeDisposition(StrEnum):
    ACTIONABLE = "actionable"
    NO_TRADE = "no_trade"


class MultiTimeframeTradeReason(StrEnum):
    BULLISH_VERDICT_AND_DAILY_SETUP = "bullish_verdict_and_daily_setup"
    JUDGE_NOT_BULLISH = "judge_not_bullish"
    DAILY_PROFILE_NOT_BULLISH = "daily_profile_not_bullish"
    DETERMINISTIC_PLANNER_NO_TRADE = "deterministic_planner_no_trade"
    TIMEFRAME_SETUP_NOT_ALIGNED = "timeframe_setup_not_aligned"


class MultiTimeframeLongTradePlanResult(TechnicalModel):
    """Legacy-named 2R planning envelope linked to one approved debate."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal[
        "jarvis.multi_timeframe_long_trade_plan.v1",
        "jarvis.multi_timeframe_trade_plan.v2",
    ] = (
        "jarvis.multi_timeframe_long_trade_plan.v1"
    )
    policy_id: Literal[
        "jarvis.long_only_2r_swing_policy.v1",
        "jarvis.buy_eligibility_2r_swing_policy.v2",
    ] = (
        "jarvis.long_only_2r_swing_policy.v1"
    )
    technical_package_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    technical_decision_id: str = Field(min_length=1)
    debate_verdict_id: str = Field(min_length=1)
    disposition: MultiTimeframeTradeDisposition
    reason: MultiTimeframeTradeReason
    trade_decision: TradeDecisionOutcome | None = None
    minimum_reward_to_risk: Literal[2.0] = 2.0
    daily_planning_result: AgenticTradePlanningResult | None = None
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_trade_outcome(self) -> Self:
        legacy_schema = (
            self.schema_version
            == "jarvis.multi_timeframe_long_trade_plan.v1"
        )
        expected_policy = (
            "jarvis.long_only_2r_swing_policy.v1"
            if legacy_schema
            else "jarvis.buy_eligibility_2r_swing_policy.v2"
        )
        if self.policy_id != expected_policy:
            raise ValueError(
                "trade-plan schema and buy-eligibility policy must match"
            )
        if not legacy_schema and self.trade_decision is None:
            raise ValueError(
                "version-2 trade plan requires a trade-decision contract"
            )

        planning = self.daily_planning_result
        if self.disposition is MultiTimeframeTradeDisposition.ACTIONABLE:
            if self.reason is not (
                MultiTimeframeTradeReason.BULLISH_VERDICT_AND_DAILY_SETUP
            ):
                raise ValueError("actionable buy plan has an invalid reason")
            if planning is None or planning.approved_trade_intent is None:
                raise ValueError(
                    "actionable multi-timeframe outcome requires an approved plan"
                )
            evaluation = planning.approved_trade_intent.evaluation
            if evaluation.direction is not TradeDirection.LONG:
                raise ValueError("multi-timeframe policy permits only buy plans")
            if not isclose(evaluation.minimum_reward_to_risk, 2.0):
                raise ValueError("multi-timeframe plan must use a 1:2 minimum")
            if self.trade_decision is not None and (
                self.trade_decision.market_condition
                is not MarketCondition.BULLISH
                or self.trade_decision.decision is not TradeDecision.BUY
            ):
                raise ValueError(
                    "actionable plan requires a bullish BUY decision"
                )
            return self

        if self.reason is (
            MultiTimeframeTradeReason.BULLISH_VERDICT_AND_DAILY_SETUP
        ):
            raise ValueError("no-trade outcome cannot use actionable reason")
        if planning is not None and planning.approved_trade_intent is not None:
            raise ValueError("no-trade outcome cannot expose an approved plan")
        if self.trade_decision is not None and (
            self.trade_decision.decision is not TradeDecision.NO_TRADE
        ):
            raise ValueError(
                "no-trade plan requires a NO_TRADE decision"
            )
        return self
