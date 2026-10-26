import unittest
from datetime import date, timedelta
from decimal import Decimal

from app.analytics.fundamental_checks import (
    EARNINGS_QUALITY_POLICY_VERSION,
    EARNINGS_QUALITY_RULE_IDS,
    FINANCIAL_HEALTH_POLICY_VERSION,
    FINANCIAL_HEALTH_RULE_IDS,
    FORENSIC_RISK_POLICY_VERSION,
    FORENSIC_RISK_RULE_IDS,
    FinancialHealthSectorProfile,
    assess_earnings_quality,
    assess_financial_health,
    assess_forensic_risk,
)
from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_assessment import (
    FundamentalAssessmentCoverageStatus,
    FundamentalAssessmentStance,
    FundamentalFindingNAReason,
    FundamentalFindingSeverity,
    FundamentalFindingStatus,
    FundamentalReviewState,
)
from app.models.fundamental_metrics import (
    FundamentalMetricAvailability,
    FundamentalMetricPeriodKind,
    FundamentalMetricQualityFlag,
    FundamentalMetricUnit,
    FundamentalMetricValueStatus,
)
from app.models.fundamentals import FundamentalValueKind
from tests.unit.test_fundamental_metrics import (
    NORMALIZED_AT,
    evidence,
    normalized_input,
    ratio_unit,
)


CHECKED_AT = NORMALIZED_AT + timedelta(seconds=1)


def percent_unit() -> FundamentalMetricUnit:
    return FundamentalMetricUnit(
        value_kind=FundamentalValueKind.PERCENTAGE,
        normalized_unit="PERCENT",
        scale=Decimal("1"),
    )


def count_unit() -> FundamentalMetricUnit:
    return FundamentalMetricUnit(
        value_kind=FundamentalValueKind.COUNT,
        normalized_unit="COUNT",
        scale=Decimal("1"),
    )


def annual_metric(
    metric_id: str,
    value: Decimal,
    *,
    year: int = 2026,
    basis: FinancialReportingBasis = FinancialReportingBasis.CONSOLIDATED,
    unit: FundamentalMetricUnit | None = None,
    source_index: int = 4,
):
    return normalized_input(
        metric_id,
        value,
        period_key=f"annual:fy{year}",
        period_end=date(year, 3, 31),
        reporting_basis=basis,
        metric_unit=unit,
        source_index=source_index,
    )


def quarterly_metric(
    metric_id: str,
    value: Decimal,
    *,
    period_key: str = "quarterly:q1fy2027",
    period_end: date = date(2026, 6, 30),
    basis: FinancialReportingBasis = FinancialReportingBasis.CONSOLIDATED,
    unit: FundamentalMetricUnit | None = None,
    source_index: int = 4,
):
    return normalized_input(
        metric_id,
        value,
        period_key=period_key,
        period_end=period_end,
        period_kind=FundamentalMetricPeriodKind.QUARTERLY,
        reporting_basis=basis,
        metric_unit=unit,
        source_index=source_index,
    )


def financial_health_metrics(
    *,
    current_ratio: str = "2",
    debt_to_equity: str = "0.5",
    net_debt_to_ebitda: str = "0.5",
    interest_coverage: str = "6",
    net_working_capital: str = "200",
    debt_prior: str = "100",
    debt_current: str = "105",
    ebitda_prior: str = "200",
    ebitda_current: str = "220",
    ocf_prior: str = "180",
    ocf_current: str = "200",
    assets_prior: str = "1000",
    assets_current: str = "1080",
    revenue_prior: str = "800",
    revenue_current: str = "880",
    pat_prior: str = "80",
    pat_current: str = "90",
    roce_values: tuple[str, str, str] = ("18", "17", "16"),
    standalone_current_ratio: str = "2",
    standalone_debt_to_equity: str = "0.6",
    standalone_interest_coverage: str = "5",
    basis: FinancialReportingBasis = FinancialReportingBasis.CONSOLIDATED,
    include_basis_comparison: bool = True,
):
    metrics = [
        annual_metric(
            "derived.working_capital.current_ratio",
            Decimal(current_ratio),
            basis=basis,
            unit=ratio_unit(),
        ),
        annual_metric(
            "derived.leverage.debt_to_equity",
            Decimal(debt_to_equity),
            basis=basis,
            unit=ratio_unit(),
        ),
        annual_metric(
            "derived.leverage.net_debt_to_ebitda",
            Decimal(net_debt_to_ebitda),
            basis=basis,
            unit=ratio_unit(),
        ),
        annual_metric(
            "derived.leverage.net_debt",
            Decimal("100")
            if Decimal(net_debt_to_ebitda) >= 0
            else Decimal("-100"),
            basis=basis,
        ),
        annual_metric(
            "ratio.interest_coverage",
            Decimal(interest_coverage),
            basis=basis,
            unit=ratio_unit(),
        ),
        annual_metric(
            "derived.working_capital.net_working_capital",
            Decimal(net_working_capital),
            basis=basis,
        ),
    ]
    for year, value in zip((2024, 2025, 2026), roce_values, strict=True):
        metrics.append(
            annual_metric(
                "ratio.roce",
                Decimal(value),
                year=year,
                basis=basis,
                unit=percent_unit(),
            )
        )
    for metric_id, prior, current in (
        ("balance.borrowings", debt_prior, debt_current),
        ("income.ebitda", ebitda_prior, ebitda_current),
        ("cash_flow.operating", ocf_prior, ocf_current),
        ("balance.total_assets", assets_prior, assets_current),
        ("income.revenue", revenue_prior, revenue_current),
        ("income.pat", pat_prior, pat_current),
    ):
        metrics.extend(
            (
                annual_metric(
                    metric_id,
                    Decimal(prior),
                    year=2025,
                    basis=basis,
                ),
                annual_metric(
                    metric_id,
                    Decimal(current),
                    year=2026,
                    basis=basis,
                ),
            )
        )
    if include_basis_comparison:
        other_basis = (
            FinancialReportingBasis.STANDALONE
            if basis is FinancialReportingBasis.CONSOLIDATED
            else FinancialReportingBasis.CONSOLIDATED
        )
        metrics.extend(
            (
                annual_metric(
                    "derived.working_capital.current_ratio",
                    Decimal(standalone_current_ratio),
                    basis=other_basis,
                    unit=ratio_unit(),
                ),
                annual_metric(
                    "derived.leverage.debt_to_equity",
                    Decimal(standalone_debt_to_equity),
                    basis=other_basis,
                    unit=ratio_unit(),
                ),
                annual_metric(
                    "ratio.interest_coverage",
                    Decimal(standalone_interest_coverage),
                    basis=other_basis,
                    unit=ratio_unit(),
                ),
            )
        )
    return tuple(sorted(metrics, key=lambda metric: metric.key.canonical_key))


def earnings_quality_metrics(*, adverse: bool = False):
    values = (
        {
            "pat": ("100", "110", "120"),
            "ocf": ("105", "115", "130"),
            "fcf": ("70", "80", "90"),
            "ebitda": ("200", "220", "245"),
            "revenue": ("1000", "1100", "1210"),
            "receivables": ("100", "105", "110"),
            "inventory": ("50", "52", "54"),
            "shares": ("100", "100.5"),
            "pbt": "140",
            "exceptional": "5",
            "annual_revenue_growth": "10",
            "annual_pat_growth": "9",
            "quarterly_revenue_growth": "12",
            "quarterly_pat_growth": "8",
        }
        if not adverse
        else {
            "pat": ("100", "120", "150"),
            "ocf": ("80", "40", "20"),
            "fcf": ("20", "-10", "-30"),
            "ebitda": ("200", "230", "330"),
            "revenue": ("1000", "1050", "1100"),
            "receivables": ("100", "130", "220"),
            "inventory": ("50", "70", "120"),
            "shares": ("100", "110"),
            "pbt": "160",
            "exceptional": "50",
            "annual_revenue_growth": "5",
            "annual_pat_growth": "25",
            "quarterly_revenue_growth": "-8",
            "quarterly_pat_growth": "-15",
        }
    )
    metrics = []
    series = (
        ("income.pat", values["pat"]),
        ("cash_flow.operating", values["ocf"]),
        ("derived.cash_conversion.free_cash_flow", values["fcf"]),
        ("income.ebitda", values["ebitda"]),
        ("income.revenue", values["revenue"]),
        ("balance.trade_receivables", values["receivables"]),
        ("balance.inventory", values["inventory"]),
    )
    for metric_id, observations in series:
        for year, value in zip((2024, 2025, 2026), observations, strict=True):
            metrics.append(annual_metric(metric_id, Decimal(value), year=year))
    for year, value in zip((2025, 2026), values["shares"], strict=True):
        metrics.append(
            annual_metric(
                "income.share_count",
                Decimal(value),
                year=year,
                unit=count_unit(),
            )
        )
    metrics.extend(
        (
            annual_metric("income.pbt", Decimal(values["pbt"])),
            annual_metric(
                "income.exceptional_items",
                Decimal(values["exceptional"]),
            ),
            annual_metric(
                "derived.growth.revenue_yoy_pct",
                Decimal(values["annual_revenue_growth"]),
                unit=percent_unit(),
            ),
            annual_metric(
                "derived.growth.pat_yoy_pct",
                Decimal(values["annual_pat_growth"]),
                unit=percent_unit(),
            ),
            quarterly_metric(
                "derived.growth.revenue_yoy_pct",
                Decimal(values["quarterly_revenue_growth"]),
                unit=percent_unit(),
            ),
            quarterly_metric(
                "derived.growth.pat_yoy_pct",
                Decimal(values["quarterly_pat_growth"]),
                unit=percent_unit(),
            ),
        )
    )
    return tuple(sorted(metrics, key=lambda metric: metric.key.canonical_key))


def forensic_metrics(*, adverse: bool = False):
    values = (
        {
            "pat": ("100", "110", "120"),
            "ocf": ("95", "105", "118"),
            "receivables": ("100", "108", "116"),
            "revenue": ("1000", "1100", "1210"),
            "debt": ("100", "105"),
            "coverage": "5",
            "pledge": "0",
            "holding": ("50", "50", "50", "50"),
            "liabilities": "1000",
            "standalone_pat": ("90", "100"),
            "standalone_ocf": ("85", "95"),
            "standalone_revenue": ("900", "990"),
            "standalone_debt": ("80", "84"),
        }
        if not adverse
        else {
            "pat": ("100", "120", "150"),
            "ocf": ("-10", "-20", "-30"),
            "receivables": ("100", "160", "270"),
            "revenue": ("1000", "1050", "1100"),
            "debt": ("100", "150"),
            "coverage": "1.5",
            "pledge": "25",
            "holding": ("50", "49.4", "48.6", "47.4"),
            "liabilities": "900",
            "standalone_pat": ("100", "80"),
            "standalone_ocf": ("80", "75"),
            "standalone_revenue": ("1000", "850"),
            "standalone_debt": ("100", "90"),
        }
    )
    metrics = []
    for metric_id, observations in (
        ("income.pat", values["pat"]),
        ("cash_flow.operating", values["ocf"]),
        ("balance.trade_receivables", values["receivables"]),
        ("income.revenue", values["revenue"]),
    ):
        for year, value in zip((2024, 2025, 2026), observations, strict=True):
            metrics.append(annual_metric(metric_id, Decimal(value), year=year))
    for basis_metric, observations in (
        ("income.pat", values["standalone_pat"]),
        ("cash_flow.operating", values["standalone_ocf"]),
        ("income.revenue", values["standalone_revenue"]),
        ("balance.borrowings", values["standalone_debt"]),
    ):
        for year, value in zip((2025, 2026), observations, strict=True):
            metrics.append(
                annual_metric(
                    basis_metric,
                    Decimal(value),
                    year=year,
                    basis=FinancialReportingBasis.STANDALONE,
                    source_index=5,
                )
            )
    for year, value in zip((2025, 2026), values["debt"], strict=True):
        metrics.append(
            annual_metric("balance.borrowings", Decimal(value), year=year)
        )
    metrics.extend(
        (
            annual_metric(
                "ratio.interest_coverage",
                Decimal(values["coverage"]),
                unit=ratio_unit(),
                source_index=10,
            ),
            annual_metric("balance.total_assets", Decimal("1000")),
            annual_metric(
                "balance.total_liabilities",
                Decimal(values["liabilities"]),
            ),
            quarterly_metric(
                "ownership.promoter_pledge_pct",
                Decimal(values["pledge"]),
                basis=FinancialReportingBasis.NOT_APPLICABLE,
                unit=percent_unit(),
                source_index=2,
            ),
        )
    )
    quarters = (
        ("quarterly:q2fy2026", date(2025, 9, 30)),
        ("quarterly:q3fy2026", date(2025, 12, 31)),
        ("quarterly:q4fy2026", date(2026, 3, 31)),
        ("quarterly:q1fy2027", date(2026, 6, 30)),
    )
    for (period_key, period_end), value in zip(
        quarters,
        values["holding"],
        strict=True,
    ):
        metrics.append(
            quarterly_metric(
                "ownership.promoter_holding_pct",
                Decimal(value),
                period_key=period_key,
                period_end=period_end,
                basis=FinancialReportingBasis.NOT_APPLICABLE,
                unit=percent_unit(),
                source_index=2,
            )
        )
    return tuple(sorted(metrics, key=lambda metric: metric.key.canonical_key))


def normalized_evidence(metrics):
    return evidence(
        metrics=tuple(sorted(metrics, key=lambda metric: metric.key.canonical_key))
    )


def findings_by_rule(result):
    return {finding.rule_id: finding for finding in result.findings}


class FinancialHealthCheckTests(unittest.TestCase):
    def test_healthy_non_financial_evidence_passes_every_rule(self):
        result = assess_financial_health(
            normalized_evidence(financial_health_metrics()),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )

        self.assertEqual(result.policy_version, FINANCIAL_HEALTH_POLICY_VERSION)
        self.assertEqual(result.expected_rule_ids, FINANCIAL_HEALTH_RULE_IDS)
        self.assertEqual(
            {finding.status for finding in result.findings},
            {FundamentalFindingStatus.PASS},
        )
        self.assertEqual(
            result.coverage_status,
            FundamentalAssessmentCoverageStatus.COMPLETE,
        )
        self.assertEqual(result.stance, FundamentalAssessmentStance.SUPPORTIVE)
        self.assertFalse(result.review_required)
        self.assertTrue(
            all(finding.observation.citations for finding in result.findings)
        )

    def test_adverse_values_fail_without_equal_weight_masking(self):
        metrics = financial_health_metrics(
            current_ratio="0.8",
            debt_to_equity="2.5",
            net_debt_to_ebitda="4",
            interest_coverage="1",
            net_working_capital="-50",
            debt_current="160",
            ebitda_current="205",
            ocf_current="180",
            assets_current="1500",
            revenue_current="820",
            pat_current="81",
            roce_values=("8", "7", "6"),
            standalone_current_ratio="2",
        )
        result = assess_financial_health(
            normalized_evidence(metrics),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        findings = findings_by_rule(result)

        for rule_id in (
            "balance_sheet_expansion",
            "capital_efficiency_persistence",
            "debt_growth_coverage",
            "debt_to_equity",
            "interest_coverage",
            "liquidity",
            "net_debt_to_ebitda",
        ):
            self.assertEqual(findings[rule_id].status, FundamentalFindingStatus.FAIL)
            self.assertEqual(
                findings[rule_id].severity,
                FundamentalFindingSeverity.HIGH,
            )
        self.assertEqual(
            findings["working_capital_position"].status,
            FundamentalFindingStatus.WARN,
        )
        self.assertEqual(
            findings["reporting_basis_conflict"].status,
            FundamentalFindingStatus.WARN,
        )
        self.assertEqual(result.stance, FundamentalAssessmentStance.ADVERSE)
        self.assertTrue(result.review_required)

    def test_bfsi_and_unknown_profiles_never_use_industrial_thresholds(self):
        source = normalized_evidence(financial_health_metrics())
        for profile in (
            FinancialHealthSectorProfile.BFSI,
            FinancialHealthSectorProfile.UNKNOWN,
        ):
            with self.subTest(profile=profile):
                result = assess_financial_health(
                    source,
                    sector_profile=profile,
                    assessed_at=CHECKED_AT,
                )
                self.assertEqual(
                    {finding.status for finding in result.findings},
                    {FundamentalFindingStatus.NA},
                )
                self.assertEqual(
                    {finding.na_reason for finding in result.findings},
                    {FundamentalFindingNAReason.UNSUPPORTED_POLICY},
                )
                self.assertEqual(
                    result.coverage_status,
                    FundamentalAssessmentCoverageStatus.INSUFFICIENT,
                )
                self.assertTrue(result.review_required)

    def test_missing_metrics_release_explicit_na_instead_of_guessing(self):
        only_valuation = annual_metric(
            "valuation.pe",
            Decimal("20"),
            unit=ratio_unit(),
        )
        result = assess_financial_health(
            normalized_evidence((only_valuation,)),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )

        self.assertEqual(
            {finding.status for finding in result.findings},
            {FundamentalFindingStatus.NA},
        )
        self.assertEqual(
            {finding.na_reason for finding in result.findings},
            {FundamentalFindingNAReason.MISSING_EVIDENCE},
        )
        self.assertEqual(
            result.coverage_status,
            FundamentalAssessmentCoverageStatus.INSUFFICIENT,
        )

    def test_non_consecutive_capital_efficiency_history_is_not_interpolated(self):
        metrics = list(financial_health_metrics())
        metrics = [
            metric
            for metric in metrics
            if not (
                metric.key.metric_id == "ratio.roce"
                and metric.key.period_key == "annual:fy2025"
            )
        ]
        result = assess_financial_health(
            normalized_evidence(tuple(metrics)),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        capital = findings_by_rule(result)["capital_efficiency_persistence"]

        self.assertEqual(capital.status, FundamentalFindingStatus.NA)
        self.assertEqual(capital.na_reason, FundamentalFindingNAReason.MISSING_EVIDENCE)
        self.assertIn("3 annual observations", capital.limitations[0])

    def test_standalone_fallback_is_explicit_and_never_silent(self):
        result = assess_financial_health(
            normalized_evidence(
                financial_health_metrics(
                    basis=FinancialReportingBasis.STANDALONE,
                    include_basis_comparison=False,
                )
            ),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        liquidity = findings_by_rule(result)["liquidity"]

        self.assertEqual(liquidity.status, FundamentalFindingStatus.PASS)
        self.assertIn("standalone evidence", liquidity.limitations[0].lower())
        self.assertEqual(
            liquidity.observation.reporting_bases,
            (FinancialReportingBasis.STANDALONE,),
        )

    def test_assessment_rejects_expired_or_future_known_evidence(self):
        source = normalized_evidence(financial_health_metrics())
        with self.assertRaisesRegex(ValueError, "active normalized package"):
            assess_financial_health(
                source,
                sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
                assessed_at=source.package_expires_at,
            )

        first = source.metrics[0]
        future_period = first.period.model_copy(
            update={
                "availability": FundamentalMetricAvailability.KNOWN,
                "available_at": source.normalized_at + timedelta(hours=1),
            }
        )
        unsafe = source.model_copy(
            update={
                "metrics": (
                    first.model_copy(update={"period": future_period}),
                    *source.metrics[1:],
                )
            }
        )
        with self.assertRaisesRegex(ValueError, "was not available"):
            assess_financial_health(
                unsafe,
                sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
                assessed_at=CHECKED_AT,
            )

    def test_identical_evidence_produces_identical_cited_findings(self):
        source = normalized_evidence(financial_health_metrics())
        first = assess_financial_health(
            source,
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        second = assess_financial_health(
            source,
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.assessment_fingerprint, second.assessment_fingerprint)


class EarningsQualityCheckTests(unittest.TestCase):
    def test_cash_backed_consistent_evidence_passes_every_rule(self):
        result = assess_earnings_quality(
            normalized_evidence(earnings_quality_metrics()),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )

        self.assertEqual(result.policy_version, EARNINGS_QUALITY_POLICY_VERSION)
        self.assertEqual(result.expected_rule_ids, EARNINGS_QUALITY_RULE_IDS)
        self.assertEqual(
            {finding.status for finding in result.findings},
            {FundamentalFindingStatus.PASS},
        )
        self.assertEqual(result.stance, FundamentalAssessmentStance.SUPPORTIVE)
        self.assertEqual(
            result.coverage_status,
            FundamentalAssessmentCoverageStatus.COMPLETE,
        )
        self.assertTrue(
            all(finding.observation.citations for finding in result.findings)
        )

    def test_weak_cash_realization_and_dilution_release_adverse_findings(self):
        result = assess_earnings_quality(
            normalized_evidence(earnings_quality_metrics(adverse=True)),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        findings = findings_by_rule(result)

        for rule_id in (
            "cash_conversion_cumulative",
            "dilution",
            "ebitda_pat_ocf_divergence",
            "exceptional_dependence",
            "fcf_consistency",
            "margin_cash_conversion",
            "pat_ocf_alignment",
            "working_capital_quality",
        ):
            self.assertEqual(
                findings[rule_id].status,
                FundamentalFindingStatus.FAIL,
            )
            self.assertEqual(
                findings[rule_id].severity,
                FundamentalFindingSeverity.HIGH,
            )
        self.assertEqual(
            findings["annual_quarterly_consistency"].status,
            FundamentalFindingStatus.WARN,
        )
        self.assertEqual(
            findings["annual_quarterly_consistency"].severity,
            FundamentalFindingSeverity.HIGH,
        )
        self.assertEqual(result.stance, FundamentalAssessmentStance.ADVERSE)
        self.assertTrue(result.review_required)

    def test_missing_exceptional_items_remain_explicit_and_do_not_get_invented(self):
        metrics = tuple(
            metric
            for metric in earnings_quality_metrics()
            if metric.key.metric_id != "income.exceptional_items"
        )
        result = assess_earnings_quality(
            normalized_evidence(metrics),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        exceptional = findings_by_rule(result)["exceptional_dependence"]

        self.assertEqual(exceptional.status, FundamentalFindingStatus.NA)
        self.assertEqual(
            exceptional.na_reason,
            FundamentalFindingNAReason.MISSING_EVIDENCE,
        )
        self.assertIsNone(exceptional.observation)
        self.assertEqual(
            result.coverage_status,
            FundamentalAssessmentCoverageStatus.PARTIAL,
        )
        self.assertEqual(
            result.stance,
            FundamentalAssessmentStance.INSUFFICIENT_EVIDENCE,
        )

    def test_inventory_is_optional_when_receivables_and_revenue_are_available(self):
        metrics = tuple(
            metric
            for metric in earnings_quality_metrics()
            if metric.key.metric_id != "balance.inventory"
        )
        result = assess_earnings_quality(
            normalized_evidence(metrics),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        working_capital = findings_by_rule(result)["working_capital_quality"]

        self.assertEqual(working_capital.status, FundamentalFindingStatus.PASS)
        self.assertIn(
            "Inventory growth was unavailable",
            working_capital.limitations[0],
        )

    def test_bfsi_policy_is_withheld_instead_of_reusing_industrial_rules(self):
        result = assess_earnings_quality(
            normalized_evidence(earnings_quality_metrics()),
            sector_profile=FinancialHealthSectorProfile.BFSI,
            assessed_at=CHECKED_AT,
        )

        self.assertEqual(
            {finding.status for finding in result.findings},
            {FundamentalFindingStatus.NA},
        )
        self.assertEqual(
            {finding.na_reason for finding in result.findings},
            {FundamentalFindingNAReason.UNSUPPORTED_POLICY},
        )
        self.assertEqual(
            result.coverage_status,
            FundamentalAssessmentCoverageStatus.INSUFFICIENT,
        )

    def test_three_period_cash_checks_do_not_skip_missing_years(self):
        metrics = tuple(
            metric
            for metric in earnings_quality_metrics()
            if not (
                metric.key.metric_id == "cash_flow.operating"
                and metric.key.period_key == "annual:fy2025"
            )
        )
        result = assess_earnings_quality(
            normalized_evidence(metrics),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        findings = findings_by_rule(result)

        for rule_id in ("cash_conversion_cumulative", "pat_ocf_alignment"):
            self.assertEqual(
                findings[rule_id].status,
                FundamentalFindingStatus.NA,
            )
            self.assertEqual(
                findings[rule_id].na_reason,
                FundamentalFindingNAReason.MISSING_EVIDENCE,
            )

    def test_annual_quarterly_rule_requires_both_revenue_and_pat_pairs(self):
        metrics = tuple(
            metric
            for metric in earnings_quality_metrics()
            if not (
                metric.key.metric_id == "derived.growth.pat_yoy_pct"
                and metric.period.period_kind
                is FundamentalMetricPeriodKind.QUARTERLY
            )
        )
        result = assess_earnings_quality(
            normalized_evidence(metrics),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        consistency = findings_by_rule(result)[
            "annual_quarterly_consistency"
        ]

        self.assertEqual(consistency.status, FundamentalFindingStatus.NA)
        self.assertEqual(
            consistency.na_reason,
            FundamentalFindingNAReason.MISSING_EVIDENCE,
        )

    def test_cash_conversion_rejects_cross_currency_comparison(self):
        usd_unit = FundamentalMetricUnit(
            value_kind=FundamentalValueKind.MONETARY,
            normalized_unit="USD_MILLION",
            scale=Decimal("1000000"),
            currency="USD",
        )
        metrics = tuple(
            metric.model_copy(update={"unit": usd_unit})
            if metric.key.metric_id == "cash_flow.operating"
            else metric
            for metric in earnings_quality_metrics()
        )
        result = assess_earnings_quality(
            normalized_evidence(metrics),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        findings = findings_by_rule(result)

        for rule_id in (
            "cash_conversion_cumulative",
            "margin_cash_conversion",
            "pat_ocf_alignment",
        ):
            self.assertEqual(
                findings[rule_id].status,
                FundamentalFindingStatus.NA,
            )
            self.assertEqual(
                findings[rule_id].na_reason,
                FundamentalFindingNAReason.CONFLICTING_EVIDENCE,
            )

    def test_identical_evidence_produces_identical_earnings_findings(self):
        source = normalized_evidence(earnings_quality_metrics())
        first = assess_earnings_quality(
            source,
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        second = assess_earnings_quality(
            source,
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.assessment_fingerprint, second.assessment_fingerprint)


class ForensicRiskCheckTests(unittest.TestCase):
    def test_clean_evidence_passes_every_configured_screen(self):
        result = assess_forensic_risk(
            normalized_evidence(forensic_metrics()),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )

        self.assertEqual(result.policy_version, FORENSIC_RISK_POLICY_VERSION)
        self.assertEqual(result.expected_rule_ids, FORENSIC_RISK_RULE_IDS)
        self.assertEqual(
            {finding.status for finding in result.findings},
            {FundamentalFindingStatus.PASS},
        )
        self.assertEqual(result.stance, FundamentalAssessmentStance.SUPPORTIVE)
        self.assertEqual(
            result.coverage_status,
            FundamentalAssessmentCoverageStatus.COMPLETE,
        )
        self.assertTrue(
            all(finding.observation.citations for finding in result.findings)
        )

    def test_adverse_evidence_releases_review_flags_without_fraud_claims(self):
        result = assess_forensic_risk(
            normalized_evidence(forensic_metrics(adverse=True)),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        findings = findings_by_rule(result)

        for rule_id in FORENSIC_RISK_RULE_IDS:
            self.assertEqual(
                findings[rule_id].status,
                FundamentalFindingStatus.FAIL,
            )
            self.assertEqual(
                findings[rule_id].review_state,
                FundamentalReviewState.REQUIRED,
            )
        self.assertEqual(
            findings["persistent_profit_cash_mismatch"].severity,
            FundamentalFindingSeverity.CRITICAL,
        )
        self.assertEqual(
            findings["promoter_pledge"].severity,
            FundamentalFindingSeverity.CRITICAL,
        )
        self.assertEqual(result.stance, FundamentalAssessmentStance.ADVERSE)
        self.assertTrue(
            any("not findings of fraud" in item for item in result.limitations)
        )

    def test_bfsi_withholds_only_industrial_semantic_rules(self):
        result = assess_forensic_risk(
            normalized_evidence(forensic_metrics()),
            sector_profile=FinancialHealthSectorProfile.BFSI,
            assessed_at=CHECKED_AT,
        )
        findings = findings_by_rule(result)

        for rule_id in (
            "leverage_and_coverage",
            "persistent_profit_cash_mismatch",
            "receivables_growth",
        ):
            self.assertEqual(findings[rule_id].status, FundamentalFindingStatus.NA)
            self.assertEqual(
                findings[rule_id].na_reason,
                FundamentalFindingNAReason.UNSUPPORTED_POLICY,
            )
        for rule_id in (
            "accounting_conflicts",
            "basis_growth_divergence",
            "promoter_holding_erosion",
            "promoter_pledge",
        ):
            self.assertEqual(
                findings[rule_id].status,
                FundamentalFindingStatus.PASS,
            )

    def test_missing_ownership_history_is_explicitly_unavailable(self):
        metrics = tuple(
            metric
            for metric in forensic_metrics()
            if not metric.key.metric_id.startswith("ownership.")
        )
        result = assess_forensic_risk(
            normalized_evidence(metrics),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        findings = findings_by_rule(result)

        for rule_id in ("promoter_holding_erosion", "promoter_pledge"):
            self.assertEqual(findings[rule_id].status, FundamentalFindingStatus.NA)
            self.assertEqual(
                findings[rule_id].na_reason,
                FundamentalFindingNAReason.MISSING_EVIDENCE,
            )
            self.assertIsNone(findings[rule_id].observation)
        self.assertEqual(
            result.coverage_status,
            FundamentalAssessmentCoverageStatus.PARTIAL,
        )

    def test_promoter_erosion_does_not_bridge_a_missing_quarter(self):
        metrics = tuple(
            metric
            for metric in forensic_metrics(adverse=True)
            if not (
                metric.key.metric_id == "ownership.promoter_holding_pct"
                and metric.key.period_key == "quarterly:q3fy2026"
            )
        )
        result = assess_forensic_risk(
            normalized_evidence(metrics),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        erosion = findings_by_rule(result)["promoter_holding_erosion"]

        self.assertEqual(erosion.status, FundamentalFindingStatus.NA)
        self.assertEqual(
            erosion.na_reason,
            FundamentalFindingNAReason.MISSING_EVIDENCE,
        )

    def test_explicit_duplicate_conflict_fails_data_integrity_screen(self):
        metrics = list(forensic_metrics())
        index = next(
            index
            for index, metric in enumerate(metrics)
            if metric.key.metric_id == "balance.total_assets"
        )
        asset = metrics[index]
        second_citation = asset.citations[0].model_copy(
            update={"source_path": (*asset.citations[0].source_path, "duplicate")}
        )
        metrics[index] = asset.model_copy(
            update={
                "value_status": FundamentalMetricValueStatus.CONFLICT,
                "value": None,
                "source_values": ("1000", "1001"),
                "citations": (*asset.citations, second_citation),
                "quality_flags": (
                    *asset.quality_flags,
                    FundamentalMetricQualityFlag.DUPLICATE_CONFLICT,
                ),
            }
        )
        result = assess_forensic_risk(
            normalized_evidence(tuple(metrics)),
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        conflict = findings_by_rule(result)["accounting_conflicts"]

        self.assertEqual(conflict.status, FundamentalFindingStatus.FAIL)
        self.assertEqual(conflict.severity, FundamentalFindingSeverity.HIGH)
        self.assertEqual(conflict.observation.value, Decimal("1"))

    def test_identical_evidence_produces_identical_forensic_findings(self):
        source = normalized_evidence(forensic_metrics())
        first = assess_forensic_risk(
            source,
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )
        second = assess_forensic_risk(
            source,
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=CHECKED_AT,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.assessment_fingerprint, second.assessment_fingerprint)


if __name__ == "__main__":
    unittest.main()
