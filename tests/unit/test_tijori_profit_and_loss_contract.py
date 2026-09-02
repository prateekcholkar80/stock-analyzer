import copy
import json
import unittest

from pydantic import ValidationError

from app.fundamentals.tijori_mcp_contracts import TijoriProfitAndLossPayload


def statement_cell(period_key: str, value: str | None) -> dict:
    return {
        "period_key": period_key,
        "source_value": value,
        "yoy_change": None,
        "percentage_of_parent": None,
        "availability_status": "available" if value is not None else "unknown",
    }


def statement_row(
    *,
    row_key: str,
    label: str,
    display_order: int,
    value_kind: str,
    source_unit: str,
    normalized_unit: str,
    values: tuple[str | None, str | None],
) -> dict:
    return {
        "row_key": row_key,
        "original_label": label,
        "parent_row_key": None,
        "depth": 0,
        "row_kind": "metric",
        "display_order": display_order,
        "value_kind": value_kind,
        "source_unit": source_unit,
        "normalized_unit": normalized_unit,
        "values": [
            statement_cell("period_mar_2025", values[0]),
            statement_cell("period_mar_2026", values[1]),
        ],
    }


def profit_and_loss_payload(reporting_basis: str = "consolidated") -> dict:
    rows = [
        statement_row(
            row_key="sales",
            label="Sales",
            display_order=0,
            value_kind="monetary",
            source_unit="Rs. Cr.",
            normalized_unit="INR crore",
            values=("100", "120"),
        ),
        statement_row(
            row_key="opm",
            label="OPM (%)",
            display_order=1,
            value_kind="percentage",
            source_unit="percent",
            normalized_unit="percent",
            values=("18", "20"),
        ),
        statement_row(
            row_key="number_of_shares",
            label="Number of shares (Crs)",
            display_order=2,
            value_kind="count",
            source_unit="crore shares",
            normalized_unit="crore shares",
            values=("10", None),
        ),
    ]
    return {
        "document": {
            "schema_version": "tijori.financial_document.v1",
            "document_type": "profit_and_loss",
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
            "source_unit": "mixed",
            "normalized_unit": "mixed",
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
            "rows": rows,
            "extraction": {
                "period_count": 2,
                "row_count": 3,
                "cell_count": 6,
                "maximum_depth": 0,
                "status": "complete",
            },
        }
    }


class TijoriProfitAndLossPayloadTests(unittest.TestCase):
    def test_accepts_complete_documents_for_both_reporting_bases(self):
        for basis in ("consolidated", "standalone"):
            with self.subTest(basis=basis):
                payload = TijoriProfitAndLossPayload.model_validate_json(
                    json.dumps(profit_and_loss_payload(basis))
                )

                self.assertEqual(payload.document.reporting_basis, basis)
                self.assertEqual(payload.document.issuer.exchange, "NSE")
                self.assertEqual(payload.document.rows[0].value_kind, "monetary")
                self.assertEqual(payload.document.rows[1].value_kind, "percentage")
                self.assertEqual(payload.document.rows[2].value_kind, "count")
                self.assertEqual(
                    payload.document.rows[2].values[1].availability_status,
                    "unknown",
                )
                self.assertEqual(len(payload.document.document_fingerprint), 64)

    def test_rejects_wrong_type_basis_or_incomplete_expansion(self):
        for field, value in (
            ("document_type", "balance_sheet"),
            ("reporting_basis", "not_applicable"),
            ("all_sections_expanded", False),
        ):
            with self.subTest(field=field):
                candidate = copy.deepcopy(profit_and_loss_payload())
                candidate["document"][field] = value
                with self.assertRaises(ValidationError):
                    TijoriProfitAndLossPayload.model_validate(candidate)

    def test_rejects_units_that_contradict_the_row_value_kind(self):
        for row_index, field, value in (
            (0, "normalized_unit", "percent"),
            (1, "source_unit", "Rs. Cr."),
            (2, "normalized_unit", "INR crore"),
        ):
            with self.subTest(row_index=row_index, field=field):
                candidate = copy.deepcopy(profit_and_loss_payload())
                candidate["document"]["rows"][row_index][field] = value
                with self.assertRaises(ValidationError):
                    TijoriProfitAndLossPayload.model_validate(candidate)

    def test_rejects_mismatched_extraction_or_period_evidence(self):
        candidate = copy.deepcopy(profit_and_loss_payload())
        candidate["document"]["extraction"]["cell_count"] = 5
        with self.assertRaises(ValidationError):
            TijoriProfitAndLossPayload.model_validate(candidate)

        wrong_period = copy.deepcopy(profit_and_loss_payload())
        wrong_period["document"]["rows"][0]["values"].reverse()
        with self.assertRaises(ValidationError):
            TijoriProfitAndLossPayload.model_validate(wrong_period)

    def test_rejects_missing_value_disguised_as_available_or_enriched(self):
        available = copy.deepcopy(profit_and_loss_payload())
        available["document"]["rows"][2]["values"][1][
            "availability_status"
        ] = "available"
        with self.assertRaises(ValidationError):
            TijoriProfitAndLossPayload.model_validate(available)

        enriched = copy.deepcopy(profit_and_loss_payload())
        enriched["document"]["rows"][2]["values"][1]["yoy_change"] = "10%"
        with self.assertRaises(ValidationError):
            TijoriProfitAndLossPayload.model_validate(enriched)

    def test_rejects_unsafe_source_unexpected_fields_and_naive_time(self):
        unsafe = copy.deepcopy(profit_and_loss_payload())
        unsafe["document"]["source"]["location"] = (
            "https://example.com/company/example-industries-limited/financials/"
        )
        with self.assertRaises(ValidationError):
            TijoriProfitAndLossPayload.model_validate(unsafe)

        unexpected = copy.deepcopy(profit_and_loss_payload())
        unexpected["document"]["session_cookie"] = "must-never-cross"
        with self.assertRaises(ValidationError):
            TijoriProfitAndLossPayload.model_validate(unexpected)

        naive = copy.deepcopy(profit_and_loss_payload())
        naive["document"]["source"]["retrieved_at"] = "2026-08-31T10:30:00"
        with self.assertRaises(ValidationError):
            TijoriProfitAndLossPayload.model_validate(naive)


if __name__ == "__main__":
    unittest.main()
