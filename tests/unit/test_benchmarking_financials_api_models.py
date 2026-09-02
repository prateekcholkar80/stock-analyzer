import unittest

from pydantic import ValidationError

from app.api.models import (
    BrowserBenchmarkingFinancialsDocument,
    BrowserBenchmarkingFinancialsDocumentResponse,
)
from app.models.browser_operations import (
    BrowserBenchmarkingFinancialsReference,
)
from app.services.fundamental_evidence import FundamentalEvidenceSource
from tests.unit.test_benchmarking_financials_in_memory_repository import (
    build_entry,
)


class BrowserBenchmarkingFinancialsApiModelTests(unittest.TestCase):
    def setUp(self):
        self.stored = build_entry()
        document = self.stored.result.document
        self.document = BrowserBenchmarkingFinancialsDocument.from_document(
            document
        )
        self.reference = BrowserBenchmarkingFinancialsReference(
            cache_entry_id=self.stored.cache_key.cache_entry_id,
            document_id=document.document_id,
            exchange=document.issuer.exchange,
            symbol=document.issuer.symbol,
            source=FundamentalEvidenceSource.CACHE,
            document_fingerprint=document.document_fingerprint,
            observation_date=document.observation_date,
            retrieved_at=self.stored.retrieved_at,
            stored_at=self.stored.stored_at,
            expires_at=self.stored.expires_at,
            all_rows_captured=True,
            company_count=len(document.companies),
            row_count=len(document.rows),
        )

    def test_projects_complete_document_without_provider_scope(self):
        response = BrowserBenchmarkingFinancialsDocumentResponse(
            operation_id="operation-1",
            reference=self.reference,
            document=self.document,
        )

        payload = response.model_dump_json()
        self.assertEqual(len(response.document.companies), 2)
        self.assertEqual(len(response.document.rows), 2)
        self.assertNotIn("tenant_id", payload)
        self.assertNotIn("provider_connection_id", payload)
        self.assertNotIn("source_location", payload)

    def test_rejects_reference_for_different_document(self):
        mismatched = self.reference.model_copy(
            update={"document_fingerprint": "f" * 64}
        )

        with self.assertRaises(ValidationError):
            BrowserBenchmarkingFinancialsDocumentResponse(
                operation_id="operation-1",
                reference=mismatched,
                document=self.document,
            )


if __name__ == "__main__":
    unittest.main()
