from datetime import datetime, timedelta, timezone
from hashlib import sha256
import unittest

from app.exceptions import StorageConflictError, StorageError
from app.models.corporate_filings_analysis import (
    CorporateFilingsAnalysisRequest,
    CorporateFilingsQuarterlyBundle,
)
from app.storage.adapters.corporate_filings_analysis_in_memory import (
    InMemoryCorporateFilingsAnalysisRepository,
)
from app.storage.corporate_filings_analysis_repositories import (
    CorporateFilingsAnalysisDeleteAuthorization,
    CorporateFilingsAnalysisDeleteStatus,
    CorporateFilingsAnalysisPeriodKey,
    CorporateFilingsAnalysisRepository,
    CorporateFilingsAnalysisRepositoryScope,
    StoredCorporateFilingsAnalysis,
)
from tests.unit.test_corporate_filings_analysis import (
    analysis_request,
    completed_result,
)


UTC = timezone.utc
STORED_AT = datetime(2026, 9, 14, 11, tzinfo=UTC)


class MutableClock:
    def __init__(self, value: datetime) -> None:
        self.value = value

    def __call__(self) -> datetime:
        return self.value


def scoped_request(
    *,
    prompt: str = "prompt-a",
    tenant_id: str = "tenant-1",
) -> CorporateFilingsAnalysisRequest:
    base = analysis_request(
        prompt_sha256=sha256(prompt.encode("utf-8")).hexdigest()
    )
    if tenant_id == base.bundle.connection.tenant_id:
        return base
    connection = base.bundle.connection.model_copy(
        update={"tenant_id": tenant_id}
    )
    bundle_payload = base.bundle.model_dump(exclude_computed_fields=True)
    bundle_payload["connection"] = connection
    bundle = CorporateFilingsQuarterlyBundle.model_validate(bundle_payload)
    request_payload = base.model_dump(
        exclude={"bundle"},
        exclude_computed_fields=True,
    )
    request_payload["bundle"] = bundle
    return CorporateFilingsAnalysisRequest.model_validate(request_payload)


def record(
    *,
    prompt: str = "prompt-a",
    tenant_id: str = "tenant-1",
    stored_at: datetime = STORED_AT,
) -> StoredCorporateFilingsAnalysis:
    request = scoped_request(prompt=prompt, tenant_id=tenant_id)
    return StoredCorporateFilingsAnalysis.from_validated(
        request=request,
        result=completed_result(request),
        stored_at=stored_at,
    )


def delete_authorization(
    value: StoredCorporateFilingsAnalysis,
    *,
    audit_id: str,
    authorized_at: datetime,
) -> CorporateFilingsAnalysisDeleteAuthorization:
    return CorporateFilingsAnalysisDeleteAuthorization(
        audit_id=audit_id,
        operation_id=f"operation-{audit_id}",
        authorized_by="owner-1",
        record_id=value.record_id,
        record_fingerprint=value.record_fingerprint,
        reason="Owner requested removal of this inactive cached analysis.",
        authorized_at=authorized_at,
    )


class InMemoryCorporateFilingsAnalysisRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock(datetime(2026, 9, 14, 12, tzinfo=UTC))
        self.repository = InMemoryCorporateFilingsAnalysisRepository(
            clock=self.clock
        )
        self.first = record()
        self.scope = self.first.scope
        self.period_key = self.first.period_key

    def test_implements_database_neutral_repository_protocol(self):
        self.assertIsInstance(
            self.repository,
            CorporateFilingsAnalysisRepository,
        )

    def test_save_is_idempotent_cacheable_and_defensively_copied(self):
        saved = self.repository.save_corporate_filings_analysis(self.first)
        repeated = self.repository.save_corporate_filings_analysis(self.first)
        before_storage = (
            self.repository.find_corporate_filings_analysis_by_reuse_key(
                self.first.reuse_key,
                scope=self.scope,
                as_of=self.first.stored_at - timedelta(microseconds=1),
            )
        )
        cached = self.repository.find_corporate_filings_analysis_by_reuse_key(
            self.first.reuse_key,
            scope=self.scope,
            as_of=self.first.stored_at,
        )

        self.assertEqual(saved, self.first)
        self.assertEqual(repeated, self.first)
        self.assertEqual(cached, self.first)
        self.assertIsNot(saved, self.first)
        self.assertIsNot(repeated, saved)
        self.assertIsNot(cached, saved)
        self.assertIsNone(before_storage)
        self.assertIsNone(
            self.repository.get_active_corporate_filings_analysis(
                self.period_key,
                scope=self.scope,
                as_of=self.clock.value,
            )
        )

    def test_record_and_reuse_key_conflicts_preserve_original(self):
        self.repository.save_corporate_filings_analysis(self.first)
        same_id_different_storage_time = self.first.model_copy(
            update={"stored_at": self.first.stored_at + timedelta(minutes=1)}
        )
        with self.assertRaisesRegex(StorageConflictError, "record ID"):
            self.repository.save_corporate_filings_analysis(
                same_id_different_storage_time
            )

        request = scoped_request()
        changed_result = completed_result(request).model_copy(
            update={"analysis_id": "corporate-filing-analysis-revised"}
        )
        same_key_different_result = StoredCorporateFilingsAnalysis.from_validated(
            request=request,
            result=changed_result,
            stored_at=self.first.stored_at,
        )
        with self.assertRaisesRegex(StorageConflictError, "reuse key"):
            self.repository.save_corporate_filings_analysis(
                same_key_different_result
            )

        cached = self.repository.find_corporate_filings_analysis_by_reuse_key(
            self.first.reuse_key,
            scope=self.scope,
            as_of=self.clock.value,
        )
        self.assertEqual(cached, self.first)

    def test_reads_are_tenant_isolated(self):
        self.repository.commit_corporate_filings_analysis(
            self.first,
            expected_active_record_id=None,
        )
        repeated = self.repository.commit_corporate_filings_analysis(
            self.first,
            expected_active_record_id=self.first.record_id,
        )
        self.assertEqual(repeated, self.first)
        self.assertEqual(
            self.repository.list_corporate_filings_analysis_history(
                self.period_key,
                scope=self.scope,
                as_of=self.clock.value,
            ),
            (self.first,),
        )
        other = record(tenant_id="tenant-2")

        self.assertIsNone(
            self.repository.find_corporate_filings_analysis_by_reuse_key(
                self.first.reuse_key,
                scope=other.scope,
                as_of=self.clock.value,
            )
        )
        self.assertEqual(
            self.repository.list_corporate_filings_analysis_history(
                self.period_key,
                scope=other.scope,
                as_of=self.clock.value,
            ),
            (),
        )
        self.assertIsNone(
            self.repository.get_active_corporate_filings_analysis(
                self.period_key,
                scope=other.scope,
                as_of=self.clock.value,
            )
        )

    def test_cas_conflict_rolls_back_candidate_and_preserves_active(self):
        active = self.repository.commit_corporate_filings_analysis(
            self.first,
            expected_active_record_id=None,
        )
        candidate = record(
            prompt="prompt-b",
            stored_at=self.first.stored_at + timedelta(minutes=10),
        )

        with self.assertRaisesRegex(StorageConflictError, "changed before commit"):
            self.repository.commit_corporate_filings_analysis(
                candidate,
                expected_active_record_id=None,
            )

        self.assertEqual(
            self.repository.get_active_corporate_filings_analysis(
                self.period_key,
                scope=self.scope,
                as_of=self.clock.value,
            ),
            active,
        )
        self.assertIsNone(
            self.repository.find_corporate_filings_analysis_by_reuse_key(
                candidate.reuse_key,
                scope=self.scope,
                as_of=self.clock.value,
            )
        )
        self.assertEqual(
            self.repository.list_corporate_filings_analysis_history(
                self.period_key,
                scope=self.scope,
                as_of=self.clock.value,
            ),
            (self.first,),
        )

    def test_successful_cas_replacement_preserves_history_and_as_of_state(self):
        first_activation = self.clock.value
        self.repository.commit_corporate_filings_analysis(
            self.first,
            expected_active_record_id=None,
        )
        candidate = record(
            prompt="prompt-b",
            stored_at=self.first.stored_at + timedelta(minutes=10),
        )
        self.clock.value += timedelta(minutes=1)
        second_activation = self.clock.value
        self.repository.commit_corporate_filings_analysis(
            candidate,
            expected_active_record_id=self.first.record_id,
        )

        before_replacement = (
            self.repository.get_active_corporate_filings_analysis(
                self.period_key,
                scope=self.scope,
                as_of=first_activation + timedelta(seconds=30),
            )
        )
        after_replacement = (
            self.repository.get_active_corporate_filings_analysis(
                self.period_key,
                scope=self.scope,
                as_of=second_activation,
            )
        )
        history = self.repository.list_corporate_filings_analysis_history(
            self.period_key,
            scope=self.scope,
            as_of=second_activation,
        )

        self.assertEqual(before_replacement, self.first)
        self.assertEqual(after_replacement, candidate)
        self.assertEqual(history, (self.first, candidate))

    def test_future_candidate_cannot_change_active_result(self):
        self.repository.commit_corporate_filings_analysis(
            self.first,
            expected_active_record_id=None,
        )
        future = record(
            prompt="prompt-future",
            stored_at=self.clock.value + timedelta(minutes=1),
        )

        with self.assertRaisesRegex(StorageError, "future"):
            self.repository.commit_corporate_filings_analysis(
                future,
                expected_active_record_id=self.first.record_id,
            )

        active = self.repository.get_active_corporate_filings_analysis(
            self.period_key,
            scope=self.scope,
            as_of=self.clock.value,
        )
        self.assertEqual(active, self.first)

    def test_active_delete_is_rejected_then_inactive_delete_is_idempotent(self):
        self.repository.commit_corporate_filings_analysis(
            self.first,
            expected_active_record_id=None,
        )
        active_authorization = delete_authorization(
            self.first,
            audit_id="audit-active",
            authorized_at=self.clock.value,
        )
        rejected = self.repository.delete_corporate_filings_analysis(
            self.first.record_id,
            scope=self.scope,
            authorization=active_authorization,
        )
        self.assertEqual(
            rejected.status,
            CorporateFilingsAnalysisDeleteStatus.REJECTED_ACTIVE_RESULT,
        )

        candidate = record(
            prompt="prompt-b",
            stored_at=self.first.stored_at + timedelta(minutes=10),
        )
        self.clock.value += timedelta(minutes=1)
        self.repository.commit_corporate_filings_analysis(
            candidate,
            expected_active_record_id=self.first.record_id,
        )
        authorization = delete_authorization(
            self.first,
            audit_id="audit-inactive",
            authorized_at=self.clock.value,
        )
        deleted = self.repository.delete_corporate_filings_analysis(
            self.first.record_id,
            scope=self.scope,
            authorization=authorization,
        )
        repeated = self.repository.delete_corporate_filings_analysis(
            self.first.record_id,
            scope=self.scope,
            authorization=authorization,
        )

        self.assertEqual(
            deleted.status,
            CorporateFilingsAnalysisDeleteStatus.DELETED,
        )
        self.assertEqual(repeated, deleted)
        self.assertIsNone(
            self.repository.find_corporate_filings_analysis_by_reuse_key(
                self.first.reuse_key,
                scope=self.scope,
                as_of=self.clock.value,
            )
        )
        self.assertEqual(
            self.repository.get_active_corporate_filings_analysis(
                self.period_key,
                scope=self.scope,
                as_of=self.clock.value,
            ),
            candidate,
        )

    def test_cross_tenant_delete_is_not_found_and_does_not_mutate(self):
        self.repository.save_corporate_filings_analysis(self.first)
        other_scope = record(tenant_id="tenant-2").scope
        authorization = delete_authorization(
            self.first,
            audit_id="audit-cross-tenant",
            authorized_at=self.clock.value,
        )

        receipt = self.repository.delete_corporate_filings_analysis(
            self.first.record_id,
            scope=other_scope,
            authorization=authorization,
        )

        self.assertEqual(
            receipt.status,
            CorporateFilingsAnalysisDeleteStatus.NOT_FOUND,
        )
        cached = self.repository.find_corporate_filings_analysis_by_reuse_key(
            self.first.reuse_key,
            scope=self.scope,
            as_of=self.clock.value,
        )
        self.assertEqual(cached, self.first)

    def test_invalid_times_identifiers_and_authorization_fail_closed(self):
        with self.assertRaisesRegex(ValueError, "timezone"):
            self.repository.list_corporate_filings_analysis_history(
                self.period_key,
                scope=self.scope,
                as_of=datetime(2026, 9, 14, 12),
            )
        with self.assertRaisesRegex(ValueError, "record ID"):
            self.repository.delete_corporate_filings_analysis(
                "invalid",
                scope=self.scope,
                authorization=delete_authorization(
                    self.first,
                    audit_id="audit-invalid",
                    authorized_at=self.clock.value,
                ),
            )
        future_authorization = delete_authorization(
            self.first,
            audit_id="audit-future",
            authorized_at=self.clock.value + timedelta(minutes=1),
        )
        with self.assertRaisesRegex(StorageError, "future"):
            self.repository.delete_corporate_filings_analysis(
                self.first.record_id,
                scope=self.scope,
                authorization=future_authorization,
            )


if __name__ == "__main__":
    unittest.main()
