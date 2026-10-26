"""Initial versioned shadow policies for fundamental framework scoring.

The named frameworks encode documented public analytical principles. They do
not simulate or endorse an investor, and they cannot infer qualitative facts
that are absent from the committed fundamental evidence package.
"""

from decimal import Decimal

from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_assessment import (
    FundamentalAssessmentDomain,
    FundamentalFindingSeverity,
    FundamentalFindingStatus,
)
from app.models.fundamental_frameworks import (
    FundamentalFramework,
    FundamentalFrameworkEvidenceFamily,
)
from app.models.fundamental_metrics import (
    FundamentalMetricPeriodKind,
    FundamentalMetricUnit,
)
from app.models.fundamental_scoring import (
    FundamentalDecisionInfluenceMode,
    FundamentalEvidenceFamilyScoringPolicy,
    FundamentalFrameworkScoringPolicy,
    FundamentalMandatoryScoreConstraint,
    FundamentalMetricAggregation,
    FundamentalMetricScoreBand,
    FundamentalMetricScoringRule,
    FundamentalScoreGrade,
    FundamentalScoreGradeThresholds,
    FundamentalScorePolicyBundle,
    FundamentalScorecardStance,
    FundamentalStancePolicy,
)
from app.models.fundamentals import FundamentalValueKind


INITIAL_FRAMEWORK_SCORE_POLICY_VERSION = "1.0.0"

_ALL_ACCOUNTING_BASES = (
    FinancialReportingBasis.CONSOLIDATED,
    FinancialReportingBasis.STANDALONE,
)
_NON_ACCOUNTING_BASIS = (FinancialReportingBasis.NOT_APPLICABLE,)
_PERCENT_UNIT = FundamentalMetricUnit(
    value_kind=FundamentalValueKind.PERCENTAGE,
    normalized_unit="PERCENT",
    scale=Decimal("0.01"),
)
_RATIO_UNIT = FundamentalMetricUnit(
    value_kind=FundamentalValueKind.RATIO,
    normalized_unit="RATIO",
    scale=Decimal("1"),
)


def _band(
    band_id: str,
    *,
    minimum: str | None = None,
    maximum: str | None = None,
    score: str,
    grade: FundamentalScoreGrade,
    summary: str,
) -> FundamentalMetricScoreBand:
    return FundamentalMetricScoreBand(
        band_id=band_id,
        minimum_inclusive=(Decimal(minimum) if minimum is not None else None),
        maximum_exclusive=(Decimal(maximum) if maximum is not None else None),
        score=Decimal(score),
        grade=grade,
        summary=summary,
    )


_PROFIT_MARGIN_BANDS = (
    _band(
        "loss_making",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Median PAT margin is negative.",
    ),
    _band(
        "thin_margin",
        minimum="0",
        maximum="5",
        score="25",
        grade=FundamentalScoreGrade.WEAK,
        summary="Median PAT margin is positive but below 5%.",
    ),
    _band(
        "moderate_margin",
        minimum="5",
        maximum="10",
        score="50",
        grade=FundamentalScoreGrade.MIXED,
        summary="Median PAT margin is between 5% and 10%.",
    ),
    _band(
        "healthy_margin",
        minimum="10",
        maximum="20",
        score="75",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Median PAT margin is between 10% and 20%.",
    ),
    _band(
        "high_margin",
        minimum="20",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Median PAT margin is at least 20%.",
    ),
)

_EBITDA_MARGIN_BANDS = (
    _band(
        "negative_ebitda_margin",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Median EBITDA margin is negative.",
    ),
    _band(
        "thin_ebitda_margin",
        minimum="0",
        maximum="10",
        score="25",
        grade=FundamentalScoreGrade.WEAK,
        summary="Median EBITDA margin is below 10%.",
    ),
    _band(
        "moderate_ebitda_margin",
        minimum="10",
        maximum="15",
        score="55",
        grade=FundamentalScoreGrade.MIXED,
        summary="Median EBITDA margin is between 10% and 15%.",
    ),
    _band(
        "healthy_ebitda_margin",
        minimum="15",
        maximum="25",
        score="80",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Median EBITDA margin is between 15% and 25%.",
    ),
    _band(
        "high_ebitda_margin",
        minimum="25",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Median EBITDA margin is at least 25%.",
    ),
)

_CAPITAL_EFFICIENCY_BANDS = (
    _band(
        "negative_return",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Median ROCE or ROE is negative.",
    ),
    _band(
        "low_return",
        minimum="0",
        maximum="8",
        score="20",
        grade=FundamentalScoreGrade.WEAK,
        summary="Median ROCE or ROE is below 8%.",
    ),
    _band(
        "moderate_return",
        minimum="8",
        maximum="12",
        score="45",
        grade=FundamentalScoreGrade.MIXED,
        summary="Median ROCE or ROE is between 8% and 12%.",
    ),
    _band(
        "healthy_return",
        minimum="12",
        maximum="18",
        score="75",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Median ROCE or ROE is between 12% and 18%.",
    ),
    _band(
        "high_return",
        minimum="18",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Median ROCE or ROE is at least 18%.",
    ),
)

_DEBT_TO_EQUITY_BANDS = (
    _band(
        "negative_equity_or_ratio",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Debt-to-equity is negative and requires balance-sheet review.",
    ),
    _band(
        "low_leverage",
        minimum="0",
        maximum="0.5",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Debt-to-equity is below 0.5.",
    ),
    _band(
        "moderate_leverage",
        minimum="0.5",
        maximum="1",
        score="80",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Debt-to-equity is between 0.5 and 1.0.",
    ),
    _band(
        "elevated_leverage",
        minimum="1",
        maximum="2",
        score="55",
        grade=FundamentalScoreGrade.MIXED,
        summary="Debt-to-equity is between 1.0 and 2.0.",
    ),
    _band(
        "high_leverage",
        minimum="2",
        maximum="3",
        score="25",
        grade=FundamentalScoreGrade.WEAK,
        summary="Debt-to-equity is between 2.0 and 3.0.",
    ),
    _band(
        "very_high_leverage",
        minimum="3",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Debt-to-equity is at least 3.0.",
    ),
)

_INTEREST_COVERAGE_BANDS = (
    _band(
        "non_positive_coverage",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Interest coverage is non-positive.",
    ),
    _band(
        "critical_coverage",
        minimum="0",
        maximum="1.5",
        score="10",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Interest coverage is below 1.5 times.",
    ),
    _band(
        "weak_coverage",
        minimum="1.5",
        maximum="3",
        score="35",
        grade=FundamentalScoreGrade.WEAK,
        summary="Interest coverage is between 1.5 and 3 times.",
    ),
    _band(
        "adequate_coverage",
        minimum="3",
        maximum="6",
        score="60",
        grade=FundamentalScoreGrade.MIXED,
        summary="Interest coverage is between 3 and 6 times.",
    ),
    _band(
        "healthy_coverage",
        minimum="6",
        maximum="10",
        score="80",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Interest coverage is between 6 and 10 times.",
    ),
    _band(
        "high_coverage",
        minimum="10",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Interest coverage is at least 10 times.",
    ),
)

_GROWTH_BANDS = (
    _band(
        "contracting",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="The annual compound growth rate is negative.",
    ),
    _band(
        "low_growth",
        minimum="0",
        maximum="5",
        score="25",
        grade=FundamentalScoreGrade.WEAK,
        summary="The annual compound growth rate is below 5%.",
    ),
    _band(
        "moderate_growth",
        minimum="5",
        maximum="10",
        score="55",
        grade=FundamentalScoreGrade.MIXED,
        summary="The annual compound growth rate is between 5% and 10%.",
    ),
    _band(
        "healthy_growth",
        minimum="10",
        maximum="20",
        score="80",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="The annual compound growth rate is between 10% and 20%.",
    ),
    _band(
        "high_growth",
        minimum="20",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="The annual compound growth rate is at least 20%.",
    ),
)

_OCF_TO_PAT_BANDS = (
    _band(
        "negative_conversion",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Median operating cash flow to PAT is negative.",
    ),
    _band(
        "weak_conversion",
        minimum="0",
        maximum="0.5",
        score="20",
        grade=FundamentalScoreGrade.WEAK,
        summary="Median operating cash flow is below half of PAT.",
    ),
    _band(
        "partial_conversion",
        minimum="0.5",
        maximum="0.8",
        score="50",
        grade=FundamentalScoreGrade.MIXED,
        summary="Median operating cash flow is between 0.5 and 0.8 of PAT.",
    ),
    _band(
        "strong_conversion",
        minimum="0.8",
        maximum="1.2",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Median operating cash flow broadly covers PAT.",
    ),
    _band(
        "high_conversion",
        minimum="1.2",
        maximum="2",
        score="85",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Median operating cash flow materially exceeds PAT.",
    ),
    _band(
        "exceptional_conversion",
        minimum="2",
        score="55",
        grade=FundamentalScoreGrade.MIXED,
        summary="Very high cash conversion requires working-capital review.",
    ),
)

_FCF_TO_PAT_BANDS = (
    _band(
        "negative_free_cash_flow",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Median free cash flow to PAT is negative.",
    ),
    _band(
        "low_free_cash_flow",
        minimum="0",
        maximum="0.25",
        score="25",
        grade=FundamentalScoreGrade.WEAK,
        summary="Median free cash flow is below one quarter of PAT.",
    ),
    _band(
        "moderate_free_cash_flow",
        minimum="0.25",
        maximum="0.6",
        score="55",
        grade=FundamentalScoreGrade.MIXED,
        summary="Median free cash flow is between 0.25 and 0.6 of PAT.",
    ),
    _band(
        "healthy_free_cash_flow",
        minimum="0.6",
        maximum="1.2",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Median free cash flow is between 0.6 and 1.2 of PAT.",
    ),
    _band(
        "high_free_cash_flow",
        minimum="1.2",
        score="85",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Median free cash flow exceeds 1.2 times PAT.",
    ),
)

_FCF_MARGIN_BANDS = (
    _band(
        "negative_fcf_margin",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Median free-cash-flow margin is negative.",
    ),
    _band(
        "thin_fcf_margin",
        minimum="0",
        maximum="5",
        score="25",
        grade=FundamentalScoreGrade.WEAK,
        summary="Median free-cash-flow margin is below 5%.",
    ),
    _band(
        "moderate_fcf_margin",
        minimum="5",
        maximum="10",
        score="55",
        grade=FundamentalScoreGrade.MIXED,
        summary="Median free-cash-flow margin is between 5% and 10%.",
    ),
    _band(
        "healthy_fcf_margin",
        minimum="10",
        maximum="20",
        score="80",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Median free-cash-flow margin is between 10% and 20%.",
    ),
    _band(
        "high_fcf_margin",
        minimum="20",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Median free-cash-flow margin is at least 20%.",
    ),
)

_PE_BANDS = (
    _band(
        "loss_or_invalid_pe",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="P/E is non-positive and is not a valid value signal.",
    ),
    _band(
        "low_pe",
        minimum="0",
        maximum="12",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="P/E is below 12 times.",
    ),
    _band(
        "moderate_pe",
        minimum="12",
        maximum="20",
        score="80",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="P/E is between 12 and 20 times.",
    ),
    _band(
        "elevated_pe",
        minimum="20",
        maximum="30",
        score="55",
        grade=FundamentalScoreGrade.MIXED,
        summary="P/E is between 20 and 30 times.",
    ),
    _band(
        "high_pe",
        minimum="30",
        maximum="50",
        score="30",
        grade=FundamentalScoreGrade.WEAK,
        summary="P/E is between 30 and 50 times.",
    ),
    _band(
        "very_high_pe",
        minimum="50",
        score="10",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="P/E is at least 50 times.",
    ),
)

_REINVESTMENT_RATE_BANDS = (
    _band(
        "invalid_reinvestment_rate",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Capex to operating cash flow is non-positive.",
    ),
    _band(
        "low_reinvestment",
        minimum="0",
        maximum="10",
        score="25",
        grade=FundamentalScoreGrade.WEAK,
        summary="Capex uses less than 10% of operating cash flow.",
    ),
    _band(
        "moderate_reinvestment",
        minimum="10",
        maximum="25",
        score="55",
        grade=FundamentalScoreGrade.MIXED,
        summary="Capex uses between 10% and 25% of operating cash flow.",
    ),
    _band(
        "balanced_reinvestment",
        minimum="25",
        maximum="60",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Capex uses between 25% and 60% of operating cash flow.",
    ),
    _band(
        "high_reinvestment",
        minimum="60",
        maximum="100",
        score="75",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Capex uses between 60% and 100% of operating cash flow.",
    ),
    _band(
        "cash_flow_exceeding_reinvestment",
        minimum="100",
        score="40",
        grade=FundamentalScoreGrade.MIXED,
        summary=(
            "Capex exceeds operating cash flow and requires funding-quality "
            "review."
        ),
    ),
)

_DILUTION_RATE_BANDS = (
    _band(
        "share_count_contraction",
        maximum="0",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="The annual share-count growth rate is negative.",
    ),
    _band(
        "stable_share_count",
        minimum="0",
        maximum="1",
        score="90",
        grade=FundamentalScoreGrade.STRONG,
        summary="The annual share-count growth rate is below 1%.",
    ),
    _band(
        "limited_dilution",
        minimum="1",
        maximum="3",
        score="75",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="The annual share-count growth rate is between 1% and 3%.",
    ),
    _band(
        "moderate_dilution",
        minimum="3",
        maximum="5",
        score="50",
        grade=FundamentalScoreGrade.MIXED,
        summary="The annual share-count growth rate is between 3% and 5%.",
    ),
    _band(
        "high_dilution",
        minimum="5",
        maximum="10",
        score="25",
        grade=FundamentalScoreGrade.WEAK,
        summary="The annual share-count growth rate is between 5% and 10%.",
    ),
    _band(
        "very_high_dilution",
        minimum="10",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="The annual share-count growth rate is at least 10%.",
    ),
)

_CAN_SLIM_QUARTERLY_EPS_GROWTH_BANDS = (
    _band(
        "contracting_quarterly_eps",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Quarterly EPS declined from the comparable prior-year quarter.",
    ),
    _band(
        "low_quarterly_eps_growth",
        minimum="0",
        maximum="10",
        score="20",
        grade=FundamentalScoreGrade.WEAK,
        summary="Quarterly EPS year-over-year growth is below 10%.",
    ),
    _band(
        "moderate_quarterly_eps_growth",
        minimum="10",
        maximum="20",
        score="45",
        grade=FundamentalScoreGrade.MIXED,
        summary="Quarterly EPS year-over-year growth is between 10% and 20%.",
    ),
    _band(
        "healthy_quarterly_eps_growth",
        minimum="20",
        maximum="25",
        score="75",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Quarterly EPS year-over-year growth is between 20% and 25%.",
    ),
    _band(
        "high_quarterly_eps_growth",
        minimum="25",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Quarterly EPS year-over-year growth is at least 25%.",
    ),
)

_CAN_SLIM_QUARTERLY_REVENUE_GROWTH_BANDS = (
    _band(
        "contracting_quarterly_revenue",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary=(
            "Quarterly revenue declined from the comparable prior-year "
            "quarter."
        ),
    ),
    _band(
        "low_quarterly_revenue_growth",
        minimum="0",
        maximum="5",
        score="20",
        grade=FundamentalScoreGrade.WEAK,
        summary="Quarterly revenue year-over-year growth is below 5%.",
    ),
    _band(
        "moderate_quarterly_revenue_growth",
        minimum="5",
        maximum="10",
        score="45",
        grade=FundamentalScoreGrade.MIXED,
        summary=(
            "Quarterly revenue year-over-year growth is between 5% and 10%."
        ),
    ),
    _band(
        "healthy_quarterly_revenue_growth",
        minimum="10",
        maximum="20",
        score="75",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary=(
            "Quarterly revenue year-over-year growth is between 10% and 20%."
        ),
    ),
    _band(
        "high_quarterly_revenue_growth",
        minimum="20",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Quarterly revenue year-over-year growth is at least 20%.",
    ),
)

_INSTITUTIONAL_SPONSORSHIP_BANDS = (
    _band(
        "declining_sponsorship",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Qualified institutional ownership declined.",
    ),
    _band(
        "stable_sponsorship",
        minimum="0",
        maximum="1",
        score="50",
        grade=FundamentalScoreGrade.MIXED,
        summary="Qualified institutional ownership was broadly stable.",
    ),
    _band(
        "increasing_sponsorship",
        minimum="1",
        maximum="3",
        score="80",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Qualified institutional ownership increased moderately.",
    ),
    _band(
        "strong_sponsorship_increase",
        minimum="3",
        maximum="10",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Qualified institutional ownership increased materially.",
    ),
    _band(
        "exceptional_sponsorship_change",
        minimum="10",
        score="55",
        grade=FundamentalScoreGrade.MIXED,
        summary=(
            "An exceptional institutional ownership change requires review "
            "for issuance, acquisition, or block-transaction effects."
        ),
    ),
)

_RELATIVE_STRENGTH_PERCENTILE_BANDS = (
    _band(
        "lagging",
        maximum="40",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Relative price strength is below the 40th percentile.",
    ),
    _band(
        "below_average",
        minimum="40",
        maximum="60",
        score="25",
        grade=FundamentalScoreGrade.WEAK,
        summary="Relative price strength is between the 40th and 60th percentile.",
    ),
    _band(
        "above_average",
        minimum="60",
        maximum="80",
        score="55",
        grade=FundamentalScoreGrade.MIXED,
        summary="Relative price strength is between the 60th and 80th percentile.",
    ),
    _band(
        "leading",
        minimum="80",
        maximum="90",
        score="80",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Relative price strength is between the 80th and 90th percentile.",
    ),
    _band(
        "market_leader",
        minimum="90",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Relative price strength is at or above the 90th percentile.",
    ),
)

_BINARY_CONFIRMATION_BANDS = (
    _band(
        "invalid_negative_flag",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="The confirmation flag is below its valid range.",
    ),
    _band(
        "not_confirmed",
        minimum="0",
        maximum="1",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="The independently calculated condition is not confirmed.",
    ),
    _band(
        "confirmed",
        minimum="1",
        maximum="2",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="The independently calculated condition is confirmed.",
    ),
    _band(
        "invalid_high_flag",
        minimum="2",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="The confirmation flag is above its valid range.",
    ),
)

_PEER_VALUATION_PERCENTILE_BANDS = (
    _band(
        "invalid_peer_percentile",
        maximum="0",
        score="0",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="The peer valuation percentile is outside its valid range.",
    ),
    _band(
        "least_expensive_quintile",
        minimum="0",
        maximum="20",
        score="100",
        grade=FundamentalScoreGrade.STRONG,
        summary="Valuation is in the least-expensive peer quintile.",
    ),
    _band(
        "below_peer_median",
        minimum="20",
        maximum="50",
        score="80",
        grade=FundamentalScoreGrade.FAVOURABLE,
        summary="Valuation is below the peer median.",
    ),
    _band(
        "near_peer_median",
        minimum="50",
        maximum="70",
        score="55",
        grade=FundamentalScoreGrade.MIXED,
        summary="Valuation is near or moderately above the peer median.",
    ),
    _band(
        "expensive_vs_peers",
        minimum="70",
        maximum="90",
        score="30",
        grade=FundamentalScoreGrade.WEAK,
        summary="Valuation is expensive relative to most peers.",
    ),
    _band(
        "most_expensive_decile",
        minimum="90",
        score="10",
        grade=FundamentalScoreGrade.ADVERSE,
        summary="Valuation is in the most-expensive peer decile.",
    ),
)


def _rule(
    rule_id: str,
    metric_ids: tuple[str, ...],
    *,
    unit: FundamentalMetricUnit,
    observations: int,
    aggregation: FundamentalMetricAggregation,
    weight_bps: int,
    bands: tuple[FundamentalMetricScoreBand, ...],
    consecutive: bool,
    period_kind: FundamentalMetricPeriodKind = (
        FundamentalMetricPeriodKind.ANNUAL
    ),
    reporting_bases: tuple[FinancialReportingBasis, ...] = (
        _ALL_ACCOUNTING_BASES
    ),
) -> FundamentalMetricScoringRule:
    return FundamentalMetricScoringRule(
        rule_id=rule_id,
        rule_version=INITIAL_FRAMEWORK_SCORE_POLICY_VERSION,
        metric_ids=metric_ids,
        expected_unit=unit,
        allowed_reporting_bases=reporting_bases,
        period_kind=period_kind,
        minimum_observations=observations,
        require_consecutive_periods=consecutive,
        aggregation=aggregation,
        component_weight_bps=weight_bps,
        bands=bands,
    )


def _leverage_family(
    framework: FundamentalFramework,
    *,
    framework_weight_bps: int,
) -> FundamentalEvidenceFamilyScoringPolicy:
    prefix = framework.value
    return FundamentalEvidenceFamilyScoringPolicy(
        family=FundamentalFrameworkEvidenceFamily.BALANCE_SHEET,
        framework_weight_bps=framework_weight_bps,
        minimum_rule_coverage_bps=10_000,
        maximum_family_score=Decimal("100"),
        metric_rules=(
            _rule(
                f"{prefix}.balance_sheet.debt_to_equity",
                (
                    "derived.leverage.debt_to_equity",
                    "ratio.debt_to_equity",
                ),
                unit=_RATIO_UNIT,
                observations=1,
                aggregation=FundamentalMetricAggregation.LATEST,
                weight_bps=6_000,
                bands=_DEBT_TO_EQUITY_BANDS,
                consecutive=False,
            ),
            _rule(
                f"{prefix}.balance_sheet.interest_coverage",
                ("ratio.interest_coverage",),
                unit=_RATIO_UNIT,
                observations=1,
                aggregation=FundamentalMetricAggregation.LATEST,
                weight_bps=4_000,
                bands=_INTEREST_COVERAGE_BANDS,
                consecutive=False,
            ),
        ),
    )


BUFFETT_SCORING_POLICY_V1 = FundamentalFrameworkScoringPolicy(
    framework=FundamentalFramework.BUFFETT,
    policy_id="jarvis.framework_score.buffett",
    policy_version=INITIAL_FRAMEWORK_SCORE_POLICY_VERSION,
    minimum_family_coverage_bps=8_000,
    family_policies=(
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.PROFITABILITY,
            framework_weight_bps=2_000,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "buffett.profitability.pat_margin",
                    ("derived.margin.pat_pct",),
                    unit=_PERCENT_UNIT,
                    observations=5,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=10_000,
                    bands=_PROFIT_MARGIN_BANDS,
                    consecutive=True,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.CAPITAL_EFFICIENCY,
            framework_weight_bps=2_500,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "buffett.capital_efficiency.return_on_capital",
                    ("ratio.roce", "ratio.roe"),
                    unit=_PERCENT_UNIT,
                    observations=5,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=10_000,
                    bands=_CAPITAL_EFFICIENCY_BANDS,
                    consecutive=True,
                ),
            ),
        ),
        _leverage_family(
            FundamentalFramework.BUFFETT,
            framework_weight_bps=2_000,
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.CASH_CONVERSION,
            framework_weight_bps=2_000,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "buffett.cash_conversion.fcf_to_pat",
                    ("derived.cash_conversion.fcf_to_pat",),
                    unit=_RATIO_UNIT,
                    observations=3,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=4_000,
                    bands=_FCF_TO_PAT_BANDS,
                    consecutive=True,
                ),
                _rule(
                    "buffett.cash_conversion.ocf_to_pat",
                    ("derived.cash_conversion.ocf_to_pat",),
                    unit=_RATIO_UNIT,
                    observations=5,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=6_000,
                    bands=_OCF_TO_PAT_BANDS,
                    consecutive=True,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.REINVESTMENT,
            framework_weight_bps=1_500,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "buffett.reinvestment.fcf_margin",
                    ("derived.cash_conversion.fcf_margin_pct",),
                    unit=_PERCENT_UNIT,
                    observations=3,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=5_000,
                    bands=_FCF_MARGIN_BANDS,
                    consecutive=True,
                ),
                _rule(
                    "buffett.reinvestment.pat_cagr",
                    ("derived.growth.pat_cagr_pct",),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=5_000,
                    bands=_GROWTH_BANDS,
                    consecutive=False,
                ),
            ),
        ),
    ),
    limitations=(
        "This initial quantitative Buffett lens runs in shadow mode only.",
        "The policy cannot infer durable moat, pricing power, management "
        "integrity, or capital-allocation intent without approved primary "
        "qualitative evidence.",
        "Reinvestment is represented by cited growth and free-cash-flow "
        "outcomes, not by a claim about management intent.",
    ),
)


LYNCH_SCORING_POLICY_V1 = FundamentalFrameworkScoringPolicy(
    framework=FundamentalFramework.LYNCH,
    policy_id="jarvis.framework_score.lynch",
    policy_version=INITIAL_FRAMEWORK_SCORE_POLICY_VERSION,
    minimum_family_coverage_bps=10_000,
    family_policies=(
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.PROFITABILITY,
            framework_weight_bps=2_000,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "lynch.profitability.pat_margin",
                    ("derived.margin.pat_pct",),
                    unit=_PERCENT_UNIT,
                    observations=3,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=10_000,
                    bands=_PROFIT_MARGIN_BANDS,
                    consecutive=True,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.GROWTH,
            framework_weight_bps=3_500,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "lynch.growth.eps_cagr",
                    (
                        "derived.growth.adjusted_eps_cagr_pct",
                        "derived.growth.eps_cagr_pct",
                    ),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=6_000,
                    bands=_GROWTH_BANDS,
                    consecutive=False,
                ),
                _rule(
                    "lynch.growth.revenue_cagr",
                    ("derived.growth.revenue_cagr_pct",),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=4_000,
                    bands=_GROWTH_BANDS,
                    consecutive=False,
                ),
            ),
        ),
        _leverage_family(
            FundamentalFramework.LYNCH,
            framework_weight_bps=1_500,
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.VALUATION,
            framework_weight_bps=3_000,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "lynch.valuation.pe",
                    ("valuation.pe",),
                    unit=_RATIO_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=10_000,
                    bands=_PE_BANDS,
                    consecutive=False,
                ),
            ),
        ),
    ),
    limitations=(
        "This initial quantitative Lynch lens runs in shadow mode only.",
        "The policy does not classify the issuer as slow grower, stalwart, "
        "fast grower, cyclical, turnaround, or asset play.",
        "The current valuation rule uses absolute P/E and is not a PEG, "
        "sector-relative, or lifecycle-adjusted valuation conclusion.",
    ),
)


JHUNJHUNWALA_STYLE_SCORING_POLICY_V1 = FundamentalFrameworkScoringPolicy(
    framework=FundamentalFramework.JHUNJHUNWALA_STYLE,
    policy_id="jarvis.framework_score.jhunjhunwala_style",
    policy_version=INITIAL_FRAMEWORK_SCORE_POLICY_VERSION,
    minimum_family_coverage_bps=10_000,
    family_policies=(
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.PROFITABILITY,
            framework_weight_bps=2_000,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "jhunjhunwala_style.profitability.ebitda_margin",
                    ("derived.margin.ebitda_pct",),
                    unit=_PERCENT_UNIT,
                    observations=3,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=6_000,
                    bands=_EBITDA_MARGIN_BANDS,
                    consecutive=True,
                ),
                _rule(
                    "jhunjhunwala_style.profitability.pat_margin",
                    ("derived.margin.pat_pct",),
                    unit=_PERCENT_UNIT,
                    observations=3,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=4_000,
                    bands=_PROFIT_MARGIN_BANDS,
                    consecutive=True,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.GROWTH,
            framework_weight_bps=3_000,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "jhunjhunwala_style.growth.ebitda_cagr",
                    ("derived.growth.ebitda_cagr_pct",),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=4_000,
                    bands=_GROWTH_BANDS,
                    consecutive=False,
                ),
                _rule(
                    "jhunjhunwala_style.growth.pat_cagr",
                    ("derived.growth.pat_cagr_pct",),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=3_000,
                    bands=_GROWTH_BANDS,
                    consecutive=False,
                ),
                _rule(
                    "jhunjhunwala_style.growth.revenue_cagr",
                    ("derived.growth.revenue_cagr_pct",),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=3_000,
                    bands=_GROWTH_BANDS,
                    consecutive=False,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.CAPITAL_EFFICIENCY,
            framework_weight_bps=2_500,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "jhunjhunwala_style.capital_efficiency.return_on_capital",
                    ("ratio.roce", "ratio.roe"),
                    unit=_PERCENT_UNIT,
                    observations=3,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=10_000,
                    bands=_CAPITAL_EFFICIENCY_BANDS,
                    consecutive=True,
                ),
            ),
        ),
        _leverage_family(
            FundamentalFramework.JHUNJHUNWALA_STYLE,
            framework_weight_bps=1_500,
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.VALUATION,
            framework_weight_bps=1_000,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "jhunjhunwala_style.valuation.pe",
                    ("valuation.pe",),
                    unit=_RATIO_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=10_000,
                    bands=_PE_BANDS,
                    consecutive=False,
                ),
            ),
        ),
    ),
    limitations=(
        "This initial quantitative Jhunjhunwala-style lens runs in shadow "
        "mode only.",
        "This is a documented public-principles lens, not impersonation, "
        "endorsement, or a claim about private investment judgment.",
        "Promoter quality, management capability, industry insight, and "
        "future opportunity size cannot be inferred from financial tables.",
        "The valuation rule uses absolute P/E and is not sector-relative or "
        "growth-adjusted.",
    ),
)


ONE_HUNDRED_BAGGER_SCORING_POLICY_V1 = FundamentalFrameworkScoringPolicy(
    framework=FundamentalFramework.ONE_HUNDRED_BAGGER,
    policy_id="jarvis.framework_score.one_hundred_bagger",
    policy_version=INITIAL_FRAMEWORK_SCORE_POLICY_VERSION,
    minimum_family_coverage_bps=10_000,
    family_policies=(
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.GROWTH,
            framework_weight_bps=3_000,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "one_hundred_bagger.growth.pat_cagr",
                    ("derived.growth.pat_cagr_pct",),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=6_000,
                    bands=_GROWTH_BANDS,
                    consecutive=False,
                ),
                _rule(
                    "one_hundred_bagger.growth.revenue_cagr",
                    ("derived.growth.revenue_cagr_pct",),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=4_000,
                    bands=_GROWTH_BANDS,
                    consecutive=False,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.CAPITAL_EFFICIENCY,
            framework_weight_bps=2_000,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "one_hundred_bagger.capital_efficiency.return_on_capital",
                    ("ratio.roce", "ratio.roe"),
                    unit=_PERCENT_UNIT,
                    observations=5,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=10_000,
                    bands=_CAPITAL_EFFICIENCY_BANDS,
                    consecutive=True,
                ),
            ),
        ),
        _leverage_family(
            FundamentalFramework.ONE_HUNDRED_BAGGER,
            framework_weight_bps=1_000,
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.CASH_CONVERSION,
            framework_weight_bps=1_500,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "one_hundred_bagger.cash_conversion.fcf_to_pat",
                    ("derived.cash_conversion.fcf_to_pat",),
                    unit=_RATIO_UNIT,
                    observations=3,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=4_000,
                    bands=_FCF_TO_PAT_BANDS,
                    consecutive=True,
                ),
                _rule(
                    "one_hundred_bagger.cash_conversion.ocf_to_pat",
                    ("derived.cash_conversion.ocf_to_pat",),
                    unit=_RATIO_UNIT,
                    observations=5,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=6_000,
                    bands=_OCF_TO_PAT_BANDS,
                    consecutive=True,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.REINVESTMENT,
            framework_weight_bps=1_500,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "one_hundred_bagger.reinvestment.capex_to_ocf",
                    ("derived.reinvestment.capex_to_ocf_pct",),
                    unit=_PERCENT_UNIT,
                    observations=5,
                    aggregation=FundamentalMetricAggregation.MEDIAN,
                    weight_bps=10_000,
                    bands=_REINVESTMENT_RATE_BANDS,
                    consecutive=True,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.DILUTION,
            framework_weight_bps=1_000,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "one_hundred_bagger.dilution.share_count_cagr",
                    ("derived.dilution.share_count_cagr_pct",),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=10_000,
                    bands=_DILUTION_RATE_BANDS,
                    consecutive=False,
                ),
            ),
        ),
    ),
    limitations=(
        "This initial quantitative 100-Bagger lens runs in shadow mode only.",
        "Addressable runway, durable moat, management quality, and future "
        "scalability remain unscored without approved primary evidence.",
        "The policy requires scale-normalized reinvestment and dilution "
        "metrics; it withholds the framework score when either is absent.",
        "A released score describes historical financial evidence and is not "
        "a forecast of a one-hundred-fold return.",
    ),
)


CAN_SLIM_SCORING_POLICY_V1 = FundamentalFrameworkScoringPolicy(
    framework=FundamentalFramework.CAN_SLIM,
    policy_id="jarvis.framework_score.can_slim",
    policy_version=INITIAL_FRAMEWORK_SCORE_POLICY_VERSION,
    minimum_family_coverage_bps=10_000,
    family_policies=(
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.GROWTH,
            framework_weight_bps=4_000,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "can_slim.growth.annual_eps_cagr",
                    (
                        "derived.growth.adjusted_eps_cagr_pct",
                        "derived.growth.eps_cagr_pct",
                    ),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=3_000,
                    bands=_GROWTH_BANDS,
                    consecutive=False,
                ),
                _rule(
                    "can_slim.growth.annual_revenue_cagr",
                    ("derived.growth.revenue_cagr_pct",),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=2_000,
                    bands=_GROWTH_BANDS,
                    consecutive=False,
                ),
                _rule(
                    "can_slim.growth.quarterly_eps_yoy",
                    (
                        "derived.growth.quarterly_adjusted_eps_yoy_pct",
                        "derived.growth.quarterly_eps_yoy_pct",
                    ),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=3_000,
                    bands=_CAN_SLIM_QUARTERLY_EPS_GROWTH_BANDS,
                    consecutive=False,
                    period_kind=FundamentalMetricPeriodKind.QUARTERLY,
                ),
                _rule(
                    "can_slim.growth.quarterly_revenue_yoy",
                    ("derived.growth.quarterly_revenue_yoy_pct",),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=2_000,
                    bands=_CAN_SLIM_QUARTERLY_REVENUE_GROWTH_BANDS,
                    consecutive=False,
                    period_kind=FundamentalMetricPeriodKind.QUARTERLY,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=(
                FundamentalFrameworkEvidenceFamily.INSTITUTIONAL_SPONSORSHIP
            ),
            framework_weight_bps=1_500,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "can_slim.institutional_sponsorship.ownership_change",
                    (
                        "derived.institutional_sponsorship."
                        "qualified_ownership_change_pct",
                    ),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=10_000,
                    bands=_INSTITUTIONAL_SPONSORSHIP_BANDS,
                    consecutive=False,
                    period_kind=FundamentalMetricPeriodKind.QUARTERLY,
                    reporting_bases=_NON_ACCOUNTING_BASIS,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.RELATIVE_LEADERSHIP,
            framework_weight_bps=1_500,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "can_slim.relative_leadership.price_strength_percentile",
                    ("derived.relative_leadership.price_strength_percentile_pct",),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=10_000,
                    bands=_RELATIVE_STRENGTH_PERCENTILE_BANDS,
                    consecutive=False,
                    period_kind=FundamentalMetricPeriodKind.POINT_IN_TIME,
                    reporting_bases=_NON_ACCOUNTING_BASIS,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.MARKET_REGIME,
            framework_weight_bps=1_500,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "can_slim.market_regime.supportive_regime",
                    ("derived.market_regime.can_slim_supportive_flag",),
                    unit=_RATIO_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=10_000,
                    bands=_BINARY_CONFIRMATION_BANDS,
                    consecutive=False,
                    period_kind=FundamentalMetricPeriodKind.POINT_IN_TIME,
                    reporting_bases=_NON_ACCOUNTING_BASIS,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.CATALYST,
            framework_weight_bps=1_500,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "can_slim.catalyst.approved_positive_material_event",
                    (
                        "derived.catalyst."
                        "approved_positive_material_event_flag",
                    ),
                    unit=_RATIO_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=10_000,
                    bands=_BINARY_CONFIRMATION_BANDS,
                    consecutive=False,
                    period_kind=FundamentalMetricPeriodKind.POINT_IN_TIME,
                    reporting_bases=_NON_ACCOUNTING_BASIS,
                ),
            ),
        ),
    ),
    limitations=(
        "This initial quantitative CAN SLIM lens runs in shadow mode only.",
        "Current-quarter growth requires a true same-quarter year-over-year "
        "calculation; adjacent-quarter growth is never substituted.",
        "Institutional sponsorship, relative price leadership, market regime, "
        "and a positive material catalyst require separately approved, "
        "point-in-time-safe normalized evidence.",
        "Supply-and-demand evidence is not yet represented by this scoring "
        "policy, so a released result cannot claim full CAN SLIM coverage.",
    ),
)


VALUATION_SCORING_POLICY_V1 = FundamentalFrameworkScoringPolicy(
    framework=FundamentalFramework.VALUATION,
    policy_id="jarvis.framework_score.valuation",
    policy_version=INITIAL_FRAMEWORK_SCORE_POLICY_VERSION,
    minimum_family_coverage_bps=10_000,
    family_policies=(
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.VALUATION,
            framework_weight_bps=7_500,
            minimum_rule_coverage_bps=5_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "valuation.valuation.ev_to_ebitda",
                    ("valuation.ev_to_ebitda",),
                    unit=_RATIO_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=5_000,
                    bands=(
                        _band(
                            "invalid_ev_to_ebitda",
                            maximum="0",
                            score="0",
                            grade=FundamentalScoreGrade.ADVERSE,
                            summary=(
                                "EV/EBITDA is non-positive and requires "
                                "issuer-specific review."
                            ),
                        ),
                        _band(
                            "low_ev_to_ebitda",
                            minimum="0",
                            maximum="8",
                            score="100",
                            grade=FundamentalScoreGrade.STRONG,
                            summary="EV/EBITDA is below 8 times.",
                        ),
                        _band(
                            "moderate_ev_to_ebitda",
                            minimum="8",
                            maximum="12",
                            score="80",
                            grade=FundamentalScoreGrade.FAVOURABLE,
                            summary="EV/EBITDA is between 8 and 12 times.",
                        ),
                        _band(
                            "elevated_ev_to_ebitda",
                            minimum="12",
                            maximum="18",
                            score="55",
                            grade=FundamentalScoreGrade.MIXED,
                            summary="EV/EBITDA is between 12 and 18 times.",
                        ),
                        _band(
                            "high_ev_to_ebitda",
                            minimum="18",
                            maximum="25",
                            score="30",
                            grade=FundamentalScoreGrade.WEAK,
                            summary="EV/EBITDA is between 18 and 25 times.",
                        ),
                        _band(
                            "very_high_ev_to_ebitda",
                            minimum="25",
                            score="10",
                            grade=FundamentalScoreGrade.ADVERSE,
                            summary="EV/EBITDA is at least 25 times.",
                        ),
                    ),
                    consecutive=False,
                ),
                _rule(
                    "valuation.valuation.pe",
                    ("valuation.pe",),
                    unit=_RATIO_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=5_000,
                    bands=_PE_BANDS,
                    consecutive=False,
                ),
            ),
        ),
        FundamentalEvidenceFamilyScoringPolicy(
            family=FundamentalFrameworkEvidenceFamily.RELATIVE_LEADERSHIP,
            framework_weight_bps=2_500,
            minimum_rule_coverage_bps=10_000,
            maximum_family_score=Decimal("100"),
            metric_rules=(
                _rule(
                    "valuation.relative_leadership.peer_valuation_percentile",
                    ("derived.valuation.peer_composite_percentile_pct",),
                    unit=_PERCENT_UNIT,
                    observations=1,
                    aggregation=FundamentalMetricAggregation.LATEST,
                    weight_bps=10_000,
                    bands=_PEER_VALUATION_PERCENTILE_BANDS,
                    consecutive=False,
                    period_kind=FundamentalMetricPeriodKind.POINT_IN_TIME,
                    reporting_bases=_NON_ACCOUNTING_BASIS,
                ),
            ),
        ),
    ),
    limitations=(
        "This initial quantitative Valuation lens runs in shadow mode only.",
        "Absolute P/E and EV/EBITDA bands are generic screening thresholds, "
        "not intrinsic-value estimates or sector-specific conclusions.",
        "P/B is deliberately not scored by this generic policy because its "
        "economic meaning is sector and balance-sheet dependent.",
        "Peer-relative valuation requires a separately normalized, "
        "point-in-time-safe comparison universe.",
        "No reverse DCF is used; intrinsic valuation remains unavailable "
        "without explicit approved assumptions and inputs.",
    ),
)


def initial_buffett_lynch_score_policies(
) -> tuple[
    FundamentalFrameworkScoringPolicy,
    FundamentalFrameworkScoringPolicy,
]:
    """Return the immutable initial policies in canonical framework order."""

    return (BUFFETT_SCORING_POLICY_V1, LYNCH_SCORING_POLICY_V1)


def initial_framework_score_policies(
) -> tuple[FundamentalFrameworkScoringPolicy, ...]:
    """Return all implemented shadow policies in canonical framework order."""

    return (
        BUFFETT_SCORING_POLICY_V1,
        LYNCH_SCORING_POLICY_V1,
        JHUNJHUNWALA_STYLE_SCORING_POLICY_V1,
        ONE_HUNDRED_BAGGER_SCORING_POLICY_V1,
        CAN_SLIM_SCORING_POLICY_V1,
        VALUATION_SCORING_POLICY_V1,
    )


def _mandatory_constraint(
    constraint_id: str,
    *,
    domain: FundamentalAssessmentDomain,
    trigger_status: FundamentalFindingStatus,
    minimum_severity: FundamentalFindingSeverity,
    stance_ceiling: FundamentalScorecardStance,
    summary: str,
) -> FundamentalMandatoryScoreConstraint:
    return FundamentalMandatoryScoreConstraint(
        constraint_id=constraint_id,
        domain=domain,
        trigger_status=trigger_status,
        minimum_severity=minimum_severity,
        stance_ceiling=stance_ceiling,
        summary=summary,
    )


INITIAL_FUNDAMENTAL_SCORE_POLICY_BUNDLE_V1 = FundamentalScorePolicyBundle(
    policy_bundle_id="jarvis.fundamental_scorecard",
    policy_bundle_version=INITIAL_FRAMEWORK_SCORE_POLICY_VERSION,
    decision_influence_mode=FundamentalDecisionInfluenceMode.SHADOW,
    grade_thresholds=FundamentalScoreGradeThresholds(
        weak_minimum=Decimal("20"),
        mixed_minimum=Decimal("40"),
        favourable_minimum=Decimal("60"),
        strong_minimum=Decimal("80"),
    ),
    stance_policy=FundamentalStancePolicy(
        minimum_scored_frameworks=2,
        healthy_minimum_frameworks=2,
        healthy_minimum_score=Decimal("65"),
        caution_minimum_score=Decimal("35"),
    ),
    framework_policies=initial_framework_score_policies(),
    mandatory_constraints=(
        _mandatory_constraint(
            "earnings_quality.fail",
            domain=FundamentalAssessmentDomain.EARNINGS_QUALITY,
            trigger_status=FundamentalFindingStatus.FAIL,
            minimum_severity=FundamentalFindingSeverity.MEDIUM,
            stance_ceiling=FundamentalScorecardStance.WEAK,
            summary=(
                "Any valid earnings-quality failure prevents a stance above "
                "weak."
            ),
        ),
        _mandatory_constraint(
            "earnings_quality.warn",
            domain=FundamentalAssessmentDomain.EARNINGS_QUALITY,
            trigger_status=FundamentalFindingStatus.WARN,
            minimum_severity=FundamentalFindingSeverity.LOW,
            stance_ceiling=FundamentalScorecardStance.CAUTION,
            summary=(
                "Any earnings-quality warning prevents a healthy stance."
            ),
        ),
        _mandatory_constraint(
            "financial_health.fail",
            domain=FundamentalAssessmentDomain.FINANCIAL_HEALTH,
            trigger_status=FundamentalFindingStatus.FAIL,
            minimum_severity=FundamentalFindingSeverity.MEDIUM,
            stance_ceiling=FundamentalScorecardStance.WEAK,
            summary=(
                "Any valid financial-health failure prevents a stance above "
                "weak."
            ),
        ),
        _mandatory_constraint(
            "financial_health.warn",
            domain=FundamentalAssessmentDomain.FINANCIAL_HEALTH,
            trigger_status=FundamentalFindingStatus.WARN,
            minimum_severity=FundamentalFindingSeverity.LOW,
            stance_ceiling=FundamentalScorecardStance.CAUTION,
            summary="Any financial-health warning prevents a healthy stance.",
        ),
        _mandatory_constraint(
            "forensic_risk.fail",
            domain=FundamentalAssessmentDomain.FORENSIC_RISK,
            trigger_status=FundamentalFindingStatus.FAIL,
            minimum_severity=FundamentalFindingSeverity.MEDIUM,
            stance_ceiling=FundamentalScorecardStance.WEAK,
            summary=(
                "Any valid forensic-risk failure prevents a stance above weak."
            ),
        ),
        _mandatory_constraint(
            "forensic_risk.warn",
            domain=FundamentalAssessmentDomain.FORENSIC_RISK,
            trigger_status=FundamentalFindingStatus.WARN,
            minimum_severity=FundamentalFindingSeverity.LOW,
            stance_ceiling=FundamentalScorecardStance.CAUTION,
            summary="Any forensic-risk warning prevents a healthy stance.",
        ),
    ),
    limitations=(
        "The complete score-policy bundle runs in shadow mode and cannot "
        "alter a trade decision.",
        "Framework scores remain separate analytical lenses and are not "
        "averaged into a synthetic conviction score.",
        "Mandatory warnings and failures impose non-offsettable stance "
        "ceilings after framework scoring.",
        "Incomplete mandatory-check coverage or fewer than two released "
        "selected-framework scores produces insufficient evidence.",
        "Provider-standardized fundamentals remain secondary research "
        "evidence under the current source policy.",
    ),
)
