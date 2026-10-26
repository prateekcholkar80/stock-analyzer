from math import isclose

from app.analytics.evidence_hierarchy import (
    assess_evidence_hierarchy,
    build_multi_timeframe_technical_findings,
)
from app.exceptions import AgentSubmissionRejectedError
from app.models.agentic import (
    AgenticSwingAnalysisResult,
    JarvisJudgeDecision,
    JarvisJudgeVerdict,
    TradePlanningReason,
)
from app.models.debate import AgenticDebateResult
from app.models.evidence_hierarchy import (
    EvidenceFamily,
    EvidenceFamilyFinding,
    EvidenceFamilyState,
    HierarchicalEvidenceAssessment,
)
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
        technical_findings = build_multi_timeframe_technical_findings(package)
        hierarchy = _hierarchy_assessment(
            technical_findings,
            entry_state=EvidenceFamilyState.INSUFFICIENT,
            entry_rationale=(
                "The deterministic entry stage has not been reached."
            ),
            risk_state=EvidenceFamilyState.INSUFFICIENT,
            risk_rationale=(
                "The deterministic stop and target stage has not been reached."
            ),
        )
        market_condition = hierarchy.primary_condition
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
                hierarchy_assessment=hierarchy,
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
                hierarchy_assessment=hierarchy,
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
            entry_state, risk_state = _planner_gate_states(
                planning.submission.reason
            )
            hierarchy = _hierarchy_assessment(
                technical_findings,
                entry_state=entry_state,
                entry_rationale=planning.submission.rationale,
                risk_state=risk_state,
                risk_rationale=planning.submission.rationale,
            )
            no_trade_reason = _planner_no_trade_reason(
                planning.submission.reason
            )
            return MultiTimeframeLongTradePlanResult(
                **common,
                hierarchy_assessment=hierarchy,
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
        hierarchy = _hierarchy_assessment(
            technical_findings,
            entry_state=EvidenceFamilyState.PASS,
            entry_rationale=planning.submission.rationale,
            risk_state=EvidenceFamilyState.PASS,
            risk_rationale=plan.evaluation.rationale,
        )
        if not hierarchy.release_eligible:
            raise AgentSubmissionRejectedError(
                "deterministic trade planning cannot bypass hierarchy gates"
            )
        rationale = (
            "The final Judge is bullish, the hierarchical primary context "
            "is bullish, and the deterministic planner found a "
            "structure-aware entry with a feasible 1:2 minimum target. "
            f"{hierarchy.rationale}"
        )
        return MultiTimeframeLongTradePlanResult(
            **common,
            hierarchy_assessment=hierarchy,
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


def _hierarchy_assessment(
    technical_findings: tuple[EvidenceFamilyFinding, ...],
    *,
    entry_state: EvidenceFamilyState,
    entry_rationale: str,
    risk_state: EvidenceFamilyState,
    risk_rationale: str,
) -> HierarchicalEvidenceAssessment:
    entry_evidence_ids = _family_evidence_ids(
        technical_findings,
        {
            EvidenceFamily.MARKET_STRUCTURE,
            EvidenceFamily.PRICE_LOCATION,
            EvidenceFamily.CPR_CONTEXT,
            EvidenceFamily.CANDLESTICK_CONTEXT,
        },
    )
    risk_evidence_ids = _family_evidence_ids(
        technical_findings,
        {
            EvidenceFamily.PRICE_LOCATION,
            EvidenceFamily.VOLATILITY_CONTEXT,
        },
    )
    gate_findings = (
        EvidenceFamilyFinding(
            family=EvidenceFamily.FUNDAMENTAL_HEALTH,
            state=EvidenceFamilyState.NOT_APPLICABLE,
            rationale=(
                "Fundamental health is a separate conclusion and is not "
                "evaluated by the technical trade-planning workflow."
            ),
        ),
        EvidenceFamilyFinding(
            family=EvidenceFamily.ENTRY_TRIGGER,
            state=entry_state,
            evidence_ids=entry_evidence_ids,
            rationale=entry_rationale,
        ),
        EvidenceFamilyFinding(
            family=EvidenceFamily.RISK_TARGET_FEASIBILITY,
            state=risk_state,
            evidence_ids=risk_evidence_ids,
            rationale=risk_rationale,
        ),
    )
    return assess_evidence_hierarchy(
        (*technical_findings, *gate_findings),
        not_applicable_families=frozenset(
            {EvidenceFamily.FUNDAMENTAL_HEALTH}
        ),
    )


def _family_evidence_ids(
    findings: tuple[EvidenceFamilyFinding, ...],
    families: set[EvidenceFamily],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            evidence_id
            for finding in findings
            if finding.family in families
            for evidence_id in finding.evidence_ids
        )
    )


def _planner_gate_states(
    reason: TradePlanningReason,
) -> tuple[EvidenceFamilyState, EvidenceFamilyState]:
    if reason is TradePlanningReason.NON_DIRECTIONAL_PROFILE:
        return EvidenceFamilyState.FAIL, EvidenceFamilyState.INSUFFICIENT
    if reason in {
        TradePlanningReason.MINIMUM_TARGET_BLOCKED,
        TradePlanningReason.INSUFFICIENT_STOP_EVIDENCE,
    }:
        return EvidenceFamilyState.PASS, EvidenceFamilyState.FAIL
    raise ValueError("unsupported planner reason for hierarchy gates")


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
