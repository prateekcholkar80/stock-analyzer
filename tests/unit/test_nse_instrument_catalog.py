import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.instruments.amfi_market_cap import AmfiMarketCapEntry
from app.instruments.catalog import (
    NseInstrumentCatalog,
    build_nse_instrument_catalog,
)
from app.instruments.classification import MarketCapClass
from app.instruments.nse_sector_master import SectorIndustry
from app.models.instruments import ResolvedInstrument


def _reliance():
    return ResolvedInstrument(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        display_name="Reliance Industries Limited",
    )


def _tcs():
    return ResolvedInstrument(
        exchange="NSE",
        symbol_token="11536",
        symbol="TCS-EQ",
        display_name="Tata Consultancy Services Limited",
    )


def _unclassified():
    return ResolvedInstrument(
        exchange="NSE",
        symbol_token="1",
        symbol="OBSCURE-EQ",
        display_name="Obscure Small Company Limited",
    )


def _infy_bare_symbol_display_name():
    # Mirrors real Angel identity data, where display_name is often just
    # the bare trading symbol, not a full company name.
    return ResolvedInstrument(
        exchange="NSE",
        symbol_token="1594",
        symbol="INFY-EQ",
        display_name="INFY",
    )


class BuildNseInstrumentCatalogTests(unittest.TestCase):
    def setUp(self):
        self.instruments = (_reliance(), _tcs(), _unclassified())
        self.market_cap_entries = (
            AmfiMarketCapEntry(
                "Reliance Industries Limited",
                "RELIANCE",
                MarketCapClass.LARGE_CAP,
            ),
            AmfiMarketCapEntry(
                "Tata Consultancy Services Limited",
                "TCS",
                MarketCapClass.LARGE_CAP,
            ),
        )
        self.sector_industry_by_symbol = {
            "RELIANCE": SectorIndustry(sector="Energy", industry="Oil & Gas"),
            "TCS": SectorIndustry(sector="IT", industry="Software"),
        }
        self.as_of = datetime(2026, 8, 26, tzinfo=UTC)

    def test_composes_identity_cap_class_and_sector(self):
        catalog = build_nse_instrument_catalog(
            self.instruments,
            market_cap_entries=self.market_cap_entries,
            sector_industry_by_symbol=self.sector_industry_by_symbol,
            as_of=self.as_of,
        )

        by_symbol = {
            item.instrument.symbol: item for item in catalog.all_instruments()
        }
        self.assertEqual(
            by_symbol["RELIANCE-EQ"].market_cap_class,
            MarketCapClass.LARGE_CAP,
        )
        self.assertEqual(by_symbol["RELIANCE-EQ"].sector, "Energy")
        self.assertEqual(by_symbol["RELIANCE-EQ"].industry, "Oil & Gas")

    def test_enriches_bare_symbol_display_names_with_the_matched_company_name(
        self,
    ):
        # A natural-language query like "Infosys" can only ever match
        # Angel's raw identity (display_name="INFY") if the AMFI company
        # name is retained as a searchable alias.
        catalog = build_nse_instrument_catalog(
            (_infy_bare_symbol_display_name(),),
            market_cap_entries=(
                AmfiMarketCapEntry(
                    "Infosys Limited",
                    "INFY",
                    MarketCapClass.LARGE_CAP,
                ),
            ),
            sector_industry_by_symbol={},
            as_of=self.as_of,
        )

        instrument = catalog.all_instruments()[0].instrument
        self.assertEqual(instrument.display_name, "INFY")
        self.assertIn("Infosys Limited", instrument.aliases)

    def test_does_not_duplicate_an_alias_matching_an_existing_identity(self):
        # RELIANCE-EQ's display_name already equals its AMFI company
        # name -- adding it again as an alias would violate
        # ResolvedInstrument's own no-redundant-alias invariant.
        catalog = build_nse_instrument_catalog(
            self.instruments,
            market_cap_entries=self.market_cap_entries,
            sector_industry_by_symbol=self.sector_industry_by_symbol,
            as_of=self.as_of,
        )

        by_symbol = {
            item.instrument.symbol: item for item in catalog.all_instruments()
        }
        self.assertEqual(by_symbol["RELIANCE-EQ"].instrument.aliases, ())

    def test_prefers_nse_symbol_join_over_fuzzy_name_matching(self):
        # A deliberately mismatched company name that would fail fuzzy
        # matching, but a correct NSE Symbol -- confirms the symbol join
        # is tried first, not the fallback.
        entries = (
            AmfiMarketCapEntry(
                "RIL (Group Consolidated Filing Name)",
                "RELIANCE",
                MarketCapClass.LARGE_CAP,
            ),
        )

        catalog = build_nse_instrument_catalog(
            self.instruments,
            market_cap_entries=entries,
            sector_industry_by_symbol={},
            as_of=self.as_of,
        )

        by_symbol = {
            item.instrument.symbol: item for item in catalog.all_instruments()
        }
        self.assertEqual(
            by_symbol["RELIANCE-EQ"].market_cap_class,
            MarketCapClass.LARGE_CAP,
        )
        self.assertEqual(catalog.market_cap_match_report.matched_count, 1)

    def test_falls_back_to_name_matching_when_no_nse_symbol(self):
        entries = (
            AmfiMarketCapEntry(
                "Reliance Industries Limited",
                None,
                MarketCapClass.LARGE_CAP,
            ),
        )

        catalog = build_nse_instrument_catalog(
            self.instruments,
            market_cap_entries=entries,
            sector_industry_by_symbol={},
            as_of=self.as_of,
        )

        by_symbol = {
            item.instrument.symbol: item for item in catalog.all_instruments()
        }
        self.assertEqual(
            by_symbol["RELIANCE-EQ"].market_cap_class,
            MarketCapClass.LARGE_CAP,
        )

    def test_every_instrument_appears_even_without_classification(self):
        catalog = build_nse_instrument_catalog(
            self.instruments,
            market_cap_entries=self.market_cap_entries,
            sector_industry_by_symbol=self.sector_industry_by_symbol,
            as_of=self.as_of,
        )

        by_symbol = {
            item.instrument.symbol: item for item in catalog.all_instruments()
        }
        self.assertIn("OBSCURE-EQ", by_symbol)
        self.assertEqual(
            by_symbol["OBSCURE-EQ"].market_cap_class,
            MarketCapClass.NONE,
        )
        self.assertIsNone(by_symbol["OBSCURE-EQ"].sector)
        self.assertIsNone(by_symbol["OBSCURE-EQ"].industry)

    def test_records_provenance_and_match_report(self):
        catalog = build_nse_instrument_catalog(
            self.instruments,
            market_cap_entries=(
                *self.market_cap_entries,
                AmfiMarketCapEntry(
                    "Nonexistent Corp", None, MarketCapClass.MID_CAP
                ),
            ),
            sector_industry_by_symbol=self.sector_industry_by_symbol,
            as_of=self.as_of,
        )

        self.assertEqual(catalog.as_of, self.as_of)
        self.assertEqual(catalog.market_cap_match_report.matched_count, 2)
        self.assertEqual(
            len(catalog.market_cap_match_report.unmatched),
            1,
        )

    def test_filter_by_market_cap_class(self):
        catalog = build_nse_instrument_catalog(
            self.instruments,
            market_cap_entries=self.market_cap_entries,
            sector_industry_by_symbol=self.sector_industry_by_symbol,
            as_of=self.as_of,
        )

        large_cap = catalog.filter(market_cap_class=MarketCapClass.LARGE_CAP)

        self.assertEqual(len(large_cap), 2)
        self.assertTrue(
            all(
                item.market_cap_class == MarketCapClass.LARGE_CAP
                for item in large_cap
            )
        )

    def test_filter_by_sector_is_case_insensitive(self):
        catalog = build_nse_instrument_catalog(
            self.instruments,
            market_cap_entries=self.market_cap_entries,
            sector_industry_by_symbol=self.sector_industry_by_symbol,
            as_of=self.as_of,
        )

        energy = catalog.filter(sector="energy")

        self.assertEqual(len(energy), 1)
        self.assertEqual(energy[0].instrument.symbol, "RELIANCE-EQ")

    def test_to_records_produces_flat_serializable_rows(self):
        catalog = build_nse_instrument_catalog(
            self.instruments,
            market_cap_entries=self.market_cap_entries,
            sector_industry_by_symbol=self.sector_industry_by_symbol,
            as_of=self.as_of,
        )

        records = catalog.to_records()

        self.assertEqual(len(records), 3)
        reliance_record = next(
            record for record in records if record["symbol"] == "RELIANCE-EQ"
        )
        self.assertEqual(
            reliance_record,
            {
                "symbol": "RELIANCE-EQ",
                "exchange": "NSE",
                "symbol_token": "2885",
                "display_name": "Reliance Industries Limited",
                "market_cap_class": "LARGE_CAP",
                "sector": "Energy",
                "industry": "Oil & Gas",
                "as_of": self.as_of.isoformat(),
            },
        )


class NseInstrumentCatalogModelTests(unittest.TestCase):
    def test_rejects_naive_as_of_timestamp(self):
        catalog = build_nse_instrument_catalog(
            (_reliance(),),
            market_cap_entries=(),
            sector_industry_by_symbol={},
            as_of=datetime(2026, 8, 26, tzinfo=UTC),
        )
        payload = catalog.model_dump()
        payload["as_of"] = datetime(2026, 8, 26)

        with self.assertRaises(ValidationError):
            NseInstrumentCatalog.model_validate(payload)

    def test_rejects_duplicate_symbols(self):
        payload = build_nse_instrument_catalog(
            (_reliance(),),
            market_cap_entries=(),
            sector_industry_by_symbol={},
            as_of=datetime(2026, 8, 26, tzinfo=UTC),
        ).model_dump()
        payload["instruments"] = (
            payload["instruments"][0],
            payload["instruments"][0],
        )

        with self.assertRaises(ValidationError):
            NseInstrumentCatalog.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
