import unittest
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from app.models.market import Candle, HistoricalCandleSeries
from app.services.research_archive import ResearchArchiveService
from app.storage.adapters.in_memory import InMemoryJarvisStorage
from app.use_cases.pull_rolling_market_series import (
    PullRollingMarketSeries,
    RollingFetchConfig,
)


IST = ZoneInfo("Asia/Kolkata")


class FakeGateway:
    """Returns one synthetic candle per requested window, at its start."""

    def __init__(self):
        self.calls = []
        self._next_close = 100.0

    def get_historical_series(
        self,
        *,
        exchange,
        symbol_token,
        symbol,
        interval,
        from_date,
        to_date,
        retrieved_at=None,
    ):
        self.calls.append((from_date, to_date))
        start = datetime.strptime(from_date, "%Y-%m-%d %H:%M").replace(
            tzinfo=IST
        )
        close = self._next_close
        self._next_close += 1
        candle = Candle(
            timestamp=start,
            open=close - 1,
            high=close + 1,
            low=close - 1,
            close=close,
            volume=100,
        )
        return HistoricalCandleSeries(
            exchange=exchange,
            symbol_token=symbol_token,
            symbol=symbol,
            interval=interval,
            candles=[candle],
            retrieved_at=datetime.now(UTC),
        )


class FakeSleep:
    def __init__(self):
        self.calls = []

    def __call__(self, seconds):
        self.calls.append(seconds)


class PullRollingMarketSeriesTests(unittest.TestCase):
    def _new_archive(self):
        return ResearchArchiveService(InMemoryJarvisStorage())

    def test_rejects_archive_that_does_not_implement_the_port(self):
        with self.assertRaises(ValueError):
            PullRollingMarketSeries(FakeGateway(), archive=object())

    def test_fresh_pull_uses_default_lookback_and_chunks_requests(self):
        gateway = FakeGateway()
        sleep_fn = FakeSleep()
        config = RollingFetchConfig(
            max_days_per_chunk=10,
            inter_request_delay_seconds=2.0,
            default_lookback_days=25,
        )
        use_case = PullRollingMarketSeries(
            gateway,
            self._new_archive(),
            config=config,
            sleep_fn=sleep_fn,
        )
        to_date = datetime(2026, 1, 26, tzinfo=IST)

        receipt = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=to_date,
        )

        self.assertEqual(receipt.chunk_request_count, 3)
        self.assertEqual(len(gateway.calls), 3)
        self.assertEqual(sleep_fn.calls, [2.0, 2.0])
        self.assertIsNone(receipt.resumed_from)
        self.assertEqual(receipt.new_candle_count, 3)
        self.assertEqual(len(receipt.stored.series.candles), 3)

    def test_resumes_from_previously_stored_last_candle(self):
        gateway = FakeGateway()
        config = RollingFetchConfig(
            max_days_per_chunk=10,
            inter_request_delay_seconds=0,
            default_lookback_days=5,
        )
        use_case = PullRollingMarketSeries(
            gateway,
            self._new_archive(),
            config=config,
            sleep_fn=FakeSleep(),
        )

        first_to_date = datetime(2026, 1, 10, tzinfo=IST)
        first_receipt = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=first_to_date,
        )
        first_last_candle_at = first_receipt.stored.series.candles[
            -1
        ].timestamp

        second_to_date = datetime(2026, 1, 20, tzinfo=IST)
        second_receipt = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=second_to_date,
        )

        self.assertEqual(second_receipt.resumed_from, first_last_candle_at)
        self.assertEqual(
            len(second_receipt.stored.series.candles),
            len(first_receipt.stored.series.candles)
            + second_receipt.new_candle_count,
        )

        boundary_candles = [
            candle
            for candle in second_receipt.stored.series.candles
            if candle.timestamp == first_last_candle_at
        ]
        self.assertEqual(len(boundary_candles), 1)
        self.assertNotEqual(
            boundary_candles[0].close,
            first_receipt.stored.series.candles[-1].close,
        )

    def test_receipt_references_the_archive_adapter(self):
        gateway = FakeGateway()
        use_case = PullRollingMarketSeries(
            gateway,
            self._new_archive(),
            config=RollingFetchConfig(default_lookback_days=1),
            sleep_fn=FakeSleep(),
        )

        receipt = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=datetime(2026, 1, 2, tzinfo=IST),
        )

        self.assertEqual(receipt.adapter_name, "in_memory")
        self.assertEqual(
            receipt.use_case_id,
            "jarvis.pull_rolling_market_series.v1",
        )


if __name__ == "__main__":
    unittest.main()
