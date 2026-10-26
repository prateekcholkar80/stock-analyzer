from datetime import datetime, timedelta, timezone
import unittest

from pydantic import ValidationError

from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisCoverage,
    CorporateFilingsAnalysisFailure,
    CorporateFilingsAnalysisResult,
    CorporateFilingsAnalysisStatus,
    CorporateFilingsCoverageStatus,
    CorporateFilingsFailureCategory,
    CorporateFilingsFailureStage,
    CorporateFilingsGroundingStatus,
)
from app.storage.corporate_filings_analysis_repositories import (
    CorporateFilingsAnalysisDeleteAuthorization,
    CorporateFilingsAnalysisDeleteReceipt,
    CorporateFilingsAnalysisDeleteStatus,
    CorporateFilingsAnalysisPeriodKey,
    CorporateFilingsAnalysisRepository,
    CorporateFilingsAnalysisRepositoryScope,
    StoredCorporateFilingsAnalysis,
)
from tests.unit.test_corporate_filings_analysis import (
    analysis_request,
    completed_result,
    digest,
)


NOW = datetime(2026, 9, 14, 11, tzinfo=timezone.utc)


def stored_analysis() -> StoredCorporateFilingsAnalysis:
    request = analysis_request()
    result = completed_result(request)
    return StoredCorporateFilingsAnalysis.from_validated(
        request=request,
        result=result,
        stored_at=NOW,
    )


class CompleteRepository:
    @property
    def adapter_name(self):
        return "complete"

    def save_corporate_filings_analysis(self, record):
        return record

    def find_corporate_filings_analysis_by_reuse_key(
        self,
        reuse_key,
        *,
        scope,
        as_of,
    ):
        return None

    def list_corporate_filings_analysis_history(
        self,
        period_key,
        *,
        scope,
        as_of,
    ):
        return ()

    def get_active_corporate_filings_analysis(
        self,
        period_key,
        *,
        scope,
        as_of,
    ):
        return None

    def commit_corporate_filings_analysis(
        self,
        record,
        *,
        expected_active_record_id,
    ):
        return record

    def delete_corporate_filings_analysis(
        self,
        record_id,
        *,
        scope,
        authorization,
    ):
        raise NotImplementedError


class IncompleteRepository:
    @property
    def adapter_name(self):
        return "incomplete"


class CorporateFilingsAnalysisRepositoryContractTests(unittest.TestCase):
    def test_stored_record_binds_scope_period_policy_model_and_result(self):
        request = analysis_request()
        result = completed_result(request)

        record = StoredCorporateFilingsAnalysis.from_validated(
            request=request,
            result=result,
            stored_at=NOW,
        )

        self.assertEqual(
            record.scope,
            CorporateFilingsAnalysisRepositoryScope.from_request(request),
        )
        self.assertEqual(
            record.period_key,
            CorporateFilingsAnalysisPeriodKey.from_request(request),
        )
        self.assertEqual(record.result, result)
        self.assertEqual(
            record.reuse_key.reuse_key_fingerprint,
            request.reuse_key.reuse_key_fingerprint,
        )
        self.assertEqual(
            record.record_id,
            f"corporate_filings_analysis:{record.record_fingerprint}",
        )

    def test_storage_json_round_trip_excludes_raw_chunks_and_prompt(self):
        record = stored_analysis()

        payload = record.to_storage_json()
        restored = StoredCorporateFilingsAnalysis.from_storage_json(payload)

        self.assertEqual(restored, record)
        self.assertNotIn("Demand remains resilient", payload)
        self.assertNotIn('"prompt"', payload)
        self.assertNotIn('"chunks"', payload)

    def test_record_identity_is_reproducible_and_ignores_storage_time(self):
        request = analysis_request()
        result = completed_result(request)
        first = StoredCorporateFilingsAnalysis.from_validated(
            request=request,
            result=result,
            stored_at=NOW,
        )
        later = StoredCorporateFilingsAnalysis.from_validated(
            request=request,
            result=result,
            stored_at=NOW + timedelta(minutes=1),
        )

        self.assertEqual(first.record_id, later.record_id)
        self.assertEqual(first.record_fingerprint, later.record_fingerprint)

    def test_tampered_bindings_and_nonreleaseable_results_fail_closed(self):
        request = analysis_request()
        result = completed_result(request)
        tampered = result.model_copy(
            update={"policy_fingerprint": digest("different-policy")}
        )

        with self.assertRaisesRegex(ValueError, "changes its policy"):
            StoredCorporateFilingsAnalysis.from_validated(
                request=request,
                result=tampered,
                stored_at=NOW,
            )

        abstained = CorporateFilingsAnalysisResult(
            analysis_id="corporate-filing-analysis-abstained",
            request_fingerprint=request.request_fingerprint,
            reuse_key_fingerprint=request.reuse_key.reuse_key_fingerprint,
            bundle_fingerprint=request.bundle.bundle_fingerprint,
            policy_fingerprint=request.policy.policy_fingerprint,
            status=CorporateFilingsAnalysisStatus.ABSTAINED,
            grounding_status=(
                CorporateFilingsGroundingStatus.INSUFFICIENT_EVIDENCE
            ),
            coverage=CorporateFilingsAnalysisCoverage(
                status=CorporateFilingsCoverageStatus.FAILED,
                expected_documents=1,
                analyzed_documents=0,
                expected_chunks=1,
                analyzed_chunks=0,
                available_citations=2,
                cited_citations=0,
                unresolved_speaker_turns=0,
            ),
            failure=CorporateFilingsAnalysisFailure(
                stage=CorporateFilingsFailureStage.PREFLIGHT,
                category=(
                    CorporateFilingsFailureCategory.INSUFFICIENT_EVIDENCE
                ),
                retryable=False,
                sanitized_message="Released conference-call evidence is incomplete.",
            ),
        )
        with self.assertRaisesRegex(ValueError, "only releaseable"):
            StoredCorporateFilingsAnalysis.from_validated(
                request=request,
                result=abstained,
                stored_at=NOW,
            )

    def test_record_rejects_storage_before_completion_and_extra_fields(self):
        request = analysis_request()
        result = completed_result(request)
        with self.assertRaisesRegex(ValidationError, "before completion"):
            StoredCorporateFilingsAnalysis.from_validated(
                request=request,
                result=result,
                stored_at=result.execution.completed_at - timedelta(seconds=1),
            )

        payload = stored_analysis().model_dump(exclude_computed_fields=True)
        payload["raw_prompt"] = "not permitted"
        with self.assertRaises(ValidationError):
            StoredCorporateFilingsAnalysis.model_validate(payload)

    def test_repository_protocol_requires_complete_bounded_surface(self):
        self.assertIsInstance(CompleteRepository(), CorporateFilingsAnalysisRepository)
        self.assertNotIsInstance(
            IncompleteRepository(),
            CorporateFilingsAnalysisRepository,
        )

    def test_delete_authorization_and_receipt_bind_exact_record(self):
        record = stored_analysis()
        authorization = CorporateFilingsAnalysisDeleteAuthorization(
            audit_id="audit-1",
            operation_id="operation-delete-1",
            authorized_by="owner-1",
            record_id=record.record_id,
            record_fingerprint=record.record_fingerprint,
            reason="Owner requested removal of this inactive cached analysis.",
            authorized_at=NOW + timedelta(minutes=1),
        )
        receipt = CorporateFilingsAnalysisDeleteReceipt(
            authorization=authorization,
            status=CorporateFilingsAnalysisDeleteStatus.DELETED,
            completed_at=NOW + timedelta(minutes=2),
        )

        self.assertTrue(receipt.deleted)
        with self.assertRaisesRegex(ValidationError, "exact record"):
            CorporateFilingsAnalysisDeleteAuthorization(
                **authorization.model_dump(
                    exclude={"record_fingerprint"},
                    exclude_computed_fields=True,
                ),
                record_fingerprint=digest("different-record"),
            )

    def test_rejected_active_delete_requires_safe_detail(self):
        record = stored_analysis()
        authorization = CorporateFilingsAnalysisDeleteAuthorization(
            audit_id="audit-2",
            operation_id="operation-delete-2",
            authorized_by="owner-1",
            record_id=record.record_id,
            record_fingerprint=record.record_fingerprint,
            reason="Owner requested removal of this inactive cached analysis.",
            authorized_at=NOW + timedelta(minutes=1),
        )

        with self.assertRaisesRegex(ValidationError, "safe explanation"):
            CorporateFilingsAnalysisDeleteReceipt(
                authorization=authorization,
                status=(
                    CorporateFilingsAnalysisDeleteStatus.REJECTED_ACTIVE_RESULT
                ),
                completed_at=NOW + timedelta(minutes=2),
            )

    def test_scope_period_and_deletion_times_fail_closed(self):
        request = analysis_request()
        period_payload = CorporateFilingsAnalysisPeriodKey.from_request(
            request
        ).model_dump(exclude_computed_fields=True)
        period_payload["fiscal_quarter"] = 5
        with self.assertRaises(ValidationError):
            CorporateFilingsAnalysisPeriodKey.model_validate(period_payload)

        record = stored_analysis()
        with self.assertRaisesRegex(ValidationError, "timezone"):
            CorporateFilingsAnalysisDeleteAuthorization(
                audit_id="audit-3",
                operation_id="operation-delete-3",
                authorized_by="owner-1",
                record_id=record.record_id,
                record_fingerprint=record.record_fingerprint,
                reason="Owner requested removal of this inactive cached analysis.",
                authorized_at=datetime(2026, 9, 14, 11),
            )


if __name__ == "__main__":
    unittest.main()
