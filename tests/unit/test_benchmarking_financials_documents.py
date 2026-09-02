import copy
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError

from app.models.financial_documents import (
    BenchmarkingSection,
    StructuredBenchmarkingFinancialsDocument,
)
from app.models.fundamentals import (
    FundamentalAvailabilityStatus,
    FundamentalIssuerIdentity,
    FundamentalValidationStatus,
    FundamentalValueKind,
    ProviderConnectionScope,
)


RETRIEVED_AT = datetime.fromisoformat("2026-09-01T10:00:00+05:30")


def document_payload() -> dict:
    companies = (
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
    )

    def cells(first: str | None, second: str | None, best: int = -1):
        return tuple(
            {
                "company_key": company["company_key"],
                "source_value": value,
                "normalized_value": Decimal(value) if value is not None else None,
                "availability_status": (
                    FundamentalAvailabilityStatus.AVAILABLE
                    if value is not None
                    else FundamentalAvailabilityStatus.UNKNOWN
                ),
                "is_best": index == best,
            }
            for index, (company, value) in enumerate(
                zip(companies, (first, second), strict=True)
            )
        )

    return {
        "document_id": (
            "tijori.NSE.COFORGE.benchmarking_financials.not_applicable"
        ),
        "connection": ProviderConnectionScope(
            tenant_id="tenant-a",
            provider="tijori",
            provider_connection_id="tijori-a",
            account_reference_hash="a" * 64,
        ),
        "issuer": FundamentalIssuerIdentity(
            exchange="NSE",
            symbol="COFORGE",
            legal_name="Coforge Ltd.",
            provider_company_id="4502",
            provider_slug="niit-technologies-limited",
        ),
        "observation_date": date(2026, 9, 1),
        "companies": companies,
        "rows": (
            {
                "row_key": "financial",
                "parent_row_key": None,
                "original_label": "Financials",
                "depth": 0,
                "row_kind": "section",
                "section": BenchmarkingSection.FINANCIALS,
                "value_kind": FundamentalValueKind.OTHER,
                "source_unit": "not applicable",
                "normalized_unit": "not applicable",
                "provider_hidden": False,
                "display_order": 0,
                "cells": cells(None, None),
            },
            {
                "row_key": "284",
                "parent_row_key": "financial",
                "original_label": "5 yr Average ROE",
                "depth": 1,
                "row_kind": "metric",
                "section": BenchmarkingSection.FINANCIALS,
                "value_kind": FundamentalValueKind.PERCENTAGE,
                "source_unit": "percent",
                "normalized_unit": "percent",
                "provider_hidden": True,
                "display_order": 1,
                "cells": cells("19.61", "15.89", best=0),
            },
        ),
        "source_location": (
            "https://www.tijorifinance.com/company/"
            "niit-technologies-limited/benchmarking/"
        ),
        "retrieved_at": RETRIEVED_AT,
        "expires_at": RETRIEVED_AT + timedelta(days=10),
        "all_rows_captured": True,
        "validation_status": FundamentalValidationStatus.VALIDATED,
    }


class StructuredBenchmarkingFinancialsDocumentTests(unittest.TestCase):
    def test_accepts_complete_provider_neutral_matrix(self):
        document = StructuredBenchmarkingFinancialsDocument.model_validate(
            document_payload()
        )

        self.assertEqual(document.issuer.symbol, "COFORGE")
        self.assertTrue(document.companies[0].is_subject)
        self.assertTrue(document.rows[1].provider_hidden)
        self.assertEqual(
            document.rows[0].cells[0].availability_status,
            FundamentalAvailabilityStatus.UNKNOWN,
        )
        self.assertEqual(
            document.rows[1].cells[0].normalized_value,
            Decimal("19.61"),
        )
        self.assertEqual(len(document.document_fingerprint), 64)

    def test_rejects_company_hierarchy_and_matrix_drift(self):
        mutations = []

        wrong_subject = copy.deepcopy(document_payload())
        wrong_subject["companies"][0]["is_subject"] = False
        mutations.append(wrong_subject)

        wrong_parent = copy.deepcopy(document_payload())
        wrong_parent["rows"][1]["parent_row_key"] = "missing"
        mutations.append(wrong_parent)

        wrong_order = copy.deepcopy(document_payload())
        wrong_order["rows"][1]["cells"] = tuple(
            reversed(wrong_order["rows"][1]["cells"])
        )
        mutations.append(wrong_order)

        incomplete = copy.deepcopy(document_payload())
        incomplete["all_rows_captured"] = False
        mutations.append(incomplete)

        for candidate in mutations:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValidationError):
                    StructuredBenchmarkingFinancialsDocument.model_validate(
                        candidate
                    )

    def test_rejects_availability_and_section_semantic_drift(self):
        wrong_availability = copy.deepcopy(document_payload())
        wrong_availability["rows"][1]["cells"][0][
            "availability_status"
        ] = FundamentalAvailabilityStatus.UNKNOWN
        with self.assertRaises(ValidationError):
            StructuredBenchmarkingFinancialsDocument.model_validate(
                wrong_availability
            )

        wrong_section = copy.deepcopy(document_payload())
        wrong_section["rows"][0]["source_unit"] = "percent"
        with self.assertRaises(ValidationError):
            StructuredBenchmarkingFinancialsDocument.model_validate(
                wrong_section
            )


if __name__ == "__main__":
    unittest.main()
