from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import unittest

from app.exceptions import StorageConflictError, StorageError
from app.models.fundamental_filings import (
    FundamentalFilingArtifactManifest,
    FundamentalFilingPackage,
    FundamentalFilingPackageManifest,
)
from app.storage.adapters.fundamental_filing_in_memory import (
    InMemoryFundamentalFilingRepository,
)
from app.storage.fundamental_filing_repositories import (
    FundamentalFilingDeleteAuthorization,
    FundamentalFilingDeleteStatus,
    FundamentalFilingDeleteTarget,
    FundamentalFilingRepository,
    FundamentalFilingRepositoryScope,
)
from tests.unit.test_fundamental_filing_repositories import (
    AUTHORIZED_AT,
    delete_authorization,
)
from tests.unit.test_fundamental_filings import (
    COMMITTED_AT,
    DOWNLOADED_AT,
    PDF_HASH,
    RECHECK_AFTER,
    REQUESTED_AT,
    artifact,
    discovery,
    package,
)
from tests.unit.test_fundamental_gateway import build_connection, build_issuer


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def revised_artifact(
    *,
    sequence: int,
    connection=None,
    issuer=None,
) -> FundamentalFilingArtifactManifest:
    content_hash = f"{sequence + 10:064x}"
    base = artifact()
    selected_connection = connection or base.reference.connection
    selected_issuer = issuer or base.reference.issuer
    checked_at = DOWNLOADED_AT + timedelta(minutes=sequence)
    updated_reference = base.reference.model_copy(
        update={
            "connection": selected_connection,
            "issuer": selected_issuer,
        }
    )
    supersedes_artifact_id = (
        base.artifact_id
        if connection is None and issuer is None
        else None
    )
    return FundamentalFilingArtifactManifest(
        reference=updated_reference,
        content_sha256=content_hash,
        safe_relative_path=(
            "tenant/provider/issuer/FY2027/Q1/earnings-release/"
            f"{content_hash}.pdf"
        ),
        byte_length=base.byte_length + sequence,
        page_count=base.page_count,
        pdf_version=base.pdf_version,
        downloaded_at=checked_at,
        last_checked_at=checked_at,
        recheck_after=checked_at + timedelta(days=10),
        supersedes_artifact_id=supersedes_artifact_id,
    )


def revised_package(
    *,
    sequence: int,
    connection=None,
    issuer=None,
) -> FundamentalFilingPackage:
    filing_artifact = revised_artifact(
        sequence=sequence,
        connection=connection,
        issuer=issuer,
    )
    filing_discovery = discovery(
        references=(filing_artifact.reference,),
        connection=filing_artifact.reference.connection,
        issuer=filing_artifact.reference.issuer,
        discovered_at=filing_artifact.reference.discovered_at,
    )
    committed_at = COMMITTED_AT + timedelta(minutes=sequence)
    manifest = FundamentalFilingPackageManifest(
        operation_id=f"operation-filing-refresh-{sequence}",
        connection=filing_artifact.reference.connection,
        issuer=filing_artifact.reference.issuer,
        requested_at=REQUESTED_AT,
        committed_at=committed_at,
        discovery_fingerprint=filing_discovery.discovery_fingerprint,
        artifact_ids=(filing_artifact.artifact_id,),
        recheck_after=filing_artifact.recheck_after,
    )
    return FundamentalFilingPackage(
        manifest=manifest,
        discovery=filing_discovery,
        artifacts=(filing_artifact,),
    )


def authorization_for(
    value: FundamentalFilingArtifactManifest | FundamentalFilingPackage,
    *,
    authorized_at: datetime = AUTHORIZED_AT,
) -> FundamentalFilingDeleteAuthorization:
    if isinstance(value, FundamentalFilingArtifactManifest):
        return delete_authorization(
            target_kind=FundamentalFilingDeleteTarget.ARTIFACT,
            target_id=value.artifact_id,
            target_fingerprint=value.artifact_fingerprint,
            authorized_at=authorized_at,
        )
    return delete_authorization(
        target_kind=FundamentalFilingDeleteTarget.PACKAGE,
        target_id=value.package_id,
        target_fingerprint=value.manifest.manifest_fingerprint,
        authorized_at=authorized_at,
    )


class InMemoryFundamentalFilingRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock(AUTHORIZED_AT + timedelta(hours=1))
        self.repository = InMemoryFundamentalFilingRepository(clock=self.clock)
        self.first = package()
        self.artifact = self.first.artifacts[0]
        self.scope = FundamentalFilingRepositoryScope.from_package(self.first)

    def test_implements_protocol_and_returns_defensive_copies(self):
        saved = self.repository.save_filing_artifact(self.artifact)
        committed = self.repository.commit_filing_package(
            self.first,
            expected_active_package_id=None,
        )
        loaded_artifact = self.repository.get_filing_artifact(
            self.artifact.artifact_id,
            scope=self.scope,
        )
        loaded_package = self.repository.get_active_filing_package(
            scope=self.scope
        )

        self.assertIsInstance(self.repository, FundamentalFilingRepository)
        self.assertEqual(
            self.repository.adapter_name,
            "in_memory_fundamental_filings",
        )
        self.assertEqual(saved, self.artifact)
        self.assertEqual(committed, self.first)
        self.assertEqual(loaded_artifact, self.artifact)
        self.assertEqual(loaded_package, self.first)
        self.assertIsNot(saved, self.artifact)
        self.assertIsNot(loaded_artifact, saved)
        self.assertIsNot(loaded_package, committed)
        self.assertIsNot(loaded_package.artifacts[0], committed.artifacts[0])

    def test_artifact_save_is_idempotent_and_queries_are_deterministic(self):
        repeated = self.repository.save_filing_artifact(self.artifact)
        self.assertEqual(
            self.repository.save_filing_artifact(self.artifact),
            repeated,
        )
        second = revised_artifact(sequence=2)
        self.clock.now = second.last_checked_at + timedelta(seconds=1)
        self.repository.save_filing_artifact(second)

        versions = self.repository.list_filing_artifact_versions(
            self.artifact.reference.filing_reference_id,
            scope=self.scope,
        )
        hashes = self.repository.find_filing_artifacts_by_content_hash(
            PDF_HASH,
            scope=self.scope,
        )

        self.assertEqual(versions, (self.artifact, second))
        self.assertEqual(hashes, (self.artifact,))

    def test_artifact_version_links_must_be_resolved_and_consistent(self):
        self.repository.save_filing_artifact(self.artifact)
        revised = revised_artifact(sequence=2)
        self.clock.now = revised.last_checked_at + timedelta(seconds=1)

        dangling = revised.model_copy(
            update={"supersedes_artifact_id": "filing_artifact:" + "f" * 64}
        )
        with self.assertRaises(StorageConflictError):
            self.repository.save_filing_artifact(dangling)

        invalid_duplicate = revised.model_copy(
            update={
                "supersedes_artifact_id": None,
                "duplicate_of_artifact_id": self.artifact.artifact_id,
            }
        )
        with self.assertRaises(StorageConflictError):
            self.repository.save_filing_artifact(invalid_duplicate)

        self.assertIsNone(
            self.repository.get_filing_artifact(
                revised.artifact_id,
                scope=self.scope,
            )
        )

    def test_compare_and_swap_conflict_rolls_back_package_and_artifacts(self):
        self.repository.commit_filing_package(
            self.first,
            expected_active_package_id=None,
        )
        second = revised_package(sequence=2)
        self.clock.now = second.manifest.committed_at + timedelta(seconds=1)

        with self.assertRaises(StorageConflictError):
            self.repository.commit_filing_package(
                second,
                expected_active_package_id="filing_package:" + "0" * 64,
            )

        self.assertEqual(
            self.repository.get_active_filing_package(scope=self.scope),
            self.first,
        )
        self.assertIsNone(
            self.repository.get_filing_package(
                second.package_id,
                scope=self.scope,
            )
        )
        self.assertIsNone(
            self.repository.get_filing_artifact(
                second.artifacts[0].artifact_id,
                scope=self.scope,
            )
        )

    def test_successful_cas_replaces_active_and_is_idempotent(self):
        self.repository.commit_filing_package(
            self.first,
            expected_active_package_id=None,
        )
        second = revised_package(sequence=2)
        self.clock.now = second.manifest.committed_at + timedelta(seconds=1)

        committed = self.repository.commit_filing_package(
            second,
            expected_active_package_id=self.first.package_id,
        )
        repeated = self.repository.commit_filing_package(
            second,
            expected_active_package_id=second.package_id,
        )

        self.assertEqual(committed, second)
        self.assertEqual(repeated, second)
        self.assertEqual(
            self.repository.get_active_filing_package(scope=self.scope),
            second,
        )
        self.assertEqual(
            self.repository.get_filing_package(
                self.first.package_id,
                scope=self.scope,
            ),
            self.first,
        )

    def test_concurrent_cas_allows_only_one_complete_replacement(self):
        self.repository.commit_filing_package(
            self.first,
            expected_active_package_id=None,
        )
        candidates = (
            revised_package(sequence=2),
            revised_package(sequence=3),
        )
        self.clock.now = max(
            candidate.manifest.committed_at for candidate in candidates
        ) + timedelta(seconds=1)

        def commit(candidate):
            try:
                return self.repository.commit_filing_package(
                    candidate,
                    expected_active_package_id=self.first.package_id,
                )
            except StorageConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(commit, candidates))

        winners = tuple(result for result in results if result is not None)
        losers = tuple(
            candidate
            for candidate, result in zip(candidates, results, strict=True)
            if result is None
        )
        self.assertEqual(len(winners), 1)
        self.assertEqual(
            self.repository.get_active_filing_package(scope=self.scope),
            winners[0],
        )
        self.assertIsNone(
            self.repository.get_filing_package(
                losers[0].package_id,
                scope=self.scope,
            )
        )
        self.assertIsNone(
            self.repository.get_filing_artifact(
                losers[0].artifacts[0].artifact_id,
                scope=self.scope,
            )
        )

    def test_reads_and_hash_queries_are_tenant_and_issuer_isolated(self):
        self.repository.commit_filing_package(
            self.first,
            expected_active_package_id=None,
        )
        other_connection = build_connection(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
        )
        other_issuer = build_issuer(
            symbol="RELIANCE-EQ",
            legal_name="Reliance Industries Limited",
            isin="INE002A01018",
            provider_company_id="999",
            provider_slug="reliance-industries-limited",
        )
        other = revised_package(
            sequence=2,
            connection=other_connection,
            issuer=other_issuer,
        )
        self.clock.now = other.manifest.committed_at + timedelta(seconds=1)
        other_scope = FundamentalFilingRepositoryScope.from_package(other)
        self.repository.commit_filing_package(
            other,
            expected_active_package_id=None,
        )

        self.assertEqual(
            self.repository.get_active_filing_package(scope=other_scope),
            other,
        )
        self.assertIsNone(
            self.repository.get_filing_package(
                self.first.package_id,
                scope=other_scope,
            )
        )
        self.assertIsNone(
            self.repository.get_filing_artifact(
                self.artifact.artifact_id,
                scope=other_scope,
            )
        )
        self.assertEqual(
            self.repository.find_filing_artifacts_by_content_hash(
                PDF_HASH,
                scope=other_scope,
            ),
            (),
        )

    def test_recheck_due_does_not_expire_or_delete_retained_data(self):
        self.repository.commit_filing_package(
            self.first,
            expected_active_package_id=None,
        )
        self.clock.now = RECHECK_AFTER

        active = self.repository.get_active_filing_package(scope=self.scope)
        stored = self.repository.get_filing_artifact(
            self.artifact.artifact_id,
            scope=self.scope,
        )

        self.assertIsNotNone(active)
        self.assertIsNotNone(stored)
        self.assertTrue(active.manifest.is_recheck_due(as_of=self.clock.now))
        self.assertTrue(stored.is_recheck_due(as_of=self.clock.now))

    def test_rejects_future_writes_and_naive_clock_without_mutation(self):
        future = revised_package(sequence=240)
        with self.assertRaises(StorageError):
            self.repository.commit_filing_package(
                future,
                expected_active_package_id=None,
            )
        self.assertIsNone(
            self.repository.get_active_filing_package(scope=self.scope)
        )

        self.clock.now = datetime(2026, 9, 14, 12, 0)
        with self.assertRaises(ValueError):
            self.repository.save_filing_artifact(self.artifact)

    def test_artifact_deletion_requires_exact_authorization_and_no_package(self):
        self.repository.commit_filing_package(
            self.first,
            expected_active_package_id=None,
        )
        rejected = self.repository.delete_filing_artifact(
            self.artifact.artifact_id,
            scope=self.scope,
            authorization=authorization_for(self.artifact),
        )

        self.assertEqual(
            rejected.status,
            FundamentalFilingDeleteStatus.REJECTED_ACTIVE_REFERENCE,
        )
        self.assertIsNotNone(rejected.sanitized_detail)

        package_receipt = self.repository.delete_filing_package(
            self.first.package_id,
            scope=self.scope,
            authorization=authorization_for(self.first),
        )
        artifact_receipt = self.repository.delete_filing_artifact(
            self.artifact.artifact_id,
            scope=self.scope,
            authorization=authorization_for(self.artifact),
        )

        self.assertTrue(package_receipt.deleted)
        self.assertTrue(artifact_receipt.deleted)
        self.assertIsNone(
            self.repository.get_active_filing_package(scope=self.scope)
        )
        self.assertIsNone(
            self.repository.get_filing_artifact(
                self.artifact.artifact_id,
                scope=self.scope,
            )
        )

    def test_wrong_scope_deletion_is_not_found_and_does_not_leak(self):
        self.repository.save_filing_artifact(self.artifact)
        other_scope = FundamentalFilingRepositoryScope(
            connection=build_connection(
                tenant_id="tenant.other",
                provider_connection_id="provider.tijori.other",
            ),
            issuer=build_issuer(symbol="RELIANCE-EQ"),
        )

        receipt = self.repository.delete_filing_artifact(
            self.artifact.artifact_id,
            scope=other_scope,
            authorization=authorization_for(self.artifact),
        )

        self.assertEqual(
            receipt.status,
            FundamentalFilingDeleteStatus.NOT_FOUND,
        )
        self.assertIsNotNone(
            self.repository.get_filing_artifact(
                self.artifact.artifact_id,
                scope=self.scope,
            )
        )

    def test_deletion_rejects_mismatched_or_future_authorization(self):
        self.repository.save_filing_artifact(self.artifact)
        with self.assertRaises(StorageError):
            self.repository.delete_filing_artifact(
                self.artifact.artifact_id,
                scope=self.scope,
                authorization=delete_authorization(
                    target_kind=FundamentalFilingDeleteTarget.PACKAGE,
                ),
            )
        with self.assertRaises(StorageError):
            self.repository.delete_filing_artifact(
                self.artifact.artifact_id,
                scope=self.scope,
                authorization=authorization_for(
                    self.artifact,
                    authorized_at=self.clock.now + timedelta(seconds=1),
                ),
            )
        self.assertIsNotNone(
            self.repository.get_filing_artifact(
                self.artifact.artifact_id,
                scope=self.scope,
            )
        )

    def test_not_found_delete_returns_audited_receipt(self):
        receipt = self.repository.delete_filing_artifact(
            self.artifact.artifact_id,
            scope=self.scope,
            authorization=authorization_for(self.artifact),
        )

        self.assertEqual(
            receipt.status,
            FundamentalFilingDeleteStatus.NOT_FOUND,
        )
        self.assertEqual(receipt.authorization.target_id, self.artifact.artifact_id)

    def test_invalid_identifiers_fail_before_any_state_change(self):
        with self.assertRaises(ValueError):
            self.repository.get_filing_artifact("../artifact.pdf", scope=self.scope)
        with self.assertRaises(ValueError):
            self.repository.get_filing_package("package", scope=self.scope)
        with self.assertRaises(ValueError):
            self.repository.find_filing_artifacts_by_content_hash(
                "not-a-hash",
                scope=self.scope,
            )
        with self.assertRaises(ValueError):
            self.repository.list_filing_artifact_versions(
                "bad/reference",
                scope=self.scope,
            )


if __name__ == "__main__":
    unittest.main()
