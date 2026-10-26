import json
from enum import StrEnum
from hashlib import sha256
from typing import Literal, Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.technical import TechnicalModel


class RuntimeAgentRole(StrEnum):
    """Agent roles governed by versioned runtime research skills."""

    BULL = "bull"
    BEAR = "bear"
    JUDGE = "judge"
    FUNDAMENTAL_ANALYST = "fundamental_analyst"


class RuntimeEvidenceBoundary(StrEnum):
    """Immutable evidence surfaces a runtime skill may consume."""

    JUDGE_RELEASED_TECHNICAL = "judge_released_technical"
    DEBATE_TRANSCRIPT = "debate_transcript"
    CACHED_STRUCTURED_FUNDAMENTALS = "cached_structured_fundamentals"


class RuntimeCitationNamespace(StrEnum):
    """Identifier namespaces accepted as grounded citations."""

    QUALIFIED_TECHNICAL_EVIDENCE = "qualified_technical_evidence"
    STRUCTURED_DOCUMENT_REFERENCE = "structured_document_reference"


class RuntimeAbstentionReason(StrEnum):
    """Fail-closed states an LLM skill must report rather than conceal."""

    MISSING_EVIDENCE = "missing_evidence"
    STALE_EVIDENCE = "stale_evidence"
    INVALID_EVIDENCE = "invalid_evidence"
    OUT_OF_SCOPE = "out_of_scope"


class RuntimeSkillContract(TechnicalModel):
    """Provider-neutral, immutable policy boundary for one deployed agent."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.runtime_skill.v1"] = (
        "jarvis.runtime_skill.v1"
    )
    skill_id: str = Field(
        min_length=1,
        pattern=r"^jarvis\.[a-z][a-z0-9_]*\.v[1-9][0-9]*$",
    )
    agent_role: RuntimeAgentRole
    version: str = Field(pattern=r"^[1-9][0-9]*\.[0-9]+\.[0-9]+$")
    purpose: str = Field(min_length=1)
    instructions: tuple[str, ...] = Field(min_length=1)
    allowed_evidence: tuple[RuntimeEvidenceBoundary, ...] = Field(
        min_length=1
    )
    required_citations: tuple[RuntimeCitationNamespace, ...] = Field(
        min_length=1
    )
    output_schema_id: str = Field(min_length=1)
    abstain_on: tuple[RuntimeAbstentionReason, ...] = Field(min_length=1)
    provider_neutral: Literal[True] = True
    can_calculate_evidence: Literal[False] = False
    can_modify_evidence: Literal[False] = False

    @field_validator("purpose", "output_schema_id")
    @classmethod
    def reject_blank_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("runtime-skill text cannot be blank")
        return normalized

    @model_validator(mode="after")
    def validate_contract(self) -> Self:
        if any(not instruction.strip() for instruction in self.instructions):
            raise ValueError("runtime-skill instructions cannot be blank")
        for name, values in (
            ("instructions", self.instructions),
            ("evidence boundaries", self.allowed_evidence),
            ("citation namespaces", self.required_citations),
            ("abstention reasons", self.abstain_on),
        ):
            if len(values) != len(set(values)):
                raise ValueError(f"runtime-skill {name} must be unique")
        return self

    @property
    def content_sha256(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()


class RuntimeSkillRegistry(TechnicalModel):
    """Complete set of deployed agent-skill contracts for one release."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.runtime_skill_registry.v1"] = (
        "jarvis.runtime_skill_registry.v1"
    )
    skills: tuple[RuntimeSkillContract, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_registry(self) -> Self:
        roles = tuple(skill.agent_role for skill in self.skills)
        if len(roles) != len(set(roles)):
            raise ValueError("runtime-skill agent roles must be unique")
        if set(roles) != set(RuntimeAgentRole):
            raise ValueError("runtime-skill registry must define every role")
        skill_ids = tuple(skill.skill_id for skill in self.skills)
        if len(skill_ids) != len(set(skill_ids)):
            raise ValueError("runtime-skill IDs must be unique")
        return self

    def skill_for(self, role: RuntimeAgentRole) -> RuntimeSkillContract:
        for skill in self.skills:
            if skill.agent_role is role:
                return skill
        raise ValueError(f"runtime skill is not configured: {role.value}")

    @property
    def content_sha256(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
        )
        return sha256(canonical.encode("utf-8")).hexdigest()


_COMMON_ABSTENTION_REASONS = tuple(RuntimeAbstentionReason)


DEFAULT_RUNTIME_SKILLS = RuntimeSkillRegistry(
    skills=(
        RuntimeSkillContract(
            skill_id="jarvis.bull_runtime_skill.v1",
            agent_role=RuntimeAgentRole.BULL,
            version="1.0.0",
            purpose=(
                "Present the strongest conditional bullish case supported "
                "by Judge-released technical evidence."
            ),
            instructions=(
                "Treat weekly evidence as structural context and daily "
                "evidence as swing-entry timing.",
                "Argue from exact released evidence IDs and disclose every "
                "material timeframe conflict.",
                "Do not issue a trade-release decision or override a "
                "deterministic gate.",
            ),
            allowed_evidence=(
                RuntimeEvidenceBoundary.JUDGE_RELEASED_TECHNICAL,
                RuntimeEvidenceBoundary.DEBATE_TRANSCRIPT,
            ),
            required_citations=(
                RuntimeCitationNamespace.QUALIFIED_TECHNICAL_EVIDENCE,
            ),
            output_schema_id="jarvis.bull_bear_argument.v1",
            abstain_on=_COMMON_ABSTENTION_REASONS,
        ),
        RuntimeSkillContract(
            skill_id="jarvis.bear_runtime_skill.v1",
            agent_role=RuntimeAgentRole.BEAR,
            version="1.0.0",
            purpose=(
                "Stress-test the bullish thesis and explain downside or "
                "no-trade risk using Judge-released technical evidence."
            ),
            instructions=(
                "Test daily timing against weekly structure and weekly "
                "claims against daily confirmation.",
                "Rebut the strongest bullish claim using exact released "
                "evidence IDs without manufacturing conflict.",
                "Describe bearish evidence as long-entry risk and never "
                "recommend short execution.",
            ),
            allowed_evidence=(
                RuntimeEvidenceBoundary.JUDGE_RELEASED_TECHNICAL,
                RuntimeEvidenceBoundary.DEBATE_TRANSCRIPT,
            ),
            required_citations=(
                RuntimeCitationNamespace.QUALIFIED_TECHNICAL_EVIDENCE,
            ),
            output_schema_id="jarvis.bull_bear_argument.v1",
            abstain_on=_COMMON_ABSTENTION_REASONS,
        ),
        RuntimeSkillContract(
            skill_id="jarvis.judge_runtime_skill.v1",
            agent_role=RuntimeAgentRole.JUDGE,
            version="1.0.0",
            purpose=(
                "Compare the Bull and Bear cases against their cited "
                "evidence and explain the stronger case."
            ),
            instructions=(
                "Use weekly evidence for structural direction and daily "
                "evidence for entry timing.",
                "Use only evidence cited in the debate for decisive findings "
                "and expose unresolved conflicts.",
                "Do not invent entry, stop, target, fundamental facts, or "
                "override the deterministic trade-release policy.",
            ),
            allowed_evidence=(
                RuntimeEvidenceBoundary.JUDGE_RELEASED_TECHNICAL,
                RuntimeEvidenceBoundary.DEBATE_TRANSCRIPT,
            ),
            required_citations=(
                RuntimeCitationNamespace.QUALIFIED_TECHNICAL_EVIDENCE,
            ),
            output_schema_id="jarvis.debate_verdict.v1",
            abstain_on=_COMMON_ABSTENTION_REASONS,
        ),
        RuntimeSkillContract(
            skill_id="jarvis.fundamental_analyst_runtime_skill.v1",
            agent_role=RuntimeAgentRole.FUNDAMENTAL_ANALYST,
            version="1.0.0",
            purpose=(
                "Explain company financial health from scoped, cached "
                "structured fundamental documents."
            ),
            instructions=(
                "Keep consolidated and standalone reporting distinct and "
                "preserve period, unit, source, and retrieval metadata.",
                "Separate financial health from technical entry quality and "
                "state missing or conflicting evidence explicitly.",
                "Do not make qualitative management, moat, or annual-report "
                "claims when those sources are unavailable.",
            ),
            allowed_evidence=(
                RuntimeEvidenceBoundary.CACHED_STRUCTURED_FUNDAMENTALS,
            ),
            required_citations=(
                RuntimeCitationNamespace.STRUCTURED_DOCUMENT_REFERENCE,
            ),
            output_schema_id="jarvis.fundamental_analysis.v1",
            abstain_on=_COMMON_ABSTENTION_REASONS,
        ),
    )
)
