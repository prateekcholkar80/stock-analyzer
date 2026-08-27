import unittest
from datetime import UTC, datetime

from app.instruments.candidate_shortlist import shortlist_candidates
from app.instruments.catalog import build_nse_instrument_catalog
from app.models.instruments import ResolvedInstrument


def _instrument(symbol, display_name, *, aliases=()):
    return ResolvedInstrument(
        exchange="NSE",
        symbol_token=symbol,
        symbol=symbol,
        display_name=display_name,
        aliases=aliases,
    )


def _catalog(*instruments):
    return build_nse_instrument_catalog(
        instruments,
        market_cap_entries=(),
        sector_industry_by_symbol={},
        as_of=datetime(2026, 8, 26, tzinfo=UTC),
    )


# A deliberately similarly-prefixed real-shaped cluster: several genuinely
# distinct NSE-listed "Reliance ..." companies sharing the same prefix, to
# stress-test ranking rather than either over- or under-matching.
_RELIANCE_CLUSTER = (
    _instrument("RELIANCE-EQ", "Reliance Industries Limited", aliases=("RIL",)),
    _instrument("RPOWER-EQ", "Reliance Power Limited"),
    _instrument("RELINFRA-EQ", "Reliance Infrastructure Limited"),
    _instrument("RCOM-EQ", "Reliance Communications Limited"),
    _instrument("TCS-EQ", "Tata Consultancy Services Limited"),
)


class ShortlistCandidatesTests(unittest.TestCase):
    def setUp(self):
        self.catalog = _catalog(*_RELIANCE_CLUSTER)

    def test_symbol_prefix_ranks_above_display_name_matches(self):
        results = shortlist_candidates("RELI", self.catalog)

        symbols = [item.instrument.symbol for item in results]
        # RELIANCE-EQ and RELINFRA-EQ both prefix-match the symbol "RELI";
        # RCOM/RPOWER only match via display name substring ("Reliance").
        self.assertIn("RELIANCE-EQ", symbols[:2])
        self.assertIn("RELINFRA-EQ", symbols[:2])
        self.assertNotIn("TCS-EQ", symbols)

    def test_display_name_prefix_word_matches(self):
        results = shortlist_candidates("Reliance", self.catalog)

        symbols = {item.instrument.symbol for item in results}
        self.assertEqual(
            symbols,
            {"RELIANCE-EQ", "RPOWER-EQ", "RELINFRA-EQ", "RCOM-EQ"},
        )

    def test_alias_match_is_included(self):
        results = shortlist_candidates("RIL", self.catalog)

        symbols = {item.instrument.symbol for item in results}
        self.assertIn("RELIANCE-EQ", symbols)

    def test_unrelated_query_returns_empty_tuple(self):
        results = shortlist_candidates("Zzzznotarealcompany", self.catalog)

        self.assertEqual(results, ())

    def test_result_is_capped_at_limit(self):
        results = shortlist_candidates("Reliance", self.catalog, limit=2)

        self.assertEqual(len(results), 2)

    def test_never_auto_selects_even_with_exactly_one_match(self):
        results = shortlist_candidates("Tata Consultancy", self.catalog)

        self.assertIsInstance(results, tuple)
        self.assertEqual(len(results), 1)
        # Contract: always a tuple, never a bare ResolvedInstrument/
        # ClassifiedInstrument even for a single unambiguous hit -- the
        # caller must still go through confirmation.
        self.assertEqual(results[0].instrument.symbol, "TCS-EQ")

    def test_rejects_blank_query(self):
        with self.assertRaises(ValueError):
            shortlist_candidates("   ", self.catalog)

    def test_rejects_non_positive_limit(self):
        with self.assertRaises(ValueError):
            shortlist_candidates("Reliance", self.catalog, limit=0)

    def test_purely_symbolic_query_normalizes_to_empty_and_yields_no_results(
        self,
    ):
        results = shortlist_candidates("---", self.catalog)

        self.assertEqual(results, ())


if __name__ == "__main__":
    unittest.main()
