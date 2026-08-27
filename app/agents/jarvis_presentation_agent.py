import json
import re
from datetime import UTC, datetime
from hashlib import sha256

from pydantic import BaseModel, ConfigDict, Field

from app.agents._debate_support import (
    serialize_multi_timeframe_evidence,
    valid_multi_timeframe_evidence_ids,
)
from app.exceptions import LLMResponseValidationError
from app.llm.gateway import StructuredGeneration, StructuredLLMGateway
from app.models.debate import DebateSide
from app.models.presentation import (
    JarvisCaseExplanation,
    JarvisMultiTimeframeFinding,
    JarvisMultiTimeframeResearchExplanation,
    JarvisResearchExplanation,
    JarvisTechnicalExplanation,
    JarvisTradePlanExplanation,
)
from app.models.storage import (
    EndToEndSwingAnalysisResult,
    MultiTimeframeEndToEndSwingAnalysisResult,
)
from app.models.multi_timeframe_trade import MultiTimeframeTradeDisposition
from app.models.technical import TechnicalModel


_ROLE = (
    "You are Jarvis, the user's Chief Investment Research Assistant. "
    "You greet and address the configured user by name and brief them as "
    "your CEO. Be calm, sharp, candid, and slightly humorous. Communicate "
    "concisely first and provide detailed technical explanation after the "
    "executive briefing or when requested. Humour must never obscure risk "
    "or uncertainty."
)

_EXECUTIVE_COMMUNICATION = (
    "Write for a CEO who may be a beginner in stock-market terminology. "
    "Use short, direct sentences and explain specialist terms in plain "
    "English when first used. Keep executive_briefing to three to five "
    "sentences: lead with the directional verdict, state whether the "
    "application approved a trade, and name the main reason and risk. Keep "
    "judge_conclusion_explanation focused and non-repetitive. In the Judge "
    "explanation, say explicitly that confidence is confidence in the named "
    "verdict -- bullish, bearish, or neutral -- and is not a probability of "
    "future price movement. Do not write Markdown headings or raw evidence "
    "IDs in prose; place citations only in the matching evidence-id fields. "
    "The application owns headers, bullets, verdict labels, and colours. "
    "executive_briefing is also read aloud by a text-to-speech engine, so "
    "punctuate it as natural spoken prose: complete sentences separated by "
    "periods, with commas where a speaker would naturally pause. Never use "
    "bullet points, numbered lists, Markdown, emojis, or asterisks in "
    "executive_briefing, since those either get read aloud literally or "
    "are silently dropped by speech synthesis."
)

_MULTI_TIMEFRAME_COMMUNICATION = (
    "Keep weekly_analysis and daily_analysis focused and non-repetitive. "
    "The executive briefing must explain the weekly-versus-daily picture: "
    "weekly is the broader swing trend and daily is near-term entry timing."
)

_RULES = (
    "Explain the completed technical research, Bull case, Bear case, and "
    "Judge conclusion without changing, overruling, or embellishing any "
    "evidence. The Context is the complete and immutable fact boundary. "
    "Never invent market data, indicators, prices, documents, financial "
    "fundamentals, trade levels, targets, or conclusions. The application "
    "will attach each original technical fact verbatim; provide only a "
    "bounded interpretation in each inference field. Explain every technical "
    "evidence item exactly once and preserve its id, name, category, "
    "direction, and "
    "strength exactly. Preserve the symbol, interval, profile stance and "
    "score, verdict id, Judge winner and confidence, and decisive evidence "
    "ids exactly. Explain all Bull and Bear arguments, referencing their "
    "real argument ids and only real evidence ids. Do not issue a guaranteed "
    "recommendation. Report the work of the specialized Technical, Bull, "
    "Bear, and Judge agents; do not silently replace any agent's conclusion. "
    "This is research, not guaranteed investment advice. "
    "If the Judge rejected the pipeline result, say so plainly. Preserve any "
    "application-owned trade disposition, entry reference, stop, targets, "
    "feasibility, and execution caveat exactly; never turn a no-trade outcome "
    "into a recommendation. If the Context contains no financial statements "
    "or executable trade plan, do not manufacture either. If Feedback "
    "identifies validation errors, "
    "correct those errors and nothing else."
)

JARVIS_PERSONA_SYSTEM_PROMPT = (
    f"# Role\n{_ROLE}\n\n# Communication contract\n"
    f"{_EXECUTIVE_COMMUNICATION}\n\n# Persona and evidence policy\n{_RULES}"
)

_MULTI_TIMEFRAME_RULES = (
    "Explain the immutable weekly structure, daily swing timing, Bull case, "
    "Bear case, and Judge conclusion using only qualified evidence IDs in "
    "Context. WEEKLY defines structural direction; DAILY defines tactical "
    "timing. Explicitly explain agreement or conflict. The application, not "
    "you, attaches every exact signal, pivot, zone, and trade-plan field. "
    "Do not reproduce, modify, calculate, or infer an entry, stop-loss, "
    "target, or reward/risk value. When trade_plan.disposition is no_trade, "
    "state plainly that no long trade is approved and do not offer a "
    "hypothetical plan. Preserve Bull/Bear argument IDs, cite only qualified "
    "daily:/weekly: IDs, and never replace the Judge verdict. No financial "
    "documents were supplied. This is research, not guaranteed advice. If "
    "Feedback identifies validation errors, correct only those errors."
)

JARVIS_MULTI_TIMEFRAME_PERSONA_SYSTEM_PROMPT = (
    f"# Role\n{_ROLE}\n\n# Communication contract\n"
    f"{_EXECUTIVE_COMMUNICATION} {_MULTI_TIMEFRAME_COMMUNICATION}\n\n"
    f"# Persona and evidence policy\n"
    f"{_MULTI_TIMEFRAME_RULES}"
)

_FIXED_LIMITATIONS = (
    "This briefing is grounded only in the supplied technical evidence "
    "and completed debate.",
    "No company financial statements or earnings-call documents were "
    "included in this result.",
    "No approved executable entry, stop-loss, or profit-target plan was "
    "included in this result.",
)
_DISCLAIMER = (
    "This is evidence-grounded investment research, not guaranteed "
    "investment advice. Validate suitability and risk before acting."
)


class _TechnicalExplanationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_id: str = Field(min_length=1)
    inference: str = Field(min_length=1)


class _JarvisPresentationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executive_briefing: str = Field(min_length=1)
    executive_evidence_ids: tuple[str, ...] = Field(min_length=1)
    judge_conclusion_explanation: str = Field(min_length=1)
    judge_evidence_ids: tuple[str, ...] = Field(min_length=1)
    technical_findings: tuple[_TechnicalExplanationDraft, ...] = Field(
        min_length=1
    )
    bull_case: JarvisCaseExplanation
    bear_case: JarvisCaseExplanation


class _MultiTimeframePresentationDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    executive_briefing: str = Field(min_length=1)
    executive_evidence_ids: tuple[str, ...] = Field(min_length=1)
    weekly_analysis: str = Field(min_length=1)
    weekly_analysis_evidence_ids: tuple[str, ...] = Field(min_length=1)
    daily_analysis: str = Field(min_length=1)
    daily_analysis_evidence_ids: tuple[str, ...] = Field(min_length=1)
    judge_conclusion_explanation: str = Field(min_length=1)
    judge_evidence_ids: tuple[str, ...] = Field(min_length=1)
    bull_case: JarvisCaseExplanation
    bear_case: JarvisCaseExplanation


class JarvisPresentationAgentConfig(TechnicalModel):
    model_config = ConfigDict(frozen=True, strict=True)

    prompt_version: str = Field(
        default="jarvis.chief_investment_research_assistant_prompt.v2",
        min_length=1,
    )


class JarvisPresentationAgent:
    """Translate a validated pipeline result without altering its evidence."""

    agent_id = "jarvis.research_presentation_agent.v1"

    def __init__(
        self,
        gateway: StructuredLLMGateway,
        config: JarvisPresentationAgentConfig | None = None,
    ) -> None:
        if not isinstance(gateway, StructuredLLMGateway):
            raise ValueError("Jarvis presenter requires a structured gateway")
        self.config = config or JarvisPresentationAgentConfig()
        if not isinstance(self.config, JarvisPresentationAgentConfig):
            raise ValueError("Jarvis presenter requires validated settings")
        self._gateway = gateway

    @property
    def configuration_fingerprint(self) -> str:
        value = (
            f"{self.config.model_dump_json()}:"
            f"{self._gateway.configuration_fingerprint}"
        )
        return sha256(value.encode("utf-8")).hexdigest()

    def explain(
        self,
        result: (
            EndToEndSwingAnalysisResult
            | MultiTimeframeEndToEndSwingAnalysisResult
        ),
        *,
        user_name: str,
    ) -> (
        JarvisResearchExplanation
        | JarvisMultiTimeframeResearchExplanation
    ):
        if isinstance(result, MultiTimeframeEndToEndSwingAnalysisResult):
            return self._explain_multi_timeframe(result, user_name=user_name)
        if not isinstance(result, EndToEndSwingAnalysisResult):
            raise ValueError("Jarvis presenter requires a validated result")
        if not isinstance(user_name, str):
            raise ValueError("Jarvis presenter requires the user's name")
        normalized_name = user_name.strip()
        if not normalized_name:
            raise ValueError("Jarvis presenter requires the user's name")

        context = self._serialize_context(result, normalized_name)
        generation = self._generate(context)
        errors = self._validation_errors(generation.value, result)
        if errors:
            generation = self._generate(
                context,
                feedback=(
                    "The previous draft changed or omitted immutable facts: "
                    + "; ".join(errors)
                    + ". Re-read the Context and preserve every required "
                    "identifier and value exactly."
                ),
            )
            errors = self._validation_errors(generation.value, result)
        if errors:
            raise LLMResponseValidationError(
                "Jarvis explanation altered or omitted validated evidence "
                "after retry",
                role="jarvis",
                provider=generation.provider,
                model=generation.model,
            )

        draft = generation.value
        profile = result.technical_result.submission.profile
        debate = result.debate_result
        verdict = debate.submission.verdict
        evidence_by_id = {
            item.evidence_id: item for item in profile.snapshot.evidence
        }
        return JarvisResearchExplanation(
            symbol=profile.snapshot.symbol,
            interval=profile.snapshot.interval,
            technical_stance=profile.stance,
            technical_score=profile.score,
            verdict_id=verdict.verdict_id,
            judge_winner=verdict.winner,
            judge_confidence_percentage=verdict.confidence_percentage,
            decisive_evidence_ids=verdict.decisive_evidence_ids,
            executive_evidence_ids=draft.executive_evidence_ids,
            executive_briefing=draft.executive_briefing,
            judge_conclusion_explanation=(
                draft.judge_conclusion_explanation
            ),
            technical_findings=tuple(
                JarvisTechnicalExplanation(
                    evidence_id=item.evidence_id,
                    name=evidence.name,
                    category=evidence.category,
                    direction=evidence.direction,
                    strength=evidence.strength,
                    fact_explanation=evidence.explanation,
                    inference=item.inference,
                )
                for item in draft.technical_findings
                for evidence in (evidence_by_id[item.evidence_id],)
            ),
            bull_case=draft.bull_case,
            bear_case=draft.bear_case,
            limitations=_FIXED_LIMITATIONS,
            disclaimer=_DISCLAIMER,
            provider=generation.provider,
            model_id=generation.model,
            generated_at=datetime.now(UTC),
        )

    def _explain_multi_timeframe(
        self,
        result: MultiTimeframeEndToEndSwingAnalysisResult,
        *,
        user_name: str,
    ) -> JarvisMultiTimeframeResearchExplanation:
        if not isinstance(user_name, str) or not user_name.strip():
            raise ValueError("Jarvis presenter requires the user's name")
        normalized_name = user_name.strip()
        context = self._serialize_multi_context(result, normalized_name)
        generation = self._generate_multi(context)
        errors = self._multi_validation_errors(generation.value, result)
        if errors:
            generation = self._generate_multi(
                context,
                feedback=(
                    "The previous draft changed or omitted immutable facts: "
                    + "; ".join(errors)
                    + ". Re-read the Context and preserve every required "
                    "identifier exactly."
                ),
            )
            errors = self._multi_validation_errors(generation.value, result)
        if errors:
            raise LLMResponseValidationError(
                "Jarvis multi-timeframe explanation altered or omitted "
                "validated evidence after retry",
                role="jarvis",
                provider=generation.provider,
                model=generation.model,
            )

        draft = generation.value
        package = result.technical_review.evidence_package
        debate = result.debate_result
        verdict = debate.submission.verdict
        findings = _multi_findings(package)
        trade_plan = _trade_plan_explanation(result)
        return JarvisMultiTimeframeResearchExplanation(
            symbol=package.technical_analysis.timeframes.hourly.symbol,
            daily_technical_stance=(
                package.technical_analysis.daily_submission.profile.stance
            ),
            daily_technical_score=(
                package.technical_analysis.daily_submission.profile.score
            ),
            weekly_technical_stance=(
                package.technical_analysis.weekly_submission.profile.stance
            ),
            weekly_technical_score=(
                package.technical_analysis.weekly_submission.profile.score
            ),
            verdict_id=verdict.verdict_id,
            judge_winner=verdict.winner,
            judge_confidence_percentage=verdict.confidence_percentage,
            decisive_evidence_ids=verdict.decisive_evidence_ids,
            executive_evidence_ids=draft.executive_evidence_ids,
            executive_briefing=(
                f"{draft.executive_briefing}\n\n"
                f"{_deterministic_trade_summary(trade_plan)}"
            ),
            weekly_analysis=draft.weekly_analysis,
            weekly_analysis_evidence_ids=(
                draft.weekly_analysis_evidence_ids
            ),
            daily_analysis=draft.daily_analysis,
            daily_analysis_evidence_ids=(
                draft.daily_analysis_evidence_ids
            ),
            judge_conclusion_explanation=(
                draft.judge_conclusion_explanation
            ),
            technical_findings=tuple(
                JarvisMultiTimeframeFinding(
                    **finding,
                    inference=_deterministic_finding_inference(finding),
                )
                for finding in findings.values()
            ),
            bull_case=draft.bull_case,
            bear_case=draft.bear_case,
            trade_plan=trade_plan,
            limitations=(
                "This briefing is grounded only in the supplied daily and "
                "weekly technical evidence and completed debate.",
                "No company financial statements or earnings-call documents "
                "were included in this result.",
                trade_plan.execution_note,
            ),
            disclaimer=_DISCLAIMER,
            provider=generation.provider,
            model_id=generation.model,
            generated_at=datetime.now(UTC),
        )

    def _generate(
        self,
        context: str,
        feedback: str = "None -- this is the first attempt.",
    ) -> StructuredGeneration[_JarvisPresentationDraft]:
        return self._gateway.generate(
            system=JARVIS_PERSONA_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"# Context\n{context}\n\n# Feedback\n{feedback}"
                    ),
                }
            ],
            response_model=_JarvisPresentationDraft,
        )

    def _generate_multi(
        self,
        context: str,
        feedback: str = "None -- this is the first attempt.",
    ) -> StructuredGeneration[_MultiTimeframePresentationDraft]:
        return self._gateway.generate(
            system=JARVIS_MULTI_TIMEFRAME_PERSONA_SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": (
                        f"# Context\n{context}\n\n# Feedback\n{feedback}"
                    ),
                }
            ],
            response_model=_MultiTimeframePresentationDraft,
        )

    @staticmethod
    def _serialize_context(
        result: EndToEndSwingAnalysisResult,
        user_name: str,
    ) -> str:
        profile = result.technical_result.submission.profile
        debate = result.debate_result
        payload = {
            "audience": {"name": user_name, "relationship": "CEO"},
            "pipeline_review": {
                "technical_accepted": result.technical_result.decision.accepted,
                "debate_accepted": debate.decision.accepted,
            },
            "technical_profile": profile.model_dump(mode="json"),
            "debate": debate.model_dump(mode="json"),
            "available_material": {
                "financial_documents": False,
                "approved_trade_plan": False,
            },
        }
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))

    @staticmethod
    def _serialize_multi_context(
        result: MultiTimeframeEndToEndSwingAnalysisResult,
        user_name: str,
    ) -> str:
        trade = _trade_plan_explanation(result)
        payload = {
            "audience": {"name": user_name, "relationship": "CEO"},
            "pipeline_review": {
                "technical_accepted": result.technical_review.decision.accepted,
                "debate_accepted": result.debate_result.decision.accepted,
            },
            "trade_plan": trade.model_dump(mode="json"),
            "debate": result.debate_result.model_dump(mode="json"),
            "available_material": {
                "financial_documents": False,
                "approved_trade_plan": (
                    trade.disposition
                    is MultiTimeframeTradeDisposition.ACTIONABLE
                ),
            },
        }
        return (
            serialize_multi_timeframe_evidence(
                result.technical_review.evidence_package
            )
            + "\n\n# Validated pipeline context\n"
            + json.dumps(payload, sort_keys=True, separators=(",", ":"))
        )

    @staticmethod
    def _validation_errors(
        draft: _JarvisPresentationDraft,
        result: EndToEndSwingAnalysisResult,
    ) -> list[str]:
        profile = result.technical_result.submission.profile
        debate = result.debate_result
        errors: list[str] = []
        evidence_by_id = {
            item.evidence_id: item for item in profile.snapshot.evidence
        }
        actual_finding_ids = tuple(
            item.evidence_id for item in draft.technical_findings
        )
        if set(actual_finding_ids) != set(evidence_by_id) or len(
            actual_finding_ids
        ) != len(evidence_by_id):
            errors.append("technical_findings must cover every evidence id once")
        arguments_by_side = {
            side: tuple(
                argument
                for round_ in debate.submission.transcript.rounds
                for argument in (round_.bull_argument, round_.bear_argument)
                if argument.side is side
            )
            for side in DebateSide
        }
        valid_evidence = set(evidence_by_id)
        if (
            len(draft.executive_evidence_ids)
            != len(set(draft.executive_evidence_ids))
            or not set(draft.executive_evidence_ids).issubset(valid_evidence)
        ):
            errors.append("executive briefing cited unknown evidence")
        decisive = debate.submission.verdict.decisive_evidence_ids
        if draft.judge_evidence_ids != decisive:
            errors.append(
                "judge conclusion must cite the exact decisive evidence ids"
            )
        for side, explanation in (
            (DebateSide.BULL, draft.bull_case),
            (DebateSide.BEAR, draft.bear_case),
        ):
            expected_arguments = {
                item.argument_id for item in arguments_by_side[side]
            }
            if set(explanation.argument_ids) != expected_arguments or len(
                explanation.argument_ids
            ) != len(expected_arguments):
                errors.append(f"{side.value}_case must cover every argument")
            allowed = {
                citation
                for item in arguments_by_side[side]
                for citation in item.evidence_citations
            }
            if not set(explanation.evidence_ids).issubset(allowed):
                errors.append(f"{side.value}_case cited unargued evidence")
            if not set(explanation.evidence_ids).issubset(valid_evidence):
                errors.append(f"{side.value}_case cited unknown evidence")
        return errors

    @staticmethod
    def _multi_validation_errors(
        draft: _MultiTimeframePresentationDraft,
        result: MultiTimeframeEndToEndSwingAnalysisResult,
    ) -> list[str]:
        package = result.technical_review.evidence_package
        debate = result.debate_result
        errors: list[str] = []
        valid_evidence = valid_multi_timeframe_evidence_ids(package)
        if (
            len(draft.executive_evidence_ids)
            != len(set(draft.executive_evidence_ids))
            or not set(draft.executive_evidence_ids).issubset(valid_evidence)
        ):
            errors.append("executive briefing cited unknown evidence")
        daily_ids = _context_finding_ids(package.daily)
        weekly_ids = _context_finding_ids(package.weekly)
        for label, citations, allowed in (
            (
                "daily",
                draft.daily_analysis_evidence_ids,
                daily_ids,
            ),
            (
                "weekly",
                draft.weekly_analysis_evidence_ids,
                weekly_ids,
            ),
        ):
            if (
                len(citations) != len(set(citations))
                or not set(citations).issubset(allowed)
            ):
                errors.append(f"{label} analysis cited another timeframe")
        decisive = debate.submission.verdict.decisive_evidence_ids
        if draft.judge_evidence_ids != decisive:
            errors.append(
                "judge conclusion must cite exact decisive evidence ids"
            )
        arguments_by_side = {
            side: tuple(
                argument
                for round_ in debate.submission.transcript.rounds
                for argument in (round_.bull_argument, round_.bear_argument)
                if argument.side is side
            )
            for side in DebateSide
        }
        for side, explanation in (
            (DebateSide.BULL, draft.bull_case),
            (DebateSide.BEAR, draft.bear_case),
        ):
            expected_arguments = {
                item.argument_id for item in arguments_by_side[side]
            }
            if (
                set(explanation.argument_ids) != expected_arguments
                or len(explanation.argument_ids) != len(expected_arguments)
            ):
                errors.append(f"{side.value}_case must cover every argument")
            allowed = {
                citation
                for item in arguments_by_side[side]
                for citation in item.evidence_citations
            }
            if not set(explanation.evidence_ids).issubset(allowed):
                errors.append(f"{side.value}_case cited unargued evidence")
            if not set(explanation.evidence_ids).issubset(valid_evidence):
                errors.append(f"{side.value}_case cited unknown evidence")
        if (
            result.trade_plan_result.disposition
            is MultiTimeframeTradeDisposition.NO_TRADE
            and _contains_hypothetical_trade_levels(draft)
        ):
            errors.append(
                "no-trade briefing cannot calculate hypothetical trade levels"
            )
        return errors


def _multi_findings(package) -> dict[str, dict]:
    findings: dict[str, dict] = {}
    for context in (package.weekly, package.daily):
        for item in context.evidence:
            findings[item.qualified_evidence_id] = {
                "evidence_id": item.qualified_evidence_id,
                "timeframe": context.timeframe,
                "finding_type": "signal",
                "name": item.evidence.name,
                "fact_explanation": item.evidence.explanation,
            }
        pivots = list(context.recent_confirmed_pivots)
        for pivot in (context.latest_confirmed_high, context.latest_confirmed_low):
            if pivot is not None and pivot.qualified_pivot_id not in {
                item.qualified_pivot_id for item in pivots
            }:
                pivots.append(pivot)
        for item in pivots:
            pivot = item.pivot
            findings[item.qualified_pivot_id] = {
                "evidence_id": item.qualified_pivot_id,
                "timeframe": context.timeframe,
                "finding_type": "pivot",
                "name": f"Confirmed {pivot.pivot_type.value} pivot",
                "fact_explanation": (
                    f"Price {pivot.price:g} pivoted at "
                    f"{pivot.pivot_at.isoformat()} and was confirmed at "
                    f"{pivot.confirmed_at.isoformat()}."
                ),
            }
        for label, item in (
            ("Immediate support", context.nearest_support),
            ("Immediate resistance", context.nearest_resistance),
        ):
            if item is None:
                continue
            zone = item.lifecycle.zone
            findings[item.qualified_zone_id] = {
                "evidence_id": item.qualified_zone_id,
                "timeframe": context.timeframe,
                "finding_type": "zone",
                "name": label,
                "fact_explanation": (
                    f"{label} spans {zone.lower_price:g} to "
                    f"{zone.upper_price:g}; boundary "
                    f"{item.boundary_price:g}, distance "
                    f"{item.distance_percentage:g}%, lifecycle "
                    f"{item.lifecycle.status.value}."
                ),
            }
    return findings


def _context_finding_ids(context) -> frozenset[str]:
    identifiers = {
        item.qualified_evidence_id for item in context.evidence
    }
    identifiers.update(
        item.qualified_pivot_id for item in context.recent_confirmed_pivots
    )
    for item in (context.latest_confirmed_high, context.latest_confirmed_low):
        if item is not None:
            identifiers.add(item.qualified_pivot_id)
    for item in (context.nearest_support, context.nearest_resistance):
        if item is not None:
            identifiers.add(item.qualified_zone_id)
    return frozenset(identifiers)


def _deterministic_finding_inference(finding: dict) -> str:
    timeframe = finding["timeframe"].value
    finding_type = finding["finding_type"]
    if finding_type == "signal":
        return (
            f"This is one deterministic {timeframe} signal used in the "
            "approved evidence package; it contributes to, but does not "
            "independently determine, the final verdict."
        )
    if finding_type == "pivot":
        return (
            f"This confirmed {timeframe} pivot is a look-ahead-safe "
            "structural reference, not a standalone trade instruction."
        )
    return (
        f"This confirmed {timeframe} zone is a structural risk/reference "
        "level, not a guaranteed reversal point."
    )


_HYPOTHETICAL_TRADE_LEVEL = re.compile(
    r"(?:"
    r"\b(?:entry|stop(?:-loss)?|target)\s+"
    r"(?:at|below|above|of|near|around)?\s*(?:₹\s*)?\d"
    r"|\b\d+(?:\.\d+)?%\s+(?:risk|reward)\b"
    r"|\brisk\s*[/:-]\s*reward\b"
    r")",
    re.IGNORECASE,
)


def _contains_hypothetical_trade_levels(
    draft: _MultiTimeframePresentationDraft,
) -> bool:
    text = "\n".join(
        (
            draft.executive_briefing,
            draft.weekly_analysis,
            draft.daily_analysis,
            draft.judge_conclusion_explanation,
        )
    )
    return _HYPOTHETICAL_TRADE_LEVEL.search(text) is not None


def _trade_plan_explanation(
    result: MultiTimeframeEndToEndSwingAnalysisResult,
) -> JarvisTradePlanExplanation:
    outcome = result.trade_plan_result
    plan = (
        outcome.daily_planning_result.approved_trade_intent
        if outcome.daily_planning_result is not None
        else None
    )
    execution_note = (
        "The entry is a technical reference at the latest completed daily "
        "close; backtesting or execution must use the next eligible candle "
        "open unless market-on-close execution is explicitly modeled."
    )
    if plan is None:
        return JarvisTradePlanExplanation(
            disposition=outcome.disposition,
            reason=outcome.reason,
            rationale=outcome.rationale,
            minimum_reward_to_risk=2.0,
            execution_note=(
                "No executable long trade was approved for this analysis."
            ),
        )
    evaluation = plan.evaluation
    return JarvisTradePlanExplanation(
        disposition=outcome.disposition,
        reason=outcome.reason,
        rationale=outcome.rationale,
        direction=evaluation.direction,
        entry_method=plan.entry_method,
        reference_entry_price=evaluation.entry_price,
        stop_loss_method=plan.stop_loss_method,
        stop_loss_price=evaluation.stop_loss_price,
        risk_per_unit=evaluation.risk_per_unit,
        minimum_reward_to_risk=evaluation.minimum_reward_to_risk,
        minimum_target_price=evaluation.minimum_target.target_price,
        minimum_target_feasibility=evaluation.minimum_target.feasibility,
        preferred_reward_to_risk=evaluation.preferred_reward_to_risk,
        preferred_target_price=evaluation.preferred_target.target_price,
        preferred_target_feasibility=(
            evaluation.preferred_target.feasibility
        ),
        execution_note=execution_note,
    )


def _deterministic_trade_summary(plan: JarvisTradePlanExplanation) -> str:
    if plan.disposition is MultiTimeframeTradeDisposition.NO_TRADE:
        return f"Trade plan: NO TRADE. {plan.rationale}"
    return (
        "Trade plan (long-only): reference entry "
        f"₹{plan.reference_entry_price:,.2f}, stop-loss "
        f"₹{plan.stop_loss_price:,.2f}, 1:2 target "
        f"₹{plan.minimum_target_price:,.2f} "
        f"({plan.minimum_target_feasibility.value}), and 1:"
        f"{plan.preferred_reward_to_risk:g} target "
        f"₹{plan.preferred_target_price:,.2f} "
        f"({plan.preferred_target_feasibility.value}). "
        f"{plan.execution_note}"
    )
