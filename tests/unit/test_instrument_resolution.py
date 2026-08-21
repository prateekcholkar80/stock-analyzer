import unittest
from datetime import UTC, datetime

from pydantic import ValidationError

from app.exceptions import (
    AmbiguousInstrumentError,
    InstrumentNotFoundError,
    IntentRecognitionError,
)
from app.instruments.in_memory import InMemoryInstrumentResolver
from app.intents.swing_analysis import PatternSwingIntentInterpreter
from app.models.instruments import ResolvedInstrument
from app.models.interaction import SwingAnalysisIntent
from app.use_cases.resolve_swing_analysis_request import (
    ResolveSwingAnalysisRequest,
)


def _reliance(*, exchange="NSE"):
    return ResolvedInstrument(
        exchange=exchange,
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        display_name="Reliance Industries Limited",
        aliases=("Reliance", "RIL"),
    )


def _tcs():
    return ResolvedInstrument(
        exchange="NSE",
        symbol_token="11536",
        symbol="TCS-EQ",
        display_name="Tata Consultancy Services Limited",
        aliases=("Tata Consultancy Services",),
    )


class PatternSwingIntentInterpreterTests(unittest.TestCase):
    def setUp(self):
        self.interpreter = PatternSwingIntentInterpreter()

    def test_interprets_reference_request_without_answering_it(self):
        intent = self.interpreter.interpret(
            "How is Reliance looking for a swing trade?"
        )

        self.assertEqual(intent.instrument_query, "Reliance")
        self.assertEqual(intent.exchange, "NSE")
        self.assertEqual(intent.interval, "ONE_HOUR")
        self.assertNotIn("buy", intent.model_dump_json().lower())

    def test_accepts_wake_prefix_contraction_and_explicit_analysis_forms(self):
        scenarios = (
            ("Hey Jarvis, how's Reliance doing today?", "Reliance"),
            ("Analyze TCS for a swing trade", "TCS"),
            ("Give me swing analysis for Infosys", "Infosys"),
        )

        for text, expected in scenarios:
            with self.subTest(text=text):
                self.assertEqual(
                    self.interpreter.interpret(text).instrument_query,
                    expected,
                )

    def test_rejects_blank_unsupported_or_multi_instrument_requests(self):
        for text in (
            "",
            "Tell me a joke",
            "Analyze Reliance versus TCS for a swing trade",
        ):
            with self.subTest(text=text):
                with self.assertRaises(IntentRecognitionError):
                    self.interpreter.interpret(text)

    def test_does_not_treat_and_inside_company_name_as_multiple_requests(self):
        intent = self.interpreter.interpret(
            "Analyze Procter and Gamble for a swing trade"
        )

        self.assertEqual(intent.instrument_query, "Procter and Gamble")

    def test_validates_default_configuration(self):
        with self.assertRaises(ValueError):
            PatternSwingIntentInterpreter(default_exchange=" ")
        with self.assertRaises(ValueError):
            PatternSwingIntentInterpreter(default_interval=" ")


class InMemoryInstrumentResolverTests(unittest.TestCase):
    def test_resolves_alias_symbol_suffixless_symbol_and_token(self):
        resolver = InMemoryInstrumentResolver((_reliance(), _tcs()))

        for query in ("reliance", "RIL", "RELIANCE-EQ", "2885"):
            with self.subTest(query=query):
                self.assertEqual(
                    resolver.resolve(query, exchange="nse"),
                    _reliance(),
                )

    def test_normalizes_spaces_and_punctuation_for_exact_matching(self):
        resolver = InMemoryInstrumentResolver((_tcs(),))

        result = resolver.resolve("Tata Consultancy-Services")

        self.assertEqual(result.symbol, "TCS-EQ")

    def test_does_not_use_fuzzy_or_partial_matching(self):
        resolver = InMemoryInstrumentResolver((_reliance(),))

        with self.assertRaises(InstrumentNotFoundError):
            resolver.resolve("Reli")

    def test_exchange_scope_disambiguates_instruments(self):
        resolver = InMemoryInstrumentResolver(
            (
                _reliance(exchange="NSE"),
                ResolvedInstrument(
                    exchange="BSE",
                    symbol_token="500325",
                    symbol="RELIANCE",
                    display_name="Reliance Industries Limited",
                ),
            )
        )

        with self.assertRaises(AmbiguousInstrumentError):
            resolver.resolve("Reliance")
        self.assertEqual(
            resolver.resolve("Reliance", exchange="NSE").exchange,
            "NSE",
        )

    def test_rejects_missing_and_invalid_catalogs_or_queries(self):
        with self.assertRaises(ValueError):
            InMemoryInstrumentResolver(())
        with self.assertRaises(ValueError):
            InMemoryInstrumentResolver("not-a-catalog")
        with self.assertRaises(ValueError):
            InMemoryInstrumentResolver((object(),))
        with self.assertRaises(ValueError):
            InMemoryInstrumentResolver((_reliance(), _reliance()))

        resolver = InMemoryInstrumentResolver((_reliance(),))
        with self.assertRaises(ValueError):
            resolver.resolve(" ")
        with self.assertRaises(InstrumentNotFoundError):
            resolver.resolve("TCS")

    def test_instrument_model_rejects_duplicate_or_primary_aliases(self):
        with self.assertRaises(ValidationError):
            ResolvedInstrument(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                display_name="Reliance Industries Limited",
                aliases=("RIL", "ril"),
            )
        with self.assertRaises(ValidationError):
            ResolvedInstrument(
                exchange="NSE",
                symbol_token="2885",
                symbol="RELIANCE-EQ",
                display_name="Reliance Industries Limited",
                aliases=("RELIANCE-EQ",),
            )


class ResolveSwingAnalysisRequestTests(unittest.TestCase):
    def test_converts_request_to_resolved_command(self):
        use_case = ResolveSwingAnalysisRequest(
            PatternSwingIntentInterpreter(),
            InMemoryInstrumentResolver((_reliance(), _tcs())),
        )
        as_of = datetime(2026, 8, 21, 15, 30, tzinfo=UTC)

        command = use_case.execute(
            "How is Reliance looking for a swing trade?",
            to_date=as_of,
        )

        self.assertEqual(command.exchange, "NSE")
        self.assertEqual(command.symbol_token, "2885")
        self.assertEqual(command.symbol, "RELIANCE-EQ")
        self.assertEqual(command.interval, "ONE_HOUR")
        self.assertEqual(command.to_date, as_of)

    def test_is_not_hardcoded_to_one_company(self):
        use_case = ResolveSwingAnalysisRequest(
            PatternSwingIntentInterpreter(),
            InMemoryInstrumentResolver((_reliance(), _tcs())),
        )

        command = use_case.execute("Analyze TCS for a swing trade")

        self.assertEqual(command.symbol_token, "11536")
        self.assertEqual(command.symbol, "TCS-EQ")

    def test_rejects_invalid_dependencies_and_adapter_results(self):
        with self.assertRaises(ValueError):
            ResolveSwingAnalysisRequest(object(), object())

        class InvalidInterpreter:
            def interpret(self, text):
                return object()

        class InvalidResolver:
            def resolve(self, query, *, exchange=None):
                return object()

        class ValidInterpreter:
            def interpret(self, text):
                return SwingAnalysisIntent(
                    original_text=text,
                    instrument_query="Reliance",
                )

        with self.assertRaisesRegex(ValueError, "invalid result"):
            ResolveSwingAnalysisRequest(
                InvalidInterpreter(),
                InMemoryInstrumentResolver((_reliance(),)),
            ).execute("request")
        with self.assertRaisesRegex(ValueError, "invalid result"):
            ResolveSwingAnalysisRequest(
                ValidInterpreter(),
                InvalidResolver(),
            ).execute("request")

    def test_rejects_cross_exchange_resolver_result(self):
        class WrongExchangeResolver:
            def resolve(self, query, *, exchange=None):
                return _reliance(exchange="BSE")

        with self.assertRaisesRegex(ValueError, "does not match"):
            ResolveSwingAnalysisRequest(
                PatternSwingIntentInterpreter(),
                WrongExchangeResolver(),
            ).execute("Analyze Reliance for a swing trade")


if __name__ == "__main__":
    unittest.main()
