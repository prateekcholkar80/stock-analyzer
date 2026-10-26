import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory

from app.fundamentals.sector_profile import (
    NseFundamentalSectorEvidenceResolver,
)
from app.instruments.nse_sector_master import (
    NseSectorMasterCatalog,
    NseSectorMasterConfig,
)
from tests.unit.test_fundamental_gateway import build_issuer


NOW = datetime(2026, 9, 13, 8, 0, tzinfo=timezone.utc)
PAYLOAD = (
    b"SYMBOL,NAME OF COMPANY,SECTOR,INDUSTRY\n"
    b"TCS,Tata Consultancy Services Limited,IT,Software\n"
)


class FailingDownloader:
    def __call__(self, *_args):
        from app.exceptions import InstrumentMasterDownloadError

        raise InstrumentMasterDownloadError("offline")


class FundamentalSectorEvidenceResolverTests(unittest.TestCase):
    def test_resolves_exact_nse_record_with_bounded_freshness(self):
        with TemporaryDirectory() as tmp:
            catalog = NseSectorMasterCatalog(
                NseSectorMasterConfig(cache_path=Path(tmp) / "nse.csv"),
                downloader=lambda *_args: PAYLOAD,
            )
            resolver = NseFundamentalSectorEvidenceResolver(
                catalog,
                max_age=timedelta(days=30),
            )

            evidence = resolver.resolve(
                issuer=build_issuer(),
                symbol="TCS-EQ",
                as_of=NOW,
            )

        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].sector, "IT")
        self.assertEqual(evidence[0].industry, "Software")
        self.assertEqual(
            evidence[0].expires_at - evidence[0].retrieved_at,
            timedelta(days=30),
        )
        self.assertEqual(
            evidence[0].source_reference_id,
            "nse-sector-master:TCS",
        )

    def test_missing_source_degrades_to_empty_evidence(self):
        with TemporaryDirectory() as tmp:
            resolver = NseFundamentalSectorEvidenceResolver(
                NseSectorMasterCatalog(
                    NseSectorMasterConfig(cache_path=Path(tmp) / "nse.csv"),
                    downloader=FailingDownloader(),
                )
            )

            evidence = resolver.resolve(
                issuer=build_issuer(),
                symbol="TCS-EQ",
                as_of=NOW,
            )

        self.assertEqual(evidence, ())

    def test_rejects_cross_issuer_symbol(self):
        with TemporaryDirectory() as tmp:
            resolver = NseFundamentalSectorEvidenceResolver(
                NseSectorMasterCatalog(
                    NseSectorMasterConfig(cache_path=Path(tmp) / "nse.csv"),
                    downloader=lambda *_args: PAYLOAD,
                )
            )

            with self.assertRaisesRegex(ValueError, "exactly match"):
                resolver.resolve(
                    issuer=build_issuer(),
                    symbol="INFY-EQ",
                    as_of=NOW,
                )

    def test_rejects_non_positive_freshness_window(self):
        with TemporaryDirectory() as tmp:
            catalog = NseSectorMasterCatalog(
                NseSectorMasterConfig(cache_path=Path(tmp) / "nse.csv"),
                downloader=lambda *_args: PAYLOAD,
            )

            with self.assertRaisesRegex(ValueError, "must be positive"):
                NseFundamentalSectorEvidenceResolver(
                    catalog,
                    max_age=timedelta(0),
                )


if __name__ == "__main__":
    unittest.main()
