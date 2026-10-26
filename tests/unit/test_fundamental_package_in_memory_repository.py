from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from types import SimpleNamespace
import unittest

from app.exceptions import StorageConflictError, StorageError
from app.models.fundamental_package import (
    FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS,
    FundamentalEvidencePackage,
    FundamentalPackageItem,
    FundamentalPackageManifest,
)
from app.models.fundamental_storage import StoredFundamentalSnapshot
from app.storage.adapters.fundamental_package_in_memory import (
    InMemoryFundamentalPackageRepository,
)
from app.storage.fundamental_package_repositories import (
    FundamentalPackageRepository,
    FundamentalPackageRepositoryScope,
)
from tests.unit.test_fundamental_gateway import build_connection, build_issuer
from tests.unit.test_fundamental_package import (
    COMMITTED_AT,
    evidence_package,
)


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def rebuild_package(
    source: FundamentalEvidencePackage,
    *,
    sequence: int,
    connection=None,
    issuer=None,
) -> FundamentalEvidencePackage:
    selected_connection = connection or source.manifest.connection
    selected_issuer = issuer or source.manifest.issuer
    operation_id = f"operation-fundamental-refresh-{sequence}"
    offset = timedelta(minutes=sequence)
    artifacts = []
    items = []

    for index, (key, artifact) in enumerate(
        zip(
            FUNDAMENTAL_PACKAGE_REQUIRED_ITEMS,
            source._artifacts(),
            strict=True,
        )
    ):
        fingerprint = f"{sequence * 100 + index + 1:064x}"
        cache_entry_id = f"fundamental_artifact:refresh-{sequence}-{index}"
        request_values = vars(artifact.request).copy()
        request_values.update(
            {
                "connection": selected_connection,
                "issuer": selected_issuer,
                "operation_id": operation_id,
            }
        )
        artifact_values = {
            "request": SimpleNamespace(**request_values),
            "cache_key": SimpleNamespace(cache_entry_id=cache_entry_id),
            "retrieved_at": artifact.retrieved_at + offset,
            "expires_at": artifact.expires_at + offset,
        }
        if isinstance(artifact, StoredFundamentalSnapshot):
            artifact_values["snapshot_fingerprint"] = fingerprint
        else:
            artifact_values["document_fingerprint"] = fingerprint
        rebuilt = artifact.__class__.model_construct(**artifact_values)
        artifacts.append(rebuilt)
        items.append(
            FundamentalPackageItem(
                key=key,
                cache_entry_id=cache_entry_id,
                content_fingerprint=fingerprint,
                retrieved_at=rebuilt.retrieved_at,
                expires_at=rebuilt.expires_at,
            )
        )

    manifest = FundamentalPackageManifest(
        operation_id=operation_id,
        connection=selected_connection,
        issuer=selected_issuer,
        requested_at=source.manifest.requested_at + offset,
        committed_at=source.manifest.committed_at + offset,
        items=tuple(items),
    )
    return FundamentalEvidencePackage(
        manifest=manifest,
        snapshots=tuple(artifacts[:3]),
        structured_documents=tuple(artifacts[3:14]),
        peer_comparison=artifacts[14],
        benchmarking_financials=artifacts[15],
    )


class InMemoryFundamentalPackageRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock(COMMITTED_AT + timedelta(hours=1))
        self.repository = InMemoryFundamentalPackageRepository(clock=self.clock)
        self.first = evidence_package()
        self.scope = FundamentalPackageRepositoryScope.from_package(self.first)

    def test_implements_protocol_and_returns_defensive_copies(self):
        committed = self.repository.commit_fundamental_package(
            self.first,
            expected_active_package_id=None,
        )
        loaded = self.repository.get_active_fundamental_package(
            scope=self.scope,
            as_of=self.clock.now,
        )
        resolved = self.repository.get_fundamental_package(
            self.first.package_id,
            scope=self.scope,
            as_of=self.clock.now,
        )

        self.assertIsInstance(self.repository, FundamentalPackageRepository)
        self.assertEqual(
            self.repository.adapter_name,
            "in_memory_fundamental_packages",
        )
        self.assertEqual(committed, self.first)
        self.assertEqual(loaded, self.first)
        self.assertEqual(resolved, self.first)
        self.assertIsNot(committed, self.first)
        self.assertIsNot(loaded, committed)
        self.assertIsNot(loaded.snapshots[0], committed.snapshots[0])

    def test_compare_and_swap_conflict_preserves_prior_active_package(self):
        self.repository.commit_fundamental_package(
            self.first,
            expected_active_package_id=None,
        )
        second = rebuild_package(self.first, sequence=2)

        with self.assertRaises(StorageConflictError):
            self.repository.commit_fundamental_package(
                second,
                expected_active_package_id="fundamental_package:" + "0" * 64,
            )

        self.assertEqual(
            self.repository.get_active_fundamental_package(
                scope=self.scope,
                as_of=self.clock.now,
            ),
            self.first,
        )
        self.assertIsNone(
            self.repository.get_fundamental_package(
                second.package_id,
                scope=self.scope,
                as_of=self.clock.now,
            )
        )

    def test_successful_cas_replaces_active_package_and_is_idempotent(self):
        self.repository.commit_fundamental_package(
            self.first,
            expected_active_package_id=None,
        )
        second = rebuild_package(self.first, sequence=2)

        committed = self.repository.commit_fundamental_package(
            second,
            expected_active_package_id=self.first.package_id,
        )
        repeated = self.repository.commit_fundamental_package(
            second,
            expected_active_package_id=second.package_id,
        )

        self.assertEqual(committed, second)
        self.assertEqual(repeated, second)
        self.assertEqual(
            self.repository.get_active_fundamental_package(
                scope=self.scope,
                as_of=self.clock.now,
            ),
            second,
        )

    def test_concurrent_cas_allows_only_one_replacement(self):
        self.repository.commit_fundamental_package(
            self.first,
            expected_active_package_id=None,
        )
        candidates = (
            rebuild_package(self.first, sequence=2),
            rebuild_package(self.first, sequence=3),
        )

        def commit(candidate):
            try:
                return self.repository.commit_fundamental_package(
                    candidate,
                    expected_active_package_id=self.first.package_id,
                )
            except StorageConflictError:
                return None

        with ThreadPoolExecutor(max_workers=2) as executor:
            results = tuple(executor.map(commit, candidates))

        winners = tuple(result for result in results if result is not None)
        self.assertEqual(len(winners), 1)
        self.assertEqual(
            self.repository.get_active_fundamental_package(
                scope=self.scope,
                as_of=self.clock.now,
            ),
            winners[0],
        )

    def test_reads_are_strictly_tenant_and_issuer_scoped(self):
        other = rebuild_package(
            self.first,
            sequence=2,
            connection=build_connection(
                tenant_id="tenant.other",
                provider_connection_id="provider.tijori.other",
            ),
        )
        other_scope = FundamentalPackageRepositoryScope.from_package(other)
        self.repository.commit_fundamental_package(
            self.first,
            expected_active_package_id=None,
        )
        self.repository.commit_fundamental_package(
            other,
            expected_active_package_id=None,
        )

        self.assertEqual(
            self.repository.get_active_fundamental_package(
                scope=other_scope,
                as_of=self.clock.now,
            ),
            other,
        )
        self.assertIsNone(
            self.repository.get_fundamental_package(
                self.first.package_id,
                scope=other_scope,
                as_of=self.clock.now,
            )
        )
        wrong_issuer_scope = FundamentalPackageRepositoryScope(
            connection=self.scope.connection,
            issuer=build_issuer(symbol="RELIANCE"),
        )
        self.assertIsNone(
            self.repository.get_active_fundamental_package(
                scope=wrong_issuer_scope,
                as_of=self.clock.now,
            )
        )

    def test_expiry_purges_package_and_active_pointer(self):
        self.repository.commit_fundamental_package(
            self.first,
            expected_active_package_id=None,
        )
        self.clock.now = self.first.manifest.expires_at

        self.assertEqual(
            self.repository.purge_expired_fundamental_packages(
                as_of=self.clock.now
            ),
            1,
        )
        self.assertIsNone(
            self.repository.get_active_fundamental_package(
                scope=self.scope,
                as_of=self.clock.now,
            )
        )
        with self.assertRaises(StorageError):
            self.repository.commit_fundamental_package(
                self.first,
                expected_active_package_id=None,
            )

    def test_rejects_future_commit_and_naive_times_without_mutation(self):
        future = rebuild_package(self.first, sequence=120)
        with self.assertRaises(StorageError):
            self.repository.commit_fundamental_package(
                future,
                expected_active_package_id=None,
            )
        with self.assertRaises(ValueError):
            self.repository.get_active_fundamental_package(
                scope=self.scope,
                as_of=datetime(2026, 9, 8, 12, 0),
            )
        self.assertIsNone(
            self.repository.get_active_fundamental_package(
                scope=self.scope,
                as_of=self.clock.now,
            )
        )


if __name__ == "__main__":
    unittest.main()
