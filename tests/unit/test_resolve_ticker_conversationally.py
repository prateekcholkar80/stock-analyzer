import io
import unittest

import pandas as pd

from app.exceptions import InstrumentMasterDownloadError
from app.intents.swing_analysis import PatternSwingIntentInterpreter
from app.instruments.amfi_market_cap import AmfiMarketCapCatalog, AmfiMarketCapConfig
from app.instruments.nse_sector_master import (
    NseSectorMasterCatalog,
    NseSectorMasterConfig,
)
from app.llm.gateway import StructuredGeneration
from app.models.instruments import ResolvedInstrument
from app.use_cases.resolve_ticker_conversationally import (
    ResolveTickerConversationally,
)


def _reliance():
    return ResolvedInstrument(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        display_name="Reliance Industries Limited",
    )


def _relaxo():
    return ResolvedInstrument(
        exchange="NSE",
        symbol_token="9999",
        symbol="RELAXO-EQ",
        display_name="Relaxo Footwears Limited",
    )


def _torrent_pharma():
    return ResolvedInstrument(
        exchange="NSE",
        symbol_token="3518",
        symbol="TORNTPHARM-EQ",
        display_name="TORNTPHARM",
    )


class FakeAngelIdentitySource:
    def __init__(self, instruments):
        self.instruments = tuple(instruments)
        self.refresh_calls = 0

    def list_instruments(self):
        return self.instruments

    def refresh(self) -> int:
        self.refresh_calls += 1
        return len(self.instruments)


class FakeGateway:
    def __init__(self, draft_payloads):
        self.draft_payloads = list(draft_payloads)
        self.calls = []

    @property
    def configuration_fingerprint(self):
        return "a" * 64

    def generate(self, *, system, messages, response_model):
        self.calls.append({"system": system, "messages": messages})
        payload = self.draft_payloads.pop(0)
        return StructuredGeneration[response_model](
            value=response_model(**payload),
            provider="fake-provider",
            model="fake-resolver-model",
            attempt_count=1,
        )


def _valid_amfi_payload():
    frame = pd.DataFrame(
        {
            "Sr No": [1],
            "Company Name": ["Reliance Industries Limited"],
            "NSE Symbol": ["RELIANCE"],
            "Category": ["Large Cap"],
        }
    )
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False, engine="openpyxl")
    return buffer.getvalue()


def _torrent_amfi_catalog(tmp_path):
    frame = pd.DataFrame(
        {
            "Sr No": [1],
            "Company Name": ["Torrent Pharmaceuticals Limited"],
            "NSE Symbol": ["TORNTPHARM"],
            "Category": ["Large Cap"],
        }
    )
    buffer = io.BytesIO()
    frame.to_excel(buffer, index=False, engine="openpyxl")
    payload = buffer.getvalue()
    return AmfiMarketCapCatalog(
        AmfiMarketCapConfig(cache_path=tmp_path / "torrent-amfi.json"),
        downloader=lambda *a: payload,
    )


def _valid_nse_payload():
    return (
        b"SYMBOL,NAME OF COMPANY,SECTOR,INDUSTRY\n"
        b"RELIANCE,Reliance Industries Limited,Energy,Oil & Gas\n"
    )


def _empty_amfi_catalog(tmp_path):
    config = AmfiMarketCapConfig(cache_path=tmp_path / "amfi.json")
    payload = _valid_amfi_payload()
    catalog = AmfiMarketCapCatalog(
        config,
        downloader=lambda *a: payload,
    )
    # No cap-class overlay needed for these tests; seed the cache directly
    # rather than requiring a network-shaped fixture with zero rows (the
    # real parser rejects an empty classification list).
    catalog._entries = ()
    return catalog


def _empty_nse_catalog(tmp_path):
    config = NseSectorMasterConfig(cache_path=tmp_path / "nse.json")
    payload = _valid_nse_payload()
    catalog = NseSectorMasterCatalog(
        config,
        downloader=lambda *a: payload,
    )
    catalog._mapping = {}
    return catalog


class ResolveTickerConversationallyTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.tmp_path = Path(self._tmp.name)
        self.interpreter = PatternSwingIntentInterpreter()
        self.angel_source = FakeAngelIdentitySource((_reliance(), _relaxo()))
        self.amfi_catalog = _empty_amfi_catalog(self.tmp_path)
        self.nse_catalog = _empty_nse_catalog(self.tmp_path)

    def _use_case(self, draft_payloads):
        return ResolveTickerConversationally(
            interpreter=self.interpreter,
            angel_identity_source=self.angel_source,
            amfi_catalog=self.amfi_catalog,
            nse_sector_catalog=self.nse_catalog,
            resolver_gateway=FakeGateway(draft_payloads),
        )

    def test_resolves_when_llm_chooses_a_candidate(self):
        use_case = self._use_case(
            [{"chosen_symbols": ("RELIANCE-EQ",), "is_ambiguous": False}]
        )

        result = use_case.attempt("Analyze Rel for me")

        self.assertEqual(result.outcome, "resolved_needs_confirmation")
        self.assertEqual(result.chosen_symbol, "RELIANCE-EQ")
        self.assertEqual(result.exchange, "NSE")

    def test_resolves_unique_torrent_pharma_candidate_without_an_llm_call(self):
        gateway = FakeGateway(
            [{"chosen_symbols": ("TORNTPHARM-EQ",), "is_ambiguous": False}]
        )
        use_case = ResolveTickerConversationally(
            interpreter=self.interpreter,
            angel_identity_source=FakeAngelIdentitySource((_torrent_pharma(),)),
            amfi_catalog=_torrent_amfi_catalog(self.tmp_path),
            nse_sector_catalog=self.nse_catalog,
            resolver_gateway=gateway,
        )

        result = use_case.attempt("Analyze Torrent Pharma for me")

        self.assertEqual(result.outcome, "resolved_needs_confirmation")
        self.assertEqual(result.chosen_symbol, "TORNTPHARM-EQ")
        self.assertEqual(result.exchange, "NSE")
        self.assertEqual(gateway.calls, [])

    def test_ambiguous_when_llm_cannot_decide(self):
        use_case = self._use_case(
            [{"chosen_symbols": (), "is_ambiguous": True}]
        )

        result = use_case.attempt("Analyze Rel for me")

        self.assertEqual(result.outcome, "ambiguous")
        self.assertIn(
            "Reliance Industries Limited", result.candidate_display_names
        )

    def test_not_found_when_shortlist_is_empty(self):
        use_case = self._use_case([])

        result = use_case.attempt("Analyze Zzzznotreal for me")

        self.assertEqual(result.outcome, "not_found")
        self.assertEqual(result.candidate_display_names, ())

    def test_resolves_even_when_nse_sector_download_fails(self):
        # Sector/industry is enrichment metadata, not required for
        # resolution -- a broken/unreachable NSE sector-master source must
        # degrade to no sector data, not block resolution entirely.
        def failing_downloader(*_args):
            raise InstrumentMasterDownloadError("NSE archive unreachable")

        broken_nse_catalog = NseSectorMasterCatalog(
            NseSectorMasterConfig(cache_path=self.tmp_path / "broken-nse.json"),
            downloader=failing_downloader,
        )
        use_case = ResolveTickerConversationally(
            interpreter=self.interpreter,
            angel_identity_source=self.angel_source,
            amfi_catalog=self.amfi_catalog,
            nse_sector_catalog=broken_nse_catalog,
            resolver_gateway=FakeGateway(
                [{"chosen_symbols": ("RELIANCE-EQ",), "is_ambiguous": False}]
            ),
        )

        result = use_case.attempt("Analyze Rel for me")

        self.assertEqual(result.outcome, "resolved_needs_confirmation")
        self.assertEqual(result.chosen_symbol, "RELIANCE-EQ")

    def test_refresh_catalog_forces_source_refresh_and_rebuild(self):
        use_case = self._use_case(
            [{"chosen_symbols": ("RELIANCE-EQ",), "is_ambiguous": False}]
        )
        use_case.attempt("Analyze Rel for me")  # populate the cached catalog

        count = use_case.refresh_catalog()

        self.assertEqual(count, 2)
        self.assertEqual(self.angel_source.refresh_calls, 1)

    def test_rejects_invalid_dependencies(self):
        with self.assertRaises(ValueError):
            ResolveTickerConversationally(
                interpreter="invalid",
                angel_identity_source=self.angel_source,
                amfi_catalog=self.amfi_catalog,
                nse_sector_catalog=self.nse_catalog,
                resolver_gateway=FakeGateway([]),
            )
        with self.assertRaises(ValueError):
            ResolveTickerConversationally(
                interpreter=self.interpreter,
                angel_identity_source="invalid",
                amfi_catalog=self.amfi_catalog,
                nse_sector_catalog=self.nse_catalog,
                resolver_gateway=FakeGateway([]),
            )
        with self.assertRaises(ValueError):
            ResolveTickerConversationally(
                interpreter=self.interpreter,
                angel_identity_source=self.angel_source,
                amfi_catalog=self.amfi_catalog,
                nse_sector_catalog=self.nse_catalog,
                resolver_gateway="invalid",
            )


if __name__ == "__main__":
    unittest.main()
