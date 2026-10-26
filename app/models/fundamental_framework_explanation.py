"""Immutable boundaries for grounded fundamental-framework explanations."""

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from hashlib import sha256
import json
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.fundamental_framework_skill import (
    FundamentalFrameworkSkillAbstentionReason,
    FundamentalFrameworkSkillCitationNamespace,
)
from app.models.fundamental_frameworks import (
    FundamentalFramework,
    FundamentalFrameworkApplicabilityStatus,
)
from app.models.fundamental_scoring import (
    FundamentalDecisionInfluenceMode,
    FundamentalScoreGrade,
    FundamentalScoreResultStatus,
    FundamentalScorecardStance,
)
from app.models.fundamentals import FundamentalModel


_FINGERPRINT_PATTERN = r"^[a-f0-9]{64}$"
_IDENTIFIER_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]*$"
_POLICY_ID_PATTERN = r"^[a-z][a-z0-9_.-]*$"
_SEMVER_PATTERN = r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$"


class FundamentalFrameworkExplanationEvidenceState(StrEnum):
    """Preflight state of the immutable evidence supplied to one skill."""

    CURRENT = "current"
    MISSING = "missing"
    INVALID = "invalid"
    PACKAGE_MISMATCH = "package_mismatch"
    POLICY_MISMATCH = "policy_mismatch"
    MISSING_CITATION = "missing_citation"
    UNSUPPORTED_CLAIM = "unsupported_claim"


class FundamentalFrameworkExplanationStatus(StrEnum):
    """Whether an explanation was produced or deterministically withheld."""

    EXPLAINED = "explained"
    ABSTAINED = "abstained"


class FundamentalFrameworkExplanationClaimKind(StrEnum):
    """Company-safe categories permitted in explanatory prose."""

    MANDATORY_FINDING = "mandatory_finding"
    SCORED_EVIDENCE = "scored_evidence"
    EVIDENCE_LIMITATION = "evidence_limitation"


class FundamentalFrameworkExplanationBinding(FundamentalModel):
    """Exact immutable identities an explanation is forbidden to alter."""

    package_id: str = Field(min_length=1, max_length=240)
    manifest_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    sector_profile_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    mandatory_assessment_fingerprint: str = Field(
        pattern=_FINGERPRINT_PATTERN
    )
    applicability_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    scorecard_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    framework_result_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    framework: FundamentalFramework
    selected_primary: bool
    applicability_status: FundamentalFrameworkApplicabilityStatus
    score_status: FundamentalScoreResultStatus
    score: Decimal | None = Field(default=None, ge=0, le=100, decimal_places=2)
    grade: FundamentalScoreGrade | None = None
    family_coverage_bps: int = Field(ge=0, le=10_000)
    stance: FundamentalScorecardStance
    review_required: bool
    score_policy_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    score_policy_version: str = Field(pattern=_SEMVER_PATTERN)
    framework_skill_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_POLICY_ID_PATTERN,
    )
    framework_skill_version: str = Field(pattern=_SEMVER_PATTERN)
    framework_skill_prompt_sha256: str = Field(
        pattern=_FINGERPRINT_PATTERN
    )
    doctrine_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=_POLICY_ID_PATTERN,
    )
    doctrine_version: str | None = Field(
        default=None,
        pattern=_SEMVER_PATTERN,
    )
    decision_influence_mode: Literal[
        FundamentalDecisionInfluenceMode.SHADOW
    ] = FundamentalDecisionInfluenceMode.SHADOW

    @model_validator(mode="after")
    def validate_binding(self) -> Self:
        if self.package_id != f"fundamental_package:{self.manifest_fingerprint}":
            raise ValueError("framework explanation package binding is invalid")
        expected_score_policy = (
            f"jarvis.framework_score.{self.framework.value}"
        )
        if self.score_policy_id != expected_score_policy:
            raise ValueError(
                "framework explanation score policy does not match framework"
            )
        expected_skill_id = (
            f"jarvis.fundamental_framework.{self.framework.value}.v1"
        )
        if self.framework_skill_id != expected_skill_id:
            raise ValueError(
                "framework explanation skill does not match framework"
            )
        if self.score_status is FundamentalScoreResultStatus.CALCULATED:
            if self.score is None or self.grade is None:
                raise ValueError(
                    "calculated framework explanation requires score and grade"
                )
        elif self.score is not None or self.grade is not None:
            raise ValueError(
                "unreleased framework explanation cannot carry score or grade"
            )
        doctrine_fields = (
            self.doctrine_id is not None,
            self.doctrine_version is not None,
        )
        if len(set(doctrine_fields)) != 1:
            raise ValueError(
                "framework explanation doctrine identity is incomplete"
            )
        if self.doctrine_id is not None:
            expected_doctrine_id = (
                "jarvis.fundamental_framework_doctrine."
                f"{self.framework.value}.v1"
            )
            if self.doctrine_id != expected_doctrine_id:
                raise ValueError(
                    "framework explanation doctrine does not match framework"
                )
        return self


class FundamentalFrameworkExplanationCitation(FundamentalModel):
    """One exact citation identifier released to the explanatory skill."""

    citation_id: str = Field(
        min_length=1,
        max_length=400,
        pattern=_IDENTIFIER_PATTERN,
    )
    namespace: FundamentalFrameworkSkillCitationNamespace
    evidence_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)


_EVIDENCE_ABSTENTION = {
    FundamentalFrameworkExplanationEvidenceState.MISSING: (
        FundamentalFrameworkSkillAbstentionReason.MISSING_EVIDENCE
    ),
    FundamentalFrameworkExplanationEvidenceState.INVALID: (
        FundamentalFrameworkSkillAbstentionReason.INVALID_EVIDENCE
    ),
    FundamentalFrameworkExplanationEvidenceState.PACKAGE_MISMATCH: (
        FundamentalFrameworkSkillAbstentionReason.PACKAGE_MISMATCH
    ),
    FundamentalFrameworkExplanationEvidenceState.POLICY_MISMATCH: (
        FundamentalFrameworkSkillAbstentionReason.POLICY_MISMATCH
    ),
    FundamentalFrameworkExplanationEvidenceState.MISSING_CITATION: (
        FundamentalFrameworkSkillAbstentionReason.MISSING_CITATION
    ),
    FundamentalFrameworkExplanationEvidenceState.UNSUPPORTED_CLAIM: (
        FundamentalFrameworkSkillAbstentionReason.UNSUPPORTED_CLAIM
    ),
}


class FundamentalFrameworkExplanationRequest(FundamentalModel):
    """Preflight-validated input for one selected framework explanation."""

    schema_version: Literal[
        "jarvis.fundamental_framework_explanation_request.v1"
    ] = "jarvis.fundamental_framework_explanation_request.v1"
    request_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=_IDENTIFIER_PATTERN,
    )
    binding: FundamentalFrameworkExplanationBinding
    evidence_state: FundamentalFrameworkExplanationEvidenceState
    allowed_citations: tuple[
        FundamentalFrameworkExplanationCitation,
        ...,
    ] = Field(default=(), max_length=512)
    mandatory_failure_citation_ids: tuple[str, ...] = Field(
        default=(),
        max_length=128,
    )
    mandatory_warning_citation_ids: tuple[str, ...] = Field(
        default=(),
        max_length=128,
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=128)
    as_of: datetime
    package_expires_at: datetime

    @field_validator("as_of", "package_expires_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("framework explanation timestamps require timezone")
        return value

    @field_validator(
        "mandatory_failure_citation_ids",
        "mandatory_warning_citation_ids",
    )
    @classmethod
    def require_canonical_citation_ids(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(
            not value
            or len(value) > 400
            for value in values
        ):
            raise ValueError("mandatory citation IDs must be bounded")
        if len(values) != len(set(values)) or values != tuple(sorted(values)):
            raise ValueError(
                "mandatory citation IDs must be unique and canonical"
            )
        return values

    @field_validator("limitations")
    @classmethod
    def require_unique_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 1_000 for value in values):
            raise ValueError("framework explanation limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("framework explanation limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_request(self) -> Self:
        citation_ids = tuple(
            citation.citation_id for citation in self.allowed_citations
        )
        if len(citation_ids) != len(set(citation_ids)):
            raise ValueError("allowed framework citations must be unique")
        if citation_ids != tuple(sorted(citation_ids)):
            raise ValueError("allowed framework citations must be canonical")

        citation_by_id = {
            citation.citation_id: citation
            for citation in self.allowed_citations
        }
        mandatory_ids = (
            self.mandatory_failure_citation_ids
            + self.mandatory_warning_citation_ids
        )
        if len(mandatory_ids) != len(set(mandatory_ids)):
            raise ValueError(
                "mandatory failure and warning citations cannot overlap"
            )
        for citation_id in mandatory_ids:
            citation = citation_by_id.get(citation_id)
            if citation is None or citation.namespace is not (
                FundamentalFrameworkSkillCitationNamespace.MANDATORY_FINDING
            ):
                raise ValueError(
                    "mandatory findings require released mandatory citations"
                )
        if mandatory_ids and not self.binding.review_required:
            raise ValueError(
                "mandatory warning or failure requires deterministic review"
            )
        if self.mandatory_failure_citation_ids and self.binding.stance is (
            FundamentalScorecardStance.HEALTHY
        ):
            raise ValueError(
                "mandatory failure cannot retain a healthy framework stance"
            )
        return self

    @computed_field
    @property
    def abstention_reason(
        self,
    ) -> FundamentalFrameworkSkillAbstentionReason | None:
        evidence_reason = _EVIDENCE_ABSTENTION.get(self.evidence_state)
        if evidence_reason is not None:
            return evidence_reason
        if self.as_of >= self.package_expires_at:
            return FundamentalFrameworkSkillAbstentionReason.STALE_EVIDENCE
        if not self.binding.selected_primary:
            return (
                FundamentalFrameworkSkillAbstentionReason.
                FRAMEWORK_NOT_SELECTED
            )
        if self.binding.applicability_status is (
            FundamentalFrameworkApplicabilityStatus.INELIGIBLE
        ):
            return (
                FundamentalFrameworkSkillAbstentionReason.
                FRAMEWORK_INAPPLICABLE
            )
        if (
            self.binding.applicability_status
            is not FundamentalFrameworkApplicabilityStatus.ELIGIBLE
            or self.binding.score_status
            is not FundamentalScoreResultStatus.CALCULATED
        ):
            return (
                FundamentalFrameworkSkillAbstentionReason.
                INSUFFICIENT_COVERAGE
            )
        if not self.allowed_citations:
            return FundamentalFrameworkSkillAbstentionReason.MISSING_CITATION
        return None

    @computed_field
    @property
    def can_invoke_llm(self) -> bool:
        return self.abstention_reason is None

    @computed_field
    @property
    def request_fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={
                "abstention_reason",
                "can_invoke_llm",
                "request_fingerprint",
            },
        )
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()


class FundamentalFrameworkExplanationClaim(FundamentalModel):
    """One bounded explanatory statement produced from released evidence."""

    claim_id: str = Field(
        min_length=1,
        max_length=240,
        pattern=_IDENTIFIER_PATTERN,
    )
    kind: FundamentalFrameworkExplanationClaimKind
    statement: str = Field(min_length=1, max_length=2_000)
    citation_ids: tuple[str, ...] = Field(default=(), max_length=64)

    @field_validator("citation_ids")
    @classmethod
    def require_unique_citations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if len(values) != len(set(values)):
            raise ValueError("framework claim citations must be unique")
        if values != tuple(sorted(values)):
            raise ValueError("framework claim citations must be canonical")
        return values

    @model_validator(mode="after")
    def validate_claim(self) -> Self:
        if self.kind is not (
            FundamentalFrameworkExplanationClaimKind.EVIDENCE_LIMITATION
        ) and not self.citation_ids:
            raise ValueError(
                "company-specific framework claims require citations"
            )
        return self


class FundamentalFrameworkExplanationResult(FundamentalModel):
    """Grounded output that can be checked against its immutable request."""

    schema_version: Literal[
        "jarvis.fundamental_framework_explanation.v1"
    ] = "jarvis.fundamental_framework_explanation.v1"
    explanation_id: str = Field(
        min_length=1,
        max_length=400,
        pattern=_IDENTIFIER_PATTERN,
    )
    request_fingerprint: str = Field(pattern=_FINGERPRINT_PATTERN)
    binding: FundamentalFrameworkExplanationBinding
    status: FundamentalFrameworkExplanationStatus
    abstention_reason: FundamentalFrameworkSkillAbstentionReason | None = None
    claims: tuple[FundamentalFrameworkExplanationClaim, ...] = Field(
        default=(),
        max_length=128,
    )
    limitations: tuple[str, ...] = Field(default=(), max_length=128)
    model_id: str | None = Field(default=None, min_length=1, max_length=240)
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("framework explanation timestamp requires timezone")
        return value

    @field_validator("limitations")
    @classmethod
    def require_unique_limitations(
        cls,
        values: tuple[str, ...],
    ) -> tuple[str, ...]:
        if any(not value or len(value) > 1_000 for value in values):
            raise ValueError("framework result limitations must be bounded")
        if len(values) != len(set(values)):
            raise ValueError("framework result limitations must be unique")
        return values

    @model_validator(mode="after")
    def validate_result_shape(self) -> Self:
        expected_id = (
            f"fundamental.framework_explanation:{self.request_fingerprint}"
        )
        if self.explanation_id != expected_id:
            raise ValueError("framework explanation ID is not stable")
        claim_ids = tuple(claim.claim_id for claim in self.claims)
        if len(claim_ids) != len(set(claim_ids)):
            raise ValueError("framework explanation claim IDs must be unique")
        if self.status is FundamentalFrameworkExplanationStatus.EXPLAINED:
            if self.abstention_reason is not None or not self.claims:
                raise ValueError(
                    "explained framework result requires claims and no "
                    "abstention"
                )
            if self.model_id is None:
                raise ValueError("explained framework result requires a model")
        else:
            if self.abstention_reason is None or self.claims:
                raise ValueError(
                    "abstained framework result requires a reason and no claims"
                )
            if not self.limitations:
                raise ValueError(
                    "abstained framework result requires a limitation"
                )
            if self.model_id is not None:
                raise ValueError(
                    "preflight abstention cannot claim an invoked model"
                )
        return self

    def validate_against(
        self,
        request: FundamentalFrameworkExplanationRequest,
    ) -> Self:
        """Reject an output that escapes its exact preflight evidence."""
        if not isinstance(request, FundamentalFrameworkExplanationRequest):
            raise TypeError("framework explanation request must be validated")
        if (
            self.request_fingerprint != request.request_fingerprint
            or self.binding != request.binding
        ):
            raise ValueError(
                "framework explanation does not match its immutable request"
            )
        if self.generated_at < request.as_of:
            raise ValueError(
                "framework explanation predates its evidence as-of time"
            )

        reason = request.abstention_reason
        if reason is not None:
            if (
                self.status is not FundamentalFrameworkExplanationStatus.ABSTAINED
                or self.abstention_reason is not reason
            ):
                raise ValueError(
                    "framework explanation must preserve preflight abstention"
                )
            if not set(self.limitations).issubset(request.limitations):
                raise ValueError(
                    "framework abstention contains an unreleased limitation"
                )
            return self

        if self.status is not FundamentalFrameworkExplanationStatus.EXPLAINED:
            raise ValueError("eligible framework request requires explanation")
        if self.generated_at >= request.package_expires_at:
            raise ValueError("framework explanation used expired evidence")

        citation_by_id = {
            citation.citation_id: citation
            for citation in request.allowed_citations
        }
        cited_ids: set[str] = set()
        for claim in self.claims:
            for citation_id in claim.citation_ids:
                citation = citation_by_id.get(citation_id)
                if citation is None:
                    raise ValueError(
                        "framework explanation cites unreleased evidence"
                    )
                expected_namespace = (
                    FundamentalFrameworkSkillCitationNamespace.
                    MANDATORY_FINDING
                    if claim.kind
                    is FundamentalFrameworkExplanationClaimKind.MANDATORY_FINDING
                    else FundamentalFrameworkSkillCitationNamespace.
                    FUNDAMENTAL_METRIC
                )
                if (
                    claim.kind
                    is not FundamentalFrameworkExplanationClaimKind.
                    EVIDENCE_LIMITATION
                    and citation.namespace is not expected_namespace
                ):
                    raise ValueError(
                        "framework claim uses the wrong citation namespace"
                    )
                cited_ids.add(citation_id)

        required_mandatory_ids = set(
            request.mandatory_failure_citation_ids
            + request.mandatory_warning_citation_ids
        )
        if not required_mandatory_ids.issubset(cited_ids):
            raise ValueError(
                "framework explanation omitted a mandatory finding"
            )
        if required_mandatory_ids and self.claims[0].kind is not (
            FundamentalFrameworkExplanationClaimKind.MANDATORY_FINDING
        ):
            raise ValueError(
                "mandatory findings must precede weighted framework evidence"
            )
        if not set(self.limitations).issubset(request.limitations):
            raise ValueError(
                "framework explanation contains an unreleased limitation"
            )
        return self

    @computed_field
    @property
    def result_fingerprint(self) -> str:
        payload = self.model_dump(
            mode="json",
            exclude={"result_fingerprint"},
        )
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ).encode("utf-8")
        return sha256(encoded).hexdigest()
