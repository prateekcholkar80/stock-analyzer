import unittest
from datetime import UTC, datetime, timedelta
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


class SequenceGateway:
    """Returns prearranged candle batches for deterministic refresh tests."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

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
        return HistoricalCandleSeries(
            exchange=exchange,
            symbol_token=symbol_token,
            symbol=symbol,
            interval=interval,
            candles=self.responses.pop(0),
            retrieved_at=datetime.now(UTC),
        )


class MismatchedGateway(SequenceGateway):
    def get_historical_series(self, **kwargs):
        result = super().get_historical_series(**kwargs)
        return result.model_copy(update={"symbol": "WRONG-EQ"})


def _candle(timestamp, close=100.0):
    return Candle(
        timestamp=timestamp,
        open=close - 1,
        high=close + 1,
        low=close - 1,
        close=close,
        volume=100,
    )


class PullRollingMarketSeriesTests(unittest.TestCase):
    def _new_archive(self):
        return ResearchArchiveService(InMemoryJarvisStorage())

    def test_rejects_archive_that_does_not_implement_the_port(self):
        with self.assertRaises(ValueError):
            PullRollingMarketSeries(FakeGateway(), archive=object())

    def test_rejects_invalid_correction_overlap(self):
        for value in (-1, 31):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    RollingFetchConfig(correction_overlap_days=value)

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
        self.assertEqual(receipt.requested_to, to_date)
        self.assertEqual(receipt.new_candle_count, 3)
        self.assertEqual(len(receipt.stored.series.candles), 3)

    def test_resumes_from_previously_stored_last_candle(self):
        gateway = FakeGateway()
        config = RollingFetchConfig(
            max_days_per_chunk=10,
            inter_request_delay_seconds=0,
            default_lookback_days=5,
            correction_overlap_days=0,
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
        self.assertEqual(second_receipt.corrected_candle_count, 1)

    def test_refresh_uses_configured_correction_overlap(self):
        gateway = FakeGateway()
        use_case = PullRollingMarketSeries(
            gateway,
            self._new_archive(),
            config=RollingFetchConfig(
                max_days_per_chunk=30,
                inter_request_delay_seconds=0,
                default_lookback_days=1,
                correction_overlap_days=3,
            ),
            sleep_fn=FakeSleep(),
        )

        first = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=datetime(2026, 1, 10, tzinfo=IST),
        )
        second = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=datetime(2026, 1, 12, tzinfo=IST),
        )

        expected_start = (
            first.stored.series.candles[-1].timestamp - timedelta(days=3)
        )
        self.assertEqual(
            gateway.calls[-1][0],
            expected_start.strftime("%Y-%m-%d %H:%M"),
        )
        self.assertEqual(
            second.requested_from,
            first.stored.series.candles[-1].timestamp
            - timedelta(days=3),
        )
        self.assertEqual(
            second.requested_to,
            datetime(2026, 1, 12, tzinfo=IST),
        )

    def test_unchanged_refresh_reuses_existing_dataset(self):
        candle = _candle(datetime(2026, 1, 9, 9, 15, tzinfo=IST))
        gateway = SequenceGateway([[candle], [candle]])
        storage = InMemoryJarvisStorage()
        archive = ResearchArchiveService(storage)
        use_case = PullRollingMarketSeries(
            gateway,
            archive,
            config=RollingFetchConfig(
                max_days_per_chunk=30,
                inter_request_delay_seconds=0,
                default_lookback_days=1,
                correction_overlap_days=1,
            ),
            sleep_fn=FakeSleep(),
        )

        first = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=datetime(2026, 1, 10, tzinfo=IST),
        )
        second = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=datetime(2026, 1, 11, tzinfo=IST),
        )

        self.assertTrue(second.reused_existing_dataset)
        self.assertEqual(second.dataset_id, first.dataset_id)
        self.assertEqual(second.new_candle_count, 0)
        self.assertEqual(second.corrected_candle_count, 0)
        self.assertIsNotNone(second.checked_at)
        self.assertEqual(len(archive.list_market_series()), 1)

    def test_corrected_candle_creates_updated_dataset(self):
        timestamp = datetime(2026, 1, 9, 9, 15, tzinfo=IST)
        gateway = SequenceGateway(
            [[_candle(timestamp, 100.0)], [_candle(timestamp, 101.0)]]
        )
        archive = self._new_archive()
        use_case = PullRollingMarketSeries(
            gateway,
            archive,
            config=RollingFetchConfig(
                max_days_per_chunk=30,
                inter_request_delay_seconds=0,
                default_lookback_days=1,
                correction_overlap_days=1,
            ),
            sleep_fn=FakeSleep(),
        )

        first = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=datetime(2026, 1, 10, tzinfo=IST),
        )
        second = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=datetime(2026, 1, 11, tzinfo=IST),
        )

        self.assertFalse(second.reused_existing_dataset)
        self.assertNotEqual(second.dataset_id, first.dataset_id)
        self.assertEqual(second.new_candle_count, 0)
        self.assertEqual(second.corrected_candle_count, 1)
        self.assertEqual(second.stored.series.candles[0].close, 101.0)
        self.assertEqual(len(archive.list_market_series()), 2)

    def test_deduplicates_fetch_and_reports_definite_intraday_gap(self):
        first = _candle(datetime(2026, 1, 5, 9, 15, tzinfo=IST))
        after_gap = _candle(datetime(2026, 1, 5, 11, 15, tzinfo=IST))
        gateway = SequenceGateway([[first, first, after_gap]])
        use_case = PullRollingMarketSeries(
            gateway,
            self._new_archive(),
            config=RollingFetchConfig(
                max_days_per_chunk=30,
                inter_request_delay_seconds=0,
                default_lookback_days=1,
            ),
            sleep_fn=FakeSleep(),
        )

        receipt = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=datetime(2026, 1, 6, tzinfo=IST),
        )

        self.assertEqual(receipt.new_candle_count, 2)
        self.assertEqual(receipt.deduplicated_fetched_candle_count, 1)
        self.assertEqual(len(receipt.stored.series.candles), 2)
        self.assertEqual(len(receipt.intraday_gaps), 1)
        self.assertEqual(receipt.intraday_gaps[0].missing_candle_count, 1)

    def test_does_not_report_overnight_or_weekend_as_definite_gap(self):
        friday = _candle(datetime(2026, 1, 9, 15, 15, tzinfo=IST))
        monday = _candle(datetime(2026, 1, 12, 9, 15, tzinfo=IST))
        gateway = SequenceGateway([[friday, monday]])
        use_case = PullRollingMarketSeries(
            gateway,
            self._new_archive(),
            config=RollingFetchConfig(
                max_days_per_chunk=30,
                inter_request_delay_seconds=0,
                default_lookback_days=5,
            ),
            sleep_fn=FakeSleep(),
        )

        receipt = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=datetime(2026, 1, 13, tzinfo=IST),
        )

        self.assertEqual(receipt.intraday_gaps, ())

    def test_rejects_chunk_for_a_different_instrument(self):
        gateway = MismatchedGateway(
            [[_candle(datetime(2026, 1, 9, 9, 15, tzinfo=IST))]]
        )
        use_case = PullRollingMarketSeries(
            gateway,
            self._new_archive(),
            config=RollingFetchConfig(default_lookback_days=1),
            sleep_fn=FakeSleep(),
        )

        with self.assertRaisesRegex(
            ValueError,
            "does not match the requested instrument",
        ):
            use_case.execute(
                "NSE",
                "2885",
                "RELIANCE-EQ",
                "ONE_HOUR",
                to_date=datetime(2026, 1, 10, tzinfo=IST),
            )

    def test_resume_requires_the_same_normalized_symbol(self):
        archive = self._new_archive()
        other_symbol = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="OTHER-EQ",
            interval="ONE_HOUR",
            candles=[
                _candle(datetime(2026, 1, 9, 9, 15, tzinfo=IST))
            ],
            retrieved_at=datetime.now(UTC),
        )
        archive.archive_market_series(other_symbol)
        gateway = SequenceGateway(
            [[_candle(datetime(2026, 1, 10, 9, 15, tzinfo=IST))]]
        )
        use_case = PullRollingMarketSeries(
            gateway,
            archive,
            config=RollingFetchConfig(
                max_days_per_chunk=30,
                inter_request_delay_seconds=0,
                default_lookback_days=1,
            ),
            sleep_fn=FakeSleep(),
        )

        receipt = use_case.execute(
            "NSE",
            "2885",
            "RELIANCE-EQ",
            "ONE_HOUR",
            to_date=datetime(2026, 1, 11, tzinfo=IST),
        )

        self.assertIsNone(receipt.resumed_from)
        self.assertEqual(receipt.stored.series.symbol, "RELIANCE-EQ")

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
