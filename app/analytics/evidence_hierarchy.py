from collections.abc import Iterable

from app.models.evidence_hierarchy import (
    DEFAULT_EVIDENCE_HIERARCHY,
    EvidenceCorrelationAssessment,
    EvidenceCorrelationGroup,
    EvidenceDecisionRole,
    EvidenceFamily,
    EvidenceFamilyFinding,
    EvidenceFamilyState,
    EvidenceHierarchyPolicy,
    HierarchicalEvidenceAssessment,
)
from app.models.multi_timeframe_evidence import MultiTimeframeEvidencePackage
from app.models.signals import SignalCategory, SignalDirection
from app.models.trade_decision import MarketCondition


_DIRECTIONAL_STATES = frozenset(
    {
        EvidenceFamilyState.BULLISH,
        EvidenceFamilyState.NEUTRAL,
        EvidenceFamilyState.BEARISH,
        EvidenceFamilyState.CONFLICTED,
        EvidenceFamilyState.INSUFFICIENT,
    }
)


_TECHNICAL_FAMILY_SELECTORS = {
    EvidenceFamily.MARKET_STRUCTURE: (
        ("market_structure.",),
        None,
    ),
    EvidenceFamily.PRICE_LOCATION: (
        ("fvg_context.", "support_resistance."),
        None,
    ),
    EvidenceFamily.PARTICIPATION: ((), SignalCategory.VOLUME),
    EvidenceFamily.TREND_CONFIRMATION: ((), SignalCategory.TREND),
    EvidenceFamily.MOMENTUM_CONFIRMATION: ((), SignalCategory.MOMENTUM),
    EvidenceFamily.CANDLESTICK_CONTEXT: ((), SignalCategory.CANDLESTICK),
    EvidenceFamily.VOLATILITY_CONTEXT: ((), SignalCategory.VOLATILITY),
}


def build_multi_timeframe_technical_findings(
    package: MultiTimeframeEvidencePackage,
) -> tuple[EvidenceFamilyFinding, ...]:
    """Translate released daily/weekly evidence without changing its meaning.

    This creates only the technical family findings. Fundamental health,
    entry-trigger and reward/risk gates must be supplied by their owning
    deterministic stages before the complete hierarchy is assessed.
    """

    if not isinstance(package, MultiTimeframeEvidencePackage):
        raise ValueError(
            "technical evidence translation requires a released package"
        )

    evidence = tuple(
        item
        for context in (package.weekly, package.daily)
        for item in context.evidence
    )
    findings: list[EvidenceFamilyFinding] = []
    for definition in DEFAULT_EVIDENCE_HIERARCHY.families:
        family = definition.family
        if family is EvidenceFamily.CPR_CONTEXT:
            findings.append(_cpr_finding(package))
            continue
        selector = _TECHNICAL_FAMILY_SELECTORS.get(family)
        if selector is None:
            continue
        prefixes, category = selector
        selected = tuple(
            item
            for item in evidence
            if (
                category is not None
                and item.evidence.category is category
            )
            or any(
                item.evidence.evidence_id.startswith(prefix)
                for prefix in prefixes
            )
        )
        findings.append(
            EvidenceFamilyFinding(
                family=family,
                state=_state_from_directions(
                    item.evidence.direction for item in selected
                ),
                evidence_ids=tuple(
                    item.qualified_evidence_id for item in selected
                ),
                rationale=_technical_family_rationale(family, selected),
            )
        )
    return tuple(findings)


def _cpr_finding(
    package: MultiTimeframeEvidencePackage,
) -> EvidenceFamilyFinding:
    from app.analytics.cpr_policy import (
        CPRTimeframeRelationship,
        evaluate_cpr_trade_policy,
    )

    assessment = evaluate_cpr_trade_policy(
        package.daily.cpr,
        package.weekly.cpr,
    )
    state = {
        CPRTimeframeRelationship.ALIGNED_BULLISH: (
            EvidenceFamilyState.BULLISH
        ),
        CPRTimeframeRelationship.ALIGNED_BEARISH: (
            EvidenceFamilyState.BEARISH
        ),
        CPRTimeframeRelationship.CONFLICTED: (
            EvidenceFamilyState.CONFLICTED
        ),
        CPRTimeframeRelationship.MIXED: EvidenceFamilyState.NEUTRAL,
        CPRTimeframeRelationship.INSUFFICIENT: (
            EvidenceFamilyState.INSUFFICIENT
        ),
    }[assessment.relationship]
    return EvidenceFamilyFinding(
        family=EvidenceFamily.CPR_CONTEXT,
        state=state,
        evidence_ids=assessment.evidence_ids,
        rationale=assessment.rationale,
    )


def _state_from_directions(
    directions: Iterable[SignalDirection],
) -> EvidenceFamilyState:
    available = set(directions)
    directional = available & {
        SignalDirection.BULLISH,
        SignalDirection.BEARISH,
    }
    if len(directional) > 1:
        return EvidenceFamilyState.CONFLICTED
    if directional == {SignalDirection.BULLISH}:
        return EvidenceFamilyState.BULLISH
    if directional == {SignalDirection.BEARISH}:
        return EvidenceFamilyState.BEARISH
    if SignalDirection.NEUTRAL in available:
        return EvidenceFamilyState.NEUTRAL
    return EvidenceFamilyState.INSUFFICIENT


def _technical_family_rationale(family, selected) -> str:
    if not selected:
        return (
            f"No released daily or weekly {family.value} evidence was "
            "available at the evaluation time."
        )
    directions = ", ".join(
        f"{item.timeframe.value}={item.evidence.direction.value}"
        for item in selected
    )
    return (
        f"The {family.value} family preserves {len(selected)} released "
        f"daily/weekly observations: {directions}."
    )
_GATE_STATES = frozenset(
    {
        EvidenceFamilyState.PASS,
        EvidenceFamilyState.FAIL,
        EvidenceFamilyState.NOT_APPLICABLE,
        EvidenceFamilyState.INSUFFICIENT,
    }
)


def assess_evidence_hierarchy(
    findings: Iterable[EvidenceFamilyFinding],
    *,
    policy: EvidenceHierarchyPolicy = DEFAULT_EVIDENCE_HIERARCHY,
    not_applicable_families: frozenset[EvidenceFamily] = frozenset(),
) -> HierarchicalEvidenceAssessment:
    """Apply primary context, correlated support and mandatory gates.

    Supporting families are summarized once per correlation group. They cannot
    create bullish primary context or independently block release.
    """

    ordered_findings = tuple(findings)
    by_family = {finding.family: finding for finding in ordered_findings}
    if len(by_family) != len(ordered_findings):
        raise ValueError("evidence-family findings must be unique")
    if set(by_family) != set(EvidenceFamily):
        raise ValueError("assessment requires one finding for every family")
    invalid_not_applicable = not_applicable_families - {
        definition.family
        for definition in policy.families
        if definition.role
        not in {
            EvidenceDecisionRole.PRIMARY_CONTEXT,
            EvidenceDecisionRole.SUPPORTING_CONFIRMATION,
        }
    }
    if invalid_not_applicable:
        raise ValueError(
            "only gate families may be declared not applicable"
        )

    primary: list[EvidenceFamilyFinding] = []
    supporting_by_group: dict[
        EvidenceCorrelationGroup, list[EvidenceFamilyFinding]
    ] = {}
    gates: list[EvidenceFamilyFinding] = []

    for definition in policy.families:
        finding = by_family[definition.family]
        allowed_states = (
            _DIRECTIONAL_STATES
            if definition.role
            in {
                EvidenceDecisionRole.PRIMARY_CONTEXT,
                EvidenceDecisionRole.SUPPORTING_CONFIRMATION,
            }
            else _GATE_STATES
        )
        if finding.state not in allowed_states:
            raise ValueError(
                f"{definition.family.value} does not accept the "
                f"{finding.state.value} state"
            )
        declared_not_applicable = (
            definition.family in not_applicable_families
        )
        if (
            finding.state is EvidenceFamilyState.NOT_APPLICABLE
        ) != declared_not_applicable:
            raise ValueError(
                f"{definition.family.value} not-applicable state must match "
                "the declared assessment scope"
            )
        if definition.role is EvidenceDecisionRole.PRIMARY_CONTEXT:
            primary.append(finding)
        elif definition.role is EvidenceDecisionRole.SUPPORTING_CONFIRMATION:
            supporting_by_group.setdefault(
                definition.correlation_group, []
            ).append(finding)
        else:
            gates.append(finding)

    primary_condition = _condition_from_directional_findings(primary)
    supporting_groups = tuple(
        EvidenceCorrelationAssessment(
            correlation_group=group,
            families=tuple(item.family for item in group_findings),
            condition=_condition_from_directional_findings(group_findings),
            evidence_ids=_unique_ids(group_findings),
        )
        for group, group_findings in supporting_by_group.items()
    )

    blocking_families = tuple(
        definition.family
        for definition in policy.families
        if definition.can_block_release
        and (
            (
                definition.role is EvidenceDecisionRole.PRIMARY_CONTEXT
                and primary_condition is not MarketCondition.BULLISH
            )
            or (
                definition.role
                not in {
                    EvidenceDecisionRole.PRIMARY_CONTEXT,
                    EvidenceDecisionRole.SUPPORTING_CONFIRMATION,
                }
                and by_family[definition.family].state
                not in {
                    EvidenceFamilyState.PASS,
                    EvidenceFamilyState.NOT_APPLICABLE,
                }
            )
        )
    )
    release_eligible = (
        primary_condition is MarketCondition.BULLISH
        and not blocking_families
    )
    all_findings = tuple(by_family[item.family] for item in policy.families)
    return HierarchicalEvidenceAssessment(
        primary_condition=primary_condition,
        supporting_groups=supporting_groups,
        gate_states=tuple(gates),
        blocking_families=blocking_families,
        not_applicable_families=tuple(
            definition.family
            for definition in policy.families
            if definition.family in not_applicable_families
        ),
        release_eligible=release_eligible,
        evidence_ids=_unique_ids(all_findings),
        rationale=_rationale(
            primary_condition=primary_condition,
            supporting_groups=supporting_groups,
            gates=gates,
            blockers=blocking_families,
        ),
    )


def _condition_from_directional_findings(
    findings: Iterable[EvidenceFamilyFinding],
) -> MarketCondition:
    states = {finding.state for finding in findings}
    if EvidenceFamilyState.INSUFFICIENT in states:
        return MarketCondition.INSUFFICIENT
    if EvidenceFamilyState.CONFLICTED in states:
        return MarketCondition.CONFLICTED
    directional = states & {
        EvidenceFamilyState.BULLISH,
        EvidenceFamilyState.BEARISH,
    }
    if len(directional) > 1:
        return MarketCondition.CONFLICTED
    if EvidenceFamilyState.NEUTRAL in states:
        return MarketCondition.NEUTRAL
    if directional == {EvidenceFamilyState.BULLISH}:
        return MarketCondition.BULLISH
    if directional == {EvidenceFamilyState.BEARISH}:
        return MarketCondition.BEARISH
    return MarketCondition.NEUTRAL


def _unique_ids(findings: Iterable[EvidenceFamilyFinding]) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            evidence_id
            for finding in findings
            for evidence_id in finding.evidence_ids
        )
    )


def _rationale(
    *,
    primary_condition: MarketCondition,
    supporting_groups: tuple[EvidenceCorrelationAssessment, ...],
    gates: list[EvidenceFamilyFinding],
    blockers: tuple[EvidenceFamily, ...],
) -> str:
    support = ", ".join(
        f"{group.correlation_group.value}={group.condition.value}"
        for group in supporting_groups
    ) or "none"
    gate_summary = ", ".join(
        f"{gate.family.value}={gate.state.value}" for gate in gates
    ) or "none"
    blocker_summary = ", ".join(item.value for item in blockers) or "none"
    return (
        f"Primary context is {primary_condition.value}. Correlated supporting "
        f"groups: {support}. Gates: {gate_summary}. Blocking families: "
        f"{blocker_summary}."
    )
