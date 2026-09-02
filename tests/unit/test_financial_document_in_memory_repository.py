from datetime import datetime, timedelta
import unittest

from app.exceptions import StorageConflictError, StorageError
from app.models.financial_document_storage import (
    StructuredDocumentRepositoryScope,
    stored_structured_financial_document,
)
from app.storage.adapters.financial_document_in_memory import (
    InMemoryStructuredFinancialDocumentRepository,
)
from app.storage.financial_document_repositories import (
    StructuredFinancialDocumentRepository,
)
from tests.unit.test_financial_document_storage import (
    COMPLETED_AT,
    REQUESTED_AT,
    request,
    result,
)


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
    requested_at = REQUESTED_AT + offset
    source_request = request(
        request_id=f"request.coforge.growth.{int(requested_at.timestamp())}",
        operation_id=f"operation.coforge.growth.{int(requested_at.timestamp())}",
        requested_at=requested_at,
    )
    source_result = result(source_request)
    return stored_structured_financial_document(
        source_request,
        source_result,
        stored_at=source_result.completed_at + timedelta(seconds=1),
        retention=retention,
    )


class InMemoryStructuredFinancialDocumentRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = MutableClock(COMPLETED_AT + timedelta(hours=1))
        self.repository = InMemoryStructuredFinancialDocumentRepository(
            clock=self.clock
        )
        self.scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant.prateek",
            provider_connection_id="provider.tijori.prateek",
            provider="tijori",
        )

    def test_implements_port_and_idempotent_save_returns_copies(self):
        stored = build_entry()

        first = self.repository.save_structured_financial_document(stored)
        second = self.repository.save_structured_financial_document(stored)
        loaded = self.repository.get_structured_financial_document(
            stored.cache_key,
            scope=self.scope,
            as_of=self.clock.now,
        )

        self.assertIsInstance(
            self.repository,
            StructuredFinancialDocumentRepository,
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
        self.repository.save_structured_financial_document(stored)

        with self.assertRaises(StorageConflictError):
            self.repository.save_structured_financial_document(conflicting)

        self.assertEqual(
            self.repository.get_structured_financial_document(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            ),
            stored,
        )

    def test_atomic_refresh_replaces_only_with_newer_document(self):
        old = build_entry()
        self.clock.now = old.stored_at
        self.repository.save_structured_financial_document(old)
        refreshed = build_entry(offset=timedelta(hours=2))
        self.clock.now = refreshed.stored_at

        replaced = self.repository.replace_structured_financial_document(
            refreshed,
            scope=self.scope,
        )
        self.assertEqual(replaced, refreshed)

        with self.assertRaises(StorageConflictError):
            self.repository.replace_structured_financial_document(
                old,
                scope=self.scope,
            )
        self.assertEqual(
            self.repository.get_structured_financial_document(
                refreshed.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            ),
            refreshed,
        )

    def test_failed_scoped_replacement_preserves_active_document(self):
        stored = build_entry()
        self.clock.now = stored.stored_at
        self.repository.save_structured_financial_document(stored)
        other_scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )

        with self.assertRaises(StorageError):
            self.repository.replace_structured_financial_document(
                stored,
                scope=other_scope,
            )
        self.assertEqual(
            self.repository.get_structured_financial_document(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            ),
            stored,
        )

    def test_point_read_and_delete_are_tenant_isolated(self):
        stored = build_entry()
        other_scope = StructuredDocumentRepositoryScope(
            tenant_id="tenant.other",
            provider_connection_id="provider.tijori.other",
            provider="tijori",
        )
        self.repository.save_structured_financial_document(stored)

        self.assertIsNone(
            self.repository.get_structured_financial_document(
                stored.cache_key,
                scope=other_scope,
                as_of=self.clock.now,
            )
        )
        self.assertFalse(
            self.repository.delete_structured_financial_document(
                stored.cache_key,
                scope=other_scope,
            )
        )
        self.assertTrue(
            self.repository.delete_structured_financial_document(
                stored.cache_key,
                scope=self.scope,
            )
        )

    def test_expiry_is_automatic_inclusive_and_physically_purged(self):
        stored = build_entry(retention=timedelta(hours=1))
        self.clock.now = stored.stored_at
        self.repository.save_structured_financial_document(stored)

        self.assertIsNotNone(
            self.repository.get_structured_financial_document(
                stored.cache_key,
                scope=self.scope,
                as_of=stored.expires_at - timedelta(microseconds=1),
            )
        )
        self.assertIsNone(
            self.repository.get_structured_financial_document(
                stored.cache_key,
                scope=self.scope,
                as_of=stored.expires_at,
            )
        )
        self.assertEqual(
            self.repository.purge_expired_structured_financial_documents(
                as_of=stored.expires_at
            ),
            0,
        )

    def test_save_and_startup_reject_future_and_ignore_expired_rows(self):
        stored = build_entry(retention=timedelta(hours=1))
        self.clock.now = stored.expires_at
        with self.assertRaises(StorageError):
            self.repository.save_structured_financial_document(stored)

        repository = InMemoryStructuredFinancialDocumentRepository(
            clock=self.clock,
            initial_entries=(stored,),
        )
        self.assertIsNone(
            repository.get_structured_financial_document(
                stored.cache_key,
                scope=self.scope,
                as_of=self.clock.now,
            )
        )

        future = build_entry(offset=timedelta(days=1))
        with self.assertRaises(StorageError):
            InMemoryStructuredFinancialDocumentRepository(
                clock=self.clock,
                initial_entries=(future,),
            )


if __name__ == "__main__":
    unittest.main()
