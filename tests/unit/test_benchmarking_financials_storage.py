from datetime import datetime, timedelta
import unittest

from pydantic import ValidationError

from app.gateways.fundamentals import (
    FundamentalBenchmarkingFinancialsResult,
    FundamentalRetrievalStatus,
)
from app.models.benchmarking_financials_storage import (
    BENCHMARKING_FINANCIALS_MAX_RETENTION,
    BenchmarkingFinancialsCacheKey,
    StoredBenchmarkingFinancialsDocument,
    stored_benchmarking_financials_document,
)
from tests.unit.test_benchmarking_financials_gateway import (
    COMPLETED_AT,
    document,
    request,
    result_values,
)


STORED_AT = COMPLETED_AT + timedelta(seconds=1)


def result(
    source_request=None,
    **overrides,
) -> FundamentalBenchmarkingFinancialsResult:
    source_request = source_request or request()
    values = result_values(source_request)
    values.update(overrides)
    return FundamentalBenchmarkingFinancialsResult(**values)


def rebuild(model, **overrides):
    values = model.model_dump(exclude_computed_fields=True)
    values.update(overrides)
    return type(model)(**values)


class BenchmarkingFinancialsCacheKeyTests(unittest.TestCase):
    def test_key_captures_tenant_provider_company_and_as_of_date(self):
        key = BenchmarkingFinancialsCacheKey.from_request(request())

        self.assertEqual(key.issuer.symbol, "COFORGE")
        self.assertEqual(key.document_type, "benchmarking_financials")
        self.assertRegex(key.cache_key_fingerprint, r"^[a-f0-9]{64}$")
        self.assertTrue(
            key.cache_entry_id.startswith("benchmarking_financials:")
        )
        self.assertEqual(key.repository_scope.tenant_id, "tenant-a")

    def test_execution_identity_does_not_change_semantic_key(self):
        first = request()
        second = request(
            request_id="request.coforge.benchmarking-financials.later",
            operation_id="operation.coforge.fundamentals.later",
            requested_at=first.requested_at + timedelta(hours=1),
        )

        self.assertNotEqual(first.request_fingerprint, second.request_fingerprint)
        self.assertEqual(
            BenchmarkingFinancialsCacheKey.from_request(first),
            BenchmarkingFinancialsCacheKey.from_request(second),
        )

    def test_tenant_company_and_as_of_date_change_key(self):
        source_request = request()
        base = BenchmarkingFinancialsCacheKey.from_request(source_request)
        other_tenant = source_request.connection.model_copy(
            update={"tenant_id": "tenant.other"}
        )
        variants = (
            request(connection=other_tenant),
            request(
                issuer=source_request.issuer.model_copy(
                    update={
                        "symbol": "INFY",
                        "legal_name": "Infosys Ltd.",
                    }
                )
            ),
            request(as_of_date=source_request.as_of_date - timedelta(days=1)),
        )

        for variant in variants:
            with self.subTest(variant=variant):
                self.assertNotEqual(
                    base.cache_key_fingerprint,
                    BenchmarkingFinancialsCacheKey.from_request(
                        variant
                    ).cache_key_fingerprint,
                )


class StoredBenchmarkingFinancialsDocumentTests(unittest.TestCase):
    def test_round_trips_as_one_complete_json_document(self):
        source_request = request()
        stored = stored_benchmarking_financials_document(
            source_request,
            result(source_request),
            stored_at=STORED_AT,
        )

        encoded = stored.model_dump_json(exclude_computed_fields=True)
        restored = StoredBenchmarkingFinancialsDocument.model_validate_json(
            encoded
        )

        self.assertEqual(restored, stored)
        self.assertEqual(
            restored.result.document.companies,
            stored.result.document.companies,
        )
        self.assertEqual(
            restored.result.document.rows,
            stored.result.document.rows,
        )
        self.assertEqual(
            restored.document_fingerprint,
            stored.document_fingerprint,
        )

    def test_builds_chain_bound_ten_day_entry(self):
        source_request = request()
        source_result = result(source_request)
        stored = stored_benchmarking_financials_document(
            source_request,
            source_result,
            stored_at=STORED_AT,
        )

        self.assertEqual(stored.expires_at, source_result.document.expires_at)
        self.assertLessEqual(
            stored.expires_at - stored.retrieved_at,
            BENCHMARKING_FINANCIALS_MAX_RETENTION,
        )
        self.assertRegex(stored.storage_fingerprint, r"^[a-f0-9]{64}$")
        self.assertFalse(
            stored.is_expired(
                as_of=stored.expires_at - timedelta(seconds=1)
            )
        )
        self.assertTrue(stored.is_expired(as_of=stored.expires_at))

    def test_retention_never_extends_document_expiry(self):
        source_request = request()
        source_result = result(source_request)
        stored = stored_benchmarking_financials_document(
            source_request,
            source_result,
            stored_at=STORED_AT,
            retention=timedelta(days=2),
        )
        self.assertEqual(
            stored.expires_at - stored.retrieved_at,
            timedelta(days=2),
        )

        earlier_expiry = source_result.document.model_copy(
            update={
                "expires_at": (
                    source_result.document.retrieved_at + timedelta(days=1)
                )
            }
        )
        bounded_result = rebuild(source_result, document=earlier_expiry)
        bounded = stored_benchmarking_financials_document(
            source_request,
            bounded_result,
            stored_at=STORED_AT,
        )
        self.assertEqual(bounded.expires_at, earlier_expiry.expires_at)

    def test_rejects_wrong_chain_failed_result_and_invalid_retention(self):
        source_request = request()
        source_result = result(source_request)
        stored = stored_benchmarking_financials_document(
            source_request,
            source_result,
            stored_at=STORED_AT,
        )
        wrong_key = BenchmarkingFinancialsCacheKey.from_request(
            request(as_of_date=source_request.as_of_date - timedelta(days=1))
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
            stored_benchmarking_financials_document(
                source_request,
                failed,
                stored_at=STORED_AT,
            )

        for retention in (timedelta(0), timedelta(days=10, seconds=1)):
            with self.subTest(retention=retention):
                with self.assertRaises(ValueError):
                    stored_benchmarking_financials_document(
                        source_request,
                        source_result,
                        stored_at=STORED_AT,
                        retention=retention,
                    )

    def test_rejects_naive_reads_and_storage_after_expiry(self):
        source_request = request()
        source_result = result(source_request)
        stored = stored_benchmarking_financials_document(
            source_request,
            source_result,
            stored_at=STORED_AT,
        )
        with self.assertRaises(ValueError):
            stored.is_expired(as_of=datetime(2026, 9, 1, 12, 0))
        with self.assertRaises(ValidationError):
            rebuild(stored, stored_at=stored.expires_at)


if __name__ == "__main__":
    unittest.main()
