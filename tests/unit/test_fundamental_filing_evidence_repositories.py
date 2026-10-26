import unittest
from datetime import timedelta

from pydantic import ValidationError

from app.fundamentals.filing_evidence_validator import (
    DeterministicFundamentalFilingEvidenceValidator,
)
from app.storage.fundamental_filing_evidence_repositories import (
    FundamentalFilingEvidencePolicyKey,
    FundamentalFilingEvidenceRepository,
    StoredFundamentalFilingEvidence,
    validation_policy_fingerprint,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingRepositoryScope,
)
from tests.unit.test_fundamental_filing_evidence import extraction
from tests.unit.test_fundamental_filings import COMMITTED_AT


def stored_evidence(**updates) -> StoredFundamentalFilingEvidence:
    evidence = extraction()
    reference = evidence.artifact.reference
    validation = DeterministicFundamentalFilingEvidenceValidator().validate(
        evidence,
        expected_issuer=reference.issuer,
        expected_period=reference.period,
        expected_source_class=reference.source_class,
    )
    values = {
        "evidence": evidence,
        "validation": validation,
        "stored_at": COMMITTED_AT + timedelta(minutes=2),
    }
    values.update(updates)
    return StoredFundamentalFilingEvidence.from_validated(**values)


class CompleteFilingEvidenceRepository:
    adapter_name = "contract_test"

    def save_filing_evidence(self, record):
        return record

    def get_filing_evidence(self, record_id, *, scope, as_of):
        del record_id, scope, as_of
        return None

    def list_filing_evidence_for_artifact(
        self,
        artifact_id,
        *,
        scope,
        as_of,
    ):
        del artifact_id, scope, as_of
        return ()

    def find_filing_evidence_by_policy(self, key, *, scope, as_of):
        del key, scope, as_of
        return ()


class IncompleteFilingEvidenceRepository:
    adapter_name = "incomplete"


class FundamentalFilingEvidenceRepositoryContractTests(unittest.TestCase):
    def test_record_binds_artifact_extraction_and_policy_fingerprints(self):
        record = stored_evidence()

        self.assertEqual(record.artifact_id, record.evidence.artifact.artifact_id)
        self.assertEqual(
            record.artifact_fingerprint,
            record.evidence.artifact.artifact_fingerprint,
        )
        self.assertEqual(
            record.extraction_policy_fingerprint,
            record.evidence.policy.policy_fingerprint,
        )
        self.assertEqual(
            record.validation_policy_fingerprint,
            validation_policy_fingerprint(
                policy_id=record.validation.policy_id,
                policy_version=record.validation.policy_version,
            ),
        )
        self.assertEqual(
            record.validation.extraction_fingerprint,
            record.extraction_fingerprint,
        )
        self.assertEqual(len(record.record_fingerprint), 64)
        self.assertTrue(record.record_id.startswith("filing_evidence_record:"))

    def test_record_round_trips_complete_json_without_changing_identity(self):
        record = stored_evidence()

        loaded = StoredFundamentalFilingEvidence.from_storage_json(
            record.to_storage_json()
        )

        self.assertEqual(loaded, record)
        self.assertEqual(loaded.record_id, record.record_id)
        self.assertEqual(
            loaded.validation.validation_fingerprint,
            record.validation.validation_fingerprint,
        )

    def test_record_rejects_mismatched_bindings_and_timestamps(self):
        record = stored_evidence()
        payload = record.model_dump(exclude_computed_fields=True)
        with self.assertRaisesRegex(ValidationError, "artifact_fingerprint"):
            StoredFundamentalFilingEvidence.model_validate(
                {**payload, "artifact_fingerprint": "f" * 64}
            )
        with self.assertRaisesRegex(ValidationError, "cannot predate"):
            StoredFundamentalFilingEvidence.model_validate(
                {
                    **payload,
                    "stored_at": record.evidence.extracted_at
                    - timedelta(seconds=1),
                }
            )

        other_validation = record.validation.__class__(
            policy_id=record.validation.policy_id,
            policy_version=record.validation.policy_version,
            extraction_fingerprint="e" * 64,
            releaseable=record.validation.releaseable,
            findings=record.validation.findings,
            evidence_page_spans=record.validation.evidence_page_spans,
        )
        with self.assertRaisesRegex(ValidationError, "does not bind"):
            StoredFundamentalFilingEvidence.model_validate(
                {
                    **payload,
                    "validation": other_validation,
                    "validation_fingerprint": (
                        other_validation.validation_fingerprint
                    ),
                }
            )

    def test_policy_key_and_scope_preserve_exact_storage_boundaries(self):
        record = stored_evidence()
        key = FundamentalFilingEvidencePolicyKey.from_record(record)
        scope = FundamentalFilingRepositoryScope.from_artifact(
            record.evidence.artifact
        )

        self.assertEqual(key.artifact_id, record.artifact_id)
        self.assertEqual(
            key.validation_policy_fingerprint,
            record.validation_policy_fingerprint,
        )
        self.assertEqual(scope, record.scope)
        self.assertEqual(
            scope.connection,
            record.evidence.artifact.reference.connection,
        )
        self.assertEqual(scope.issuer, record.evidence.artifact.reference.issuer)

    def test_runtime_protocol_requires_the_complete_bounded_surface(self):
        self.assertIsInstance(
            CompleteFilingEvidenceRepository(),
            FundamentalFilingEvidenceRepository,
        )
        self.assertNotIsInstance(
            IncompleteFilingEvidenceRepository(),
            FundamentalFilingEvidenceRepository,
        )

        methods = set(dir(FundamentalFilingEvidenceRepository))
        self.assertNotIn("execute_sql", methods)
        self.assertNotIn("delete_all", methods)
        self.assertNotIn("list_all_tenants", methods)
        self.assertNotIn("purge_expired", methods)


if __name__ == "__main__":
    unittest.main()
