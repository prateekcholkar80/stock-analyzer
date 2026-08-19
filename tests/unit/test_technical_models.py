import unittest
from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from app.models.technical import (
    IndicatorBundle,
    IndicatorComponent,
    IndicatorPoint,
    IndicatorSeries,
    PriceField,
)


class IndicatorPointModelTests(unittest.TestCase):
    def test_builds_valid_indicator_point(self):
        timestamp = datetime(2026, 8, 18, tzinfo=UTC)

        point = IndicatorPoint(timestamp=timestamp, value=1320.5)

        self.assertEqual(point.timestamp, timestamp)
        self.assertEqual(point.value, 1320.5)

    def test_rejects_timestamp_without_timezone(self):
        with self.assertRaisesRegex(
            ValidationError,
            "indicator timestamp must include timezone information",
        ):
            IndicatorPoint(
                timestamp=datetime(2026, 8, 18),
                value=1320.5,
            )

    def test_rejects_non_finite_value(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(
                    ValidationError,
                    "indicator value must be finite",
                ):
                    IndicatorPoint(
                        timestamp=datetime(2026, 8, 18, tzinfo=UTC),
                        value=value,
                    )


class IndicatorSeriesModelTests(unittest.TestCase):
    def setUp(self):
        self.first_timestamp = datetime(2026, 8, 18, tzinfo=UTC)
        self.points = [
            IndicatorPoint(
                timestamp=self.first_timestamp,
                value=1310.0,
            ),
            IndicatorPoint(
                timestamp=self.first_timestamp + timedelta(days=1),
                value=1312.5,
            ),
        ]

    def build_series(self, **overrides):
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "indicator": "SMA",
            "price_field": PriceField.CLOSE,
            "parameters": {"period": 20},
            "points": self.points,
        }
        values.update(overrides)

        return IndicatorSeries(**values)

    def test_builds_series_with_instrument_metadata(self):
        series = self.build_series()

        self.assertEqual(series.indicator, "SMA")
        self.assertEqual(series.price_field, PriceField.CLOSE)
        self.assertEqual(series.parameters, {"period": 20})
        self.assertEqual(series.points, self.points)

    def test_builds_series_with_multiple_input_fields(self):
        series = self.build_series(
            price_field=None,
            input_fields=(
                PriceField.HIGH,
                PriceField.LOW,
                PriceField.CLOSE,
            ),
        )

        self.assertIsNone(series.price_field)
        self.assertEqual(
            series.input_fields,
            (
                PriceField.HIGH,
                PriceField.LOW,
                PriceField.CLOSE,
            ),
        )

    def test_builds_series_with_price_and_volume_inputs(self):
        series = self.build_series(
            price_field=None,
            input_fields=(PriceField.CLOSE, PriceField.VOLUME),
        )

        self.assertEqual(
            series.input_fields,
            (PriceField.CLOSE, PriceField.VOLUME),
        )

    def test_rejects_missing_indicator_input(self):
        with self.assertRaisesRegex(
            ValidationError,
            "must define either price_field or input_fields",
        ):
            self.build_series(price_field=None)

    def test_rejects_single_and_multi_field_input_together(self):
        with self.assertRaisesRegex(
            ValidationError,
            "must define either price_field or input_fields",
        ):
            self.build_series(
                input_fields=(PriceField.HIGH, PriceField.LOW)
            )

    def test_rejects_duplicate_multi_field_input(self):
        with self.assertRaisesRegex(
            ValidationError,
            "indicator input fields must be unique",
        ):
            self.build_series(
                price_field=None,
                input_fields=(PriceField.HIGH, PriceField.HIGH),
            )

    def test_rejects_single_value_multi_field_input(self):
        with self.assertRaisesRegex(
            ValidationError,
            "multi-field indicator input requires at least two fields",
        ):
            self.build_series(
                price_field=None,
                input_fields=(PriceField.HIGH,),
            )

    def test_rejects_blank_metadata(self):
        for field_name in (
            "exchange",
            "symbol_token",
            "symbol",
            "interval",
            "indicator",
        ):
            with self.subTest(field_name=field_name):
                with self.assertRaisesRegex(
                    ValidationError,
                    "technical-analysis metadata cannot be blank",
                ):
                    self.build_series(**{field_name: "   "})

    def test_rejects_empty_point_list(self):
        with self.assertRaises(ValidationError):
            self.build_series(points=[])

    def test_rejects_duplicate_point_timestamps(self):
        duplicate_points = [self.points[0], self.points[0]]

        with self.assertRaisesRegex(
            ValidationError,
            "indicator points must have unique timestamps",
        ):
            self.build_series(points=duplicate_points)

    def test_rejects_points_out_of_order(self):
        with self.assertRaisesRegex(
            ValidationError,
            "indicator points must have unique timestamps",
        ):
            self.build_series(points=list(reversed(self.points)))

    def test_rejects_blank_parameter_name(self):
        with self.assertRaisesRegex(
            ValidationError,
            "indicator parameter name cannot be blank",
        ):
            self.build_series(parameters={" ": 20})

    def test_rejects_non_finite_numeric_parameter(self):
        with self.assertRaisesRegex(
            ValidationError,
            "numeric indicator parameters must be finite",
        ):
            self.build_series(parameters={"deviation": float("nan")})

    def test_series_is_immutable(self):
        series = self.build_series()

        with self.assertRaises(ValidationError):
            series.indicator = "EMA"


class IndicatorComponentModelTests(unittest.TestCase):
    def setUp(self):
        first_timestamp = datetime(2026, 8, 18, tzinfo=UTC)
        self.points = [
            IndicatorPoint(timestamp=first_timestamp, value=1.5),
            IndicatorPoint(
                timestamp=first_timestamp + timedelta(days=1),
                value=2.5,
            ),
        ]

    def test_builds_valid_component(self):
        component = IndicatorComponent(
            name="macd",
            points=self.points,
        )

        self.assertEqual(component.name, "macd")
        self.assertEqual(component.points, self.points)

    def test_rejects_blank_component_name(self):
        with self.assertRaisesRegex(
            ValidationError,
            "indicator component name cannot be blank",
        ):
            IndicatorComponent(name=" ", points=self.points)

    def test_rejects_empty_component_points(self):
        with self.assertRaises(ValidationError):
            IndicatorComponent(name="macd", points=[])

    def test_rejects_duplicate_component_timestamps(self):
        with self.assertRaisesRegex(
            ValidationError,
            "component points must have unique timestamps",
        ):
            IndicatorComponent(
                name="macd",
                points=[self.points[0], self.points[0]],
            )

    def test_rejects_component_points_out_of_order(self):
        with self.assertRaisesRegex(
            ValidationError,
            "component points must have unique timestamps",
        ):
            IndicatorComponent(
                name="macd",
                points=list(reversed(self.points)),
            )


class IndicatorBundleModelTests(unittest.TestCase):
    def setUp(self):
        first_timestamp = datetime(2026, 8, 18, tzinfo=UTC)
        self.timestamps = [
            first_timestamp,
            first_timestamp + timedelta(days=1),
        ]
        self.components = [
            self.build_component("macd", (1.5, 2.5)),
            self.build_component("signal", (1.0, 2.0)),
            self.build_component("histogram", (0.5, 0.5)),
        ]

    def build_component(
        self,
        name,
        values,
        timestamps=None,
    ):
        component_timestamps = timestamps or self.timestamps
        return IndicatorComponent(
            name=name,
            points=[
                IndicatorPoint(timestamp=timestamp, value=value)
                for timestamp, value in zip(
                    component_timestamps,
                    values,
                )
            ],
        )

    def build_bundle(self, **overrides):
        values = {
            "exchange": "NSE",
            "symbol_token": "2885",
            "symbol": "RELIANCE-EQ",
            "interval": "ONE_DAY",
            "indicator": "MACD",
            "price_field": PriceField.CLOSE,
            "parameters": {
                "fast_period": 12,
                "slow_period": 26,
                "signal_period": 9,
            },
            "components": self.components,
        }
        values.update(overrides)

        return IndicatorBundle(**values)

    def test_builds_bundle_with_synchronized_components(self):
        bundle = self.build_bundle()

        self.assertEqual(bundle.indicator, "MACD")
        self.assertEqual(bundle.price_field, PriceField.CLOSE)
        self.assertEqual(bundle.components, self.components)

    def test_builds_bundle_with_multiple_input_fields(self):
        bundle = self.build_bundle(
            price_field=None,
            input_fields=(PriceField.HIGH, PriceField.LOW),
        )

        self.assertIsNone(bundle.price_field)
        self.assertEqual(
            bundle.input_fields,
            (PriceField.HIGH, PriceField.LOW),
        )

    def test_rejects_empty_component_list(self):
        with self.assertRaises(ValidationError):
            self.build_bundle(components=[])

    def test_rejects_duplicate_component_names(self):
        duplicate = self.build_component("MACD", (3.0, 4.0))

        with self.assertRaisesRegex(
            ValidationError,
            "indicator component names must be unique",
        ):
            self.build_bundle(
                components=[self.components[0], duplicate]
            )

    def test_rejects_components_with_different_timestamps(self):
        shifted_timestamps = [
            timestamp + timedelta(days=1)
            for timestamp in self.timestamps
        ]
        shifted = self.build_component(
            "signal",
            (1.0, 2.0),
            timestamps=shifted_timestamps,
        )

        with self.assertRaisesRegex(
            ValidationError,
            "components must use identical timestamps",
        ):
            self.build_bundle(
                components=[self.components[0], shifted]
            )

    def test_rejects_blank_bundle_metadata(self):
        with self.assertRaisesRegex(
            ValidationError,
            "technical-analysis metadata cannot be blank",
        ):
            self.build_bundle(indicator=" ")

    def test_rejects_invalid_bundle_parameters(self):
        with self.assertRaisesRegex(
            ValidationError,
            "numeric indicator parameters must be finite",
        ):
            self.build_bundle(
                parameters={"deviation": float("inf")}
            )

    def test_bundle_is_immutable(self):
        bundle = self.build_bundle()

        with self.assertRaises(ValidationError):
            bundle.indicator = "BBANDS"


if __name__ == "__main__":
    unittest.main()
