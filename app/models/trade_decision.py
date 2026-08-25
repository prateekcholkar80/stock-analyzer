from enum import StrEnum
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from app.models.signals import (
    SignalDirection,
    SwingTradingSignalProfile,
    SwingTradingStance,
)
from app.models.technical import TechnicalModel


class MarketCondition(StrEnum):
    """Evidence-grounded directional character of the market."""

    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    CONFLICTED = "conflicted"
    INSUFFICIENT = "insufficient"


class TradeDecision(StrEnum):
    """The only trade actions Jarvis may expose to the user."""

    BUY = "buy"
    NO_TRADE = "no_trade"


class NoTradeReason(StrEnum):
    """Deterministic reasons that prevent a buy decision."""

    BEARISH_CONDITION = "bearish_condition"
    NEUTRAL_CONDITION = "neutral_condition"
    TIMEFRAME_CONFLICT = "timeframe_conflict"
    INSUFFICIENT_DATA = "insufficient_data"
    STALE_DATA = "stale_data"
    DAILY_TRIGGER_INCOMPLETE = "daily_trigger_incomplete"
    BUY_SETUP_INVALIDATED = "buy_setup_invalidated"
    ACTIVE_BEARISH_BREAK = "active_bearish_break"
    STRUCTURAL_STOP_UNAVAILABLE = "structural_stop_unavailable"
    MINIMUM_TARGET_BLOCKED = "minimum_target_blocked"
    INSUFFICIENT_REWARD_TO_RISK = "insufficient_reward_to_risk"
    JUDGE_REJECTED_BULLISH_CASE = "judge_rejected_bullish_case"


_CONDITION_REASON = {
    MarketCondition.BEARISH: NoTradeReason.BEARISH_CONDITION,
    MarketCondition.NEUTRAL: NoTradeReason.NEUTRAL_CONDITION,
    MarketCondition.CONFLICTED: NoTradeReason.TIMEFRAME_CONFLICT,
    MarketCondition.INSUFFICIENT: NoTradeReason.INSUFFICIENT_DATA,
}
_CONDITION_REASONS = frozenset(_CONDITION_REASON.values())


class TradeDecisionOutcome(TechnicalModel):
    """Separate market character from Jarvis's buy/no-trade decision.

    Bullish evidence is necessary but not sufficient for a buy. Bearish,
    neutral, conflicted, or insufficient evidence can only produce no-trade.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.trade_decision.v1"] = (
        "jarvis.trade_decision.v1"
    )
    market_condition: MarketCondition
    decision: TradeDecision
    no_trade_reasons: tuple[NoTradeReason, ...] = ()
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_decision_policy(self) -> Self:
        if len(self.no_trade_reasons) != len(set(self.no_trade_reasons)):
            raise ValueError("no-trade reasons must be unique")

        if self.decision is TradeDecision.BUY:
            if self.market_condition is not MarketCondition.BULLISH:
                raise ValueError(
                    "buy decision requires a bullish market condition"
                )
            if self.no_trade_reasons:
                raise ValueError("buy decision cannot include no-trade reasons")
            return self

        if not self.no_trade_reasons:
            raise ValueError("no-trade decision requires at least one reason")

        required_reason = _CONDITION_REASON.get(self.market_condition)
        if (
            required_reason is not None
            and required_reason not in self.no_trade_reasons
        ):
            raise ValueError(
                f"{self.market_condition.value} market condition requires "
                f"the {required_reason.value} no-trade reason"
            )

        mismatched_reasons = (
            set(self.no_trade_reasons) & _CONDITION_REASONS
        ) - ({required_reason} if required_reason is not None else set())
        if mismatched_reasons:
            raise ValueError(
                "market-condition no-trade reason does not match the "
                "reported market condition"
            )
        return self


def market_condition_from_profile(
    profile: SwingTradingSignalProfile,
) -> MarketCondition:
    """Translate a deterministic technical profile into market character."""

    if not isinstance(profile, SwingTradingSignalProfile):
        raise ValueError("market condition requires a swing profile")
    if profile.stance is SwingTradingStance.INSUFFICIENT_EVIDENCE:
        return MarketCondition.INSUFFICIENT
    if profile.direction is SignalDirection.BULLISH:
        return MarketCondition.BULLISH
    if profile.direction is SignalDirection.BEARISH:
        return MarketCondition.BEARISH
    if profile.has_directional_conflict:
        return MarketCondition.CONFLICTED
    return MarketCondition.NEUTRAL


def combined_profile_market_condition(
    daily: SwingTradingSignalProfile,
    weekly: SwingTradingSignalProfile,
) -> MarketCondition:
    """Combine daily and weekly character without consulting an LLM verdict."""

    conditions = {
        market_condition_from_profile(daily),
        market_condition_from_profile(weekly),
    }
    if MarketCondition.INSUFFICIENT in conditions:
        return MarketCondition.INSUFFICIENT
    if MarketCondition.CONFLICTED in conditions or len(conditions) > 1:
        return MarketCondition.CONFLICTED
    return next(iter(conditions))
