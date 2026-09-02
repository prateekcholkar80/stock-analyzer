import copy
import json
import unittest

from pydantic import ValidationError

from app.fundamentals.tijori_mcp_contracts import TijoriCashFlowPayload


def cell(period_key: str, value: str | None) -> dict:
    return {
        "period_key": period_key,
        "source_value": value,
        "yoy_change": None,
        "percentage_of_parent": None,
        "availability_status": "available" if value is not None else "unknown",
    }


def cash_flow_payload(reporting_basis: str = "consolidated") -> dict:
    return {
        "document": {
            "schema_version": "tijori.financial_document.v1",
            "document_type": "cash_flow",
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
                "retrieved_at": "2026-09-01T10:30:00+05:30",
            },
            "source_unit": "Rs. Cr.",
            "normalized_unit": "INR crore",
            "skipped_report_dates": ["Mar 2023"],
            "all_sections_expanded": True,
            "periods": [
                {
                    "period_key": "period_mar_2025",
                    "source_label": "Mar 2025",
                    "display_order": 0,
                },
                {
                    "period_key": "period_mar_2026",
                    "source_label": "Mar 2026",
                    "display_order": 1,
                },
            ],
            "rows": [
                {
                    "row_key": "cash_from_operating_activity",
                    "original_label": "Cash from Operating Activity",
                    "parent_row_key": None,
                    "depth": 0,
                    "row_kind": "section",
                    "display_order": 0,
                    "values": [
                        cell("period_mar_2025", "100"),
                        cell("period_mar_2026", "120"),
                    ],
                },
                {
                    "row_key": "working_capital_changes",
                    "original_label": "Working Capital Changes",
                    "parent_row_key": "cash_from_operating_activity",
                    "depth": 1,
                    "row_kind": "metric",
                    "display_order": 1,
                    "values": [
                        cell("period_mar_2025", "0"),
                        cell("period_mar_2026", "-12"),
                    ],
                },
                {
                    "row_key": "net_cash_flow",
                    "original_label": "Net Cash Flow",
                    "parent_row_key": None,
                    "depth": 0,
                    "row_kind": "metric",
                    "display_order": 2,
                    "values": [
                        cell("period_mar_2025", "0"),
                        cell("period_mar_2026", None),
                    ],
                },
            ],
            "extraction": {
                "period_count": 2,
                "row_count": 3,
                "cell_count": 6,
                "maximum_depth": 1,
                "status": "complete",
            },
        }
    }


class TijoriCashFlowPayloadTests(unittest.TestCase):
    def test_accepts_complete_documents_for_both_reporting_bases(self):
        for basis in ("consolidated", "standalone"):
            with self.subTest(basis=basis):
                payload = TijoriCashFlowPayload.model_validate_json(
                    json.dumps(cash_flow_payload(basis))
                )

                self.assertEqual(payload.document.reporting_basis, basis)
                self.assertEqual(payload.document.issuer.exchange, "NSE")
                self.assertEqual(
                    payload.document.rows[1].values[0].source_value,
                    "0",
                )
                self.assertEqual(
                    payload.document.rows[1].values[1].source_value,
                    "-12",
                )
                self.assertEqual(
                    payload.document.rows[2].values[1].availability_status,
                    "unknown",
                )
                self.assertEqual(len(payload.document.document_fingerprint), 64)

    def test_rejects_wrong_type_basis_units_or_incomplete_expansion(self):
        mutations = (
            ("document_type", "balance_sheet"),
            ("reporting_basis", "not_applicable"),
            ("source_unit", "mixed"),
            ("normalized_unit", "percent"),
            ("all_sections_expanded", False),
        )
        for field, value in mutations:
            with self.subTest(field=field):
                candidate = copy.deepcopy(cash_flow_payload())
                candidate["document"][field] = value
                with self.assertRaises(ValidationError):
                    TijoriCashFlowPayload.model_validate(candidate)

    def test_rejects_mismatched_extraction_evidence(self):
        for field, value in (
            ("period_count", 1),
            ("row_count", 2),
            ("cell_count", 5),
            ("maximum_depth", 0),
            ("status", "partial"),
        ):
            with self.subTest(field=field):
                candidate = copy.deepcopy(cash_flow_payload())
                candidate["document"]["extraction"][field] = value
                with self.assertRaises(ValidationError):
                    TijoriCashFlowPayload.model_validate(candidate)

    def test_rejects_period_order_and_invalid_hierarchy(self):
        wrong_period = copy.deepcopy(cash_flow_payload())
        wrong_period["document"]["rows"][1]["values"].reverse()
        with self.assertRaises(ValidationError):
            TijoriCashFlowPayload.model_validate(wrong_period)

        bad_hierarchy = copy.deepcopy(cash_flow_payload())
        bad_hierarchy["document"]["rows"][1]["parent_row_key"] = "missing"
        with self.assertRaises(ValidationError):
            TijoriCashFlowPayload.model_validate(bad_hierarchy)

    def test_rejects_missing_value_disguised_as_available_or_enriched(self):
        available = copy.deepcopy(cash_flow_payload())
        available["document"]["rows"][2]["values"][1][
            "availability_status"
        ] = "available"
        with self.assertRaises(ValidationError):
            TijoriCashFlowPayload.model_validate(available)

        enriched = copy.deepcopy(cash_flow_payload())
        enriched["document"]["rows"][2]["values"][1]["yoy_change"] = "10%"
        with self.assertRaises(ValidationError):
            TijoriCashFlowPayload.model_validate(enriched)

    def test_rejects_unsafe_source_unexpected_fields_and_naive_time(self):
        unsafe = copy.deepcopy(cash_flow_payload())
        unsafe["document"]["source"]["location"] = (
            "https://example.com/company/example-industries-limited/financials/"
        )
        with self.assertRaises(ValidationError):
            TijoriCashFlowPayload.model_validate(unsafe)

        unexpected = copy.deepcopy(cash_flow_payload())
        unexpected["document"]["session_cookie"] = "must-never-cross"
        with self.assertRaises(ValidationError):
            TijoriCashFlowPayload.model_validate(unexpected)

        naive = copy.deepcopy(cash_flow_payload())
        naive["document"]["source"]["retrieved_at"] = "2026-09-01T10:30:00"
        with self.assertRaises(ValidationError):
            TijoriCashFlowPayload.model_validate(naive)


if __name__ == "__main__":
    unittest.main()
