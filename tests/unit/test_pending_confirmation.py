import unittest

from pydantic import ValidationError

from app.conversation.pending_confirmation import PendingConfirmation


class PendingConfirmationTests(unittest.TestCase):
    def test_ticker_guess_preserves_fundamental_routing_flags(self):
        pending = PendingConfirmation(
            kind="ticker_guess",
            original_command=(
                "Refresh fundamentals and analyze Torrent Pharma for me"
            ),
            chosen_symbol="TORNTPHARM-EQ",
            exchange="NSE",
            fundamentals_requested=True,
            refresh_requested=True,
        )

        self.assertTrue(pending.fundamentals_requested)
        self.assertTrue(pending.refresh_requested)

    def test_technical_confirmation_defaults_to_no_fundamental_work(self):
        pending = PendingConfirmation(
            kind="ticker_guess",
            original_command="Analyze Torrent Pharma for me",
            chosen_symbol="TORNTPHARM-EQ",
            exchange="NSE",
        )

        self.assertFalse(pending.fundamentals_requested)
        self.assertFalse(pending.refresh_requested)

    def test_refresh_without_fundamental_research_is_rejected(self):
        with self.assertRaisesRegex(ValidationError, "requires fundamental"):
            PendingConfirmation(
                kind="catalog_refresh",
                original_command="Analyze Torrent Pharma for me",
                refresh_requested=True,
            )


if __name__ == "__main__":
    unittest.main()
