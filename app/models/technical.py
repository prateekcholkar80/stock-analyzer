from datetime import datetime
from enum import StrEnum
from math import isfinite
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


IndicatorParameterValue = bool | int | float | str


class PriceField(StrEnum):
    OPEN = "open"
    HIGH = "high"
    LOW = "low"
    CLOSE = "close"
    VOLUME = "volume"


def _validate_indicator_inputs(
    price_field: PriceField | None,
    input_fields: tuple[PriceField, ...],
) -> None:
    has_price_field = price_field is not None
    has_input_fields = bool(input_fields)

    if has_price_field == has_input_fields:
        raise ValueError(
            "indicator result must define either price_field or "
            "input_fields"
        )

    if len(input_fields) != len(set(input_fields)):
        raise ValueError("indicator input fields must be unique")

    if input_fields and len(input_fields) < 2:
        raise ValueError(
            "multi-field indicator input requires at least two fields"
        )


class TechnicalModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class IndicatorPoint(TechnicalModel):
    timestamp: datetime
    value: float

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "indicator timestamp must include timezone information"
            )

        return value

    @field_validator("value")
    @classmethod
    def require_finite_value(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("indicator value must be finite")

        return value


class IndicatorComponent(TechnicalModel):
    name: str = Field(min_length=1)
    points: list[IndicatorPoint] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def reject_blank_name(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("indicator component name cannot be blank")

        return value

    @model_validator(mode="after")
    def require_ordered_unique_points(self) -> Self:
        for previous, current in zip(self.points, self.points[1:]):
            if current.timestamp <= previous.timestamp:
                raise ValueError(
                    "indicator component points must have unique "
                    "timestamps in ascending order"
                )

        return self


class IndicatorSeries(TechnicalModel):
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    indicator: str = Field(min_length=1)
    price_field: PriceField | None = None
    input_fields: tuple[PriceField, ...] = ()
    parameters: dict[str, IndicatorParameterValue] = Field(
        default_factory=dict
    )
    points: list[IndicatorPoint] = Field(min_length=1)

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "indicator",
    )
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("technical-analysis metadata cannot be blank")

        return value

    @field_validator("parameters")
    @classmethod
    def validate_parameters(
        cls,
        value: dict[str, IndicatorParameterValue],
    ) -> dict[str, IndicatorParameterValue]:
        for name, parameter_value in value.items():
            if not name.strip():
                raise ValueError("indicator parameter name cannot be blank")

            if (
                isinstance(parameter_value, float)
                and not isfinite(parameter_value)
            ):
                raise ValueError(
                    "numeric indicator parameters must be finite"
                )

        return value

    @model_validator(mode="after")
    def require_ordered_unique_points(self) -> Self:
        _validate_indicator_inputs(
            self.price_field,
            self.input_fields,
        )

        for previous, current in zip(self.points, self.points[1:]):
            if current.timestamp <= previous.timestamp:
                raise ValueError(
                    "indicator points must have unique timestamps in "
                    "ascending order"
                )

        return self


class IndicatorBundle(TechnicalModel):
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    indicator: str = Field(min_length=1)
    price_field: PriceField | None = None
    input_fields: tuple[PriceField, ...] = ()
    parameters: dict[str, IndicatorParameterValue] = Field(
        default_factory=dict
    )
    components: list[IndicatorComponent] = Field(min_length=1)

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "indicator",
    )
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("technical-analysis metadata cannot be blank")

        return value

    @field_validator("parameters")
    @classmethod
    def validate_parameters(
        cls,
        value: dict[str, IndicatorParameterValue],
    ) -> dict[str, IndicatorParameterValue]:
        for name, parameter_value in value.items():
            if not name.strip():
                raise ValueError("indicator parameter name cannot be blank")

            if (
                isinstance(parameter_value, float)
                and not isfinite(parameter_value)
            ):
                raise ValueError(
                    "numeric indicator parameters must be finite"
                )

        return value

    @model_validator(mode="after")
    def validate_components(self) -> Self:
        _validate_indicator_inputs(
            self.price_field,
            self.input_fields,
        )

        normalized_names = [
            component.name.casefold()
            for component in self.components
        ]
        if len(normalized_names) != len(set(normalized_names)):
            raise ValueError("indicator component names must be unique")

        expected_timestamps = [
            point.timestamp
            for point in self.components[0].points
        ]
        for component in self.components[1:]:
            component_timestamps = [
                point.timestamp
                for point in component.points
            ]
            if component_timestamps != expected_timestamps:
                raise ValueError(
                    "indicator components must use identical timestamps"
                )

        return self
