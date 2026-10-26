import unittest
from datetime import timedelta
from pathlib import Path

from app.agents.fundamental_analyst_agent import (
    FundamentalAnalystAgent,
    FundamentalDocumentEvidence,
)
from app.analytics.fundamental_intelligence import (
    build_shadow_fundamental_intelligence,
)
from app.exceptions import LLMResponseValidationError
from app.llm.gateway import StructuredGeneration
from app.models.runtime_skill import RuntimeAgentRole
from app.models.fundamental_analysis import FundamentalShadowAssessmentReference
from app.models.fundamental_scoring import FundamentalDecisionInfluenceMode
from app.runtime_skill_loader import RuntimeSkillLoader
from tests.unit.test_financial_documents import document
from tests.unit.test_fundamental_applicability import comprehensive_evidence
from tests.unit.test_fundamental_intelligence import (
    cited_non_financial_classification,
)


_REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


class FakeGateway:
    def __init__(self, payloads):
        self.payloads = list(payloads)
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return "d" * 64

    def generate(self, *, system, messages, response_model):
        self.calls.append({"system": system, "messages": messages})
        return StructuredGeneration[response_model](
            value=response_model(**self.payloads.pop(0)),
            provider="fake-provider",
            model="fake-fundamental-model",
            attempt_count=1,
        )


def _draft(*, citations):
    return {
        "evidence_coverage_and_freshness": "One current Growth Table.",
        "financial_strengths": ["Reported revenue growth improved."],
        "financial_risks_and_conflicts": [],
        "preliminary_financial_health": "Evidence is preliminary.",
        "confidence_percentage": 45.0,
        "valuation_observations": [],
        "missing_evidence_and_limitations": [
            "Balance Sheet and Cash Flow are unavailable."
        ],
        "document_citations": citations,
    }


class FundamentalAnalystAgentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        skills = RuntimeSkillLoader(
            _REPOSITORY_ROOT / "runtime-skills" / "manifest.yaml"
        ).load()
        cls.fundamental_skill = skills.skill_for(
            RuntimeAgentRole.FUNDAMENTAL_ANALYST
        )
        cls.bull_skill = skills.skill_for(RuntimeAgentRole.BULL)

    def setUp(self):
        normalized = comprehensive_evidence()
        self.as_of = normalized.normalized_at + timedelta(seconds=1)
        self.intelligence = build_shadow_fundamental_intelligence(
            normalized,
            sector_classification=cited_non_financial_classification(
                normalized,
                self.as_of,
            ),
            assessed_at=self.as_of,
        )
        self.document = document(
            issuer=normalized.issuer,
            retrieved_at=normalized.package_committed_at,
            expires_at=normalized.package_expires_at,
        )
        self.reference_id = "financial_document:cached-growth-table"
        self.evidence = (
            FundamentalDocumentEvidence(
                reference_id=self.reference_id,
                document_fingerprint=self.document.document_fingerprint,
                document=self.document,
            ),
        )

    def test_requires_verified_role_specific_skill(self):
        with self.assertRaisesRegex(ValueError, "must be verified"):
            FundamentalAnalystAgent(
                FakeGateway([]),
                runtime_skill=object(),
            )
        with self.assertRaisesRegex(ValueError, "approved runtime skill"):
            FundamentalAnalystAgent(
                FakeGateway([]),
                runtime_skill=self.bull_skill,
            )

    def test_rejects_non_gateway_dependency(self):
        with self.assertRaisesRegex(ValueError, "structured LLM gateway"):
            FundamentalAnalystAgent(
                object(),
                runtime_skill=self.fundamental_skill,
            )

    def test_uses_verified_skill_and_complete_bound_document(self):
        gateway = FakeGateway([_draft(citations=[self.reference_id])])
        agent = FundamentalAnalystAgent(
            gateway,
            runtime_skill=self.fundamental_skill,
        )

        result = agent.analyze(
            evidence=self.evidence,
            intelligence=self.intelligence,
            as_of=self.as_of,
        )

        self.assertEqual(
            gateway.calls[0]["system"],
            self.fundamental_skill.system_instructions,
        )
        context = gateway.calls[0]["messages"][0]["content"]
        self.assertIn(self.reference_id, context)
        self.assertIn('"profile":"non_financial"', context)
        self.assertIn('"source_reference_id":"nse-sector-master:TCS"', context)
        self.assertIn(
            self.intelligence.sector_classification.classification_fingerprint,
            context,
        )
        self.assertIn(self.document.document_id, context)
        self.assertIn('"rows"', context)
        self.assertIn('"decision_influence_mode":"shadow"', context)
        self.assertIn('"framework":"buffett"', context)
        self.assertIn('"framework":"valuation"', context)
        self.assertIn(
            self.intelligence.scorecard.scorecard_fingerprint,
            context,
        )
        self.assertEqual(result.document_citations, (self.reference_id,))
        self.assertIsNotNone(result.shadow_assessment)
        self.assertEqual(
            result.shadow_assessment.package_id,
            self.intelligence.scorecard.package_id,
        )
        self.assertEqual(
            result.shadow_assessment.scorecard_fingerprint,
            self.intelligence.scorecard.scorecard_fingerprint,
        )
        self.assertIs(
            result.shadow_assessment.decision_influence_mode,
            FundamentalDecisionInfluenceMode.SHADOW,
        )
        self.assertEqual(result.runtime_skill_id, self.fundamental_skill.contract.skill_id)
        self.assertEqual(result.model_id, "fake-fundamental-model")

    def test_configuration_fingerprint_includes_verified_skill(self):
        agent = FundamentalAnalystAgent(
            FakeGateway([]),
            runtime_skill=self.fundamental_skill,
        )

        self.assertEqual(len(agent.configuration_fingerprint), 64)
        int(agent.configuration_fingerprint, 16)

    def test_shadow_trace_contract_rejects_decision_enabled_mode(self):
        scorecard = self.intelligence.scorecard
        values = {
            "package_id": scorecard.package_id,
            "manifest_fingerprint": scorecard.manifest_fingerprint,
            "normalized_evidence_fingerprint": (
                scorecard.normalized_evidence_fingerprint
            ),
            "mandatory_assessment_fingerprint": (
                scorecard.mandatory_assessment_fingerprint
            ),
            "applicability_assessment_fingerprint": (
                scorecard.applicability_assessment_fingerprint
            ),
            "scorecard_fingerprint": scorecard.scorecard_fingerprint,
            "score_policy_bundle_id": scorecard.score_policy_bundle_id,
            "score_policy_bundle_version": scorecard.score_policy_bundle_version,
            "score_policy_bundle_fingerprint": (
                scorecard.score_policy_bundle_fingerprint
            ),
            "decision_influence_mode": FundamentalDecisionInfluenceMode.ADVISORY,
            "stance": scorecard.stance,
            "selected_primary_frameworks": (
                scorecard.selected_primary_frameworks
            ),
            "review_required": scorecard.review_required,
            "scored_at": scorecard.scored_at,
            "expires_at": scorecard.package_expires_at,
        }

        with self.assertRaisesRegex(ValueError, "shadow-only"):
            FundamentalShadowAssessmentReference(**values)

    def test_rejects_expired_evidence_before_llm_call(self):
        gateway = FakeGateway([])
        agent = FundamentalAnalystAgent(
            gateway,
            runtime_skill=self.fundamental_skill,
        )

        with self.assertRaisesRegex(ValueError, "expired"):
            agent.analyze(
                evidence=self.evidence,
                intelligence=self.intelligence,
                as_of=self.document.expires_at,
            )

        self.assertEqual(gateway.calls, [])

    def test_rejects_duplicate_references_before_llm_call(self):
        gateway = FakeGateway([])
        agent = FundamentalAnalystAgent(
            gateway,
            runtime_skill=self.fundamental_skill,
        )

        with self.assertRaisesRegex(ValueError, "references must be unique"):
            agent.analyze(
                evidence=self.evidence + self.evidence,
                intelligence=self.intelligence,
                as_of=self.as_of,
            )

        self.assertEqual(gateway.calls, [])

    def test_retries_and_rejects_invented_document_citations(self):
        gateway = FakeGateway(
            [
                _draft(citations=["financial_document:invented"]),
                _draft(citations=["financial_document:still-invented"]),
            ]
        )
        agent = FundamentalAnalystAgent(
            gateway,
            runtime_skill=self.fundamental_skill,
        )

        with self.assertRaises(LLMResponseValidationError):
            agent.analyze(
                evidence=self.evidence,
                intelligence=self.intelligence,
                as_of=self.as_of,
            )

        self.assertEqual(len(gateway.calls), 2)

    def test_rejects_mismatched_intelligence_before_llm_call(self):
        gateway = FakeGateway([])
        agent = FundamentalAnalystAgent(
            gateway,
            runtime_skill=self.fundamental_skill,
        )
        mismatched_document = document(
            issuer=self.document.issuer.model_copy(update={"symbol": "TCS"}),
            retrieved_at=self.document.retrieved_at,
            expires_at=self.document.expires_at,
        )
        mismatched_evidence = (
            FundamentalDocumentEvidence(
                reference_id=self.reference_id,
                document_fingerprint=mismatched_document.document_fingerprint,
                document=mismatched_document,
            ),
        )

        with self.assertRaisesRegex(ValueError, "issuer must match"):
            agent.analyze(
                evidence=mismatched_evidence,
                intelligence=self.intelligence,
                as_of=self.as_of,
            )

        self.assertEqual(gateway.calls, [])


if __name__ == "__main__":
    unittest.main()
