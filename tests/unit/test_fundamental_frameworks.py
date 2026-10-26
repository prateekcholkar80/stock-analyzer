import unittest
from datetime import timedelta
from decimal import Decimal

from pydantic import ValidationError

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
from tests.unit.test_fundamental_gateway import build_issuer
from tests.unit.test_fundamental_metrics import (
    EXPIRES_AT,
    MANIFEST_FINGERPRINT,
    NORMALIZED_AT,
    citation,
)


POLICY_ID = "jarvis.framework_applicability"
POLICY_VERSION = "1.0.0"


def evidence_coverage(
    family: FundamentalFrameworkEvidenceFamily,
    *,
    status: FundamentalFrameworkEvidenceStatus = (
        FundamentalFrameworkEvidenceStatus.AVAILABLE
    ),
) -> FundamentalFrameworkEvidenceCoverage:
    if status is FundamentalFrameworkEvidenceStatus.AVAILABLE:
        return FundamentalFrameworkEvidenceCoverage(
            family=family,
            status=status,
            metric_keys=(f"framework.{family.value}:consolidated:annual:fy2026",),
            citations=(
                citation(
                    source_path=("framework", family.value),
                    period_key="fy2026",
                ),
            ),
        )
    if status is FundamentalFrameworkEvidenceStatus.CONFLICTING:
        return FundamentalFrameworkEvidenceCoverage(
            family=family,
            status=status,
            metric_keys=(f"framework.{family.value}:consolidated:annual:fy2026",),
            citations=(
                citation(
                    source_path=("framework", family.value),
                    period_key="fy2026",
                ),
            ),
            limitation="Normalized observations conflict.",
        )
    return FundamentalFrameworkEvidenceCoverage(
        family=family,
        status=status,
        limitation="Required normalized evidence is unavailable.",
    )


def applicability(
    framework: FundamentalFramework,
    priority: int,
    *,
    status: FundamentalFrameworkApplicabilityStatus = (
        FundamentalFrameworkApplicabilityStatus.ELIGIBLE
    ),
) -> FundamentalFrameworkApplicability:
    families = (
        FundamentalFrameworkEvidenceFamily.PROFITABILITY,
        FundamentalFrameworkEvidenceFamily.GROWTH,
    )
    coverage_statuses = {
        FundamentalFrameworkApplicabilityStatus.ELIGIBLE: (
            FundamentalFrameworkEvidenceStatus.AVAILABLE,
            FundamentalFrameworkEvidenceStatus.AVAILABLE,
        ),
        FundamentalFrameworkApplicabilityStatus.PARTIAL: (
            FundamentalFrameworkEvidenceStatus.AVAILABLE,
            FundamentalFrameworkEvidenceStatus.MISSING,
        ),
        FundamentalFrameworkApplicabilityStatus.UNAVAILABLE: (
            FundamentalFrameworkEvidenceStatus.MISSING,
            FundamentalFrameworkEvidenceStatus.MISSING,
        ),
        FundamentalFrameworkApplicabilityStatus.INELIGIBLE: (
            FundamentalFrameworkEvidenceStatus.AVAILABLE,
            FundamentalFrameworkEvidenceStatus.AVAILABLE,
        ),
    }
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
    limitations = (
        (
            FundamentalFrameworkApplicabilityLimitation(
                limitation_id=f"{framework.value}.sector_exclusion",
                kind=FundamentalFrameworkLimitationKind.SECTOR,
                summary="The framework is excluded under this sector policy.",
                excludes_framework=True,
            ),
        )
        if status is FundamentalFrameworkApplicabilityStatus.INELIGIBLE
        else ()
    )
    return FundamentalFrameworkApplicability(
        result_id=f"fundamental.framework_applicability:{framework.value}",
        framework=framework,
        status=status,
        applicability_policy_id=POLICY_ID,
        applicability_policy_version=POLICY_VERSION,
        deterministic_priority=priority,
        evidence_coverage=tuple(
            evidence_coverage(family, status=coverage_status)
            for family, coverage_status in zip(
                families,
                coverage_statuses[status],
                strict=True,
            )
        ),
        reasons=(
            FundamentalFrameworkApplicabilityReason(
                reason_id=f"{framework.value}.{status.value}",
                kind=reason_kind,
                summary=f"Deterministic {status.value} result.",
                evidence_families=families,
            ),
        ),
        limitations=limitations,
    )


def assessment(
    *,
    results: tuple[FundamentalFrameworkApplicability, ...] | None = None,
    selected: tuple[FundamentalFramework, ...] = (
        FundamentalFramework.BUFFETT,
        FundamentalFramework.LYNCH,
        FundamentalFramework.JHUNJHUNWALA_STYLE,
    ),
    **updates,
) -> FundamentalFrameworkApplicabilityAssessment:
    values = {
        "package_id": f"fundamental_package:{MANIFEST_FINGERPRINT}",
        "manifest_fingerprint": MANIFEST_FINGERPRINT,
        "normalized_evidence_fingerprint": "b" * 64,
        "mandatory_assessment_fingerprint": "c" * 64,
        "issuer": build_issuer(),
        "evidence_normalized_at": NORMALIZED_AT,
        "mandatory_assessed_at": NORMALIZED_AT + timedelta(seconds=1),
        "assessed_at": NORMALIZED_AT + timedelta(seconds=2),
        "package_expires_at": EXPIRES_AT,
        "applicability_policy_id": POLICY_ID,
        "applicability_policy_version": POLICY_VERSION,
        "results": (
            results
            if results is not None
            else tuple(
                applicability(framework, priority)
                for priority, framework in enumerate(
                    FundamentalFramework,
                    start=1,
                )
            )
        ),
        "selected_primary_frameworks": selected,
        "limitations": (
            "Applicability is quantitative and excludes qualitative moat claims.",
        ),
    }
    values.update(updates)
    return FundamentalFrameworkApplicabilityAssessment(**values)


def rebuild_applicability(
    source: FundamentalFrameworkApplicability,
    **updates,
) -> FundamentalFrameworkApplicability:
    values = {
        field_name: getattr(source, field_name)
        for field_name in FundamentalFrameworkApplicability.model_fields
    }
    values.update(updates)
    return FundamentalFrameworkApplicability(**values)


class FrameworkEvidenceCoverageTests(unittest.TestCase):
    def test_available_and_conflicting_evidence_require_exact_citations(self):
        available = evidence_coverage(
            FundamentalFrameworkEvidenceFamily.PROFITABILITY
        )
        conflicting = evidence_coverage(
            FundamentalFrameworkEvidenceFamily.GROWTH,
            status=FundamentalFrameworkEvidenceStatus.CONFLICTING,
        )

        self.assertTrue(available.metric_keys)
        self.assertTrue(available.citations)
        self.assertIsNotNone(conflicting.limitation)

        with self.assertRaisesRegex(ValidationError, "metrics and citations"):
            FundamentalFrameworkEvidenceCoverage(
                family=FundamentalFrameworkEvidenceFamily.PROFITABILITY,
                status=FundamentalFrameworkEvidenceStatus.AVAILABLE,
            )

    def test_missing_evidence_cannot_carry_invented_metric_citations(self):
        with self.assertRaisesRegex(ValidationError, "cannot cite metrics"):
            FundamentalFrameworkEvidenceCoverage(
                family=FundamentalFrameworkEvidenceFamily.PROFITABILITY,
                status=FundamentalFrameworkEvidenceStatus.MISSING,
                metric_keys=("income.pat:consolidated:annual:fy2026",),
                citations=(citation(),),
                limitation="Evidence is missing.",
            )


class FrameworkApplicabilityContractTests(unittest.TestCase):
    def test_eligible_result_has_complete_derived_coverage_and_fingerprint(self):
        result = applicability(FundamentalFramework.BUFFETT, 1)

        self.assertEqual(
            result.required_evidence_families,
            (
                FundamentalFrameworkEvidenceFamily.PROFITABILITY,
                FundamentalFrameworkEvidenceFamily.GROWTH,
            ),
        )
        self.assertEqual(
            result.available_evidence_families,
            result.required_evidence_families,
        )
        self.assertEqual(result.missing_evidence_families, ())
        self.assertEqual(result.evidence_coverage_percentage, Decimal("100.00"))
        self.assertEqual(len(result.applicability_fingerprint), 64)

    def test_partial_and_unavailable_results_preserve_missing_families(self):
        partial = applicability(
            FundamentalFramework.LYNCH,
            2,
            status=FundamentalFrameworkApplicabilityStatus.PARTIAL,
        )
        unavailable = applicability(
            FundamentalFramework.CAN_SLIM,
            5,
            status=FundamentalFrameworkApplicabilityStatus.UNAVAILABLE,
        )

        self.assertEqual(partial.evidence_coverage_percentage, Decimal("50.00"))
        self.assertEqual(
            partial.missing_evidence_families,
            (FundamentalFrameworkEvidenceFamily.GROWTH,),
        )
        self.assertEqual(
            unavailable.evidence_coverage_percentage,
            Decimal("0.00"),
        )

    def test_status_cannot_contradict_evidence_coverage(self):
        eligible = applicability(FundamentalFramework.BUFFETT, 1)

        with self.assertRaisesRegex(ValidationError, "incomplete non-zero"):
            rebuild_applicability(
                eligible,
                status=FundamentalFrameworkApplicabilityStatus.PARTIAL,
            )

    def test_ineligible_result_requires_typed_exclusion(self):
        eligible = applicability(FundamentalFramework.VALUATION, 6)
        reasons = (
            FundamentalFrameworkApplicabilityReason(
                reason_id="valuation.excluded",
                kind=FundamentalFrameworkReasonKind.EXCLUSION,
                summary="Policy excludes this framework.",
            ),
        )

        with self.assertRaisesRegex(ValidationError, "requires an exclusion"):
            rebuild_applicability(
                eligible,
                status=FundamentalFrameworkApplicabilityStatus.INELIGIBLE,
                reasons=reasons,
            )

    def test_contracts_are_frozen(self):
        result = applicability(FundamentalFramework.BUFFETT, 1)

        with self.assertRaises(ValidationError):
            result.status = FundamentalFrameworkApplicabilityStatus.INELIGIBLE


class FrameworkApplicabilityAssessmentTests(unittest.TestCase):
    def test_assessment_requires_all_frameworks_and_stable_fingerprint(self):
        first = assessment()
        second = assessment()

        self.assertEqual(
            tuple(item.framework for item in first.results),
            tuple(FundamentalFramework),
        )
        self.assertEqual(first, second)
        self.assertEqual(first.assessment_fingerprint, second.assessment_fingerprint)

    def test_assessment_requires_all_six_and_at_most_three_selected(self):
        complete = assessment()

        with self.assertRaises(ValidationError):
            assessment(results=complete.results[:-1])
        with self.assertRaises(ValidationError):
            assessment(
                selected=(
                    FundamentalFramework.BUFFETT,
                    FundamentalFramework.LYNCH,
                    FundamentalFramework.JHUNJHUNWALA_STYLE,
                    FundamentalFramework.ONE_HUNDRED_BAGGER,
                )
            )

    def test_only_eligible_frameworks_can_be_selected(self):
        results = tuple(
            applicability(
                framework,
                priority,
                status=(
                    FundamentalFrameworkApplicabilityStatus.PARTIAL
                    if framework is FundamentalFramework.LYNCH
                    else FundamentalFrameworkApplicabilityStatus.ELIGIBLE
                ),
            )
            for priority, framework in enumerate(FundamentalFramework, start=1)
        )

        with self.assertRaisesRegex(ValidationError, "only eligible"):
            assessment(results=results)

    def test_selected_frameworks_follow_deterministic_priority(self):
        with self.assertRaisesRegex(ValidationError, "deterministic priority"):
            assessment(
                selected=(
                    FundamentalFramework.LYNCH,
                    FundamentalFramework.BUFFETT,
                )
            )

    def test_priorities_must_be_unique_complete_ranks(self):
        results = tuple(
            applicability(framework, 1)
            for framework in FundamentalFramework
        )

        with self.assertRaisesRegex(ValidationError, "unique ranks"):
            assessment(results=results)

    def test_result_policy_and_active_package_must_match_aggregate(self):
        results = list(assessment().results)
        results[0] = rebuild_applicability(
            results[0],
            applicability_policy_version="2.0.0",
        )

        with self.assertRaisesRegex(ValidationError, "aggregate policy"):
            assessment(results=tuple(results))
        with self.assertRaisesRegex(ValidationError, "active package"):
            assessment(assessed_at=EXPIRES_AT)


if __name__ == "__main__":
    unittest.main()
