"""Deterministic applicability and selection of fundamental frameworks."""

from dataclasses import dataclass
from datetime import date, datetime
from fractions import Fraction

from app.analytics.fundamental_checks import FinancialHealthSectorProfile
from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_assessment import FundamentalMandatoryAssessment
from app.models.fundamental_frameworks import (
    FundamentalFramework,
    FundamentalFrameworkApplicability,
    FundamentalFrameworkApplicabilityAssessment,
    FundamentalFrameworkApplicabilityLimitation,
    FundamentalFrameworkApplicabilityReason,
    FundamentalFrameworkApplicabilityStatus,
    FundamentalFrameworkEvidenceCoverage,
    FundamentalFrameworkEvidenceFamily,
    FundamentalFrameworkEvidenceStatus,
    FundamentalFrameworkLimitationKind,
    FundamentalFrameworkReasonKind,
)
from app.models.fundamental_metrics import (
    FundamentalMetricAvailability,
    FundamentalMetricPeriodKind,
    FundamentalMetricValueStatus,
    NormalizedFinancialEvidence,
    NormalizedFundamentalMetric,
)


FRAMEWORK_APPLICABILITY_POLICY_ID = "jarvis.framework_applicability"
FRAMEWORK_APPLICABILITY_POLICY_VERSION = "1.0.0"
MAX_PRIMARY_FRAMEWORKS = 3

_AVAILABLE_STATES = frozenset(
    {
        FundamentalMetricValueStatus.PRESENT,
        FundamentalMetricValueStatus.ZERO,
    }
)
_BASIS_PREFERENCE = (
    FinancialReportingBasis.CONSOLIDATED,
    FinancialReportingBasis.STANDALONE,
    FinancialReportingBasis.NOT_APPLICABLE,
)


@dataclass(frozen=True, slots=True)
class _SeriesRequirement:
    metric_ids: tuple[str, ...]
    period_kind: FundamentalMetricPeriodKind
    minimum_periods: int


@dataclass(frozen=True, slots=True)
class _FamilyRequirement:
    family: FundamentalFrameworkEvidenceFamily
    series: tuple[_SeriesRequirement, ...] = ()
    unsupported_reason: str | None = None


@dataclass(frozen=True, slots=True)
class _FrameworkPolicy:
    framework: FundamentalFramework
    base_priority: int
    families: tuple[_FamilyRequirement, ...]
    limitation_id: str
    limitation: str


@dataclass(frozen=True, slots=True)
class _SeriesOutcome:
    status: FundamentalFrameworkEvidenceStatus
    metrics: tuple[NormalizedFundamentalMetric, ...] = ()
    limitation: str | None = None


@dataclass(frozen=True, slots=True)
class _FrameworkEvaluation:
    policy: _FrameworkPolicy
    status: FundamentalFrameworkApplicabilityStatus
    evidence_coverage: tuple[FundamentalFrameworkEvidenceCoverage, ...]
    reasons: tuple[FundamentalFrameworkApplicabilityReason, ...]
    limitations: tuple[FundamentalFrameworkApplicabilityLimitation, ...]

    @property
    def coverage_count(self) -> int:
        return sum(
            item.status is FundamentalFrameworkEvidenceStatus.AVAILABLE
            for item in self.evidence_coverage
        )

    @property
    def coverage_ratio(self) -> Fraction:
        return Fraction(self.coverage_count, len(self.evidence_coverage))


def _series(
    metric_ids: tuple[str, ...],
    *,
    periods: int,
    kind: FundamentalMetricPeriodKind = FundamentalMetricPeriodKind.ANNUAL,
) -> _SeriesRequirement:
    return _SeriesRequirement(
        metric_ids=metric_ids,
        period_kind=kind,
        minimum_periods=periods,
    )


def _family(
    family: FundamentalFrameworkEvidenceFamily,
    *series: _SeriesRequirement,
    unsupported_reason: str | None = None,
) -> _FamilyRequirement:
    return _FamilyRequirement(
        family=family,
        series=tuple(series),
        unsupported_reason=unsupported_reason,
    )


_LEVERAGE_SERIES = (
    _series(
        ("derived.leverage.debt_to_equity", "ratio.debt_to_equity"),
        periods=1,
    ),
    _series(("ratio.interest_coverage",), periods=1),
)
_VALUATION_SERIES = _series(
    (
        "valuation.pe",
        "valuation.pb",
        "valuation.ev_to_ebitda",
    ),
    periods=1,
)
_CAPITAL_EFFICIENCY_3Y = _series(("ratio.roce", "ratio.roe"), periods=3)
_CAPITAL_EFFICIENCY_5Y = _series(("ratio.roce", "ratio.roe"), periods=5)

_FRAMEWORK_POLICIES = (
    _FrameworkPolicy(
        framework=FundamentalFramework.BUFFETT,
        base_priority=1,
        families=(
            _family(
                FundamentalFrameworkEvidenceFamily.PROFITABILITY,
                _series(("income.pat",), periods=5),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.CAPITAL_EFFICIENCY,
                _CAPITAL_EFFICIENCY_5Y,
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.BALANCE_SHEET,
                *_LEVERAGE_SERIES,
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.CASH_CONVERSION,
                _series(("cash_flow.operating",), periods=5),
                _series(("income.pat",), periods=5),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.REINVESTMENT,
                _series(("cash_flow.capex",), periods=5),
                _series(("income.pat",), periods=5),
            ),
        ),
        limitation_id="buffett.qualitative_boundary",
        limitation=(
            "Durable moat, pricing power, and management integrity remain "
            "unavailable without approved primary qualitative evidence."
        ),
    ),
    _FrameworkPolicy(
        framework=FundamentalFramework.LYNCH,
        base_priority=2,
        families=(
            _family(
                FundamentalFrameworkEvidenceFamily.PROFITABILITY,
                _series(("income.pat",), periods=3),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.GROWTH,
                _series(("income.revenue",), periods=3),
                _series(("income.eps", "income.adjusted_eps"), periods=3),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.BALANCE_SHEET,
                *_LEVERAGE_SERIES,
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.VALUATION,
                _VALUATION_SERIES,
            ),
        ),
        limitation_id="lynch.classification_boundary",
        limitation=(
            "Narrative growth classification is withheld until a separately "
            "approved deterministic company-lifecycle policy exists."
        ),
    ),
    _FrameworkPolicy(
        framework=FundamentalFramework.JHUNJHUNWALA_STYLE,
        base_priority=3,
        families=(
            _family(
                FundamentalFrameworkEvidenceFamily.PROFITABILITY,
                _series(("income.ebitda",), periods=3),
                _series(("income.pat",), periods=3),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.GROWTH,
                _series(("income.revenue",), periods=3),
                _series(("income.pat",), periods=3),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.CAPITAL_EFFICIENCY,
                _CAPITAL_EFFICIENCY_3Y,
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.BALANCE_SHEET,
                *_LEVERAGE_SERIES,
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.VALUATION,
                _VALUATION_SERIES,
            ),
        ),
        limitation_id="jhunjhunwala_style.public_principles_boundary",
        limitation=(
            "This is a documented public-principles lens, not impersonation, "
            "endorsement, or a claim about private investment judgment."
        ),
    ),
    _FrameworkPolicy(
        framework=FundamentalFramework.ONE_HUNDRED_BAGGER,
        base_priority=4,
        families=(
            _family(
                FundamentalFrameworkEvidenceFamily.GROWTH,
                _series(("income.revenue",), periods=5),
                _series(("income.pat",), periods=5),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.CAPITAL_EFFICIENCY,
                _CAPITAL_EFFICIENCY_5Y,
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.BALANCE_SHEET,
                *_LEVERAGE_SERIES,
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.CASH_CONVERSION,
                _series(("cash_flow.operating",), periods=5),
                _series(("income.pat",), periods=5),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.REINVESTMENT,
                _series(("cash_flow.capex",), periods=5),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.DILUTION,
                _series(("income.share_count",), periods=3),
            ),
        ),
        limitation_id="one_hundred_bagger.qualitative_boundary",
        limitation=(
            "Addressable runway and durable moat remain preliminary without "
            "approved primary qualitative evidence."
        ),
    ),
    _FrameworkPolicy(
        framework=FundamentalFramework.CAN_SLIM,
        base_priority=5,
        families=(
            _family(
                FundamentalFrameworkEvidenceFamily.GROWTH,
                _series(("income.revenue",), periods=3),
                _series(("income.eps", "income.adjusted_eps"), periods=3),
                _series(
                    ("income.revenue",),
                    periods=2,
                    kind=FundamentalMetricPeriodKind.QUARTERLY,
                ),
                _series(
                    ("income.eps", "income.adjusted_eps"),
                    periods=2,
                    kind=FundamentalMetricPeriodKind.QUARTERLY,
                ),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.INSTITUTIONAL_SPONSORSHIP,
                unsupported_reason=(
                    "Shareholding sponsorship is cached but not yet normalized "
                    "under this evidence policy."
                ),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.RELATIVE_LEADERSHIP,
                unsupported_reason=(
                    "Peer-relative price leadership is not part of normalized "
                    "financial evidence."
                ),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.MARKET_REGIME,
                unsupported_reason=(
                    "Point-in-time market regime is not yet joined to the "
                    "fundamental applicability input."
                ),
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.CATALYST,
                unsupported_reason=(
                    "The CAN SLIM N catalyst requires approved news or filing "
                    "evidence, which is outside this milestone."
                ),
            ),
        ),
        limitation_id="can_slim.catalyst_boundary",
        limitation=(
            "The N catalyst remains unavailable without approved news or "
            "filing evidence."
        ),
    ),
    _FrameworkPolicy(
        framework=FundamentalFramework.VALUATION,
        base_priority=6,
        families=(
            _family(
                FundamentalFrameworkEvidenceFamily.VALUATION,
                _VALUATION_SERIES,
            ),
            _family(
                FundamentalFrameworkEvidenceFamily.RELATIVE_LEADERSHIP,
                unsupported_reason=(
                    "Peer distributions are cached but not yet normalized "
                    "under this evidence policy."
                ),
            ),
        ),
        limitation_id="valuation.intrinsic_boundary",
        limitation=(
            "No reverse DCF is used; intrinsic valuation remains unavailable "
            "without explicit approved assumptions and inputs."
        ),
    ),
)


def assess_fundamental_framework_applicability(
    evidence: NormalizedFinancialEvidence,
    mandatory_assessment: FundamentalMandatoryAssessment,
    *,
    sector_profile: FinancialHealthSectorProfile,
    assessed_at: datetime,
) -> FundamentalFrameworkApplicabilityAssessment:
    """Assess all frameworks and select up to three eligible primary lenses."""

    _validate_inputs(evidence, mandatory_assessment, assessed_at)
    evaluations = tuple(
        _evaluate_framework(evidence, policy, sector_profile)
        for policy in _FRAMEWORK_POLICIES
    )
    priority_order = tuple(
        sorted(evaluations, key=_priority_key)
    )
    priorities = {
        item.policy.framework: priority
        for priority, item in enumerate(priority_order, start=1)
    }
    results = tuple(
        _build_result(item, priorities[item.policy.framework])
        for item in evaluations
    )
    selected = tuple(
        item.framework
        for item in sorted(
            results,
            key=lambda result: result.deterministic_priority,
        )
        if item.status is FundamentalFrameworkApplicabilityStatus.ELIGIBLE
    )[:MAX_PRIMARY_FRAMEWORKS]
    return FundamentalFrameworkApplicabilityAssessment(
        package_id=evidence.package_id,
        manifest_fingerprint=evidence.manifest_fingerprint,
        normalized_evidence_fingerprint=evidence.evidence_fingerprint,
        mandatory_assessment_fingerprint=(
            mandatory_assessment.assessment_fingerprint
        ),
        issuer=evidence.issuer,
        evidence_normalized_at=evidence.normalized_at,
        mandatory_assessed_at=mandatory_assessment.assessed_at,
        assessed_at=assessed_at,
        package_expires_at=evidence.package_expires_at,
        applicability_policy_id=FRAMEWORK_APPLICABILITY_POLICY_ID,
        applicability_policy_version=FRAMEWORK_APPLICABILITY_POLICY_VERSION,
        results=results,
        selected_primary_frameworks=selected,
        limitations=(
            "Framework selection reflects quantitative evidence coverage, not "
            "a recommendation or an imitation of any investor.",
            "Provider-standardized fundamentals remain secondary research "
            "evidence under the current source policy.",
        ),
    )


def _validate_inputs(
    evidence: NormalizedFinancialEvidence,
    mandatory_assessment: FundamentalMandatoryAssessment,
    assessed_at: datetime,
) -> None:
    if assessed_at.tzinfo is None or assessed_at.utcoffset() is None:
        raise ValueError("framework applicability time requires timezone")
    if not (
        mandatory_assessment.assessed_at
        <= assessed_at
        < evidence.package_expires_at
    ):
        raise ValueError(
            "framework applicability requires a completed mandatory assessment "
            "from an active package"
        )
    if (
        mandatory_assessment.package_id != evidence.package_id
        or mandatory_assessment.manifest_fingerprint
        != evidence.manifest_fingerprint
        or mandatory_assessment.normalized_evidence_fingerprint
        != evidence.evidence_fingerprint
        or mandatory_assessment.issuer != evidence.issuer
        or mandatory_assessment.evidence_normalized_at != evidence.normalized_at
        or mandatory_assessment.package_expires_at != evidence.package_expires_at
    ):
        raise ValueError(
            "mandatory assessment and normalized evidence do not share one "
            "immutable package boundary"
        )
    for metric in evidence.metrics:
        if metric.period.is_forecast:
            continue
        if (
            metric.period.end_date is not None
            and metric.period.end_date > evidence.normalized_at.date()
        ):
            raise ValueError(
                "framework applicability cannot use a future non-forecast period"
            )
        if (
            metric.period.availability is FundamentalMetricAvailability.KNOWN
            and metric.period.available_at is not None
            and metric.period.available_at > evidence.normalized_at
        ):
            raise ValueError(
                "framework applicability cannot use evidence before publication"
            )


def _evaluate_framework(
    evidence: NormalizedFinancialEvidence,
    policy: _FrameworkPolicy,
    sector_profile: FinancialHealthSectorProfile,
) -> _FrameworkEvaluation:
    coverage = tuple(
        _evaluate_family(evidence, requirement)
        for requirement in policy.families
    )
    limitations = [
        FundamentalFrameworkApplicabilityLimitation(
            limitation_id=policy.limitation_id,
            kind=FundamentalFrameworkLimitationKind.EVIDENCE_BOUNDARY,
            summary=policy.limitation,
        )
    ]
    if sector_profile is not FinancialHealthSectorProfile.NON_FINANCIAL:
        sector_summary = (
            "BFSI requires separately approved framework requirements."
            if sector_profile is FinancialHealthSectorProfile.BFSI
            else "Issuer sector classification is unavailable."
        )
        limitations.append(
            FundamentalFrameworkApplicabilityLimitation(
                limitation_id=f"{policy.framework.value}.sector_policy",
                kind=FundamentalFrameworkLimitationKind.SECTOR,
                summary=sector_summary,
                excludes_framework=True,
            )
        )
        status = FundamentalFrameworkApplicabilityStatus.INELIGIBLE
    else:
        available_count = sum(
            item.status is FundamentalFrameworkEvidenceStatus.AVAILABLE
            for item in coverage
        )
        if available_count == len(coverage):
            status = FundamentalFrameworkApplicabilityStatus.ELIGIBLE
        elif available_count:
            status = FundamentalFrameworkApplicabilityStatus.PARTIAL
        else:
            status = FundamentalFrameworkApplicabilityStatus.UNAVAILABLE

    missing_families = tuple(
        item.family
        for item in coverage
        if item.status is not FundamentalFrameworkEvidenceStatus.AVAILABLE
    )
    reason_kind = {
        FundamentalFrameworkApplicabilityStatus.ELIGIBLE: (
            FundamentalFrameworkReasonKind.ELIGIBILITY
        ),
        FundamentalFrameworkApplicabilityStatus.PARTIAL: (
            FundamentalFrameworkReasonKind.EVIDENCE_GAP
        ),
        FundamentalFrameworkApplicabilityStatus.UNAVAILABLE: (
            FundamentalFrameworkReasonKind.EVIDENCE_GAP
        ),
        FundamentalFrameworkApplicabilityStatus.INELIGIBLE: (
            FundamentalFrameworkReasonKind.EXCLUSION
        ),
    }[status]
    reason_families = (
        ()
        if status is FundamentalFrameworkApplicabilityStatus.INELIGIBLE
        else tuple(item.family for item in coverage)
        if status is FundamentalFrameworkApplicabilityStatus.ELIGIBLE
        else missing_families
    )
    summary = _reason_summary(policy.framework, status, missing_families)
    return _FrameworkEvaluation(
        policy=policy,
        status=status,
        evidence_coverage=coverage,
        reasons=(
            FundamentalFrameworkApplicabilityReason(
                reason_id=f"{policy.framework.value}.{status.value}",
                kind=reason_kind,
                summary=summary,
                evidence_families=reason_families,
            ),
        ),
        limitations=tuple(
            sorted(limitations, key=lambda item: item.limitation_id)
        ),
    )


def _evaluate_family(
    evidence: NormalizedFinancialEvidence,
    requirement: _FamilyRequirement,
) -> FundamentalFrameworkEvidenceCoverage:
    if requirement.unsupported_reason is not None:
        return FundamentalFrameworkEvidenceCoverage(
            family=requirement.family,
            status=FundamentalFrameworkEvidenceStatus.UNSUPPORTED,
            limitation=requirement.unsupported_reason,
        )

    outcomes = tuple(
        _select_series(evidence, series_requirement)
        for series_requirement in requirement.series
    )
    conflicts = tuple(
        metric
        for outcome in outcomes
        if outcome.status is FundamentalFrameworkEvidenceStatus.CONFLICTING
        for metric in outcome.metrics
    )
    if conflicts:
        return _cited_family_coverage(
            requirement.family,
            FundamentalFrameworkEvidenceStatus.CONFLICTING,
            conflicts,
            "One or more required normalized series contain conflicting "
            "observations.",
        )
    unavailable = tuple(
        outcome
        for outcome in outcomes
        if outcome.status is not FundamentalFrameworkEvidenceStatus.AVAILABLE
    )
    if unavailable:
        limitations = sorted(
            {
                outcome.limitation
                for outcome in unavailable
                if outcome.limitation is not None
            }
        )
        return FundamentalFrameworkEvidenceCoverage(
            family=requirement.family,
            status=FundamentalFrameworkEvidenceStatus.MISSING,
            limitation=" ".join(limitations),
        )
    metrics = tuple(
        metric
        for outcome in outcomes
        for metric in outcome.metrics
    )
    return _cited_family_coverage(
        requirement.family,
        FundamentalFrameworkEvidenceStatus.AVAILABLE,
        metrics,
    )


def _select_series(
    evidence: NormalizedFinancialEvidence,
    requirement: _SeriesRequirement,
) -> _SeriesOutcome:
    for basis in _BASIS_PREFERENCE:
        basis_candidates = tuple(
            metric
            for metric in evidence.metrics
            if metric.key.reporting_basis is basis
            and metric.period.period_kind is requirement.period_kind
            and not metric.period.is_forecast
            and metric.key.metric_id in requirement.metric_ids
        )
        if not basis_candidates:
            continue

        conflicts: list[NormalizedFundamentalMetric] = []
        missing_limitations: list[str] = []
        for metric_id in requirement.metric_ids:
            candidates = tuple(
                metric
                for metric in basis_candidates
                if metric.key.metric_id == metric_id
            )
            if not candidates:
                continue
            outcome = _validate_series_candidates(candidates, requirement)
            if outcome.status is FundamentalFrameworkEvidenceStatus.AVAILABLE:
                limitation = (
                    "Consolidated evidence was absent; standalone evidence "
                    "was used explicitly."
                    if basis is FinancialReportingBasis.STANDALONE
                    else outcome.limitation
                )
                return _SeriesOutcome(
                    status=outcome.status,
                    metrics=outcome.metrics,
                    limitation=limitation,
                )
            if outcome.status is FundamentalFrameworkEvidenceStatus.CONFLICTING:
                conflicts.extend(outcome.metrics)
            elif outcome.limitation is not None:
                missing_limitations.append(outcome.limitation)
        if conflicts:
            return _SeriesOutcome(
                status=FundamentalFrameworkEvidenceStatus.CONFLICTING,
                metrics=_canonical_metrics(tuple(conflicts)),
                limitation="Required observations conflict.",
            )
        if missing_limitations:
            return _SeriesOutcome(
                status=FundamentalFrameworkEvidenceStatus.MISSING,
                limitation=" ".join(sorted(set(missing_limitations))),
            )
        return _SeriesOutcome(
            status=FundamentalFrameworkEvidenceStatus.MISSING,
            limitation=(
                f"Requires {requirement.minimum_periods} consecutive "
                f"{requirement.period_kind.value} observation(s) for one of: "
                f"{', '.join(requirement.metric_ids)}."
            ),
        )
    return _SeriesOutcome(
        status=FundamentalFrameworkEvidenceStatus.MISSING,
        limitation=(
            f"Required {requirement.period_kind.value} metric is missing: "
            f"{', '.join(requirement.metric_ids)}."
        ),
    )


def _validate_series_candidates(
    candidates: tuple[NormalizedFundamentalMetric, ...],
    requirement: _SeriesRequirement,
) -> _SeriesOutcome:
    if any(metric.period.end_date is None for metric in candidates):
        return _SeriesOutcome(
            status=FundamentalFrameworkEvidenceStatus.MISSING,
            limitation="Required observations do not have explicit end dates.",
        )
    ordered = tuple(
        sorted(
            candidates,
            key=lambda metric: (
                metric.period.end_date or date.min,
                metric.key.period_key,
            ),
        )
    )
    dates = tuple(metric.period.end_date for metric in ordered)
    if len(dates) != len(set(dates)):
        return _SeriesOutcome(
            status=FundamentalFrameworkEvidenceStatus.CONFLICTING,
            metrics=ordered,
            limitation="Multiple observations share one period end date.",
        )
    selected = ordered[-requirement.minimum_periods :]
    if len(selected) < requirement.minimum_periods:
        return _SeriesOutcome(
            status=FundamentalFrameworkEvidenceStatus.MISSING,
            limitation=(
                f"Only {len(selected)} of {requirement.minimum_periods} "
                "required observations are available."
            ),
        )
    unavailable = tuple(
        metric
        for metric in selected
        if metric.value_status not in _AVAILABLE_STATES
    )
    conflicts = tuple(
        metric
        for metric in unavailable
        if metric.value_status is FundamentalMetricValueStatus.CONFLICT
    )
    if conflicts:
        return _SeriesOutcome(
            status=FundamentalFrameworkEvidenceStatus.CONFLICTING,
            metrics=conflicts,
            limitation="Selected observations contain normalized conflicts.",
        )
    if unavailable:
        return _SeriesOutcome(
            status=FundamentalFrameworkEvidenceStatus.MISSING,
            limitation="Selected observations contain unavailable values.",
        )
    if requirement.minimum_periods > 1 and any(
        not _periods_are_consecutive(prior, current, requirement.period_kind)
        for prior, current in zip(selected[:-1], selected[1:], strict=True)
    ):
        return _SeriesOutcome(
            status=FundamentalFrameworkEvidenceStatus.MISSING,
            limitation="Required observations are not consecutive.",
        )
    return _SeriesOutcome(
        status=FundamentalFrameworkEvidenceStatus.AVAILABLE,
        metrics=selected,
    )


def _periods_are_consecutive(
    prior: NormalizedFundamentalMetric,
    current: NormalizedFundamentalMetric,
    period_kind: FundamentalMetricPeriodKind,
) -> bool:
    prior_date = prior.period.end_date
    current_date = current.period.end_date
    if prior_date is None or current_date is None:
        return False
    if period_kind is FundamentalMetricPeriodKind.ANNUAL:
        return current_date.year - prior_date.year == 1
    if period_kind is FundamentalMetricPeriodKind.QUARTERLY:
        month_gap = (
            (current_date.year - prior_date.year) * 12
            + current_date.month
            - prior_date.month
        )
        return month_gap == 3
    return False


def _cited_family_coverage(
    family: FundamentalFrameworkEvidenceFamily,
    status: FundamentalFrameworkEvidenceStatus,
    metrics: tuple[NormalizedFundamentalMetric, ...],
    limitation: str | None = None,
) -> FundamentalFrameworkEvidenceCoverage:
    canonical_metrics = _canonical_metrics(metrics)
    citations = {
        citation.citation_key: citation
        for metric in canonical_metrics
        for citation in metric.citations
    }
    return FundamentalFrameworkEvidenceCoverage(
        family=family,
        status=status,
        metric_keys=tuple(metric.key.canonical_key for metric in canonical_metrics),
        citations=tuple(citations[key] for key in sorted(citations)),
        limitation=limitation,
    )


def _canonical_metrics(
    metrics: tuple[NormalizedFundamentalMetric, ...],
) -> tuple[NormalizedFundamentalMetric, ...]:
    by_key = {metric.key.canonical_key: metric for metric in metrics}
    return tuple(by_key[key] for key in sorted(by_key))


def _priority_key(evaluation: _FrameworkEvaluation) -> tuple[int, Fraction, int]:
    status_priority = {
        FundamentalFrameworkApplicabilityStatus.ELIGIBLE: 0,
        FundamentalFrameworkApplicabilityStatus.PARTIAL: 1,
        FundamentalFrameworkApplicabilityStatus.UNAVAILABLE: 2,
        FundamentalFrameworkApplicabilityStatus.INELIGIBLE: 3,
    }[evaluation.status]
    return (
        status_priority,
        -evaluation.coverage_ratio,
        evaluation.policy.base_priority,
    )


def _build_result(
    evaluation: _FrameworkEvaluation,
    priority: int,
) -> FundamentalFrameworkApplicability:
    framework = evaluation.policy.framework
    return FundamentalFrameworkApplicability(
        result_id=f"fundamental.framework_applicability:{framework.value}",
        framework=framework,
        status=evaluation.status,
        applicability_policy_id=FRAMEWORK_APPLICABILITY_POLICY_ID,
        applicability_policy_version=FRAMEWORK_APPLICABILITY_POLICY_VERSION,
        deterministic_priority=priority,
        evidence_coverage=evaluation.evidence_coverage,
        reasons=evaluation.reasons,
        limitations=evaluation.limitations,
    )


def _reason_summary(
    framework: FundamentalFramework,
    status: FundamentalFrameworkApplicabilityStatus,
    missing_families: tuple[FundamentalFrameworkEvidenceFamily, ...],
) -> str:
    if status is FundamentalFrameworkApplicabilityStatus.ELIGIBLE:
        return (
            f"{framework.value} has complete required quantitative evidence "
            "under the current policy."
        )
    if status is FundamentalFrameworkApplicabilityStatus.INELIGIBLE:
        return (
            f"{framework.value} is excluded because no approved policy applies "
            "to the issuer's sector classification."
        )
    names = ", ".join(item.value for item in missing_families)
    return (
        f"{framework.value} is {status.value} because required evidence "
        f"families are unavailable: {names}."
    )
