import unittest
from datetime import timedelta
from decimal import Decimal

from pydantic import ValidationError

from app.models.financial_documents import FinancialReportingBasis
from app.models.fundamental_assessment import (
    FundamentalAssessmentDomain,
    FundamentalFindingSeverity,
    FundamentalFindingStatus,
)
from app.models.fundamental_frameworks import (
    FundamentalFramework,
    FundamentalFrameworkApplicabilityStatus,
    FundamentalFrameworkEvidenceFamily,
)
from app.models.fundamental_metrics import FundamentalMetricPeriodKind
from app.models.fundamental_scoring import (
    FundamentalDecisionInfluenceMode,
    FundamentalEvidenceFamilyScoreResult,
    FundamentalEvidenceFamilyScoringPolicy,
    FundamentalFrameworkScoreResult,
    FundamentalFrameworkScoringPolicy,
    FundamentalMandatoryScoreConstraint,
    FundamentalMetricAggregation,
    FundamentalMetricScoreResult,
    FundamentalMetricScoreBand,
    FundamentalMetricScoringRule,
    FundamentalScoreGrade,
    FundamentalScoreGradeThresholds,
    FundamentalScorePolicyBundle,
    FundamentalScoreResultStatus,
    FundamentalScoreWithholdingReason,
    FundamentalScorecardResult,
    FundamentalScorecardStance,
    FundamentalStancePolicy,
)
from tests.unit.test_fundamental_gateway import build_issuer
from tests.unit.test_fundamental_metrics import (
    EXPIRES_AT,
    MANIFEST_FINGERPRINT,
    NORMALIZED_AT,
    citation,
    ratio_unit,
)


def bands():
    return (
        FundamentalMetricScoreBand(
            band_id="adverse",
            maximum_exclusive=Decimal("10"),
            score=Decimal("0"),
            grade=FundamentalScoreGrade.ADVERSE,
            summary="Observed value is adverse under this rule.",
        ),
        FundamentalMetricScoreBand(
            band_id="mixed",
            minimum_inclusive=Decimal("10"),
            maximum_exclusive=Decimal("20"),
            score=Decimal("50"),
            grade=FundamentalScoreGrade.MIXED,
            summary="Observed value is mixed under this rule.",
        ),
        FundamentalMetricScoreBand(
            band_id="strong",
            minimum_inclusive=Decimal("20"),
            score=Decimal("100"),
            grade=FundamentalScoreGrade.STRONG,
            summary="Observed value is strong under this rule.",
        ),
    )


def metric_rule(rule_id="capital_efficiency.roce", *, weight_bps=10_000):
    return FundamentalMetricScoringRule(
        rule_id=rule_id,
        rule_version="1.0.0",
        metric_ids=("ratio.roce",),
        expected_unit=ratio_unit(),
        allowed_reporting_bases=(
            FinancialReportingBasis.CONSOLIDATED,
            FinancialReportingBasis.STANDALONE,
        ),
        period_kind=FundamentalMetricPeriodKind.ANNUAL,
        minimum_observations=3,
        require_consecutive_periods=True,
        aggregation=FundamentalMetricAggregation.MEDIAN,
        component_weight_bps=weight_bps,
        bands=bands(),
    )


def family_policy(
    family=FundamentalFrameworkEvidenceFamily.CAPITAL_EFFICIENCY,
    *,
    framework_weight_bps=10_000,
):
    return FundamentalEvidenceFamilyScoringPolicy(
        family=family,
        framework_weight_bps=framework_weight_bps,
        minimum_rule_coverage_bps=10_000,
        metric_rules=(metric_rule(),),
    )


def framework_policy(framework, *, family=None):
    selected_family = family or FundamentalFrameworkEvidenceFamily.PROFITABILITY
    return FundamentalFrameworkScoringPolicy(
        framework=framework,
        policy_id=f"jarvis.framework_score.{framework.value}",
        policy_version="1.0.0",
        minimum_family_coverage_bps=10_000,
        family_policies=(family_policy(selected_family),),
        limitations=("Qualitative evidence remains outside this policy.",),
    )


def constraint(
    constraint_id="earnings_quality.fail",
    *,
    trigger_status=FundamentalFindingStatus.FAIL,
    minimum_severity=FundamentalFindingSeverity.HIGH,
    stance_ceiling=FundamentalScorecardStance.WEAK,
):
    return FundamentalMandatoryScoreConstraint(
        constraint_id=constraint_id,
        domain=FundamentalAssessmentDomain.EARNINGS_QUALITY,
        trigger_status=trigger_status,
        minimum_severity=minimum_severity,
        stance_ceiling=stance_ceiling,
        summary="A mandatory finding cannot be offset by framework scores.",
    )


def bundle(**updates):
    values = {
        "policy_bundle_id": "jarvis.fundamental_score_policy",
        "policy_bundle_version": "1.0.0",
        "decision_influence_mode": FundamentalDecisionInfluenceMode.SHADOW,
        "grade_thresholds": FundamentalScoreGradeThresholds(
            weak_minimum=Decimal("35"),
            mixed_minimum=Decimal("50"),
            favourable_minimum=Decimal("65"),
            strong_minimum=Decimal("80"),
        ),
        "stance_policy": FundamentalStancePolicy(
            minimum_scored_frameworks=2,
            healthy_minimum_frameworks=2,
            healthy_minimum_score=Decimal("65"),
            caution_minimum_score=Decimal("35"),
        ),
        "framework_policies": tuple(
            framework_policy(framework)
            for framework in FundamentalFramework
        ),
        "mandatory_constraints": (constraint(),),
        "limitations": (
            "The score policy begins in shadow mode.",
        ),
    }
    values.update(updates)
    return FundamentalScorePolicyBundle(**values)


def metric_score_result(
    framework=FundamentalFramework.BUFFETT,
    family=FundamentalFrameworkEvidenceFamily.PROFITABILITY,
    *,
    rule_id="profitability.pat",
    status=FundamentalScoreResultStatus.CALCULATED,
    component_weight_bps=10_000,
):
    values = {
        "result_id": (
            "fundamental.metric_score:"
            f"{framework.value}:{family.value}:{rule_id}"
        ),
        "framework": framework,
        "family": family,
        "rule_id": rule_id,
        "rule_version": "1.0.0",
        "rule_policy_fingerprint": "1" * 64,
        "status": status,
        "component_weight_bps": component_weight_bps,
    }
    if status is FundamentalScoreResultStatus.CALCULATED:
        values.update(
            {
                "metric_keys": (
                    "income.pat:consolidated:annual:fy2026",
                ),
                "citations": (
                    citation(
                        source_path=("profit_and_loss", "pat"),
                        period_key="fy2026",
                    ),
                ),
                "observed_value": Decimal("15"),
                "band_id": "mixed",
                "grade": FundamentalScoreGrade.MIXED,
                "score": Decimal("50"),
                "weighted_score": (
                    Decimal("50")
                    * Decimal(component_weight_bps)
                    / Decimal("10000")
                ).quantize(Decimal("0.0001")),
            }
        )
    elif status is FundamentalScoreResultStatus.NOT_APPLICABLE:
        values.update(
            {
                "withholding_reason": (
                    FundamentalScoreWithholdingReason.INELIGIBLE_FRAMEWORK
                ),
                "limitation": "The framework is ineligible.",
            }
        )
    else:
        values.update(
            {
                "withholding_reason": (
                    FundamentalScoreWithholdingReason.MISSING_EVIDENCE
                ),
                "limitation": "Required metric evidence is missing.",
            }
        )
    return FundamentalMetricScoreResult(**values)


def family_score_result(
    framework=FundamentalFramework.BUFFETT,
    family=FundamentalFrameworkEvidenceFamily.PROFITABILITY,
    *,
    status=FundamentalScoreResultStatus.CALCULATED,
    framework_weight_bps=10_000,
):
    metric_status = status
    metric = metric_score_result(
        framework,
        family,
        status=metric_status,
    )
    values = {
        "result_id": (
            f"fundamental.family_score:{framework.value}:{family.value}"
        ),
        "framework": framework,
        "family": family,
        "policy_fingerprint": "2" * 64,
        "status": status,
        "framework_weight_bps": framework_weight_bps,
        "minimum_rule_coverage_bps": 10_000,
        "maximum_family_score": Decimal("100"),
        "metric_results": (metric,),
    }
    if status is FundamentalScoreResultStatus.CALCULATED:
        values.update(
            {
                "score": Decimal("50"),
                "weighted_score": (
                    Decimal("50")
                    * Decimal(framework_weight_bps)
                    / Decimal("10000")
                ).quantize(Decimal("0.0001")),
            }
        )
    elif status is FundamentalScoreResultStatus.NOT_APPLICABLE:
        values.update(
            {
                "withholding_reason": (
                    FundamentalScoreWithholdingReason.INELIGIBLE_FRAMEWORK
                ),
                "limitations": ("The framework is ineligible.",),
            }
        )
    else:
        values.update(
            {
                "withholding_reason": (
                    FundamentalScoreWithholdingReason.INSUFFICIENT_COVERAGE
                ),
                "limitations": ("Family coverage is insufficient.",),
            }
        )
    return FundamentalEvidenceFamilyScoreResult(**values)


def framework_score_result(
    framework,
    *,
    status=FundamentalScoreResultStatus.CALCULATED,
    selected_primary=False,
):
    family_status = status
    values = {
        "result_id": f"fundamental.framework_score:{framework.value}",
        "framework": framework,
        "status": status,
        "applicability_status": (
            FundamentalFrameworkApplicabilityStatus.ELIGIBLE
            if status is FundamentalScoreResultStatus.CALCULATED
            else FundamentalFrameworkApplicabilityStatus.INELIGIBLE
            if status is FundamentalScoreResultStatus.NOT_APPLICABLE
            else FundamentalFrameworkApplicabilityStatus.PARTIAL
        ),
        "applicability_fingerprint": "3" * 64,
        "scoring_policy_id": f"jarvis.framework_score.{framework.value}",
        "scoring_policy_version": "1.0.0",
        "scoring_policy_fingerprint": "4" * 64,
        "minimum_family_coverage_bps": 10_000,
        "selected_primary": selected_primary,
        "family_results": (
            family_score_result(framework, status=family_status),
        ),
    }
    if status is FundamentalScoreResultStatus.CALCULATED:
        values.update(
            {
                "score": Decimal("50"),
                "grade": FundamentalScoreGrade.MIXED,
            }
        )
    elif status is FundamentalScoreResultStatus.NOT_APPLICABLE:
        values.update(
            {
                "withholding_reason": (
                    FundamentalScoreWithholdingReason.INELIGIBLE_FRAMEWORK
                ),
                "limitations": ("The framework is ineligible.",),
            }
        )
    else:
        values.update(
            {
                "withholding_reason": (
                    FundamentalScoreWithholdingReason.MISSING_EVIDENCE
                ),
                "limitations": ("Framework evidence is incomplete.",),
            }
        )
    return FundamentalFrameworkScoreResult(**values)


def scorecard(**updates):
    selected = (
        FundamentalFramework.BUFFETT,
        FundamentalFramework.LYNCH,
    )
    values = {
        "package_id": f"fundamental_package:{MANIFEST_FINGERPRINT}",
        "manifest_fingerprint": MANIFEST_FINGERPRINT,
        "normalized_evidence_fingerprint": "5" * 64,
        "mandatory_assessment_fingerprint": "6" * 64,
        "applicability_assessment_fingerprint": "7" * 64,
        "score_policy_bundle_id": "jarvis.fundamental_score_policy",
        "score_policy_bundle_version": "1.0.0",
        "score_policy_bundle_fingerprint": "8" * 64,
        "issuer": build_issuer(),
        "evidence_normalized_at": NORMALIZED_AT,
        "mandatory_assessed_at": NORMALIZED_AT + timedelta(seconds=1),
        "applicability_assessed_at": NORMALIZED_AT + timedelta(seconds=2),
        "scored_at": NORMALIZED_AT + timedelta(seconds=3),
        "package_expires_at": EXPIRES_AT,
        "decision_influence_mode": FundamentalDecisionInfluenceMode.SHADOW,
        "framework_results": tuple(
            framework_score_result(
                framework,
                selected_primary=framework in selected,
            )
            for framework in FundamentalFramework
        ),
        "selected_primary_frameworks": selected,
        "stance": FundamentalScorecardStance.CAUTION,
        "review_required": False,
        "limitations": (
            "The scorecard is preliminary and operates in shadow mode.",
        ),
    }
    values.update(updates)
    return FundamentalScorecardResult(**values)


class MetricScoringPolicyTests(unittest.TestCase):
    def test_metric_rule_is_unit_basis_and_interval_explicit(self):
        result = metric_rule()

        self.assertEqual(result.minimum_observations, 3)
        self.assertEqual(result.period_kind, FundamentalMetricPeriodKind.ANNUAL)
        self.assertTrue(result.require_consecutive_periods)
        self.assertEqual(result.aggregation, FundamentalMetricAggregation.MEDIAN)
        self.assertEqual(result.component_weight_bps, 10_000)
        self.assertEqual(result.bands[1].minimum_inclusive, Decimal("10"))
        self.assertEqual(len(result.policy_fingerprint), 64)

    def test_bands_must_cover_the_number_line_without_gaps(self):
        invalid_bands = list(bands())
        invalid_bands[1] = invalid_bands[1].model_copy(
            update={"minimum_inclusive": Decimal("11")}
        )

        with self.assertRaisesRegex(ValidationError, "contiguous"):
            FundamentalMetricScoringRule(
                **{
                    **metric_rule().model_dump(
                        exclude={"bands", "policy_fingerprint"}
                    ),
                    "bands": tuple(invalid_bands),
                }
            )

    def test_metric_ids_and_reporting_bases_are_canonical(self):
        source = metric_rule()
        values = source.model_dump(exclude={"policy_fingerprint"})

        with self.assertRaisesRegex(ValidationError, "canonical order"):
            FundamentalMetricScoringRule(
                **{
                    **values,
                    "metric_ids": ("ratio.roe", "ratio.roce"),
                }
            )

    def test_period_semantics_are_explicit_and_fail_closed(self):
        values = metric_rule().model_dump(exclude_computed_fields=True)

        with self.assertRaisesRegex(ValidationError, "cannot be unknown"):
            FundamentalMetricScoringRule(
                **{
                    **values,
                    "period_kind": FundamentalMetricPeriodKind.UNKNOWN,
                }
            )
        with self.assertRaisesRegex(ValidationError, "point-in-time"):
            FundamentalMetricScoringRule(
                **{
                    **values,
                    "period_kind": (
                        FundamentalMetricPeriodKind.POINT_IN_TIME
                    ),
                    "require_consecutive_periods": True,
                }
            )
        with self.assertRaisesRegex(ValidationError, "canonical order"):
            FundamentalMetricScoringRule(
                **{
                    **values,
                    "allowed_reporting_bases": (
                        FinancialReportingBasis.STANDALONE,
                        FinancialReportingBasis.CONSOLIDATED,
                    ),
                }
            )


class FamilyAndFrameworkPolicyTests(unittest.TestCase):
    def test_family_component_weights_must_total_10000_bps(self):
        with self.assertRaisesRegex(ValidationError, "total 10000"):
            FundamentalEvidenceFamilyScoringPolicy(
                family=FundamentalFrameworkEvidenceFamily.PROFITABILITY,
                framework_weight_bps=10_000,
                minimum_rule_coverage_bps=10_000,
                metric_rules=(metric_rule(weight_bps=9_999),),
            )

    def test_framework_family_weights_are_capped_and_canonical(self):
        with self.assertRaisesRegex(ValidationError, "total 10000"):
            FundamentalFrameworkScoringPolicy(
                framework=FundamentalFramework.BUFFETT,
                policy_id="jarvis.framework_score.buffett",
                policy_version="1.0.0",
                minimum_family_coverage_bps=10_000,
                family_policies=(
                    family_policy(framework_weight_bps=9_999),
                ),
            )

        with self.assertRaisesRegex(ValidationError, "canonical order"):
            FundamentalFrameworkScoringPolicy(
                framework=FundamentalFramework.BUFFETT,
                policy_id="jarvis.framework_score.buffett",
                policy_version="1.0.0",
                minimum_family_coverage_bps=10_000,
                family_policies=(
                    family_policy(
                        FundamentalFrameworkEvidenceFamily.GROWTH,
                        framework_weight_bps=5_000,
                    ),
                    family_policy(
                        FundamentalFrameworkEvidenceFamily.PROFITABILITY,
                        framework_weight_bps=5_000,
                    ),
                ),
            )

    def test_framework_policy_requires_stable_identity_and_eligibility(self):
        source = framework_policy(FundamentalFramework.BUFFETT)
        values = source.model_dump(exclude_computed_fields=True)

        with self.assertRaisesRegex(ValidationError, "not stable"):
            FundamentalFrameworkScoringPolicy(
                **{**values, "policy_id": "jarvis.framework_score.lynch"}
            )
        with self.assertRaises(ValidationError):
            FundamentalFrameworkScoringPolicy(
                **{**values, "requires_eligible_applicability": False}
            )


class ScorePolicyBundleTests(unittest.TestCase):
    def test_builds_frozen_canonical_shadow_policy(self):
        result = bundle()

        self.assertEqual(
            tuple(item.framework for item in result.framework_policies),
            tuple(FundamentalFramework),
        )
        self.assertEqual(
            result.decision_influence_mode,
            FundamentalDecisionInfluenceMode.SHADOW,
        )
        self.assertEqual(len(result.policy_fingerprint), 64)
        with self.assertRaises(ValidationError):
            result.framework_policies = ()

    def test_bundle_requires_every_framework_in_canonical_order(self):
        policies = list(bundle().framework_policies)

        with self.assertRaisesRegex(ValidationError, "canonical order"):
            bundle(framework_policies=tuple(reversed(policies)))
        with self.assertRaises(ValidationError):
            bundle(framework_policies=tuple(policies[:-1]))

    def test_non_shadow_modes_require_explicit_approval(self):
        with self.assertRaisesRegex(ValidationError, "approval reference"):
            bundle(
                decision_influence_mode=(
                    FundamentalDecisionInfluenceMode.ADVISORY
                )
            )

        approved = bundle(
            decision_influence_mode=FundamentalDecisionInfluenceMode.ADVISORY,
            approval_reference="approval:fundamental-advisory-v1",
        )
        self.assertEqual(
            approved.decision_influence_mode,
            FundamentalDecisionInfluenceMode.ADVISORY,
        )

    def test_mandatory_constraints_cannot_target_pass_or_na(self):
        with self.assertRaisesRegex(ValidationError, "WARN or FAIL"):
            constraint(trigger_status=FundamentalFindingStatus.PASS)
        with self.assertRaisesRegex(ValidationError, "WARN or FAIL"):
            constraint(trigger_status=FundamentalFindingStatus.NA)

    def test_stance_policy_does_not_average_framework_scores(self):
        result = bundle().stance_policy

        self.assertEqual(result.aggregation_method, "selected_framework_count")
        self.assertEqual(result.minimum_scored_frameworks, 2)

    def test_policy_fingerprint_is_stable_and_change_sensitive(self):
        first = bundle()
        second = bundle()
        changed = bundle(
            limitations=("A different versioned policy limitation.",)
        )

        self.assertEqual(first.policy_fingerprint, second.policy_fingerprint)
        self.assertNotEqual(
            first.policy_fingerprint,
            changed.policy_fingerprint,
        )


class MetricScoreResultTests(unittest.TestCase):
    def test_calculated_metric_result_is_cited_weighted_and_frozen(self):
        result = metric_score_result(component_weight_bps=5_000)

        self.assertEqual(result.score, Decimal("50"))
        self.assertEqual(result.weighted_score, Decimal("25.0000"))
        self.assertTrue(result.metric_keys)
        self.assertTrue(result.citations)
        self.assertEqual(len(result.result_fingerprint), 64)
        with self.assertRaises(ValidationError):
            result.score = Decimal("100")

    def test_metric_result_rejects_unsubstantiated_or_wrong_arithmetic(self):
        source = metric_score_result()
        values = source.model_dump(exclude_computed_fields=True)

        with self.assertRaisesRegex(ValidationError, "does not match policy"):
            FundamentalMetricScoreResult(
                **{**values, "weighted_score": Decimal("49.9999")}
            )
        with self.assertRaisesRegex(ValidationError, "requires cited evidence"):
            FundamentalMetricScoreResult(
                **{**values, "metric_keys": (), "citations": ()}
            )

    def test_only_conflicting_withheld_metric_retains_citations(self):
        source = metric_score_result(status=FundamentalScoreResultStatus.WITHHELD)
        values = source.model_dump(exclude_computed_fields=True)
        cited_values = {
            **values,
            "metric_keys": ("income.pat:consolidated:annual:fy2026",),
            "citations": (citation(),),
        }

        with self.assertRaisesRegex(ValidationError, "only conflicting"):
            FundamentalMetricScoreResult(**cited_values)

        conflict = FundamentalMetricScoreResult(
            **{
                **cited_values,
                "withholding_reason": (
                    FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE
                ),
            }
        )
        self.assertEqual(
            conflict.withholding_reason,
            FundamentalScoreWithholdingReason.CONFLICTING_EVIDENCE,
        )

        with self.assertRaisesRegex(ValidationError, "only conflicting"):
            FundamentalMetricScoreResult(
                **{
                    **values,
                    "metric_keys": (
                        "income.pat:consolidated:annual:fy2026",
                    ),
                }
            )


class EvidenceFamilyScoreResultTests(unittest.TestCase):
    def test_family_result_derives_coverage_and_validates_both_weight_layers(self):
        result = family_score_result(framework_weight_bps=4_000)

        self.assertEqual(result.rule_coverage_bps, 10_000)
        self.assertEqual(result.score, Decimal("50"))
        self.assertEqual(result.weighted_score, Decimal("20.0000"))
        self.assertEqual(len(result.result_fingerprint), 64)

    def test_family_result_rejects_claimed_score_without_required_coverage(self):
        calculated = metric_score_result(
            rule_id="profitability.a",
            component_weight_bps=5_000,
        )
        missing = metric_score_result(
            rule_id="profitability.b",
            status=FundamentalScoreResultStatus.WITHHELD,
            component_weight_bps=5_000,
        )

        with self.assertRaisesRegex(ValidationError, "insufficient coverage"):
            FundamentalEvidenceFamilyScoreResult(
                result_id=(
                    "fundamental.family_score:buffett:profitability"
                ),
                framework=FundamentalFramework.BUFFETT,
                family=FundamentalFrameworkEvidenceFamily.PROFITABILITY,
                policy_fingerprint="2" * 64,
                status=FundamentalScoreResultStatus.CALCULATED,
                framework_weight_bps=10_000,
                minimum_rule_coverage_bps=10_000,
                maximum_family_score=Decimal("100"),
                metric_results=(calculated, missing),
                score=Decimal("25"),
                weighted_score=Decimal("25"),
            )

    def test_not_applicable_family_requires_a_closed_metric_chain(self):
        source = family_score_result(
            status=FundamentalScoreResultStatus.NOT_APPLICABLE
        )
        self.assertEqual(source.rule_coverage_bps, 0)

        values = source.model_dump(exclude_computed_fields=True)
        with self.assertRaisesRegex(ValidationError, "cannot contain released"):
            FundamentalEvidenceFamilyScoreResult(
                **{
                    **values,
                    "metric_results": (metric_score_result(),),
                }
            )


class FrameworkScoreResultTests(unittest.TestCase):
    def test_framework_result_validates_applicability_coverage_and_arithmetic(self):
        result = framework_score_result(
            FundamentalFramework.BUFFETT,
            selected_primary=True,
        )

        self.assertEqual(result.family_coverage_bps, 10_000)
        self.assertEqual(result.score, Decimal("50"))
        self.assertEqual(result.grade, FundamentalScoreGrade.MIXED)
        self.assertEqual(len(result.result_fingerprint), 64)

    def test_partial_or_ineligible_framework_cannot_release_a_score(self):
        source = framework_score_result(FundamentalFramework.BUFFETT)
        values = source.model_dump(exclude_computed_fields=True)

        with self.assertRaisesRegex(ValidationError, "only eligible"):
            FundamentalFrameworkScoreResult(
                **{
                    **values,
                    "applicability_status": (
                        FundamentalFrameworkApplicabilityStatus.PARTIAL
                    ),
                }
            )
        with self.assertRaisesRegex(ValidationError, "only eligible"):
            FundamentalFrameworkScoreResult(
                **{
                    **values,
                    "applicability_status": (
                        FundamentalFrameworkApplicabilityStatus.INELIGIBLE
                    ),
                    "selected_primary": True,
                }
            )

    def test_not_applicable_framework_preserves_explicit_reason(self):
        result = framework_score_result(
            FundamentalFramework.CAN_SLIM,
            status=FundamentalScoreResultStatus.NOT_APPLICABLE,
        )

        self.assertEqual(
            result.withholding_reason,
            FundamentalScoreWithholdingReason.INELIGIBLE_FRAMEWORK,
        )
        self.assertEqual(result.family_coverage_bps, 0)

    def test_calculated_framework_can_preserve_non_blocking_limitations(self):
        source = framework_score_result(FundamentalFramework.BUFFETT)
        values = source.model_dump(exclude_computed_fields=True)

        result = FundamentalFrameworkScoreResult(
            **{
                **values,
                "limitations": (
                    "Qualitative moat evidence remains unavailable.",
                ),
            }
        )
        self.assertEqual(len(result.limitations), 1)


class AggregateScorecardResultTests(unittest.TestCase):
    def test_scorecard_preserves_lineage_selection_and_fingerprint(self):
        result = scorecard()

        self.assertEqual(
            tuple(item.framework for item in result.framework_results),
            tuple(FundamentalFramework),
        )
        self.assertEqual(
            result.selected_primary_frameworks,
            (
                FundamentalFramework.BUFFETT,
                FundamentalFramework.LYNCH,
            ),
        )
        self.assertEqual(len(result.scorecard_fingerprint), 64)

    def test_scorecard_rejects_selection_or_package_lineage_mismatch(self):
        with self.assertRaisesRegex(ValidationError, "must match"):
            scorecard(
                selected_primary_frameworks=(FundamentalFramework.BUFFETT,)
            )
        with self.assertRaisesRegex(ValidationError, "package ID"):
            scorecard(package_id="fundamental_package:wrong")

    def test_scorecard_without_selected_scores_is_insufficient(self):
        results = tuple(
            framework_score_result(
                framework,
                status=FundamentalScoreResultStatus.WITHHELD,
            )
            for framework in FundamentalFramework
        )

        result = scorecard(
            framework_results=results,
            selected_primary_frameworks=(),
            stance=FundamentalScorecardStance.INSUFFICIENT_EVIDENCE,
            review_required=True,
        )
        self.assertEqual(
            result.stance,
            FundamentalScorecardStance.INSUFFICIENT_EVIDENCE,
        )
        with self.assertRaisesRegex(ValidationError, "insufficient stance"):
            scorecard(
                framework_results=results,
                selected_primary_frameworks=(),
                stance=FundamentalScorecardStance.CAUTION,
            )

    def test_healthy_scorecard_cannot_hide_review_or_gate_approval(self):
        with self.assertRaisesRegex(ValidationError, "cannot require review"):
            scorecard(
                stance=FundamentalScorecardStance.HEALTHY,
                review_required=True,
            )
        with self.assertRaisesRegex(ValidationError, "approval reference"):
            scorecard(
                decision_influence_mode=(
                    FundamentalDecisionInfluenceMode.APPROVED_GATE
                )
            )

    def test_identical_aggregate_inputs_have_identical_fingerprints(self):
        first = scorecard()
        second = scorecard()
        changed = scorecard(review_required=True)

        self.assertEqual(
            first.scorecard_fingerprint,
            second.scorecard_fingerprint,
        )
        self.assertNotEqual(
            first.scorecard_fingerprint,
            changed.scorecard_fingerprint,
        )


if __name__ == "__main__":
    unittest.main()
