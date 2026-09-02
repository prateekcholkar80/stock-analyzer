import copy
import unittest
from datetime import date, datetime, timedelta
from decimal import Decimal

from pydantic import ValidationError

from app.models.financial_documents import StructuredPeerComparisonDocument
from app.models.fundamentals import (
    FundamentalAvailabilityStatus,
    FundamentalIssuerIdentity,
    FundamentalValidationStatus,
    FundamentalValueKind,
    ProviderConnectionScope,
)


RETRIEVED_AT = datetime.fromisoformat("2026-09-01T10:00:00+05:30")


def document_payload() -> dict:
    connection = ProviderConnectionScope(
        tenant_id="tenant-a",
        provider="tijori",
        provider_connection_id="tijori-a",
        account_reference_hash="a" * 64,
    )
    issuer = FundamentalIssuerIdentity(
        exchange="NSE",
        symbol="COFORGE",
        legal_name="Coforge Ltd.",
        provider_company_id="123",
        provider_slug="niit-technologies-limited",
    )
    metrics = [
        {
            "metric_key": "latest_price",
            "source_label": "Latest Price",
            "standardized_label": "Latest Price",
            "value_kind": FundamentalValueKind.MONETARY,
            "source_unit": "INR",
            "normalized_unit": "INR",
            "currency": "INR",
            "display_order": 0,
        },
        {
            "metric_key": "pe",
            "source_label": "PE",
            "standardized_label": "P/E",
            "value_kind": FundamentalValueKind.RATIO,
            "source_unit": "ratio",
            "normalized_unit": "ratio",
            "currency": None,
            "display_order": 1,
        },
    ]

    def peer(slug: str, order: int, subject: bool, values: tuple[str | None, ...]) -> dict:
        return {
            "peer_key": f"peer_{slug.replace('-', '_')}",
            "legal_name": slug.replace("-", " ").title(),
            "provider_slug": slug,
            "is_subject": subject,
            "display_order": order,
            "cells": tuple(
                {
                    "metric_key": metric["metric_key"],
                    "source_value": value,
                    "normalized_value": Decimal(value) if value is not None else None,
                    "availability_status": (
                        FundamentalAvailabilityStatus.AVAILABLE
                        if value is not None
                        else FundamentalAvailabilityStatus.UNKNOWN
                    ),
                }
                for metric, value in zip(metrics, values, strict=True)
            ),
        }

    return {
        "document_id": "tijori.NSE.COFORGE.peer_comparison.not_applicable",
        "connection": connection,
        "issuer": issuer,
        "observation_date": date(2026, 9, 1),
        "metrics": tuple(metrics),
        "peers": (
            peer("niit-technologies-limited", 0, True, ("1450.50", None)),
            peer("persistent-systems-limited", 1, False, ("5100", "55.4")),
        ),
        "source_location": (
            "https://www.tijorifinance.com/company/niit-technologies-limited/"
        ),
        "retrieved_at": RETRIEVED_AT,
        "expires_at": RETRIEVED_AT + timedelta(days=10),
        "validation_status": FundamentalValidationStatus.VALIDATED,
    }


class StructuredPeerComparisonDocumentTests(unittest.TestCase):
    def test_accepts_complete_provider_neutral_matrix(self):
        document = StructuredPeerComparisonDocument.model_validate(
            document_payload()
        )

        self.assertEqual(document.issuer.symbol, "COFORGE")
        self.assertTrue(document.peers[0].is_subject)
        self.assertEqual(
            document.peers[0].cells[1].availability_status,
            FundamentalAvailabilityStatus.UNKNOWN,
        )
        self.assertEqual(document.peers[1].cells[1].normalized_value, Decimal("55.4"))
        self.assertEqual(len(document.document_fingerprint), 64)

    def test_rejects_semantic_and_matrix_drift(self):
        mutations = []

        wrong_currency = copy.deepcopy(document_payload())
        wrong_currency["metrics"][1]["currency"] = "INR"
        mutations.append(wrong_currency)

        wrong_subject = copy.deepcopy(document_payload())
        wrong_subject["peers"][0]["is_subject"] = False
        mutations.append(wrong_subject)

        wrong_order = copy.deepcopy(document_payload())
        wrong_order["peers"][0]["cells"] = tuple(
            reversed(wrong_order["peers"][0]["cells"])
        )
        mutations.append(wrong_order)

        wrong_expiry = copy.deepcopy(document_payload())
        wrong_expiry["expires_at"] = RETRIEVED_AT
        mutations.append(wrong_expiry)

        for candidate in mutations:
            with self.subTest(candidate=candidate):
                with self.assertRaises(ValidationError):
                    StructuredPeerComparisonDocument.model_validate(candidate)


if __name__ == "__main__":
    unittest.main()
