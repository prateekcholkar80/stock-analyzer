import copy
import json
import unittest

from pydantic import ValidationError

from app.fundamentals.tijori_mcp_contracts import (
    TijoriFinancialDocumentPackagePayload,
)


ISSUER = {
    "exchange": "NSE",
    "symbol": "COFORGE",
    "legal_name": "Coforge Limited",
    "provider_company_id": "242",
    "provider_slug": "niit-technologies-limited",
}
SOURCE = {
    "provider": "tijori",
    "location": (
        "https://www.tijorifinance.com/company/"
        "niit-technologies-limited/financials/"
    ),
    "retrieved_at": "2026-09-11T23:30:00+05:30",
}


def validate(candidate: dict) -> TijoriFinancialDocumentPackagePayload:
    return TijoriFinancialDocumentPackagePayload.model_validate_json(
        json.dumps(candidate)
    )


def package_payload() -> dict:
    documents = [growth_document()]
    for basis in ("consolidated", "standalone"):
        documents.extend(
            statement_document(document_type, basis)
            for document_type in (
                "balance_sheet",
                "profit_and_loss",
                "cash_flow",
                "ratios",
                "quarterly_results",
            )
        )
    return {
        "package": {
            "schema_version": "tijori.financial_document_package.v1",
            "issuer": ISSUER,
            "source": SOURCE,
            "documents": documents,
            "extraction": {"document_count": 11, "status": "complete"},
        }
    }


def growth_document() -> dict:
    return {
        "schema_version": "tijori.financial_document.v1",
        "document_type": "growth_table",
        "reporting_basis": "not_applicable",
        "issuer": ISSUER,
        "source": SOURCE,
        "unit": "percent",
        "all_sections_expanded": True,
        "columns": [
            {
                "column_key": "period_1yr",
                "source_label": "1yr",
                "display_order": 0,
            }
        ],
        "rows": [growth_row()],
        "extraction": {
            "column_count": 1,
            "row_count": 1,
            "status": "complete",
        },
    }


def growth_row() -> dict:
    return {
        "row_key": "sales_growth",
        "original_label": "Sales Growth",
        "parent_row_key": None,
        "depth": 0,
        "row_kind": "metric",
        "display_order": 0,
        "values": [
            {
                "column_key": "period_1yr",
                "source_value": "12.5",
                "yoy_change": None,
                "percentage_of_parent": None,
                "availability_status": "available",
            }
        ],
    }


def statement_document(document_type: str, basis: str) -> dict:
    row = {
        "row_key": "total",
        "original_label": "Total",
        "parent_row_key": None,
        "depth": 0,
        "row_kind": "metric",
        "display_order": 0,
        "values": [
            {
                "period_key": "period_mar_2026",
                "source_value": "100",
                "yoy_change": None,
                "percentage_of_parent": None,
                "availability_status": "available",
            }
        ],
    }
    source_unit = "Rs. Cr."
    normalized_unit = "INR crore"
    if document_type in {"profit_and_loss", "ratios", "quarterly_results"}:
        source_unit = "mixed"
        normalized_unit = "mixed"
        row.update(row_measurement(document_type))
    return {
        "schema_version": "tijori.financial_document.v1",
        "document_type": document_type,
        "reporting_basis": basis,
        "issuer": ISSUER,
        "source": SOURCE,
        "source_unit": source_unit,
        "normalized_unit": normalized_unit,
        "skipped_report_dates": [],
        "all_sections_expanded": True,
        "periods": [
            {
                "period_key": "period_mar_2026",
                "source_label": "Mar 2026",
                "display_order": 0,
            }
        ],
        "rows": [row],
        "extraction": {
            "period_count": 1,
            "row_count": 1,
            "cell_count": 1,
            "maximum_depth": 0,
            "status": "complete",
        },
    }


def row_measurement(document_type: str) -> dict:
    if document_type == "ratios":
        return {
            "value_kind": "ratio",
            "source_unit": "ratio",
            "normalized_unit": "ratio",
        }
    return {
        "value_kind": "monetary",
        "source_unit": "Rs. Cr.",
        "normalized_unit": "INR crore",
    }


class TijoriFinancialDocumentPackagePayloadTests(unittest.TestCase):
    def test_accepts_exact_complete_single_snapshot_package(self):
        payload = validate(package_payload())

        self.assertEqual(len(payload.package.documents), 11)
        self.assertEqual(payload.package.extraction.status, "complete")
        self.assertEqual(len(payload.package.package_fingerprint), 64)

    def test_rejects_missing_or_duplicate_scenarios(self):
        missing = package_payload()
        missing["package"]["documents"].pop()
        missing["package"]["extraction"]["document_count"] = 10
        with self.assertRaises(ValidationError):
            validate(missing)

        duplicate = package_payload()
        duplicate["package"]["documents"][-1] = copy.deepcopy(
            duplicate["package"]["documents"][-2]
        )
        with self.assertRaises(ValidationError):
            validate(duplicate)

    def test_rejects_mixed_issuer_or_provider_snapshot(self):
        mixed_issuer = package_payload()
        mixed_issuer["package"]["documents"][1]["issuer"] = {
            **ISSUER,
            "symbol": "OTHER",
        }
        with self.assertRaises(ValidationError):
            validate(mixed_issuer)

        mixed_time = package_payload()
        mixed_time["package"]["documents"][1]["source"] = {
            **SOURCE,
            "retrieved_at": "2026-09-11T23:31:00+05:30",
        }
        with self.assertRaises(ValidationError):
            validate(mixed_time)

    def test_rejects_unsafe_metadata_or_incomplete_status(self):
        unsafe = package_payload()
        unsafe["package"]["source"]["location"] = (
            "https://example.com/company/coforge/financials/"
        )
        with self.assertRaises(ValidationError):
            validate(unsafe)

        partial = package_payload()
        partial["package"]["extraction"]["status"] = "partial"
        with self.assertRaises(ValidationError):
            validate(partial)


if __name__ == "__main__":
    unittest.main()
