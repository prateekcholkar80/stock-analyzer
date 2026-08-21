import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import requests
from pydantic import ValidationError

from app.exceptions import (
    InstrumentMasterDataError,
    InstrumentMasterDownloadError,
    InstrumentNotFoundError,
)
from app.instruments.angel_master import (
    DEFAULT_ANGEL_INSTRUMENT_MASTER_URL,
    AngelInstrumentMasterConfig,
    AngelInstrumentMasterResolver,
    _download_instrument_master,
)


def _payload(*rows):
    return json.dumps(rows).encode("utf-8")


def _equity(
    *,
    token="2885",
    symbol="RELIANCE-EQ",
    name="RELIANCE",
    exchange="NSE",
    instrument_type="",
):
    return {
        "token": token,
        "symbol": symbol,
        "name": name,
        "expiry": "",
        "strike": "-1.000000",
        "lotsize": "1",
        "instrumenttype": instrument_type,
        "exch_seg": exchange,
        "tick_size": "5.000000",
    }


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


class FakeHTTPResponse:
    def __init__(self, chunks=(), *, content_length=None, failure=None):
        self._chunks = chunks
        self._failure = failure
        self.headers = {}
        if content_length is not None:
            self.headers["Content-Length"] = content_length

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def raise_for_status(self):
        if self._failure is not None:
            raise self._failure

    def iter_content(self, chunk_size):
        self.chunk_size = chunk_size
        return iter(self._chunks)


class AngelInstrumentMasterConfigTests(unittest.TestCase):
    def test_defaults_match_official_daily_master(self):
        config = AngelInstrumentMasterConfig()

        self.assertEqual(
            config.endpoint_url,
            DEFAULT_ANGEL_INSTRUMENT_MASTER_URL,
        )
        self.assertEqual(config.exchanges, ("NSE",))
        self.assertEqual(config.cache_ttl_seconds, 86_400)

    def test_loads_optional_environment_overrides(self):
        config = AngelInstrumentMasterConfig.from_environment(
            {
                "ANGEL_INSTRUMENT_MASTER_URL": "https://example.test/master",
                "ANGEL_INSTRUMENT_CACHE_PATH": "tmp/instruments.json",
                "ANGEL_INSTRUMENT_CACHE_TTL_SECONDS": "60",
                "ANGEL_INSTRUMENT_DOWNLOAD_TIMEOUT_SECONDS": "5.5",
                "ANGEL_INSTRUMENT_MAX_PAYLOAD_BYTES": "2048",
                "ANGEL_INSTRUMENT_EXCHANGES": " nse, bse ",
            }
        )

        self.assertEqual(config.cache_path, Path("tmp/instruments.json"))
        self.assertEqual(config.cache_ttl_seconds, 60)
        self.assertEqual(config.download_timeout_seconds, 5.5)
        self.assertEqual(config.max_payload_bytes, 2048)
        self.assertEqual(config.exchanges, ("NSE", "BSE"))

    def test_rejects_insecure_or_invalid_configuration(self):
        invalid_values = (
            {"endpoint_url": "http://example.test/master"},
            {"cache_ttl_seconds": -1},
            {"download_timeout_seconds": 0.0},
            {"max_payload_bytes": 1},
            {"exchanges": ()},
            {"exchanges": ("NSE", "nse")},
        )
        for values in invalid_values:
            with self.subTest(values=values):
                with self.assertRaises(ValidationError):
                    AngelInstrumentMasterConfig(**values)


class AngelInstrumentMasterDownloaderTests(unittest.TestCase):
    def test_streams_response_with_timeout_and_size_limit(self):
        response = FakeHTTPResponse(
            (b"abc", b"", b"def"),
            content_length="6",
        )

        with patch(
            "app.instruments.angel_master.requests.get",
            return_value=response,
        ) as request:
            payload = _download_instrument_master(
                "https://example.test/master.json",
                7.0,
                10,
            )

        self.assertEqual(payload, b"abcdef")
        request.assert_called_once_with(
            "https://example.test/master.json",
            timeout=7.0,
            stream=True,
        )
        self.assertEqual(response.chunk_size, 64 * 1024)

    def test_rejects_declared_or_streamed_oversized_response(self):
        responses = (
            FakeHTTPResponse((b"small",), content_length="11"),
            FakeHTTPResponse((b"123456", b"78901")),
            FakeHTTPResponse((b"small",), content_length="invalid"),
        )

        for response in responses:
            with self.subTest(headers=response.headers):
                with patch(
                    "app.instruments.angel_master.requests.get",
                    return_value=response,
                ):
                    with self.assertRaises(InstrumentMasterDataError):
                        _download_instrument_master(
                            "https://example.test/master.json",
                            7.0,
                            10,
                        )

    def test_translates_http_failure_without_exposing_provider_message(self):
        secret = "http-provider-secret"
        response = FakeHTTPResponse(
            failure=requests.ConnectionError(secret)
        )

        with patch(
            "app.instruments.angel_master.requests.get",
            return_value=response,
        ):
            with self.assertRaises(InstrumentMasterDownloadError) as context:
                _download_instrument_master(
                    "https://example.test/master.json",
                    7.0,
                    10,
                )

        self.assertNotIn(secret, str(context.exception))


class AngelInstrumentMasterResolverTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.cache_path = Path(self.temporary_directory.name) / "cache.json"
        self.now = 2_000_000_000.0

    def _config(self, **overrides):
        values = {
            "endpoint_url": "https://example.test/master.json",
            "cache_path": self.cache_path,
            "cache_ttl_seconds": 60,
            "download_timeout_seconds": 7.0,
            "max_payload_bytes": 10_000,
            "exchanges": ("NSE",),
        }
        values.update(overrides)
        return AngelInstrumentMasterConfig(**values)

    def _write_cache(self, payload, *, age_seconds):
        self.cache_path.write_bytes(payload)
        modified = self.now - age_seconds
        os.utime(self.cache_path, (modified, modified))

    def test_fresh_valid_cache_avoids_download(self):
        self._write_cache(_payload(_equity()), age_seconds=30)
        downloader = RecordingDownloader(failure=AssertionError("no call"))
        resolver = AngelInstrumentMasterResolver(
            self._config(),
            downloader=downloader,
            clock=lambda: self.now,
        )

        instrument = resolver.resolve("Reliance", exchange="NSE")

        self.assertEqual(instrument.symbol_token, "2885")
        self.assertEqual(downloader.calls, [])

    def test_expired_cache_downloads_validates_and_replaces_atomically(self):
        self._write_cache(
            _payload(_equity(token="old-token")),
            age_seconds=61,
        )
        downloaded = _payload(_equity(token="new-token"))
        downloader = RecordingDownloader(payload=downloaded)
        resolver = AngelInstrumentMasterResolver(
            self._config(),
            downloader=downloader,
            clock=lambda: self.now,
        )

        instrument = resolver.resolve("Reliance")

        self.assertEqual(instrument.symbol_token, "new-token")
        self.assertEqual(
            downloader.calls,
            [("https://example.test/master.json", 7.0, 10_000)],
        )
        self.assertEqual(self.cache_path.read_bytes(), downloaded)
        self.assertEqual(list(self.cache_path.parent.glob("*.tmp")), [])

    def test_transient_download_failure_uses_valid_stale_cache(self):
        stale = _payload(_equity(token="stale-token"))
        self._write_cache(stale, age_seconds=61)
        resolver = AngelInstrumentMasterResolver(
            self._config(),
            downloader=RecordingDownloader(
                failure=InstrumentMasterDownloadError("offline")
            ),
            clock=lambda: self.now,
        )

        instrument = resolver.resolve("Reliance")

        self.assertEqual(instrument.symbol_token, "stale-token")

    def test_download_failure_without_cache_remains_typed_and_safe(self):
        secret = "network-library-secret"
        resolver = AngelInstrumentMasterResolver(
            self._config(),
            downloader=RecordingDownloader(failure=RuntimeError(secret)),
            clock=lambda: self.now,
        )

        with self.assertRaises(InstrumentMasterDownloadError) as context:
            resolver.resolve("Reliance")

        self.assertIsInstance(context.exception.__cause__, RuntimeError)
        self.assertNotIn(secret, str(context.exception))

    def test_invalid_download_never_falls_back_to_stale_cache(self):
        self._write_cache(_payload(_equity()), age_seconds=61)
        resolver = AngelInstrumentMasterResolver(
            self._config(),
            downloader=RecordingDownloader(payload=b"not-json"),
            clock=lambda: self.now,
        )

        with self.assertRaises(InstrumentMasterDataError):
            resolver.resolve("Reliance")

    def test_invalid_fresh_cache_is_replaced_by_valid_download(self):
        self._write_cache(b"not-json", age_seconds=1)
        downloaded = _payload(_equity())
        resolver = AngelInstrumentMasterResolver(
            self._config(),
            downloader=RecordingDownloader(payload=downloaded),
            clock=lambda: self.now,
        )

        instrument = resolver.resolve("Reliance")

        self.assertEqual(instrument.symbol, "RELIANCE-EQ")
        self.assertEqual(self.cache_path.read_bytes(), downloaded)

    def test_filters_derivatives_other_exchanges_and_non_equity_nse_rows(self):
        payload = _payload(
            _equity(),
            _equity(
                token="future",
                symbol="RELIANCE30AUG26FUT",
                exchange="NFO",
                instrument_type="FUTSTK",
            ),
            _equity(
                token="index",
                symbol="Nifty 50",
                name="NIFTY",
                instrument_type="AMXIDX",
            ),
            _equity(
                token="be-series",
                symbol="ILLIQUID-BE",
                name="ILLIQUID",
            ),
        )
        resolver = AngelInstrumentMasterResolver(
            self._config(),
            downloader=RecordingDownloader(payload=payload),
            clock=lambda: self.now,
        )

        self.assertEqual(resolver.refresh(), 1)
        self.assertEqual(resolver.resolve("Reliance").symbol, "RELIANCE-EQ")
        with self.assertRaises(InstrumentNotFoundError):
            resolver.resolve("Nifty")

    def test_supports_explicit_bse_cash_catalog(self):
        bse = _equity(
            token="500325",
            symbol="RELIANCE",
            name="RELIANCE",
            exchange="BSE",
        )
        resolver = AngelInstrumentMasterResolver(
            self._config(exchanges=("BSE",)),
            downloader=RecordingDownloader(payload=_payload(bse)),
            clock=lambda: self.now,
        )

        self.assertEqual(
            resolver.resolve("Reliance", exchange="BSE").symbol_token,
            "500325",
        )

    def test_rejects_malformed_empty_oversized_and_duplicate_payloads(self):
        scenarios = (
            b"",
            b"{}",
            b"[]",
            _payload(_equity(), _equity()),
            _payload({"exch_seg": "NSE", "symbol": "BROKEN-EQ"}),
        )
        for payload in scenarios:
            with self.subTest(payload=payload[:30]):
                resolver = AngelInstrumentMasterResolver(
                    self._config(),
                    downloader=RecordingDownloader(payload=payload),
                    clock=lambda: self.now,
                )
                with self.assertRaises(InstrumentMasterDataError):
                    resolver.resolve("Reliance")

        resolver = AngelInstrumentMasterResolver(
            self._config(max_payload_bytes=1_024),
            downloader=RecordingDownloader(payload=b"x" * 1_025),
        )
        with self.assertRaises(InstrumentMasterDataError):
            resolver.resolve("Reliance")

    def test_refresh_forces_download_after_in_memory_catalog_exists(self):
        downloader = RecordingDownloader(payload=_payload(_equity()))
        resolver = AngelInstrumentMasterResolver(
            self._config(),
            downloader=downloader,
            clock=lambda: self.now,
        )
        resolver.resolve("Reliance")
        downloader.payload = _payload(_equity(token="updated-token"))

        count = resolver.refresh()

        self.assertEqual(count, 1)
        self.assertEqual(resolver.resolve("Reliance").symbol_token, "updated-token")
        self.assertEqual(len(downloader.calls), 2)

    def test_rejects_invalid_dependencies(self):
        with self.assertRaises(ValueError):
            AngelInstrumentMasterResolver(config="invalid")
        with self.assertRaises(ValueError):
            AngelInstrumentMasterResolver(downloader="invalid")
        with self.assertRaises(ValueError):
            AngelInstrumentMasterResolver(clock=None)


if __name__ == "__main__":
    unittest.main()
