"""Deterministic mandatory checks over normalized fundamental evidence."""

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, localcontext
from enum import StrEnum
from types import MappingProxyType
from typing import Callable

from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_assessment import (
    FundamentalAssessmentDomain,
    FundamentalAssessmentFinding,
    FundamentalAssessmentThreshold,
    FundamentalDomainAssessment,
    FundamentalFindingNAReason,
    FundamentalFindingObservation,
    FundamentalFindingSeverity,
    FundamentalFindingStatus,
    FundamentalReviewState,
    FundamentalThresholdOperator,
)
from app.models.fundamental_metrics import (
    FundamentalMetricAvailability,
    FundamentalMetricPeriodKind,
    FundamentalMetricQualityFlag,
    FundamentalMetricUnit,
    FundamentalMetricValueStatus,
    NormalizedFinancialEvidence,
    NormalizedFundamentalMetric,
)
from app.models.fundamentals import FundamentalValueKind


FINANCIAL_HEALTH_POLICY_ID = "jarvis.financial_health"
FINANCIAL_HEALTH_POLICY_VERSION = "1.0.0"
EARNINGS_QUALITY_POLICY_ID = "jarvis.earnings_quality"
EARNINGS_QUALITY_POLICY_VERSION = "1.0.0"
FORENSIC_RISK_POLICY_ID = "jarvis.forensic_risk"
FORENSIC_RISK_POLICY_VERSION = "1.0.0"


class FinancialHealthSectorProfile(StrEnum):
    """Approved threshold family for one issuer classification."""

    NON_FINANCIAL = "non_financial"
    BFSI = "bfsi"
    UNKNOWN = "unknown"


FINANCIAL_HEALTH_RULE_IDS = (
    "balance_sheet_expansion",
    "capital_efficiency_persistence",
    "debt_growth_coverage",
    "debt_to_equity",
    "interest_coverage",
    "liquidity",
    "net_debt_to_ebitda",
    "reporting_basis_conflict",
    "working_capital_position",
)

EARNINGS_QUALITY_RULE_IDS = (
    "annual_quarterly_consistency",
    "cash_conversion_cumulative",
    "dilution",
    "ebitda_pat_ocf_divergence",
    "exceptional_dependence",
    "fcf_consistency",
    "margin_cash_conversion",
    "pat_ocf_alignment",
    "working_capital_quality",
)

FORENSIC_RISK_RULE_IDS = (
    "accounting_conflicts",
    "basis_growth_divergence",
    "leverage_and_coverage",
    "persistent_profit_cash_mismatch",
    "promoter_holding_erosion",
    "promoter_pledge",
    "receivables_growth",
)

_RULE_TITLES = MappingProxyType(
    {
        "balance_sheet_expansion": "Balance-sheet expansion",
        "capital_efficiency_persistence": "Capital-efficiency persistence",
        "debt_growth_coverage": "Debt growth versus operating capacity",
        "debt_to_equity": "Debt-to-equity position",
        "interest_coverage": "Interest coverage",
        "liquidity": "Short-term liquidity",
        "net_debt_to_ebitda": "Net debt to EBITDA",
        "reporting_basis_conflict": "Reporting-basis directional conflict",
        "working_capital_position": "Working-capital position",
    }
)

_EARNINGS_RULE_TITLES = MappingProxyType(
    {
        "annual_quarterly_consistency": "Annual and quarterly consistency",
        "cash_conversion_cumulative": "Cumulative cash conversion",
        "dilution": "Share-count dilution",
        "ebitda_pat_ocf_divergence": "EBITDA, PAT, and OCF divergence",
        "exceptional_dependence": "Exceptional-item dependence",
        "fcf_consistency": "Free-cash-flow consistency",
        "margin_cash_conversion": "Margin and cash-conversion alignment",
        "pat_ocf_alignment": "PAT and operating-cash-flow alignment",
        "working_capital_quality": "Working-capital quality",
    }
)

_FORENSIC_RULE_TITLES = MappingProxyType(
    {
        "accounting_conflicts": "Conflicting or inconsistent accounting data",
        "basis_growth_divergence": "Reporting-basis growth divergence",
        "leverage_and_coverage": "Leverage growth and debt-service capacity",
        "persistent_profit_cash_mismatch": "Persistent profit and cash mismatch",
        "promoter_holding_erosion": "Promoter-holding erosion",
        "promoter_pledge": "Promoter pledge",
        "receivables_growth": "Receivables growth versus revenue",
    }
)

_AVAILABLE_STATES = frozenset(
    {
        FundamentalMetricValueStatus.PRESENT,
        FundamentalMetricValueStatus.ZERO,
    }
)


@dataclass(frozen=True, slots=True)
class _SeriesSelection:
    metrics: tuple[NormalizedFundamentalMetric, ...]
    had_candidates: bool
    na_reason: FundamentalFindingNAReason | None = None
    limitation: str | None = None
    basis_limitation: str | None = None


@dataclass(frozen=True, slots=True)
class _Classification:
    status: FundamentalFindingStatus
    severity: FundamentalFindingSeverity
    label: str


def assess_financial_health(
    evidence: NormalizedFinancialEvidence,
    *,
    sector_profile: FinancialHealthSectorProfile,
    assessed_at: datetime,
) -> FundamentalDomainAssessment:
    """Evaluate the versioned financial-health policy without inference.

    The caller must provide an approved sector profile. Industrial thresholds
    are never applied to BFSI or unclassified issuers. Consolidated evidence is
    preferred; standalone evidence is used only when consolidated observations
    do not exist, and that fallback is recorded in each affected finding.
    """

    _validate_assessment_time(evidence, assessed_at)
    _validate_point_in_time_metrics(evidence)

    if sector_profile is not FinancialHealthSectorProfile.NON_FINANCIAL:
        limitation = (
            "BFSI requires a separately approved financial-health policy."
            if sector_profile is FinancialHealthSectorProfile.BFSI
            else "Issuer sector classification is unavailable."
        )
        findings = tuple(
            _na_finding(
                rule_id,
                FundamentalFindingNAReason.UNSUPPORTED_POLICY,
                limitation,
            )
            for rule_id in FINANCIAL_HEALTH_RULE_IDS
        )
    else:
        findings = tuple(
            sorted(
                (
                    _assess_balance_sheet_expansion(evidence),
                    _assess_capital_efficiency(evidence),
                    _assess_debt_growth_coverage(evidence),
                    _assess_debt_to_equity(evidence),
                    _assess_interest_coverage(evidence),
                    _assess_liquidity(evidence),
                    _assess_net_debt_to_ebitda(evidence),
                    _assess_reporting_basis_conflict(evidence),
                    _assess_working_capital(evidence),
                ),
                key=lambda finding: finding.rule_id,
            )
        )

    return FundamentalDomainAssessment(
        domain=FundamentalAssessmentDomain.FINANCIAL_HEALTH,
        policy_id=FINANCIAL_HEALTH_POLICY_ID,
        policy_version=FINANCIAL_HEALTH_POLICY_VERSION,
        expected_rule_ids=FINANCIAL_HEALTH_RULE_IDS,
        findings=findings,
        limitations=(
            "Provider-standardized financial data supports screening, not a "
            "qualitative solvency or management conclusion.",
        ),
    )


def assess_earnings_quality(
    evidence: NormalizedFinancialEvidence,
    *,
    sector_profile: FinancialHealthSectorProfile,
    assessed_at: datetime,
) -> FundamentalDomainAssessment:
    """Evaluate multi-period earnings quality from normalized evidence.

    The initial policy is calibrated for non-financial companies. It screens
    reported profit against cash realization, working-capital absorption,
    dilution, and exceptional-item dependence. Findings identify review risks;
    they do not allege accounting misconduct.
    """

    _validate_assessment_time(evidence, assessed_at)
    _validate_point_in_time_metrics(evidence)

    if sector_profile is not FinancialHealthSectorProfile.NON_FINANCIAL:
        limitation = (
            "BFSI cash-flow and working-capital semantics require a separately "
            "approved earnings-quality policy."
            if sector_profile is FinancialHealthSectorProfile.BFSI
            else "Issuer sector classification is unavailable."
        )
        findings = tuple(
            _earnings_na_finding(
                rule_id,
                FundamentalFindingNAReason.UNSUPPORTED_POLICY,
                limitation,
            )
            for rule_id in EARNINGS_QUALITY_RULE_IDS
        )
    else:
        findings = tuple(
            sorted(
                (
                    _assess_annual_quarterly_consistency(evidence),
                    _assess_cumulative_cash_conversion(evidence),
                    _assess_dilution(evidence),
                    _assess_earnings_cash_divergence(evidence),
                    _assess_exceptional_dependence(evidence),
                    _assess_fcf_consistency(evidence),
                    _assess_margin_cash_conversion(evidence),
                    _assess_pat_ocf_alignment(evidence),
                    _assess_working_capital_quality(evidence),
                ),
                key=lambda finding: finding.rule_id,
            )
        )

    return FundamentalDomainAssessment(
        domain=FundamentalAssessmentDomain.EARNINGS_QUALITY,
        policy_id=EARNINGS_QUALITY_POLICY_ID,
        policy_version=EARNINGS_QUALITY_POLICY_VERSION,
        expected_rule_ids=EARNINGS_QUALITY_RULE_IDS,
        findings=findings,
        limitations=(
            "These deterministic screens assess reported earnings quality; "
            "they do not replace primary-document accounting review.",
        ),
    )


def assess_forensic_risk(
    evidence: NormalizedFinancialEvidence,
    *,
    sector_profile: FinancialHealthSectorProfile,
    assessed_at: datetime,
) -> FundamentalDomainAssessment:
    """Run deterministic forensic-risk screens without alleging misconduct.

    General data-integrity, ownership, and reporting-basis checks apply across
    sectors. Cash-flow, receivables, and industrial leverage screens are
    withheld for BFSI and unclassified issuers until an approved sector policy
    exists.
    """

    _validate_assessment_time(evidence, assessed_at)
    _validate_point_in_time_metrics(evidence)

    general_findings = (
        _assess_accounting_conflicts(evidence),
        _assess_basis_growth_divergence(evidence),
        _assess_promoter_holding_erosion(evidence),
        _assess_promoter_pledge(evidence),
    )
    if sector_profile is FinancialHealthSectorProfile.NON_FINANCIAL:
        sector_findings = (
            _assess_forensic_leverage_and_coverage(evidence),
            _assess_persistent_profit_cash_mismatch(evidence),
            _assess_receivables_growth(evidence),
        )
    else:
        limitation = (
            "BFSI cash-flow, receivables, and leverage semantics require a "
            "separately approved forensic-screening policy."
            if sector_profile is FinancialHealthSectorProfile.BFSI
            else "Issuer sector classification is unavailable."
        )
        sector_findings = tuple(
            _forensic_na_finding(
                rule_id,
                FundamentalFindingNAReason.UNSUPPORTED_POLICY,
                limitation,
            )
            for rule_id in (
                "leverage_and_coverage",
                "persistent_profit_cash_mismatch",
                "receivables_growth",
            )
        )

    return FundamentalDomainAssessment(
        domain=FundamentalAssessmentDomain.FORENSIC_RISK,
        policy_id=FORENSIC_RISK_POLICY_ID,
        policy_version=FORENSIC_RISK_POLICY_VERSION,
        expected_rule_ids=FORENSIC_RISK_RULE_IDS,
        findings=tuple(
            sorted((*general_findings, *sector_findings), key=lambda item: item.rule_id)
        ),
        limitations=(
            "These deterministic red flags require review; they are not "
            "findings of fraud, misconduct, or governance failure.",
            "Auditor qualifications, related-party transactions, contingent "
            "liabilities, governance conduct, and management representations "
            "remain unavailable without primary documents.",
        ),
    )


def _validate_assessment_time(
    evidence: NormalizedFinancialEvidence,
    assessed_at: datetime,
) -> None:
    if assessed_at.tzinfo is None or assessed_at.utcoffset() is None:
        raise ValueError("financial-health assessment time requires timezone")
    if not evidence.normalized_at <= assessed_at < evidence.package_expires_at:
        raise ValueError(
            "financial-health checks require an active normalized package"
        )


def _validate_point_in_time_metrics(evidence: NormalizedFinancialEvidence) -> None:
    for metric in evidence.metrics:
        if metric.period.is_forecast:
            continue
        if (
            metric.period.end_date is not None
            and metric.period.end_date > evidence.normalized_at.date()
        ):
            raise ValueError(
                "non-forecast metric period cannot end after normalization"
            )
        if (
            metric.period.availability is FundamentalMetricAvailability.KNOWN
            and metric.period.available_at is not None
            and metric.period.available_at > evidence.normalized_at
        ):
            raise ValueError(
                "metric was not available when normalized evidence was created"
            )


def _assess_liquidity(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    selection = _primary_series(
        evidence,
        ("derived.working_capital.current_ratio", "ratio.current"),
    )
    if not selection.metrics:
        return _selection_na("liquidity", selection)
    metric = selection.metrics[-1]
    value = _metric_value(metric)
    classification = _classify_liquidity(value)
    return _evaluated_finding(
        "liquidity",
        classification,
        _observation((metric,), value=value, unit=metric.unit),
        _numeric_threshold(
            "financial_health.liquidity.current_ratio",
            FundamentalThresholdOperator.GREATER_THAN_OR_EQUAL,
            Decimal("1.5"),
            metric.unit,
            "Pass at or above 1.5; warn from 1.0 to below 1.5; fail below 1.0.",
        ),
        f"Latest current ratio is {_format(value)}x ({classification.label}).",
        selection,
    )


def _assess_debt_to_equity(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    selection = _primary_series(
        evidence,
        ("derived.leverage.debt_to_equity", "ratio.debt_to_equity"),
    )
    if not selection.metrics:
        return _selection_na("debt_to_equity", selection)
    metric = selection.metrics[-1]
    value = _metric_value(metric)
    classification = _classify_debt_to_equity(value)
    return _evaluated_finding(
        "debt_to_equity",
        classification,
        _observation((metric,), value=value, unit=metric.unit),
        _composite_threshold(
            "financial_health.leverage.debt_to_equity",
            "Pass from 0x to 1x; warn above 1x through 2x; fail below 0x "
            "or above 2x.",
        ),
        f"Latest debt to equity is {_format(value)}x ({classification.label}).",
        selection,
    )


def _assess_net_debt_to_ebitda(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    ratio = _primary_series(
        evidence,
        ("derived.leverage.net_debt_to_ebitda",),
    )
    if not ratio.metrics:
        return _selection_na("net_debt_to_ebitda", ratio)
    basis = ratio.metrics[-1].key.reporting_basis
    net_debt = _series(
        evidence,
        ("derived.leverage.net_debt",),
        basis=basis,
    )
    ebitda = _series(evidence, ("income.ebitda",), basis=basis)
    if not net_debt.metrics or not ebitda.metrics:
        missing = net_debt if not net_debt.metrics else ebitda
        return _selection_na("net_debt_to_ebitda", missing)
    selected = (ratio.metrics[-1], net_debt.metrics[-1], ebitda.metrics[-1])
    if len({item.period.end_date for item in selected}) != 1:
        return _na_finding(
            "net_debt_to_ebitda",
            FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            "Net debt, EBITDA, and leverage ratio do not share one latest period.",
        )
    ratio_value = _metric_value(selected[0])
    net_debt_value = _base_value(selected[1])
    ebitda_value = _base_value(selected[2])
    classification = _classify_net_debt_to_ebitda(
        ratio_value,
        net_debt_value=net_debt_value,
        ebitda_value=ebitda_value,
    )
    return _evaluated_finding(
        "net_debt_to_ebitda",
        classification,
        _observation(selected, value=ratio_value, unit=selected[0].unit),
        _composite_threshold(
            "financial_health.leverage.net_debt_to_ebitda",
            "Pass at or below 2x with positive EBITDA, including genuine net "
            "cash; warn above 2x through 3x; fail above 3x or when a negative "
            "ratio is caused by non-positive EBITDA.",
        ),
        (
            f"Latest net debt/EBITDA is {_format(ratio_value)}x "
            f"({classification.label})."
        ),
        ratio,
    )


def _assess_interest_coverage(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    selection = _primary_series(evidence, ("ratio.interest_coverage",))
    if not selection.metrics:
        return _selection_na("interest_coverage", selection)
    metric = selection.metrics[-1]
    value = _metric_value(metric)
    classification = _classify_interest_coverage(value)
    return _evaluated_finding(
        "interest_coverage",
        classification,
        _observation((metric,), value=value, unit=metric.unit),
        _numeric_threshold(
            "financial_health.coverage.interest",
            FundamentalThresholdOperator.GREATER_THAN_OR_EQUAL,
            Decimal("3"),
            metric.unit,
            "Pass at or above 3x; warn from 1.5x to below 3x; fail below 1.5x.",
        ),
        (
            f"Latest interest coverage is {_format(value)}x "
            f"({classification.label})."
        ),
        selection,
    )


def _assess_capital_efficiency(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    selection = _primary_series(evidence, ("ratio.roce",), count=3)
    metric_name = "ROCE"
    if not selection.metrics and not selection.had_candidates:
        selection = _primary_series(evidence, ("ratio.roe",), count=3)
        metric_name = "ROE"
    if not selection.metrics:
        return _selection_na("capital_efficiency_persistence", selection)
    values = tuple(_metric_value(metric) for metric in selection.metrics)
    average = sum(values, Decimal("0")) / Decimal(len(values))
    minimum = min(values)
    if average >= Decimal("15") and minimum >= Decimal("10"):
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "persistent",
        )
    elif average >= Decimal("10") and minimum >= Decimal("5"):
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "moderate or uneven",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "weak",
        )
    return _evaluated_finding(
        "capital_efficiency_persistence",
        classification,
        _observation(
            selection.metrics,
            value=average,
            unit=selection.metrics[-1].unit,
        ),
        _composite_threshold(
            "financial_health.capital_efficiency.persistence",
            "Using three consecutive annual periods: pass when average is at "
            "least 15% and no period is below 10%; warn when average is at "
            "least 10% and no period is below 5%; otherwise fail.",
        ),
        (
            f"Three-period {metric_name} averages {_format(average)}% with a "
            f"minimum of {_format(minimum)}% ({classification.label})."
        ),
        selection,
    )


def _assess_working_capital(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    selection = _primary_series(
        evidence,
        ("derived.working_capital.net_working_capital",),
    )
    if not selection.metrics:
        return _selection_na("working_capital_position", selection)
    metric = selection.metrics[-1]
    value = _metric_value(metric)
    if value >= 0:
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "non-negative",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "negative and requires business-model review",
        )
    return _evaluated_finding(
        "working_capital_position",
        classification,
        _observation((metric,), value=value, unit=metric.unit),
        _numeric_threshold(
            "financial_health.working_capital.position",
            FundamentalThresholdOperator.GREATER_THAN_OR_EQUAL,
            Decimal("0"),
            metric.unit,
            "Non-negative net working capital passes; a negative value warns "
            "but does not fail because some business models operate structurally "
            "with negative working capital.",
        ),
        (
            f"Latest net working capital is {_format(value)} "
            f"{metric.unit.normalized_unit} ({classification.label})."
        ),
        selection,
        extra_limitations=(
            "Negative working capital is a screening observation, not proof of "
            "financial weakness.",
        ),
    )


def _assess_debt_growth_coverage(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    candidates = (
        ("balance.borrowings", "derived.leverage.total_debt_proxy"),
        ("income.ebitda",),
        ("cash_flow.operating",),
    )
    bundle, selection = _primary_aligned_bundle(evidence, candidates)
    if bundle is None:
        return _selection_na("debt_growth_coverage", selection)
    debt, ebitda, operating_cash_flow = bundle
    growth_values = tuple(_growth(pair) for pair in bundle)
    if any(value is None for value in growth_values):
        return _na_finding(
            "debt_growth_coverage",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "Debt, EBITDA, and operating-cash-flow growth require positive "
            "prior-period bases.",
        )
    debt_growth, ebitda_growth, ocf_growth = growth_values
    assert debt_growth is not None
    assert ebitda_growth is not None
    assert ocf_growth is not None
    excess = debt_growth - max(ebitda_growth, ocf_growth)
    if debt_growth <= 0 or excess <= Decimal("10"):
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "covered",
        )
    elif excess <= Decimal("25"):
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "outpacing operating capacity",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "materially outpacing operating capacity",
        )
    metrics = tuple(item for pair in bundle for item in pair)
    return _evaluated_finding(
        "debt_growth_coverage",
        classification,
        _observation(metrics, value=excess, unit=_percent_unit()),
        _numeric_threshold(
            "financial_health.debt_growth.excess",
            FundamentalThresholdOperator.LESS_THAN_OR_EQUAL,
            Decimal("10"),
            _percent_unit(),
            "Debt growth less the stronger of EBITDA and operating-cash-flow "
            "growth passes at 10 percentage points or below, warns through 25, "
            "and fails above 25.",
        ),
        (
            f"Debt growth exceeds the stronger operating-capacity growth rate "
            f"by {_format(excess)} percentage points ({classification.label})."
        ),
        selection,
    )


def _assess_balance_sheet_expansion(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    candidates = (
        ("balance.total_assets",),
        ("income.revenue",),
        ("income.pat",),
    )
    bundle, selection = _primary_aligned_bundle(evidence, candidates)
    if bundle is None:
        return _selection_na("balance_sheet_expansion", selection)
    growth_values = tuple(_growth(pair) for pair in bundle)
    if any(value is None for value in growth_values):
        return _na_finding(
            "balance_sheet_expansion",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "Asset, revenue, and profit growth require positive prior-period bases.",
        )
    asset_growth, revenue_growth, profit_growth = growth_values
    assert asset_growth is not None
    assert revenue_growth is not None
    assert profit_growth is not None
    excess = asset_growth - max(revenue_growth, profit_growth)
    if excess <= Decimal("10"):
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "proportionate",
        )
    elif excess <= Decimal("25"):
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "faster than operating growth",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "materially faster than operating growth",
        )
    metrics = tuple(item for pair in bundle for item in pair)
    return _evaluated_finding(
        "balance_sheet_expansion",
        classification,
        _observation(metrics, value=excess, unit=_percent_unit()),
        _numeric_threshold(
            "financial_health.asset_growth.excess",
            FundamentalThresholdOperator.LESS_THAN_OR_EQUAL,
            Decimal("10"),
            _percent_unit(),
            "Asset growth less the stronger of revenue and PAT growth passes "
            "at 10 percentage points or below, warns through 25, and fails above 25.",
        ),
        (
            f"Asset growth exceeds the stronger revenue/PAT growth rate by "
            f"{_format(excess)} percentage points ({classification.label})."
        ),
        selection,
    )


def _assess_reporting_basis_conflict(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    comparisons: tuple[
        tuple[str, tuple[str, ...], Callable[[Decimal], _Classification]], ...
    ] = (
        (
            "current ratio",
            ("derived.working_capital.current_ratio", "ratio.current"),
            _classify_liquidity,
        ),
        (
            "debt to equity",
            ("derived.leverage.debt_to_equity", "ratio.debt_to_equity"),
            _classify_debt_to_equity,
        ),
        (
            "interest coverage",
            ("ratio.interest_coverage",),
            _classify_interest_coverage,
        ),
    )
    compared: list[NormalizedFundamentalMetric] = []
    differences: list[str] = []
    severe_conflicts: list[str] = []
    for label, metric_ids, classifier in comparisons:
        consolidated = _series(
            evidence,
            metric_ids,
            basis=FinancialReportingBasis.CONSOLIDATED,
        )
        standalone = _series(
            evidence,
            metric_ids,
            basis=FinancialReportingBasis.STANDALONE,
        )
        if not consolidated.metrics or not standalone.metrics:
            continue
        left = consolidated.metrics[-1]
        right = standalone.metrics[-1]
        if left.period.end_date != right.period.end_date:
            continue
        left_class = classifier(_metric_value(left))
        right_class = classifier(_metric_value(right))
        compared.extend((left, right))
        if left_class.status is not right_class.status:
            differences.append(label)
            if {
                left_class.status,
                right_class.status,
            } == {
                FundamentalFindingStatus.PASS,
                FundamentalFindingStatus.FAIL,
            }:
                severe_conflicts.append(label)

    if not compared:
        return _na_finding(
            "reporting_basis_conflict",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "No same-period consolidated and standalone health metric could "
            "be compared.",
        )
    if severe_conflicts:
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.HIGH,
            "material directional conflict",
        )
    elif differences:
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "threshold-band divergence",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "aligned threshold bands",
        )
    return _evaluated_finding(
        "reporting_basis_conflict",
        classification,
        _observation(
            tuple(compared),
            value=Decimal(len(differences)),
            unit=_count_unit(),
        ),
        _numeric_threshold(
            "financial_health.reporting_basis.conflict_count",
            FundamentalThresholdOperator.EQUAL,
            Decimal("0"),
            _count_unit(),
            "Consolidated and standalone metrics must remain in the same "
            "deterministic health band; raw magnitude differences are not flagged.",
        ),
        (
            f"Compared {len(compared) // 2} same-period metric pairs; "
            f"{len(differences)} crossed a health threshold band "
            f"({classification.label})."
        ),
        _SeriesSelection(metrics=tuple(compared), had_candidates=True),
        extra_limitations=(
            "A reporting-basis conflict requires review but does not imply an "
            "accounting error or misconduct.",
        ),
    )


def _assess_pat_ocf_alignment(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    bundle, selection = _primary_aligned_bundle(
        evidence,
        (("income.pat",), ("cash_flow.operating",)),
        count=3,
    )
    if bundle is None:
        return _earnings_selection_na("pat_ocf_alignment", selection)
    pat, operating_cash_flow = bundle
    if not _same_monetary_currency((*pat, *operating_cash_flow)):
        return _earnings_na_finding(
            "pat_ocf_alignment",
            FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            "PAT and operating cash flow do not use one compatible currency.",
        )
    pat_values = tuple(_base_value(metric) for metric in pat)
    if any(value <= 0 for value in pat_values):
        return _earnings_na_finding(
            "pat_ocf_alignment",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "PAT-to-OCF alignment requires positive PAT in all three periods.",
        )
    conversion = tuple(
        _base_value(ocf_metric) / pat_value
        for ocf_metric, pat_value in zip(
            operating_cash_flow,
            pat_values,
            strict=True,
        )
    )
    average = sum(conversion, Decimal("0")) / Decimal(len(conversion))
    minimum = min(conversion)
    if average >= Decimal("0.8") and minimum >= Decimal("0.5"):
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "cash-backed",
        )
    elif average >= Decimal("0.5") and minimum >= 0:
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "partially cash-backed",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "weakly cash-backed",
        )
    metrics = tuple((*pat, *operating_cash_flow))
    return _earnings_evaluated_finding(
        "pat_ocf_alignment",
        classification,
        _observation(metrics, value=average, unit=_ratio_unit()),
        _composite_threshold_for_policy(
            "earnings_quality.pat_ocf.alignment",
            EARNINGS_QUALITY_POLICY_VERSION,
            "Using three consecutive annual periods: pass when average OCF/PAT "
            "is at least 0.8x and no period is below 0.5x; warn when average is "
            "at least 0.5x with no negative period; otherwise fail.",
        ),
        (
            f"Three-period OCF/PAT averages {_format(average)}x with a minimum "
            f"of {_format(minimum)}x ({classification.label})."
        ),
        selection,
    )


def _assess_cumulative_cash_conversion(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    bundle, selection = _primary_aligned_bundle(
        evidence,
        (("income.pat",), ("cash_flow.operating",)),
        count=3,
    )
    if bundle is None:
        return _earnings_selection_na("cash_conversion_cumulative", selection)
    pat, operating_cash_flow = bundle
    if not _same_monetary_currency((*pat, *operating_cash_flow)):
        return _earnings_na_finding(
            "cash_conversion_cumulative",
            FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            "PAT and operating cash flow do not use one compatible currency.",
        )
    cumulative_pat = sum((_base_value(metric) for metric in pat), Decimal("0"))
    if cumulative_pat <= 0:
        return _earnings_na_finding(
            "cash_conversion_cumulative",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "Cumulative cash conversion requires positive cumulative PAT.",
        )
    cumulative_ocf = sum(
        (_base_value(metric) for metric in operating_cash_flow),
        Decimal("0"),
    )
    conversion = cumulative_ocf / cumulative_pat
    if conversion >= Decimal("0.9"):
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "strong",
        )
    elif conversion >= Decimal("0.6"):
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "moderate",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "weak",
        )
    return _earnings_evaluated_finding(
        "cash_conversion_cumulative",
        classification,
        _observation(
            tuple((*pat, *operating_cash_flow)),
            value=conversion,
            unit=_ratio_unit(),
        ),
        _numeric_threshold_for_policy(
            "earnings_quality.cash_conversion.cumulative",
            EARNINGS_QUALITY_POLICY_VERSION,
            FundamentalThresholdOperator.GREATER_THAN_OR_EQUAL,
            Decimal("0.9"),
            _ratio_unit(),
            "Cumulative three-period OCF/PAT passes at or above 0.9x, warns "
            "from 0.6x to below 0.9x, and fails below 0.6x.",
        ),
        (
            f"Cumulative three-period OCF/PAT is {_format(conversion)}x "
            f"({classification.label})."
        ),
        selection,
    )


def _assess_fcf_consistency(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    selection = _primary_series(
        evidence,
        (
            "derived.cash_conversion.free_cash_flow",
            "cash_flow.free_cash_flow",
        ),
        count=3,
    )
    if not selection.metrics:
        return _earnings_selection_na("fcf_consistency", selection)
    positive_periods = sum(
        _base_value(metric) > 0 for metric in selection.metrics
    )
    if positive_periods == 3:
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "consistently positive",
        )
    elif positive_periods == 2:
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "intermittent",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "persistently weak",
        )
    return _earnings_evaluated_finding(
        "fcf_consistency",
        classification,
        _observation(
            selection.metrics,
            value=Decimal(positive_periods),
            unit=_count_unit(),
        ),
        _numeric_threshold_for_policy(
            "earnings_quality.fcf.positive_periods",
            EARNINGS_QUALITY_POLICY_VERSION,
            FundamentalThresholdOperator.EQUAL,
            Decimal("3"),
            _count_unit(),
            "Three positive annual FCF periods pass; two warn; zero or one fail.",
        ),
        (
            f"FCF is positive in {positive_periods} of 3 consecutive annual "
            f"periods ({classification.label})."
        ),
        selection,
        extra_limitations=(
            "Negative FCF can reflect an investment cycle and requires capital-"
            "allocation context.",
        ),
    )


def _assess_earnings_cash_divergence(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    bundle, selection = _primary_aligned_bundle(
        evidence,
        (("income.ebitda",), ("income.pat",), ("cash_flow.operating",)),
    )
    if bundle is None:
        return _earnings_selection_na("ebitda_pat_ocf_divergence", selection)
    growth_values = tuple(_growth(pair) for pair in bundle)
    if any(value is None for value in growth_values):
        return _earnings_na_finding(
            "ebitda_pat_ocf_divergence",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "Growth divergence requires positive prior EBITDA, PAT, and OCF.",
        )
    ebitda_growth, pat_growth, ocf_growth = growth_values
    assert ebitda_growth is not None
    assert pat_growth is not None
    assert ocf_growth is not None
    spread = max(ebitda_growth, pat_growth, ocf_growth) - min(
        ebitda_growth,
        pat_growth,
        ocf_growth,
    )
    profits_up_cash_down = (
        ebitda_growth >= 0 and pat_growth >= 0 and ocf_growth < 0
    )
    if profits_up_cash_down and spread > Decimal("25"):
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "profit growth diverges materially from cash flow",
        )
    elif profits_up_cash_down or spread > Decimal("25"):
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "growth paths diverge",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "growth paths are reasonably aligned",
        )
    return _earnings_evaluated_finding(
        "ebitda_pat_ocf_divergence",
        classification,
        _observation(
            tuple(metric for pair in bundle for metric in pair),
            value=spread,
            unit=_percent_unit(),
        ),
        _numeric_threshold_for_policy(
            "earnings_quality.growth_divergence.spread",
            EARNINGS_QUALITY_POLICY_VERSION,
            FundamentalThresholdOperator.LESS_THAN_OR_EQUAL,
            Decimal("25"),
            _percent_unit(),
            "A growth-rate spread up to 25 percentage points passes unless "
            "EBITDA and PAT rise while OCF declines; material opposing paths fail.",
        ),
        (
            f"The EBITDA/PAT/OCF growth spread is {_format(spread)} percentage "
            f"points ({classification.label})."
        ),
        selection,
    )


def _assess_working_capital_quality(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    bundle, selection = _primary_aligned_bundle(
        evidence,
        (("balance.trade_receivables",), ("income.revenue",)),
    )
    if bundle is None:
        return _earnings_selection_na("working_capital_quality", selection)
    receivables, revenue = bundle
    receivable_growth = _growth(receivables)
    revenue_growth = _growth(revenue)
    if receivable_growth is None or revenue_growth is None:
        return _earnings_na_finding(
            "working_capital_quality",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "Working-capital growth requires positive prior receivables and revenue.",
        )
    metrics: list[NormalizedFundamentalMetric] = [*receivables, *revenue]
    excess_values = [receivable_growth - revenue_growth]
    basis = receivables[-1].key.reporting_basis
    inventory = _series(
        evidence,
        ("balance.inventory",),
        basis=basis,
        count=2,
    )
    if inventory.metrics and tuple(
        metric.period.end_date for metric in inventory.metrics
    ) == tuple(metric.period.end_date for metric in revenue):
        inventory_growth = _growth(inventory.metrics)
        if inventory_growth is not None:
            excess_values.append(inventory_growth - revenue_growth)
            metrics.extend(inventory.metrics)
    excess = max(excess_values)
    if excess <= Decimal("10"):
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "controlled",
        )
    elif excess <= Decimal("25"):
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "absorbing additional working capital",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "materially outgrowing revenue",
        )
    limitations = (
        ()
        if inventory.metrics
        else (
            "Inventory growth was unavailable; the rule used receivables and "
            "revenue only.",
        )
    )
    return _earnings_evaluated_finding(
        "working_capital_quality",
        classification,
        _observation(tuple(metrics), value=excess, unit=_percent_unit()),
        _numeric_threshold_for_policy(
            "earnings_quality.working_capital.excess_growth",
            EARNINGS_QUALITY_POLICY_VERSION,
            FundamentalThresholdOperator.LESS_THAN_OR_EQUAL,
            Decimal("10"),
            _percent_unit(),
            "The greater of receivables or inventory growth, less revenue "
            "growth, passes at 10 percentage points or below, warns through "
            "25, and fails above 25.",
        ),
        (
            f"Maximum working-capital growth excess is {_format(excess)} "
            f"percentage points ({classification.label})."
        ),
        selection,
        extra_limitations=limitations,
    )


def _assess_margin_cash_conversion(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    bundle, selection = _primary_aligned_bundle(
        evidence,
        (
            ("income.ebitda",),
            ("income.revenue",),
            ("income.pat",),
            ("cash_flow.operating",),
        ),
    )
    if bundle is None:
        return _earnings_selection_na("margin_cash_conversion", selection)
    ebitda, revenue, pat, operating_cash_flow = bundle
    if not _same_monetary_currency(
        (*ebitda, *revenue, *pat, *operating_cash_flow)
    ):
        return _earnings_na_finding(
            "margin_cash_conversion",
            FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            "EBITDA, revenue, PAT, and OCF do not use one compatible currency.",
        )
    if any(
        _base_value(metric) <= 0
        for metric in (*revenue, *pat)
    ):
        return _earnings_na_finding(
            "margin_cash_conversion",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "Margin/cash alignment requires positive revenue and PAT.",
        )
    margins = tuple(
        _base_value(ebitda_metric) / _base_value(revenue_metric)
        * Decimal("100")
        for ebitda_metric, revenue_metric in zip(ebitda, revenue, strict=True)
    )
    conversions = tuple(
        _base_value(ocf_metric) / _base_value(pat_metric)
        for ocf_metric, pat_metric in zip(
            operating_cash_flow,
            pat,
            strict=True,
        )
    )
    margin_change = margins[-1] - margins[0]
    conversion_change = conversions[-1] - conversions[0]
    improving_margin = margin_change >= Decimal("2")
    if improving_margin and conversions[-1] < Decimal("0.5"):
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "margin expansion is not cash-backed",
        )
    elif improving_margin and (
        conversions[-1] < Decimal("0.8")
        or conversion_change < Decimal("-0.2")
    ):
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "margin expansion has weaker cash support",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "margin and cash conversion are aligned",
        )
    return _earnings_evaluated_finding(
        "margin_cash_conversion",
        classification,
        _observation(
            tuple(metric for pair in bundle for metric in pair),
            value=conversions[-1],
            unit=_ratio_unit(),
        ),
        _composite_threshold_for_policy(
            "earnings_quality.margin_cash.alignment",
            EARNINGS_QUALITY_POLICY_VERSION,
            "When EBITDA margin expands by at least 2 percentage points, "
            "latest OCF/PAT below 0.8x or deterioration above 0.2x warns; "
            "latest conversion below 0.5x fails.",
        ),
        (
            f"EBITDA margin changed {_format(margin_change)} percentage points "
            f"and latest OCF/PAT is {_format(conversions[-1])}x "
            f"({classification.label})."
        ),
        selection,
    )


def _assess_annual_quarterly_consistency(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    compared: list[NormalizedFundamentalMetric] = []
    conflicts: list[str] = []
    for label, metric_id in (
        ("revenue", "derived.growth.revenue_yoy_pct"),
        ("PAT", "derived.growth.pat_yoy_pct"),
    ):
        annual = _primary_series(
            evidence,
            (metric_id,),
            period_kind=FundamentalMetricPeriodKind.ANNUAL,
        )
        quarterly = _primary_series(
            evidence,
            (metric_id,),
            period_kind=FundamentalMetricPeriodKind.QUARTERLY,
        )
        if not annual.metrics or not quarterly.metrics:
            continue
        annual_metric = annual.metrics[-1]
        quarterly_metric = quarterly.metrics[-1]
        compared.extend((annual_metric, quarterly_metric))
        if _metric_value(annual_metric) * _metric_value(quarterly_metric) < 0:
            conflicts.append(label)
    if len(compared) != 4:
        return _earnings_na_finding(
            "annual_quarterly_consistency",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "Both revenue and PAT require annual and quarterly YoY growth "
            "observations.",
        )
    if len(conflicts) == 2:
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.HIGH,
            "broad recent directional conflict",
        )
    elif conflicts:
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "partial recent directional conflict",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "directionally consistent",
        )
    return _earnings_evaluated_finding(
        "annual_quarterly_consistency",
        classification,
        _observation(
            tuple(compared),
            value=Decimal(len(conflicts)),
            unit=_count_unit(),
        ),
        _numeric_threshold_for_policy(
            "earnings_quality.annual_quarterly.direction_conflicts",
            EARNINGS_QUALITY_POLICY_VERSION,
            FundamentalThresholdOperator.EQUAL,
            Decimal("0"),
            _count_unit(),
            "Latest annual and quarterly YoY growth must not have opposing "
            "signs for revenue or PAT.",
        ),
        (
            f"Compared {len(compared) // 2} annual/quarterly growth pairs; "
            f"{len(conflicts)} have opposing direction ({classification.label})."
        ),
        _SeriesSelection(metrics=tuple(compared), had_candidates=True),
        extra_limitations=(
            "This is a recency-consistency screen, not a forecast of the next "
            "annual result.",
        ),
    )


def _assess_dilution(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    selection = _primary_series(evidence, ("income.share_count",), count=2)
    if not selection.metrics:
        return _earnings_selection_na("dilution", selection)
    growth = _growth(selection.metrics)
    if growth is None:
        return _earnings_na_finding(
            "dilution",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "Dilution requires a positive prior-period share count.",
        )
    if growth <= Decimal("1"):
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "limited",
        )
    elif growth <= Decimal("5"):
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "notable",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "material",
        )
    return _earnings_evaluated_finding(
        "dilution",
        classification,
        _observation(selection.metrics, value=growth, unit=_percent_unit()),
        _numeric_threshold_for_policy(
            "earnings_quality.share_count.growth",
            EARNINGS_QUALITY_POLICY_VERSION,
            FundamentalThresholdOperator.LESS_THAN_OR_EQUAL,
            Decimal("1"),
            _percent_unit(),
            "Annual share-count growth up to 1% passes, above 1% through 5% "
            "warns, and above 5% fails.",
        ),
        (
            f"Annual share count changed {_format(growth)}% "
            f"({classification.label})."
        ),
        selection,
    )


def _assess_exceptional_dependence(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    bundle, selection = _primary_aligned_bundle(
        evidence,
        (("income.exceptional_items",), ("income.pbt",)),
        count=1,
    )
    if bundle is None:
        return _earnings_selection_na("exceptional_dependence", selection)
    exceptional, pbt = bundle
    if not _same_monetary_currency((*exceptional, *pbt)):
        return _earnings_na_finding(
            "exceptional_dependence",
            FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            "Exceptional items and PBT do not use one compatible currency.",
        )
    pbt_value = abs(_base_value(pbt[-1]))
    if pbt_value == 0:
        return _earnings_na_finding(
            "exceptional_dependence",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "Exceptional-item dependence is undefined when PBT is zero.",
        )
    share = abs(_base_value(exceptional[-1])) / pbt_value * Decimal("100")
    if share <= Decimal("5"):
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "limited",
        )
    elif share <= Decimal("15"):
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "notable",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "material",
        )
    return _earnings_evaluated_finding(
        "exceptional_dependence",
        classification,
        _observation(
            tuple((*exceptional, *pbt)),
            value=share,
            unit=_percent_unit(),
        ),
        _numeric_threshold_for_policy(
            "earnings_quality.exceptional_items.pbt_share",
            EARNINGS_QUALITY_POLICY_VERSION,
            FundamentalThresholdOperator.LESS_THAN_OR_EQUAL,
            Decimal("5"),
            _percent_unit(),
            "Absolute exceptional items as a percentage of absolute PBT pass "
            "at 5% or below, warn through 15%, and fail above 15%.",
        ),
        (
            f"Exceptional items equal {_format(share)}% of absolute PBT "
            f"({classification.label})."
        ),
        selection,
    )


def _assess_persistent_profit_cash_mismatch(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    bundle, selection = _primary_aligned_bundle(
        evidence,
        (("income.pat",), ("cash_flow.operating",)),
        count=3,
    )
    if bundle is None:
        return _forensic_selection_na(
            "persistent_profit_cash_mismatch",
            selection,
        )
    pat, operating_cash_flow = bundle
    metrics = tuple((*pat, *operating_cash_flow))
    if not _same_monetary_currency(metrics):
        return _forensic_na_finding(
            "persistent_profit_cash_mismatch",
            FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            "PAT and operating cash flow do not use one compatible currency.",
        )
    mismatch_count = sum(
        _base_value(profit) > 0 and _base_value(cash_flow) < 0
        for profit, cash_flow in zip(pat, operating_cash_flow, strict=True)
    )
    if mismatch_count >= 2:
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.CRITICAL,
            "persistent",
        )
    elif mismatch_count == 1:
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.HIGH,
            "present in one period",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "not observed",
        )
    return _forensic_evaluated_finding(
        "persistent_profit_cash_mismatch",
        classification,
        _observation(
            metrics,
            value=Decimal(mismatch_count),
            unit=_count_unit(),
        ),
        _numeric_threshold_for_policy(
            "forensic_risk.profit_cash_mismatch.period_count",
            FORENSIC_RISK_POLICY_VERSION,
            FundamentalThresholdOperator.EQUAL,
            Decimal("0"),
            _count_unit(),
            "Across three consecutive annual periods, zero periods with "
            "positive PAT and negative OCF pass, one warns, and two or more "
            "fail.",
        ),
        (
            f"Positive PAT coincides with negative OCF in {mismatch_count} "
            f"of three periods ({classification.label})."
        ),
        selection,
    )


def _assess_receivables_growth(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    bundle, selection = _primary_aligned_bundle(
        evidence,
        (("balance.trade_receivables",), ("income.revenue",)),
        count=3,
    )
    metric_label = "Receivables"
    if bundle is None and not selection.had_candidates:
        bundle, selection = _primary_aligned_bundle(
            evidence,
            (("working_capital.debtor_days",), ("income.revenue",)),
            count=3,
        )
        metric_label = "Debtor days"
    if bundle is None:
        return _forensic_selection_na("receivables_growth", selection)
    receivables, revenue = bundle
    if metric_label == "Receivables" and not _same_monetary_currency(
        tuple((*receivables, *revenue))
    ):
        return _forensic_na_finding(
            "receivables_growth",
            FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            "Receivables and revenue do not use one compatible currency.",
        )
    excesses: list[Decimal] = []
    for index in range(2):
        receivable_growth = _growth(receivables[index : index + 2])
        revenue_growth = _growth(revenue[index : index + 2])
        if receivable_growth is None or revenue_growth is None:
            return _forensic_na_finding(
                "receivables_growth",
                FundamentalFindingNAReason.MISSING_EVIDENCE,
                f"{metric_label} and revenue growth require positive "
                "prior-period bases.",
            )
        excesses.append(receivable_growth - revenue_growth)
    material_count = sum(excess > Decimal("25") for excess in excesses)
    maximum_excess = max(excesses)
    if material_count == 2:
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "repeatedly material",
        )
    elif material_count == 1:
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.HIGH,
            "material in one interval",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "not materially faster",
        )
    return _forensic_evaluated_finding(
        "receivables_growth",
        classification,
        _observation(
            tuple((*receivables, *revenue)),
            value=maximum_excess,
            unit=_percent_unit(),
        ),
        _composite_threshold_for_policy(
            "forensic_risk.receivables_growth.excess",
            FORENSIC_RISK_POLICY_VERSION,
            "Across two consecutive annual intervals, growth exceeding "
            "revenue growth by more than 25 percentage points warns once and "
            "fails when repeated.",
        ),
        (
            f"Maximum {metric_label.lower()} growth excess is "
            f"{_format(maximum_excess)} percentage points across two "
            f"intervals ({classification.label})."
        ),
        selection,
    )


def _assess_forensic_leverage_and_coverage(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    debt = _primary_series(
        evidence,
        ("balance.borrowings", "derived.leverage.total_debt_proxy"),
        count=2,
    )
    if not debt.metrics:
        return _forensic_selection_na("leverage_and_coverage", debt)
    basis = debt.metrics[-1].key.reporting_basis
    coverage = _series(
        evidence,
        ("ratio.interest_coverage",),
        basis=basis,
    )
    if not coverage.metrics:
        return _forensic_selection_na("leverage_and_coverage", coverage)
    if (
        coverage.metrics[-1].unit.value_kind
        is not FundamentalValueKind.RATIO
    ):
        return _forensic_na_finding(
            "leverage_and_coverage",
            FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            "Interest coverage is not normalized as a ratio.",
        )
    if debt.metrics[-1].period.end_date != coverage.metrics[-1].period.end_date:
        return _forensic_na_finding(
            "leverage_and_coverage",
            FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            "Debt growth and interest coverage do not share a latest period.",
        )
    debt_growth = _growth(debt.metrics)
    if debt_growth is None:
        return _forensic_na_finding(
            "leverage_and_coverage",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "Debt growth requires a positive prior-period debt base.",
        )
    coverage_value = _metric_value(coverage.metrics[-1])
    if debt_growth > Decimal("20") and coverage_value < Decimal("2"):
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "rapid debt growth with weak coverage",
        )
    elif debt_growth > Decimal("10") and coverage_value < Decimal("3"):
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.HIGH,
            "debt growth with thin coverage",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "combined stress not observed",
        )
    metrics = tuple((*debt.metrics, *coverage.metrics))
    return _forensic_evaluated_finding(
        "leverage_and_coverage",
        classification,
        _observation(metrics, value=debt_growth, unit=_percent_unit()),
        _composite_threshold_for_policy(
            "forensic_risk.leverage_growth_and_coverage",
            FORENSIC_RISK_POLICY_VERSION,
            "Debt growth above 10% with coverage below 3x warns; debt growth "
            "above 20% with coverage below 2x fails.",
        ),
        (
            f"Debt changed {_format(debt_growth)}% while latest interest "
            f"coverage is {_format(coverage_value)}x "
            f"({classification.label})."
        ),
        debt,
    )


def _assess_promoter_pledge(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    selection = _ownership_series(
        evidence,
        ("ownership.promoter_pledge_pct",),
    )
    if not selection.metrics:
        return _forensic_selection_na("promoter_pledge", selection)
    metric = selection.metrics[-1]
    value = _metric_value(metric)
    if (
        metric.unit.value_kind is not FundamentalValueKind.PERCENTAGE
        or value < 0
        or value > 100
    ):
        return _forensic_na_finding(
            "promoter_pledge",
            FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            "Promoter pledge is not a valid percentage from 0 through 100.",
        )
    if value > Decimal("20"):
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.CRITICAL,
            "high",
        )
    elif value > Decimal("5"):
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.HIGH,
            "elevated",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "limited",
        )
    return _forensic_evaluated_finding(
        "promoter_pledge",
        classification,
        _observation((metric,), value=value, unit=metric.unit),
        _numeric_threshold_for_policy(
            "forensic_risk.promoter_pledge",
            FORENSIC_RISK_POLICY_VERSION,
            FundamentalThresholdOperator.LESS_THAN_OR_EQUAL,
            Decimal("5"),
            metric.unit,
            "Promoter pledge up to 5% passes, above 5% through 20% warns, "
            "and above 20% fails.",
        ),
        f"Latest promoter pledge is {_format(value)}% ({classification.label}).",
        selection,
    )


def _assess_promoter_holding_erosion(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    selection = _ownership_series(
        evidence,
        ("ownership.promoter_holding_pct",),
        count=4,
    )
    if not selection.metrics:
        return _forensic_selection_na("promoter_holding_erosion", selection)
    values = tuple(_metric_value(metric) for metric in selection.metrics)
    if any(
        metric.unit.value_kind is not FundamentalValueKind.PERCENTAGE
        for metric in selection.metrics
    ) or any(value < 0 or value > 100 for value in values):
        return _forensic_na_finding(
            "promoter_holding_erosion",
            FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            "Promoter holdings are not valid percentages from 0 through 100.",
        )
    decline_count = sum(
        current < prior
        for prior, current in zip(values[:-1], values[1:], strict=True)
    )
    total_drop = values[0] - values[-1]
    if decline_count == 3 and total_drop >= Decimal("2"):
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "persistent and material",
        )
    elif decline_count == 3 or total_drop >= Decimal("1"):
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.HIGH,
            "requires ownership review",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "not persistent or material",
        )
    return _forensic_evaluated_finding(
        "promoter_holding_erosion",
        classification,
        _observation(
            selection.metrics,
            value=total_drop,
            unit=_percent_unit(),
        ),
        _composite_threshold_for_policy(
            "forensic_risk.promoter_holding.erosion",
            FORENSIC_RISK_POLICY_VERSION,
            "Across four consecutive quarters, three declines or a total "
            "drop of at least 1 percentage point warn; three declines and a "
            "drop of at least 2 points fail.",
        ),
        (
            f"Promoter holding changed by {_format(-total_drop)} percentage "
            "points with "
            f"{decline_count} sequential declines ({classification.label})."
        ),
        selection,
    )


def _assess_basis_growth_divergence(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    comparable_metrics: list[NormalizedFundamentalMetric] = []
    divergent_labels: list[str] = []
    for metric_id in (
        "balance.borrowings",
        "cash_flow.operating",
        "income.pat",
        "income.revenue",
    ):
        consolidated = _series(
            evidence,
            (metric_id,),
            basis=FinancialReportingBasis.CONSOLIDATED,
            count=2,
        )
        standalone = _series(
            evidence,
            (metric_id,),
            basis=FinancialReportingBasis.STANDALONE,
            count=2,
        )
        if not consolidated.metrics or not standalone.metrics:
            continue
        if tuple(item.period.end_date for item in consolidated.metrics) != tuple(
            item.period.end_date for item in standalone.metrics
        ):
            continue
        units = tuple(
            item.unit for item in (*consolidated.metrics, *standalone.metrics)
        )
        if len({(unit.value_kind, unit.currency) for unit in units}) != 1:
            continue
        consolidated_growth = _growth(consolidated.metrics)
        standalone_growth = _growth(standalone.metrics)
        if consolidated_growth is None or standalone_growth is None:
            continue
        comparable_metrics.extend((*consolidated.metrics, *standalone.metrics))
        difference = abs(consolidated_growth - standalone_growth)
        if (
            consolidated_growth * standalone_growth < 0
            and difference >= Decimal("20")
        ) or difference >= Decimal("40"):
            divergent_labels.append(metric_id)
    comparison_count = len(comparable_metrics) // 4
    if comparison_count < 2:
        return _forensic_na_finding(
            "basis_growth_divergence",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "At least two aligned consolidated and standalone two-period "
            "series are required for a reporting-basis screen.",
        )
    divergence_count = len(divergent_labels)
    if divergence_count >= 2:
        classification = _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "repeatedly material",
        )
    elif divergence_count == 1:
        classification = _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "material in one metric",
        )
    else:
        classification = _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "not material",
        )
    return _forensic_evaluated_finding(
        "basis_growth_divergence",
        classification,
        _observation(
            tuple(comparable_metrics),
            value=Decimal(divergence_count),
            unit=_count_unit(),
        ),
        _composite_threshold_for_policy(
            "forensic_risk.reporting_basis.growth_divergence",
            FORENSIC_RISK_POLICY_VERSION,
            "A growth-rate gap of at least 40 percentage points, or opposite "
            "directions separated by at least 20 points, is material; one "
            "metric warns and two or more fail.",
        ),
        (
            f"Material divergence appears in {divergence_count} of "
            f"{comparison_count} comparable metrics ({classification.label})."
        ),
        _SeriesSelection(metrics=tuple(comparable_metrics), had_candidates=True),
        extra_limitations=(
            "Consolidated and standalone divergence can reflect group structure; "
            "it requires explanation and does not establish accounting error.",
        ),
    )


def _assess_accounting_conflicts(
    evidence: NormalizedFinancialEvidence,
) -> FundamentalAssessmentFinding:
    flagged = tuple(
        metric
        for metric in evidence.metrics
        if metric.value_status is FundamentalMetricValueStatus.CONFLICT
        or FundamentalMetricQualityFlag.DUPLICATE_CONFLICT in metric.quality_flags
    )
    checked_totals: list[NormalizedFundamentalMetric] = []
    mismatch_count = 0
    for basis in (
        FinancialReportingBasis.CONSOLIDATED,
        FinancialReportingBasis.STANDALONE,
    ):
        assets = _series(evidence, ("balance.total_assets",), basis=basis)
        liabilities = _series(
            evidence,
            ("balance.total_liabilities",),
            basis=basis,
        )
        if not assets.metrics or not liabilities.metrics:
            continue
        pair = (*assets.metrics, *liabilities.metrics)
        if (
            assets.metrics[-1].period.end_date
            != liabilities.metrics[-1].period.end_date
            or not _same_monetary_currency(pair)
        ):
            return _forensic_na_finding(
                "accounting_conflicts",
                FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
                "Asset and liability totals are not period- and currency-aligned.",
            )
        checked_totals.extend(pair)
        asset_value = abs(_base_value(assets.metrics[-1]))
        liability_value = abs(_base_value(liabilities.metrics[-1]))
        denominator = max(asset_value, liability_value, Decimal("1"))
        mismatch = abs(asset_value - liability_value) / denominator * Decimal("100")
        mismatch_count += mismatch > Decimal("0.5")
    if not flagged and not checked_totals:
        return _forensic_na_finding(
            "accounting_conflicts",
            FundamentalFindingNAReason.MISSING_EVIDENCE,
            "No explicit conflict marker or aligned balance-sheet totals are "
            "available for integrity screening.",
        )
    issue_count = len(flagged) + mismatch_count
    classification = (
        _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "conflicts detected",
        )
        if issue_count
        else _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "no configured conflict detected",
        )
    )
    metrics = tuple((*flagged, *checked_totals))
    return _forensic_evaluated_finding(
        "accounting_conflicts",
        classification,
        _observation(metrics, value=Decimal(issue_count), unit=_count_unit()),
        _numeric_threshold_for_policy(
            "forensic_risk.accounting_conflict.count",
            FORENSIC_RISK_POLICY_VERSION,
            FundamentalThresholdOperator.EQUAL,
            Decimal("0"),
            _count_unit(),
            "Zero normalized duplicate conflicts and zero asset/liability "
            "mismatches above 0.5% pass; any configured issue fails.",
        ),
        (
            f"The normalized evidence contains {issue_count} configured data "
            f"integrity issue(s) ({classification.label})."
        ),
        _SeriesSelection(metrics=metrics, had_candidates=True),
        extra_limitations=(
            "This validates only configured normalized-data invariants; it is "
            "not an audit opinion.",
        ),
    )


def _ownership_series(
    evidence: NormalizedFinancialEvidence,
    metric_ids: tuple[str, ...],
    *,
    count: int = 1,
) -> _SeriesSelection:
    quarterly = _series(
        evidence,
        metric_ids,
        basis=FinancialReportingBasis.NOT_APPLICABLE,
        count=count,
        period_kind=FundamentalMetricPeriodKind.QUARTERLY,
    )
    if quarterly.metrics or quarterly.had_candidates:
        return quarterly
    annual = _series(
        evidence,
        metric_ids,
        basis=FinancialReportingBasis.NOT_APPLICABLE,
        count=count,
        period_kind=FundamentalMetricPeriodKind.ANNUAL,
    )
    if annual.metrics:
        return _SeriesSelection(
            metrics=annual.metrics,
            had_candidates=True,
            basis_limitation=(
                "Quarterly ownership evidence was absent; annual evidence "
                "was evaluated explicitly."
            ),
        )
    return annual


def _primary_series(
    evidence: NormalizedFinancialEvidence,
    metric_ids: tuple[str, ...],
    *,
    count: int = 1,
    period_kind: FundamentalMetricPeriodKind = FundamentalMetricPeriodKind.ANNUAL,
) -> _SeriesSelection:
    consolidated = _series(
        evidence,
        metric_ids,
        basis=FinancialReportingBasis.CONSOLIDATED,
        count=count,
        period_kind=period_kind,
    )
    if consolidated.metrics or consolidated.had_candidates:
        return consolidated
    standalone = _series(
        evidence,
        metric_ids,
        basis=FinancialReportingBasis.STANDALONE,
        count=count,
        period_kind=period_kind,
    )
    if standalone.metrics:
        return _SeriesSelection(
            metrics=standalone.metrics,
            had_candidates=True,
            basis_limitation=(
                "Consolidated evidence was absent; standalone evidence was "
                "evaluated explicitly."
            ),
        )
    return standalone


def _series(
    evidence: NormalizedFinancialEvidence,
    metric_ids: tuple[str, ...],
    *,
    basis: FinancialReportingBasis,
    count: int = 1,
    period_kind: FundamentalMetricPeriodKind = FundamentalMetricPeriodKind.ANNUAL,
) -> _SeriesSelection:
    candidates: tuple[NormalizedFundamentalMetric, ...] = ()
    selected_metric_id: str | None = None
    for metric_id in metric_ids:
        matching = tuple(
            metric
            for metric in evidence.metrics
            if metric.key.metric_id == metric_id
            and metric.key.reporting_basis is basis
            and metric.period.period_kind is period_kind
            and not metric.period.is_forecast
        )
        if matching:
            candidates = matching
            selected_metric_id = metric_id
            break
    if not candidates:
        return _SeriesSelection(
            metrics=(),
            had_candidates=False,
            na_reason=FundamentalFindingNAReason.MISSING_EVIDENCE,
            limitation=(
                f"Required {period_kind.value} metric is missing for "
                f"{basis.value}: "
                f"{', '.join(metric_ids)}."
            ),
        )
    if any(metric.period.end_date is None for metric in candidates):
        return _SeriesSelection(
            metrics=(),
            had_candidates=True,
            na_reason=FundamentalFindingNAReason.MISSING_EVIDENCE,
            limitation=(
                f"{selected_metric_id} requires dated "
                f"{period_kind.value} observations."
            ),
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
        return _SeriesSelection(
            metrics=(),
            had_candidates=True,
            na_reason=FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            limitation=(
                f"{selected_metric_id} has multiple observations for one "
                f"{period_kind.value} end date."
            ),
        )
    selected = ordered[-count:]
    if len(selected) < count:
        return _SeriesSelection(
            metrics=(),
            had_candidates=True,
            na_reason=FundamentalFindingNAReason.MISSING_EVIDENCE,
            limitation=(
                f"{selected_metric_id} requires {count} "
                f"{period_kind.value} observations."
            ),
        )
    unavailable = tuple(
        metric for metric in selected if metric.value_status not in _AVAILABLE_STATES
    )
    if unavailable:
        reason = (
            FundamentalFindingNAReason.CONFLICTING_EVIDENCE
            if any(
                metric.value_status is FundamentalMetricValueStatus.CONFLICT
                for metric in unavailable
            )
            else FundamentalFindingNAReason.MISSING_EVIDENCE
        )
        return _SeriesSelection(
            metrics=(),
            had_candidates=True,
            na_reason=reason,
            limitation=(
                f"{selected_metric_id} contains unavailable "
                f"{period_kind.value} observations."
            ),
        )
    if count > 1 and any(
        not _periods_are_consecutive(prior, current, period_kind)
        for prior, current in zip(selected, selected[1:], strict=False)
    ):
        return _SeriesSelection(
            metrics=(),
            had_candidates=True,
            na_reason=FundamentalFindingNAReason.MISSING_EVIDENCE,
            limitation=(
                f"{selected_metric_id} does not have {count} consecutive "
                f"{period_kind.value} periods."
            ),
        )
    return _SeriesSelection(metrics=selected, had_candidates=True)


def _periods_are_consecutive(
    prior: NormalizedFundamentalMetric,
    current: NormalizedFundamentalMetric,
    period_kind: FundamentalMetricPeriodKind,
) -> bool:
    prior_date = prior.period.end_date
    current_date = current.period.end_date
    if prior_date is None or current_date is None:
        return False
    month_gap = (
        (current_date.year - prior_date.year) * 12
        + current_date.month
        - prior_date.month
    )
    if period_kind is FundamentalMetricPeriodKind.ANNUAL:
        return current_date.year - prior_date.year == 1
    if period_kind is FundamentalMetricPeriodKind.QUARTERLY:
        return month_gap == 3
    return False


def _primary_aligned_bundle(
    evidence: NormalizedFinancialEvidence,
    metric_candidates: tuple[tuple[str, ...], ...],
    *,
    count: int = 2,
    period_kind: FundamentalMetricPeriodKind = FundamentalMetricPeriodKind.ANNUAL,
) -> tuple[
    tuple[tuple[NormalizedFundamentalMetric, ...], ...] | None,
    _SeriesSelection,
]:
    for index, basis in enumerate(
        (
            FinancialReportingBasis.CONSOLIDATED,
            FinancialReportingBasis.STANDALONE,
        )
    ):
        selections = tuple(
            _series(
                evidence,
                candidates,
                basis=basis,
                count=count,
                period_kind=period_kind,
            )
            for candidates in metric_candidates
        )
        if index == 0 and any(item.had_candidates for item in selections):
            allow_standalone = False
        else:
            allow_standalone = True
        if all(item.metrics for item in selections):
            period_sets = {
                tuple(metric.period.end_date for metric in item.metrics)
                for item in selections
            }
            if len(period_sets) != 1:
                return None, _SeriesSelection(
                    metrics=(),
                    had_candidates=True,
                    na_reason=FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
                    limitation=(
                        "Required annual metric series do not share the same "
                        "two reporting periods."
                    ),
                )
            basis_limitation = (
                "Consolidated evidence was absent; standalone evidence was "
                "evaluated explicitly."
                if basis is FinancialReportingBasis.STANDALONE
                else None
            )
            return (
                tuple(item.metrics for item in selections),
                _SeriesSelection(
                    metrics=tuple(
                        metric
                        for item in selections
                        for metric in item.metrics
                    ),
                    had_candidates=True,
                    basis_limitation=basis_limitation,
                ),
            )
        if not allow_standalone:
            failed = next(item for item in selections if not item.metrics)
            return None, failed
    return None, next(
        item
        for item in selections
        if not item.metrics
    )


def _growth(
    metrics: tuple[NormalizedFundamentalMetric, ...],
) -> Decimal | None:
    first_unit = metrics[0].unit
    if any(
        metric.unit.value_kind is not first_unit.value_kind
        or metric.unit.currency != first_unit.currency
        for metric in metrics[1:]
    ):
        return None
    prior = _base_value(metrics[0])
    current = _base_value(metrics[1])
    if prior <= 0:
        return None
    with localcontext() as context:
        context.prec = 34
        return (current / prior - Decimal("1")) * Decimal("100")


def _same_monetary_currency(
    metrics: tuple[NormalizedFundamentalMetric, ...],
) -> bool:
    return bool(metrics) and all(
        metric.unit.value_kind is FundamentalValueKind.MONETARY
        for metric in metrics
    ) and len({metric.unit.currency for metric in metrics}) == 1


def _metric_value(metric: NormalizedFundamentalMetric) -> Decimal:
    if metric.value is None:
        raise ValueError("available assessment metric must have a value")
    return metric.value


def _base_value(metric: NormalizedFundamentalMetric) -> Decimal:
    return _metric_value(metric) * metric.unit.scale


def _classify_liquidity(value: Decimal) -> _Classification:
    if value < Decimal("1"):
        return _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "weak",
        )
    if value < Decimal("1.5"):
        return _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "thin",
        )
    return _Classification(
        FundamentalFindingStatus.PASS,
        FundamentalFindingSeverity.NONE,
        "adequate",
    )


def _classify_debt_to_equity(value: Decimal) -> _Classification:
    if value < 0 or value > Decimal("2"):
        return _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "adverse",
        )
    if value > Decimal("1"):
        return _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "elevated",
        )
    return _Classification(
        FundamentalFindingStatus.PASS,
        FundamentalFindingSeverity.NONE,
        "controlled",
    )


def _classify_interest_coverage(value: Decimal) -> _Classification:
    if value < Decimal("1.5"):
        return _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "weak",
        )
    if value < Decimal("3"):
        return _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "thin",
        )
    return _Classification(
        FundamentalFindingStatus.PASS,
        FundamentalFindingSeverity.NONE,
        "adequate",
    )


def _classify_net_debt_to_ebitda(
    value: Decimal,
    *,
    net_debt_value: Decimal,
    ebitda_value: Decimal,
) -> _Classification:
    if ebitda_value <= 0:
        return _Classification(
            FundamentalFindingStatus.FAIL,
            FundamentalFindingSeverity.HIGH,
            "unreliable because EBITDA is non-positive",
        )
    if net_debt_value <= 0 or value <= Decimal("2"):
        return _Classification(
            FundamentalFindingStatus.PASS,
            FundamentalFindingSeverity.NONE,
            "controlled",
        )
    if value <= Decimal("3"):
        return _Classification(
            FundamentalFindingStatus.WARN,
            FundamentalFindingSeverity.MEDIUM,
            "elevated",
        )
    return _Classification(
        FundamentalFindingStatus.FAIL,
        FundamentalFindingSeverity.HIGH,
        "high",
    )


def _observation(
    metrics: tuple[NormalizedFundamentalMetric, ...],
    *,
    value: Decimal,
    unit: FundamentalMetricUnit,
) -> FundamentalFindingObservation:
    citations = {
        citation.citation_key: citation
        for metric in metrics
        for citation in metric.citations
    }
    bases = tuple(
        basis
        for basis in FinancialReportingBasis
        if any(metric.key.reporting_basis is basis for metric in metrics)
    )
    return FundamentalFindingObservation(
        metric_keys=tuple(
            sorted({metric.key.canonical_key for metric in metrics})
        ),
        reporting_bases=bases,
        value=value,
        unit=unit,
        citations=tuple(citations[key] for key in sorted(citations)),
    )


def _evaluated_finding(
    rule_id: str,
    classification: _Classification,
    observation: FundamentalFindingObservation,
    threshold: FundamentalAssessmentThreshold,
    summary: str,
    selection: _SeriesSelection,
    *,
    extra_limitations: tuple[str, ...] = (),
) -> FundamentalAssessmentFinding:
    limitations = tuple(
        limitation
        for limitation in (
            selection.basis_limitation,
            *extra_limitations,
        )
        if limitation is not None
    )
    return FundamentalAssessmentFinding(
        finding_id=(
            "fundamental.finding:financial_health:" + rule_id
        ),
        domain=FundamentalAssessmentDomain.FINANCIAL_HEALTH,
        rule_id=rule_id,
        rule_version=FINANCIAL_HEALTH_POLICY_VERSION,
        status=classification.status,
        severity=classification.severity,
        review_state=(
            FundamentalReviewState.NOT_REQUIRED
            if classification.status is FundamentalFindingStatus.PASS
            else FundamentalReviewState.REQUIRED
        ),
        title=_RULE_TITLES[rule_id],
        summary=summary,
        threshold=threshold,
        observation=observation,
        limitations=limitations,
    )


def _earnings_evaluated_finding(
    rule_id: str,
    classification: _Classification,
    observation: FundamentalFindingObservation,
    threshold: FundamentalAssessmentThreshold,
    summary: str,
    selection: _SeriesSelection,
    *,
    extra_limitations: tuple[str, ...] = (),
) -> FundamentalAssessmentFinding:
    limitations = tuple(
        limitation
        for limitation in (
            selection.basis_limitation,
            *extra_limitations,
        )
        if limitation is not None
    )
    return FundamentalAssessmentFinding(
        finding_id="fundamental.finding:earnings_quality:" + rule_id,
        domain=FundamentalAssessmentDomain.EARNINGS_QUALITY,
        rule_id=rule_id,
        rule_version=EARNINGS_QUALITY_POLICY_VERSION,
        status=classification.status,
        severity=classification.severity,
        review_state=(
            FundamentalReviewState.NOT_REQUIRED
            if classification.status is FundamentalFindingStatus.PASS
            else FundamentalReviewState.REQUIRED
        ),
        title=_EARNINGS_RULE_TITLES[rule_id],
        summary=summary,
        threshold=threshold,
        observation=observation,
        limitations=limitations,
    )


def _forensic_evaluated_finding(
    rule_id: str,
    classification: _Classification,
    observation: FundamentalFindingObservation,
    threshold: FundamentalAssessmentThreshold,
    summary: str,
    selection: _SeriesSelection,
    *,
    extra_limitations: tuple[str, ...] = (),
) -> FundamentalAssessmentFinding:
    limitations = tuple(
        limitation
        for limitation in (
            selection.basis_limitation,
            *extra_limitations,
        )
        if limitation is not None
    )
    return FundamentalAssessmentFinding(
        finding_id="fundamental.finding:forensic_risk:" + rule_id,
        domain=FundamentalAssessmentDomain.FORENSIC_RISK,
        rule_id=rule_id,
        rule_version=FORENSIC_RISK_POLICY_VERSION,
        status=classification.status,
        severity=classification.severity,
        review_state=(
            FundamentalReviewState.NOT_REQUIRED
            if classification.status is FundamentalFindingStatus.PASS
            else FundamentalReviewState.REQUIRED
        ),
        title=_FORENSIC_RULE_TITLES[rule_id],
        summary=summary,
        threshold=threshold,
        observation=observation,
        limitations=limitations,
    )


def _selection_na(
    rule_id: str,
    selection: _SeriesSelection,
) -> FundamentalAssessmentFinding:
    return _na_finding(
        rule_id,
        selection.na_reason or FundamentalFindingNAReason.MISSING_EVIDENCE,
        selection.limitation or "Required normalized evidence is unavailable.",
    )


def _earnings_selection_na(
    rule_id: str,
    selection: _SeriesSelection,
) -> FundamentalAssessmentFinding:
    return _earnings_na_finding(
        rule_id,
        selection.na_reason or FundamentalFindingNAReason.MISSING_EVIDENCE,
        selection.limitation or "Required normalized evidence is unavailable.",
    )


def _forensic_selection_na(
    rule_id: str,
    selection: _SeriesSelection,
) -> FundamentalAssessmentFinding:
    return _forensic_na_finding(
        rule_id,
        selection.na_reason or FundamentalFindingNAReason.MISSING_EVIDENCE,
        selection.limitation or "Required normalized evidence is unavailable.",
    )


def _na_finding(
    rule_id: str,
    reason: FundamentalFindingNAReason,
    limitation: str,
) -> FundamentalAssessmentFinding:
    return FundamentalAssessmentFinding(
        finding_id=(
            "fundamental.finding:financial_health:" + rule_id
        ),
        domain=FundamentalAssessmentDomain.FINANCIAL_HEALTH,
        rule_id=rule_id,
        rule_version=FINANCIAL_HEALTH_POLICY_VERSION,
        status=FundamentalFindingStatus.NA,
        severity=FundamentalFindingSeverity.NONE,
        review_state=(
            FundamentalReviewState.NOT_REQUIRED
            if reason is FundamentalFindingNAReason.NOT_APPLICABLE
            else FundamentalReviewState.REQUIRED
        ),
        na_reason=reason,
        title=_RULE_TITLES[rule_id],
        summary=(
            "The rule was not evaluated because required policy or evidence "
            "is unavailable."
        ),
        threshold=_composite_threshold(
            f"financial_health.{rule_id}.availability",
            "The rule requires complete, compatible, point-in-time evidence "
            "under an approved sector policy.",
        ),
        observation=None,
        limitations=(limitation,),
    )


def _earnings_na_finding(
    rule_id: str,
    reason: FundamentalFindingNAReason,
    limitation: str,
) -> FundamentalAssessmentFinding:
    return FundamentalAssessmentFinding(
        finding_id=(
            "fundamental.finding:earnings_quality:" + rule_id
        ),
        domain=FundamentalAssessmentDomain.EARNINGS_QUALITY,
        rule_id=rule_id,
        rule_version=EARNINGS_QUALITY_POLICY_VERSION,
        status=FundamentalFindingStatus.NA,
        severity=FundamentalFindingSeverity.NONE,
        review_state=(
            FundamentalReviewState.NOT_REQUIRED
            if reason is FundamentalFindingNAReason.NOT_APPLICABLE
            else FundamentalReviewState.REQUIRED
        ),
        na_reason=reason,
        title=_EARNINGS_RULE_TITLES[rule_id],
        summary=(
            "The rule was not evaluated because required policy or evidence "
            "is unavailable."
        ),
        threshold=_composite_threshold_for_policy(
            f"earnings_quality.{rule_id}.availability",
            EARNINGS_QUALITY_POLICY_VERSION,
            "The rule requires complete, compatible, point-in-time evidence "
            "under an approved sector policy.",
        ),
        observation=None,
        limitations=(limitation,),
    )


def _forensic_na_finding(
    rule_id: str,
    reason: FundamentalFindingNAReason,
    limitation: str,
) -> FundamentalAssessmentFinding:
    return FundamentalAssessmentFinding(
        finding_id="fundamental.finding:forensic_risk:" + rule_id,
        domain=FundamentalAssessmentDomain.FORENSIC_RISK,
        rule_id=rule_id,
        rule_version=FORENSIC_RISK_POLICY_VERSION,
        status=FundamentalFindingStatus.NA,
        severity=FundamentalFindingSeverity.NONE,
        review_state=(
            FundamentalReviewState.NOT_REQUIRED
            if reason is FundamentalFindingNAReason.NOT_APPLICABLE
            else FundamentalReviewState.REQUIRED
        ),
        na_reason=reason,
        title=_FORENSIC_RULE_TITLES[rule_id],
        summary=(
            "The rule was not evaluated because required policy or evidence "
            "is unavailable."
        ),
        threshold=_composite_threshold_for_policy(
            f"forensic_risk.{rule_id}.availability",
            FORENSIC_RISK_POLICY_VERSION,
            "The rule requires complete, compatible, point-in-time evidence "
            "under an approved sector policy.",
        ),
        observation=None,
        limitations=(limitation,),
    )


def _numeric_threshold(
    threshold_id: str,
    operator: FundamentalThresholdOperator,
    bound: Decimal,
    unit: FundamentalMetricUnit,
    description: str,
) -> FundamentalAssessmentThreshold:
    return FundamentalAssessmentThreshold(
        threshold_id=threshold_id,
        threshold_version=FINANCIAL_HEALTH_POLICY_VERSION,
        operator=operator,
        lower_bound=bound,
        unit=unit,
        description=description,
    )


def _numeric_threshold_for_policy(
    threshold_id: str,
    threshold_version: str,
    operator: FundamentalThresholdOperator,
    bound: Decimal,
    unit: FundamentalMetricUnit,
    description: str,
) -> FundamentalAssessmentThreshold:
    return FundamentalAssessmentThreshold(
        threshold_id=threshold_id,
        threshold_version=threshold_version,
        operator=operator,
        lower_bound=bound,
        unit=unit,
        description=description,
    )


def _composite_threshold(
    threshold_id: str,
    description: str,
) -> FundamentalAssessmentThreshold:
    return FundamentalAssessmentThreshold(
        threshold_id=threshold_id,
        threshold_version=FINANCIAL_HEALTH_POLICY_VERSION,
        operator=FundamentalThresholdOperator.COMPOSITE,
        description=description,
    )


def _composite_threshold_for_policy(
    threshold_id: str,
    threshold_version: str,
    description: str,
) -> FundamentalAssessmentThreshold:
    return FundamentalAssessmentThreshold(
        threshold_id=threshold_id,
        threshold_version=threshold_version,
        operator=FundamentalThresholdOperator.COMPOSITE,
        description=description,
    )


def _percent_unit() -> FundamentalMetricUnit:
    return FundamentalMetricUnit(
        value_kind=FundamentalValueKind.PERCENTAGE,
        normalized_unit="PERCENT",
        scale=Decimal("1"),
    )


def _ratio_unit() -> FundamentalMetricUnit:
    return FundamentalMetricUnit(
        value_kind=FundamentalValueKind.RATIO,
        normalized_unit="RATIO",
        scale=Decimal("1"),
    )


def _count_unit() -> FundamentalMetricUnit:
    return FundamentalMetricUnit(
        value_kind=FundamentalValueKind.COUNT,
        normalized_unit="COUNT",
        scale=Decimal("1"),
    )


def _format(value: Decimal) -> str:
    normalized = value.normalize()
    return format(normalized, "f")
