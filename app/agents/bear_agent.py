from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from app.agents._debate_support import (
    build_system_prompt,
    generate_grounded,
    serialize_evidence,
    serialize_multi_timeframe_evidence,
    serialize_precedent,
    serialize_transcript,
    valid_evidence_ids,
    valid_multi_timeframe_evidence_ids,
)
from app.llm.gateway import StructuredLLMGateway
from app.models.debate import (
    BullBearArgument,
    DebateSide,
    TimeframeRelationship,
)
from app.models.signals import SwingTradingSignalProfile
from app.models.storage import DebateRunSummary
from app.models.multi_timeframe_evidence import MultiTimeframeEvidenceReview
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

_MULTI_TIMEFRAME_RULES = (
    _RULES
    + " For multi-timeframe analysis, treat WEEKLY as structural context "
    "and DAILY as swing-entry timing. Explicitly classify their relationship "
    "as aligned, conflicted, mixed, or insufficient. Test the weekly thesis "
    "against daily confirmation and the daily thesis against weekly structure. "
    "Never manufacture conflict, but never hide it. Support, resistance, and "
    "pivot claims must cite their supplied qualified daily: or weekly: id. "
    "Use only qualified ids exactly as written."
)
_MULTI_TIMEFRAME_SYSTEM_PROMPT = build_system_prompt(
    role=_ROLE,
    rules=_MULTI_TIMEFRAME_RULES,
)


class _ArgumentDraft(BaseModel):
    thesis: str = Field(min_length=1)
    evidence_citations: list[str] = Field(min_length=1)
    rebuts_argument_id: str | None = None


class _MultiTimeframeArgumentDraft(_ArgumentDraft):
    timeframe_relationship: TimeframeRelationship


class BearDebateAgentConfig(TechnicalModel):
    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    prompt_version: str = Field(
        default="jarvis.bear_debate_prompt.v1",
        min_length=1,
        pattern=r".*\S.*",
    )


class BearDebateAgent:
    """Own the LLM-driven bearish argument for one debate round."""

    agent_id = "jarvis.bear_debate_agent.v1"

    def __init__(
        self,
        gateway: StructuredLLMGateway,
        config: BearDebateAgentConfig | None = None,
    ) -> None:
        if not isinstance(gateway, StructuredLLMGateway):
            raise ValueError(
                "bear debate agent requires a structured LLM gateway"
            )
        if config is not None and not isinstance(
            config,
            BearDebateAgentConfig,
        ):
            raise ValueError(
                "bear debate agent config must be a validated configuration"
            )
        self.config = config or BearDebateAgentConfig()
        self._gateway = gateway

    @property
    def configuration_fingerprint(self) -> str:
        serialized = (
            f"{self.config.model_dump_json()}:"
            f"{self._gateway.configuration_fingerprint}"
        )
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
        generation = generate_grounded(
            gateway=self._gateway,
            system=_SYSTEM_PROMPT,
            context=context,
            draft_model=_ArgumentDraft,
            valid_ids=valid_evidence_ids(profile),
            citation_field="evidence_citations",
        )
        draft = generation.value
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
            model_id=generation.model,
            generated_at=datetime.now(UTC),
        )

    def generate_multi_timeframe_argument(
        self,
        *,
        technical_review: MultiTimeframeEvidenceReview,
        transcript_so_far: tuple[BullBearArgument, ...],
        round_number: int,
    ) -> BullBearArgument:
        package = technical_review.released_evidence
        if package is None:
            raise ValueError(
                "bear agent requires Judge-released multi-timeframe evidence"
            )
        context = (
            "Judge-approved multi-timeframe technical evidence:\n"
            f"{serialize_multi_timeframe_evidence(package)}\n\n"
            "Debate so far:\n"
            f"{serialize_transcript(transcript_so_far)}\n\n"
            f"This is round {round_number}. Present the bear case and "
            "explicitly address the daily/weekly relationship."
        )
        generation = generate_grounded(
            gateway=self._gateway,
            system=_MULTI_TIMEFRAME_SYSTEM_PROMPT,
            context=context,
            draft_model=_MultiTimeframeArgumentDraft,
            valid_ids=valid_multi_timeframe_evidence_ids(package),
            citation_field="evidence_citations",
        )
        draft = generation.value
        return BullBearArgument(
            argument_id=(
                f"{self.agent_id}:{package.package_fingerprint}:"
                f"{round_number}"
            ),
            side=DebateSide.BEAR,
            round_number=round_number,
            thesis=draft.thesis,
            evidence_citations=tuple(draft.evidence_citations),
            rebuts_argument_id=draft.rebuts_argument_id,
            model_id=generation.model,
            generated_at=datetime.now(UTC),
            timeframe_relationship=draft.timeframe_relationship,
        )
