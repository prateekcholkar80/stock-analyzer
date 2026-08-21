from pydantic import BaseModel

from app.exceptions import LLMResponseValidationError
from app.llm.client import LLMClient, LLMGenerationConfig
from app.models.debate import BullBearArgument
from app.models.signals import SwingTradingSignalProfile
from app.models.storage import DebateRunSummary


def valid_evidence_ids(profile: SwingTradingSignalProfile) -> frozenset[str]:
    return frozenset(
        item.evidence_id for item in profile.snapshot.evidence
    )


def serialize_evidence(profile: SwingTradingSignalProfile) -> str:
    lines = [
        (
            f"- id={item.evidence_id} category={item.category.value} "
            f"direction={item.direction.value} "
            f"strength={item.strength.value} "
            f"observed_values={item.observed_values} "
            f"explanation={item.explanation}"
        )
        for item in profile.snapshot.evidence
    ]
    return "\n".join(lines)


def serialize_transcript(
    transcript_so_far: tuple[BullBearArgument, ...],
) -> str:
    if not transcript_so_far:
        return "(no prior arguments yet)"
    lines = []
    for argument in transcript_so_far:
        rebuttal_note = (
            f", rebuts={argument.rebuts_argument_id}"
            if argument.rebuts_argument_id
            else ""
        )
        lines.append(
            f"[round {argument.round_number} {argument.side.value}"
            f"{rebuttal_note}] citing {list(argument.evidence_citations)}: "
            f"{argument.thesis}"
        )
    return "\n".join(lines)


_NO_FEEDBACK_YET = "None yet -- this is the first attempt."


def build_system_prompt(*, role: str, rules: str) -> str:
    """Compose the two static, per-agent sections of the prompt."""
    return f"# Role\n{role}\n\n# System Prompt\n{rules}"


def build_user_message(
    *,
    context: str,
    feedback: str = _NO_FEEDBACK_YET,
) -> str:
    """Compose the two dynamic, per-call sections of the prompt.

    Feedback is always present, even when empty, rather than only
    appended when there's a correction -- the model always knows where
    to look for it.
    """
    return f"# Context\n{context}\n\n# Feedback\n{feedback}"


def serialize_precedent(
    summaries: tuple[DebateRunSummary, ...],
) -> str:
    """Format past similar debates as optional, non-authoritative context.

    Empty when there's no precedent -- callers should skip adding this to
    the prompt entirely in that case rather than include an empty section.
    """
    if not summaries:
        return ""
    lines = [
        "Precedent -- most similar past debates, for context only. This "
        "does not override the current evidence; weigh it only if the "
        "current symptoms genuinely match:",
    ]
    for summary in summaries:
        lines.append(
            f"- {summary.symbol} {summary.interval} "
            f"({summary.stored_at.date().isoformat()}): "
            f"verdict={summary.winner.value} "
            f"confidence={summary.confidence_percentage:.0f}% "
            f"shared_signals={list(summary.signature)}"
        )
    return "\n".join(lines)


def generate_grounded(
    *,
    client: LLMClient,
    config: LLMGenerationConfig,
    system: str,
    context: str,
    draft_model: type[BaseModel],
    valid_ids: frozenset[str],
    citation_field: str,
) -> BaseModel:
    """Call the LLM and require every citation to be a real evidence id.

    Retries once, with the Feedback section of the user message rebuilt
    to name the exact valid ids, if the first draft hallucinates a
    citation; raises if the retry still cites something nonexistent.
    """
    draft = client.complete_structured(
        config=config,
        system=system,
        messages=[
            {"role": "user", "content": build_user_message(context=context)}
        ],
        response_model=draft_model,
    )
    invalid = _invalid_citations(draft, citation_field, valid_ids)
    if not invalid:
        return draft

    feedback = (
        f"Your previous attempt cited evidence id(s) that do not exist: "
        f"{invalid}. Only cite ids from this exact list: "
        f"{sorted(valid_ids)}."
    )
    draft = client.complete_structured(
        config=config,
        system=system,
        messages=[
            {
                "role": "user",
                "content": build_user_message(
                    context=context,
                    feedback=feedback,
                ),
            }
        ],
        response_model=draft_model,
    )
    invalid = _invalid_citations(draft, citation_field, valid_ids)
    if invalid:
        raise LLMResponseValidationError(
            f"LLM cited nonexistent evidence ids after retry: {invalid}"
        )
    return draft


def _invalid_citations(
    draft: BaseModel,
    citation_field: str,
    valid_ids: frozenset[str],
) -> list[str]:
    citations = getattr(draft, citation_field)
    return [citation for citation in citations if citation not in valid_ids]
