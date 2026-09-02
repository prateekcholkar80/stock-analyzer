from datetime import datetime, timedelta
import unittest

from app.exceptions import StorageConflictError, StorageError
from app.gateways.fundamentals import (
    FundamentalBenchmarkingFinancialsResult,
)
from app.models.benchmarking_financials_storage import (
    stored_benchmarking_financials_document,
)
from app.models.financial_document_storage import (
    StructuredDocumentRepositoryScope,
)
from app.storage.adapters.benchmarking_financials_in_memory import (
    InMemoryBenchmarkingFinancialsRepository,
)
from app.storage.benchmarking_financials_repositories import (
    BenchmarkingFinancialsRepository,
)
from tests.unit.test_benchmarking_financials_gateway import (
    COMPLETED_AT,
    document,
    request,
    result_values,
)
from tests.unit.test_benchmarking_financials_documents import RETRIEVED_AT


class MutableClock:
    def __init__(self, now: datetime) -> None:
        self.now = now

    def __call__(self) -> datetime:
        return self.now


def build_entry(
    *,
    offset: timedelta = timedelta(0),
    retention: timedelta = timedelta(days=10),
):
    requested_at = RETRIEVED_AT + offset
    source_request = request(
        request_id=(
            "request.coforge.benchmarking."
            f"{int(requested_at.timestamp())}"
        ),
        operation_id=(
            "operation.coforge.benchmarking."
            f"{int(requested_at.timestamp())}"
        ),
        requested_at=requested_at,
    )
    retrieved_at = requested_at + timedelta(seconds=1)
    source_document = document(
        connection=source_request.connection,
        issuer=source_request.issuer,
        retrieved_at=retrieved_at,
        expires_at=retrieved_at + timedelta(days=10),
    )
    values = result_values(source_request)
    values.update(
        {
            "started_at": requested_at,
            "completed_at": requested_at + timedelta(seconds=2),
            "document": source_document,
        }
    )
    source_result = FundamentalBenchmarkingFinancialsResult(**values)
    return stored_benchmarking_financials_document(
        source_request,
        source_result,
        stored_at=source_result.completed_at + timedelta(seconds=1),
        retention=retention,
    )


class InMemoryBenchmarkingFinancialsRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock(COMPLETED_AT + timedelta(hours=1))
        self.repository = InMemoryBenchmarkingFinancialsRepository(
            clock=self.clock
        )
        self.scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant-a",
            provider_connection_id="tijori-a",
            provider="tijori",
        )

    def test_implements_port_and_idempotent_save_returns_copies(self):
        stored = build_entry()

        first = self.repository.save_benchmarking_financials(stored)
        second = self.repository.save_benchmarking_financials(stored)
        loaded = self.repository.get_benchmarking_financials(
            stored.cache_key,
            scope=self.scope,
            as_of=self.clock.now,
        )

        self.assertIsInstance(
            self.repository,
            BenchmarkingFinancialsRepository,
        )
        self.assertEqual(first, stored)
        self.assertEqual(second, stored)
        self.assertEqual(loaded, stored)
        self.assertIsNot(first, stored)
        self.assertIsNot(second, first)
        self.assertIsNot(loaded, first)

    def test_conflicting_save_preserves_active_document(self):
        stored = build_entry()
        conflicting = stored.model_copy(
            update={"expires_at": stored.expires_at - timedelta(days=1)}
        )
        self.repository.save_benchmarking_financials(stored)

        with self.assertRaises(StorageConflictError):
            self.repository.save_benchmarking_financials(conflicting)

        self.assertEqual(
            self.repository.get_benchmarking_financials(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            ),
            stored,
        )

    def test_atomic_refresh_replaces_only_with_newer_document(self):
        old = build_entry()
        self.clock.now = old.stored_at
        self.repository.save_benchmarking_financials(old)
        refreshed = build_entry(offset=timedelta(hours=2))
        self.clock.now = refreshed.stored_at

        replaced = self.repository.replace_benchmarking_financials(
            refreshed,
            scope=self.scope,
        )
        self.assertEqual(replaced, refreshed)

        with self.assertRaises(StorageConflictError):
            self.repository.replace_benchmarking_financials(
                old,
                scope=self.scope,
            )
        self.assertEqual(
            self.repository.get_benchmarking_financials(
                refreshed.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            ),
            refreshed,
        )

    def test_failed_scoped_replacement_preserves_active_document(self):
        stored = build_entry()
        self.clock.now = stored.stored_at
        self.repository.save_benchmarking_financials(stored)
        other_scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )

        with self.assertRaises(StorageError):
            self.repository.replace_benchmarking_financials(
                stored,
                scope=other_scope,
            )
        self.assertEqual(
            self.repository.get_benchmarking_financials(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            ),
            stored,
        )

    def test_reads_and_deletes_are_tenant_isolated(self):
        stored = build_entry()
        other_scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )
        self.repository.save_benchmarking_financials(stored)

        self.assertIsNone(
            self.repository.get_benchmarking_financials(
                stored.cache_key,
                scope=other_scope,
                as_of=self.clock.now,
            )
        )
        self.assertIsNone(
            self.repository.get_benchmarking_financials_by_cache_entry_id(
                stored.cache_key.cache_entry_id,
                scope=other_scope,
                as_of=self.clock.now,
            )
        )
        self.assertEqual(
            self.repository.get_benchmarking_financials_by_cache_entry_id(
                stored.cache_key.cache_entry_id,
                scope=self.scope,
                as_of=self.clock.now,
            ),
            stored,
        )
        with self.assertRaises(ValueError):
            self.repository.get_benchmarking_financials_by_cache_entry_id(
                "benchmarking_financials:unsafe",
                scope=self.scope,
                as_of=self.clock.now,
            )
        self.assertFalse(
            self.repository.delete_benchmarking_financials(
                stored.cache_key,
                scope=other_scope,
            )
        )
        self.assertTrue(
            self.repository.delete_benchmarking_financials(
                stored.cache_key,
                scope=self.scope,
            )
        )

    def test_expiry_is_inclusive_and_physically_purged(self):
        stored = build_entry(retention=timedelta(hours=1))
        self.clock.now = stored.stored_at
        self.repository.save_benchmarking_financials(stored)

        self.assertIsNotNone(
            self.repository.get_benchmarking_financials(
                stored.cache_key,
                scope=self.scope,
                as_of=stored.expires_at - timedelta(microseconds=1),
            )
        )
        self.assertIsNone(
            self.repository.get_benchmarking_financials(
                stored.cache_key,
                scope=self.scope,
                as_of=stored.expires_at,
            )
        )
        self.assertEqual(
            self.repository.purge_expired_benchmarking_financials(
                as_of=stored.expires_at
            ),
            0,
        )

    def test_startup_and_save_handle_future_and_expired_entries(self):
        stored = build_entry(retention=timedelta(hours=1))
        self.clock.now = stored.expires_at
        with self.assertRaises(StorageError):
            self.repository.save_benchmarking_financials(stored)

        repository = InMemoryBenchmarkingFinancialsRepository(
            clock=self.clock,
            initial_entries=(stored,),
        )
        self.assertIsNone(
            repository.get_benchmarking_financials(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )
        )

        future = build_entry(offset=timedelta(days=1))
        with self.assertRaises(StorageError):
            InMemoryBenchmarkingFinancialsRepository(
                clock=self.clock,
                initial_entries=(future,),
            )


if __name__ == "__main__":
    unittest.main()
