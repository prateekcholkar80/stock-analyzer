from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from app.agents._debate_support import (
    build_system_prompt,
    generate_grounded,
    serialize_evidence,
    serialize_transcript,
    valid_evidence_ids,
)
from app.llm.client import LLMClient, LLMGenerationConfig
from app.models.debate import DebateTranscript, DebateVerdict
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


class _VerdictDraft(BaseModel):
    winner: SignalDirection
    confidence_percentage: float = Field(ge=0, le=100)
    decisive_evidence_ids: list[str] = Field(min_length=1)
    bull_case_summary: str = Field(min_length=1)
    bear_case_summary: str = Field(min_length=1)
    rationale: str = Field(min_length=1)


class DebateJudgeAgentConfig(TechnicalModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    generation: LLMGenerationConfig = Field(
        default_factory=lambda: LLMGenerationConfig(
            model="anthropic/claude-sonnet-4-5",
            temperature=0.0,
            max_tokens=600,
        )
    )


class DebateJudgeAgent:
    """The LLM-as-judge: renders the substantive verdict on a debate."""

    agent_id = "jarvis.debate_judge_agent.v1"

    def __init__(
        self,
        config: DebateJudgeAgentConfig | None = None,
        client: LLMClient | None = None,
    ) -> None:
        if config is not None and not isinstance(
            config,
            DebateJudgeAgentConfig,
        ):
            raise ValueError(
                "debate judge agent config must be a validated "
                "configuration"
            )
        self.config = config or DebateJudgeAgentConfig()
        self._client = client or LLMClient()

    @property
    def configuration_fingerprint(self) -> str:
        serialized = self.config.model_dump_json()
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
        draft = generate_grounded(
            client=self._client,
            config=self.config.generation,
            system=_SYSTEM_PROMPT,
            context=context,
            draft_model=_VerdictDraft,
            valid_ids=valid_evidence_ids(profile),
            citation_field="decisive_evidence_ids",
        )
        return DebateVerdict(
            verdict_id=f"{self.agent_id}:{technical_submission_id}:verdict",
            judge_model_id=self.config.generation.model,
            winner=draft.winner,
            confidence_percentage=draft.confidence_percentage,
            decisive_evidence_ids=tuple(draft.decisive_evidence_ids),
            bull_case_summary=draft.bull_case_summary,
            bear_case_summary=draft.bear_case_summary,
            rationale=draft.rationale,
            generated_at=datetime.now(UTC),
        )
