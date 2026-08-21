import json
from hashlib import sha256
from typing import Protocol, runtime_checkable

from pydantic import ConfigDict, Field

from app.agents.bear_agent import BearDebateAgent
from app.agents.bull_agent import BullDebateAgent
from app.agents.debate_judge_agent import DebateJudgeAgent
from app.exceptions import AgentSubmissionRejectedError, LLMResponseValidationError
from app.models.agentic import (
    AgenticSwingAnalysisResult,
    JarvisJudgeDecision,
    JarvisJudgeVerdict,
)
from app.models.debate import (
    AgenticDebateResult,
    BullBearArgument,
    BullBearDebateSubmission,
    DebateRound,
    DebateTerminationReason,
    DebateVerdict,
)
from app.models.signals import SignalDirection, SwingTradingSignalProfile
from app.models.storage import DebateRunSummary, StoredDebateRun
from app.models.technical import TechnicalModel
from app.orchestration.agent_orchestrator import (
    _validate_fingerprint,
    _validate_identifier,
)
from app.orchestration.debate_session import DebateSession


SUBMISSION_AGENT_ID = "jarvis.bull_bear_debate.v1"


@runtime_checkable
class DebateArchive(Protocol):
    """Narrow port for fetching precedent and persisting a debate result."""

    def find_similar_debate_runs(
        self,
        profile: SwingTradingSignalProfile,
        *,
        exclude_run_id: str | None = None,
        limit: int = 5,
    ) -> tuple[DebateRunSummary, ...]:
        ...

    def archive_debate_run(
        self,
        result: AgenticDebateResult,
        profile: SwingTradingSignalProfile,
        *,
        run_id: str | None = None,
        stored_at=None,
    ) -> StoredDebateRun:
        ...


class DebateOrchestratorConfig(TechnicalModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    max_rounds: int = Field(default=3, ge=1)
    indecisive_confidence_threshold: float = Field(default=55.0, gt=0, le=100)
    precedent_limit: int = Field(default=5, ge=0)


class JarvisDebateJudge:
    """Structurally audit a Bull/Bear debate submission before acceptance.

    Never judges debate quality -- only that citations are real, the
    configuration/chain matches what was assigned, and the transcript is
    internally consistent. The substantive verdict comes from
    ``DebateJudgeAgent``.
    """

    judge_id = "jarvis.debate_judge.v1"

    def __init__(
        self,
        *,
        expected_agent_id: str,
        expected_configuration_fingerprint: str,
        max_rounds: int,
    ) -> None:
        self.expected_agent_id = _validate_identifier(
            "expected debate submission",
            expected_agent_id,
        )
        self.expected_configuration_fingerprint = _validate_fingerprint(
            "expected debate configuration",
            expected_configuration_fingerprint,
        )
        if isinstance(max_rounds, bool) or not isinstance(max_rounds, int) or max_rounds < 1:
            raise ValueError("expected debate max rounds must be a positive integer")
        self.max_rounds = max_rounds

    def review(
        self,
        submission: BullBearDebateSubmission,
        technical_result: AgenticSwingAnalysisResult,
    ) -> JarvisJudgeDecision:
        if not isinstance(submission, BullBearDebateSubmission):
            raise ValueError(
                "Jarvis debate judge requires a debate submission"
            )
        submission = BullBearDebateSubmission.model_validate(
            submission.model_dump(exclude_computed_fields=True)
        )
        technical_result = AgenticSwingAnalysisResult.model_validate(
            technical_result.model_dump(exclude_computed_fields=True)
        )

        passed_checks = ["debate_submission_schema_valid"]
        reasons: list[str] = []

        if submission.agent_id == self.expected_agent_id:
            passed_checks.append("expected_debate_agent_identity")
        else:
            reasons.append(
                "submission came from an unexpected debate process"
            )

        if (
            submission.configuration_fingerprint
            == self.expected_configuration_fingerprint
        ):
            passed_checks.append("expected_debate_configuration")
        else:
            reasons.append(
                "submission used an unexpected debate configuration"
            )

        if technical_result.decision.accepted:
            passed_checks.append("technical_submission_approved")
        else:
            reasons.append(
                "debate is based on an unapproved technical result"
            )

        if (
            submission.technical_submission_id
            == technical_result.submission.submission_id
            and submission.technical_decision_id
            == technical_result.decision.decision_id
        ):
            passed_checks.append("technical_review_chain")
        else:
            reasons.append(
                "debate submission does not reference the assigned "
                "technical review"
            )

        if submission.input_fingerprint == profile_fingerprint(
            technical_result.submission.profile
        ):
            passed_checks.append("exact_technical_profile_fingerprint")
        else:
            reasons.append(
                "debate submission does not match the approved "
                "technical profile"
            )

        valid_evidence_ids = {
            item.evidence_id
            for item in technical_result.submission.profile.snapshot.evidence
        }
        cited_evidence_ids = {
            citation
            for debate_round in submission.transcript.rounds
            for argument in (
                debate_round.bull_argument,
                debate_round.bear_argument,
            )
            for citation in argument.evidence_citations
        }
        if cited_evidence_ids.issubset(valid_evidence_ids):
            passed_checks.append("evidence_citations_grounded")
        else:
            reasons.append(
                "debate arguments cite evidence that does not exist in "
                "the technical profile"
            )

        if set(submission.verdict.decisive_evidence_ids).issubset(
            valid_evidence_ids
        ):
            passed_checks.append("verdict_citations_grounded")
        else:
            reasons.append(
                "debate verdict cites evidence that does not exist in "
                "the technical profile"
            )

        round_count = submission.transcript.round_count
        if submission.transcript.termination_reason is (
            DebateTerminationReason.MAX_ROUNDS_REACHED
        ):
            round_count_valid = round_count == self.max_rounds
        else:
            round_count_valid = 1 <= round_count <= self.max_rounds
        if round_count_valid:
            passed_checks.append("round_count_consistent_with_termination")
        else:
            reasons.append(
                "debate round count is not consistent with its "
                "termination reason"
            )

        if _chronological(submission):
            passed_checks.append("chronological_debate_sequence")
        else:
            reasons.append(
                "debate artifacts are not in chronological order"
            )

        verdict = (
            JarvisJudgeVerdict.REJECTED
            if reasons
            else JarvisJudgeVerdict.ACCEPTED
        )
        return JarvisJudgeDecision(
            decision_id=f"{self.judge_id}:{submission.submission_id}",
            judge_id=self.judge_id,
            submission_id=submission.submission_id,
            verdict=verdict,
            decided_at=submission.evaluated_at,
            passed_checks=tuple(passed_checks),
            reasons=tuple(reasons),
        )


class DebateOrchestrator:
    """Run a bounded Bull/Bear debate and enforce Jarvis review."""

    orchestrator_id = "jarvis.debate_orchestrator.v1"

    def __init__(
        self,
        bull_agent: BullDebateAgent | None = None,
        bear_agent: BearDebateAgent | None = None,
        judge_agent: DebateJudgeAgent | None = None,
        structural_judge: JarvisDebateJudge | None = None,
        config: DebateOrchestratorConfig | None = None,
        archive: DebateArchive | None = None,
    ) -> None:
        self.bull_agent = bull_agent or BullDebateAgent()
        _validate_debate_side_agent("bull", self.bull_agent)
        self.bear_agent = bear_agent or BearDebateAgent()
        _validate_debate_side_agent("bear", self.bear_agent)
        self.judge_agent = judge_agent or DebateJudgeAgent()
        _validate_judge_agent(self.judge_agent)
        self.config = config or DebateOrchestratorConfig()
        self.structural_judge = structural_judge or JarvisDebateJudge(
            expected_agent_id=SUBMISSION_AGENT_ID,
            expected_configuration_fingerprint=(
                self.configuration_fingerprint
            ),
            max_rounds=self.config.max_rounds,
        )
        if not callable(getattr(self.structural_judge, "review", None)):
            raise ValueError(
                "debate orchestrator judge must provide review()"
            )
        if archive is not None and not isinstance(archive, DebateArchive):
            raise ValueError(
                "debate orchestrator archive must implement DebateArchive"
            )
        self.archive = archive

    @property
    def configuration_fingerprint(self) -> str:
        payload = {
            "orchestrator_config": self.config.model_dump(mode="json"),
            "bull": self.bull_agent.configuration_fingerprint,
            "bear": self.bear_agent.configuration_fingerprint,
            "judge": self.judge_agent.configuration_fingerprint,
        }
        serialized = json.dumps(payload, sort_keys=True)
        return sha256(serialized.encode("utf-8")).hexdigest()

    def run_debate(
        self,
        technical_result: AgenticSwingAnalysisResult,
    ) -> AgenticDebateResult:
        if not isinstance(technical_result, AgenticSwingAnalysisResult):
            raise ValueError(
                "debate orchestrator requires an agentic swing result"
            )
        technical_result = AgenticSwingAnalysisResult.model_validate(
            technical_result.model_dump(exclude_computed_fields=True)
        )
        if not technical_result.decision.accepted:
            raise AgentSubmissionRejectedError(
                "debate orchestrator requires a Jarvis-approved technical "
                "submission"
            )

        profile = technical_result.submission.profile
        technical_submission_id = technical_result.submission.submission_id

        # Precedent goes to Bull/Bear only, to help them argue -- never to
        # the judge, so the verdict is grounded solely in this debate.
        precedent: tuple[DebateRunSummary, ...] = ()
        if self.archive is not None and self.config.precedent_limit > 0:
            precedent = self.archive.find_similar_debate_runs(
                profile,
                exclude_run_id=None,
                limit=self.config.precedent_limit,
            )

        session = DebateSession()
        flat_arguments: tuple[BullBearArgument, ...] = ()

        for round_number in range(1, self.config.max_rounds + 1):
            try:
                bull_argument = self.bull_agent.generate_argument(
                    profile=profile,
                    transcript_so_far=flat_arguments,
                    round_number=round_number,
                    technical_submission_id=technical_submission_id,
                    precedent=precedent,
                )
                flat_arguments = (*flat_arguments, bull_argument)
                bear_argument = self.bear_agent.generate_argument(
                    profile=profile,
                    transcript_so_far=flat_arguments,
                    round_number=round_number,
                    technical_submission_id=technical_submission_id,
                    precedent=precedent,
                )
                flat_arguments = (*flat_arguments, bear_argument)
            except LLMResponseValidationError:
                session.mark_agent_failure()
                break

            debate_round = DebateRound(
                round_number=round_number,
                bull_argument=bull_argument,
                bear_argument=bear_argument,
            )
            session.record_round(debate_round)

            if len(session.rounds) >= 2 and _is_stalled(
                session.rounds[-2],
                session.rounds[-1],
            ):
                session.mark_stalled()
                break
        else:
            session.mark_max_rounds_reached()

        if not session.rounds:
            raise AgentSubmissionRejectedError(
                "debate orchestrator could not complete a single round "
                "before an agent failure"
            )

        transcript = session.to_transcript()
        verdict = self.judge_agent.render_verdict(
            profile=profile,
            transcript=transcript,
            technical_submission_id=technical_submission_id,
        )
        verdict = _normalize_verdict(
            verdict,
            termination_reason=transcript.termination_reason,
            indecisive_confidence_threshold=(
                self.config.indecisive_confidence_threshold
            ),
        )
        session.attach_verdict(verdict)

        input_fingerprint = profile_fingerprint(profile)
        submission = BullBearDebateSubmission(
            submission_id=(
                f"{SUBMISSION_AGENT_ID}:{technical_submission_id}:"
                f"{input_fingerprint}"
            ),
            agent_id=SUBMISSION_AGENT_ID,
            technical_submission_id=technical_submission_id,
            technical_decision_id=technical_result.decision.decision_id,
            input_fingerprint=input_fingerprint,
            configuration_fingerprint=self.configuration_fingerprint,
            transcript=transcript,
            verdict=verdict,
            evaluated_at=verdict.generated_at,
        )
        decision = self.structural_judge.review(submission, technical_result)
        result = AgenticDebateResult(
            orchestrator_id=self.orchestrator_id,
            submission=submission,
            decision=decision,
        )
        if self.archive is not None and result.decision.accepted:
            self.archive.archive_debate_run(result, profile)
        return result

    def require_approved_debate_verdict(
        self,
        technical_result: AgenticSwingAnalysisResult,
    ) -> DebateVerdict:
        result = self.run_debate(technical_result)
        if not result.decision.accepted:
            reasons = "; ".join(result.decision.reasons)
            raise AgentSubmissionRejectedError(
                f"Jarvis rejected debate submission: {reasons}"
            )
        return result.submission.verdict


def profile_fingerprint(profile: SwingTradingSignalProfile) -> str:
    """Stable digest for the exact technical profile a debate consumed."""
    serialized = profile.model_dump_json()
    return sha256(serialized.encode("utf-8")).hexdigest()


def _is_stalled(
    previous_round: DebateRound,
    current_round: DebateRound,
) -> bool:
    return (
        set(current_round.bull_argument.evidence_citations)
        == set(previous_round.bull_argument.evidence_citations)
        and set(current_round.bear_argument.evidence_citations)
        == set(previous_round.bear_argument.evidence_citations)
    )


def _normalize_verdict(
    verdict: DebateVerdict,
    *,
    termination_reason: DebateTerminationReason,
    indecisive_confidence_threshold: float,
) -> DebateVerdict:
    """Deterministically force an indecisive outcome Jarvis can trust.

    The judge agent's own prompt already asks it to return a neutral,
    low-confidence winner when neither side clearly won -- but that is
    only a suggestion to an LLM, not a guarantee. This is the structural
    backstop: a debate that went in circles (STALL_DETECTED) or that
    couldn't even complete (AGENT_FAILURE) is never allowed to produce a
    directional winner, no matter what the judge said, and any winner
    whose confidence doesn't clear the configured threshold is forced to
    neutral regardless of termination reason.
    """
    forced_reason: str | None = None
    effective_confidence = verdict.confidence_percentage

    if termination_reason is DebateTerminationReason.STALL_DETECTED:
        effective_confidence = 0.0
        forced_reason = "the debate stalled without either side citing new evidence"
    elif termination_reason is DebateTerminationReason.AGENT_FAILURE:
        effective_confidence = 0.0
        forced_reason = "the debate ended early because an agent failed"
    elif verdict.confidence_percentage < indecisive_confidence_threshold:
        forced_reason = (
            "the judge's confidence "
            f"({verdict.confidence_percentage:g}%) did not clear the "
            f"indecisive threshold ({indecisive_confidence_threshold:g}%)"
        )

    if forced_reason is None:
        return verdict

    if (
        verdict.winner is SignalDirection.NEUTRAL
        and effective_confidence == verdict.confidence_percentage
    ):
        return verdict

    return DebateVerdict(
        verdict_id=verdict.verdict_id,
        judge_model_id=verdict.judge_model_id,
        winner=SignalDirection.NEUTRAL,
        confidence_percentage=effective_confidence,
        decisive_evidence_ids=verdict.decisive_evidence_ids,
        bull_case_summary=verdict.bull_case_summary,
        bear_case_summary=verdict.bear_case_summary,
        rationale=f"{verdict.rationale} [Forced indecisive: {forced_reason}.]",
        generated_at=verdict.generated_at,
    )


def _chronological(submission: BullBearDebateSubmission) -> bool:
    previous_timestamp = None
    for debate_round in submission.transcript.rounds:
        for argument in (
            debate_round.bull_argument,
            debate_round.bear_argument,
        ):
            if (
                previous_timestamp is not None
                and argument.generated_at < previous_timestamp
            ):
                return False
            previous_timestamp = argument.generated_at
    return submission.verdict.generated_at >= previous_timestamp


def _validate_debate_side_agent(label: str, agent) -> None:
    if not callable(getattr(agent, "generate_argument", None)):
        raise ValueError(
            f"debate orchestrator requires a {label} agent with "
            "generate_argument()"
        )
    _validate_identifier(
        f"{label} debate agent",
        getattr(agent, "agent_id", None),
    )
    _validate_fingerprint(
        f"{label} debate agent configuration",
        getattr(agent, "configuration_fingerprint", None),
    )


def _validate_judge_agent(agent) -> None:
    if not callable(getattr(agent, "render_verdict", None)):
        raise ValueError(
            "debate orchestrator requires a judge agent with "
            "render_verdict()"
        )
    _validate_identifier(
        "debate judge agent",
        getattr(agent, "agent_id", None),
    )
    _validate_fingerprint(
        "debate judge agent configuration",
        getattr(agent, "configuration_fingerprint", None),
    )
