import unittest

from app.intents.fundamentals import FundamentalRefreshIntentInterpreter
from app.intents.swing_analysis import PatternSwingIntentInterpreter


class FundamentalRefreshIntentInterpreterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.interpreter = FundamentalRefreshIntentInterpreter()

    def test_ordinary_company_analysis_does_not_request_refresh(self):
        directive = self.interpreter.interpret(
            "Analyze Torrent Pharma for me"
        )

        self.assertFalse(directive.fundamentals_requested)
        self.assertFalse(directive.refresh_requested)
        self.assertEqual(
            directive.research_text,
            "Analyze Torrent Pharma for me",
        )

    def test_recognizes_only_explicit_supported_refresh_phrases(self):
        phrases = (
            "I need refresh on fundamentals",
            "I need a refresh of the fundamentals",
            "Please refresh the fundamentals",
            "Refresh fundamentals data",
            "Update fundamental data",
            "Please update the fundamentals",
            "Re-pull company fundamentals",
            "Please repull the fundamentals",
        )
        for phrase in phrases:
            with self.subTest(phrase=phrase):
                directive = self.interpreter.interpret(phrase)
                self.assertTrue(directive.fundamentals_requested)
                self.assertTrue(directive.refresh_requested)
                self.assertIsNone(directive.research_text)

    def test_removes_refresh_clause_before_existing_swing_parser(self):
        requests = (
            (
                "Analyze Torrent Pharma for me, "
                "I need refresh on fundamentals",
                "Analyze Torrent Pharma for me",
            ),
            (
                "Refresh the fundamentals and analyze Torrent Pharma for me",
                "analyze Torrent Pharma for me",
            ),
            (
                "Analyze Torrent Pharma for me and update fundamental data",
                "Analyze Torrent Pharma for me",
            ),
        )
        swing = PatternSwingIntentInterpreter()
        for original, expected in requests:
            with self.subTest(original=original):
                directive = self.interpreter.interpret(original)
                self.assertTrue(directive.fundamentals_requested)
                self.assertTrue(directive.refresh_requested)
                self.assertEqual(directive.research_text, expected)
                intent = swing.interpret(directive.research_text)
                self.assertEqual(intent.instrument_query, "Torrent Pharma")

    def test_explicit_fundamental_request_uses_cache_without_forcing_refresh(self):
        requests = (
            "Analyze Torrent Pharma for me with fundamentals",
            "Analyze Torrent Pharma for me using financial statements",
            "Analyze Torrent Pharma for me and include shareholding history",
        )
        swing = PatternSwingIntentInterpreter()
        for request in requests:
            with self.subTest(request=request):
                directive = self.interpreter.interpret(request)
                self.assertTrue(directive.fundamentals_requested)
                self.assertFalse(directive.refresh_requested)
                intent = swing.interpret(directive.research_text)
                self.assertEqual(intent.instrument_query, "Torrent Pharma")

    def test_negation_and_generic_freshness_language_do_not_force_refresh(self):
        requests = (
            "Analyze Torrent Pharma without refresh fundamentals",
            "Analyze Torrent Pharma and do not refresh fundamentals",
            "No need to refresh the fundamentals",
            "Give me the latest analysis on Torrent Pharma",
            "How current are the fundamentals?",
            "Analyze Torrent Pharma today",
        )
        for request in requests:
            with self.subTest(request=request):
                directive = self.interpreter.interpret(request)
                self.assertFalse(directive.refresh_requested)
                self.assertEqual(directive.research_text, request)

        self.assertFalse(
            self.interpreter.interpret(
                "Give me the latest analysis on Torrent Pharma"
            ).fundamentals_requested
        )
        self.assertTrue(
            self.interpreter.interpret(
                "How current are the fundamentals?"
            ).fundamentals_requested
        )

    def test_rejects_blank_non_text_and_oversized_input(self):
        for value in (None, "", "   ", "a" * 2_001):
            with self.subTest(value_type=type(value).__name__):
                with self.assertRaises(ValueError):
                    self.interpreter.interpret(value)


if __name__ == "__main__":
    unittest.main()
