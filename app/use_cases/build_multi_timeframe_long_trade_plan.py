from math import isclose

from app.analytics.cpr_policy import evaluate_cpr_trade_policy
from app.exceptions import AgentSubmissionRejectedError
from app.models.agentic import (
    AgenticSwingAnalysisResult,
    JarvisJudgeDecision,
    JarvisJudgeVerdict,
    TradePlanningReason,
)
from app.models.debate import AgenticDebateResult
from app.models.multi_timeframe_evidence import MultiTimeframeEvidenceReview
from app.models.multi_timeframe_trade import (
    MultiTimeframeLongTradePlanResult,
    MultiTimeframeTradeDisposition,
    MultiTimeframeTradeReason,
)
from app.models.signals import SignalDirection
from app.models.trade_decision import (
    MarketCondition,
    NoTradeReason,
    TradeDecision,
    TradeDecisionOutcome,
    combined_profile_market_condition,
)
from app.orchestration.agent_orchestrator import AgentOrchestrator


class BuildMultiTimeframeLongTradePlan:
    """Build a 2R BUY/NO_TRADE outcome after the Judge verdict."""

    use_case_id = "jarvis.build_multi_timeframe_long_trade_plan.v1"

    def __init__(self, agent_orchestrator: AgentOrchestrator) -> None:
        if not isinstance(agent_orchestrator, AgentOrchestrator):
            raise ValueError("trade planning requires an agent orchestrator")
        config = agent_orchestrator.trade_planning_agent.config
        if not isclose(config.minimum_reward_to_risk, 2.0):
            raise ValueError("multi-timeframe trade policy requires 1:2 minimum")
        self._orchestrator = agent_orchestrator

    def execute(
        self,
        technical_review: MultiTimeframeEvidenceReview,
        debate_result: AgenticDebateResult,
    ) -> MultiTimeframeLongTradePlanResult:
        package = _validated_chain(technical_review, debate_result)
        verdict = debate_result.submission.verdict
        analysis = package.technical_analysis
        market_condition = combined_profile_market_condition(
            analysis.daily_submission.profile,
            analysis.weekly_submission.profile,
        )
        common = {
            "schema_version": "jarvis.multi_timeframe_trade_plan.v2",
            "policy_id": "jarvis.buy_eligibility_2r_swing_policy.v2",
            "technical_package_fingerprint": package.package_fingerprint,
            "technical_decision_id": technical_review.decision.decision_id,
            "debate_verdict_id": verdict.verdict_id,
        }

        if verdict.winner is not SignalDirection.BULLISH:
            no_trade_reason = _condition_no_trade_reason(
                market_condition,
                judge_rejected=True,
            )
            rationale = (
                "No BUY decision was issued because the final Judge "
                f"verdict is {verdict.winner.value}; the deterministic "
                "daily/weekly market condition remains "
                f"{market_condition.value}."
            )
            return MultiTimeframeLongTradePlanResult(
                **common,
                disposition=MultiTimeframeTradeDisposition.NO_TRADE,
                reason=MultiTimeframeTradeReason.JUDGE_NOT_BULLISH,
                trade_decision=TradeDecisionOutcome(
                    market_condition=market_condition,
                    decision=TradeDecision.NO_TRADE,
                    no_trade_reasons=(no_trade_reason,),
                    rationale=rationale,
                ),
                rationale=rationale,
            )

        if market_condition is not MarketCondition.BULLISH:
            no_trade_reason = _condition_no_trade_reason(market_condition)
            rationale = (
                "No BUY decision was issued because the deterministic "
                "daily and weekly profiles are not aligned bullish; the "
                f"combined market condition is {market_condition.value}."
            )
            return MultiTimeframeLongTradePlanResult(
                **common,
                disposition=MultiTimeframeTradeDisposition.NO_TRADE,
                reason=MultiTimeframeTradeReason.TIMEFRAME_SETUP_NOT_ALIGNED,
                trade_decision=TradeDecisionOutcome(
                    market_condition=market_condition,
                    decision=TradeDecision.NO_TRADE,
                    no_trade_reasons=(no_trade_reason,),
                    rationale=rationale,
                ),
                rationale=rationale,
            )

        cpr_policy = evaluate_cpr_trade_policy(
            package.daily.cpr,
            package.weekly.cpr,
        )
        if not cpr_policy.buy_eligible:
            rationale = (
                "No BUY decision was issued because the deterministic CPR "
                "confirmation policy found unresolved timing or structural "
                f"risk. {cpr_policy.rationale}"
            )
            return MultiTimeframeLongTradePlanResult(
                **common,
                disposition=MultiTimeframeTradeDisposition.NO_TRADE,
                reason=MultiTimeframeTradeReason.TIMEFRAME_SETUP_NOT_ALIGNED,
                trade_decision=TradeDecisionOutcome(
                    market_condition=MarketCondition.BULLISH,
                    decision=TradeDecision.NO_TRADE,
                    no_trade_reasons=cpr_policy.blocking_reasons,
                    rationale=rationale,
                ),
                rationale=rationale,
            )

        daily_submission = analysis.daily_submission
        daily_result = AgenticSwingAnalysisResult(
            orchestrator_id=self._orchestrator.orchestrator_id,
            submission=daily_submission,
            decision=JarvisJudgeDecision(
                decision_id=_daily_projection_decision_id(technical_review),
                judge_id="jarvis.swing_judge.v1",
                submission_id=daily_submission.submission_id,
                verdict=JarvisJudgeVerdict.ACCEPTED,
                decided_at=technical_review.decision.decided_at,
                passed_checks=(
                    "multi_timeframe_release_accepted",
                    "daily_technical_submission_approved",
                    "daily_trade_planning_projection",
                ),
            ),
        )
        planning = self._orchestrator.run_trade_planning(
            daily_result,
            analysis.timeframes.daily,
        )
        if not planning.decision.accepted:
            raise AgentSubmissionRejectedError(
                "Jarvis rejected the deterministic daily trade plan: "
                + "; ".join(planning.decision.reasons)
            )
        plan = planning.approved_trade_intent
        if plan is None:
            no_trade_reason = _planner_no_trade_reason(
                planning.submission.reason
            )
            return MultiTimeframeLongTradePlanResult(
                **common,
                disposition=MultiTimeframeTradeDisposition.NO_TRADE,
                reason=(
                    MultiTimeframeTradeReason.DETERMINISTIC_PLANNER_NO_TRADE
                ),
                daily_planning_result=planning,
                trade_decision=TradeDecisionOutcome(
                    market_condition=MarketCondition.BULLISH,
                    decision=TradeDecision.NO_TRADE,
                    no_trade_reasons=(no_trade_reason,),
                    rationale=planning.submission.rationale,
                ),
                rationale=planning.submission.rationale,
            )
        rationale = (
            "The final Judge and daily profile are bullish, and the "
            "deterministic planner found a structure-aware buy setup "
            "with a feasible 1:2 minimum target. "
            f"{cpr_policy.rationale}"
        )
        return MultiTimeframeLongTradePlanResult(
            **common,
            disposition=MultiTimeframeTradeDisposition.ACTIONABLE,
            reason=(
                MultiTimeframeTradeReason.BULLISH_VERDICT_AND_DAILY_SETUP
            ),
            daily_planning_result=planning,
            trade_decision=TradeDecisionOutcome(
                market_condition=MarketCondition.BULLISH,
                decision=TradeDecision.BUY,
                rationale=rationale,
            ),
            rationale=rationale,
        )


def _validated_chain(technical_review, debate_result):
    if not isinstance(technical_review, MultiTimeframeEvidenceReview):
        raise ValueError("trade planning requires a technical review")
    if not isinstance(debate_result, AgenticDebateResult):
        raise ValueError("trade planning requires a debate result")
    package = technical_review.released_evidence
    if package is None or not debate_result.decision.accepted:
        raise AgentSubmissionRejectedError(
            "trade planning requires released evidence and an approved debate"
        )
    submission = debate_result.submission
    if (
        submission.technical_submission_id != package.package_fingerprint
        or submission.technical_decision_id
        != technical_review.decision.decision_id
    ):
        raise AgentSubmissionRejectedError(
            "trade-planning inputs do not belong to the same review chain"
        )
    return package


def _daily_projection_decision_id(
    technical_review: MultiTimeframeEvidenceReview,
) -> str:
    return f"{technical_review.decision.decision_id}:daily_trade_projection"


def _condition_no_trade_reason(
    condition: MarketCondition,
    *,
    judge_rejected: bool = False,
) -> NoTradeReason:
    mapping = {
        MarketCondition.BEARISH: NoTradeReason.BEARISH_CONDITION,
        MarketCondition.NEUTRAL: NoTradeReason.NEUTRAL_CONDITION,
        MarketCondition.CONFLICTED: NoTradeReason.TIMEFRAME_CONFLICT,
        MarketCondition.INSUFFICIENT: NoTradeReason.INSUFFICIENT_DATA,
    }
    if condition is MarketCondition.BULLISH and judge_rejected:
        return NoTradeReason.JUDGE_REJECTED_BULLISH_CASE
    try:
        return mapping[condition]
    except KeyError as error:
        raise ValueError(
            "bullish market condition requires a bullish Judge verdict"
        ) from error


def _planner_no_trade_reason(
    reason: TradePlanningReason,
) -> NoTradeReason:
    mapping = {
        TradePlanningReason.MINIMUM_TARGET_BLOCKED: (
            NoTradeReason.MINIMUM_TARGET_BLOCKED
        ),
        TradePlanningReason.INSUFFICIENT_STOP_EVIDENCE: (
            NoTradeReason.STRUCTURAL_STOP_UNAVAILABLE
        ),
        TradePlanningReason.NON_DIRECTIONAL_PROFILE: (
            NoTradeReason.DAILY_TRIGGER_INCOMPLETE
        ),
    }
    try:
        return mapping[reason]
    except KeyError as error:
        raise ValueError(
            "planner no-trade outcome has an unsupported reason"
        ) from error
