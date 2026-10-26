import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from pydantic import ValidationError

from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_assessment import (
    FundamentalAssessmentCoverageStatus,
    FundamentalAssessmentDomain,
    FundamentalAssessmentFinding,
    FundamentalAssessmentStance,
    FundamentalAssessmentThreshold,
    FundamentalDomainAssessment,
    FundamentalFindingNAReason,
    FundamentalFindingObservation,
    FundamentalFindingSeverity,
    FundamentalFindingStatus,
    FundamentalMandatoryAssessment,
    FundamentalReviewState,
    FundamentalThresholdOperator,
)
from app.models.fundamental_metrics import (
    FundamentalMetricCitation,
    FundamentalMetricUnit,
)
from app.models.fundamental_package import FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS
from app.models.fundamentals import FundamentalValueKind
from tests.unit.test_fundamental_gateway import build_issuer


IST = ZoneInfo("Asia/Kolkata")
NORMALIZED_AT = datetime(2026, 9, 12, 10, 0, tzinfo=IST)
ASSESSED_AT = NORMALIZED_AT + timedelta(seconds=1)
EXPIRES_AT = NORMALIZED_AT + timedelta(days=10)
MANIFEST_FINGERPRINT = "a" * 64
EVIDENCE_FINGERPRINT = "b" * 64


def ratio_unit() -> FundamentalMetricUnit:
    return FundamentalMetricUnit(
        value_kind=FundamentalValueKind.RATIO,
        normalized_unit="RATIO",
        scale=Decimal("1"),
        currency=None,
    )


def citation(*, source_index: int = 4, label: str = "Debt to equity"):
    key = FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS[source_index]
    return FundamentalMetricCitation(
        package_item_key=key,
        source_reference_id=f"fundamental_source:item-{source_index}",
        content_fingerprint=f"{source_index + 1:064x}",
        source_path=("metrics", "leverage.debt_to_equity", "fy2026"),
        source_label=label,
        period_key="fy2026",
    )


def observation(**updates) -> FundamentalFindingObservation:
    values = {
        "metric_keys": (
            "leverage.debt_to_equity:consolidated:annual:fy2026",
        ),
        "reporting_bases": (FinancialReportingBasis.CONSOLIDATED,),
        "value": Decimal("0.42"),
        "unit": ratio_unit(),
        "citations": (citation(),),
    }
    values.update(updates)
    return FundamentalFindingObservation(**values)


def threshold(**updates) -> FundamentalAssessmentThreshold:
    values = {
        "threshold_id": "financial_health.leverage.debt_to_equity",
        "threshold_version": "1.0.0",
        "operator": FundamentalThresholdOperator.LESS_THAN_OR_EQUAL,
        "lower_bound": Decimal("2"),
        "upper_bound": None,
        "unit": ratio_unit(),
        "description": "Debt to equity must not exceed 2.0.",
    }
    values.update(updates)
    return FundamentalAssessmentThreshold(**values)


def finding(
    domain: FundamentalAssessmentDomain,
    rule_id: str,
    **updates,
) -> FundamentalAssessmentFinding:
    values = {
        "finding_id": f"fundamental.finding:{domain.value}:{rule_id}",
        "domain": domain,
        "rule_id": rule_id,
        "rule_version": "1.0.0",
        "status": FundamentalFindingStatus.PASS,
        "severity": FundamentalFindingSeverity.NONE,
        "review_state": FundamentalReviewState.NOT_REQUIRED,
        "title": "Deterministic rule result",
        "summary": "The cited normalized evidence satisfies this rule.",
        "threshold": threshold(
            threshold_id=f"{domain.value}.{rule_id}",
        ),
        "observation": observation(),
    }
    values.update(updates)
    return FundamentalAssessmentFinding(**values)


def domain_assessment(
    domain: FundamentalAssessmentDomain,
    *,
    findings: tuple[FundamentalAssessmentFinding, ...] | None = None,
    **updates,
) -> FundamentalDomainAssessment:
    domain_findings = findings or (finding(domain, "baseline"),)
    values = {
        "domain": domain,
        "policy_id": f"jarvis.{domain.value}",
        "policy_version": "1.0.0",
        "expected_rule_ids": tuple(item.rule_id for item in domain_findings),
        "findings": domain_findings,
    }
    values.update(updates)
    return FundamentalDomainAssessment(**values)


def assessment(**updates) -> FundamentalMandatoryAssessment:
    values = {
        "package_id": f"fundamental_package:{MANIFEST_FINGERPRINT}",
        "manifest_fingerprint": MANIFEST_FINGERPRINT,
        "normalized_evidence_fingerprint": EVIDENCE_FINGERPRINT,
        "issuer": build_issuer(),
        "evidence_normalized_at": NORMALIZED_AT,
        "package_expires_at": EXPIRES_AT,
        "assessed_at": ASSESSED_AT,
        "policy_bundle_id": "jarvis.mandatory_fundamental_checks",
        "policy_bundle_version": "1.0.0",
        "domains": tuple(
            domain_assessment(domain)
            for domain in FundamentalAssessmentDomain
        ),
    }
    values.update(updates)
    return FundamentalMandatoryAssessment(**values)


class FundamentalAssessmentContractTests(unittest.TestCase):
    def test_complete_assessment_is_immutable_cited_and_fingerprinted(self):
        result = assessment()

        self.assertEqual(
            result.coverage_status,
            FundamentalAssessmentCoverageStatus.COMPLETE,
        )
        self.assertEqual(result.stance, FundamentalAssessmentStance.SUPPORTIVE)
        self.assertFalse(result.review_required)
        self.assertEqual(result.finding_count, 3)
        self.assertEqual(len(result.assessment_fingerprint), 64)
        self.assertEqual(
            result.domains[0].findings[0].observation.citations[0].period_key,
            "fy2026",
        )
        with self.assertRaises(ValidationError):
            result.policy_bundle_version = "2.0.0"

    def test_finding_status_controls_severity_review_and_observation(self):
        domain = FundamentalAssessmentDomain.FINANCIAL_HEALTH
        with self.assertRaisesRegex(
            ValidationError,
            "finding status contradicts severity",
        ):
            finding(
                domain,
                "invalid.pass",
                severity=FundamentalFindingSeverity.HIGH,
            )
        with self.assertRaisesRegex(
            ValidationError,
            "warning and failing findings require review",
        ):
            finding(
                domain,
                "invalid.warn",
                status=FundamentalFindingStatus.WARN,
                severity=FundamentalFindingSeverity.MEDIUM,
            )
        with self.assertRaisesRegex(
            ValidationError,
            "evaluated finding requires cited observations",
        ):
            finding(domain, "invalid.observation", observation=None)

    def test_na_distinguishes_not_applicable_from_unavailable_evidence(self):
        domain = FundamentalAssessmentDomain.FORENSIC_RISK
        not_applicable = finding(
            domain,
            "sector_specific",
            status=FundamentalFindingStatus.NA,
            severity=FundamentalFindingSeverity.NONE,
            review_state=FundamentalReviewState.NOT_REQUIRED,
            na_reason=FundamentalFindingNAReason.NOT_APPLICABLE,
            observation=None,
            limitations=("Rule does not apply to this issuer classification.",),
        )
        unavailable = finding(
            domain,
            "promoter_pledge",
            status=FundamentalFindingStatus.NA,
            severity=FundamentalFindingSeverity.NONE,
            review_state=FundamentalReviewState.REQUIRED,
            na_reason=FundamentalFindingNAReason.MISSING_EVIDENCE,
            observation=None,
            limitations=("Promoter pledge evidence is unavailable.",),
        )
        result = domain_assessment(
            domain,
            findings=(unavailable, not_applicable),
            expected_rule_ids=("promoter_pledge", "sector_specific"),
        )

        self.assertEqual(result.evaluated_rule_count, 0)
        self.assertEqual(result.not_applicable_rule_count, 1)
        self.assertEqual(result.unavailable_rule_count, 1)
        self.assertEqual(
            result.coverage_status,
            FundamentalAssessmentCoverageStatus.INSUFFICIENT,
        )
        self.assertEqual(
            result.stance,
            FundamentalAssessmentStance.INSUFFICIENT_EVIDENCE,
        )
        self.assertTrue(result.review_required)

    def test_na_requires_reason_limitation_and_consistent_review_state(self):
        domain = FundamentalAssessmentDomain.EARNINGS_QUALITY
        with self.assertRaisesRegex(
            ValidationError,
            "NA finding requires an explicit reason",
        ):
            finding(
                domain,
                "missing_reason",
                status=FundamentalFindingStatus.NA,
                severity=FundamentalFindingSeverity.NONE,
                review_state=FundamentalReviewState.REQUIRED,
                observation=None,
                limitations=("Evidence unavailable.",),
            )
        with self.assertRaisesRegex(
            ValidationError,
            "unavailable mandatory evidence requires review",
        ):
            finding(
                domain,
                "missing_review",
                status=FundamentalFindingStatus.NA,
                severity=FundamentalFindingSeverity.NONE,
                review_state=FundamentalReviewState.NOT_REQUIRED,
                na_reason=FundamentalFindingNAReason.MISSING_EVIDENCE,
                observation=None,
                limitations=("Evidence unavailable.",),
            )

    def test_threshold_contract_rejects_ambiguous_or_non_finite_bounds(self):
        with self.assertRaisesRegex(
            ValidationError,
            "single-bound threshold requires only its lower bound",
        ):
            threshold(upper_bound=Decimal("3"))
        with self.assertRaisesRegex(
            ValidationError,
            "threshold lower bound cannot exceed upper bound",
        ):
            threshold(
                operator=FundamentalThresholdOperator.BETWEEN_INCLUSIVE,
                lower_bound=Decimal("2"),
                upper_bound=Decimal("1"),
            )
        with self.assertRaisesRegex(
            ValidationError,
            "finite",
        ):
            threshold(lower_bound=Decimal("NaN"))
        composite = threshold(
            operator=FundamentalThresholdOperator.COMPOSITE,
            lower_bound=None,
            unit=None,
        )
        self.assertIs(composite.operator, FundamentalThresholdOperator.COMPOSITE)

    def test_observation_requires_unit_safe_canonical_citations(self):
        with self.assertRaisesRegex(
            ValidationError,
            "requires value and unit together",
        ):
            observation(unit=None)
        with self.assertRaisesRegex(
            ValidationError,
            "assessment metric keys must be unique",
        ):
            observation(metric_keys=("metric:a:p", "metric:a:p"))
        duplicated = citation()
        with self.assertRaisesRegex(
            ValidationError,
            "assessment citations must be unique",
        ):
            observation(citations=(duplicated, duplicated))

    def test_domain_requires_exact_configured_rules_and_canonical_order(self):
        domain = FundamentalAssessmentDomain.FINANCIAL_HEALTH
        second = finding(domain, "z_rule")
        first = finding(domain, "a_rule")
        with self.assertRaisesRegex(
            ValidationError,
            "assessment rule IDs must use canonical order",
        ):
            domain_assessment(
                domain,
                findings=(second, first),
                expected_rule_ids=("z_rule", "a_rule"),
            )
        with self.assertRaisesRegex(
            ValidationError,
            "cover every expected rule",
        ):
            domain_assessment(
                domain,
                findings=(first,),
                expected_rule_ids=("a_rule", "z_rule"),
            )

    def test_domain_computes_adverse_stance_without_masking_failure(self):
        domain = FundamentalAssessmentDomain.FINANCIAL_HEALTH
        passing = finding(domain, "a_pass")
        failing = finding(
            domain,
            "b_fail",
            status=FundamentalFindingStatus.FAIL,
            severity=FundamentalFindingSeverity.HIGH,
            review_state=FundamentalReviewState.REQUIRED,
        )
        result = domain_assessment(domain, findings=(passing, failing))

        self.assertEqual(result.stance, FundamentalAssessmentStance.ADVERSE)
        self.assertTrue(result.review_required)
        self.assertEqual(
            result.coverage_status,
            FundamentalAssessmentCoverageStatus.COMPLETE,
        )

    def test_aggregate_requires_all_domains_in_canonical_order(self):
        domains = tuple(
            reversed(
                tuple(
                    domain_assessment(domain)
                    for domain in FundamentalAssessmentDomain
                )
            )
        )
        with self.assertRaisesRegex(
            ValidationError,
            "every domain in canonical order",
        ):
            assessment(domains=domains)

    def test_aggregate_rejects_stale_or_unbound_package_context(self):
        with self.assertRaisesRegex(
            ValidationError,
            "package ID must match its manifest",
        ):
            assessment(package_id="fundamental_package:wrong")
        with self.assertRaisesRegex(
            ValidationError,
            "active package",
        ):
            assessment(assessed_at=EXPIRES_AT)
        with self.assertRaisesRegex(
            ValidationError,
            "timestamps require timezone",
        ):
            assessment(assessed_at=ASSESSED_AT.replace(tzinfo=None))

    def test_round_trip_preserves_fingerprint_and_exact_findings(self):
        original = assessment()
        restored = FundamentalMandatoryAssessment.model_validate_json(
            original.model_dump_json(exclude_computed_fields=True)
        )

        self.assertEqual(restored, original)
        self.assertEqual(
            restored.assessment_fingerprint,
            original.assessment_fingerprint,
        )
        self.assertEqual(
            restored.domains[0].findings[0].finding_fingerprint,
            original.domains[0].findings[0].finding_fingerprint,
        )


if __name__ == "__main__":
    unittest.main()
