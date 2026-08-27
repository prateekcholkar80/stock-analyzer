import io
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd
from pydantic import ValidationError

from app.exceptions import (
    InstrumentMasterDataError,
    InstrumentMasterDownloadError,
)
from app.instruments.amfi_market_cap import (
    AmfiMarketCapCatalog,
    AmfiMarketCapConfig,
    AmfiMarketCapEntry,
)
from app.instruments.classification import MarketCapClass


def _xlsx_payload(
    *rows,
    columns=("Sr No", "Company Name", "NSE Symbol", "Category"),
    title_row=True,
):
    records = [
        dict(zip(columns, (index, *row)))
        for index, row in enumerate(rows, start=1)
    ]
    frame = pd.DataFrame.from_records(records, columns=columns)
    buffer = io.BytesIO()
    with pd.ExcelWriter(buffer, engine="openpyxl") as writer:
        start_row = 1 if title_row else 0
        if title_row:
            title = pd.DataFrame({0: ["Average Market Capitalization"]})
            title.to_excel(
                writer, index=False, header=False, startrow=0, sheet_name="FINAL"
            )
        frame.to_excel(
            writer, index=False, startrow=start_row, sheet_name="FINAL"
        )
    return buffer.getvalue()


def _missing_columns_payload():
    frame = pd.DataFrame({"Sr No": [1], "Company Name": ["Reliance"]})
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


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


class AmfiMarketCapConfigTests(unittest.TestCase):
    def test_defaults_are_valid(self):
        config = AmfiMarketCapConfig()

        self.assertTrue(config.endpoint_url.startswith("https://"))

    def test_rejects_non_https_endpoint(self):
        with self.assertRaises(ValidationError):
            AmfiMarketCapConfig(endpoint_url="http://example.com/data")

    def test_from_environment_overrides_defaults(self):
        config = AmfiMarketCapConfig.from_environment(
            {
                "AMFI_MARKET_CAP_URL": "https://example.com/cap.xlsx",
                "AMFI_MARKET_CAP_DOWNLOAD_TIMEOUT_SECONDS": "45",
            }
        )

        self.assertEqual(config.endpoint_url, "https://example.com/cap.xlsx")
        self.assertEqual(config.download_timeout_seconds, 45)


class AmfiMarketCapCatalogTests(unittest.TestCase):
    def test_parses_valid_payload_with_title_row_above_header(self):
        payload = _xlsx_payload(
            ("Reliance Industries Limited", "RELIANCE", "Large Cap"),
            ("Some Mid Cap Company Ltd", "MIDCO", "Mid Cap"),
            ("A Small Cap Company Ltd", "-", "small cap"),
        )
        downloader = RecordingDownloader(payload=payload)
        with TemporaryDirectory() as tmp:
            config = AmfiMarketCapConfig(
                cache_path=Path(tmp) / "amfi_market_cap.json"
            )
            catalog = AmfiMarketCapCatalog(config, downloader=downloader)

            entries = catalog.entries()

        self.assertEqual(
            entries,
            (
                AmfiMarketCapEntry(
                    "Reliance Industries Limited",
                    "RELIANCE",
                    MarketCapClass.LARGE_CAP,
                ),
                AmfiMarketCapEntry(
                    "Some Mid Cap Company Ltd", "MIDCO", MarketCapClass.MID_CAP
                ),
                AmfiMarketCapEntry(
                    "A Small Cap Company Ltd", None, MarketCapClass.SMALL_CAP
                ),
            ),
        )
        self.assertEqual(len(downloader.calls), 1)

    def test_parses_valid_payload_without_title_row(self):
        payload = _xlsx_payload(
            ("Reliance Industries Limited", "RELIANCE", "Large Cap"),
            title_row=False,
        )
        downloader = RecordingDownloader(payload=payload)
        with TemporaryDirectory() as tmp:
            config = AmfiMarketCapConfig(
                cache_path=Path(tmp) / "amfi_market_cap.json"
            )
            catalog = AmfiMarketCapCatalog(config, downloader=downloader)

            entries = catalog.entries()

        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].nse_symbol, "RELIANCE")

    def test_missing_nse_symbol_column_still_parses(self):
        payload = _xlsx_payload(
            ("Reliance Industries Limited", "Large Cap"),
            columns=("Sr No", "Company Name", "Category"),
        )
        downloader = RecordingDownloader(payload=payload)
        with TemporaryDirectory() as tmp:
            config = AmfiMarketCapConfig(
                cache_path=Path(tmp) / "amfi_market_cap.json"
            )
            catalog = AmfiMarketCapCatalog(config, downloader=downloader)

            entries = catalog.entries()

        self.assertEqual(entries[0].nse_symbol, None)

    def test_rejects_missing_required_columns(self):
        payload = _missing_columns_payload()
        downloader = RecordingDownloader(payload=payload)
        with TemporaryDirectory() as tmp:
            config = AmfiMarketCapConfig(
                cache_path=Path(tmp) / "amfi_market_cap.json"
            )
            catalog = AmfiMarketCapCatalog(config, downloader=downloader)

            with self.assertRaises(InstrumentMasterDataError):
                catalog.entries()

    def test_rejects_unrecognized_category(self):
        payload = _xlsx_payload(("Some Company Ltd", "-", "Mega Cap"))
        downloader = RecordingDownloader(payload=payload)
        with TemporaryDirectory() as tmp:
            config = AmfiMarketCapConfig(
                cache_path=Path(tmp) / "amfi_market_cap.json"
            )
            catalog = AmfiMarketCapCatalog(config, downloader=downloader)

            with self.assertRaises(InstrumentMasterDataError):
                catalog.entries()

    def test_rejects_duplicate_company_name(self):
        payload = _xlsx_payload(
            ("Duplicate Co Ltd", "DUPCO", "Large Cap"),
            ("Duplicate Co Ltd", "DUPCO", "Mid Cap"),
        )
        downloader = RecordingDownloader(payload=payload)
        with TemporaryDirectory() as tmp:
            config = AmfiMarketCapConfig(
                cache_path=Path(tmp) / "amfi_market_cap.json"
            )
            catalog = AmfiMarketCapCatalog(config, downloader=downloader)

            with self.assertRaises(InstrumentMasterDataError):
                catalog.entries()

    def test_caches_result_and_avoids_second_download(self):
        payload = _xlsx_payload(
            ("Reliance Industries Limited", "RELIANCE", "Large Cap")
        )
        downloader = RecordingDownloader(payload=payload)
        with TemporaryDirectory() as tmp:
            config = AmfiMarketCapConfig(
                cache_path=Path(tmp) / "amfi_market_cap.json"
            )
            catalog = AmfiMarketCapCatalog(config, downloader=downloader)

            first = catalog.entries()
            second = catalog.entries()

        self.assertEqual(first, second)
        self.assertEqual(len(downloader.calls), 1)

    def test_reuses_sticky_cache_across_new_catalog_instances(self):
        payload = _xlsx_payload(
            ("Reliance Industries Limited", "RELIANCE", "Large Cap")
        )
        downloader = RecordingDownloader(payload=payload)
        with TemporaryDirectory() as tmp:
            config = AmfiMarketCapConfig(
                cache_path=Path(tmp) / "amfi_market_cap.json"
            )
            AmfiMarketCapCatalog(config, downloader=downloader).entries()

            second_downloader = RecordingDownloader(payload=payload)
            second_catalog = AmfiMarketCapCatalog(
                config, downloader=second_downloader
            )
            entries = second_catalog.entries()

        self.assertEqual(
            entries,
            (
                AmfiMarketCapEntry(
                    "Reliance Industries Limited",
                    "RELIANCE",
                    MarketCapClass.LARGE_CAP,
                ),
            ),
        )
        self.assertEqual(len(second_downloader.calls), 0)

    def test_corrupt_cache_with_failed_download_surfaces_a_typed_error(self):
        with TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "amfi_market_cap.json"
            cache_path.write_bytes(b"not-a-valid-workbook")
            config = AmfiMarketCapConfig(cache_path=cache_path)
            failing_downloader = RecordingDownloader(
                failure=InstrumentMasterDownloadError("network down")
            )
            catalog = AmfiMarketCapCatalog(config, downloader=failing_downloader)

            with self.assertRaises(InstrumentMasterDataError):
                catalog.entries()

    def test_raises_when_no_cache_and_download_fails(self):
        with TemporaryDirectory() as tmp:
            config = AmfiMarketCapConfig(
                cache_path=Path(tmp) / "amfi_market_cap.json"
            )
            failing_downloader = RecordingDownloader(
                failure=InstrumentMasterDownloadError("network down")
            )
            catalog = AmfiMarketCapCatalog(config, downloader=failing_downloader)

            with self.assertRaises(InstrumentMasterDownloadError):
                catalog.entries()

    def test_refresh_forces_download_and_replaces_sticky_cache(self):
        downloader = RecordingDownloader(
            payload=_xlsx_payload(
                ("Reliance Industries Limited", "RELIANCE", "Large Cap")
            )
        )
        with TemporaryDirectory() as tmp:
            config = AmfiMarketCapConfig(
                cache_path=Path(tmp) / "amfi_market_cap.json"
            )
            catalog = AmfiMarketCapCatalog(config, downloader=downloader)
            catalog.entries()
            downloader.payload = _xlsx_payload(
                ("Reliance Industries Limited", "RELIANCE", "Mid Cap")
            )

            count = catalog.refresh()

            self.assertEqual(count, 1)
            self.assertEqual(
                catalog.entries(),
                (
                    AmfiMarketCapEntry(
                        "Reliance Industries Limited",
                        "RELIANCE",
                        MarketCapClass.MID_CAP,
                    ),
                ),
            )
            self.assertEqual(len(downloader.calls), 2)


if __name__ == "__main__":
    unittest.main()
