import unittest
from datetime import timedelta
from decimal import Decimal

from app.analytics.fundamental_applicability import (
    assess_fundamental_framework_applicability,
)
from app.analytics.fundamental_checks import FinancialHealthSectorProfile
from app.analytics.fundamental_scorecard import build_fundamental_scorecard
from app.models.fundamental_assessment import (
    FundamentalAssessmentDomain,
    FundamentalFindingSeverity,
    FundamentalFindingStatus,
    FundamentalReviewState,
)
from app.models.fundamental_frameworks import (
    FundamentalFramework,
    FundamentalFrameworkApplicabilityStatus,
    FundamentalFrameworkEvidenceStatus,
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
    FundamentalScoreResultStatus,
    FundamentalScoreWithholdingReason,
    FundamentalScorecardStance,
    FundamentalStancePolicy,
)
from tests.unit.test_fundamental_applicability import (
    comprehensive_evidence,
    mandatory_for,
)
from tests.unit.test_fundamental_assessment import (
    assessment as mandatory_assessment_fixture,
    domain_assessment,
    finding,
)


def assess_applicability(evidence, mandatory):
    return assess_fundamental_framework_applicability(
        evidence,
        mandatory,
        sector_profile=FinancialHealthSectorProfile.NON_FINANCIAL,
        assessed_at=mandatory.assessed_at + timedelta(seconds=1),
    )


def failed_mandatory_for(evidence):
    failed_finding = finding(
        FundamentalAssessmentDomain.FINANCIAL_HEALTH,
        "debt_risk",
        status=FundamentalFindingStatus.FAIL,
        severity=FundamentalFindingSeverity.HIGH,
        review_state=FundamentalReviewState.REQUIRED,
        summary="Debt risk breached the deterministic policy threshold.",
    )
    domains = tuple(
        domain_assessment(
            domain,
            findings=(failed_finding,)
            if domain is FundamentalAssessmentDomain.FINANCIAL_HEALTH
            else None,
        )
        for domain in FundamentalAssessmentDomain
    )
    return mandatory_assessment_fixture(
        package_id=evidence.package_id,
        manifest_fingerprint=evidence.manifest_fingerprint,
        normalized_evidence_fingerprint=evidence.evidence_fingerprint,
        issuer=evidence.issuer,
        evidence_normalized_at=evidence.normalized_at,
        package_expires_at=evidence.package_expires_at,
        assessed_at=evidence.normalized_at + timedelta(seconds=1),
        domains=domains,
    )


def positive_bands(score=Decimal("80")):
    return (
        FundamentalMetricScoreBand(
            band_id="adverse",
            maximum_exclusive=Decimal("0"),
            score=Decimal("0"),
            grade=FundamentalScoreGrade.ADVERSE,
            summary="The metric is below zero.",
        ),
        FundamentalMetricScoreBand(
            band_id="positive",
            minimum_inclusive=Decimal("0"),
            score=score,
            grade=FundamentalScoreGrade.STRONG,
            summary="The metric is non-negative.",
        ),
    )


def score_policy_for(evidence, applicability, *, family_cap=Decimal("100")):
    by_key = {metric.key.canonical_key: metric for metric in evidence.metrics}
    fallback = evidence.metrics[0]
    framework_policies = []
    for framework_result in applicability.results:
        family_policies = []
        family_weight = 10_000 // len(framework_result.evidence_coverage)
        remainder = 10_000 - (
            family_weight * len(framework_result.evidence_coverage)
        )
        for index, coverage in enumerate(framework_result.evidence_coverage):
            metric = next(
                (
                    by_key[key]
                    for key in coverage.metric_keys
                    if key in by_key
                ),
                fallback,
            )
            family_policies.append(
                FundamentalEvidenceFamilyScoringPolicy(
                    family=coverage.family,
                    framework_weight_bps=family_weight
                    + (remainder if index == 0 else 0),
                    minimum_rule_coverage_bps=10_000,
                    maximum_family_score=family_cap,
                    metric_rules=(
                        FundamentalMetricScoringRule(
                            rule_id=(
                                f"{framework_result.framework.value}."
                                f"{coverage.family.value}.primary"
                            ),
                            rule_version="1.0.0",
                            metric_ids=(metric.key.metric_id,),
                            expected_unit=metric.unit,
                            allowed_reporting_bases=(
                                metric.key.reporting_basis,
                            ),
                            period_kind=metric.period.period_kind,
                            minimum_observations=1,
                            require_consecutive_periods=False,
                            aggregation=FundamentalMetricAggregation.LATEST,
                            component_weight_bps=10_000,
                            bands=positive_bands(),
                        ),
                    ),
                )
            )
        framework_policies.append(
            FundamentalFrameworkScoringPolicy(
                framework=framework_result.framework,
                policy_id=(
                    "jarvis.framework_score."
                    f"{framework_result.framework.value}"
                ),
                policy_version="1.0.0",
                minimum_family_coverage_bps=10_000,
                family_policies=tuple(family_policies),
            )
        )
    return FundamentalScorePolicyBundle(
        policy_bundle_id="jarvis.fundamental_scorecard",
        policy_bundle_version="1.0.0",
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
        framework_policies=tuple(framework_policies),
        mandatory_constraints=(
            FundamentalMandatoryScoreConstraint(
                constraint_id="financial_health.high_fail",
                domain=FundamentalAssessmentDomain.FINANCIAL_HEALTH,
                trigger_status=FundamentalFindingStatus.FAIL,
                minimum_severity=FundamentalFindingSeverity.HIGH,
                stance_ceiling=FundamentalScorecardStance.WEAK,
                summary="A high-severity financial-health failure caps stance.",
            ),
        ),
        limitations=("Version 1 policies run in shadow mode.",),
    )


def scored_fixture(*, mandatory=None, family_cap=Decimal("100")):
    evidence = comprehensive_evidence()
    mandatory = mandatory or mandatory_for(evidence)
    applicability = assess_applicability(evidence, mandatory)
    policy = score_policy_for(
        evidence,
        applicability,
        family_cap=family_cap,
    )
    result = build_fundamental_scorecard(
        evidence,
        mandatory,
        applicability,
        policy,
        scored_at=applicability.assessed_at + timedelta(seconds=1),
    )
    return evidence, mandatory, applicability, policy, result


class FundamentalScorecardTests(unittest.TestCase):
    def test_releases_cited_scores_for_selected_eligible_frameworks(self):
        evidence, _, applicability, policy, result = scored_fixture()

        self.assertEqual(result.package_id, evidence.package_id)
        self.assertEqual(
            result.applicability_assessment_fingerprint,
            applicability.assessment_fingerprint,
        )
        self.assertEqual(
            result.score_policy_bundle_fingerprint,
            policy.policy_fingerprint,
        )
        self.assertEqual(result.stance, FundamentalScorecardStance.HEALTHY)
        self.assertFalse(result.review_required)
        self.assertEqual(
            result.decision_influence_mode,
            FundamentalDecisionInfluenceMode.SHADOW,
        )
        selected = tuple(
            item
            for item in result.framework_results
            if item.selected_primary
        )
        self.assertEqual(len(selected), 3)
        self.assertTrue(
            all(
                item.status is FundamentalScoreResultStatus.CALCULATED
                and item.score == Decimal("80.00")
                and item.grade is FundamentalScoreGrade.STRONG
                for item in selected
            )
        )
        self.assertTrue(
            all(
                metric.citations and metric.metric_keys
                for item in selected
                for family in item.family_results
                for metric in family.metric_results
            )
        )

    def test_same_inputs_produce_the_same_immutable_fingerprint(self):
        evidence, mandatory, applicability, policy, result = scored_fixture()
        repeated = build_fundamental_scorecard(
            evidence,
            mandatory,
            applicability,
            policy,
            scored_at=result.scored_at,
        )

        self.assertEqual(result, repeated)
        self.assertEqual(
            result.scorecard_fingerprint,
            repeated.scorecard_fingerprint,
        )

    def test_partial_frameworks_are_withheld_instead_of_neutral_scored(self):
        _, _, applicability, _, result = scored_fixture()
        by_framework = {item.framework: item for item in result.framework_results}

        partial_frameworks = tuple(
            item.framework
            for item in applicability.results
            if item.status is FundamentalFrameworkApplicabilityStatus.PARTIAL
        )
        self.assertTrue(partial_frameworks)
        for framework in partial_frameworks:
            score = by_framework[framework]
            self.assertEqual(score.status, FundamentalScoreResultStatus.WITHHELD)
            self.assertIsNone(score.score)
            self.assertIn(
                score.withholding_reason,
                {
                    FundamentalScoreWithholdingReason.MISSING_EVIDENCE,
                    FundamentalScoreWithholdingReason.UNSUPPORTED_POLICY,
                },
            )

    def test_family_cap_prevents_correlated_evidence_overstatement(self):
        _, _, _, _, result = scored_fixture(family_cap=Decimal("50"))
        selected = tuple(
            item
            for item in result.framework_results
            if item.selected_primary
        )

        self.assertTrue(
            all(item.score == Decimal("50.00") for item in selected)
        )
        self.assertEqual(result.stance, FundamentalScorecardStance.CAUTION)

    def test_mandatory_failure_caps_stance_without_changing_scores(self):
        evidence = comprehensive_evidence()
        failed_mandatory = failed_mandatory_for(evidence)
        _, _, _, _, result = scored_fixture(mandatory=failed_mandatory)

        self.assertEqual(result.stance, FundamentalScorecardStance.WEAK)
        self.assertTrue(result.review_required)
        selected_scores = tuple(
            item.score
            for item in result.framework_results
            if item.selected_primary
        )
        self.assertTrue(selected_scores)
        self.assertEqual(set(selected_scores), {Decimal("80.00")})

    def test_rejects_mixed_package_lineage(self):
        evidence, mandatory, applicability, policy, _ = scored_fixture()
        mismatched = mandatory.model_copy(
            update={"normalized_evidence_fingerprint": "f" * 64}
        )

        with self.assertRaisesRegex(ValueError, "do not share one package"):
            build_fundamental_scorecard(
                evidence,
                mismatched,
                applicability,
                policy,
                scored_at=applicability.assessed_at + timedelta(seconds=1),
            )

    def test_rejects_scoring_outside_the_active_package_window(self):
        evidence, mandatory, applicability, policy, _ = scored_fixture()

        with self.assertRaisesRegex(ValueError, "current completed"):
            build_fundamental_scorecard(
                evidence,
                mandatory,
                applicability,
                policy,
                scored_at=evidence.package_expires_at,
            )

    def test_policy_must_cover_exact_applicability_families(self):
        evidence, mandatory, applicability, policy, _ = scored_fixture()
        first_policy = policy.framework_policies[0]
        altered = first_policy.model_copy(
            update={"family_policies": first_policy.family_policies[:-1]}
        )
        broken_policy = policy.model_copy(
            update={
                "framework_policies": (
                    altered,
                    *policy.framework_policies[1:],
                )
            }
        )

        with self.assertRaisesRegex(ValueError, "exact applicability families"):
            build_fundamental_scorecard(
                evidence,
                mandatory,
                applicability,
                broken_policy,
                scored_at=applicability.assessed_at + timedelta(seconds=1),
            )

    def test_unavailable_family_status_never_fabricates_a_score(self):
        _, _, applicability, _, result = scored_fixture()
        by_framework = {item.framework: item for item in result.framework_results}
        target = next(
            item
            for item in applicability.results
            if any(
                coverage.status
                is not FundamentalFrameworkEvidenceStatus.AVAILABLE
                for coverage in item.evidence_coverage
            )
        )
        framework_result = by_framework[target.framework]

        withheld = tuple(
            family
            for family in framework_result.family_results
            if family.status is not FundamentalScoreResultStatus.CALCULATED
        )
        self.assertTrue(withheld)
        self.assertTrue(all(family.score is None for family in withheld))


if __name__ == "__main__":
    unittest.main()
