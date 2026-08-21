from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from app.agents._debate_support import (
    build_system_prompt,
    generate_grounded,
    serialize_evidence,
    serialize_precedent,
    serialize_transcript,
    valid_evidence_ids,
)
from app.llm.client import LLMClient, LLMGenerationConfig
from app.models.debate import BullBearArgument, DebateSide
from app.models.signals import SwingTradingSignalProfile
from app.models.storage import DebateRunSummary
from app.models.technical import TechnicalModel


_ROLE = (
    "You are the Bear Agent in a structured trading debate -- channel the "
    "mindset of India's sharpest skeptical bears: forensic, contrarian, "
    "distrustful of narrative-driven rallies, and quick to flag "
    "deteriorating momentum, weakening breadth, or overextension the "
    "crowd is choosing to ignore. You don't argue from pessimism for its "
    "own sake -- you argue from discipline, treating every bullish "
    "narrative as a claim that must survive scrutiny of the actual "
    "evidence before it's believed."
)

_RULES = (
    "Argue the bearish case for this symbol using ONLY the technical "
    "evidence given to you in the Context section -- never invent data, "
    "prices, or indicators that are not listed there. Every claim you "
    "make must cite at least one evidence id from that list. The Bull "
    "agent has just argued -- directly rebut its strongest point by "
    "setting rebuts_argument_id to that argument's id. If precedent from "
    "past debates is included in Context, treat it purely as background "
    "color for phrasing or framing -- it carries no evidentiary weight, "
    "cannot be cited in place of real evidence, and must never make you "
    "argue harder or with more confidence than the current evidence "
    "alone justifies. If the Feedback section flags a problem with your "
    "previous attempt, correct exactly that problem."
)

_SYSTEM_PROMPT = build_system_prompt(role=_ROLE, rules=_RULES)


class _ArgumentDraft(BaseModel):
    thesis: str = Field(min_length=1)
    evidence_citations: list[str] = Field(min_length=1)
    rebuts_argument_id: str | None = None


class BearDebateAgentConfig(TechnicalModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    generation: LLMGenerationConfig = Field(
        default_factory=lambda: LLMGenerationConfig(
            model="anthropic/claude-sonnet-4-5",
            temperature=0.4,
            max_tokens=800,
        )
    )


class BearDebateAgent:
    """Own the LLM-driven bearish argument for one debate round."""

    agent_id = "jarvis.bear_debate_agent.v1"

    def __init__(
        self,
        config: BearDebateAgentConfig | None = None,
        client: LLMClient | None = None,
    ) -> None:
        if config is not None and not isinstance(
            config,
            BearDebateAgentConfig,
        ):
            raise ValueError(
                "bear debate agent config must be a validated configuration"
            )
        self.config = config or BearDebateAgentConfig()
        self._client = client or LLMClient()

    @property
    def configuration_fingerprint(self) -> str:
        serialized = self.config.model_dump_json()
        return sha256(serialized.encode("utf-8")).hexdigest()

    def generate_argument(
        self,
        *,
        profile: SwingTradingSignalProfile,
        transcript_so_far: tuple[BullBearArgument, ...],
        round_number: int,
        technical_submission_id: str,
        precedent: tuple[DebateRunSummary, ...] = (),
    ) -> BullBearArgument:
        precedent_text = serialize_precedent(precedent)
        precedent_section = f"\n\n{precedent_text}" if precedent_text else ""
        context = (
            "Available technical evidence:\n"
            f"{serialize_evidence(profile)}\n\n"
            "Debate so far:\n"
            f"{serialize_transcript(transcript_so_far)}"
            f"{precedent_section}\n\n"
            f"This is round {round_number}. Present the bear case."
        )
        draft = generate_grounded(
            client=self._client,
            config=self.config.generation,
            system=_SYSTEM_PROMPT,
            context=context,
            draft_model=_ArgumentDraft,
            valid_ids=valid_evidence_ids(profile),
            citation_field="evidence_citations",
        )
        return BullBearArgument(
            argument_id=(
                f"{self.agent_id}:{technical_submission_id}:"
                f"{round_number}"
            ),
            side=DebateSide.BEAR,
            round_number=round_number,
            thesis=draft.thesis,
            evidence_citations=tuple(draft.evidence_citations),
            rebuts_argument_id=draft.rebuts_argument_id,
            model_id=self.config.generation.model,
            generated_at=datetime.now(UTC),
        )
