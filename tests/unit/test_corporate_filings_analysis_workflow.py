from __future__ import annotations

from datetime import timedelta
import unittest

from pydantic import ValidationError

from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisCoverage,
    CorporateFilingsAnalysisExecution,
    CorporateFilingsAnalysisFinding,
    CorporateFilingsAnalysisKind,
    CorporateFilingsAnalysisResult,
    CorporateFilingsAnalysisStatus,
    CorporateFilingsCoverageStatus,
    CorporateFilingsFindingConfidence,
    CorporateFilingsFindingKind,
    CorporateFilingsGroundingStatus,
    CorporateFilingsTokenUsage,
)
from app.services.corporate_filings_analysis import (
    CorporateFilingsAnalysisCoordinationStatus,
)
from app.services.corporate_filings_analysis_workflow import (
    CorporateFilingsAnalysisWorkflow,
    CorporateFilingsAnalysisWorkflowResult,
)
from app.storage.adapters.corporate_filings_analysis_in_memory import (
    InMemoryCorporateFilingsAnalysisRepository,
)
from tests.unit.test_corporate_filings_analysis_assembly import (
    StubEvidenceRepository,
    StubFilingRepository,
    analysis_policy,
    assembly_input,
    complete_quarter_package_and_evidence,
    releaseable_evidence,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingRepositoryScope,
)
from tests.unit.test_corporate_filings_analysis_in_memory import MutableClock
from tests.unit.test_fundamental_filings import package


NOW = assembly_input().as_of


class StubVerifiedAnalyst:
    def __init__(self) -> None:
        self.policy = analysis_policy()
        self.policy_calls = 0
        self.analysis_calls = []

    def build_analysis_policy(self):
        self.policy_calls += 1
        return self.policy

    def analyze(self, request):
        self.analysis_calls.append(request)
        document = request.bundle.documents[0]
        finding = CorporateFilingsAnalysisFinding(
            finding_id="finding.observed.1",
            kind=CorporateFilingsFindingKind.OBSERVED_FACT,
            statement="A grounded synthetic filing fact.",
            confidence=CorporateFilingsFindingConfidence.HIGH,
            citation_ids=(document.citations[0].citation_id,),
            chunk_ids=(document.chunks[0].chunk_id,),
        )
        available_citations = {
            citation.citation_id
            for item in request.bundle.documents
            for citation in item.citations
        }
        chunk_count = sum(
            len(item.chunks) for item in request.bundle.documents
        )
        return CorporateFilingsAnalysisResult(
            analysis_id="corporate-filing-analysis.workflow.1",
            request_fingerprint=request.request_fingerprint,
            reuse_key_fingerprint=request.reuse_key.reuse_key_fingerprint,
            bundle_fingerprint=request.bundle.bundle_fingerprint,
            policy_fingerprint=request.policy.policy_fingerprint,
            status=CorporateFilingsAnalysisStatus.COMPLETED,
            grounding_status=CorporateFilingsGroundingStatus.GROUNDED,
            coverage=CorporateFilingsAnalysisCoverage(
                status=CorporateFilingsCoverageStatus.COMPLETE,
                expected_documents=len(request.bundle.documents),
                analyzed_documents=len(request.bundle.documents),
                expected_chunks=chunk_count,
                analyzed_chunks=chunk_count,
                available_citations=len(available_citations),
                cited_citations=1,
                unresolved_speaker_turns=0,
            ),
            observed_facts=(finding,),
            execution=CorporateFilingsAnalysisExecution(
                model_id="provider/model",
                model_configuration_fingerprint=(
                    request.policy.model_configuration_fingerprint
                ),
                started_at=request.as_of,
                completed_at=request.as_of + timedelta(seconds=1),
                token_usage=CorporateFilingsTokenUsage(
                    input_tokens=1_000,
                    output_tokens=500,
                    model_calls=1,
                ),
            ),
        )


class PolicyOnlyRuntime:
    def build_analysis_policy(self):
        return analysis_policy()


class CorporateFilingsAnalysisWorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock(NOW + timedelta(seconds=2))
        self.analyst = StubVerifiedAnalyst()
        self.analysis_repository = InMemoryCorporateFilingsAnalysisRepository(
            clock=self.clock
        )
        active_package, _, conference_call = (
            complete_quarter_package_and_evidence()
        )
        self.input = assembly_input(
            scope=FundamentalFilingRepositoryScope.from_package(active_package),
            analysis_kind=CorporateFilingsAnalysisKind.CONFERENCE_CALL,
        )
        self.workflow = CorporateFilingsAnalysisWorkflow(
            analyst=self.analyst,
            filing_repository=StubFilingRepository(active_package),
            evidence_repository=StubEvidenceRepository((conference_call,)),
            analysis_repository=self.analysis_repository,
            clock=self.clock,
        )

    def test_one_entry_point_assembles_and_publishes_exact_policy(self):
        outcome = self.workflow.execute(self.input)

        self.assertIsInstance(outcome, CorporateFilingsAnalysisWorkflowResult)
        self.assertEqual(outcome.request.policy, self.analyst.policy)
        self.assertEqual(self.analyst.policy_calls, 1)
        self.assertEqual(self.analyst.analysis_calls, [outcome.request])
        self.assertIs(
            outcome.coordination.status,
            CorporateFilingsAnalysisCoordinationStatus.ANALYSIS_PUBLISHED,
        )
        self.assertEqual(
            outcome.coordination.request_fingerprint,
            outcome.request.request_fingerprint,
        )

    def test_same_runtime_policy_drives_exact_cache_reuse(self):
        first = self.workflow.execute(self.input)
        second = self.workflow.execute(self.input)

        self.assertIs(
            second.coordination.status,
            CorporateFilingsAnalysisCoordinationStatus.EXACT_CACHE_REUSED,
        )
        self.assertEqual(second.request.policy, first.request.policy)
        self.assertEqual(len(self.analyst.analysis_calls), 1)
        self.assertEqual(self.analyst.policy_calls, 2)

    def test_explicit_refresh_reuses_policy_but_invokes_analysis_again(self):
        self.workflow.execute(self.input)
        self.clock.value += timedelta(seconds=1)

        refreshed = self.workflow.execute(
            self.input,
            refresh_requested=True,
        )

        self.assertTrue(refreshed.coordination.refresh_requested)
        self.assertEqual(refreshed.request.policy, self.analyst.policy)
        self.assertEqual(len(self.analyst.analysis_calls), 2)

    def test_rejects_runtime_missing_analysis_capability(self):
        with self.assertRaisesRegex(TypeError, "one verified analyst"):
            CorporateFilingsAnalysisWorkflow(
                analyst=PolicyOnlyRuntime(),
                filing_repository=StubFilingRepository(package()),
                evidence_repository=StubEvidenceRepository(
                    (releaseable_evidence(),)
                ),
                analysis_repository=self.analysis_repository,
            )

    def test_workflow_result_is_immutable(self):
        outcome = self.workflow.execute(self.input)

        with self.assertRaises(ValidationError):
            outcome.request = outcome.request


if __name__ == "__main__":
    unittest.main()
