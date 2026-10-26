"""Immutable contracts for deterministic fundamental-framework skills.

Framework skills explain an already-released deterministic assessment. They
cannot select a lens, acquire evidence, calculate or alter scores, weaken a
mandatory finding, or issue a trade decision.
"""

import json
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Self

from pydantic import Field, computed_field, field_validator, model_validator

from app.models.fundamental_frameworks import FundamentalFramework
from app.models.fundamentals import FundamentalModel


_IDENTIFIER_PATTERN = r"^[a-z][a-z0-9_.-]*$"
_SEMVER_PATTERN = r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$"


class FundamentalFrameworkSkillEvidence(StrEnum):
    """Released evidence surfaces available to an explanatory framework."""

    COMMITTED_PACKAGE_REFERENCE = "committed_package_reference"
    SECTOR_PROFILE = "sector_profile"
    FRAMEWORK_APPLICABILITY = "framework_applicability"
    MANDATORY_ASSESSMENTS = "mandatory_assessments"
    FRAMEWORK_SCORE = "framework_score"


class FundamentalFrameworkSkillCitationNamespace(StrEnum):
    """Citation namespaces accepted in a framework explanation."""

    FUNDAMENTAL_METRIC = "fundamental_metric"
    MANDATORY_FINDING = "mandatory_finding"


class FundamentalFrameworkSkillAbstentionReason(StrEnum):
    """Fail-closed states that prohibit a company-level explanation."""

    FRAMEWORK_NOT_SELECTED = "framework_not_selected"
    FRAMEWORK_INAPPLICABLE = "framework_inapplicable"
    INSUFFICIENT_COVERAGE = "insufficient_coverage"
    MISSING_EVIDENCE = "missing_evidence"
    STALE_EVIDENCE = "stale_evidence"
    INVALID_EVIDENCE = "invalid_evidence"
    PACKAGE_MISMATCH = "package_mismatch"
    POLICY_MISMATCH = "policy_mismatch"
    MISSING_CITATION = "missing_citation"
    UNSUPPORTED_CLAIM = "unsupported_claim"


REQUIRED_FRAMEWORK_SKILL_EVIDENCE = tuple(
    FundamentalFrameworkSkillEvidence
)
REQUIRED_FRAMEWORK_SKILL_CITATIONS = tuple(
    FundamentalFrameworkSkillCitationNamespace
)
REQUIRED_FRAMEWORK_SKILL_ABSTENTIONS = tuple(
    FundamentalFrameworkSkillAbstentionReason
)


class FundamentalFrameworkSkillContract(FundamentalModel):
    """Provider-neutral policy boundary for one explanatory framework skill."""

    schema_version: Literal["jarvis.fundamental_framework_skill.v1"] = (
        "jarvis.fundamental_framework_skill.v1"
    )
    skill_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_IDENTIFIER_PATTERN,
    )
    framework: FundamentalFramework
    version: str = Field(pattern=_SEMVER_PATTERN)
    purpose: str = Field(min_length=1, max_length=500)
    score_policy_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=_IDENTIFIER_PATTERN,
    )
    score_policy_version: str = Field(pattern=_SEMVER_PATTERN)
    output_schema_id: Literal[
        "jarvis.fundamental_framework_explanation.v1"
    ] = "jarvis.fundamental_framework_explanation.v1"
    doctrine_id: str | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        pattern=_IDENTIFIER_PATTERN,
    )
    doctrine_version: str | None = Field(
        default=None,
        pattern=_SEMVER_PATTERN,
    )
    allowed_evidence: tuple[FundamentalFrameworkSkillEvidence, ...] = Field(
        min_length=len(FundamentalFrameworkSkillEvidence),
        max_length=len(FundamentalFrameworkSkillEvidence),
    )
    required_citations: tuple[
        FundamentalFrameworkSkillCitationNamespace,
        ...,
    ] = Field(
        min_length=len(FundamentalFrameworkSkillCitationNamespace),
        max_length=len(FundamentalFrameworkSkillCitationNamespace),
    )
    abstain_on: tuple[
        FundamentalFrameworkSkillAbstentionReason,
        ...,
    ] = Field(
        min_length=len(FundamentalFrameworkSkillAbstentionReason),
        max_length=len(FundamentalFrameworkSkillAbstentionReason),
    )
    shadow_only: Literal[True] = True
    provider_neutral: Literal[True] = True
    can_select_framework: Literal[False] = False
    can_calculate_evidence: Literal[False] = False
    can_modify_evidence: Literal[False] = False
    can_override_mandatory_findings: Literal[False] = False
    can_issue_trade_decision: Literal[False] = False

    @field_validator("purpose")
    @classmethod
    def reject_blank_purpose(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("framework-skill purpose cannot be blank")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        expected_skill_id = (
            f"jarvis.fundamental_framework.{self.framework.value}.v1"
        )
        if self.skill_id != expected_skill_id:
            raise ValueError("framework-skill ID must match its framework")

        expected_policy_id = f"jarvis.framework_score.{self.framework.value}"
        if self.score_policy_id != expected_policy_id:
            raise ValueError(
                "framework-skill score policy must match its framework"
            )

        if self.allowed_evidence != REQUIRED_FRAMEWORK_SKILL_EVIDENCE:
            raise ValueError(
                "framework-skill evidence must contain every required surface "
                "in canonical order"
            )
        if self.required_citations != REQUIRED_FRAMEWORK_SKILL_CITATIONS:
            raise ValueError(
                "framework-skill citations must contain every required "
                "namespace in canonical order"
            )
        if self.abstain_on != REQUIRED_FRAMEWORK_SKILL_ABSTENTIONS:
            raise ValueError(
                "framework-skill abstentions must contain every fail-closed "
                "reason in canonical order"
            )

        doctrine_fields = (
            self.doctrine_id is not None,
            self.doctrine_version is not None,
        )
        if len(set(doctrine_fields)) != 1:
            raise ValueError(
                "framework-skill doctrine ID and version must be supplied "
                "together"
            )
        if self.doctrine_id is not None:
            expected_doctrine_id = (
                "jarvis.fundamental_framework_doctrine."
                f"{self.framework.value}.v1"
            )
            if self.doctrine_id != expected_doctrine_id:
                raise ValueError(
                    "framework-skill doctrine ID must match its framework"
                )
        return self

    @computed_field
    @property
    def content_sha256(self) -> str:
        payload = self.model_dump(mode="json", exclude={"content_sha256"})
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return sha256(canonical.encode("utf-8")).hexdigest()


class FundamentalFrameworkSkillRegistry(FundamentalModel):
    """Validated, partial-or-complete set of framework-skill contracts."""

    schema_version: Literal[
        "jarvis.fundamental_framework_skill_registry.v1"
    ] = "jarvis.fundamental_framework_skill_registry.v1"
    skills: tuple[FundamentalFrameworkSkillContract, ...] = Field(
        min_length=1,
        max_length=len(FundamentalFramework),
    )

    @model_validator(mode="after")
    def validate_registry(self) -> Self:
        frameworks = tuple(skill.framework for skill in self.skills)
        if len(frameworks) != len(set(frameworks)):
            raise ValueError("framework-skill frameworks must be unique")
        canonical = tuple(
            framework
            for framework in FundamentalFramework
            if framework in frameworks
        )
        if frameworks != canonical:
            raise ValueError(
                "framework-skill registry must use canonical framework order"
            )
        skill_ids = tuple(skill.skill_id for skill in self.skills)
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("framework-skill IDs must be unique")
        return self

    def skill_for(
        self,
        framework: FundamentalFramework,
    ) -> FundamentalFrameworkSkillContract:
        for skill in self.skills:
            if skill.framework is framework:
                return skill
        raise ValueError(
            f"fundamental framework skill is not configured: {framework.value}"
        )

    @computed_field
    @property
    def content_sha256(self) -> str:
        payload = self.model_dump(mode="json", exclude={"content_sha256"})
        canonical = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
        return sha256(canonical.encode("utf-8")).hexdigest()
