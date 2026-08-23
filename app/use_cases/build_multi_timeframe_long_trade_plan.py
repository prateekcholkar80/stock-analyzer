from math import isclose

from app.exceptions import AgentSubmissionRejectedError
from app.models.agentic import (
    AgenticSwingAnalysisResult,
    JarvisJudgeDecision,
    JarvisJudgeVerdict,
)
from app.models.debate import AgenticDebateResult
from app.models.multi_timeframe_evidence import MultiTimeframeEvidenceReview
from app.models.multi_timeframe_trade import (
    MultiTimeframeLongTradePlanResult,
    MultiTimeframeTradeDisposition,
    MultiTimeframeTradeReason,
)
from app.models.signals import SignalDirection
from app.orchestration.agent_orchestrator import AgentOrchestrator


class BuildMultiTimeframeLongTradePlan:
    """Apply a long-only 2R policy after the multi-timeframe Judge verdict."""

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
        common = {
            "technical_package_fingerprint": package.package_fingerprint,
            "technical_decision_id": technical_review.decision.decision_id,
            "debate_verdict_id": verdict.verdict_id,
        }

        if verdict.winner is not SignalDirection.BULLISH:
            return MultiTimeframeLongTradePlanResult(
                **common,
                disposition=MultiTimeframeTradeDisposition.NO_TRADE,
                reason=MultiTimeframeTradeReason.JUDGE_NOT_BULLISH,
                rationale=(
                    "No long trade was proposed because the final Judge "
                    f"verdict is {verdict.winner.value}."
                ),
            )

        analysis = package.technical_analysis
        daily_submission = analysis.daily_submission
        if daily_submission.profile.direction is not SignalDirection.BULLISH:
            return MultiTimeframeLongTradePlanResult(
                **common,
                disposition=MultiTimeframeTradeDisposition.NO_TRADE,
                reason=MultiTimeframeTradeReason.DAILY_PROFILE_NOT_BULLISH,
                rationale=(
                    "No long trade was proposed because the Judge is bullish "
                    "but the daily technical profile is not bullish enough "
                    "to define a long entry."
                ),
            )

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
            return MultiTimeframeLongTradePlanResult(
                **common,
                disposition=MultiTimeframeTradeDisposition.NO_TRADE,
                reason=(
                    MultiTimeframeTradeReason.DETERMINISTIC_PLANNER_NO_TRADE
                ),
                daily_planning_result=planning,
                rationale=planning.submission.rationale,
            )
        return MultiTimeframeLongTradePlanResult(
            **common,
            disposition=MultiTimeframeTradeDisposition.ACTIONABLE,
            reason=(
                MultiTimeframeTradeReason.BULLISH_VERDICT_AND_DAILY_SETUP
            ),
            daily_planning_result=planning,
            rationale=(
                "The final Judge and daily profile are bullish, and the "
                "deterministic planner found a structure-aware long setup "
                "with a feasible 1:2 minimum target."
            ),
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
