from datetime import timedelta
import unittest

from pydantic import ValidationError

from app.models.market import Candle


class CandleModelTests(unittest.TestCase):
    def test_builds_candle_from_valid_market_data(self):
        candle = Candle(
            timestamp="2026-08-17T00:00:00+05:30",
            open=1314.0,
            high=1320.8,
            low=1298.1,
            close=1320.0,
            volume=13_090_231,
        )

        self.assertEqual(candle.timestamp.utcoffset(), timedelta(hours=5, minutes=30))
        self.assertEqual(candle.close, 1320.0)
        self.assertEqual(candle.volume, 13_090_231)

    def test_rejects_missing_required_market_data(self):
        with self.assertRaises(ValidationError):
            Candle(
                timestamp="2026-08-17T00:00:00+05:30",
                open=1314.0,
                high=1320.8,
                low=1298.1,
                close=1320.0,
            )


if __name__ == "__main__":
    unittest.main()
