from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from app.agents._debate_support import (
    build_system_prompt,
    generate_grounded,
    serialize_evidence,
    serialize_multi_timeframe_evidence,
    serialize_transcript,
    valid_evidence_ids,
    valid_multi_timeframe_evidence_ids,
)
from app.llm.gateway import StructuredLLMGateway
from app.models.debate import (
    DebateTranscript,
    DebateVerdict,
    JudgeFollowUpAnswer,
)
from app.models.multi_timeframe_evidence import MultiTimeframeEvidenceReview
from app.models.signals import SignalDirection, SwingTradingSignalProfile
from app.models.technical import TechnicalModel


_ROLE = (
    "You are a neutral judge for a Bull vs Bear trading debate -- "
    "dispassionate and evidence-driven, with no stake in either side "
    "winning."
)

_RULES = (
    "Read the full transcript and the underlying technical evidence "
    "given to you in the Context section. First, write bull_case_summary "
    "and bear_case_summary: one to two sentences each, summarizing that "
    "side's single strongest argument, grounded ONLY in evidence it "
    "actually cited during the debate -- never introduce a claim neither "
    "side made. Then render a verdict using ONLY evidence ids that were "
    "actually cited during the debate -- never invent data or evidence. "
    "Weigh which side grounded its argument in stronger, "
    "less-contradicted evidence. If neither side made a clearly stronger "
    "case, return a neutral winner with low confidence rather than "
    "forcing a decision. You are shown only this debate's transcript and "
    "evidence -- there is no history of past debates to weigh, by "
    "design, so your verdict is grounded solely in what was argued here. "
    "If the Feedback section flags a problem with your previous attempt, "
    "correct exactly that problem."
)

_SYSTEM_PROMPT = build_system_prompt(role=_ROLE, rules=_RULES)

_MULTI_TIMEFRAME_RULES = (
    _RULES
    + " Evaluate WEEKLY as structural direction and DAILY as swing-entry "
    "timing. Explicitly explain whether the timeframes are aligned, "
    "conflicted, mixed, or insufficient. A bullish weekly structure does "
    "not erase weak daily timing, and weak daily timing does not rewrite "
    "weekly facts. Use only qualified daily: or weekly: ids exactly as "
    "provided. Do not create entry, stop, target, or financial facts."
)
_MULTI_TIMEFRAME_SYSTEM_PROMPT = build_system_prompt(
    role=_ROLE,
    rules=_MULTI_TIMEFRAME_RULES,
)

_FOLLOW_UP_RULES = (
    "Answer the CEO's follow-up using ONLY the Judge-approved technical "
    "evidence in Context. Be concise, candid, and precise. Do not rerun "
    "analysis, change the completed evidence, or invent a missing level. "
    "For support, resistance, pivot, indicator, or timeframe claims, cite "
    "the exact qualified daily: or weekly: id. If the requested fact is "
    "unavailable, say so plainly and cite the closest relevant evidence "
    "that establishes the limitation. This is research, not guaranteed "
    "investment advice. Correct only errors named in Feedback. answer is "
    "also read aloud by a text-to-speech engine, so punctuate it as "
    "natural spoken prose: complete sentences separated by periods, with "
    "commas where a speaker would naturally pause. Never use bullet "
    "points, numbered lists, Markdown, emojis, or asterisks in answer, "
    "since those either get read aloud literally or are silently dropped "
    "by speech synthesis."
)
_FOLLOW_UP_SYSTEM_PROMPT = build_system_prompt(
    role=(
        "You are the same senior technical Judge who reviewed the Bull vs "
        "Bear analysis. Explain the approved evidence to Jarvis as if Jarvis "
        "will brief the CEO."
    ),
    rules=_FOLLOW_UP_RULES,
)


class _VerdictDraft(BaseModel):
    winner: SignalDirection
    confidence_percentage: float = Field(ge=0, le=100)
    decisive_evidence_ids: list[str] = Field(min_length=1)
    bull_case_summary: str = Field(min_length=1)
    bear_case_summary: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class _FollowUpDraft(BaseModel):
    answer: str = Field(min_length=1)
    evidence_citations: list[str] = Field(min_length=1)


class DebateJudgeAgentConfig(TechnicalModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    prompt_version: str = Field(
        default="jarvis.debate_judge_prompt.v1",
        min_length=1,
        pattern=r".*\S.*",
    )


class DebateJudgeAgent:
    """The LLM-as-judge: renders the substantive verdict on a debate."""

    agent_id = "jarvis.debate_judge_agent.v1"

    def __init__(
        self,
        gateway: StructuredLLMGateway,
        config: DebateJudgeAgentConfig | None = None,
    ) -> None:
        if not isinstance(gateway, StructuredLLMGateway):
            raise ValueError(
                "debate judge agent requires a structured LLM gateway"
            )
        if config is not None and not isinstance(
            config,
            DebateJudgeAgentConfig,
        ):
            raise ValueError(
                "debate judge agent config must be a validated "
                "configuration"
            )
        self.config = config or DebateJudgeAgentConfig()
        self._gateway = gateway

    @property
    def configuration_fingerprint(self) -> str:
        serialized = (
            f"{self.config.model_dump_json()}:"
            f"{self._gateway.configuration_fingerprint}"
        )
        return sha256(serialized.encode("utf-8")).hexdigest()

    def render_verdict(
        self,
        *,
        profile: SwingTradingSignalProfile,
        transcript: DebateTranscript,
        technical_submission_id: str,
    ) -> DebateVerdict:
        flat_arguments = tuple(
            argument
            for debate_round in transcript.rounds
            for argument in (
                debate_round.bull_argument,
                debate_round.bear_argument,
            )
        )
        context = (
            "Available technical evidence:\n"
            f"{serialize_evidence(profile)}\n\n"
            "Full debate transcript:\n"
            f"{serialize_transcript(flat_arguments)}\n\n"
            "Render your verdict."
        )
        generation = generate_grounded(
            gateway=self._gateway,
            system=_SYSTEM_PROMPT,
            context=context,
            draft_model=_VerdictDraft,
            valid_ids=valid_evidence_ids(profile),
            citation_field="decisive_evidence_ids",
        )
        draft = generation.value
        return DebateVerdict(
            verdict_id=f"{self.agent_id}:{technical_submission_id}:verdict",
            judge_model_id=generation.model,
            winner=draft.winner,
            confidence_percentage=draft.confidence_percentage,
            decisive_evidence_ids=tuple(draft.decisive_evidence_ids),
            bull_case_summary=draft.bull_case_summary,
            bear_case_summary=draft.bear_case_summary,
            rationale=draft.rationale,
            generated_at=datetime.now(UTC),
        )

    def render_multi_timeframe_verdict(
        self,
        *,
        technical_review: MultiTimeframeEvidenceReview,
        transcript: DebateTranscript,
    ) -> DebateVerdict:
        package = technical_review.released_evidence
        if package is None:
            raise ValueError(
                "Judge requires released multi-timeframe evidence"
            )
        flat_arguments = tuple(
            argument
            for debate_round in transcript.rounds
            for argument in (
                debate_round.bull_argument,
                debate_round.bear_argument,
            )
        )
        context = (
            "Judge-approved multi-timeframe technical evidence:\n"
            f"{serialize_multi_timeframe_evidence(package)}\n\n"
            "Full debate transcript:\n"
            f"{serialize_transcript(flat_arguments)}\n\n"
            "Render the verdict and explicitly resolve any daily/weekly "
            "agreement or conflict."
        )
        generation = generate_grounded(
            gateway=self._gateway,
            system=_MULTI_TIMEFRAME_SYSTEM_PROMPT,
            context=context,
            draft_model=_VerdictDraft,
            valid_ids=valid_multi_timeframe_evidence_ids(package),
            citation_field="decisive_evidence_ids",
        )
        draft = generation.value
        return DebateVerdict(
            verdict_id=(
                f"{self.agent_id}:{package.package_fingerprint}:verdict"
            ),
            judge_model_id=generation.model,
            winner=draft.winner,
            confidence_percentage=draft.confidence_percentage,
            decisive_evidence_ids=tuple(draft.decisive_evidence_ids),
            bull_case_summary=draft.bull_case_summary,
            bear_case_summary=draft.bear_case_summary,
            rationale=draft.rationale,
            generated_at=datetime.now(UTC),
        )

    def answer_follow_up(
        self,
        *,
        question: str,
        technical_review: MultiTimeframeEvidenceReview,
        verdict: DebateVerdict | None = None,
    ) -> JudgeFollowUpAnswer:
        if not isinstance(question, str) or not question.strip():
            raise ValueError("Judge follow-up question cannot be blank")
        package = technical_review.released_evidence
        if package is None:
            raise ValueError(
                "Judge follow-up requires released multi-timeframe evidence"
            )
        verdict_context = (
            verdict.model_dump_json()
            if isinstance(verdict, DebateVerdict)
            else "(no completed debate verdict supplied)"
        )
        context = (
            "Judge-approved multi-timeframe technical evidence:\n"
            f"{serialize_multi_timeframe_evidence(package)}\n\n"
            "Completed verdict, if available:\n"
            f"{verdict_context}\n\n"
            "CEO follow-up question relayed by Jarvis:\n"
            f"{question.strip()}"
        )
        generation = generate_grounded(
            gateway=self._gateway,
            system=_FOLLOW_UP_SYSTEM_PROMPT,
            context=context,
            draft_model=_FollowUpDraft,
            valid_ids=valid_multi_timeframe_evidence_ids(package),
            citation_field="evidence_citations",
        )
        draft = generation.value
        return JudgeFollowUpAnswer(
            question=question,
            answer=draft.answer,
            evidence_citations=tuple(draft.evidence_citations),
            technical_package_fingerprint=package.package_fingerprint,
            debate_verdict_id=(
                verdict.verdict_id
                if isinstance(verdict, DebateVerdict)
                else None
            ),
            judge_agent_id=self.agent_id,
            model_id=generation.model,
            generated_at=datetime.now(UTC),
        )
