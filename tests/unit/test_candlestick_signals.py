import unittest
from datetime import UTC, datetime, timedelta

from app.analytics.candlestick_signals import (
    generate_candlestick_pattern_signal,
)
from app.analytics.indicators import (
    CANDLESTICK_PATTERN_NAMES,
    calculate_candlestick_patterns,
)
from app.models.market import Candle, HistoricalCandleSeries
from app.models.signals import (
    SignalCategory,
    SignalDirection,
    SignalProvenance,
    SignalStrength,
)
from app.models.technical import (
    IndicatorBundle,
    IndicatorComponent,
    IndicatorPoint,
    PriceField,
)


class CandlestickPatternSignalTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 1, tzinfo=UTC)
        self.timestamps = [
            self.first_timestamp + timedelta(days=day)
            for day in range(3)
        ]

    def component(self, name, values, timestamps=None):
        return IndicatorComponent(
            name=name,
            points=[
                IndicatorPoint(timestamp=timestamp, value=value)
                for timestamp, value in zip(
                    timestamps or self.timestamps[: len(values)],
                    values,
                )
            ],
        )

    def build_bundle(
        self,
        overrides=None,
        *,
        point_count=1,
        **bundle_overrides,
    ):
        overrides = overrides or {}
        timestamps = self.timestamps[:point_count]
        components = [
            self.component(
                name,
                overrides.get(name, [0.0] * point_count),
                timestamps,
            )
            for name, _, _ in CANDLESTICK_PATTERN_NAMES
        ]
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "indicator": "CDL_PATTERNS",
            "input_fields": (
                PriceField.OPEN,
                PriceField.HIGH,
                PriceField.LOW,
                PriceField.CLOSE,
            ),
            "parameters": {"penetration": 0.3},
            "components": components,
        }
        values.update(bundle_overrides)
        return IndicatorBundle(**values)

    def signal_for(self, overrides=None, *, point_count=1, **options):
        return generate_candlestick_pattern_signal(
            self.build_bundle(overrides, point_count=point_count),
            **options,
        )

    def test_no_pattern_fired_is_neutral_and_weak(self):
        evidence = self.signal_for()

        self.assertEqual(evidence.category, SignalCategory.CANDLESTICK)
        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.provenance,
            SignalProvenance.DETERMINISTIC,
        )
        self.assertEqual(
            evidence.evidence_id,
            "candlestick_pattern.talib_v1",
        )
        self.assertEqual(
            evidence.observed_values["bullish_patterns"],
            "none",
        )
        self.assertEqual(
            evidence.observed_values["bearish_patterns"],
            "none",
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "shows no directional pattern",
        )

    def test_fixed_bullish_pattern_fires_regardless_of_raw_sign(self):
        # TA-Lib emits an unsigned 100 for mono-directional patterns;
        # the fixed classification must still resolve the direction.
        evidence = self.signal_for({"hammer": [100.0]})

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["bullish_patterns"],
            "hammer",
        )

    def test_fixed_bearish_pattern_fires_from_a_positive_raw_value(self):
        evidence = self.signal_for({"hanging_man": [100.0]})

        self.assertEqual(evidence.direction, SignalDirection.BEARISH)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["bearish_patterns"],
            "hanging_man",
        )

    def test_bidirectional_pattern_uses_the_signed_output(self):
        bullish = self.signal_for({"engulfing": [100.0]})
        bearish = self.signal_for({"engulfing": [-100.0]})

        self.assertEqual(bullish.direction, SignalDirection.BULLISH)
        self.assertEqual(bearish.direction, SignalDirection.BEARISH)
        self.assertEqual(bullish.strength, SignalStrength.MODERATE)

    def test_neutral_pattern_alone_stays_neutral_and_is_recorded(self):
        evidence = self.signal_for({"doji": [100.0]})

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(
            evidence.observed_values["neutral_patterns"],
            "doji",
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "shows no directional pattern",
        )
        self.assertIn("Indecision context: doji", evidence.explanation)

    def test_agreeing_patterns_bump_strength_toward_confluence(self):
        evidence = self.signal_for(
            {
                "hammer": [100.0],
                "dragonfly_doji": [100.0],
            }
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.MODERATE)
        self.assertEqual(
            set(evidence.observed_values["bullish_patterns"].split(",")),
            {"hammer", "dragonfly_doji"},
        )
        self.assertEqual(
            evidence.observed_values["condition"],
            "confirms bullish confluence",
        )

    def test_confluence_strength_bump_is_capped_at_strong(self):
        evidence = self.signal_for(
            {
                "morning_star": [100.0],
                "hammer": [100.0],
            }
        )

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.strength, SignalStrength.STRONG)

    def test_conflicting_patterns_are_neutral_and_weak(self):
        evidence = self.signal_for(
            {
                "hammer": [100.0],
                "hanging_man": [100.0],
            }
        )

        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)
        self.assertEqual(evidence.strength, SignalStrength.WEAK)
        self.assertEqual(
            evidence.observed_values["condition"],
            "shows conflicting bullish and bearish patterns",
        )

    def test_parameters_propagate_penetration_and_pattern_count(self):
        evidence = self.signal_for()
        overridden = generate_candlestick_pattern_signal(
            self.build_bundle(parameters={"penetration": 0.5})
        )

        self.assertEqual(evidence.parameters["penetration"], 0.3)
        self.assertEqual(evidence.parameters["pattern_count"], 61)
        self.assertEqual(overridden.parameters["penetration"], 0.5)

    def test_uses_latest_available_component_values(self):
        evidence = self.signal_for(
            {"hammer": [0.0, 100.0, 0.0]},
            point_count=3,
        )

        self.assertEqual(evidence.observed_at, self.timestamps[-1])
        self.assertEqual(evidence.direction, SignalDirection.NEUTRAL)

    def test_as_of_excludes_future_points(self):
        evidence = self.signal_for(
            {"hammer": [0.0, 100.0, 0.0]},
            point_count=3,
            as_of=self.timestamps[1],
        )

        self.assertEqual(evidence.observed_at, self.timestamps[1])
        self.assertEqual(evidence.direction, SignalDirection.BULLISH)

    def test_rejects_as_of_before_first_component_point(self):
        bundle = self.build_bundle()

        with self.assertRaisesRegex(
            ValueError,
            "requires an available indicator point",
        ):
            generate_candlestick_pattern_signal(
                bundle,
                as_of=self.first_timestamp - timedelta(seconds=1),
            )

    def test_rejects_invalid_as_of_time(self):
        bundle = self.build_bundle()

        for as_of in (datetime(2026, 8, 1), "2026-08-01", 1):
            with self.subTest(as_of=as_of):
                with self.assertRaises(ValueError):
                    generate_candlestick_pattern_signal(bundle, as_of=as_of)

    def test_rejects_non_candlestick_indicator(self):
        bundle = self.build_bundle(indicator="STOCH")

        with self.assertRaisesRegex(
            ValueError,
            "requires a CDL_PATTERNS indicator bundle",
        ):
            generate_candlestick_pattern_signal(bundle)

    def test_rejects_missing_component(self):
        bundle = self.build_bundle()
        incomplete = bundle.model_copy(
            update={"components": bundle.components[:-1]}
        )

        with self.assertRaisesRegex(
            ValueError,
            "complete set of supported TA-Lib pattern components",
        ):
            generate_candlestick_pattern_signal(incomplete)

    def test_rejects_extra_component(self):
        bundle = self.build_bundle()
        extra = bundle.model_copy(
            update={
                "components": bundle.components
                + [self.component("not_a_pattern", [0.0])]
            }
        )

        with self.assertRaisesRegex(
            ValueError,
            "complete set of supported TA-Lib pattern components",
        ):
            generate_candlestick_pattern_signal(extra)

    def test_detects_a_real_bullish_engulfing_candle(self):
        candles = [
            Candle(
                timestamp=self.first_timestamp + timedelta(days=index),
                open=100.0,
                high=101.0,
                low=99.0,
                close=100.0,
                volume=1_000 + index,
            )
            for index in range(18)
        ]
        candles.append(
            Candle(
                timestamp=self.first_timestamp + timedelta(days=18),
                open=100.0,
                high=100.2,
                low=97.0,
                close=97.5,
                volume=2_000,
            )
        )
        candles.append(
            Candle(
                timestamp=self.first_timestamp + timedelta(days=19),
                open=97.0,
                high=102.0,
                low=96.5,
                close=101.5,
                volume=2_500,
            )
        )
        market_series = HistoricalCandleSeries(
            exchange="NSE",
            symbol_token="2885",
            symbol="RELIANCE-EQ",
            interval="ONE_DAY",
            candles=candles,
            retrieved_at=candles[-1].timestamp,
        )
        bundle = calculate_candlestick_patterns(market_series)

        evidence = generate_candlestick_pattern_signal(bundle)

        self.assertEqual(evidence.direction, SignalDirection.BULLISH)
        self.assertEqual(evidence.observed_at, candles[-1].timestamp)
        self.assertIn(
            "engulfing",
            evidence.observed_values["bullish_patterns"].split(","),
        )


if __name__ == "__main__":
    unittest.main()
