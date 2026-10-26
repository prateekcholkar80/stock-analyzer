import unittest
from datetime import date, timedelta
from decimal import Decimal

from app.analytics.fundamental_applicability import (
    FRAMEWORK_APPLICABILITY_POLICY_VERSION,
    assess_fundamental_framework_applicability,
)
from app.analytics.fundamental_checks import FinancialHealthSectorProfile
from app.models.fundamental_frameworks import (
    FundamentalFramework,
    FundamentalFrameworkApplicabilityStatus,
    FundamentalFrameworkEvidenceFamily,
    FundamentalFrameworkEvidenceStatus,
    FundamentalFrameworkLimitationKind,
)
from app.models.fundamental_metrics import (
    FundamentalMetricAvailability,
    FundamentalMetricQualityFlag,
    FundamentalMetricUnit,
    FundamentalMetricValueStatus,
)
from app.models.fundamentals import FundamentalValueKind
from tests.unit.test_fundamental_assessment import (
    assessment as mandatory_assessment_fixture,
)
from tests.unit.test_fundamental_checks import (
    annual_metric,
    count_unit,
    normalized_evidence,
    percent_unit,
    quarterly_metric,
)
from tests.unit.test_fundamental_metrics import NORMALIZED_AT, ratio_unit


ASSESSED_AT = NORMALIZED_AT + timedelta(seconds=2)


def per_share_unit() -> FundamentalMetricUnit:
    return FundamentalMetricUnit(
        value_kind=FundamentalValueKind.PER_SHARE,
        normalized_unit="INR_PER_SHARE",
        scale=Decimal("1"),
        currency="INR",
    )


def comprehensive_evidence(*, start_year: int = 2022):
    metrics = []
    for year in range(start_year, 2027):
        offset = year - start_year + 1
        for metric_id, value, unit, source_index in (
            ("income.pat", Decimal(100 + offset * 10), None, 6),
            ("income.revenue", Decimal(1000 + offset * 100), None, 6),
            ("income.ebitda", Decimal(200 + offset * 20), None, 6),
            ("income.eps", Decimal(10 + offset), per_share_unit(), 6),
            ("cash_flow.operating", Decimal(90 + offset * 10), None, 8),
            ("cash_flow.capex", Decimal(20 + offset), None, 8),
            ("ratio.roce", Decimal(15 + offset), percent_unit(), 10),
            ("income.share_count", Decimal("100"), count_unit(), 6),
        ):
            metrics.append(
                annual_metric(
                    metric_id,
                    value,
                    year=year,
                    unit=unit,
                    source_index=source_index,
                )
            )
    metrics.extend(
        (
            annual_metric(
                "ratio.debt_to_equity",
                Decimal("0.4"),
                unit=ratio_unit(),
                source_index=10,
            ),
            annual_metric(
                "ratio.interest_coverage",
                Decimal("8"),
                unit=ratio_unit(),
                source_index=10,
            ),
            annual_metric(
                "valuation.pe",
                Decimal("20"),
                unit=ratio_unit(),
                source_index=10,
            ),
            quarterly_metric(
                "income.revenue",
                Decimal("340"),
                period_key="quarterly:q3fy2026",
                period_end=date(2025, 12, 31),
                source_index=12,
            ),
            quarterly_metric(
                "income.revenue",
                Decimal("360"),
                period_key="quarterly:q4fy2026",
                period_end=date(2026, 3, 31),
                source_index=12,
            ),
            quarterly_metric(
                "income.eps",
                Decimal("3.2"),
                period_key="quarterly:q3fy2026",
                period_end=date(2025, 12, 31),
                unit=per_share_unit(),
                source_index=12,
            ),
            quarterly_metric(
                "income.eps",
                Decimal("3.5"),
                period_key="quarterly:q4fy2026",
                period_end=date(2026, 3, 31),
                unit=per_share_unit(),
                source_index=12,
            ),
        )
    )
    return normalized_evidence(tuple(metrics))


def mandatory_for(evidence):
    return mandatory_assessment_fixture(
        package_id=evidence.package_id,
        manifest_fingerprint=evidence.manifest_fingerprint,
        normalized_evidence_fingerprint=evidence.evidence_fingerprint,
        issuer=evidence.issuer,
        evidence_normalized_at=evidence.normalized_at,
        package_expires_at=evidence.package_expires_at,
        assessed_at=evidence.normalized_at + timedelta(seconds=1),
    )


def assess(evidence, *, profile=FinancialHealthSectorProfile.NON_FINANCIAL):
    return assess_fundamental_framework_applicability(
        evidence,
        mandatory_for(evidence),
        sector_profile=profile,
        assessed_at=ASSESSED_AT,
    )


def by_framework(result):
    return {item.framework: item for item in result.results}


class FundamentalApplicabilityTests(unittest.TestCase):
    def test_complete_financial_history_selects_three_eligible_lenses(self):
        result = assess(comprehensive_evidence())
        frameworks = by_framework(result)

        self.assertEqual(
            result.applicability_policy_version,
            FRAMEWORK_APPLICABILITY_POLICY_VERSION,
        )
        for framework in (
            FundamentalFramework.BUFFETT,
            FundamentalFramework.LYNCH,
            FundamentalFramework.JHUNJHUNWALA_STYLE,
            FundamentalFramework.ONE_HUNDRED_BAGGER,
        ):
            self.assertEqual(
                frameworks[framework].status,
                FundamentalFrameworkApplicabilityStatus.ELIGIBLE,
            )
            self.assertEqual(
                frameworks[framework].evidence_coverage_percentage,
                Decimal("100.00"),
            )
        self.assertEqual(
            result.selected_primary_frameworks,
            (
                FundamentalFramework.BUFFETT,
                FundamentalFramework.LYNCH,
                FundamentalFramework.JHUNJHUNWALA_STYLE,
            ),
        )

    def test_unsupported_sources_keep_can_slim_and_valuation_partial(self):
        result = assess(comprehensive_evidence())
        frameworks = by_framework(result)
        can_slim = frameworks[FundamentalFramework.CAN_SLIM]
        valuation = frameworks[FundamentalFramework.VALUATION]

        self.assertEqual(
            can_slim.status,
            FundamentalFrameworkApplicabilityStatus.PARTIAL,
        )
        self.assertEqual(
            can_slim.evidence_coverage_percentage,
            Decimal("20.00"),
        )
        self.assertEqual(
            valuation.status,
            FundamentalFrameworkApplicabilityStatus.PARTIAL,
        )
        self.assertEqual(
            valuation.evidence_coverage_percentage,
            Decimal("50.00"),
        )
        can_coverage = {item.family: item for item in can_slim.evidence_coverage}
        self.assertEqual(
            can_coverage[
                FundamentalFrameworkEvidenceFamily.INSTITUTIONAL_SPONSORSHIP
            ].status,
            FundamentalFrameworkEvidenceStatus.UNSUPPORTED,
        )
        self.assertEqual(valuation.deterministic_priority, 5)
        self.assertEqual(can_slim.deterministic_priority, 6)

    def test_three_year_history_does_not_satisfy_five_year_frameworks(self):
        result = assess(comprehensive_evidence(start_year=2024))
        frameworks = by_framework(result)

        self.assertEqual(
            frameworks[FundamentalFramework.BUFFETT].status,
            FundamentalFrameworkApplicabilityStatus.PARTIAL,
        )
        self.assertEqual(
            frameworks[FundamentalFramework.ONE_HUNDRED_BAGGER].status,
            FundamentalFrameworkApplicabilityStatus.PARTIAL,
        )
        self.assertEqual(
            result.selected_primary_frameworks,
            (
                FundamentalFramework.LYNCH,
                FundamentalFramework.JHUNJHUNWALA_STYLE,
            ),
        )

    def test_missing_capital_efficiency_never_becomes_a_silent_pass(self):
        source = comprehensive_evidence()
        without_returns = source.model_copy(
            update={
                "metrics": tuple(
                    metric
                    for metric in source.metrics
                    if metric.key.metric_id not in {"ratio.roce", "ratio.roe"}
                )
            }
        )
        result = assess(without_returns)
        frameworks = by_framework(result)

        for framework in (
            FundamentalFramework.BUFFETT,
            FundamentalFramework.JHUNJHUNWALA_STYLE,
            FundamentalFramework.ONE_HUNDRED_BAGGER,
        ):
            capital = next(
                item
                for item in frameworks[framework].evidence_coverage
                if item.family
                is FundamentalFrameworkEvidenceFamily.CAPITAL_EFFICIENCY
            )
            self.assertEqual(
                capital.status,
                FundamentalFrameworkEvidenceStatus.MISSING,
            )
            self.assertFalse(capital.metric_keys)
            self.assertFalse(capital.citations)

    def test_bfsi_is_fail_closed_until_sector_policies_are_approved(self):
        result = assess(
            comprehensive_evidence(),
            profile=FinancialHealthSectorProfile.BFSI,
        )

        self.assertEqual(result.selected_primary_frameworks, ())
        self.assertEqual(
            {item.status for item in result.results},
            {FundamentalFrameworkApplicabilityStatus.INELIGIBLE},
        )
        for item in result.results:
            self.assertTrue(
                any(
                    limitation.kind is FundamentalFrameworkLimitationKind.SECTOR
                    and limitation.excludes_framework
                    for limitation in item.limitations
                )
            )

    def test_non_consecutive_history_is_not_interpolated(self):
        source = comprehensive_evidence()
        without_2025_pat = source.model_copy(
            update={
                "metrics": tuple(
                    metric
                    for metric in source.metrics
                    if not (
                        metric.key.metric_id == "income.pat"
                        and metric.key.period_key == "annual:fy2025"
                    )
                )
            }
        )
        result = assess(without_2025_pat)
        profitability = next(
            item
            for item in by_framework(result)[
                FundamentalFramework.LYNCH
            ].evidence_coverage
            if item.family is FundamentalFrameworkEvidenceFamily.PROFITABILITY
        )

        self.assertEqual(
            profitability.status,
            FundamentalFrameworkEvidenceStatus.MISSING,
        )
        self.assertIn("not consecutive", profitability.limitation)

    def test_conflicting_metric_is_preserved_with_source_citations(self):
        source = comprehensive_evidence()
        metrics = list(source.metrics)
        index = next(
            index
            for index, metric in enumerate(metrics)
            if metric.key.metric_id == "ratio.roce"
            and metric.key.period_key == "annual:fy2026"
        )
        metric = metrics[index]
        second_citation = metric.citations[0].model_copy(
            update={"source_path": (*metric.citations[0].source_path, "duplicate")}
        )
        metrics[index] = metric.model_copy(
            update={
                "value_status": FundamentalMetricValueStatus.CONFLICT,
                "value": None,
                "source_values": ("20", "21"),
                "citations": (*metric.citations, second_citation),
                "quality_flags": (
                    *metric.quality_flags,
                    FundamentalMetricQualityFlag.DUPLICATE_CONFLICT,
                ),
            }
        )
        conflicted = source.model_copy(update={"metrics": tuple(metrics)})
        result = assess(conflicted)
        capital = next(
            item
            for item in by_framework(result)[
                FundamentalFramework.BUFFETT
            ].evidence_coverage
            if item.family
            is FundamentalFrameworkEvidenceFamily.CAPITAL_EFFICIENCY
        )

        self.assertEqual(
            capital.status,
            FundamentalFrameworkEvidenceStatus.CONFLICTING,
        )
        self.assertTrue(capital.metric_keys)
        self.assertEqual(len(capital.citations), 2)

    def test_mandatory_assessment_must_share_the_exact_evidence_package(self):
        source = comprehensive_evidence()
        mandatory = mandatory_for(source).model_copy(
            update={"normalized_evidence_fingerprint": "f" * 64}
        )

        with self.assertRaisesRegex(ValueError, "immutable package boundary"):
            assess_fundamental_framework_applicability(
                source,
                mandatory,
                sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
                assessed_at=ASSESSED_AT,
            )

    def test_future_or_not_yet_published_evidence_is_rejected(self):
        source = comprehensive_evidence()
        first = source.metrics[0]
        unsafe_period = first.period.model_copy(
            update={
                "availability": FundamentalMetricAvailability.KNOWN,
                "available_at": source.normalized_at + timedelta(hours=1),
            }
        )
        unsafe = source.model_copy(
            update={
                "metrics": (
                    first.model_copy(update={"period": unsafe_period}),
                    *source.metrics[1:],
                )
            }
        )

        with self.assertRaisesRegex(ValueError, "before publication"):
            assess_fundamental_framework_applicability(
                unsafe,
                mandatory_for(unsafe),
                sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
                assessed_at=ASSESSED_AT,
            )

    def test_identical_inputs_produce_identical_selection_and_fingerprint(self):
        source = comprehensive_evidence()
        mandatory = mandatory_for(source)
        first = assess_fundamental_framework_applicability(
            source,
            mandatory,
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=ASSESSED_AT,
        )
        second = assess_fundamental_framework_applicability(
            source,
            mandatory,
            sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
            assessed_at=ASSESSED_AT,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.assessment_fingerprint, second.assessment_fingerprint)


if __name__ == "__main__":
    unittest.main()
