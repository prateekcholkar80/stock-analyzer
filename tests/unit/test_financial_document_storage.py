from datetime import datetime, timedelta
import unittest

from pydantic import ValidationError

from app.gateways.fundamentals import (
    FundamentalRetrievalStatus,
    FundamentalStructuredDocumentRequest,
    FundamentalStructuredDocumentResult,
)
from app.models.financial_document_storage import (
    STRUCTURED_DOCUMENT_MAX_RETENTION,
    StoredStructuredFinancialDocument,
    StructuredDocumentCacheKey,
    StructuredDocumentRepositoryScope,
    stored_structured_financial_document,
)
from app.models.financial_documents import (
    FinancialDocumentType,
    FinancialReportingBasis,
)
from app.storage.financial_document_repositories import (
    StructuredFinancialDocumentRepository,
)
from tests.unit.test_structured_financial_gateway import (
    ADAPTER_HASH,
    COMPLETED_AT,
    REQUESTED_AT,
    connection,
    document,
    issuer,
)


STORED_AT = COMPLETED_AT + timedelta(seconds=1)


def request(**overrides) -> FundamentalStructuredDocumentRequest:
    values = {
        "request_id": "request.coforge.growth.cache",
        "operation_id": "operation.coforge.growth",
        "connection": connection(),
        "requested_at": REQUESTED_AT,
        "issuer": issuer(),
        "as_of_date": REQUESTED_AT.date(),
        "document_type": FinancialDocumentType.GROWTH_TABLE,
        "reporting_basis": FinancialReportingBasis.NOT_APPLICABLE,
    }
    values.update(overrides)
    return FundamentalStructuredDocumentRequest(**values)


def result(source_request=None, **overrides):
    source_request = source_request or request()
    retrieved_at = source_request.requested_at + timedelta(seconds=1)
    source_document = document(
        document_id="provider.NSE.COFORGE.growth_table.not_applicable",
        connection=source_request.connection,
        issuer=source_request.issuer,
        document_type=source_request.document_type,
        reporting_basis=source_request.reporting_basis,
        retrieved_at=retrieved_at,
        expires_at=retrieved_at + timedelta(days=10),
    )
    values = {
        "request_id": source_request.request_id,
        "request_fingerprint": source_request.request_fingerprint,
        "connection": source_request.connection,
        "requested_at": source_request.requested_at,
        "started_at": source_request.requested_at,
        "completed_at": source_request.requested_at + timedelta(seconds=2),
        "provider_contract_version": "provider.contract.v1",
        "adapter_fingerprint": ADAPTER_HASH,
        "issuer": source_request.issuer,
        "document_type": source_request.document_type,
        "reporting_basis": source_request.reporting_basis,
        "status": FundamentalRetrievalStatus.COMPLETED,
        "document": source_document,
    }
    values.update(overrides)
    return FundamentalStructuredDocumentResult(**values)


def rebuild(model, **overrides):
    values = model.model_dump(exclude_computed_fields=True)
    values.update(overrides)
    return type(model)(**values)


class StructuredDocumentCacheKeyTests(unittest.TestCase):
    def test_key_captures_tenant_company_document_and_basis(self):
        key = StructuredDocumentCacheKey.from_request(request())

        self.assertEqual(key.issuer.symbol, "COFORGE")
        self.assertIs(key.document_type, FinancialDocumentType.GROWTH_TABLE)
        self.assertIs(
            key.reporting_basis,
            FinancialReportingBasis.NOT_APPLICABLE,
        )
        self.assertRegex(key.cache_key_fingerprint, r"^[a-f0-9]{64}$")
        self.assertEqual(
            key.repository_scope,
            StructuredDocumentRepositoryScope(
                tenant_id="tenant.prateek",
                provider_connection_id="provider.tijori.prateek",
                provider="tijori",
            ),
        )

    def test_execution_identity_does_not_change_semantic_key(self):
        first = request()
        second = request(
            request_id="request.coforge.growth.later",
            operation_id="operation.coforge.growth.later",
            requested_at=REQUESTED_AT + timedelta(hours=1),
        )

        self.assertNotEqual(first.request_fingerprint, second.request_fingerprint)
        self.assertEqual(
            StructuredDocumentCacheKey.from_request(first),
            StructuredDocumentCacheKey.from_request(second),
        )

    def test_company_document_basis_and_as_of_date_change_key(self):
        base = StructuredDocumentCacheKey.from_request(request())
        variants = (
            request(issuer=issuer(symbol="INFY", legal_name="Infosys Limited")),
            request(document_type=FinancialDocumentType.BALANCE_SHEET),
            request(reporting_basis=FinancialReportingBasis.CONSOLIDATED),
            request(as_of_date=REQUESTED_AT.date() - timedelta(days=1)),
        )

        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(
                    base.cache_key_fingerprint,
                    StructuredDocumentCacheKey.from_request(
                        variant
                    ).cache_key_fingerprint,
                )


class StoredStructuredFinancialDocumentTests(unittest.TestCase):
    def test_builds_chain_bound_ten_day_entry(self):
        source_request = request()
        source_result = result(source_request)
        stored = stored_structured_financial_document(
            source_request,
            source_result,
            stored_at=STORED_AT,
        )

        self.assertEqual(
            stored.expires_at - stored.retrieved_at,
            STRUCTURED_DOCUMENT_MAX_RETENTION,
        )
        self.assertRegex(stored.storage_fingerprint, r"^[a-f0-9]{64}$")
        self.assertFalse(stored.is_expired(as_of=stored.expires_at - timedelta(seconds=1)))
        self.assertTrue(stored.is_expired(as_of=stored.expires_at))

    def test_shorter_retention_never_extends_provider_expiry(self):
        source_request = request()
        source_result = result(source_request)
        stored = stored_structured_financial_document(
            source_request,
            source_result,
            stored_at=STORED_AT,
            retention=timedelta(days=2),
        )
        self.assertEqual(stored.expires_at - stored.retrieved_at, timedelta(days=2))

        earlier_expiry = source_result.document.model_copy(
            update={"expires_at": source_result.document.retrieved_at + timedelta(days=1)}
        )
        bounded_result = rebuild(source_result, document=earlier_expiry)
        bounded = stored_structured_financial_document(
            source_request,
            bounded_result,
            stored_at=STORED_AT,
        )
        self.assertEqual(bounded.expires_at, earlier_expiry.expires_at)

    def test_rejects_wrong_chain_failed_result_and_invalid_retention(self):
        source_request = request()
        source_result = result(source_request)
        stored = stored_structured_financial_document(
            source_request,
            source_result,
            stored_at=STORED_AT,
        )
        wrong_key = StructuredDocumentCacheKey.from_request(
            request(reporting_basis=FinancialReportingBasis.CONSOLIDATED)
        )
        with self.assertRaises(ValidationError):
            rebuild(stored, cache_key=wrong_key)

        failed = rebuild(
            source_result,
            status=FundamentalRetrievalStatus.PROVIDER_UNAVAILABLE,
            document=None,
            limitations=("Provider unavailable.",),
        )
        with self.assertRaises(ValueError):
            stored_structured_financial_document(
                source_request,
                failed,
                stored_at=STORED_AT,
            )

        for retention in (timedelta(0), timedelta(days=10, seconds=1)):
            with self.subTest(retention=retention):
                with self.assertRaises(ValueError):
                    stored_structured_financial_document(
                        source_request,
                        source_result,
                        stored_at=STORED_AT,
                        retention=retention,
                    )

    def test_rejects_naive_read_and_storage_after_expiry(self):
        source_request = request()
        source_result = result(source_request)
        stored = stored_structured_financial_document(
            source_request,
            source_result,
            stored_at=STORED_AT,
        )
        with self.assertRaises(ValueError):
            stored.is_expired(as_of=datetime(2026, 9, 1, 12, 0))
        with self.assertRaises(ValidationError):
            rebuild(stored, stored_at=stored.expires_at)


class CompleteStructuredDocumentRepository:
    adapter_name = "memory"

    def save_structured_financial_document(self, stored):
        return stored

    def replace_structured_financial_document(self, stored, *, scope):
        return stored

    def get_structured_financial_document(self, key, *, scope, as_of):
        return None

    def get_structured_financial_document_by_cache_entry_id(
        self,
        cache_entry_id,
        *,
        scope,
        as_of,
    ):
        return None

    def delete_structured_financial_document(self, key, *, scope):
        return False

    def purge_expired_structured_financial_documents(self, *, as_of):
        return 0


class StructuredFinancialDocumentRepositoryTests(unittest.TestCase):
    def test_complete_adapter_satisfies_runtime_protocol(self):
        repository = CompleteStructuredDocumentRepository()

        self.assertIsInstance(
            repository,
            StructuredFinancialDocumentRepository,
        )
        self.assertEqual(repository.adapter_name, "memory")

    def test_incomplete_adapter_does_not_satisfy_runtime_protocol(self):
        class IncompleteRepository:
            adapter_name = "incomplete"

            def save_structured_financial_document(self, stored):
                return stored

        self.assertNotIsInstance(
            IncompleteRepository(),
            StructuredFinancialDocumentRepository,
        )

    def test_protocol_exposes_only_bounded_cache_mutations(self):
        methods = set(dir(StructuredFinancialDocumentRepository))
        expected = {
            "save_structured_financial_document",
            "replace_structured_financial_document",
            "get_structured_financial_document",
            "get_structured_financial_document_by_cache_entry_id",
            "delete_structured_financial_document",
            "purge_expired_structured_financial_documents",
        }

        self.assertTrue(expected.issubset(methods))
        self.assertNotIn("execute_sql", methods)
        self.assertNotIn("delete_all", methods)
        self.assertNotIn("list_all_tenants", methods)


if __name__ == "__main__":
    unittest.main()
