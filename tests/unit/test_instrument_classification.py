import unittest

from pydantic import ValidationError

from app.instruments.classification import (
    ClassifiedInstrument,
    MarketCapClass,
    build_market_cap_match_report,
    match_company_name_to_symbol,
)
from app.models.instruments import ResolvedInstrument


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


class ClassifiedInstrumentTests(unittest.TestCase):
    def test_defaults_to_none_classification_without_sector_or_industry(self):
        classified = ClassifiedInstrument(instrument=_reliance())

        self.assertEqual(classified.market_cap_class, MarketCapClass.NONE)
        self.assertIsNone(classified.sector)
        self.assertIsNone(classified.industry)

    def test_accepts_full_classification(self):
        classified = ClassifiedInstrument(
            instrument=_reliance(),
            market_cap_class=MarketCapClass.LARGE_CAP,
            sector="Energy",
            industry="Oil & Gas",
        )

        self.assertEqual(classified.market_cap_class, MarketCapClass.LARGE_CAP)
        self.assertEqual(classified.sector, "Energy")
        self.assertEqual(classified.industry, "Oil & Gas")

    def test_blank_sector_normalizes_to_none(self):
        classified = ClassifiedInstrument(
            instrument=_reliance(),
            sector="   ",
        )

        self.assertIsNone(classified.sector)

    def test_rejects_unknown_market_cap_value(self):
        with self.assertRaises(ValidationError):
            ClassifiedInstrument(
                instrument=_reliance(),
                market_cap_class="MEGA_CAP",
            )


class MatchCompanyNameToSymbolTests(unittest.TestCase):
    def setUp(self):
        self.candidates = (_reliance(), _relaxo())

    def test_matches_exact_normalized_company_name(self):
        symbol = match_company_name_to_symbol(
            "Reliance Industries Ltd.",
            self.candidates,
        )

        self.assertEqual(symbol, "RELIANCE-EQ")

    def test_does_not_confuse_similarly_named_companies(self):
        symbol = match_company_name_to_symbol(
            "Relaxo Footwears Limited",
            self.candidates,
        )

        self.assertEqual(symbol, "RELAXO-EQ")

    def test_returns_none_for_no_match(self):
        symbol = match_company_name_to_symbol(
            "Completely Unrelated Company",
            self.candidates,
        )

        self.assertIsNone(symbol)

    def test_rejects_blank_company_name(self):
        with self.assertRaises(ValueError):
            match_company_name_to_symbol("   ", self.candidates)


class BuildMarketCapMatchReportTests(unittest.TestCase):
    def test_resolves_matches_and_reports_gaps(self):
        candidates = (_reliance(), _relaxo())
        market_cap_by_company = {
            "Reliance Industries Ltd.": MarketCapClass.LARGE_CAP,
            "Relaxo Footwears Limited": MarketCapClass.SMALL_CAP,
            "Nonexistent Corp": MarketCapClass.MID_CAP,
        }

        by_symbol, report = build_market_cap_match_report(
            market_cap_by_company,
            candidates,
        )

        self.assertEqual(
            by_symbol,
            {
                "RELIANCE-EQ": MarketCapClass.LARGE_CAP,
                "RELAXO-EQ": MarketCapClass.SMALL_CAP,
            },
        )
        self.assertEqual(report.matched_count, 2)
        self.assertEqual(len(report.unmatched), 1)
        self.assertEqual(report.unmatched[0].company_name, "Nonexistent Corp")
        self.assertEqual(
            report.unmatched[0].market_cap_class,
            MarketCapClass.MID_CAP,
        )

    def test_empty_input_produces_empty_report(self):
        by_symbol, report = build_market_cap_match_report({}, (_reliance(),))

        self.assertEqual(by_symbol, {})
        self.assertEqual(report.matched_count, 0)
        self.assertEqual(report.unmatched, ())


if __name__ == "__main__":
    unittest.main()
