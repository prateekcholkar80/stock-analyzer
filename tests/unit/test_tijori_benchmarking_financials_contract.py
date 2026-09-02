import copy
import json
import unittest

from pydantic import ValidationError

from app.fundamentals.tijori_mcp_contracts import (
    TijoriBenchmarkingFinancialsPayload,
)


def benchmarking_payload() -> dict:
    companies = [
        {
            "company_key": "company_niit_technologies_limited",
            "legal_name": "Coforge",
            "provider_slug": "niit-technologies-limited",
            "is_subject": True,
            "display_order": 0,
        },
        {
            "company_key": "company_kpit_technologies_limited",
            "legal_name": "Birlasoft",
            "provider_slug": "kpit-technologies-limited",
            "is_subject": False,
            "display_order": 1,
        },
    ]

    def values(first: str | None, second: str | None, best: int = -1):
        source_values = (first, second)
        return [
            {
                "company_key": company["company_key"],
                "source_value": source_value,
                "availability_status": (
                    "available" if source_value is not None else "unknown"
                ),
                "is_best": index == best,
            }
            for index, (company, source_value) in enumerate(
                zip(companies, source_values, strict=True)
            )
        ]

    return {
        "document": {
            "schema_version": "tijori.benchmarking_financials.v1",
            "document_type": "benchmarking_financials",
            "reporting_basis": "not_applicable",
            "issuer": {
                "exchange": "nse",
                "symbol": "coforge",
                "legal_name": "Coforge Ltd.",
                "provider_company_id": "4502",
                "provider_slug": "niit-technologies-limited",
            },
            "source": {
                "provider": "tijori",
                "location": (
                    "https://www.tijorifinance.com/company/"
                    "niit-technologies-limited/benchmarking/"
                ),
                "retrieved_at": "2026-09-01T10:00:00+05:30",
            },
            "observation_date": "2026-09-01",
            "all_rows_captured": True,
            "companies": companies,
            "rows": [
                {
                    "row_key": "financial",
                    "parent_row_key": None,
                    "original_label": "Financials",
                    "depth": 0,
                    "row_kind": "section",
                    "provider_section": "bch_financial",
                    "provider_hidden": False,
                    "display_order": 0,
                    "values": values(None, None),
                },
                {
                    "row_key": "284",
                    "parent_row_key": "financial",
                    "original_label": "5 yr Average ROE",
                    "depth": 1,
                    "row_kind": "section",
                    "provider_section": "bch_financial",
                    "provider_hidden": False,
                    "display_order": 1,
                    "values": values("19.61 %", "15.89 %", best=0),
                },
                {
                    "row_key": "303",
                    "parent_row_key": "financial",
                    "original_label": "5yr Avg ROCE",
                    "depth": 1,
                    "row_kind": "metric",
                    "provider_section": "bch_financial",
                    "provider_hidden": True,
                    "display_order": 2,
                    "values": values("0", None, best=0),
                },
            ],
            "extraction": {
                "company_count": 2,
                "row_count": 3,
                "cell_count": 6,
                "maximum_depth": 1,
                "provider_hidden_row_count": 1,
                "status": "complete",
            },
        }
    }


class TijoriBenchmarkingFinancialsPayloadTests(unittest.TestCase):
    def test_accepts_complete_hierarchical_matrix(self):
        payload = TijoriBenchmarkingFinancialsPayload.model_validate_json(
            json.dumps(benchmarking_payload())
        )

        self.assertEqual(payload.document.issuer.exchange, "NSE")
        self.assertEqual(payload.document.reporting_basis, "not_applicable")
        self.assertTrue(payload.document.companies[0].is_subject)
        self.assertEqual(payload.document.rows[2].values[0].source_value, "0")
        self.assertEqual(
            payload.document.rows[2].values[1].availability_status,
            "unknown",
        )
        self.assertTrue(payload.document.rows[2].provider_hidden)
        self.assertEqual(len(payload.document.document_fingerprint), 64)

    def test_rejects_identity_source_and_subject_drift(self):
        for section, field, value in (
            ("issuer", "provider_company_id", None),
            ("issuer", "provider_slug", "different-company"),
            ("source", "location", "https://example.com/benchmarking/"),
        ):
            with self.subTest(section=section, field=field):
                candidate = copy.deepcopy(benchmarking_payload())
                candidate["document"][section][field] = value
                with self.assertRaises(ValidationError):
                    TijoriBenchmarkingFinancialsPayload.model_validate(candidate)

        candidate = copy.deepcopy(benchmarking_payload())
        candidate["document"]["companies"][0]["is_subject"] = False
        with self.assertRaises(ValidationError):
            TijoriBenchmarkingFinancialsPayload.model_validate(candidate)

    def test_rejects_hierarchy_order_and_matrix_drift(self):
        wrong_parent = copy.deepcopy(benchmarking_payload())
        wrong_parent["document"]["rows"][2]["parent_row_key"] = "missing"
        with self.assertRaises(ValidationError):
            TijoriBenchmarkingFinancialsPayload.model_validate(wrong_parent)

        wrong_order = copy.deepcopy(benchmarking_payload())
        wrong_order["document"]["rows"][1]["values"].reverse()
        with self.assertRaises(ValidationError):
            TijoriBenchmarkingFinancialsPayload.model_validate(wrong_order)

        wrong_availability = copy.deepcopy(benchmarking_payload())
        wrong_availability["document"]["rows"][2]["values"][0][
            "availability_status"
        ] = "unknown"
        with self.assertRaises(ValidationError):
            TijoriBenchmarkingFinancialsPayload.model_validate(
                wrong_availability
            )

    def test_rejects_duplicate_companies_and_extraction_count_drift(self):
        duplicate = copy.deepcopy(benchmarking_payload())
        duplicate["document"]["companies"][1] = copy.deepcopy(
            duplicate["document"]["companies"][0]
        )
        duplicate["document"]["companies"][1]["display_order"] = 1
        duplicate["document"]["companies"][1]["is_subject"] = False
        with self.assertRaises(ValidationError):
            TijoriBenchmarkingFinancialsPayload.model_validate(duplicate)

        wrong_count = copy.deepcopy(benchmarking_payload())
        wrong_count["document"]["extraction"]["provider_hidden_row_count"] = 0
        with self.assertRaises(ValidationError):
            TijoriBenchmarkingFinancialsPayload.model_validate(wrong_count)


if __name__ == "__main__":
    unittest.main()
