from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json

from pydantic import BaseModel, ConfigDict, Field

from app.agents._debate_support import generate_grounded
from app.analytics.fundamental_intelligence import FundamentalIntelligenceResult
from app.llm.gateway import StructuredLLMGateway
from app.models.fundamental_analysis import (
    FundamentalAnalysis,
    FundamentalDocument,
    FundamentalDocumentEvidence,
    FundamentalShadowAssessmentReference,
)
from app.models.runtime_skill import DEFAULT_RUNTIME_SKILLS, RuntimeAgentRole
from app.models.fundamental_scoring import FundamentalDecisionInfluenceMode
from app.runtime_skill_loader import LoadedRuntimeSkill


class _FundamentalAnalysisDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    evidence_coverage_and_freshness: str = Field(min_length=1)
    financial_strengths: list[str]
    financial_risks_and_conflicts: list[str]
    preliminary_financial_health: str = Field(min_length=1)
    confidence_percentage: float = Field(ge=0, le=100)
    valuation_observations: list[str]
    missing_evidence_and_limitations: list[str]
    document_citations: list[str] = Field(min_length=1)


class FundamentalAnalystAgent:
    """Explain cached structured fundamentals without acquiring new data."""

    agent_id = "jarvis.fundamental_analyst_agent.v1"

    def __init__(
        self,
        gateway: StructuredLLMGateway,
        *,
        runtime_skill: LoadedRuntimeSkill,
    ) -> None:
        if not isinstance(gateway, StructuredLLMGateway):
            raise ValueError(
                "fundamental analyst requires a structured LLM gateway"
            )
        if not isinstance(runtime_skill, LoadedRuntimeSkill):
            raise ValueError(
                "fundamental analyst runtime skill must be verified"
            )
        expected_skill = DEFAULT_RUNTIME_SKILLS.skill_for(
            RuntimeAgentRole.FUNDAMENTAL_ANALYST
        )
        if runtime_skill.contract != expected_skill:
            raise ValueError(
                "fundamental analyst requires its approved runtime skill"
            )
        self._gateway = gateway
        self.runtime_skill = runtime_skill

    @property
    def configuration_fingerprint(self) -> str:
        serialized = ":".join(
            (
                self._gateway.configuration_fingerprint,
                self.runtime_skill.contract.skill_id,
                self.runtime_skill.contract.version,
                self.runtime_skill.skill_content_sha256,
                self.runtime_skill.shared_policy_content_sha256,
                self.runtime_skill.prompt_content_sha256,
            )
        )
        return sha256(serialized.encode("utf-8")).hexdigest()

    def analyze(
        self,
        *,
        evidence: tuple[FundamentalDocumentEvidence, ...],
        intelligence: FundamentalIntelligenceResult,
        as_of: datetime,
    ) -> FundamentalAnalysis:
        _validate_evidence_set(evidence, as_of=as_of)
        _validate_intelligence(
            intelligence,
            evidence=evidence,
            as_of=as_of,
        )
        valid_reference_ids = frozenset(
            item.reference_id for item in evidence
        )
        generation = generate_grounded(
            gateway=self._gateway,
            system=self.runtime_skill.system_instructions,
            context=_serialize_evidence(
                evidence,
                intelligence=intelligence,
                as_of=as_of,
            ),
            draft_model=_FundamentalAnalysisDraft,
            valid_ids=valid_reference_ids,
            citation_field="document_citations",
        )
        draft = generation.value
        evidence_fingerprint = sha256(
            ":".join(
                f"{item.reference_id}:{item.document_fingerprint}"
                for item in evidence
            ).encode("utf-8")
        ).hexdigest()
        scorecard = intelligence.scorecard
        analysis_fingerprint = sha256(
            (
                f"{evidence_fingerprint}:"
                f"{scorecard.scorecard_fingerprint}"
            ).encode("utf-8")
        ).hexdigest()
        return FundamentalAnalysis(
            analysis_id=f"{self.agent_id}:{analysis_fingerprint}",
            evidence_coverage_and_freshness=(
                draft.evidence_coverage_and_freshness
            ),
            financial_strengths=tuple(draft.financial_strengths),
            financial_risks_and_conflicts=tuple(
                draft.financial_risks_and_conflicts
            ),
            preliminary_financial_health=(
                draft.preliminary_financial_health
            ),
            confidence_percentage=draft.confidence_percentage,
            valuation_observations=tuple(draft.valuation_observations),
            missing_evidence_and_limitations=tuple(
                draft.missing_evidence_and_limitations
            ),
            document_citations=tuple(draft.document_citations),
            shadow_assessment=FundamentalShadowAssessmentReference(
                package_id=scorecard.package_id,
                manifest_fingerprint=scorecard.manifest_fingerprint,
                normalized_evidence_fingerprint=(
                    scorecard.normalized_evidence_fingerprint
                ),
                mandatory_assessment_fingerprint=(
                    scorecard.mandatory_assessment_fingerprint
                ),
                applicability_assessment_fingerprint=(
                    scorecard.applicability_assessment_fingerprint
                ),
                scorecard_fingerprint=scorecard.scorecard_fingerprint,
                score_policy_bundle_id=scorecard.score_policy_bundle_id,
                score_policy_bundle_version=(
                    scorecard.score_policy_bundle_version
                ),
                score_policy_bundle_fingerprint=(
                    scorecard.score_policy_bundle_fingerprint
                ),
                decision_influence_mode=scorecard.decision_influence_mode,
                stance=scorecard.stance,
                selected_primary_frameworks=(
                    scorecard.selected_primary_frameworks
                ),
                review_required=scorecard.review_required,
                scored_at=scorecard.scored_at,
                expires_at=scorecard.package_expires_at,
            ),
            runtime_skill_id=self.runtime_skill.contract.skill_id,
            runtime_skill_version=self.runtime_skill.contract.version,
            runtime_skill_prompt_sha256=(
                self.runtime_skill.prompt_content_sha256
            ),
            model_id=generation.model,
            generated_at=datetime.now(UTC),
        )


def _validate_evidence_set(
    evidence: tuple[FundamentalDocumentEvidence, ...],
    *,
    as_of: datetime,
) -> None:
    if not isinstance(evidence, tuple) or not evidence:
        raise ValueError(
            "fundamental analyst requires structured document evidence"
        )
    if any(not isinstance(item, FundamentalDocumentEvidence) for item in evidence):
        raise ValueError("fundamental analyst evidence must be validated")
    if not isinstance(as_of, datetime) or (
        as_of.tzinfo is None or as_of.utcoffset() is None
    ):
        raise ValueError("fundamental analyst as-of time requires timezone")
    reference_ids = tuple(item.reference_id for item in evidence)
    if len(reference_ids) != len(set(reference_ids)):
        raise ValueError("fundamental analyst references must be unique")
    documents = tuple(item.document for item in evidence)
    if any(document.expires_at <= as_of for document in documents):
        raise ValueError("fundamental analyst evidence is expired")
    first = documents[0]
    if any(document.connection != first.connection for document in documents[1:]):
        raise ValueError("fundamental analyst evidence scope must be consistent")
    if any(document.issuer != first.issuer for document in documents[1:]):
        raise ValueError("fundamental analyst issuer must be consistent")


def _serialize_evidence(
    evidence: tuple[FundamentalDocumentEvidence, ...],
    *,
    intelligence: FundamentalIntelligenceResult,
    as_of: datetime,
) -> str:
    sections = [
        "Released cached structured fundamental documents.",
        f"Analysis as-of: {as_of.isoformat()}",
        "Use reference_id values exactly as document citations.",
        (
            "The deterministic assessment below is immutable, SHADOW-only, "
            "and cannot be recalculated, overridden, or converted into a "
            "trade decision by the Fundamental Analyst."
        ),
        "## deterministic_shadow_assessment",
        json.dumps(
            _intelligence_projection(intelligence),
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ),
    ]
    for item in evidence:
        sections.append(
            "\n".join(
                (
                    f"## reference_id={item.reference_id}",
                    f"document_fingerprint={item.document_fingerprint}",
                    item.document.model_dump_json(),
                )
            )
        )
    return "\n\n".join(sections)


def _validate_intelligence(
    intelligence: FundamentalIntelligenceResult,
    *,
    evidence: tuple[FundamentalDocumentEvidence, ...],
    as_of: datetime,
) -> None:
    if not isinstance(intelligence, FundamentalIntelligenceResult):
        raise ValueError(
            "fundamental analyst requires validated deterministic intelligence"
        )
    scorecard = intelligence.scorecard
    if scorecard.decision_influence_mode is not (
        FundamentalDecisionInfluenceMode.SHADOW
    ):
        raise ValueError("fundamental analyst accepts shadow intelligence only")
    if scorecard.issuer != evidence[0].document.issuer:
        raise ValueError(
            "fundamental analyst intelligence issuer must match its documents"
        )
    if not scorecard.scored_at <= as_of < scorecard.package_expires_at:
        raise ValueError(
            "fundamental analyst requires current completed shadow intelligence"
        )


def _intelligence_projection(
    intelligence: FundamentalIntelligenceResult,
) -> dict[str, object]:
    """Bound the deterministic context without dropping material outcomes."""

    mandatory = intelligence.mandatory_assessment
    applicability = intelligence.applicability_assessment
    scorecard = intelligence.scorecard
    sector_classification = intelligence.sector_classification
    return {
        "package_id": scorecard.package_id,
        "manifest_fingerprint": scorecard.manifest_fingerprint,
        "normalized_evidence_fingerprint": (
            scorecard.normalized_evidence_fingerprint
        ),
        "decision_influence_mode": scorecard.decision_influence_mode.value,
        "sector_profile": {
            "classification_fingerprint": (
                sector_classification.classification_fingerprint
            ),
            "policy_id": sector_classification.policy_id,
            "policy_version": sector_classification.policy_version,
            "status": sector_classification.status.value,
            "profile": sector_classification.profile.value,
            "reasons": sector_classification.reasons,
            "matches": [
                {
                    "citation_key": match.citation_key,
                    "field": match.matched_field.value,
                    "observed_label": match.observed_label,
                    "canonical_label": match.canonical_label,
                    "profile": match.profile.value,
                }
                for match in sector_classification.matches
            ],
            "citations": [
                {
                    "citation_key": item.citation_key,
                    "source_reference_id": item.source_reference_id,
                    "source_name": item.source_name,
                    "source_location": item.source_location,
                    "source_authority": item.source_authority.value,
                    "source_record_fingerprint": (
                        item.source_record_fingerprint
                    ),
                    "taxonomy": item.taxonomy.value,
                    "sector": item.sector,
                    "industry": item.industry,
                    "retrieved_at": item.retrieved_at.isoformat(),
                    "expires_at": item.expires_at.isoformat(),
                }
                for item in sector_classification.evidence
            ],
        },
        "mandatory": {
            "assessment_fingerprint": mandatory.assessment_fingerprint,
            "coverage_status": mandatory.coverage_status.value,
            "stance": mandatory.stance.value,
            "review_required": mandatory.review_required,
            "domains": [
                {
                    "domain": domain.domain.value,
                    "coverage_status": domain.coverage_status.value,
                    "stance": domain.stance.value,
                    "findings": [
                        {
                            "rule_id": finding.rule_id,
                            "status": finding.status.value,
                            "severity": finding.severity.value,
                            "summary": finding.summary,
                            "limitations": finding.limitations,
                        }
                        for finding in domain.findings
                    ],
                }
                for domain in mandatory.domains
            ],
        },
        "applicability": {
            "assessment_fingerprint": applicability.assessment_fingerprint,
            "selected_primary_frameworks": [
                framework.value
                for framework in applicability.selected_primary_frameworks
            ],
            "frameworks": [
                {
                    "framework": result.framework.value,
                    "status": result.status.value,
                    "deterministic_priority": result.deterministic_priority,
                    "limitations": [
                        limitation.summary for limitation in result.limitations
                    ],
                }
                for result in applicability.results
            ],
        },
        "scorecard": {
            "scorecard_fingerprint": scorecard.scorecard_fingerprint,
            "policy_bundle_id": scorecard.score_policy_bundle_id,
            "policy_bundle_version": scorecard.score_policy_bundle_version,
            "policy_bundle_fingerprint": (
                scorecard.score_policy_bundle_fingerprint
            ),
            "stance": scorecard.stance.value,
            "review_required": scorecard.review_required,
            "frameworks": [
                {
                    "framework": result.framework.value,
                    "selected_primary": result.selected_primary,
                    "status": result.status.value,
                    "score": result.score,
                    "grade": result.grade.value if result.grade else None,
                    "withholding_reason": (
                        result.withholding_reason.value
                        if result.withholding_reason
                        else None
                    ),
                    "families": [
                        {
                            "family": family.family.value,
                            "status": family.status.value,
                            "score": family.score,
                            "withholding_reason": (
                                family.withholding_reason.value
                                if family.withholding_reason
                                else None
                            ),
                        }
                        for family in result.family_results
                    ],
                    "limitations": result.limitations,
                }
                for result in scorecard.framework_results
            ],
            "limitations": scorecard.limitations,
        },
    }
