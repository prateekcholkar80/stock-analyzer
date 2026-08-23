from typing import TypeVar

from pydantic import BaseModel

from app.exceptions import LLMResponseValidationError
from app.llm.gateway import StructuredGeneration, StructuredLLMGateway
from app.models.debate import BullBearArgument
from app.models.multi_timeframe_evidence import (
    ConfirmedPivotSummary,
    MultiTimeframeEvidencePackage,
    NearestPriceZoneSummary,
    TimeframeTechnicalEvidenceContext,
)
from app.models.signals import SwingTradingSignalProfile
from app.models.storage import DebateRunSummary


DraftModelT = TypeVar("DraftModelT", bound=BaseModel)


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


def valid_multi_timeframe_evidence_ids(
    package: MultiTimeframeEvidencePackage,
) -> frozenset[str]:
    """Return every signal, level, and pivot id the panel may cite."""
    identifiers = {
        item.qualified_evidence_id
        for context in (package.weekly, package.daily)
        for item in context.evidence
    }
    for context in (package.weekly, package.daily):
        identifiers.update(
            item.qualified_pivot_id
            for item in context.recent_confirmed_pivots
        )
        for pivot in (
            context.latest_confirmed_high,
            context.latest_confirmed_low,
        ):
            if pivot is not None:
                identifiers.add(pivot.qualified_pivot_id)
        for zone in (
            context.nearest_support,
            context.nearest_resistance,
        ):
            if zone is not None:
                identifiers.add(zone.qualified_zone_id)
    return frozenset(identifiers)


def serialize_multi_timeframe_evidence(
    package: MultiTimeframeEvidencePackage,
) -> str:
    """Format weekly structure and daily timing as a compact fact boundary."""
    identity = package.technical_analysis.timeframes.hourly
    sections = [
        (
            f"Instrument: {identity.exchange} {identity.symbol} "
            f"(token={identity.symbol_token})"
        ),
        "Interpretation order: WEEKLY defines structural direction and "
        "DAILY defines swing-entry timing. Neither timeframe overrides "
        "the other's facts.",
        _serialize_timeframe_context(
            package.weekly,
            package.technical_analysis.weekly_submission.profile,
        ),
        _serialize_timeframe_context(
            package.daily,
            package.technical_analysis.daily_submission.profile,
        ),
    ]
    return "\n\n".join(sections)


def _serialize_timeframe_context(
    context: TimeframeTechnicalEvidenceContext,
    profile: SwingTradingSignalProfile,
) -> str:
    lines = [
        f"## {context.timeframe.value.upper()} ({context.interval})",
        f"Evaluated at: {context.evaluated_at.isoformat()}",
        (
            f"Deterministic profile: stance={profile.stance.value} "
            f"score={profile.score} "
            f"confidence_pct={profile.confidence_percentage} "
            f"agreement_pct={profile.agreement_percentage}"
        ),
        f"Profile rationale: {profile.rationale}",
        f"Current completed-candle close: {context.current_close}",
        _serialize_zone("Immediate support", context.nearest_support),
        _serialize_zone("Immediate resistance", context.nearest_resistance),
        _serialize_latest_pivot("Latest confirmed high", context.latest_confirmed_high),
        _serialize_latest_pivot("Latest confirmed low", context.latest_confirmed_low),
        "Qualified deterministic signals:",
    ]
    lines.extend(
        (
            f"- id={item.qualified_evidence_id} "
            f"category={item.evidence.category.value} "
            f"direction={item.evidence.direction.value} "
            f"strength={item.evidence.strength.value} "
            f"observed_values={item.evidence.observed_values} "
            f"explanation={item.evidence.explanation}"
        )
        for item in context.evidence
    )
    if context.recent_confirmed_pivots:
        lines.append("Recent look-ahead-safe confirmed pivots:")
        lines.extend(
            (
                f"- id={item.qualified_pivot_id} "
                f"type={item.pivot.pivot_type.value} "
                f"price={item.pivot.price} "
                f"pivot_at={item.pivot.pivot_at.isoformat()} "
                f"confirmed_at={item.pivot.confirmed_at.isoformat()}"
            )
            for item in context.recent_confirmed_pivots
        )
    return "\n".join(lines)


def _serialize_zone(
    label: str,
    summary: NearestPriceZoneSummary | None,
) -> str:
    if summary is None:
        return f"{label}: unavailable; do not infer a level."
    zone = summary.lifecycle.zone
    return (
        f"{label}: id={summary.qualified_zone_id} "
        f"range={zone.lower_price}-{zone.upper_price} "
        f"boundary={summary.boundary_price} "
        f"distance_pct={summary.distance_percentage} "
        f"status={summary.lifecycle.status.value} "
        f"confirmed_at={zone.confirmed_at.isoformat()}"
    )


def _serialize_latest_pivot(
    label: str,
    summary: ConfirmedPivotSummary | None,
) -> str:
    if summary is None:
        return f"{label}: unavailable; do not infer a pivot."
    pivot = summary.pivot
    return (
        f"{label}: id={summary.qualified_pivot_id} price={pivot.price} "
        f"pivot_at={pivot.pivot_at.isoformat()} "
        f"confirmed_at={pivot.confirmed_at.isoformat()}"
    )


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
        relationship_note = (
            f", timeframe_relationship="
            f"{argument.timeframe_relationship.value}"
            if argument.timeframe_relationship is not None
            else ""
        )
        lines.append(
            f"[round {argument.round_number} {argument.side.value}, "
            f"id={argument.argument_id}"
            f"{rebuttal_note}] citing {list(argument.evidence_citations)}: "
            f"{argument.thesis}{relationship_note}"
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
    gateway: StructuredLLMGateway,
    system: str,
    context: str,
    draft_model: type[DraftModelT],
    valid_ids: frozenset[str],
    citation_field: str,
) -> StructuredGeneration[DraftModelT]:
    """Call the LLM and require every citation to be a real evidence id.

    Retries once, with the Feedback section of the user message rebuilt
    to name the exact valid ids, if the first draft hallucinates a
    citation; raises if the retry still cites something nonexistent.
    """
    generation = gateway.generate(
        system=system,
        messages=[
            {"role": "user", "content": build_user_message(context=context)}
        ],
        response_model=draft_model,
    )
    draft = generation.value
    invalid = _invalid_citations(draft, citation_field, valid_ids)
    if not invalid:
        return generation

    feedback = (
        f"Your previous attempt cited evidence id(s) that do not exist: "
        f"{invalid}. Only cite ids from this exact list: "
        f"{sorted(valid_ids)}."
    )
    generation = gateway.generate(
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
    draft = generation.value
    invalid = _invalid_citations(draft, citation_field, valid_ids)
    if invalid:
        raise LLMResponseValidationError(
            "LLM cited nonexistent evidence ids after grounding retry",
            provider=generation.provider,
            model=generation.model,
        )
    return generation


def _invalid_citations(
    draft: BaseModel,
    citation_field: str,
    valid_ids: frozenset[str],
) -> list[str]:
    citations = getattr(draft, citation_field)
    return [citation for citation in citations if citation not in valid_ids]
