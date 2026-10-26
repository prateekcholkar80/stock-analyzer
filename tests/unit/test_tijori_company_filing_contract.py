import copy
import json
import unittest
from hashlib import sha256

from pydantic import ValidationError

from app.fundamentals.tijori_mcp_contracts import (
    TIJORI_APPROVED_TOOLS,
    TijoriCompanyFilingDownloadPayload,
    TijoriCompanyFilingsPayload,
    TijoriMcpAdapterSettings,
    TijoriMcpToolResult,
    TijoriToolStatus,
)


DISCOVERED_AT = "2026-09-14T10:00:00+05:30"
SOURCE_URL = (
    "https://www.tijorifinance.com/company/niit-technologies-limited/"
)
EARNINGS_URL = (
    "https://files.tijorifinance.com/insight/india/242/"
    "Earnings%20Release/ER-Jan26.pdf"
)
CALL_URL = (
    "https://files.tijorifinance.com/insight/india/242/"
    "Conference%20Call/CC-Jan26.pdf"
)
DISCOVERY_FINGERPRINT = "a" * 64
CONTENT_HASH = "b" * 64


def reference(category="earnings_release", artifact_url=EARNINGS_URL):
    provider_labels = {
        "earnings_release": (
            "Earnings Release",
            "company_authored_earnings_release",
            "ER-Jan26.pdf",
        ),
        "conference_call": (
            "Conference Call",
            "earnings_call_transcript",
            "CC-Jan26.pdf",
        ),
    }
    title, source_class, filename = provider_labels[category]
    digest = sha256(f"{category}\n{artifact_url}".encode()).hexdigest()
    return {
        "filing_reference_id": f"filing.coforge.{digest[:32]}",
        "category": category,
        "provider_company_id": "242",
        "provider_slug": "niit-technologies-limited",
        "source_page_url": SOURCE_URL,
        "artifact_url": artifact_url,
        "discovery_fingerprint": DISCOVERY_FINGERPRINT,
        "source_class": source_class,
        "period": {
            "state": "unclassified",
            "period_type": "unclassified",
            "source_label": "Jan 2026",
            "fiscal_year": None,
            "fiscal_quarter": None,
            "period_start": None,
            "period_end": None,
        },
        "title": f"{title} — Jan 2026",
        "provider_label": "Jan",
        "provider_year": 2026,
        "provider_month": 1,
        "original_filename": filename,
        "provider_published_at": None,
        "available_at": None,
        "discovered_at": DISCOVERED_AT,
    }


def discovery_payload():
    return {
        "schema_version": "tijori.company_filings.v1",
        "issuer": {
            "exchange": "NSE",
            "symbol": "COFORGE",
            "legal_name": "Coforge Ltd.",
            "isin": "INE591G01017",
            "provider_company_id": "242",
            "provider_slug": "niit-technologies-limited",
        },
        "source": {
            "provider": "tijori",
            "location": SOURCE_URL,
            "discovered_at": DISCOVERED_AT,
        },
        "requested_filters": {
            "categories": ["earnings_release", "conference_call"],
            "from_fiscal_year": 2017,
            "through_date": "2026-09-14",
            "max_documents": 100,
        },
        "discovery_fingerprint": DISCOVERY_FINGERPRINT,
        "filings": [
            reference(),
            reference("conference_call", CALL_URL),
        ],
        "coverage": [
            {
                "category": "earnings_release",
                "provider_year": 2026,
                "status": "available",
                "document_count": 1,
                "unavailable_labels": [],
            },
            {
                "category": "conference_call",
                "provider_year": 2026,
                "status": "available",
                "document_count": 1,
                "unavailable_labels": [],
            },
            {
                "category": "conference_call",
                "provider_year": 2025,
                "status": "unavailable",
                "document_count": 0,
                "unavailable_labels": ["Mar"],
            },
        ],
        "unavailable_period_labels": ["conference_call:Mar 2025"],
        "validation_status": "validated",
        "limitations": [
            "Fiscal year and quarter remain unclassified until validated."
        ],
        "extraction": {
            "filing_count": 2,
            "available_candidate_count": 2,
            "coverage_cell_count": 3,
            "truncated": False,
            "status": "complete",
        },
    }


def download_payload():
    filing = reference()
    return {
        "schema_version": "tijori.company_filing_download.v1",
        "filing_reference_id": filing["filing_reference_id"],
        "category": filing["category"],
        "provider_company_id": filing["provider_company_id"],
        "provider_slug": filing["provider_slug"],
        "source_page_url": filing["source_page_url"],
        "artifact_url": filing["artifact_url"],
        "discovery_fingerprint": filing["discovery_fingerprint"],
        "content_sha256": CONTENT_HASH,
        "byte_length": 4096,
        "content_type": "application/pdf",
        "staged_artifact_key": f"{CONTENT_HASH}.pdf",
        "downloaded_at": DISCOVERED_AT,
        "validation_status": "validated",
    }


class TijoriCompanyFilingsPayloadTests(unittest.TestCase):
    def test_accepts_complete_bounded_discovery_snapshot(self):
        result = TijoriCompanyFilingsPayload.model_validate_json(
            json.dumps(discovery_payload())
        )

        self.assertEqual(result.issuer.symbol, "COFORGE")
        self.assertEqual(len(result.filings), 2)
        self.assertEqual(result.filings[0].period.state, "unclassified")
        self.assertTrue(result.model_config["frozen"])

    def test_rejects_identity_fingerprint_url_and_count_drift(self):
        candidates = []

        fingerprint_drift = discovery_payload()
        fingerprint_drift["filings"][0]["discovery_fingerprint"] = "c" * 64
        candidates.append(fingerprint_drift)

        issuer_drift = discovery_payload()
        issuer_drift["filings"][0]["provider_company_id"] = "999"
        candidates.append(issuer_drift)

        unsafe_source = discovery_payload()
        unsafe_source["source"]["location"] = f"{SOURCE_URL}?token=secret"
        candidates.append(unsafe_source)

        wrong_count = discovery_payload()
        wrong_count["extraction"]["filing_count"] = 1
        candidates.append(wrong_count)

        coverage_drift = discovery_payload()
        coverage_drift["unavailable_period_labels"] = []
        candidates.append(coverage_drift)

        duplicate = discovery_payload()
        duplicate["filings"][1] = copy.deepcopy(duplicate["filings"][0])
        candidates.append(duplicate)

        for candidate in candidates:
            with self.subTest(candidate=candidates.index(candidate)):
                with self.assertRaises(ValidationError):
                    TijoriCompanyFilingsPayload.model_validate_json(
                        json.dumps(candidate)
                    )

    def test_rejects_secret_path_extra_fields_and_unbounded_filters(self):
        for field, value in (
            ("cookie", "provider-secret"),
            ("session_token", "provider-secret"),
            ("staging_path", "/private/staging/file.pdf"),
            ("safe_relative_path", "tenant/file.pdf"),
        ):
            candidate = discovery_payload()
            candidate[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    TijoriCompanyFilingsPayload.model_validate_json(
                        json.dumps(candidate)
                    )

        oversized = discovery_payload()
        oversized["requested_filters"]["max_documents"] = 201
        with self.assertRaises(ValidationError):
            TijoriCompanyFilingsPayload.model_validate_json(
                json.dumps(oversized)
            )


class TijoriCompanyFilingDownloadPayloadTests(unittest.TestCase):
    def test_accepts_validated_content_addressed_staging_manifest(self):
        result = TijoriCompanyFilingDownloadPayload.model_validate_json(
            json.dumps(download_payload())
        )

        self.assertEqual(result.content_sha256, CONTENT_HASH)
        self.assertEqual(result.staged_artifact_key, f"{CONTENT_HASH}.pdf")
        self.assertNotIn("path", result.model_dump_json())

    def test_rejects_hash_identity_source_and_timestamp_mismatches(self):
        candidates = []

        wrong_key = download_payload()
        wrong_key["staged_artifact_key"] = f"{'c' * 64}.pdf"
        candidates.append(wrong_key)

        wrong_issuer = download_payload()
        wrong_issuer["provider_company_id"] = "999"
        candidates.append(wrong_issuer)

        wrong_category = download_payload()
        wrong_category["category"] = "conference_call"
        candidates.append(wrong_category)

        unsafe_url = download_payload()
        unsafe_url["artifact_url"] = f"{EARNINGS_URL}?token=secret"
        candidates.append(unsafe_url)

        naive_time = download_payload()
        naive_time["downloaded_at"] = "2026-09-14T10:00:00"
        candidates.append(naive_time)

        for candidate in candidates:
            with self.subTest(candidate=candidates.index(candidate)):
                with self.assertRaises(ValidationError):
                    TijoriCompanyFilingDownloadPayload.model_validate_json(
                        json.dumps(candidate)
                    )

    def test_rejects_paths_secrets_invalid_content_and_oversize(self):
        for field, value in (
            ("absolute_path", "/private/staging/file.pdf"),
            ("file_path", "/private/staging/file.pdf"),
            ("cookie", "provider-secret"),
        ):
            candidate = download_payload()
            candidate[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    TijoriCompanyFilingDownloadPayload.model_validate_json(
                        json.dumps(candidate)
                    )

        for field, value in (
            ("content_type", "text/html"),
            ("byte_length", 100_000_001),
            ("validation_status", "pending"),
        ):
            candidate = download_payload()
            candidate[field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValidationError):
                    TijoriCompanyFilingDownloadPayload.model_validate_json(
                        json.dumps(candidate)
                    )


class TijoriFilingToolBoundaryTests(unittest.TestCase):
    def test_registers_only_the_two_new_approved_filing_tools(self):
        self.assertEqual(TIJORI_APPROVED_TOOLS[-2:], (
            "list_company_filings",
            "download_company_filing",
        ))
        settings = TijoriMcpAdapterSettings()
        self.assertEqual(settings.approved_tools, TIJORI_APPROVED_TOOLS)

        for tool_name, payload in (
            ("list_company_filings", discovery_payload()),
            ("download_company_filing", download_payload()),
        ):
            result = TijoriMcpToolResult(
                tool_name=tool_name,
                status=TijoriToolStatus.SUCCESS,
                payload=payload,
            )
            self.assertEqual(result.tool_name, tool_name)


if __name__ == "__main__":
    unittest.main()
