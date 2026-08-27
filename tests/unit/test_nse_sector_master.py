import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from pydantic import ValidationError

from app.exceptions import (
    InstrumentMasterDataError,
    InstrumentMasterDownloadError,
)
from app.instruments.nse_sector_master import (
    NseSectorMasterCatalog,
    NseSectorMasterConfig,
    SectorIndustry,
)


def _csv_payload(*rows, header="SYMBOL,NAME OF COMPANY,SECTOR,INDUSTRY"):
    lines = [header]
    for symbol, name, sector, industry in rows:
        lines.append(f"{symbol},{name},{sector},{industry}")
    return ("\n".join(lines) + "\n").encode("utf-8")


class RecordingDownloader:
    def __init__(self, payload=None, failure=None):
        self.payload = payload
        self.failure = failure
        self.calls = []

    def __call__(self, url, timeout, max_payload_bytes):
        self.calls.append((url, timeout, max_payload_bytes))
        if self.failure is not None:
            raise self.failure
        return self.payload


class NseSectorMasterConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        config = NseSectorMasterConfig()

        self.assertTrue(config.endpoint_url.startswith("https://"))

    def test_rejects_non_https_endpoint(self):
        with self.assertRaises(ValidationError):
            NseSectorMasterConfig(endpoint_url="http://example.com/data")


class NseSectorMasterCatalogTests(unittest.TestCase):
    def test_parses_valid_payload_into_mapping(self):
        payload = _csv_payload(
            ("RELIANCE", "Reliance Industries Limited", "Energy", "Oil & Gas"),
            ("TCS", "Tata Consultancy Services Limited", "IT", "Software"),
        )
        downloader = RecordingDownloader(payload=payload)
        with TemporaryDirectory() as tmp:
            config = NseSectorMasterConfig(
                cache_path=Path(tmp) / "nse_sector_master.json"
            )
            catalog = NseSectorMasterCatalog(config, downloader=downloader)

            mapping = catalog.sector_industry_by_symbol()

        self.assertEqual(
            mapping,
            {
                "RELIANCE": SectorIndustry(
                    sector="Energy", industry="Oil & Gas"
                ),
                "TCS": SectorIndustry(sector="IT", industry="Software"),
            },
        )
        self.assertEqual(len(downloader.calls), 1)

    def test_rejects_missing_required_columns(self):
        payload = b"SYMBOL,NAME OF COMPANY\nRELIANCE,Reliance Industries\n"
        downloader = RecordingDownloader(payload=payload)
        with TemporaryDirectory() as tmp:
            config = NseSectorMasterConfig(
                cache_path=Path(tmp) / "nse_sector_master.json"
            )
            catalog = NseSectorMasterCatalog(config, downloader=downloader)

            with self.assertRaises(InstrumentMasterDataError):
                catalog.sector_industry_by_symbol()

    def test_rejects_duplicate_symbol(self):
        payload = _csv_payload(
            ("RELIANCE", "Reliance Industries Limited", "Energy", "Oil & Gas"),
            ("RELIANCE", "Reliance Industries Limited", "Energy", "Refining"),
        )
        downloader = RecordingDownloader(payload=payload)
        with TemporaryDirectory() as tmp:
            config = NseSectorMasterConfig(
                cache_path=Path(tmp) / "nse_sector_master.json"
            )
            catalog = NseSectorMasterCatalog(config, downloader=downloader)

            with self.assertRaises(InstrumentMasterDataError):
                catalog.sector_industry_by_symbol()

    def test_caches_result_and_avoids_second_download(self):
        payload = _csv_payload(
            ("RELIANCE", "Reliance Industries Limited", "Energy", "Oil & Gas"),
        )
        downloader = RecordingDownloader(payload=payload)
        with TemporaryDirectory() as tmp:
            config = NseSectorMasterConfig(
                cache_path=Path(tmp) / "nse_sector_master.json"
            )
            catalog = NseSectorMasterCatalog(config, downloader=downloader)

            first = catalog.sector_industry_by_symbol()
            second = catalog.sector_industry_by_symbol()

        self.assertEqual(first, second)
        self.assertEqual(len(downloader.calls), 1)

    def test_reuses_sticky_cache_across_new_catalog_instances(self):
        payload = _csv_payload(
            ("RELIANCE", "Reliance Industries Limited", "Energy", "Oil & Gas"),
        )
        with TemporaryDirectory() as tmp:
            config = NseSectorMasterConfig(
                cache_path=Path(tmp) / "nse_sector_master.json"
            )
            NseSectorMasterCatalog(
                config, downloader=RecordingDownloader(payload=payload)
            ).sector_industry_by_symbol()

            second_downloader = RecordingDownloader(payload=payload)
            second_catalog = NseSectorMasterCatalog(
                config, downloader=second_downloader
            )
            mapping = second_catalog.sector_industry_by_symbol()

        self.assertEqual(
            mapping,
            {"RELIANCE": SectorIndustry(sector="Energy", industry="Oil & Gas")},
        )
        self.assertEqual(len(second_downloader.calls), 0)

    def test_corrupt_cache_with_failed_download_surfaces_a_typed_error(self):
        with TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "nse_sector_master.json"
            cache_path.write_bytes(b"SYMBOL,NAME OF COMPANY\nRELIANCE,Reliance\n")
            config = NseSectorMasterConfig(cache_path=cache_path)
            failing_downloader = RecordingDownloader(
                failure=InstrumentMasterDownloadError("network down")
            )
            catalog = NseSectorMasterCatalog(config, downloader=failing_downloader)

            with self.assertRaises(InstrumentMasterDataError):
                catalog.sector_industry_by_symbol()

    def test_raises_when_no_cache_and_download_fails(self):
        with TemporaryDirectory() as tmp:
            config = NseSectorMasterConfig(
                cache_path=Path(tmp) / "nse_sector_master.json"
            )
            failing_downloader = RecordingDownloader(
                failure=InstrumentMasterDownloadError("network down")
            )
            catalog = NseSectorMasterCatalog(config, downloader=failing_downloader)

            with self.assertRaises(InstrumentMasterDownloadError):
                catalog.sector_industry_by_symbol()

    def test_refresh_forces_download_and_replaces_sticky_cache(self):
        downloader = RecordingDownloader(
            payload=_csv_payload(
                ("RELIANCE", "Reliance Industries Limited", "Energy", "Oil & Gas"),
            )
        )
        with TemporaryDirectory() as tmp:
            config = NseSectorMasterConfig(
                cache_path=Path(tmp) / "nse_sector_master.json"
            )
            catalog = NseSectorMasterCatalog(config, downloader=downloader)
            catalog.sector_industry_by_symbol()
            downloader.payload = _csv_payload(
                ("RELIANCE", "Reliance Industries Limited", "Energy", "Refining"),
            )

            count = catalog.refresh()

            self.assertEqual(count, 1)
            self.assertEqual(
                catalog.sector_industry_by_symbol(),
                {"RELIANCE": SectorIndustry(sector="Energy", industry="Refining")},
            )
            self.assertEqual(len(downloader.calls), 2)


if __name__ == "__main__":
    unittest.main()
