import copy
import json
import unittest

from pydantic import ValidationError

from app.fundamentals.tijori_mcp_contracts import TijoriPeerComparisonPayload


def peer_comparison_payload() -> dict:
    metrics = [
        {
            "metric_key": "latest_price",
            "standardized_label": "Latest Price",
            "value_kind": "monetary",
            "source_unit": "INR",
            "source_label": "Latest Price",
            "display_order": 0,
        },
        {
            "metric_key": "pe",
            "standardized_label": "P/E",
            "value_kind": "ratio",
            "source_unit": "ratio",
            "source_label": "PE",
            "display_order": 1,
        },
        {
            "metric_key": "promoter_holding",
            "standardized_label": "Promoter Holding",
            "value_kind": "percentage",
            "source_unit": "percent",
            "source_label": "Prom Holding(%)",
            "display_order": 2,
        },
    ]

    def peer(
        slug: str,
        name: str,
        display_order: int,
        is_subject: bool,
        values: tuple[str | None, ...],
    ) -> dict:
        return {
            "peer_key": f"peer_{slug.replace('-', '_')}",
            "legal_name": name,
            "provider_slug": slug,
            "is_subject": is_subject,
            "display_order": display_order,
            "values": [
                {
                    "metric_key": metric["metric_key"],
                    "source_value": value,
                    "availability_status": (
                        "available" if value is not None else "unknown"
                    ),
                }
                for metric, value in zip(metrics, values, strict=True)
            ],
        }

    return {
        "document": {
            "schema_version": "tijori.peer_comparison.v1",
            "document_type": "peer_comparison",
            "issuer": {
                "exchange": "nse",
                "symbol": "coforge",
                "legal_name": "Coforge Ltd.",
                "provider_company_id": "123",
                "provider_slug": "niit-technologies-limited",
            },
            "source": {
                "provider": "tijori",
                "location": (
                    "https://www.tijorifinance.com/company/"
                    "niit-technologies-limited/"
                ),
                "retrieved_at": "2026-09-01T10:00:00+05:30",
            },
            "observation_date": "2026-09-01",
            "metrics": metrics,
            "peers": [
                peer(
                    "niit-technologies-limited",
                    "Coforge Ltd.",
                    0,
                    True,
                    ("1450.50", "24.1", None),
                ),
                peer(
                    "persistent-systems-limited",
                    "Persistent Systems Ltd.",
                    1,
                    False,
                    ("5100", "55.4", "0"),
                ),
            ],
            "extraction": {
                "metric_count": 3,
                "peer_count": 2,
                "cell_count": 6,
                "status": "complete",
            },
        }
    }


class TijoriPeerComparisonPayloadTests(unittest.TestCase):
    def test_accepts_complete_cross_sectional_document(self):
        payload = TijoriPeerComparisonPayload.model_validate_json(
            json.dumps(peer_comparison_payload())
        )

        self.assertEqual(payload.document.issuer.exchange, "NSE")
        self.assertEqual(payload.document.issuer.symbol, "COFORGE")
        self.assertEqual(payload.document.observation_date.isoformat(), "2026-09-01")
        self.assertTrue(payload.document.peers[0].is_subject)
        self.assertEqual(
            payload.document.peers[0].values[2].availability_status,
            "unknown",
        )
        self.assertEqual(payload.document.peers[1].values[2].source_value, "0")
        self.assertEqual(len(payload.document.document_fingerprint), 64)

    def test_rejects_identity_source_and_subject_drift(self):
        mutations = (
            ("issuer", "provider_company_id", None),
            ("issuer", "provider_slug", "different-company"),
            ("source", "location", "https://example.com/company/coforge/"),
        )
        for section, field, value in mutations:
            with self.subTest(section=section, field=field):
                candidate = copy.deepcopy(peer_comparison_payload())
                candidate["document"][section][field] = value
                with self.assertRaises(ValidationError):
                    TijoriPeerComparisonPayload.model_validate(candidate)

        candidate = copy.deepcopy(peer_comparison_payload())
        candidate["document"]["peers"][0]["is_subject"] = False
        with self.assertRaises(ValidationError):
            TijoriPeerComparisonPayload.model_validate(candidate)

    def test_rejects_metric_semantic_order_and_availability_drift(self):
        wrong_unit = copy.deepcopy(peer_comparison_payload())
        wrong_unit["document"]["metrics"][1]["source_unit"] = "percent"
        with self.assertRaises(ValidationError):
            TijoriPeerComparisonPayload.model_validate(wrong_unit)

        wrong_order = copy.deepcopy(peer_comparison_payload())
        wrong_order["document"]["peers"][0]["values"].reverse()
        with self.assertRaises(ValidationError):
            TijoriPeerComparisonPayload.model_validate(wrong_order)

        wrong_availability = copy.deepcopy(peer_comparison_payload())
        wrong_availability["document"]["peers"][1]["values"][2][
            "availability_status"
        ] = "unknown"
        with self.assertRaises(ValidationError):
            TijoriPeerComparisonPayload.model_validate(wrong_availability)

    def test_rejects_duplicate_peers_and_extraction_count_drift(self):
        duplicate = copy.deepcopy(peer_comparison_payload())
        duplicate["document"]["peers"][1] = copy.deepcopy(
            duplicate["document"]["peers"][0]
        )
        duplicate["document"]["peers"][1]["display_order"] = 1
        duplicate["document"]["peers"][1]["is_subject"] = False
        with self.assertRaises(ValidationError):
            TijoriPeerComparisonPayload.model_validate(duplicate)

        wrong_count = copy.deepcopy(peer_comparison_payload())
        wrong_count["document"]["extraction"]["cell_count"] = 5
        with self.assertRaises(ValidationError):
            TijoriPeerComparisonPayload.model_validate(wrong_count)


if __name__ == "__main__":
    unittest.main()
