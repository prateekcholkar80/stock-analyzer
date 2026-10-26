import unittest
from decimal import Decimal

from pydantic import ValidationError

from app.analytics.fundamental_score_policies import (
    BUFFETT_SCORING_POLICY_V1,
    CAN_SLIM_SCORING_POLICY_V1,
    INITIAL_FUNDAMENTAL_SCORE_POLICY_BUNDLE_V1,
    INITIAL_FRAMEWORK_SCORE_POLICY_VERSION,
    JHUNJHUNWALA_STYLE_SCORING_POLICY_V1,
    LYNCH_SCORING_POLICY_V1,
    ONE_HUNDRED_BAGGER_SCORING_POLICY_V1,
    VALUATION_SCORING_POLICY_V1,
    initial_buffett_lynch_score_policies,
    initial_framework_score_policies,
)
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
from app.models.fundamental_metrics import FundamentalMetricPeriodKind
from app.models.fundamental_scoring import (
    FundamentalDecisionInfluenceMode,
    FundamentalFrameworkScoringPolicy,
    FundamentalMetricScoringRule,
    FundamentalScoreGrade,
    FundamentalScorecardStance,
)
from app.models.fundamentals import FundamentalValueKind


def rules_by_id(
    policy: FundamentalFrameworkScoringPolicy,
) -> dict[str, FundamentalMetricScoringRule]:
    return {
        rule.rule_id: rule
        for family in policy.family_policies
        for rule in family.metric_rules
    }


def score_for(rule: FundamentalMetricScoringRule, value: str):
    observed = Decimal(value)
    matches = tuple(
        band
        for band in rule.bands
        if (
            band.minimum_inclusive is None
            or observed >= band.minimum_inclusive
        )
        and (
            band.maximum_exclusive is None
            or observed < band.maximum_exclusive
        )
    )
    if len(matches) != 1:
        raise AssertionError("golden value did not resolve exactly one band")
    return matches[0].score, matches[0].grade


class InitialFrameworkScorePolicyTests(unittest.TestCase):
    def test_returns_only_buffett_and_lynch_in_canonical_order(self):
        policies = initial_buffett_lynch_score_policies()

        self.assertEqual(
            tuple(policy.framework for policy in policies),
            (FundamentalFramework.BUFFETT, FundamentalFramework.LYNCH),
        )
        self.assertIs(policies[0], BUFFETT_SCORING_POLICY_V1)
        self.assertIs(policies[1], LYNCH_SCORING_POLICY_V1)

    def test_policy_identities_and_fingerprints_are_golden(self):
        self.assertEqual(INITIAL_FRAMEWORK_SCORE_POLICY_VERSION, "1.0.0")
        self.assertEqual(
            BUFFETT_SCORING_POLICY_V1.policy_id,
            "jarvis.framework_score.buffett",
        )
        self.assertEqual(
            LYNCH_SCORING_POLICY_V1.policy_id,
            "jarvis.framework_score.lynch",
        )
        self.assertEqual(
            BUFFETT_SCORING_POLICY_V1.policy_fingerprint,
            "674ff2af5164df04dcfc01f4d917ef70cea42eba5a1ce60788f31ff9b7f93302",
        )
        self.assertEqual(
            LYNCH_SCORING_POLICY_V1.policy_fingerprint,
            "7a57f22ded55f086efae5d5c1b3ab3ac82f23bee96d023eb49464f7bd087915f",
        )

    def test_returns_all_implemented_policies_in_canonical_order(self):
        policies = initial_framework_score_policies()

        self.assertEqual(
            tuple(policy.framework for policy in policies),
            (
                FundamentalFramework.BUFFETT,
                FundamentalFramework.LYNCH,
                FundamentalFramework.JHUNJHUNWALA_STYLE,
                FundamentalFramework.ONE_HUNDRED_BAGGER,
                FundamentalFramework.CAN_SLIM,
                FundamentalFramework.VALUATION,
            ),
        )
        self.assertEqual(
            tuple(policy.policy_fingerprint for policy in policies[2:]),
            (
                "811e5a0a2e4e3e1cafc6e421e202b6eaaee464b5faf933e45678765f33bba98f",
                "4969e4650bc354bffb47e18f5734d9d1b7b14209763b999937b4e46faf7f444e",
                "8d6fe9d609e55edba89a54cf0280a0dc4ec7376e8a7813b4e83e37d32c34442f",
                "a8debf4765643a7e88a8e0af83ae3474616c0599937d4957cb58c19a183777cc",
            ),
        )

    def test_buffett_uses_required_evidence_families_and_weights(self):
        policy = BUFFETT_SCORING_POLICY_V1

        self.assertEqual(
            tuple(family.family for family in policy.family_policies),
            (
                FundamentalFrameworkEvidenceFamily.PROFITABILITY,
                FundamentalFrameworkEvidenceFamily.CAPITAL_EFFICIENCY,
                FundamentalFrameworkEvidenceFamily.BALANCE_SHEET,
                FundamentalFrameworkEvidenceFamily.CASH_CONVERSION,
                FundamentalFrameworkEvidenceFamily.REINVESTMENT,
            ),
        )
        self.assertEqual(
            tuple(
                family.framework_weight_bps
                for family in policy.family_policies
            ),
            (2_000, 2_500, 2_000, 2_000, 1_500),
        )
        self.assertEqual(policy.minimum_family_coverage_bps, 8_000)

    def test_lynch_uses_required_evidence_families_and_weights(self):
        policy = LYNCH_SCORING_POLICY_V1

        self.assertEqual(
            tuple(family.family for family in policy.family_policies),
            (
                FundamentalFrameworkEvidenceFamily.PROFITABILITY,
                FundamentalFrameworkEvidenceFamily.GROWTH,
                FundamentalFrameworkEvidenceFamily.BALANCE_SHEET,
                FundamentalFrameworkEvidenceFamily.VALUATION,
            ),
        )
        self.assertEqual(
            tuple(
                family.framework_weight_bps
                for family in policy.family_policies
            ),
            (2_000, 3_500, 1_500, 3_000),
        )
        self.assertEqual(policy.minimum_family_coverage_bps, 10_000)

    def test_jhunjhunwala_style_uses_required_families_and_weights(self):
        policy = JHUNJHUNWALA_STYLE_SCORING_POLICY_V1

        self.assertEqual(
            tuple(family.family for family in policy.family_policies),
            (
                FundamentalFrameworkEvidenceFamily.PROFITABILITY,
                FundamentalFrameworkEvidenceFamily.GROWTH,
                FundamentalFrameworkEvidenceFamily.CAPITAL_EFFICIENCY,
                FundamentalFrameworkEvidenceFamily.BALANCE_SHEET,
                FundamentalFrameworkEvidenceFamily.VALUATION,
            ),
        )
        self.assertEqual(
            tuple(
                family.framework_weight_bps
                for family in policy.family_policies
            ),
            (2_000, 3_000, 2_500, 1_500, 1_000),
        )
        self.assertEqual(policy.minimum_family_coverage_bps, 10_000)

    def test_one_hundred_bagger_uses_required_families_and_weights(self):
        policy = ONE_HUNDRED_BAGGER_SCORING_POLICY_V1

        self.assertEqual(
            tuple(family.family for family in policy.family_policies),
            (
                FundamentalFrameworkEvidenceFamily.GROWTH,
                FundamentalFrameworkEvidenceFamily.CAPITAL_EFFICIENCY,
                FundamentalFrameworkEvidenceFamily.BALANCE_SHEET,
                FundamentalFrameworkEvidenceFamily.CASH_CONVERSION,
                FundamentalFrameworkEvidenceFamily.REINVESTMENT,
                FundamentalFrameworkEvidenceFamily.DILUTION,
            ),
        )
        self.assertEqual(
            tuple(
                family.framework_weight_bps
                for family in policy.family_policies
            ),
            (3_000, 2_000, 1_000, 1_500, 1_500, 1_000),
        )
        self.assertEqual(policy.minimum_family_coverage_bps, 10_000)

    def test_can_slim_uses_required_families_and_weights(self):
        policy = CAN_SLIM_SCORING_POLICY_V1

        self.assertEqual(
            tuple(family.family for family in policy.family_policies),
            (
                FundamentalFrameworkEvidenceFamily.GROWTH,
                FundamentalFrameworkEvidenceFamily.INSTITUTIONAL_SPONSORSHIP,
                FundamentalFrameworkEvidenceFamily.RELATIVE_LEADERSHIP,
                FundamentalFrameworkEvidenceFamily.MARKET_REGIME,
                FundamentalFrameworkEvidenceFamily.CATALYST,
            ),
        )
        self.assertEqual(
            tuple(
                family.framework_weight_bps
                for family in policy.family_policies
            ),
            (4_000, 1_500, 1_500, 1_500, 1_500),
        )
        self.assertEqual(policy.minimum_family_coverage_bps, 10_000)

    def test_valuation_uses_absolute_and_peer_relative_families(self):
        policy = VALUATION_SCORING_POLICY_V1

        self.assertEqual(
            tuple(family.family for family in policy.family_policies),
            (
                FundamentalFrameworkEvidenceFamily.VALUATION,
                FundamentalFrameworkEvidenceFamily.RELATIVE_LEADERSHIP,
            ),
        )
        self.assertEqual(
            tuple(
                family.framework_weight_bps
                for family in policy.family_policies
            ),
            (7_500, 2_500),
        )
        self.assertEqual(policy.minimum_family_coverage_bps, 10_000)

    def test_every_rule_is_typed_cited_metric_policy_not_raw_amount(self):
        non_accounting_families = {
            FundamentalFrameworkEvidenceFamily.INSTITUTIONAL_SPONSORSHIP,
            FundamentalFrameworkEvidenceFamily.RELATIVE_LEADERSHIP,
            FundamentalFrameworkEvidenceFamily.MARKET_REGIME,
            FundamentalFrameworkEvidenceFamily.CATALYST,
        }
        for policy in initial_framework_score_policies():
            for family in policy.family_policies:
                self.assertEqual(
                    sum(
                        rule.component_weight_bps
                        for rule in family.metric_rules
                    ),
                    10_000,
                )
                for rule in family.metric_rules:
                    self.assertNotEqual(
                        rule.period_kind,
                        FundamentalMetricPeriodKind.UNKNOWN,
                    )
                    expected_bases = (
                        (FinancialReportingBasis.NOT_APPLICABLE,)
                        if family.family in non_accounting_families
                        else (
                            FinancialReportingBasis.CONSOLIDATED,
                            FinancialReportingBasis.STANDALONE,
                        )
                    )
                    self.assertEqual(
                        rule.allowed_reporting_bases,
                        expected_bases,
                    )
                    self.assertIn(
                        rule.expected_unit.value_kind,
                        {
                            FundamentalValueKind.PERCENTAGE,
                            FundamentalValueKind.RATIO,
                        },
                    )

    def test_buffett_history_and_cash_conversion_requirements_are_explicit(self):
        rules = rules_by_id(BUFFETT_SCORING_POLICY_V1)

        self.assertEqual(
            rules["buffett.profitability.pat_margin"].minimum_observations,
            5,
        )
        self.assertTrue(
            rules[
                "buffett.capital_efficiency.return_on_capital"
            ].require_consecutive_periods
        )
        self.assertEqual(
            rules[
                "buffett.cash_conversion.ocf_to_pat"
            ].minimum_observations,
            5,
        )
        self.assertEqual(
            score_for(
                rules["buffett.cash_conversion.ocf_to_pat"],
                "1",
            ),
            (Decimal("100"), FundamentalScoreGrade.STRONG),
        )
        self.assertEqual(
            score_for(
                rules["buffett.cash_conversion.ocf_to_pat"],
                "2.5",
            ),
            (Decimal("55"), FundamentalScoreGrade.MIXED),
        )

    def test_lynch_growth_and_valuation_rules_have_bounded_meaning(self):
        rules = rules_by_id(LYNCH_SCORING_POLICY_V1)

        self.assertEqual(
            rules["lynch.growth.eps_cagr"].metric_ids,
            (
                "derived.growth.adjusted_eps_cagr_pct",
                "derived.growth.eps_cagr_pct",
            ),
        )
        self.assertEqual(
            score_for(rules["lynch.growth.revenue_cagr"], "15"),
            (Decimal("80"), FundamentalScoreGrade.FAVOURABLE),
        )
        self.assertEqual(
            score_for(rules["lynch.valuation.pe"], "35"),
            (Decimal("30"), FundamentalScoreGrade.WEAK),
        )
        self.assertTrue(
            any("not a PEG" in item for item in LYNCH_SCORING_POLICY_V1.limitations)
        )

    def test_leverage_thresholds_are_not_directionally_inverted(self):
        for policy in initial_framework_score_policies()[:4]:
            rule = rules_by_id(policy)[
                f"{policy.framework.value}.balance_sheet.debt_to_equity"
            ]
            self.assertEqual(
                score_for(rule, "0.25"),
                (Decimal("100"), FundamentalScoreGrade.STRONG),
            )
            self.assertEqual(
                score_for(rule, "3"),
                (Decimal("0"), FundamentalScoreGrade.ADVERSE),
            )

    def test_jhunjhunwala_style_scores_outcomes_not_investor_judgment(self):
        rules = rules_by_id(JHUNJHUNWALA_STYLE_SCORING_POLICY_V1)

        self.assertEqual(
            score_for(
                rules["jhunjhunwala_style.profitability.ebitda_margin"],
                "20",
            ),
            (Decimal("80"), FundamentalScoreGrade.FAVOURABLE),
        )
        self.assertEqual(
            score_for(
                rules["jhunjhunwala_style.growth.ebitda_cagr"],
                "22",
            ),
            (Decimal("100"), FundamentalScoreGrade.STRONG),
        )
        self.assertTrue(
            any(
                "not impersonation" in item
                for item in JHUNJHUNWALA_STYLE_SCORING_POLICY_V1.limitations
            )
        )

    def test_one_hundred_bagger_requires_normalized_reinvestment_and_dilution(self):
        policy = ONE_HUNDRED_BAGGER_SCORING_POLICY_V1
        rules = rules_by_id(policy)

        self.assertEqual(
            rules[
                "one_hundred_bagger.reinvestment.capex_to_ocf"
            ].metric_ids,
            ("derived.reinvestment.capex_to_ocf_pct",),
        )
        self.assertEqual(
            rules[
                "one_hundred_bagger.dilution.share_count_cagr"
            ].metric_ids,
            ("derived.dilution.share_count_cagr_pct",),
        )
        self.assertEqual(
            score_for(
                rules["one_hundred_bagger.reinvestment.capex_to_ocf"],
                "40",
            ),
            (Decimal("100"), FundamentalScoreGrade.STRONG),
        )
        self.assertEqual(
            score_for(
                rules["one_hundred_bagger.dilution.share_count_cagr"],
                "10",
            ),
            (Decimal("0"), FundamentalScoreGrade.ADVERSE),
        )
        self.assertTrue(
            any("withholds" in item for item in policy.limitations)
        )

    def test_can_slim_requires_true_yoy_and_external_evidence_families(self):
        rules = rules_by_id(CAN_SLIM_SCORING_POLICY_V1)
        quarterly_eps = rules["can_slim.growth.quarterly_eps_yoy"]
        sponsorship = rules[
            "can_slim.institutional_sponsorship.ownership_change"
        ]

        self.assertEqual(
            quarterly_eps.metric_ids,
            (
                "derived.growth.quarterly_adjusted_eps_yoy_pct",
                "derived.growth.quarterly_eps_yoy_pct",
            ),
        )
        self.assertEqual(
            quarterly_eps.period_kind,
            FundamentalMetricPeriodKind.QUARTERLY,
        )
        self.assertEqual(
            sponsorship.allowed_reporting_bases,
            (FinancialReportingBasis.NOT_APPLICABLE,),
        )
        self.assertEqual(
            score_for(quarterly_eps, "25"),
            (Decimal("100"), FundamentalScoreGrade.STRONG),
        )
        self.assertEqual(
            score_for(sponsorship, "12"),
            (Decimal("55"), FundamentalScoreGrade.MIXED),
        )
        self.assertEqual(
            score_for(
                rules["can_slim.market_regime.supportive_regime"],
                "2",
            ),
            (Decimal("0"), FundamentalScoreGrade.ADVERSE),
        )
        self.assertTrue(
            any(
                "adjacent-quarter growth is never substituted" in item
                for item in CAN_SLIM_SCORING_POLICY_V1.limitations
            )
        )

    def test_valuation_is_bounded_and_does_not_claim_intrinsic_value(self):
        rules = rules_by_id(VALUATION_SCORING_POLICY_V1)

        self.assertEqual(
            score_for(rules["valuation.valuation.pe"], "35"),
            (Decimal("30"), FundamentalScoreGrade.WEAK),
        )
        self.assertEqual(
            score_for(rules["valuation.valuation.ev_to_ebitda"], "10"),
            (Decimal("80"), FundamentalScoreGrade.FAVOURABLE),
        )
        self.assertEqual(
            score_for(
                rules[
                    "valuation.relative_leadership."
                    "peer_valuation_percentile"
                ],
                "92",
            ),
            (Decimal("10"), FundamentalScoreGrade.ADVERSE),
        )
        self.assertTrue(
            any(
                "No reverse DCF" in item
                for item in VALUATION_SCORING_POLICY_V1.limitations
            )
        )

    def test_shadow_bundle_has_stable_identity_and_all_six_policies(self):
        bundle = INITIAL_FUNDAMENTAL_SCORE_POLICY_BUNDLE_V1

        self.assertEqual(
            bundle.policy_bundle_id,
            "jarvis.fundamental_scorecard",
        )
        self.assertEqual(bundle.policy_bundle_version, "1.0.0")
        self.assertIs(
            bundle.decision_influence_mode,
            FundamentalDecisionInfluenceMode.SHADOW,
        )
        self.assertIsNone(bundle.approval_reference)
        self.assertEqual(
            bundle.framework_policies,
            initial_framework_score_policies(),
        )
        self.assertEqual(
            bundle.policy_fingerprint,
            "a823a605a5c5567602b6e5f30e780576bf4ec370afc3f26102e3058b7af75b3d",
        )

    def test_shadow_bundle_has_explicit_grade_and_stance_thresholds(self):
        bundle = INITIAL_FUNDAMENTAL_SCORE_POLICY_BUNDLE_V1

        self.assertEqual(
            (
                bundle.grade_thresholds.weak_minimum,
                bundle.grade_thresholds.mixed_minimum,
                bundle.grade_thresholds.favourable_minimum,
                bundle.grade_thresholds.strong_minimum,
            ),
            (
                Decimal("20"),
                Decimal("40"),
                Decimal("60"),
                Decimal("80"),
            ),
        )
        self.assertEqual(bundle.stance_policy.minimum_scored_frameworks, 2)
        self.assertEqual(bundle.stance_policy.healthy_minimum_frameworks, 2)
        self.assertEqual(
            bundle.stance_policy.healthy_minimum_score,
            Decimal("65"),
        )
        self.assertEqual(
            bundle.stance_policy.caution_minimum_score,
            Decimal("35"),
        )
        self.assertTrue(
            bundle.stance_policy.incomplete_mandatory_is_insufficient
        )

    def test_each_mandatory_domain_has_non_offsettable_warn_and_fail_ceiling(self):
        constraints = (
            INITIAL_FUNDAMENTAL_SCORE_POLICY_BUNDLE_V1.mandatory_constraints
        )

        self.assertEqual(
            tuple(item.constraint_id for item in constraints),
            (
                "earnings_quality.fail",
                "earnings_quality.warn",
                "financial_health.fail",
                "financial_health.warn",
                "forensic_risk.fail",
                "forensic_risk.warn",
            ),
        )
        by_key = {
            (item.domain, item.trigger_status): item
            for item in constraints
        }
        for domain in FundamentalAssessmentDomain:
            fail = by_key[(domain, FundamentalFindingStatus.FAIL)]
            warning = by_key[(domain, FundamentalFindingStatus.WARN)]

            self.assertIs(
                fail.minimum_severity,
                FundamentalFindingSeverity.MEDIUM,
            )
            self.assertIs(fail.stance_ceiling, FundamentalScorecardStance.WEAK)
            self.assertTrue(fail.review_required)
            self.assertIs(
                warning.minimum_severity,
                FundamentalFindingSeverity.LOW,
            )
            self.assertIs(
                warning.stance_ceiling,
                FundamentalScorecardStance.CAUTION,
            )
            self.assertTrue(warning.review_required)

        self.assertTrue(
            any(
                "not averaged" in item
                for item in INITIAL_FUNDAMENTAL_SCORE_POLICY_BUNDLE_V1.limitations
            )
        )

    def test_named_lenses_are_frozen_policies_not_mutable_personas(self):
        with self.assertRaises(ValidationError):
            BUFFETT_SCORING_POLICY_V1.policy_version = "2.0.0"
        with self.assertRaises(ValidationError):
            LYNCH_SCORING_POLICY_V1.minimum_family_coverage_bps = 1
        with self.assertRaises(ValidationError):
            JHUNJHUNWALA_STYLE_SCORING_POLICY_V1.policy_version = "2.0.0"
        with self.assertRaises(ValidationError):
            ONE_HUNDRED_BAGGER_SCORING_POLICY_V1.policy_version = "2.0.0"
        with self.assertRaises(ValidationError):
            CAN_SLIM_SCORING_POLICY_V1.policy_version = "2.0.0"
        with self.assertRaises(ValidationError):
            VALUATION_SCORING_POLICY_V1.policy_version = "2.0.0"
        with self.assertRaises(ValidationError):
            INITIAL_FUNDAMENTAL_SCORE_POLICY_BUNDLE_V1.policy_bundle_version = (
                "2.0.0"
            )

        self.assertTrue(
            any(
                "cannot infer durable moat" in item
                for item in BUFFETT_SCORING_POLICY_V1.limitations
            )
        )
        self.assertTrue(
            any(
                "does not classify the issuer" in item
                for item in LYNCH_SCORING_POLICY_V1.limitations
            )
        )


if __name__ == "__main__":
    unittest.main()
