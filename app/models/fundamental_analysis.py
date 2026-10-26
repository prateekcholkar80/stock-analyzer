"""Provider-neutral contracts for grounded Fundamental Analyst reasoning."""

from datetime import datetime
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from app.models.financial_documents import (
    StructuredBenchmarkingFinancialsDocument,
    StructuredFinancialDocument,
    StructuredPeerComparisonDocument,
)
from app.models.fundamentals import FundamentalModel
from app.models.fundamental_frameworks import FundamentalFramework
from app.models.fundamental_scoring import (
    FundamentalDecisionInfluenceMode,
    FundamentalScorecardStance,
)


FundamentalDocument = (
    StructuredFinancialDocument
    | StructuredPeerComparisonDocument
    | StructuredBenchmarkingFinancialsDocument
)


class FundamentalDocumentEvidence(FundamentalModel):
    """One scoped cache reference bound to its complete validated JSON."""

    reference_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    document_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    document: FundamentalDocument

    @model_validator(mode="after")
    def validate_document_binding(self) -> Self:
        if self.document_fingerprint != self.document.document_fingerprint:
            raise ValueError(
                "fundamental evidence fingerprint must match its document"
            )
        return self


class FundamentalShadowAssessmentReference(FundamentalModel):
    """Browser-safe trace to the deterministic scorecard explained by the LLM."""

    package_id: str = Field(min_length=1, max_length=240)
    manifest_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    normalized_evidence_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    mandatory_assessment_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    applicability_assessment_fingerprint: str = Field(
        pattern=r"^[a-f0-9]{64}$"
    )
    scorecard_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    score_policy_bundle_id: str = Field(min_length=1, max_length=160)
    score_policy_bundle_version: str = Field(min_length=1, max_length=40)
    score_policy_bundle_fingerprint: str = Field(pattern=r"^[a-f0-9]{64}$")
    decision_influence_mode: FundamentalDecisionInfluenceMode
    stance: FundamentalScorecardStance
    selected_primary_frameworks: tuple[FundamentalFramework, ...] = Field(
        default=(),
        max_length=3,
    )
    review_required: bool
    scored_at: datetime
    expires_at: datetime

    @field_validator("scored_at", "expires_at")
    @classmethod
    def require_trace_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("shadow assessment timestamps require timezone")
        return value

    @model_validator(mode="after")
    def validate_shadow_reference(self) -> Self:
        if self.decision_influence_mode is not (
            FundamentalDecisionInfluenceMode.SHADOW
        ):
            raise ValueError("fundamental assessment reference must be shadow-only")
        if self.package_id != f"fundamental_package:{self.manifest_fingerprint}":
            raise ValueError("shadow assessment package ID must match its manifest")
        if self.scored_at >= self.expires_at:
            raise ValueError("shadow assessment reference must be unexpired")
        if len(self.selected_primary_frameworks) != len(
            set(self.selected_primary_frameworks)
        ):
            raise ValueError("selected shadow frameworks must be unique")
        return self


class FundamentalAnalysis(FundamentalModel):
    """Grounded Fundamental Analyst output for one immutable evidence set."""

    schema_version: Literal["jarvis.fundamental_analysis.v1"] = (
        "jarvis.fundamental_analysis.v1"
    )
    analysis_id: str = Field(
        min_length=1,
        max_length=180,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$",
    )
    evidence_coverage_and_freshness: str = Field(min_length=1)
    financial_strengths: tuple[str, ...]
    financial_risks_and_conflicts: tuple[str, ...]
    preliminary_financial_health: str = Field(min_length=1)
    confidence_percentage: float = Field(ge=0, le=100)
    valuation_observations: tuple[str, ...]
    missing_evidence_and_limitations: tuple[str, ...]
    document_citations: tuple[str, ...] = Field(min_length=1)
    shadow_assessment: FundamentalShadowAssessmentReference | None = None
    runtime_skill_id: str = Field(min_length=1)
    runtime_skill_version: str = Field(min_length=1)
    runtime_skill_prompt_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_id: str = Field(min_length=1)
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("fundamental analysis timestamp requires timezone")
        return value
