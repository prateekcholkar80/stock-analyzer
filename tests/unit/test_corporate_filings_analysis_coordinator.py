from __future__ import annotations

from datetime import datetime, timedelta, timezone
import unittest

from pydantic import ValidationError

from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisCoverage,
    CorporateFilingsAnalysisFailure,
    CorporateFilingsAnalysisRequest,
    CorporateFilingsAnalysisResult,
    CorporateFilingsAnalysisStatus,
    CorporateFilingsCoverageStatus,
    CorporateFilingsFailureCategory,
    CorporateFilingsFailureStage,
    CorporateFilingsGroundingStatus,
)
from app.services.corporate_filings_analysis import (
    CorporateFilingsAnalysisCoordinationResult,
    CorporateFilingsAnalysisCoordinationStatus,
    CorporateFilingsAnalysisCoordinator,
)
from app.storage.adapters.corporate_filings_analysis_in_memory import (
    InMemoryCorporateFilingsAnalysisRepository,
)
from app.storage.corporate_filings_analysis_repositories import (
    StoredCorporateFilingsAnalysis,
)
from tests.unit.test_corporate_filings_analysis import completed_result
from tests.unit.test_corporate_filings_analysis_in_memory import (
    MutableClock,
    record,
    scoped_request,
)


UTC = timezone.utc
NOW = datetime(2026, 9, 14, 12, tzinfo=UTC)


class FakeAnalyzer:
    def __init__(self, results: list[CorporateFilingsAnalysisResult]) -> None:
        self._results = list(results)
        self.calls: list[CorporateFilingsAnalysisRequest] = []

    def analyze(
        self,
        request: CorporateFilingsAnalysisRequest,
    ) -> CorporateFilingsAnalysisResult:
        self.calls.append(request)
        if not self._results:
            raise AssertionError("unexpected corporate filings analysis call")
        return self._results.pop(0)


class InvalidAnalyzer:
    def analyze(self, request: CorporateFilingsAnalysisRequest) -> object:
        return object()


class CallbackAnalyzer(FakeAnalyzer):
    def __init__(self, result, callback) -> None:
        super().__init__([result])
        self._callback = callback

    def analyze(self, request):
        self._callback()
        return super().analyze(request)


class RacingRepository(InMemoryCorporateFilingsAnalysisRepository):
    def __init__(self, *, clock: MutableClock) -> None:
        super().__init__(clock=clock)
        self.rival: StoredCorporateFilingsAnalysis | None = None
        self.inject_race = False

    def commit_corporate_filings_analysis(
        self,
        value: StoredCorporateFilingsAnalysis,
        *,
        expected_active_record_id: str | None,
    ) -> StoredCorporateFilingsAnalysis:
        if self.inject_race:
            self.inject_race = False
            assert self.rival is not None
            super().commit_corporate_filings_analysis(
                self.rival,
                expected_active_record_id=expected_active_record_id,
            )
        return super().commit_corporate_filings_analysis(
            value,
            expected_active_record_id=expected_active_record_id,
        )


def failed_result(
    request: CorporateFilingsAnalysisRequest,
) -> CorporateFilingsAnalysisResult:
    documents = request.bundle.documents
    return CorporateFilingsAnalysisResult(
        analysis_id=f"failed:{request.request_fingerprint}",
        request_fingerprint=request.request_fingerprint,
        reuse_key_fingerprint=request.reuse_key.reuse_key_fingerprint,
        bundle_fingerprint=request.bundle.bundle_fingerprint,
        policy_fingerprint=request.policy.policy_fingerprint,
        status=CorporateFilingsAnalysisStatus.FAILED,
        grounding_status=CorporateFilingsGroundingStatus.INVALID_EVIDENCE,
        coverage=CorporateFilingsAnalysisCoverage(
            status=CorporateFilingsCoverageStatus.FAILED,
            expected_documents=len(documents),
            analyzed_documents=0,
            expected_chunks=sum(len(item.chunks) for item in documents),
            analyzed_chunks=0,
            available_citations=sum(
                len(item.citations) for item in documents
            ),
            cited_citations=0,
            unresolved_speaker_turns=0,
        ),
        failure=CorporateFilingsAnalysisFailure(
            stage=CorporateFilingsFailureStage.LLM_INVOCATION,
            category=CorporateFilingsFailureCategory.PROVIDER_REJECTED,
            retryable=False,
            sanitized_message="The LLM provider rejected its credential.",
        ),
    )


class CorporateFilingsAnalysisCoordinatorTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock(NOW)
        self.repository = InMemoryCorporateFilingsAnalysisRepository(
            clock=self.clock
        )

    def _coordinator(
        self,
        analyzer: object,
        *,
        repository=None,
    ) -> CorporateFilingsAnalysisCoordinator:
        return CorporateFilingsAnalysisCoordinator(
            analyzer=analyzer,
            repository=repository or self.repository,
            clock=self.clock,
        )

    def test_cache_miss_publishes_releaseable_analysis(self):
        request = scoped_request()
        result = completed_result(request)
        analyzer = FakeAnalyzer([result])

        outcome = self._coordinator(analyzer).coordinate(request)

        self.assertIs(
            outcome.status,
            CorporateFilingsAnalysisCoordinationStatus.ANALYSIS_PUBLISHED,
        )
        self.assertEqual(outcome.analysis_result, result)
        self.assertEqual(outcome.selected_analysis.result, result)
        self.assertEqual(analyzer.calls, [request])
        active = self.repository.get_active_corporate_filings_analysis(
            outcome.period_key,
            scope=outcome.scope,
            as_of=self.clock.value,
        )
        self.assertEqual(active, outcome.selected_analysis)

    def test_exact_cache_hit_does_not_invoke_analyzer(self):
        cached = record(stored_at=NOW - timedelta(minutes=1))
        self.repository.commit_corporate_filings_analysis(
            cached,
            expected_active_record_id=None,
        )
        analyzer = FakeAnalyzer([])

        outcome = self._coordinator(analyzer).coordinate(scoped_request())

        self.assertIs(
            outcome.status,
            CorporateFilingsAnalysisCoordinationStatus.EXACT_CACHE_REUSED,
        )
        self.assertEqual(outcome.selected_analysis, cached)
        self.assertEqual(analyzer.calls, [])

    def test_explicit_refresh_bypasses_exact_cache_and_is_idempotent(self):
        cached = record(stored_at=NOW - timedelta(minutes=1))
        self.repository.commit_corporate_filings_analysis(
            cached,
            expected_active_record_id=None,
        )
        analyzer = FakeAnalyzer([cached.result])

        outcome = self._coordinator(analyzer).coordinate(
            scoped_request(),
            refresh_requested=True,
        )

        self.assertIs(
            outcome.status,
            CorporateFilingsAnalysisCoordinationStatus.ANALYSIS_PUBLISHED,
        )
        self.assertTrue(outcome.refresh_requested)
        self.assertEqual(len(analyzer.calls), 1)
        self.assertEqual(outcome.selected_analysis, cached)

    def test_failed_refresh_preserves_previous_active_result(self):
        active = record(stored_at=NOW - timedelta(minutes=2))
        self.repository.commit_corporate_filings_analysis(
            active,
            expected_active_record_id=None,
        )
        request = scoped_request(prompt="prompt-b")
        analyzer = FakeAnalyzer([failed_result(request)])

        outcome = self._coordinator(analyzer).coordinate(
            request,
            refresh_requested=True,
        )

        self.assertIs(
            outcome.status,
            CorporateFilingsAnalysisCoordinationStatus.
            ANALYSIS_FAILED_ACTIVE_PRESERVED,
        )
        self.assertFalse(outcome.analysis_result.releaseable)
        self.assertEqual(outcome.selected_analysis, active)
        history = self.repository.list_corporate_filings_analysis_history(
            active.period_key,
            scope=active.scope,
            as_of=self.clock.value,
        )
        self.assertEqual(history, (active,))

    def test_failed_attempt_returns_latest_concurrently_published_active(self):
        first = record(stored_at=NOW - timedelta(minutes=3))
        self.repository.commit_corporate_filings_analysis(
            first,
            expected_active_record_id=None,
        )
        rival = record(
            prompt="prompt-c",
            stored_at=NOW - timedelta(minutes=1),
        )
        request = scoped_request(prompt="prompt-b")
        analyzer = CallbackAnalyzer(
            failed_result(request),
            lambda: self.repository.commit_corporate_filings_analysis(
                rival,
                expected_active_record_id=first.record_id,
            ),
        )

        outcome = self._coordinator(analyzer).coordinate(
            request,
            refresh_requested=True,
        )

        self.assertIs(
            outcome.status,
            CorporateFilingsAnalysisCoordinationStatus.
            ANALYSIS_FAILED_ACTIVE_PRESERVED,
        )
        self.assertEqual(outcome.selected_analysis, rival)

    def test_failed_analysis_without_active_result_stays_unreleased(self):
        request = scoped_request()
        analyzer = FakeAnalyzer([failed_result(request)])

        outcome = self._coordinator(analyzer).coordinate(request)

        self.assertIs(
            outcome.status,
            CorporateFilingsAnalysisCoordinationStatus.
            ANALYSIS_FAILED_NO_ACTIVE,
        )
        self.assertIsNone(outcome.selected_analysis)

    def test_same_reuse_key_conflict_preserves_active_result(self):
        active = record(stored_at=NOW - timedelta(minutes=2))
        self.repository.commit_corporate_filings_analysis(
            active,
            expected_active_record_id=None,
        )
        changed_result = active.result.model_copy(
            update={"analysis_id": "corporate-filing-analysis-regenerated"}
        )
        analyzer = FakeAnalyzer([changed_result])

        outcome = self._coordinator(analyzer).coordinate(
            scoped_request(),
            refresh_requested=True,
        )

        self.assertIs(
            outcome.status,
            CorporateFilingsAnalysisCoordinationStatus.
            PUBLICATION_CONFLICT_ACTIVE_PRESERVED,
        )
        self.assertEqual(outcome.selected_analysis, active)

    def test_reuse_conflict_without_active_result_releases_no_fallback(self):
        cached = record(stored_at=NOW - timedelta(minutes=2))
        self.repository.save_corporate_filings_analysis(cached)
        changed_result = cached.result.model_copy(
            update={"analysis_id": "corporate-filing-analysis-regenerated"}
        )

        outcome = self._coordinator(
            FakeAnalyzer([changed_result])
        ).coordinate(scoped_request(), refresh_requested=True)

        self.assertIs(
            outcome.status,
            CorporateFilingsAnalysisCoordinationStatus.
            PUBLICATION_CONFLICT_NO_ACTIVE,
        )
        self.assertIsNone(outcome.selected_analysis)
        self.assertEqual(
            self.repository.find_corporate_filings_analysis_by_reuse_key(
                cached.reuse_key,
                scope=cached.scope,
                as_of=self.clock.value,
            ),
            cached,
        )

    def test_concurrent_publication_returns_new_active_result(self):
        repository = RacingRepository(clock=self.clock)
        active = record(stored_at=NOW - timedelta(minutes=3))
        repository.commit_corporate_filings_analysis(
            active,
            expected_active_record_id=None,
        )
        request = scoped_request(prompt="prompt-b")
        candidate_result = completed_result(request)
        rival = record(
            prompt="prompt-c",
            stored_at=NOW - timedelta(minutes=1),
        )
        repository.rival = rival
        repository.inject_race = True

        outcome = self._coordinator(
            FakeAnalyzer([candidate_result]),
            repository=repository,
        ).coordinate(request, refresh_requested=True)

        self.assertIs(
            outcome.status,
            CorporateFilingsAnalysisCoordinationStatus.
            PUBLICATION_CONFLICT_ACTIVE_PRESERVED,
        )
        self.assertEqual(outcome.selected_analysis, rival)
        self.assertIsNone(
            repository.find_corporate_filings_analysis_by_reuse_key(
                request.reuse_key,
                scope=outcome.scope,
                as_of=self.clock.value,
            )
        )

    def test_failed_other_tenant_cannot_reuse_active_result(self):
        active = record(stored_at=NOW - timedelta(minutes=1))
        self.repository.commit_corporate_filings_analysis(
            active,
            expected_active_record_id=None,
        )
        request = scoped_request(tenant_id="tenant-2")

        outcome = self._coordinator(
            FakeAnalyzer([failed_result(request)])
        ).coordinate(request, refresh_requested=True)

        self.assertIs(
            outcome.status,
            CorporateFilingsAnalysisCoordinationStatus.
            ANALYSIS_FAILED_NO_ACTIVE,
        )
        self.assertIsNone(outcome.selected_analysis)

    def test_invalid_analyzer_result_and_refresh_flag_fail_closed(self):
        request = scoped_request()
        with self.assertRaisesRegex(ValueError, "invalid result"):
            self._coordinator(InvalidAnalyzer()).coordinate(request)
        with self.assertRaisesRegex(TypeError, "refresh flag"):
            self._coordinator(FakeAnalyzer([])).coordinate(
                request,
                refresh_requested="yes",
            )

    def test_dependencies_and_clock_are_validated(self):
        with self.assertRaisesRegex(TypeError, "analysis generator"):
            CorporateFilingsAnalysisCoordinator(
                analyzer=object(),
                repository=self.repository,
            )
        with self.assertRaisesRegex(TypeError, "repository"):
            CorporateFilingsAnalysisCoordinator(
                analyzer=FakeAnalyzer([]),
                repository=object(),
            )
        with self.assertRaisesRegex(TypeError, "clock"):
            CorporateFilingsAnalysisCoordinator(
                analyzer=FakeAnalyzer([]),
                repository=self.repository,
                clock=object(),
            )
        coordinator = CorporateFilingsAnalysisCoordinator(
            analyzer=FakeAnalyzer([]),
            repository=self.repository,
            clock=lambda: datetime(2026, 9, 14, 12),
        )
        with self.assertRaisesRegex(ValueError, "timezone"):
            coordinator.coordinate(scoped_request())

    def test_outcome_contract_is_immutable_and_rejects_inconsistent_state(self):
        request = scoped_request()
        outcome = self._coordinator(
            FakeAnalyzer([failed_result(request)])
        ).coordinate(request)

        with self.assertRaises(ValidationError):
            outcome.refresh_requested = True
        with self.assertRaisesRegex(ValidationError, "failed analysis"):
            CorporateFilingsAnalysisCoordinationResult(
                status=(
                    CorporateFilingsAnalysisCoordinationStatus.
                    ANALYSIS_FAILED_ACTIVE_PRESERVED
                ),
                request_fingerprint=request.request_fingerprint,
                scope=outcome.scope,
                period_key=outcome.period_key,
                refresh_requested=True,
                analysis_result=outcome.analysis_result,
            )


if __name__ == "__main__":
    unittest.main()
