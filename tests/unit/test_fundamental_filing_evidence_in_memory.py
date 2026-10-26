import unittest
from datetime import datetime, timedelta

from app.exceptions import StorageConflictError, StorageError
from app.fundamentals.filing_evidence_validator import (
    DeterministicFundamentalFilingEvidenceValidator,
)
from app.storage.adapters.fundamental_filing_evidence_in_memory import (
    InMemoryFundamentalFilingEvidenceRepository,
)
from app.storage.fundamental_filing_evidence_repositories import (
    FundamentalFilingEvidencePolicyKey,
    FundamentalFilingEvidenceRepository,
    StoredFundamentalFilingEvidence,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingRepositoryScope,
)
from tests.unit.test_fundamental_filing_evidence import extraction
from tests.unit.test_fundamental_filing_evidence_repositories import (
    stored_evidence,
)
from tests.unit.test_fundamental_gateway import build_connection, build_issuer


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def revised_record(*, minutes: int) -> StoredFundamentalFilingEvidence:
    first = extraction()
    revised = first.model_copy(
        update={"extracted_at": first.extracted_at + timedelta(minutes=minutes)}
    )
    reference = revised.artifact.reference
    validation = DeterministicFundamentalFilingEvidenceValidator().validate(
        revised,
        expected_issuer=reference.issuer,
        expected_period=reference.period,
        expected_source_class=reference.source_class,
    )
    return StoredFundamentalFilingEvidence.from_validated(
        evidence=revised,
        validation=validation,
        stored_at=revised.extracted_at + timedelta(seconds=1),
    )


class InMemoryFundamentalFilingEvidenceRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.first = stored_evidence()
        self.clock = MutableClock(self.first.stored_at + timedelta(hours=1))
        self.repository = InMemoryFundamentalFilingEvidenceRepository(
            clock=self.clock
        )
        self.scope = FundamentalFilingRepositoryScope.from_artifact(
            self.first.evidence.artifact
        )

    def test_implements_protocol_and_returns_defensive_copies(self):
        saved = self.repository.save_filing_evidence(self.first)
        loaded = self.repository.get_filing_evidence(
            self.first.record_id,
            scope=self.scope,
            as_of=self.clock.now,
        )

        self.assertIsInstance(
            self.repository,
            FundamentalFilingEvidenceRepository,
        )
        self.assertEqual(saved, self.first)
        self.assertEqual(loaded, self.first)
        self.assertIsNot(saved, self.first)
        self.assertIsNot(loaded, saved)
        self.assertIsNot(loaded.evidence, saved.evidence)

    def test_save_is_idempotent_and_conflict_preserves_original(self):
        saved = self.repository.save_filing_evidence(self.first)
        repeated = self.repository.save_filing_evidence(self.first)
        conflicting = self.first.model_copy(
            update={"stored_at": self.first.stored_at + timedelta(seconds=1)}
        )

        with self.assertRaises(StorageConflictError):
            self.repository.save_filing_evidence(conflicting)

        self.assertEqual(saved, repeated)
        self.assertEqual(
            self.repository.get_filing_evidence(
                self.first.record_id,
                scope=self.scope,
                as_of=self.clock.now,
            ),
            self.first,
        )

    def test_versions_and_policy_reads_are_canonical_and_point_in_time(self):
        self.repository.save_filing_evidence(self.first)
        second = revised_record(minutes=2)
        self.repository.save_filing_evidence(second)
        key = FundamentalFilingEvidencePolicyKey.from_record(self.first)

        before_second = self.repository.find_filing_evidence_by_policy(
            key,
            scope=self.scope,
            as_of=second.stored_at - timedelta(microseconds=1),
        )
        after_second = self.repository.list_filing_evidence_for_artifact(
            self.first.artifact_id,
            scope=self.scope,
            as_of=second.stored_at,
        )

        self.assertEqual(before_second, (self.first,))
        self.assertEqual(after_second, (self.first, second))
        self.assertEqual(
            self.repository.find_filing_evidence_by_policy(
                key,
                scope=self.scope,
                as_of=second.stored_at,
            ),
            (self.first, second),
        )

    def test_reads_are_tenant_connection_and_issuer_isolated(self):
        self.repository.save_filing_evidence(self.first)
        other_scope = FundamentalFilingRepositoryScope(
            connection=build_connection(
                tenant_id="tenant.other",
                provider_connection_id="provider.tijori.other",
            ),
            issuer=build_issuer(
                symbol="RELIANCE-EQ",
                legal_name="Reliance Industries Limited",
            ),
        )

        self.assertIsNone(
            self.repository.get_filing_evidence(
                self.first.record_id,
                scope=other_scope,
                as_of=self.clock.now,
            )
        )
        self.assertEqual(
            self.repository.list_filing_evidence_for_artifact(
                self.first.artifact_id,
                scope=other_scope,
                as_of=self.clock.now,
            ),
            (),
        )

    def test_future_write_and_naive_times_fail_without_mutation(self):
        self.clock.now = self.first.stored_at - timedelta(seconds=1)
        with self.assertRaises(StorageError):
            self.repository.save_filing_evidence(self.first)

        self.clock.now = datetime(2026, 9, 14, 12, 0)
        with self.assertRaises(ValueError):
            self.repository.save_filing_evidence(self.first)

        self.clock.now = self.first.stored_at + timedelta(hours=1)
        self.assertIsNone(
            self.repository.get_filing_evidence(
                self.first.record_id,
                scope=self.scope,
                as_of=self.clock.now,
            )
        )
        with self.assertRaises(ValueError):
            self.repository.get_filing_evidence(
                self.first.record_id,
                scope=self.scope,
                as_of=datetime(2026, 9, 14, 12, 0),
            )

    def test_failed_validation_remains_auditable_not_releaseable(self):
        self.assertFalse(self.first.validation.releaseable)

        stored = self.repository.save_filing_evidence(self.first)

        self.assertFalse(stored.releaseable)
        self.assertEqual(
            stored.validation.validation_fingerprint,
            self.first.validation.validation_fingerprint,
        )

    def test_invalid_identifiers_and_keys_fail_closed(self):
        with self.assertRaises(ValueError):
            self.repository.get_filing_evidence(
                "../record",
                scope=self.scope,
                as_of=self.clock.now,
            )
        with self.assertRaises(ValueError):
            self.repository.list_filing_evidence_for_artifact(
                "artifact",
                scope=self.scope,
                as_of=self.clock.now,
            )


if __name__ == "__main__":
    unittest.main()
