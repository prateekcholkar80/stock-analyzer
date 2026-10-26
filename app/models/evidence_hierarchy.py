from enum import StrEnum
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from app.models.technical import TechnicalModel
from app.models.trade_decision import MarketCondition


class EvidenceFamily(StrEnum):
    """Independent evidence families used by the swing release policy."""

    MARKET_STRUCTURE = "market_structure"
    PRICE_LOCATION = "price_location"
    PARTICIPATION = "participation"
    TREND_CONFIRMATION = "trend_confirmation"
    MOMENTUM_CONFIRMATION = "momentum_confirmation"
    CPR_CONTEXT = "cpr_context"
    CANDLESTICK_CONTEXT = "candlestick_context"
    VOLATILITY_CONTEXT = "volatility_context"
    FUNDAMENTAL_HEALTH = "fundamental_health"
    ENTRY_TRIGGER = "entry_trigger"
    RISK_TARGET_FEASIBILITY = "risk_target_feasibility"


class EvidenceDecisionRole(StrEnum):
    """Authority an evidence family may have in the decision hierarchy."""

    PRIMARY_CONTEXT = "primary_context"
    SUPPORTING_CONFIRMATION = "supporting_confirmation"
    ELIGIBILITY_GATE = "eligibility_gate"
    ENTRY_TRIGGER = "entry_trigger"
    FEASIBILITY_GATE = "feasibility_gate"


class EvidenceCorrelationGroup(StrEnum):
    """Groups whose members must not be counted as independent votes."""

    MARKET_STRUCTURE = "market_structure"
    PRICE_CONTEXT = "price_context"
    PARTICIPATION = "participation"
    OHLC_DERIVED_CONFIRMATION = "ohlc_derived_confirmation"
    FUNDAMENTAL = "fundamental"
    ENTRY = "entry"
    RISK = "risk"


class EvidenceFamilyState(StrEnum):
    """Deterministic state supplied by one evidence-family evaluator."""

    BULLISH = "bullish"
    NEUTRAL = "neutral"
    BEARISH = "bearish"
    CONFLICTED = "conflicted"
    PASS = "pass"
    FAIL = "fail"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT = "insufficient"


class EvidenceFamilyFinding(TechnicalModel):
    """Point-in-time finding for one configured evidence family."""

    model_config = ConfigDict(frozen=True, strict=True)

    family: EvidenceFamily
    state: EvidenceFamilyState
    evidence_ids: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_finding(self) -> Self:
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("evidence-family IDs must be unique")
        if any(not evidence_id.strip() for evidence_id in self.evidence_ids):
            raise ValueError("evidence-family IDs cannot be blank")
        return self


class EvidenceCorrelationAssessment(TechnicalModel):
    """One correlated group, represented once rather than as indicator votes."""

    model_config = ConfigDict(frozen=True, strict=True)

    correlation_group: EvidenceCorrelationGroup
    families: tuple[EvidenceFamily, ...] = Field(min_length=1)
    condition: MarketCondition
    evidence_ids: tuple[str, ...] = ()


class HierarchicalEvidenceAssessment(TechnicalModel):
    """Traceable hierarchy outcome used ahead of deterministic trade release."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.evidence_hierarchy_assessment.v1"] = (
        "jarvis.evidence_hierarchy_assessment.v1"
    )
    primary_condition: MarketCondition
    supporting_groups: tuple[EvidenceCorrelationAssessment, ...] = ()
    gate_states: tuple[EvidenceFamilyFinding, ...]
    blocking_families: tuple[EvidenceFamily, ...] = ()
    not_applicable_families: tuple[EvidenceFamily, ...] = ()
    release_eligible: bool
    evidence_ids: tuple[str, ...] = ()
    rationale: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_assessment(self) -> Self:
        if len(self.blocking_families) != len(set(self.blocking_families)):
            raise ValueError("blocking evidence families must be unique")
        if len(self.not_applicable_families) != len(
            set(self.not_applicable_families)
        ):
            raise ValueError("not-applicable evidence families must be unique")
        if set(self.blocking_families) & set(self.not_applicable_families):
            raise ValueError(
                "not-applicable evidence families cannot also be blockers"
            )
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("hierarchy evidence IDs must be unique")
        if self.release_eligible and (
            self.primary_condition is not MarketCondition.BULLISH
            or self.blocking_families
        ):
            raise ValueError(
                "release eligibility requires bullish primary context and no "
                "blocking families"
            )
        return self


class EvidenceFamilyPolicy(TechnicalModel):
    """Immutable authority and dependency rules for one evidence family."""

    model_config = ConfigDict(frozen=True, strict=True)

    family: EvidenceFamily
    role: EvidenceDecisionRole
    correlation_group: EvidenceCorrelationGroup
    evaluation_order: int = Field(ge=1)
    dependencies: tuple[EvidenceFamily, ...] = ()
    can_block_release: bool
    can_create_buy: Literal[False] = False

    @model_validator(mode="after")
    def validate_family_policy(self) -> Self:
        if self.family in self.dependencies:
            raise ValueError("evidence family cannot depend on itself")
        if len(self.dependencies) != len(set(self.dependencies)):
            raise ValueError("evidence-family dependencies must be unique")
        if (
            self.role is EvidenceDecisionRole.SUPPORTING_CONFIRMATION
            and self.can_block_release
        ):
            raise ValueError(
                "supporting confirmation cannot independently block release"
            )
        if (
            self.role
            in {
                EvidenceDecisionRole.ELIGIBILITY_GATE,
                EvidenceDecisionRole.ENTRY_TRIGGER,
                EvidenceDecisionRole.FEASIBILITY_GATE,
            }
            and not self.can_block_release
        ):
            raise ValueError("decision gates must be able to block release")
        return self


class EvidenceHierarchyPolicy(TechnicalModel):
    """Complete, non-equal-weight evidence hierarchy for swing decisions."""

    model_config = ConfigDict(frozen=True, strict=True)

    schema_version: Literal["jarvis.evidence_hierarchy.v1"] = (
        "jarvis.evidence_hierarchy.v1"
    )
    aggregation_method: Literal["hierarchical_gates"] = "hierarchical_gates"
    supporting_aggregation_method: Literal[
        "correlated_family_confluence"
    ] = "correlated_family_confluence"
    families: tuple[EvidenceFamilyPolicy, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_hierarchy(self) -> Self:
        by_family = {item.family: item for item in self.families}
        if len(by_family) != len(self.families):
            raise ValueError("evidence hierarchy families must be unique")
        if set(by_family) != set(EvidenceFamily):
            raise ValueError("evidence hierarchy must define every family")

        orders = [item.evaluation_order for item in self.families]
        expected_orders = list(range(1, len(self.families) + 1))
        if sorted(orders) != expected_orders:
            raise ValueError(
                "evidence hierarchy order must be unique and contiguous"
            )

        for item in self.families:
            for dependency in item.dependencies:
                if (
                    by_family[dependency].evaluation_order
                    >= item.evaluation_order
                ):
                    raise ValueError(
                        "evidence-family dependencies must be evaluated first"
                    )

        if any(item.can_create_buy for item in self.families):
            raise ValueError("no evidence family may create a BUY by itself")
        return self

    def policy_for(self, family: EvidenceFamily) -> EvidenceFamilyPolicy:
        for policy in self.families:
            if policy.family is family:
                return policy
        raise ValueError(f"evidence family is not configured: {family.value}")


DEFAULT_EVIDENCE_HIERARCHY = EvidenceHierarchyPolicy(
    families=(
        EvidenceFamilyPolicy(
            family=EvidenceFamily.MARKET_STRUCTURE,
            role=EvidenceDecisionRole.PRIMARY_CONTEXT,
            correlation_group=EvidenceCorrelationGroup.MARKET_STRUCTURE,
            evaluation_order=1,
            can_block_release=True,
        ),
        EvidenceFamilyPolicy(
            family=EvidenceFamily.PRICE_LOCATION,
            role=EvidenceDecisionRole.PRIMARY_CONTEXT,
            correlation_group=EvidenceCorrelationGroup.PRICE_CONTEXT,
            evaluation_order=2,
            dependencies=(EvidenceFamily.MARKET_STRUCTURE,),
            can_block_release=True,
        ),
        EvidenceFamilyPolicy(
            family=EvidenceFamily.PARTICIPATION,
            role=EvidenceDecisionRole.SUPPORTING_CONFIRMATION,
            correlation_group=EvidenceCorrelationGroup.PARTICIPATION,
            evaluation_order=3,
            dependencies=(
                EvidenceFamily.MARKET_STRUCTURE,
                EvidenceFamily.PRICE_LOCATION,
            ),
            can_block_release=False,
        ),
        EvidenceFamilyPolicy(
            family=EvidenceFamily.TREND_CONFIRMATION,
            role=EvidenceDecisionRole.SUPPORTING_CONFIRMATION,
            correlation_group=(
                EvidenceCorrelationGroup.OHLC_DERIVED_CONFIRMATION
            ),
            evaluation_order=4,
            dependencies=(
                EvidenceFamily.MARKET_STRUCTURE,
                EvidenceFamily.PRICE_LOCATION,
            ),
            can_block_release=False,
        ),
        EvidenceFamilyPolicy(
            family=EvidenceFamily.MOMENTUM_CONFIRMATION,
            role=EvidenceDecisionRole.SUPPORTING_CONFIRMATION,
            correlation_group=(
                EvidenceCorrelationGroup.OHLC_DERIVED_CONFIRMATION
            ),
            evaluation_order=5,
            dependencies=(
                EvidenceFamily.MARKET_STRUCTURE,
                EvidenceFamily.PRICE_LOCATION,
            ),
            can_block_release=False,
        ),
        EvidenceFamilyPolicy(
            family=EvidenceFamily.CPR_CONTEXT,
            role=EvidenceDecisionRole.SUPPORTING_CONFIRMATION,
            correlation_group=(
                EvidenceCorrelationGroup.OHLC_DERIVED_CONFIRMATION
            ),
            evaluation_order=6,
            dependencies=(
                EvidenceFamily.MARKET_STRUCTURE,
                EvidenceFamily.PRICE_LOCATION,
            ),
            can_block_release=False,
        ),
        EvidenceFamilyPolicy(
            family=EvidenceFamily.CANDLESTICK_CONTEXT,
            role=EvidenceDecisionRole.SUPPORTING_CONFIRMATION,
            correlation_group=(
                EvidenceCorrelationGroup.OHLC_DERIVED_CONFIRMATION
            ),
            evaluation_order=7,
            dependencies=(
                EvidenceFamily.MARKET_STRUCTURE,
                EvidenceFamily.PRICE_LOCATION,
            ),
            can_block_release=False,
        ),
        EvidenceFamilyPolicy(
            family=EvidenceFamily.VOLATILITY_CONTEXT,
            role=EvidenceDecisionRole.SUPPORTING_CONFIRMATION,
            correlation_group=(
                EvidenceCorrelationGroup.OHLC_DERIVED_CONFIRMATION
            ),
            evaluation_order=8,
            dependencies=(
                EvidenceFamily.MARKET_STRUCTURE,
                EvidenceFamily.PRICE_LOCATION,
            ),
            can_block_release=False,
        ),
        EvidenceFamilyPolicy(
            family=EvidenceFamily.FUNDAMENTAL_HEALTH,
            role=EvidenceDecisionRole.ELIGIBILITY_GATE,
            correlation_group=EvidenceCorrelationGroup.FUNDAMENTAL,
            evaluation_order=9,
            can_block_release=True,
        ),
        EvidenceFamilyPolicy(
            family=EvidenceFamily.ENTRY_TRIGGER,
            role=EvidenceDecisionRole.ENTRY_TRIGGER,
            correlation_group=EvidenceCorrelationGroup.ENTRY,
            evaluation_order=10,
            dependencies=(
                EvidenceFamily.MARKET_STRUCTURE,
                EvidenceFamily.PRICE_LOCATION,
                EvidenceFamily.FUNDAMENTAL_HEALTH,
            ),
            can_block_release=True,
        ),
        EvidenceFamilyPolicy(
            family=EvidenceFamily.RISK_TARGET_FEASIBILITY,
            role=EvidenceDecisionRole.FEASIBILITY_GATE,
            correlation_group=EvidenceCorrelationGroup.RISK,
            evaluation_order=11,
            dependencies=(
                EvidenceFamily.MARKET_STRUCTURE,
                EvidenceFamily.PRICE_LOCATION,
                EvidenceFamily.ENTRY_TRIGGER,
            ),
            can_block_release=True,
        ),
    )
)
