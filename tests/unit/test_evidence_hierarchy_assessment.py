import unittest

from pydantic import ValidationError

from app.analytics.evidence_hierarchy import (
    EvidenceFamilyFinding,
    EvidenceFamilyState,
    assess_evidence_hierarchy,
    build_multi_timeframe_technical_findings,
)
from app.models.evidence_hierarchy import (
    EvidenceCorrelationGroup,
    EvidenceFamily,
)
from app.models.trade_decision import MarketCondition
from app.orchestration.agent_orchestrator import AgentOrchestrator
from tests.unit.test_build_multi_timeframe_evidence import (
    _cyclical_timeframes,
)


def _findings(
    *,
    market_structure: EvidenceFamilyState = EvidenceFamilyState.BULLISH,
    price_location: EvidenceFamilyState = EvidenceFamilyState.BULLISH,
    supporting: EvidenceFamilyState = EvidenceFamilyState.BULLISH,
    fundamental: EvidenceFamilyState = EvidenceFamilyState.PASS,
    entry: EvidenceFamilyState = EvidenceFamilyState.PASS,
    risk: EvidenceFamilyState = EvidenceFamilyState.PASS,
    fundamental_not_applicable: bool = False,
) -> tuple[EvidenceFamilyFinding, ...]:
    states = {
        EvidenceFamily.MARKET_STRUCTURE: market_structure,
        EvidenceFamily.PRICE_LOCATION: price_location,
        EvidenceFamily.PARTICIPATION: supporting,
        EvidenceFamily.TREND_CONFIRMATION: supporting,
        EvidenceFamily.MOMENTUM_CONFIRMATION: supporting,
        EvidenceFamily.CPR_CONTEXT: supporting,
        EvidenceFamily.CANDLESTICK_CONTEXT: supporting,
        EvidenceFamily.VOLATILITY_CONTEXT: supporting,
        EvidenceFamily.FUNDAMENTAL_HEALTH: (
            EvidenceFamilyState.NOT_APPLICABLE
            if fundamental_not_applicable
            else fundamental
        ),
        EvidenceFamily.ENTRY_TRIGGER: entry,
        EvidenceFamily.RISK_TARGET_FEASIBILITY: risk,
    }
    return tuple(
        EvidenceFamilyFinding(
            family=family,
            state=state,
            evidence_ids=(f"daily:{family.value}.test",),
            rationale=f"Deterministic {family.value} test finding.",
        )
        for family, state in states.items()
    )


class HierarchicalEvidenceAssessmentTests(unittest.TestCase):
    def test_bullish_primary_context_and_passing_gates_allow_release(self):
        assessment = assess_evidence_hierarchy(_findings())

        self.assertIs(assessment.primary_condition, MarketCondition.BULLISH)
        self.assertTrue(assessment.release_eligible)
        self.assertEqual(assessment.blocking_families, ())

    def test_correlated_ohlc_indicators_are_represented_once(self):
        assessment = assess_evidence_hierarchy(_findings())
        ohlc_group = next(
            group
            for group in assessment.supporting_groups
            if group.correlation_group
            is EvidenceCorrelationGroup.OHLC_DERIVED_CONFIRMATION
        )

        self.assertEqual(len(ohlc_group.families), 5)
        self.assertIs(ohlc_group.condition, MarketCondition.BULLISH)
        self.assertEqual(
            sum(
                group.correlation_group
                is EvidenceCorrelationGroup.OHLC_DERIVED_CONFIRMATION
                for group in assessment.supporting_groups
            ),
            1,
        )

    def test_supporting_bullish_signals_cannot_create_bullish_context(self):
        assessment = assess_evidence_hierarchy(
            _findings(
                market_structure=EvidenceFamilyState.NEUTRAL,
                price_location=EvidenceFamilyState.NEUTRAL,
            )
        )

        self.assertIs(assessment.primary_condition, MarketCondition.NEUTRAL)
        self.assertFalse(assessment.release_eligible)
        self.assertEqual(
            assessment.blocking_families,
            (
                EvidenceFamily.MARKET_STRUCTURE,
                EvidenceFamily.PRICE_LOCATION,
            ),
        )

    def test_supporting_conflict_does_not_independently_block_release(self):
        findings = list(_findings())
        momentum_index = next(
            index
            for index, item in enumerate(findings)
            if item.family is EvidenceFamily.MOMENTUM_CONFIRMATION
        )
        findings[momentum_index] = findings[momentum_index].model_copy(
            update={"state": EvidenceFamilyState.BEARISH}
        )

        assessment = assess_evidence_hierarchy(findings)

        self.assertTrue(assessment.release_eligible)
        ohlc_group = next(
            group
            for group in assessment.supporting_groups
            if group.correlation_group
            is EvidenceCorrelationGroup.OHLC_DERIVED_CONFIRMATION
        )
        self.assertIs(ohlc_group.condition, MarketCondition.CONFLICTED)

    def test_failed_or_insufficient_gate_blocks_release(self):
        assessment = assess_evidence_hierarchy(
            _findings(
                fundamental=EvidenceFamilyState.FAIL,
                risk=EvidenceFamilyState.INSUFFICIENT,
            )
        )
        self.assertFalse(assessment.release_eligible)
        self.assertEqual(
            assessment.blocking_families,
            (
                EvidenceFamily.FUNDAMENTAL_HEALTH,
                EvidenceFamily.RISK_TARGET_FEASIBILITY,
            ),
        )

    def test_explicit_out_of_scope_gate_does_not_fabricate_a_pass(self):
        assessment = assess_evidence_hierarchy(
            _findings(fundamental_not_applicable=True),
            not_applicable_families=frozenset(
                {EvidenceFamily.FUNDAMENTAL_HEALTH}
            ),
        )

        fundamental = next(
            finding
            for finding in assessment.gate_states
            if finding.family is EvidenceFamily.FUNDAMENTAL_HEALTH
        )
        self.assertIs(
            fundamental.state,
            EvidenceFamilyState.NOT_APPLICABLE,
        )
        self.assertEqual(
            assessment.not_applicable_families,
            (EvidenceFamily.FUNDAMENTAL_HEALTH,),
        )
        self.assertTrue(assessment.release_eligible)

    def test_not_applicable_gate_requires_explicit_scope_declaration(self):
        with self.assertRaisesRegex(
            ValueError,
            "must match the declared assessment scope",
        ):
            assess_evidence_hierarchy(
                _findings(fundamental_not_applicable=True)
            )

    def test_primary_family_cannot_be_declared_not_applicable(self):
        with self.assertRaisesRegex(
            ValueError,
            "only gate families may be declared not applicable",
        ):
            assess_evidence_hierarchy(
                _findings(),
                not_applicable_families=frozenset(
                    {EvidenceFamily.MARKET_STRUCTURE}
                ),
            )

    def test_primary_conflict_and_insufficiency_are_preserved(self):
        conflicted = assess_evidence_hierarchy(
            _findings(price_location=EvidenceFamilyState.BEARISH)
        )
        insufficient = assess_evidence_hierarchy(
            _findings(
                market_structure=EvidenceFamilyState.INSUFFICIENT
            )
        )

        self.assertIs(
            conflicted.primary_condition,
            MarketCondition.CONFLICTED,
        )
        self.assertIs(
            insufficient.primary_condition,
            MarketCondition.INSUFFICIENT,
        )

    def test_rejects_missing_or_duplicate_family_findings(self):
        findings = _findings()
        with self.assertRaisesRegex(ValueError, "one finding for every family"):
            assess_evidence_hierarchy(findings[:-1])
        with self.assertRaisesRegex(ValueError, "findings must be unique"):
            assess_evidence_hierarchy((*findings, findings[0]))

    def test_rejects_state_that_does_not_match_family_role(self):
        findings = list(_findings())
        findings[0] = findings[0].model_copy(
            update={"state": EvidenceFamilyState.PASS}
        )

        with self.assertRaisesRegex(ValueError, "does not accept the pass state"):
            assess_evidence_hierarchy(findings)

    def test_finding_rejects_duplicate_or_blank_evidence_ids(self):
        with self.assertRaisesRegex(ValidationError, "IDs must be unique"):
            EvidenceFamilyFinding(
                family=EvidenceFamily.MARKET_STRUCTURE,
                state=EvidenceFamilyState.BULLISH,
                evidence_ids=("daily:structure.test", "daily:structure.test"),
                rationale="Duplicate evidence IDs.",
            )
        with self.assertRaisesRegex(ValidationError, "IDs cannot be blank"):
            EvidenceFamilyFinding(
                family=EvidenceFamily.MARKET_STRUCTURE,
                state=EvidenceFamilyState.BULLISH,
                evidence_ids=(" ",),
                rationale="Blank evidence ID.",
            )


class MultiTimeframeTechnicalFindingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        result = AgentOrchestrator().run_multi_timeframe_analysis(
            _cyclical_timeframes()
        )
        cls.package = result.evidence_package
        cls.findings = build_multi_timeframe_technical_findings(cls.package)

    def test_builds_each_technical_family_in_policy_order(self):
        self.assertEqual(
            tuple(finding.family for finding in self.findings),
            (
                EvidenceFamily.MARKET_STRUCTURE,
                EvidenceFamily.PRICE_LOCATION,
                EvidenceFamily.PARTICIPATION,
                EvidenceFamily.TREND_CONFIRMATION,
                EvidenceFamily.MOMENTUM_CONFIRMATION,
                EvidenceFamily.CPR_CONTEXT,
                EvidenceFamily.CANDLESTICK_CONTEXT,
                EvidenceFamily.VOLATILITY_CONTEXT,
            ),
        )

    def test_preserves_only_released_qualified_evidence_ids(self):
        released_ids = {
            item.qualified_evidence_id
            for context in (self.package.daily, self.package.weekly)
            for item in context.evidence
        }
        released_ids.update(self.package.daily.cpr.evidence_ids)
        released_ids.update(self.package.weekly.cpr.evidence_ids)

        translated_ids = {
            evidence_id
            for finding in self.findings
            for evidence_id in finding.evidence_ids
        }
        self.assertTrue(translated_ids)
        self.assertLessEqual(translated_ids, released_ids)
        self.assertTrue(
            all(
                evidence_id.startswith(("daily:", "weekly:"))
                for evidence_id in translated_ids
            )
        )

    def test_keeps_correlated_indicators_inside_their_own_family(self):
        momentum = next(
            item
            for item in self.findings
            if item.family is EvidenceFamily.MOMENTUM_CONFIRMATION
        )
        self.assertEqual(len(momentum.evidence_ids), 6)
        self.assertTrue(
            all(
                any(
                    name in evidence_id
                    for name in (
                        "rsi_condition.",
                        "macd_momentum.",
                        "stochastic_momentum.",
                    )
                )
                for evidence_id in momentum.evidence_ids
            )
        )

    def test_does_not_fabricate_unowned_release_gates(self):
        self.assertTrue(
            {
                EvidenceFamily.FUNDAMENTAL_HEALTH,
                EvidenceFamily.ENTRY_TRIGGER,
                EvidenceFamily.RISK_TARGET_FEASIBILITY,
            }.isdisjoint(finding.family for finding in self.findings)
        )

    def test_rejects_an_unvalidated_package_type(self):
        with self.assertRaisesRegex(
            ValueError,
            "requires a released package",
        ):
            build_multi_timeframe_technical_findings(object())


if __name__ == "__main__":
    unittest.main()
