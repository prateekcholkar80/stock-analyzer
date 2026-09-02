import copy
import json
import unittest

from pydantic import ValidationError

from app.fundamentals.tijori_mcp_contracts import TijoriBalanceSheetPayload


def balance_sheet_payload(reporting_basis: str = "consolidated") -> dict:
    return {
        "document": {
            "schema_version": "tijori.financial_document.v1",
            "document_type": "balance_sheet",
            "reporting_basis": reporting_basis,
            "issuer": {
                "exchange": "nse",
                "symbol": "example",
                "legal_name": "Example Industries Limited",
                "provider_company_id": "example-1",
                "provider_slug": "example-industries-limited",
            },
            "source": {
                "provider": "tijori",
                "location": (
                    "https://www.tijorifinance.com/company/"
                    "example-industries-limited/financials/"
                ),
                "retrieved_at": "2026-08-31T10:30:00+05:30",
            },
            "source_unit": "Rs. Cr.",
            "normalized_unit": "INR crore",
            "skipped_report_dates": ["Mar 2023"],
            "all_sections_expanded": True,
            "periods": [
                {
                    "period_key": "period_mar_2024",
                    "source_label": "Mar 2024",
                    "display_order": 0,
                },
                {
                    "period_key": "period_mar_2025",
                    "source_label": "Mar 2025",
                    "display_order": 1,
                },
            ],
            "rows": [
                {
                    "row_key": "assets",
                    "original_label": "Assets",
                    "parent_row_key": None,
                    "depth": 0,
                    "row_kind": "section",
                    "display_order": 0,
                    "values": [
                        {
                            "period_key": "period_mar_2024",
                            "source_value": "100",
                            "yoy_change": None,
                            "percentage_of_parent": "100%",
                            "availability_status": "available",
                        },
                        {
                            "period_key": "period_mar_2025",
                            "source_value": "120",
                            "yoy_change": "20%",
                            "percentage_of_parent": "100%",
                            "availability_status": "available",
                        },
                    ],
                },
                {
                    "row_key": "current_assets",
                    "original_label": "Current Assets",
                    "parent_row_key": "assets",
                    "depth": 1,
                    "row_kind": "section",
                    "display_order": 1,
                    "values": [
                        {
                            "period_key": "period_mar_2024",
                            "source_value": "40",
                            "yoy_change": None,
                            "percentage_of_parent": "40%",
                            "availability_status": "available",
                        },
                        {
                            "period_key": "period_mar_2025",
                            "source_value": None,
                            "yoy_change": None,
                            "percentage_of_parent": None,
                            "availability_status": "unknown",
                        },
                    ],
                },
            ],
            "extraction": {
                "period_count": 2,
                "row_count": 2,
                "cell_count": 4,
                "maximum_depth": 1,
                "status": "complete",
            },
        }
    }


class TijoriBalanceSheetPayloadTests(unittest.TestCase):
    def test_accepts_complete_documents_for_both_reporting_bases(self):
        for basis in ("consolidated", "standalone"):
            with self.subTest(basis=basis):
                payload = TijoriBalanceSheetPayload.model_validate_json(
                    json.dumps(balance_sheet_payload(basis))
                )

                self.assertEqual(payload.document.reporting_basis, basis)
                self.assertEqual(payload.document.issuer.exchange, "NSE")
                self.assertEqual(payload.document.issuer.symbol, "EXAMPLE")
                self.assertEqual(
                    payload.document.skipped_report_dates,
                    ("Mar 2023",),
                )
                self.assertEqual(
                    payload.document.rows[1].values[1].availability_status,
                    "unknown",
                )
                self.assertEqual(len(payload.document.document_fingerprint), 64)

    def test_accepts_bounded_fully_expanded_20_000_cell_document(self):
        candidate = balance_sheet_payload()
        document = candidate["document"]
        document["skipped_report_dates"] = []
        document["periods"] = [
            {
                "period_key": f"period_{index}",
                "source_label": f"Mar {1987 + index}",
                "display_order": index,
            }
            for index in range(40)
        ]
        document["rows"] = [
            {
                "row_key": f"row_{row_index}",
                "original_label": f"Financial statement line {row_index}",
                "parent_row_key": None if row_index == 0 else "row_0",
                "depth": 0 if row_index == 0 else 1,
                "row_kind": "section" if row_index == 0 else "metric",
                "display_order": row_index,
                "values": [
                    {
                        "period_key": period["period_key"],
                        "source_value": str((row_index + 1) * (period_index + 1)),
                        "yoy_change": None,
                        "percentage_of_parent": None,
                        "availability_status": "available",
                    }
                    for period_index, period in enumerate(document["periods"])
                ],
            }
            for row_index in range(500)
        ]
        document["extraction"] = {
            "period_count": 40,
            "row_count": 500,
            "cell_count": 20_000,
            "maximum_depth": 1,
            "status": "complete",
        }

        payload = TijoriBalanceSheetPayload.model_validate_json(
            json.dumps(candidate)
        )

        self.assertEqual(len(payload.document.periods), 40)
        self.assertEqual(len(payload.document.rows), 500)
        self.assertEqual(len(payload.document.rows[-1].values), 40)
        self.assertEqual(len(payload.document.document_fingerprint), 64)

    def test_rejects_wrong_type_basis_or_incomplete_expansion(self):
        mutations = (
            ("document_type", "growth_table"),
            ("reporting_basis", "not_applicable"),
            ("all_sections_expanded", False),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                candidate = copy.deepcopy(balance_sheet_payload())
                candidate["document"][field] = value
                with self.assertRaises(ValidationError):
                    TijoriBalanceSheetPayload.model_validate(candidate)

    def test_rejects_mismatched_extraction_evidence(self):
        for field, value in (
            ("period_count", 1),
            ("row_count", 1),
            ("cell_count", 3),
            ("maximum_depth", 0),
            ("status", "partial"),
        ):
            with self.subTest(field=field):
                candidate = copy.deepcopy(balance_sheet_payload())
                candidate["document"]["extraction"][field] = value
                with self.assertRaises(ValidationError):
                    TijoriBalanceSheetPayload.model_validate(candidate)

    def test_rejects_period_order_and_invalid_hierarchy(self):
        wrong_period = copy.deepcopy(balance_sheet_payload())
        wrong_period["document"]["rows"][1]["values"].reverse()
        with self.assertRaises(ValidationError):
            TijoriBalanceSheetPayload.model_validate(wrong_period)

        bad_hierarchy = copy.deepcopy(balance_sheet_payload())
        bad_hierarchy["document"]["rows"][1]["parent_row_key"] = "missing"
        with self.assertRaises(ValidationError):
            TijoriBalanceSheetPayload.model_validate(bad_hierarchy)

    def test_rejects_missing_value_disguised_as_available_or_enriched(self):
        available = copy.deepcopy(balance_sheet_payload())
        available["document"]["rows"][1]["values"][1][
            "availability_status"
        ] = "available"
        with self.assertRaises(ValidationError):
            TijoriBalanceSheetPayload.model_validate(available)

        enriched = copy.deepcopy(balance_sheet_payload())
        enriched["document"]["rows"][1]["values"][1][
            "yoy_change"
        ] = "10%"
        with self.assertRaises(ValidationError):
            TijoriBalanceSheetPayload.model_validate(enriched)

    def test_rejects_unsafe_source_unexpected_fields_and_naive_time(self):
        unsafe = copy.deepcopy(balance_sheet_payload())
        unsafe["document"]["source"]["location"] = (
            "https://example.com/company/example-industries-limited/financials/"
        )
        with self.assertRaises(ValidationError):
            TijoriBalanceSheetPayload.model_validate(unsafe)

        unexpected = copy.deepcopy(balance_sheet_payload())
        unexpected["document"]["session_cookie"] = "must-never-cross"
        with self.assertRaises(ValidationError):
            TijoriBalanceSheetPayload.model_validate(unexpected)

        naive = copy.deepcopy(balance_sheet_payload())
        naive["document"]["source"]["retrieved_at"] = "2026-08-31T10:30:00"
        with self.assertRaises(ValidationError):
            TijoriBalanceSheetPayload.model_validate(naive)


if __name__ == "__main__":
    unittest.main()
