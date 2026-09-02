import copy
import unittest

from pydantic import ValidationError

from app.fundamentals.tijori_mcp_contracts import TijoriGrowthTablePayload


def growth_table_payload() -> dict:
    return {
        "document": {
            "schema_version": "tijori.financial_document.v1",
            "document_type": "growth_table",
            "reporting_basis": "consolidated",
            "issuer": {
                "exchange": "nse",
                "symbol": "coforge",
                "legal_name": "Coforge Limited",
                "provider_company_id": "coforge-1",
                "provider_slug": "coforge-limited",
            },
            "source": {
                "provider": "tijori",
                "location": (
                    "https://www.tijorifinance.com/company/"
                    "coforge-limited/financials/"
                ),
                "retrieved_at": "2026-08-30T17:30:00+05:30",
            },
            "unit": "In Rs. Cr.",
            "all_sections_expanded": True,
            "columns": [
                {
                    "column_key": "column_0",
                    "source_label": "3Y",
                    "display_order": 0,
                },
                {
                    "column_key": "column_1",
                    "source_label": "5Y",
                    "display_order": 1,
                },
            ],
            "rows": [
                {
                    "row_key": "sales_growth",
                    "original_label": "Sales Growth",
                    "parent_row_key": None,
                    "depth": 0,
                    "row_kind": "section",
                    "display_order": 0,
                    "values": [
                        {
                            "column_key": "column_0",
                            "source_value": "15.4%",
                            "yoy_change": None,
                            "percentage_of_parent": "15.4%",
                            "availability_status": "available",
                        },
                        {
                            "column_key": "column_1",
                            "source_value": None,
                            "yoy_change": None,
                            "percentage_of_parent": None,
                            "availability_status": "unknown",
                        },
                    ],
                },
                {
                    "row_key": "revenue_cagr",
                    "original_label": "Revenue CAGR",
                    "parent_row_key": "sales_growth",
                    "depth": 1,
                    "row_kind": "metric",
                    "display_order": 1,
                    "values": [
                        {
                            "column_key": "column_0",
                            "source_value": "18.2%",
                            "yoy_change": "12%",
                            "percentage_of_parent": None,
                            "availability_status": "available",
                        },
                        {
                            "column_key": "column_1",
                            "source_value": "17.8%",
                            "yoy_change": "-2%",
                            "percentage_of_parent": None,
                            "availability_status": "available",
                        },
                    ],
                },
            ],
            "extraction": {
                "column_count": 2,
                "row_count": 2,
                "status": "complete",
            },
        }
    }


class TijoriGrowthTablePayloadTests(unittest.TestCase):
    def test_accepts_complete_growth_table_and_normalizes_market_identity(self):
        payload = TijoriGrowthTablePayload.model_validate_json(
            __import__("json").dumps(growth_table_payload())
        )

        self.assertEqual(payload.document.issuer.exchange, "NSE")
        self.assertEqual(payload.document.issuer.symbol, "COFORGE")
        self.assertEqual(
            payload.document.rows[1].values[0].yoy_change,
            "12%",
        )
        self.assertEqual(
            payload.document.rows[0].values[0].percentage_of_parent,
            "15.4%",
        )
        self.assertEqual(len(payload.document.document_fingerprint), 64)

    def test_rejects_collapsed_or_incomplete_documents(self):
        for field, value in (
            ("all_sections_expanded", False),
            ("extraction.status", "partial"),
        ):
            with self.subTest(field=field):
                candidate = copy.deepcopy(growth_table_payload())
                if field == "all_sections_expanded":
                    candidate["document"][field] = value
                else:
                    candidate["document"]["extraction"]["status"] = value
                with self.assertRaises(ValidationError):
                    TijoriGrowthTablePayload.model_validate(candidate)

    def test_rejects_wrong_source_or_mismatched_extraction_counts(self):
        unsafe = copy.deepcopy(growth_table_payload())
        unsafe["document"]["source"]["location"] = (
            "https://example.com/company/coforge-limited/financials/"
        )
        with self.assertRaises(ValidationError):
            TijoriGrowthTablePayload.model_validate(unsafe)

        mismatched = copy.deepcopy(growth_table_payload())
        mismatched["document"]["extraction"]["row_count"] = 1
        with self.assertRaises(ValidationError):
            TijoriGrowthTablePayload.model_validate(mismatched)

    def test_rejects_missing_values_disguised_as_available(self):
        candidate = copy.deepcopy(growth_table_payload())
        candidate["document"]["rows"][0]["values"][1][
            "availability_status"
        ] = "available"

        with self.assertRaises(ValidationError):
            TijoriGrowthTablePayload.model_validate(candidate)

    def test_rejects_auxiliary_values_without_primary_value(self):
        candidate = copy.deepcopy(growth_table_payload())
        candidate["document"]["rows"][0]["values"][1][
            "yoy_change"
        ] = "10%"

        with self.assertRaises(ValidationError):
            TijoriGrowthTablePayload.model_validate(candidate)

    def test_rejects_unbounded_or_non_string_auxiliary_values(self):
        oversized = copy.deepcopy(growth_table_payload())
        oversized["document"]["rows"][1]["values"][0][
            "percentage_of_parent"
        ] = "x" * 81
        with self.assertRaises(ValidationError):
            TijoriGrowthTablePayload.model_validate(oversized)

        wrong_type = copy.deepcopy(growth_table_payload())
        wrong_type["document"]["rows"][1]["values"][0][
            "yoy_change"
        ] = 12
        with self.assertRaises(ValidationError):
            TijoriGrowthTablePayload.model_validate(wrong_type)

    def test_rejects_column_order_and_invalid_hierarchy(self):
        wrong_columns = copy.deepcopy(growth_table_payload())
        wrong_columns["document"]["rows"][1]["values"].reverse()
        with self.assertRaises(ValidationError):
            TijoriGrowthTablePayload.model_validate(wrong_columns)

        bad_hierarchy = copy.deepcopy(growth_table_payload())
        bad_hierarchy["document"]["rows"][1]["parent_row_key"] = "missing"
        with self.assertRaises(ValidationError):
            TijoriGrowthTablePayload.model_validate(bad_hierarchy)

    def test_rejects_unexpected_fields_and_naive_retrieval_time(self):
        unexpected = copy.deepcopy(growth_table_payload())
        unexpected["document"]["session_cookie"] = "must-never-cross"
        with self.assertRaises(ValidationError):
            TijoriGrowthTablePayload.model_validate(unexpected)

        naive = copy.deepcopy(growth_table_payload())
        naive["document"]["source"]["retrieved_at"] = "2026-08-30T17:30:00"
        with self.assertRaises(ValidationError):
            TijoriGrowthTablePayload.model_validate_json(
                __import__("json").dumps(naive)
            )


if __name__ == "__main__":
    unittest.main()
