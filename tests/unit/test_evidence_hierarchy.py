import unittest

from pydantic import ValidationError

from app.models.evidence_hierarchy import (
    DEFAULT_EVIDENCE_HIERARCHY,
    EvidenceCorrelationGroup,
    EvidenceDecisionRole,
    EvidenceFamily,
    EvidenceFamilyPolicy,
    EvidenceHierarchyPolicy,
)


class EvidenceHierarchyPolicyTests(unittest.TestCase):
    def test_default_policy_defines_every_family_once_in_order(self):
        policy = DEFAULT_EVIDENCE_HIERARCHY

        self.assertEqual(
            {item.family for item in policy.families},
            set(EvidenceFamily),
        )
        self.assertEqual(
            [item.evaluation_order for item in policy.families],
            list(range(1, len(EvidenceFamily) + 1)),
        )
        self.assertEqual(policy.aggregation_method, "hierarchical_gates")
        self.assertEqual(
            policy.supporting_aggregation_method,
            "correlated_family_confluence",
        )

    def test_market_structure_and_location_are_primary_context(self):
        policy = DEFAULT_EVIDENCE_HIERARCHY

        for family in (
            EvidenceFamily.MARKET_STRUCTURE,
            EvidenceFamily.PRICE_LOCATION,
        ):
            definition = policy.policy_for(family)
            self.assertIs(
                definition.role,
                EvidenceDecisionRole.PRIMARY_CONTEXT,
            )
            self.assertTrue(definition.can_block_release)

    def test_correlated_ohlc_families_are_one_supporting_group(self):
        policy = DEFAULT_EVIDENCE_HIERARCHY

        for family in (
            EvidenceFamily.TREND_CONFIRMATION,
            EvidenceFamily.MOMENTUM_CONFIRMATION,
            EvidenceFamily.CPR_CONTEXT,
            EvidenceFamily.CANDLESTICK_CONTEXT,
            EvidenceFamily.VOLATILITY_CONTEXT,
        ):
            definition = policy.policy_for(family)
            self.assertIs(
                definition.role,
                EvidenceDecisionRole.SUPPORTING_CONFIRMATION,
            )
            self.assertIs(
                definition.correlation_group,
                EvidenceCorrelationGroup.OHLC_DERIVED_CONFIRMATION,
            )
            self.assertFalse(definition.can_block_release)
            self.assertFalse(definition.can_create_buy)

    def test_participation_is_supporting_but_not_an_ohlc_vote(self):
        definition = DEFAULT_EVIDENCE_HIERARCHY.policy_for(
            EvidenceFamily.PARTICIPATION
        )

        self.assertIs(
            definition.role,
            EvidenceDecisionRole.SUPPORTING_CONFIRMATION,
        )
        self.assertIs(
            definition.correlation_group,
            EvidenceCorrelationGroup.PARTICIPATION,
        )
        self.assertFalse(definition.can_block_release)

    def test_fundamental_trigger_and_feasibility_are_release_gates(self):
        policy = DEFAULT_EVIDENCE_HIERARCHY
        expected = {
            EvidenceFamily.FUNDAMENTAL_HEALTH: (
                EvidenceDecisionRole.ELIGIBILITY_GATE
            ),
            EvidenceFamily.ENTRY_TRIGGER: (
                EvidenceDecisionRole.ENTRY_TRIGGER
            ),
            EvidenceFamily.RISK_TARGET_FEASIBILITY: (
                EvidenceDecisionRole.FEASIBILITY_GATE
            ),
        }

        for family, role in expected.items():
            definition = policy.policy_for(family)
            self.assertIs(definition.role, role)
            self.assertTrue(definition.can_block_release)
            self.assertFalse(definition.can_create_buy)

    def test_no_individual_family_can_create_buy(self):
        self.assertTrue(
            all(
                not item.can_create_buy
                for item in DEFAULT_EVIDENCE_HIERARCHY.families
            )
        )

    def test_supporting_family_cannot_be_an_independent_blocker(self):
        with self.assertRaisesRegex(
            ValidationError,
            "supporting confirmation cannot independently block release",
        ):
            EvidenceFamilyPolicy(
                family=EvidenceFamily.MOMENTUM_CONFIRMATION,
                role=EvidenceDecisionRole.SUPPORTING_CONFIRMATION,
                correlation_group=(
                    EvidenceCorrelationGroup.OHLC_DERIVED_CONFIRMATION
                ),
                evaluation_order=1,
                can_block_release=True,
            )

    def test_gate_cannot_be_declared_non_blocking(self):
        with self.assertRaisesRegex(
            ValidationError,
            "decision gates must be able to block release",
        ):
            EvidenceFamilyPolicy(
                family=EvidenceFamily.ENTRY_TRIGGER,
                role=EvidenceDecisionRole.ENTRY_TRIGGER,
                correlation_group=EvidenceCorrelationGroup.ENTRY,
                evaluation_order=1,
                can_block_release=False,
            )

    def test_rejects_duplicate_or_missing_families(self):
        market_structure = DEFAULT_EVIDENCE_HIERARCHY.families[0]
        with self.assertRaisesRegex(
            ValidationError,
            "evidence hierarchy families must be unique",
        ):
            EvidenceHierarchyPolicy(
                families=(market_structure, market_structure)
            )

        with self.assertRaisesRegex(
            ValidationError,
            "evidence hierarchy must define every family",
        ):
            EvidenceHierarchyPolicy(families=(market_structure,))

    def test_rejects_non_contiguous_order(self):
        policies = list(DEFAULT_EVIDENCE_HIERARCHY.families)
        policies[-1] = policies[-1].model_copy(
            update={"evaluation_order": 9}
        )

        with self.assertRaisesRegex(
            ValidationError,
            "order must be unique and contiguous",
        ):
            EvidenceHierarchyPolicy(families=tuple(policies))

    def test_rejects_dependency_that_is_evaluated_too_late(self):
        policies = list(DEFAULT_EVIDENCE_HIERARCHY.families)
        policies[0] = policies[0].model_copy(
            update={"evaluation_order": 2}
        )
        policies[1] = policies[1].model_copy(
            update={"evaluation_order": 1}
        )

        with self.assertRaisesRegex(
            ValidationError,
            "dependencies must be evaluated first",
        ):
            EvidenceHierarchyPolicy(families=tuple(policies))

    def test_models_are_immutable_and_strict(self):
        definition = DEFAULT_EVIDENCE_HIERARCHY.families[0]

        with self.assertRaises(ValidationError):
            definition.evaluation_order = 2
        with self.assertRaises(ValidationError):
            EvidenceFamilyPolicy(
                family=EvidenceFamily.MARKET_STRUCTURE,
                role=EvidenceDecisionRole.PRIMARY_CONTEXT,
                correlation_group=EvidenceCorrelationGroup.MARKET_STRUCTURE,
                evaluation_order=True,
                can_block_release=True,
            )


if __name__ == "__main__":
    unittest.main()
