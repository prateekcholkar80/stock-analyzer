import unittest
from datetime import timedelta

from app.analytics.fundamental_checks import FinancialHealthSectorProfile
from app.analytics.fundamental_intelligence import (
    FUNDAMENTAL_NORMALIZATION_POLICY_ID,
    MANDATORY_ASSESSMENT_POLICY_BUNDLE_ID,
    build_shadow_fundamental_intelligence,
    normalize_committed_fundamental_package,
)
from app.analytics.fundamental_score_policies import (
    INITIAL_FUNDAMENTAL_SCORE_POLICY_BUNDLE_V1,
)
from app.analytics.fundamental_sector_profile import (
    FundamentalSectorEvidence,
    FundamentalSectorSourceAuthority,
    FundamentalSectorTaxonomy,
    classify_fundamental_sector_profile,
)
from hashlib import sha256
from app.models.fundamental_assessment import FundamentalAssessmentDomain
from app.models.fundamental_frameworks import FundamentalFramework
from app.models.fundamental_scoring import (
    FundamentalDecisionInfluenceMode,
)
from tests.unit.test_fundamental_applicability import comprehensive_evidence
from tests.unit.test_fundamental_evidence_coordinator import (
    FakeFundamentalGateway,
    FundamentalEvidenceCoordinatorTests,
)


def cited_non_financial_classification(evidence, assessed_at):
    sector_evidence = FundamentalSectorEvidence(
        issuer=evidence.issuer,
        taxonomy=FundamentalSectorTaxonomy.NSE_SECTOR_INDUSTRY,
        source_authority=(
            FundamentalSectorSourceAuthority.EXCHANGE_OFFICIAL
        ),
        source_reference_id="nse-sector-master:TCS",
        source_name="NSE equity sector master",
        source_location="https://www.nseindia.com/market-data",
        source_record_fingerprint=sha256(b"TCS|IT|Software").hexdigest(),
        sector="IT",
        industry="Software",
        retrieved_at=evidence.package_committed_at,
        expires_at=evidence.package_expires_at,
    )
    return classify_fundamental_sector_profile(
        package_id=evidence.package_id,
        manifest_fingerprint=evidence.manifest_fingerprint,
        issuer=evidence.issuer,
        package_expires_at=evidence.package_expires_at,
        evidence=(sector_evidence,),
        classified_at=assessed_at,
    )


def cited_bfsi_classification(evidence, assessed_at):
    source = FundamentalSectorEvidence(
        issuer=evidence.issuer,
        taxonomy=FundamentalSectorTaxonomy.NSE_SECTOR_INDUSTRY,
        source_authority=FundamentalSectorSourceAuthority.EXCHANGE_OFFICIAL,
        source_reference_id="nse-sector-master:TCS",
        source_name="NSE equity sector master",
        source_location="https://www.nseindia.com/market-data",
        source_record_fingerprint=sha256(
            b"TCS|Financial Services|Private Sector Bank"
        ).hexdigest(),
        sector="Financial Services",
        industry="Private Sector Bank",
        retrieved_at=evidence.package_committed_at,
        expires_at=evidence.package_expires_at,
    )
    return classify_fundamental_sector_profile(
        package_id=evidence.package_id,
        manifest_fingerprint=evidence.manifest_fingerprint,
        issuer=evidence.issuer,
        package_expires_at=evidence.package_expires_at,
        evidence=(source,),
        classified_at=assessed_at,
    )


class FundamentalIntelligenceTests(unittest.TestCase):
    def _committed_package(self):
        fixture = FundamentalEvidenceCoordinatorTests()
        fixture.setUp()
        coordinator = fixture.coordinator(FakeFundamentalGateway())
        package = fixture.publish_refresh(
            coordinator,
            fixture.complete_refresh(coordinator),
        )
        return package

    def test_normalizes_one_complete_package_with_exact_source_lineage(self):
        package = self._committed_package()
        normalized_at = package.manifest.committed_at

        first = normalize_committed_fundamental_package(
            package,
            normalized_at=normalized_at,
        )
        second = normalize_committed_fundamental_package(
            package,
            normalized_at=normalized_at,
        )

        self.assertEqual(first, second)
        self.assertEqual(first.package_id, package.package_id)
        self.assertEqual(
            first.package_aggregate_fingerprint,
            package.aggregate_fingerprint,
        )
        self.assertEqual(len(first.sources), 16)
        self.assertEqual(
            {source.source_reference_id for source in first.sources},
            {item.cache_entry_id for item in package.manifest.items},
        )
        self.assertEqual(
            first.normalization_policy_id,
            FUNDAMENTAL_NORMALIZATION_POLICY_ID,
        )
        self.assertGreater(len(first.metrics), 0)
        self.assertEqual(
            tuple(metric.key.canonical_key for metric in first.metrics),
            tuple(sorted(metric.key.canonical_key for metric in first.metrics)),
        )

    def test_package_normalization_rejects_expired_package(self):
        package = self._committed_package()

        with self.assertRaisesRegex(ValueError, "active committed package"):
            normalize_committed_fundamental_package(
                package,
                normalized_at=package.manifest.expires_at,
            )

    def test_runs_checks_applicability_then_all_six_shadow_policies(self):
        evidence = comprehensive_evidence()
        assessed_at = evidence.normalized_at + timedelta(seconds=1)

        result = build_shadow_fundamental_intelligence(
            evidence,
            sector_classification=cited_non_financial_classification(
                evidence,
                assessed_at,
            ),
            assessed_at=assessed_at,
        )

        self.assertEqual(
            tuple(domain.domain for domain in result.mandatory_assessment.domains),
            tuple(FundamentalAssessmentDomain),
        )
        self.assertEqual(
            result.mandatory_assessment.policy_bundle_id,
            MANDATORY_ASSESSMENT_POLICY_BUNDLE_ID,
        )
        self.assertEqual(
            result.applicability_assessment.mandatory_assessment_fingerprint,
            result.mandatory_assessment.assessment_fingerprint,
        )
        self.assertEqual(
            tuple(item.framework for item in result.scorecard.framework_results),
            tuple(FundamentalFramework),
        )
        self.assertEqual(
            result.scorecard.applicability_assessment_fingerprint,
            result.applicability_assessment.assessment_fingerprint,
        )
        self.assertEqual(
            result.scorecard.score_policy_bundle_fingerprint,
            INITIAL_FUNDAMENTAL_SCORE_POLICY_BUNDLE_V1.policy_fingerprint,
        )
        self.assertIs(
            result.scorecard.decision_influence_mode,
            FundamentalDecisionInfluenceMode.SHADOW,
        )
        self.assertIsNone(result.scorecard.approval_reference)

    def test_uses_cited_classification_bound_to_the_same_package(self):
        evidence = comprehensive_evidence()
        assessed_at = evidence.normalized_at + timedelta(seconds=1)
        classification = cited_non_financial_classification(
            evidence,
            assessed_at,
        )

        result = build_shadow_fundamental_intelligence(
            evidence,
            sector_classification=classification,
            assessed_at=assessed_at,
        )

        self.assertEqual(result.sector_classification, classification)
        self.assertEqual(
            result.sector_classification.profile,
            FinancialHealthSectorProfile.NON_FINANCIAL,
        )

    def test_rejects_classification_from_another_package(self):
        evidence = comprehensive_evidence()
        assessed_at = evidence.normalized_at + timedelta(seconds=1)
        classification = classify_fundamental_sector_profile(
            package_id=f"fundamental_package:{'b' * 64}",
            manifest_fingerprint="b" * 64,
            issuer=evidence.issuer,
            package_expires_at=evidence.package_expires_at,
            evidence=(),
            classified_at=assessed_at,
        )

        with self.assertRaisesRegex(ValueError, "must match normalized evidence"):
            build_shadow_fundamental_intelligence(
                evidence,
                sector_classification=classification,
                assessed_at=assessed_at,
            )

    def test_bfsi_classification_withholds_non_financial_check_policy(self):
        evidence = comprehensive_evidence()
        assessed_at = evidence.normalized_at + timedelta(seconds=1)

        result = build_shadow_fundamental_intelligence(
            evidence,
            sector_classification=cited_bfsi_classification(
                evidence,
                assessed_at,
            ),
            assessed_at=assessed_at,
        )

        financial_health = result.mandatory_assessment.domains[0]
        self.assertEqual(
            result.sector_classification.profile,
            FinancialHealthSectorProfile.BFSI,
        )
        self.assertEqual(financial_health.evaluated_rule_count, 0)
        self.assertTrue(
            all(
                finding.na_reason.value == "unsupported_policy"
                for finding in financial_health.findings
            )
        )

    def test_rejects_a_non_shadow_policy_bundle_before_assessment(self):
        evidence = comprehensive_evidence()
        advisory_bundle = (
            INITIAL_FUNDAMENTAL_SCORE_POLICY_BUNDLE_V1.model_copy(
                update={
                    "decision_influence_mode": (
                        FundamentalDecisionInfluenceMode.ADVISORY
                    ),
                    "approval_reference": "approval:test-only",
                }
            )
        )

        with self.assertRaisesRegex(ValueError, "shadow-only"):
            build_shadow_fundamental_intelligence(
                evidence,
                sector_classification=cited_non_financial_classification(
                    evidence,
                    evidence.normalized_at + timedelta(seconds=1),
                ),
                assessed_at=evidence.normalized_at + timedelta(seconds=1),
                policy_bundle=advisory_bundle,
            )

    def test_rejects_expired_evidence(self):
        evidence = comprehensive_evidence()
        classification = cited_non_financial_classification(
            evidence,
            evidence.normalized_at + timedelta(seconds=1),
        )

        with self.assertRaisesRegex(ValueError, "active normalized package"):
            build_shadow_fundamental_intelligence(
                evidence,
                sector_classification=classification,
                assessed_at=evidence.package_expires_at,
            )


if __name__ == "__main__":
    unittest.main()
