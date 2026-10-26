import unittest
from datetime import datetime, timedelta, timezone
from hashlib import sha256

from pydantic import ValidationError

from app.analytics.fundamental_checks import FinancialHealthSectorProfile
from app.analytics.fundamental_sector_profile import (
    FundamentalSectorClassificationStatus,
    FundamentalSectorEvidence,
    FundamentalSectorSourceAuthority,
    FundamentalSectorTaxonomy,
    classify_fundamental_sector_profile,
)
from tests.unit.test_fundamental_gateway import build_issuer


NOW = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)
MANIFEST = "a" * 64


def sector_evidence(**overrides) -> FundamentalSectorEvidence:
    values = {
        "issuer": build_issuer(),
        "taxonomy": FundamentalSectorTaxonomy.NSE_SECTOR_INDUSTRY,
        "source_authority": (
            FundamentalSectorSourceAuthority.EXCHANGE_OFFICIAL
        ),
        "source_reference_id": "nse-sector-master:TCS",
        "source_name": "NSE equity sector master",
        "source_location": (
            "https://www.nseindia.com/market-data/"
            "securities-available-for-trading"
        ),
        "source_record_fingerprint": sha256(b"TCS|IT|Software").hexdigest(),
        "sector": "IT",
        "industry": "Software",
        "retrieved_at": NOW - timedelta(days=1),
        "expires_at": NOW + timedelta(days=29),
    }
    values.update(overrides)
    return FundamentalSectorEvidence(**values)


def classify(*evidence: FundamentalSectorEvidence):
    return classify_fundamental_sector_profile(
        package_id=f"fundamental_package:{MANIFEST}",
        manifest_fingerprint=MANIFEST,
        issuer=build_issuer(),
        package_expires_at=NOW + timedelta(days=10),
        evidence=tuple(evidence),
        classified_at=NOW,
    )


class FundamentalSectorEvidenceContractTests(unittest.TestCase):
    def test_requires_at_least_one_classification_label(self):
        with self.assertRaises(ValidationError):
            sector_evidence(sector=None, industry=None)

    def test_requires_expiry_after_retrieval(self):
        with self.assertRaises(ValidationError):
            sector_evidence(expires_at=NOW - timedelta(days=2))

    def test_rejects_taxonomy_authority_mismatch(self):
        with self.assertRaises(ValidationError):
            sector_evidence(
                source_authority=(
                    FundamentalSectorSourceAuthority.PROVIDER_STANDARDIZED
                )
            )


class FundamentalSectorProfileClassifierTests(unittest.TestCase):
    def test_classifies_reviewed_non_financial_sector_with_citation(self):
        result = classify(sector_evidence(sector="Information Technology"))

        self.assertEqual(
            result.profile,
            FinancialHealthSectorProfile.NON_FINANCIAL,
        )
        self.assertEqual(
            result.status,
            FundamentalSectorClassificationStatus.CLASSIFIED,
        )
        self.assertEqual(
            result.matches[0].citation_key,
            result.evidence[0].citation_key,
        )
        self.assertEqual(len(result.classification_fingerprint), 64)

    def test_classifies_reviewed_bfsi_sector(self):
        result = classify(
            sector_evidence(
                sector="Financial Services",
                industry="Private Sector Bank",
            )
        )

        self.assertEqual(result.profile, FinancialHealthSectorProfile.BFSI)
        self.assertEqual(len(result.matches), 2)

    def test_industry_can_fail_safe_to_bfsi_when_sector_is_unsupported(self):
        result = classify(
            sector_evidence(
                sector="Specialised Services",
                industry="Housing Finance Company",
            )
        )

        self.assertEqual(result.profile, FinancialHealthSectorProfile.BFSI)

    def test_unknown_label_is_not_guessed_from_a_finance_substring(self):
        result = classify(
            sector_evidence(
                sector="Finance Software",
                industry="Enterprise Software",
            )
        )

        self.assertEqual(result.profile, FinancialHealthSectorProfile.UNKNOWN)
        self.assertEqual(
            result.status,
            FundamentalSectorClassificationStatus.INSUFFICIENT_EVIDENCE,
        )
        self.assertEqual(result.reasons, ("unsupported_taxonomy_label",))

    def test_conflicting_current_sources_fail_closed(self):
        non_financial = sector_evidence()
        bfsi = sector_evidence(
            source_reference_id="provider-sector:TCS",
            source_record_fingerprint=sha256(b"TCS|Financial Services").hexdigest(),
            source_authority=(
                FundamentalSectorSourceAuthority.PROVIDER_STANDARDIZED
            ),
            taxonomy=FundamentalSectorTaxonomy.PROVIDER_SECTOR_INDUSTRY,
            sector="Financial Services",
            industry=None,
        )

        result = classify(bfsi, non_financial)

        self.assertEqual(result.profile, FinancialHealthSectorProfile.UNKNOWN)
        self.assertEqual(
            result.status,
            FundamentalSectorClassificationStatus.CONFLICTING_EVIDENCE,
        )

    def test_expired_evidence_is_not_used(self):
        result = classify(
            sector_evidence(
                retrieved_at=NOW - timedelta(days=40),
                expires_at=NOW - timedelta(days=10),
            )
        )

        self.assertEqual(result.profile, FinancialHealthSectorProfile.UNKNOWN)
        self.assertEqual(len(result.evidence), 1)
        self.assertEqual(result.reasons, ("current_sector_evidence_missing",))

    def test_cross_issuer_evidence_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must match package issuer"):
            classify(
                sector_evidence(
                    issuer=build_issuer(symbol="INFY", legal_name="Infosys Ltd"),
                )
            )

    def test_order_is_canonical_and_fingerprint_is_reproducible(self):
        first = sector_evidence()
        second = sector_evidence(
            source_reference_id="provider-sector:TCS",
            source_record_fingerprint=sha256(b"TCS|IT").hexdigest(),
            source_authority=(
                FundamentalSectorSourceAuthority.PROVIDER_STANDARDIZED
            ),
            taxonomy=FundamentalSectorTaxonomy.PROVIDER_SECTOR_INDUSTRY,
        )

        left = classify(second, first)
        right = classify(first, second)

        self.assertEqual(left, right)
        self.assertEqual(
            left.classification_fingerprint,
            right.classification_fingerprint,
        )


if __name__ == "__main__":
    unittest.main()
