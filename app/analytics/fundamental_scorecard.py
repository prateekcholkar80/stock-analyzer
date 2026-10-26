"""Deterministic, policy-injected fundamental scorecard calculation."""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from statistics import median

from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_assessment import (
    FundamentalAssessmentCoverageStatus,
    FundamentalFindingSeverity,
    FundamentalFindingStatus,
    FundamentalMandatoryAssessment,
)
from app.models.fundamental_frameworks import (
    FundamentalFramework,
    FundamentalFrameworkApplicability,
    FundamentalFrameworkApplicabilityAssessment,
    FundamentalFrameworkApplicabilityStatus,
    FundamentalFrameworkEvidenceFamily,
    FundamentalFrameworkEvidenceCoverage,
    FundamentalFrameworkEvidenceStatus,
)
from app.models.fundamental_metrics import (
    FundamentalMetricAvailability,
    FundamentalMetricCitation,
    FundamentalMetricPeriodKind,
    FundamentalMetricValueStatus,
    NormalizedFinancialEvidence,
    NormalizedFundamentalMetric,
)
from app.models.fundamental_scoring import (
    FundamentalEvidenceFamilyScoreResult,
    FundamentalEvidenceFamilyScoringPolicy,
    FundamentalFrameworkScoreResult,
    FundamentalFrameworkScoringPolicy,
    FundamentalMetricAggregation,
    FundamentalMetricScoreResult,
    FundamentalMetricScoringRule,
    FundamentalScoreGrade,
    FundamentalScoreGradeThresholds,
    FundamentalScorePolicyBundle,
    FundamentalScoreResultStatus,
    FundamentalScoreWithholdingReason,
    FundamentalScorecardResult,
    FundamentalScorecardStance,
)


_SCORE_QUANTUM = Decimal("0.01")
_CONTRIBUTION_QUANTUM = Decimal("0.0001")
_TOTAL_WEIGHT_BPS = Decimal("10000")
_AVAILABLE_VALUES = frozenset(
    {
        FundamentalMetricValueStatus.PRESENT,
        FundamentalMetricValueStatus.ZERO,
    }
)
_SEVERITY_RANK = {
    FundamentalFindingSeverity.NONE: 0,
    FundamentalFindingSeverity.LOW: 1,
    FundamentalFindingSeverity.MEDIUM: 2,
    FundamentalFindingSeverity.HIGH: 3,
    FundamentalFindingSeverity.CRITICAL: 4,
}


@dataclass(frozen=True, slots=True)
class _SeriesSelection:
    status: FundamentalScoreResultStatus
    withholding_reason: FundamentalScoreWithholdingReason | None
    metrics: tuple[NormalizedFundamentalMetric, ...]
    observed_value: Decimal | None
    limitation: str | None


def build_fundamental_scorecard(
    evidence: NormalizedFinancialEvidence,
    mandatory_assessment: FundamentalMandatoryAssessment,
    applicability_assessment: FundamentalFrameworkApplicabilityAssessment,
    policy_bundle: FundamentalScorePolicyBundle,
    *,
    scored_at: datetime,
) -> FundamentalScorecardResult:
    """Build one reproducible scorecard from immutable released inputs."""

    _validate_inputs(
        evidence,
        mandatory_assessment,
        applicability_assessment,
        scored_at,
    )
    applicability_by_framework = {
        item.framework: item for item in applicability_assessment.results
    }
    policy_by_framework = {
        item.framework: item for item in policy_bundle.framework_policies
    }
    selected = applicability_assessment.selected_primary_frameworks
    framework_results = tuple(
        _score_framework(
            evidence,
            applicability_by_framework[framework],
            policy_by_framework[framework],
            policy_bundle.grade_thresholds,
            selected_primary=framework in selected,
        )
        for framework in FundamentalFramework
    )
    review_required = mandatory_assessment.review_required or (
        _contains_conflicting_score_evidence(framework_results)
    )
    stance = _determine_stance(
        mandatory_assessment,
        framework_results,
        policy_bundle,
        review_required=review_required,
    )
    limitations = _canonical_limitations(
        (
            *evidence.limitations,
            *mandatory_assessment.limitations,
            *applicability_assessment.limitations,
            *policy_bundle.limitations,
            *(
                limitation
                for result in framework_results
                for limitation in result.limitations
            ),
        )
    )
    return FundamentalScorecardResult(
        package_id=evidence.package_id,
        manifest_fingerprint=evidence.manifest_fingerprint,
        normalized_evidence_fingerprint=evidence.evidence_fingerprint,
        mandatory_assessment_fingerprint=(
            mandatory_assessment.assessment_fingerprint
        ),
        applicability_assessment_fingerprint=(
            applicability_assessment.assessment_fingerprint
        ),
        score_policy_bundle_id=policy_bundle.policy_bundle_id,
        score_policy_bundle_version=policy_bundle.policy_bundle_version,
        score_policy_bundle_fingerprint=policy_bundle.policy_fingerprint,
        issuer=evidence.issuer,
        evidence_normalized_at=evidence.normalized_at,
        mandatory_assessed_at=mandatory_assessment.assessed_at,
        applicability_assessed_at=applicability_assessment.assessed_at,
        scored_at=scored_at,
        package_expires_at=evidence.package_expires_at,
        decision_influence_mode=policy_bundle.decision_influence_mode,
        approval_reference=policy_bundle.approval_reference,
        framework_results=framework_results,
        selected_primary_frameworks=selected,
        stance=stance,
        review_required=review_required,
        limitations=limitations,
    )


def _validate_inputs(
    evidence: NormalizedFinancialEvidence,
    mandatory_assessment: FundamentalMandatoryAssessment,
    applicability_assessment: FundamentalFrameworkApplicabilityAssessment,
    scored_at: datetime,
) -> None:
    if scored_at.tzinfo is None or scored_at.utcoffset() is None:
        raise ValueError("fundamental scoring time requires timezone")
    if not (
        applicability_assessment.assessed_at
        <= scored_at
        < evidence.package_expires_at
    ):
        raise ValueError("scoring requires current completed applicability")
    if (
        mandatory_assessment.package_id != evidence.package_id
        or mandatory_assessment.manifest_fingerprint
        != evidence.manifest_fingerprint
        or mandatory_assessment.normalized_evidence_fingerprint
        != evidence.evidence_fingerprint
        or mandatory_assessment.issuer != evidence.issuer
        or mandatory_assessment.package_expires_at != evidence.package_expires_at
    ):
        raise ValueError(
            "mandatory assessment and evidence do not share one package"
        )
    if (
        applicability_assessment.package_id != evidence.package_id
        or applicability_assessment.manifest_fingerprint
        != evidence.manifest_fingerprint
        or applicability_assessment.normalized_evidence_fingerprint
        != evidence.evidence_fingerprint
        or applicability_assessment.mandatory_assessment_fingerprint
        != mandatory_assessment.assessment_fingerprint
        or applicability_assessment.issuer != evidence.issuer
        or applicability_assessment.evidence_normalized_at
        != evidence.normalized_at
        or applicability_assessment.mandatory_assessed_at
        != mandatory_assessment.assessed_at
        or applicability_assessment.package_expires_at
        != evidence.package_expires_at
    ):
        raise ValueError(
            "applicability and mandatory evidence do not share one package"
        )


def _score_framework(
    evidence: NormalizedFinancialEvidence,
    applicability: FundamentalFrameworkApplicability,
    policy: FundamentalFrameworkScoringPolicy,
    grade_thresholds: FundamentalScoreGradeThresholds,
    *,
    selected_primary: bool,
) -> FundamentalFrameworkScoreResult:
    if applicability.framework is not policy.framework:
        raise ValueError("framework applicability and score policy differ")
    applicability_families = tuple(
        item.family for item in applicability.evidence_coverage
    )
    policy_families = tuple(item.family for item in policy.family_policies)
    if applicability_families != policy_families:
        raise ValueError(
            "framework score policy must cover its exact applicability families"
        )

    if (
        applicability.status
        is FundamentalFrameworkApplicabilityStatus.INELIGIBLE
    ):
        family_results = tuple(
            _not_applicable_family(policy.framework, family_policy)
            for family_policy in policy.family_policies
        )
        return _unreleased_framework(
            applicability,
            policy,
            family_results,
            selected_primary=selected_primary,
            status=FundamentalScoreResultStatus.NOT_APPLICABLE,
            reason=FundamentalScoreWithholdingReason.INELIGIBLE_FRAMEWORK,
            limitation="Framework is excluded by applicability policy.",
        )

    coverage_by_family = {
        item.family: item for item in applicability.evidence_coverage
    }
    family_results = tuple(
        _score_family(
            evidence,
            policy.framework,
            family_policy,
            coverage_by_family[family_policy.family],
        )
        for family_policy in policy.family_policies
    )
    calculated = tuple(
        item
        for item in family_results
        if item.status is FundamentalScoreResultStatus.CALCULATED
    )
    coverage_bps = sum(item.framework_weight_bps for item in calculated)
    can_release = (
        applicability.status is FundamentalFrameworkApplicabilityStatus.ELIGIBLE
        and coverage_bps >= policy.minimum_family_coverage_bps
    )
    limitations = _canonical_limitations(
        (
            *(item.summary for item in applicability.limitations),
            *(
                limitation
                for family in family_results
                for limitation in family.limitations
            ),
            *policy.limitations,
        )
    )
    if can_release:
        score = sum(
            item.weighted_score or Decimal("0") for item in calculated
        ).quantize(_SCORE_QUANTUM, rounding=ROUND_HALF_UP)
        return FundamentalFrameworkScoreResult(
            result_id=(
                f"fundamental.framework_score:{policy.framework.value}"
            ),
            framework=policy.framework,
            status=FundamentalScoreResultStatus.CALCULATED,
            applicability_status=applicability.status,
            applicability_fingerprint=applicability.applicability_fingerprint,
            scoring_policy_id=policy.policy_id,
            scoring_policy_version=policy.policy_version,
            scoring_policy_fingerprint=policy.policy_fingerprint,
            minimum_family_coverage_bps=policy.minimum_family_coverage_bps,
            selected_primary=selected_primary,
            family_results=family_results,
            score=score,
            grade=_grade_score(score, grade_thresholds),
            limitations=limitations,
        )

    reason = _framework_withholding_reason(applicability, family_results)
    return _unreleased_framework(
        applicability,
        policy,
        family_results,
        selected_primary=selected_primary,
        status=FundamentalScoreResultStatus.WITHHELD,
        reason=reason,
        limitation="Framework score did not satisfy released evidence coverage.",
        additional_limitations=limitations,
    )


def _score_family(
    evidence: NormalizedFinancialEvidence,
    framework: FundamentalFramework,
    policy: FundamentalEvidenceFamilyScoringPolicy,
    coverage: FundamentalFrameworkEvidenceCoverage,
) -> FundamentalEvidenceFamilyScoreResult:
    if coverage.status is not FundamentalFrameworkEvidenceStatus.AVAILABLE:
        reason = _coverage_withholding_reason(coverage.status)
        metric_results = tuple(
            _unreleased_metric(
                framework,
                policy.family,
                rule,
                status=FundamentalScoreResultStatus.WITHHELD,
                reason=reason,
                limitation=coverage.limitation or "Family evidence unavailable.",
                metric_keys=(
                    coverage.metric_keys
                    if reason
                    is FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE
                    else ()
                ),
                citations=(
                    coverage.citations
                    if reason
                    is FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE
                    else ()
                ),
            )
            for rule in policy.metric_rules
        )
        return FundamentalEvidenceFamilyScoreResult(
            result_id=(
                f"fundamental.family_score:{framework.value}:"
                f"{policy.family.value}"
            ),
            framework=framework,
            family=policy.family,
            policy_fingerprint=policy.policy_fingerprint,
            status=FundamentalScoreResultStatus.WITHHELD,
            withholding_reason=reason,
            framework_weight_bps=policy.framework_weight_bps,
            minimum_rule_coverage_bps=policy.minimum_rule_coverage_bps,
            maximum_family_score=policy.maximum_family_score,
            metric_results=metric_results,
            limitations=(coverage.limitation or "Family evidence unavailable.",),
        )

    metric_results = tuple(
        _score_metric(evidence, framework, policy.family, rule)
        for rule in policy.metric_rules
    )
    calculated = tuple(
        item
        for item in metric_results
        if item.status is FundamentalScoreResultStatus.CALCULATED
    )
    coverage_bps = sum(item.component_weight_bps for item in calculated)
    if coverage_bps >= policy.minimum_rule_coverage_bps:
        raw_score = sum(
            item.weighted_score or Decimal("0") for item in calculated
        )
        score = min(raw_score, policy.maximum_family_score).quantize(
            _SCORE_QUANTUM,
            rounding=ROUND_HALF_UP,
        )
        weighted_score = (
            score
            * Decimal(policy.framework_weight_bps)
            / _TOTAL_WEIGHT_BPS
        ).quantize(_CONTRIBUTION_QUANTUM, rounding=ROUND_HALF_UP)
        limitations = _canonical_limitations(
            tuple(
                item.limitation
                for item in calculated
                if item.limitation is not None
            )
        )
        return FundamentalEvidenceFamilyScoreResult(
            result_id=(
                f"fundamental.family_score:{framework.value}:"
                f"{policy.family.value}"
            ),
            framework=framework,
            family=policy.family,
            policy_fingerprint=policy.policy_fingerprint,
            status=FundamentalScoreResultStatus.CALCULATED,
            framework_weight_bps=policy.framework_weight_bps,
            minimum_rule_coverage_bps=policy.minimum_rule_coverage_bps,
            maximum_family_score=policy.maximum_family_score,
            metric_results=metric_results,
            score=score,
            weighted_score=weighted_score,
            limitations=limitations,
        )

    reason = _metric_results_withholding_reason(metric_results)
    limitations = _canonical_limitations(
        tuple(
            item.limitation
            for item in metric_results
            if item.limitation is not None
        )
        or ("Family metric coverage is below policy minimum.",)
    )
    return FundamentalEvidenceFamilyScoreResult(
        result_id=(
            f"fundamental.family_score:{framework.value}:{policy.family.value}"
        ),
        framework=framework,
        family=policy.family,
        policy_fingerprint=policy.policy_fingerprint,
        status=FundamentalScoreResultStatus.WITHHELD,
        withholding_reason=reason,
        framework_weight_bps=policy.framework_weight_bps,
        minimum_rule_coverage_bps=policy.minimum_rule_coverage_bps,
        maximum_family_score=policy.maximum_family_score,
        metric_results=metric_results,
        limitations=limitations,
    )


def _score_metric(
    evidence: NormalizedFinancialEvidence,
    framework: FundamentalFramework,
    family: FundamentalFrameworkEvidenceFamily,
    rule: FundamentalMetricScoringRule,
) -> FundamentalMetricScoreResult:
    selection = _select_metric_series(evidence, rule)
    if selection.status is not FundamentalScoreResultStatus.CALCULATED:
        assert selection.withholding_reason is not None
        return _unreleased_metric(
            framework,
            family,
            rule,
            status=selection.status,
            reason=selection.withholding_reason,
            limitation=selection.limitation or "Metric score was withheld.",
            metric_keys=_metric_keys(selection.metrics),
            citations=_citations(selection.metrics),
        )

    assert selection.observed_value is not None
    matching_bands = tuple(
        band
        for band in rule.bands
        if (
            band.minimum_inclusive is None
            or selection.observed_value >= band.minimum_inclusive
        )
        and (
            band.maximum_exclusive is None
            or selection.observed_value < band.maximum_exclusive
        )
    )
    if len(matching_bands) != 1:
        raise ValueError("metric score bands did not resolve exactly once")
    band = matching_bands[0]
    weighted_score = (
        band.score * Decimal(rule.component_weight_bps) / _TOTAL_WEIGHT_BPS
    ).quantize(_CONTRIBUTION_QUANTUM, rounding=ROUND_HALF_UP)
    return FundamentalMetricScoreResult(
        result_id=(
            f"fundamental.metric_score:{framework.value}:{family.value}:"
            f"{rule.rule_id}"
        ),
        framework=framework,
        family=family,
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        rule_policy_fingerprint=rule.policy_fingerprint,
        status=FundamentalScoreResultStatus.CALCULATED,
        metric_keys=_metric_keys(selection.metrics),
        citations=_citations(selection.metrics),
        observed_value=selection.observed_value,
        band_id=band.band_id,
        grade=band.grade,
        score=band.score,
        component_weight_bps=rule.component_weight_bps,
        weighted_score=weighted_score,
        limitation=selection.limitation,
    )


def _select_metric_series(
    evidence: NormalizedFinancialEvidence,
    rule: FundamentalMetricScoringRule,
) -> _SeriesSelection:
    for basis in rule.allowed_reporting_bases:
        basis_candidates = tuple(
            metric
            for metric in evidence.metrics
            if metric.key.reporting_basis is basis
            and metric.period.period_kind is rule.period_kind
            and not metric.period.is_forecast
            and metric.key.metric_id in rule.metric_ids
        )
        if not basis_candidates:
            continue

        conflicts: list[NormalizedFundamentalMetric] = []
        unsupported = False
        missing_limitations: list[str] = []
        for metric_id in rule.metric_ids:
            candidates = tuple(
                metric
                for metric in basis_candidates
                if metric.key.metric_id == metric_id
            )
            if not candidates:
                continue
            selection = _validate_metric_candidates(
                candidates,
                rule,
                evidence.normalized_at,
            )
            if selection.status is FundamentalScoreResultStatus.CALCULATED:
                limitation = _series_limitation(selection.metrics, basis)
                return _SeriesSelection(
                    status=selection.status,
                    withholding_reason=None,
                    metrics=selection.metrics,
                    observed_value=selection.observed_value,
                    limitation=limitation,
                )
            if (
                selection.withholding_reason
                is FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE
            ):
                conflicts.extend(selection.metrics)
            elif (
                selection.withholding_reason
                is FundamentalScoreWithholdingReason.UNSUPPORTED_POLICY
            ):
                unsupported = True
            elif selection.limitation is not None:
                missing_limitations.append(selection.limitation)
        if conflicts:
            return _SeriesSelection(
                status=FundamentalScoreResultStatus.WITHHELD,
                withholding_reason=(
                    FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE
                ),
                metrics=_canonical_metrics(tuple(conflicts)),
                observed_value=None,
                limitation="Required normalized observations conflict.",
            )
        if unsupported:
            return _SeriesSelection(
                status=FundamentalScoreResultStatus.WITHHELD,
                withholding_reason=(
                    FundamentalScoreWithholdingReason.UNSUPPORTED_POLICY
                ),
                metrics=(),
                observed_value=None,
                limitation="Metric unit does not match the score policy.",
            )
        return _SeriesSelection(
            status=FundamentalScoreResultStatus.WITHHELD,
            withholding_reason=(
                FundamentalScoreWithholdingReason.MISSING_EVIDENCE
            ),
            metrics=(),
            observed_value=None,
            limitation=(
                " ".join(sorted(set(missing_limitations)))
                or "Required metric observations are unavailable."
            ),
        )
    return _SeriesSelection(
        status=FundamentalScoreResultStatus.WITHHELD,
        withholding_reason=FundamentalScoreWithholdingReason.MISSING_EVIDENCE,
        metrics=(),
        observed_value=None,
        limitation="Required metric and reporting basis are unavailable.",
    )


def _validate_metric_candidates(
    candidates: tuple[NormalizedFundamentalMetric, ...],
    rule: FundamentalMetricScoringRule,
    normalized_at: datetime,
) -> _SeriesSelection:
    if any(metric.unit != rule.expected_unit for metric in candidates):
        return _SeriesSelection(
            status=FundamentalScoreResultStatus.WITHHELD,
            withholding_reason=(
                FundamentalScoreWithholdingReason.UNSUPPORTED_POLICY
            ),
            metrics=(),
            observed_value=None,
            limitation="Metric unit does not match the score policy.",
        )
    if any(metric.period.end_date is None for metric in candidates):
        return _missing_selection("Metric observations require explicit end dates.")
    ordered = tuple(
        sorted(
            candidates,
            key=lambda metric: (
                metric.period.end_date or date.min,
                metric.key.canonical_key,
            ),
        )
    )
    dates = tuple(metric.period.end_date for metric in ordered)
    if len(dates) != len(set(dates)):
        return _SeriesSelection(
            status=FundamentalScoreResultStatus.WITHHELD,
            withholding_reason=(
                FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE
            ),
            metrics=ordered,
            observed_value=None,
            limitation="Multiple observations share one period end date.",
        )
    selected = ordered[-rule.minimum_observations :]
    if len(selected) < rule.minimum_observations:
        return _missing_selection(
            f"Only {len(selected)} of {rule.minimum_observations} required "
            "observations are available."
        )
    for metric in selected:
        assert metric.period.end_date is not None
        if metric.period.end_date > normalized_at.date():
            raise ValueError("scoring cannot use a future non-forecast period")
        if (
            metric.period.availability is FundamentalMetricAvailability.KNOWN
            and metric.period.available_at is not None
            and metric.period.available_at > normalized_at
        ):
            raise ValueError("scoring cannot use evidence before publication")
    conflicts = tuple(
        metric
        for metric in selected
        if metric.value_status is FundamentalMetricValueStatus.CONFLICT
    )
    if conflicts:
        return _SeriesSelection(
            status=FundamentalScoreResultStatus.WITHHELD,
            withholding_reason=(
                FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE
            ),
            metrics=conflicts,
            observed_value=None,
            limitation="Selected observations contain normalized conflicts.",
        )
    if any(metric.value_status not in _AVAILABLE_VALUES for metric in selected):
        return _missing_selection(
            "Selected observations contain unavailable values."
        )
    if rule.require_consecutive_periods and any(
        not _periods_are_consecutive(prior, current, rule.period_kind)
        for prior, current in zip(selected[:-1], selected[1:], strict=True)
    ):
        return _missing_selection("Required observations are not consecutive.")
    values = tuple(
        metric.value for metric in selected if metric.value is not None
    )
    if len(values) != len(selected):
        return _missing_selection("Selected observations have no numeric value.")
    return _SeriesSelection(
        status=FundamentalScoreResultStatus.CALCULATED,
        withholding_reason=None,
        metrics=selected,
        observed_value=_aggregate(values, rule.aggregation),
        limitation=None,
    )


def _aggregate(
    values: tuple[Decimal, ...],
    aggregation: FundamentalMetricAggregation,
) -> Decimal:
    if aggregation is FundamentalMetricAggregation.LATEST:
        return values[-1]
    if aggregation is FundamentalMetricAggregation.MEAN:
        return sum(values) / Decimal(len(values))
    if aggregation is FundamentalMetricAggregation.MEDIAN:
        return median(values)
    if aggregation is FundamentalMetricAggregation.MINIMUM:
        return min(values)
    return max(values)


def _periods_are_consecutive(
    prior: NormalizedFundamentalMetric,
    current: NormalizedFundamentalMetric,
    period_kind: FundamentalMetricPeriodKind,
) -> bool:
    prior_date = prior.period.end_date
    current_date = current.period.end_date
    if prior_date is None or current_date is None:
        return False
    if period_kind in {
        FundamentalMetricPeriodKind.ANNUAL,
        FundamentalMetricPeriodKind.YTD,
    }:
        return (
            current_date.year - prior_date.year == 1
            and current_date.month == prior_date.month
            and current_date.day == prior_date.day
        )
    if period_kind in {
        FundamentalMetricPeriodKind.QUARTERLY,
        FundamentalMetricPeriodKind.LTM,
    }:
        month_gap = (
            (current_date.year - prior_date.year) * 12
            + current_date.month
            - prior_date.month
        )
        return month_gap == 3
    return False


def _not_applicable_family(
    framework: FundamentalFramework,
    policy: FundamentalEvidenceFamilyScoringPolicy,
) -> FundamentalEvidenceFamilyScoreResult:
    metric_results = tuple(
        _unreleased_metric(
            framework,
            policy.family,
            rule,
            status=FundamentalScoreResultStatus.NOT_APPLICABLE,
            reason=FundamentalScoreWithholdingReason.INELIGIBLE_FRAMEWORK,
            limitation="Framework is ineligible under applicability policy.",
        )
        for rule in policy.metric_rules
    )
    return FundamentalEvidenceFamilyScoreResult(
        result_id=(
            f"fundamental.family_score:{framework.value}:{policy.family.value}"
        ),
        framework=framework,
        family=policy.family,
        policy_fingerprint=policy.policy_fingerprint,
        status=FundamentalScoreResultStatus.NOT_APPLICABLE,
        withholding_reason=(
            FundamentalScoreWithholdingReason.INELIGIBLE_FRAMEWORK
        ),
        framework_weight_bps=policy.framework_weight_bps,
        minimum_rule_coverage_bps=policy.minimum_rule_coverage_bps,
        maximum_family_score=policy.maximum_family_score,
        metric_results=metric_results,
        limitations=("Framework is ineligible under applicability policy.",),
    )


def _unreleased_metric(
    framework: FundamentalFramework,
    family: FundamentalFrameworkEvidenceFamily,
    rule: FundamentalMetricScoringRule,
    *,
    status: FundamentalScoreResultStatus,
    reason: FundamentalScoreWithholdingReason,
    limitation: str,
    metric_keys: tuple[str, ...] = (),
    citations: tuple[FundamentalMetricCitation, ...] = (),
) -> FundamentalMetricScoreResult:
    return FundamentalMetricScoreResult(
        result_id=(
            f"fundamental.metric_score:{framework.value}:{family.value}:"
            f"{rule.rule_id}"
        ),
        framework=framework,
        family=family,
        rule_id=rule.rule_id,
        rule_version=rule.rule_version,
        rule_policy_fingerprint=rule.policy_fingerprint,
        status=status,
        withholding_reason=reason,
        metric_keys=metric_keys,
        citations=citations,
        component_weight_bps=rule.component_weight_bps,
        limitation=limitation,
    )


def _unreleased_framework(
    applicability: FundamentalFrameworkApplicability,
    policy: FundamentalFrameworkScoringPolicy,
    family_results: tuple[FundamentalEvidenceFamilyScoreResult, ...],
    *,
    selected_primary: bool,
    status: FundamentalScoreResultStatus,
    reason: FundamentalScoreWithholdingReason,
    limitation: str,
    additional_limitations: tuple[str, ...] = (),
) -> FundamentalFrameworkScoreResult:
    return FundamentalFrameworkScoreResult(
        result_id=f"fundamental.framework_score:{policy.framework.value}",
        framework=policy.framework,
        status=status,
        withholding_reason=reason,
        applicability_status=applicability.status,
        applicability_fingerprint=applicability.applicability_fingerprint,
        scoring_policy_id=policy.policy_id,
        scoring_policy_version=policy.policy_version,
        scoring_policy_fingerprint=policy.policy_fingerprint,
        minimum_family_coverage_bps=policy.minimum_family_coverage_bps,
        selected_primary=selected_primary,
        family_results=family_results,
        limitations=_canonical_limitations(
            (
                limitation,
                *(item.summary for item in applicability.limitations),
                *policy.limitations,
                *additional_limitations,
            )
        ),
    )


def _missing_selection(limitation: str) -> _SeriesSelection:
    return _SeriesSelection(
        status=FundamentalScoreResultStatus.WITHHELD,
        withholding_reason=FundamentalScoreWithholdingReason.MISSING_EVIDENCE,
        metrics=(),
        observed_value=None,
        limitation=limitation,
    )


def _series_limitation(
    metrics: tuple[NormalizedFundamentalMetric, ...],
    basis: FinancialReportingBasis,
) -> str | None:
    limitations: list[str] = []
    if basis is FinancialReportingBasis.STANDALONE:
        limitations.append(
            "Consolidated evidence was absent; standalone evidence was used."
        )
    if any(
        metric.period.availability is FundamentalMetricAvailability.UNKNOWN
        for metric in metrics
    ):
        limitations.append("One or more publication timestamps are unknown.")
    if any(metric.limitations for metric in metrics):
        limitations.append(
            "Selected normalized metrics carry source limitations; inspect "
            "their citations."
        )
    return " ".join(limitations) or None


def _metric_keys(
    metrics: tuple[NormalizedFundamentalMetric, ...],
) -> tuple[str, ...]:
    return tuple(sorted(metric.key.canonical_key for metric in metrics))


def _citations(
    metrics: tuple[NormalizedFundamentalMetric, ...],
) -> tuple[FundamentalMetricCitation, ...]:
    by_key = {
        citation.citation_key: citation
        for metric in metrics
        for citation in metric.citations
    }
    return tuple(by_key[key] for key in sorted(by_key))


def _canonical_metrics(
    metrics: tuple[NormalizedFundamentalMetric, ...],
) -> tuple[NormalizedFundamentalMetric, ...]:
    by_key = {metric.key.canonical_key: metric for metric in metrics}
    return tuple(by_key[key] for key in sorted(by_key))


def _canonical_limitations(
    values: Iterable[str | None],
) -> tuple[str, ...]:
    return tuple(sorted({value for value in values if value}))


def _coverage_withholding_reason(
    status: FundamentalFrameworkEvidenceStatus,
) -> FundamentalScoreWithholdingReason:
    return {
        FundamentalFrameworkEvidenceStatus.MISSING: (
            FundamentalScoreWithholdingReason.MISSING_EVIDENCE
        ),
        FundamentalFrameworkEvidenceStatus.CONFLICTING: (
            FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE
        ),
        FundamentalFrameworkEvidenceStatus.UNSUPPORTED: (
            FundamentalScoreWithholdingReason.UNSUPPORTED_POLICY
        ),
    }[status]


def _metric_results_withholding_reason(
    results: tuple[FundamentalMetricScoreResult, ...],
) -> FundamentalScoreWithholdingReason:
    reasons = {result.withholding_reason for result in results}
    for reason in (
        FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE,
        FundamentalScoreWithholdingReason.UNSUPPORTED_POLICY,
        FundamentalScoreWithholdingReason.MISSING_EVIDENCE,
    ):
        if reason in reasons:
            return reason
    return FundamentalScoreWithholdingReason.INSUFFICIENT_COVERAGE


def _framework_withholding_reason(
    applicability: FundamentalFrameworkApplicability,
    family_results: tuple[FundamentalEvidenceFamilyScoreResult, ...],
) -> FundamentalScoreWithholdingReason:
    coverage_statuses = {
        item.status for item in applicability.evidence_coverage
    }
    if FundamentalFrameworkEvidenceStatus.CONFLICTING in coverage_statuses:
        return FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE
    if FundamentalFrameworkEvidenceStatus.UNSUPPORTED in coverage_statuses:
        return FundamentalScoreWithholdingReason.UNSUPPORTED_POLICY
    family_reasons = {item.withholding_reason for item in family_results}
    if FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE in family_reasons:
        return FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE
    if FundamentalScoreWithholdingReason.UNSUPPORTED_POLICY in family_reasons:
        return FundamentalScoreWithholdingReason.UNSUPPORTED_POLICY
    if applicability.status in {
        FundamentalFrameworkApplicabilityStatus.PARTIAL,
        FundamentalFrameworkApplicabilityStatus.UNAVAILABLE,
    }:
        return FundamentalScoreWithholdingReason.MISSING_EVIDENCE
    return FundamentalScoreWithholdingReason.INSUFFICIENT_COVERAGE


def _grade_score(
    score: Decimal,
    thresholds: FundamentalScoreGradeThresholds,
) -> FundamentalScoreGrade:
    if score >= thresholds.strong_minimum:
        return FundamentalScoreGrade.STRONG
    if score >= thresholds.favourable_minimum:
        return FundamentalScoreGrade.FAVOURABLE
    if score >= thresholds.mixed_minimum:
        return FundamentalScoreGrade.MIXED
    if score >= thresholds.weak_minimum:
        return FundamentalScoreGrade.WEAK
    return FundamentalScoreGrade.ADVERSE


def _determine_stance(
    mandatory_assessment: FundamentalMandatoryAssessment,
    framework_results: tuple[FundamentalFrameworkScoreResult, ...],
    policy_bundle: FundamentalScorePolicyBundle,
    *,
    review_required: bool,
) -> FundamentalScorecardStance:
    stance_policy = policy_bundle.stance_policy
    if (
        stance_policy.incomplete_mandatory_is_insufficient
        and mandatory_assessment.coverage_status
        is not FundamentalAssessmentCoverageStatus.COMPLETE
    ):
        return FundamentalScorecardStance.INSUFFICIENT_EVIDENCE
    selected = tuple(
        result
        for result in framework_results
        if result.selected_primary
        and result.status is FundamentalScoreResultStatus.CALCULATED
    )
    if len(selected) < stance_policy.minimum_scored_frameworks:
        return FundamentalScorecardStance.INSUFFICIENT_EVIDENCE
    healthy_count = sum(
        (result.score or Decimal("0"))
        >= stance_policy.healthy_minimum_score
        for result in selected
    )
    if healthy_count >= stance_policy.healthy_minimum_frameworks:
        stance = FundamentalScorecardStance.HEALTHY
    elif any(
        (result.score or Decimal("0"))
        >= stance_policy.caution_minimum_score
        for result in selected
    ):
        stance = FundamentalScorecardStance.CAUTION
    else:
        stance = FundamentalScorecardStance.WEAK

    stance = _apply_mandatory_constraints(
        mandatory_assessment,
        policy_bundle,
        stance,
    )
    if review_required and stance is FundamentalScorecardStance.HEALTHY:
        return FundamentalScorecardStance.CAUTION
    return stance


def _apply_mandatory_constraints(
    mandatory_assessment: FundamentalMandatoryAssessment,
    policy_bundle: FundamentalScorePolicyBundle,
    stance: FundamentalScorecardStance,
) -> FundamentalScorecardStance:
    if stance is FundamentalScorecardStance.INSUFFICIENT_EVIDENCE:
        return stance
    findings = tuple(
        finding
        for domain in mandatory_assessment.domains
        for finding in domain.findings
    )
    ceilings = tuple(
        constraint.stance_ceiling
        for constraint in policy_bundle.mandatory_constraints
        if any(
            finding.domain is constraint.domain
            and finding.status is constraint.trigger_status
            and _SEVERITY_RANK[finding.severity]
            >= _SEVERITY_RANK[constraint.minimum_severity]
            for finding in findings
        )
    )
    if FundamentalScorecardStance.WEAK in ceilings:
        return FundamentalScorecardStance.WEAK
    if (
        FundamentalScorecardStance.CAUTION in ceilings
        and stance is FundamentalScorecardStance.HEALTHY
    ):
        return FundamentalScorecardStance.CAUTION
    return stance


def _contains_conflicting_score_evidence(
    framework_results: tuple[FundamentalFrameworkScoreResult, ...],
) -> bool:
    return any(
        metric.withholding_reason
        is FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE
        for framework in framework_results
        for family in framework.family_results
        for metric in family.metric_results
    )
