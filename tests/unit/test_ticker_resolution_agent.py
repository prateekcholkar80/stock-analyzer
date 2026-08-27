import unittest
from datetime import UTC, datetime

from app.agents.ticker_resolution_agent import resolve_via_llm
from app.exceptions import LLMResponseValidationError
from app.instruments.catalog import build_nse_instrument_catalog
from app.llm.gateway import StructuredGeneration
from app.models.instruments import ResolvedInstrument
from app.models.ticker_resolution import TickerResolutionChoice


class FakeGateway:
    def __init__(self, draft_payloads, model="fake-resolver-model"):
        self.draft_payloads = list(draft_payloads)
        self.calls = []
        self.model = model

    @property
    def configuration_fingerprint(self):
        return "a" * 64

    def generate(self, *, system, messages, response_model):
        self.calls.append({"system": system, "messages": messages})
        payload = self.draft_payloads.pop(0)
        return StructuredGeneration[response_model](
            value=response_model(**payload),
            provider="fake-provider",
            model=self.model,
            attempt_count=1,
        )


def _shortlist():
    instruments = (
        ResolvedInstrument(
            exchange="NSE",
            symbol_token="RELIANCE-EQ",
            symbol="RELIANCE-EQ",
            display_name="Reliance Industries Limited",
        ),
        ResolvedInstrument(
            exchange="NSE",
            symbol_token="RELAXO-EQ",
            symbol="RELAXO-EQ",
            display_name="Relaxo Footwears Limited",
        ),
    )
    catalog = build_nse_instrument_catalog(
        instruments,
        market_cap_entries=(),
        sector_industry_by_symbol={},
        as_of=datetime(2026, 8, 26, tzinfo=UTC),
    )
    return catalog.all_instruments()


class ResolveViaLlmTests(unittest.TestCase):
    def test_returns_empty_choice_without_calling_gateway_for_empty_shortlist(
        self,
    ):
        gateway = FakeGateway(draft_payloads=[])

        choice = resolve_via_llm(gateway=gateway, query="Rel", shortlist=())

        self.assertEqual(choice, TickerResolutionChoice())
        self.assertEqual(gateway.calls, [])

    def test_in_shortlist_choice_passes_through(self):
        gateway = FakeGateway(
            draft_payloads=[
                {"chosen_symbols": ("RELIANCE-EQ",), "is_ambiguous": False}
            ]
        )

        choice = resolve_via_llm(
            gateway=gateway,
            query="Reliance",
            shortlist=_shortlist(),
        )

        self.assertEqual(choice.chosen_symbols, ("RELIANCE-EQ",))
        self.assertFalse(choice.is_ambiguous)
        self.assertEqual(len(gateway.calls), 1)

    def test_ambiguous_choice_passes_through(self):
        gateway = FakeGateway(
            draft_payloads=[{"chosen_symbols": (), "is_ambiguous": True}]
        )

        choice = resolve_via_llm(
            gateway=gateway,
            query="Rel",
            shortlist=_shortlist(),
        )

        self.assertEqual(choice.chosen_symbols, ())
        self.assertTrue(choice.is_ambiguous)

    def test_out_of_shortlist_choice_triggers_retry_with_feedback(self):
        gateway = FakeGateway(
            draft_payloads=[
                {"chosen_symbols": ("NOTREAL-EQ",), "is_ambiguous": False},
                {"chosen_symbols": ("RELIANCE-EQ",), "is_ambiguous": False},
            ]
        )

        choice = resolve_via_llm(
            gateway=gateway,
            query="Reliance",
            shortlist=_shortlist(),
        )

        self.assertEqual(choice.chosen_symbols, ("RELIANCE-EQ",))
        self.assertEqual(len(gateway.calls), 2)
        self.assertIn("NOTREAL-EQ", gateway.calls[1]["messages"][0]["content"])

    def test_still_invalid_after_retry_raises(self):
        gateway = FakeGateway(
            draft_payloads=[
                {"chosen_symbols": ("NOTREAL-EQ",), "is_ambiguous": False},
                {"chosen_symbols": ("STILLNOTREAL-EQ",), "is_ambiguous": False},
            ]
        )

        with self.assertRaises(LLMResponseValidationError):
            resolve_via_llm(
                gateway=gateway,
                query="Reliance",
                shortlist=_shortlist(),
            )

    def test_rejects_blank_query(self):
        gateway = FakeGateway(draft_payloads=[])

        with self.assertRaises(ValueError):
            resolve_via_llm(gateway=gateway, query="   ", shortlist=_shortlist())


if __name__ == "__main__":
    unittest.main()
