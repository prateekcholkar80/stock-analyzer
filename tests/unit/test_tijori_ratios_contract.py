import copy
import json
import unittest

from pydantic import ValidationError

from app.fundamentals.tijori_mcp_contracts import TijoriRatiosPayload


def cell(period_key: str, value: str | None) -> dict:
    return {
        "period_key": period_key,
        "source_value": value,
        "yoy_change": None,
        "percentage_of_parent": None,
        "availability_status": "available" if value is not None else "unknown",
    }


def row(
    *,
    row_key: str,
    label: str,
    parent_row_key: str | None,
    depth: int,
    display_order: int,
    value_kind: str,
    source_unit: str,
    normalized_unit: str,
    values: tuple[str | None, str | None],
    row_kind: str = "metric",
) -> dict:
    return {
        "row_key": row_key,
        "original_label": label,
        "parent_row_key": parent_row_key,
        "depth": depth,
        "row_kind": row_kind,
        "display_order": display_order,
        "value_kind": value_kind,
        "source_unit": source_unit,
        "normalized_unit": normalized_unit,
        "values": [
            cell("period_mar_2025", values[0]),
            cell("period_mar_2026", values[1]),
        ],
    }


def ratios_payload(reporting_basis: str = "consolidated") -> dict:
    rows = [
        row(
            row_key="operational_ratios",
            label="Operational Ratios",
            parent_row_key=None,
            depth=0,
            display_order=0,
            value_kind="other",
            source_unit="not applicable",
            normalized_unit="not applicable",
            values=("0", "0"),
            row_kind="section",
        ),
        row(
            row_key="current_ratio",
            label="Current Ratio",
            parent_row_key="operational_ratios",
            depth=1,
            display_order=1,
            value_kind="ratio",
            source_unit="ratio",
            normalized_unit="ratio",
            values=("1.1", "1.2"),
        ),
        row(
            row_key="current_assets",
            label="Current Assets (Crs)",
            parent_row_key="current_ratio",
            depth=2,
            display_order=2,
            value_kind="monetary",
            source_unit="Rs. Cr.",
            normalized_unit="INR crore",
            values=("100", "120"),
        ),
        row(
            row_key="cash_conversion_cycle",
            label="Cash Conversion Cycle",
            parent_row_key="operational_ratios",
            depth=1,
            display_order=3,
            value_kind="other",
            source_unit="days",
            normalized_unit="days",
            values=("-30", "-20"),
        ),
        row(
            row_key="profitability_ratios",
            label="Profitability Ratios",
            parent_row_key=None,
            depth=0,
            display_order=4,
            value_kind="other",
            source_unit="not applicable",
            normalized_unit="not applicable",
            values=("0", "0"),
            row_kind="section",
        ),
        row(
            row_key="gross_margin",
            label="Gross Margin (%)",
            parent_row_key="profitability_ratios",
            depth=1,
            display_order=5,
            value_kind="percentage",
            source_unit="percent",
            normalized_unit="percent",
            values=("35", "36"),
        ),
        row(
            row_key="adjusted_eps",
            label="Adjusted EPS",
            parent_row_key="profitability_ratios",
            depth=1,
            display_order=6,
            value_kind="per_share",
            source_unit="per share",
            normalized_unit="per share",
            values=("10", None),
        ),
    ]
    return {
        "document": {
            "schema_version": "tijori.financial_document.v1",
            "document_type": "ratios",
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
                "row_count": 7,
                "cell_count": 14,
                "maximum_depth": 2,
                "status": "complete",
            },
        }
    }


class TijoriRatiosPayloadTests(unittest.TestCase):
    def test_accepts_complete_documents_for_both_reporting_bases(self):
        for basis in ("consolidated", "standalone"):
            with self.subTest(basis=basis):
                payload = TijoriRatiosPayload.model_validate_json(
                    json.dumps(ratios_payload(basis))
                )

                self.assertEqual(payload.document.reporting_basis, basis)
                self.assertEqual(payload.document.issuer.exchange, "NSE")
                self.assertEqual(payload.document.rows[1].value_kind, "ratio")
                self.assertEqual(payload.document.rows[2].value_kind, "monetary")
                self.assertEqual(payload.document.rows[3].source_unit, "days")
                self.assertEqual(payload.document.rows[5].value_kind, "percentage")
                self.assertEqual(payload.document.rows[6].value_kind, "per_share")
                self.assertEqual(
                    payload.document.rows[6].values[1].availability_status,
                    "unknown",
                )
                self.assertEqual(len(payload.document.document_fingerprint), 64)

    def test_rejects_wrong_type_basis_or_incomplete_expansion(self):
        for field, value in (
            ("document_type", "cash_flow"),
            ("reporting_basis", "not_applicable"),
            ("all_sections_expanded", False),
        ):
            with self.subTest(field=field):
                candidate = copy.deepcopy(ratios_payload())
                candidate["document"][field] = value
                with self.assertRaises(ValidationError):
                    TijoriRatiosPayload.model_validate(candidate)

    def test_rejects_units_that_contradict_row_semantics(self):
        for row_index, field, value in (
            (0, "source_unit", "days"),
            (1, "normalized_unit", "percent"),
            (2, "source_unit", "ratio"),
            (3, "normalized_unit", "not applicable"),
            (5, "normalized_unit", "ratio"),
            (6, "source_unit", "percent"),
        ):
            with self.subTest(row_index=row_index, field=field):
                candidate = copy.deepcopy(ratios_payload())
                candidate["document"]["rows"][row_index][field] = value
                with self.assertRaises(ValidationError):
                    TijoriRatiosPayload.model_validate(candidate)

    def test_rejects_extraction_period_and_hierarchy_drift(self):
        wrong_count = copy.deepcopy(ratios_payload())
        wrong_count["document"]["extraction"]["cell_count"] = 13
        with self.assertRaises(ValidationError):
            TijoriRatiosPayload.model_validate(wrong_count)

        wrong_period = copy.deepcopy(ratios_payload())
        wrong_period["document"]["rows"][1]["values"].reverse()
        with self.assertRaises(ValidationError):
            TijoriRatiosPayload.model_validate(wrong_period)

        bad_hierarchy = copy.deepcopy(ratios_payload())
        bad_hierarchy["document"]["rows"][2]["parent_row_key"] = "missing"
        with self.assertRaises(ValidationError):
            TijoriRatiosPayload.model_validate(bad_hierarchy)

    def test_rejects_missing_value_disguised_as_available_or_enriched(self):
        available = copy.deepcopy(ratios_payload())
        available["document"]["rows"][6]["values"][1][
            "availability_status"
        ] = "available"
        with self.assertRaises(ValidationError):
            TijoriRatiosPayload.model_validate(available)

        enriched = copy.deepcopy(ratios_payload())
        enriched["document"]["rows"][6]["values"][1]["yoy_change"] = "10%"
        with self.assertRaises(ValidationError):
            TijoriRatiosPayload.model_validate(enriched)

    def test_rejects_unsafe_source_unexpected_fields_and_naive_time(self):
        unsafe = copy.deepcopy(ratios_payload())
        unsafe["document"]["source"]["location"] = (
            "https://example.com/company/example-industries-limited/financials/"
        )
        with self.assertRaises(ValidationError):
            TijoriRatiosPayload.model_validate(unsafe)

        unexpected = copy.deepcopy(ratios_payload())
        unexpected["document"]["session_cookie"] = "must-never-cross"
        with self.assertRaises(ValidationError):
            TijoriRatiosPayload.model_validate(unexpected)

        naive = copy.deepcopy(ratios_payload())
        naive["document"]["source"]["retrieved_at"] = "2026-09-01T10:30:00"
        with self.assertRaises(ValidationError):
            TijoriRatiosPayload.model_validate(naive)


if __name__ == "__main__":
    unittest.main()
