from datetime import datetime
from enum import StrEnum
from math import isclose, isfinite
from typing import Self

from pydantic import (
    Field,
    computed_field,
    field_validator,
    model_validator,
)

from app.models.technical import TechnicalModel


class FairValueGapDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class FairValueGapStatus(StrEnum):
    OPEN = "open"
    PARTIALLY_FILLED = "partially_filled"
    FILLED = "filled"
    INVALIDATED = "invalidated"
    EXPIRED = "expired"


class SwingPivotType(StrEnum):
    HIGH = "high"
    LOW = "low"


class PriceZoneType(StrEnum):
    SUPPORT = "support"
    RESISTANCE = "resistance"


class PriceZoneLifecycleStatus(StrEnum):
    ACTIVE = "active"
    BROKEN = "broken"
    RETESTED = "retested"
    ROLE_REVERSED = "role_reversed"
    FAILED_BREAK = "failed_break"


class PriceZoneBreakDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


class MarketStructureClassification(StrEnum):
    HIGHER_HIGH = "higher_high"
    LOWER_HIGH = "lower_high"
    EQUAL_HIGH = "equal_high"
    HIGHER_LOW = "higher_low"
    LOWER_LOW = "lower_low"
    EQUAL_LOW = "equal_low"


class MarketStructureBias(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"
    RANGE_BOUND = "range_bound"
    MIXED = "mixed"
    UNDETERMINED = "undetermined"


class StructureBreakType(StrEnum):
    BREAK_OF_STRUCTURE = "break_of_structure"
    CHANGE_OF_CHARACTER = "change_of_character"
    UNCLASSIFIED = "unclassified_break"


class StructureBreakDirection(StrEnum):
    BULLISH = "bullish"
    BEARISH = "bearish"


def _require_positive_strength(name: str, value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{name} must be an integer")

    if value < 1:
        raise ValueError(f"{name} must be at least 1")

    return value


class SwingPivot(TechnicalModel):
    pivot_type: SwingPivotType
    pivot_at: datetime
    confirmed_at: datetime
    price: float = Field(ge=0)
    left_strength: int
    right_strength: int

    @field_validator("pivot_at", "confirmed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "swing-pivot timestamps must include timezone "
                "information"
            )

        return value

    @field_validator("price")
    @classmethod
    def require_finite_price(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("swing-pivot price must be finite")

        return value

    @field_validator("left_strength", "right_strength", mode="before")
    @classmethod
    def require_positive_strength(cls, value: int, info) -> int:
        return _require_positive_strength(
            info.field_name.replace("_", " "),
            value,
        )

    @model_validator(mode="after")
    def validate_confirmation(self) -> Self:
        if self.confirmed_at <= self.pivot_at:
            raise ValueError(
                "swing-pivot confirmation must occur after the pivot"
            )

        return self


class SwingPivotDetectionResult(TechnicalModel):
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_retrieved_at: datetime
    left_strength: int
    right_strength: int
    pivots: list[SwingPivot] = Field(default_factory=list)

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "source",
    )
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("swing-pivot metadata cannot be blank")

        return value

    @field_validator("source_retrieved_at")
    @classmethod
    def require_retrieval_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "swing-pivot source retrieval time must include "
                "timezone information"
            )

        return value

    @field_validator("left_strength", "right_strength", mode="before")
    @classmethod
    def require_positive_strength(cls, value: int, info) -> int:
        return _require_positive_strength(
            info.field_name.replace("_", " "),
            value,
        )

    @model_validator(mode="after")
    def validate_pivots(self) -> Self:
        identities: set[tuple[SwingPivotType, datetime]] = set()
        previous_timestamp: datetime | None = None

        for pivot in self.pivots:
            if (
                pivot.left_strength != self.left_strength
                or pivot.right_strength != self.right_strength
            ):
                raise ValueError(
                    "detected swing pivots must use result strengths"
                )

            if (
                previous_timestamp is not None
                and pivot.pivot_at < previous_timestamp
            ):
                raise ValueError(
                    "detected swing pivots must be in ascending order"
                )

            identity = (pivot.pivot_type, pivot.pivot_at)
            if identity in identities:
                raise ValueError("detected swing pivots must be unique")

            identities.add(identity)
            previous_timestamp = pivot.pivot_at

        return self


class MarketStructurePoint(TechnicalModel):
    classification: MarketStructureClassification
    pivot: SwingPivot
    reference_pivot: SwingPivot
    equality_tolerance_percentage: float = Field(ge=0)

    @field_validator("equality_tolerance_percentage")
    @classmethod
    def require_finite_tolerance(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("market-structure tolerance must be finite")

        return value

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        if self.pivot.pivot_type is not self.reference_pivot.pivot_type:
            raise ValueError(
                "market-structure pivots must have the same type"
            )

        if self.reference_pivot.pivot_at >= self.pivot.pivot_at:
            raise ValueError(
                "market-structure reference must precede current pivot"
            )

        if (
            self.reference_pivot.left_strength
            != self.pivot.left_strength
            or self.reference_pivot.right_strength
            != self.pivot.right_strength
        ):
            raise ValueError(
                "market-structure pivots must use identical strengths"
            )

        expected = _classify_market_structure(
            self.pivot.pivot_type,
            self.pivot.price,
            self.reference_pivot.price,
            self.equality_tolerance_percentage,
        )
        if self.classification is not expected:
            raise ValueError(
                "market-structure classification does not match prices"
            )

        return self

    @computed_field
    @property
    def price_change(self) -> float:
        return self.pivot.price - self.reference_pivot.price

    @computed_field
    @property
    def price_change_percentage(self) -> float | None:
        if self.reference_pivot.price == 0:
            return None

        return self.price_change / self.reference_pivot.price * 100


class MarketStructureAnalysisResult(TechnicalModel):
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_retrieved_at: datetime
    evaluated_at: datetime
    pivot_left_strength: int
    pivot_right_strength: int
    equality_tolerance_percentage: float = Field(ge=0)
    points: list[MarketStructurePoint] = Field(default_factory=list)

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "source",
    )
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("market-structure metadata cannot be blank")

        return value

    @field_validator("source_retrieved_at", "evaluated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "market-structure timestamps must include timezone "
                "information"
            )

        return value

    @field_validator(
        "pivot_left_strength",
        "pivot_right_strength",
        mode="before",
    )
    @classmethod
    def require_positive_strength(cls, value: int, info) -> int:
        return _require_positive_strength(
            info.field_name.replace("pivot_", "").replace("_", " "),
            value,
        )

    @field_validator("equality_tolerance_percentage")
    @classmethod
    def require_finite_tolerance(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("market-structure tolerance must be finite")

        return value

    @model_validator(mode="after")
    def validate_points(self) -> Self:
        if self.evaluated_at > self.source_retrieved_at:
            raise ValueError(
                "market-structure evaluation cannot follow source "
                "retrieval"
            )

        identities: set[tuple[SwingPivotType, datetime]] = set()
        previous_timestamp: datetime | None = None
        for point in self.points:
            if not isclose(
                point.equality_tolerance_percentage,
                self.equality_tolerance_percentage,
                rel_tol=1e-12,
                abs_tol=1e-12,
            ):
                raise ValueError(
                    "market-structure points must use result tolerance"
                )

            for pivot in (point.reference_pivot, point.pivot):
                if (
                    pivot.left_strength != self.pivot_left_strength
                    or pivot.right_strength
                    != self.pivot_right_strength
                ):
                    raise ValueError(
                        "market-structure pivots must use result "
                        "strengths"
                    )

                if pivot.confirmed_at > self.evaluated_at:
                    raise ValueError(
                        "market-structure cannot use future pivots"
                    )

            if (
                previous_timestamp is not None
                and point.pivot.pivot_at < previous_timestamp
            ):
                raise ValueError(
                    "market-structure points must be in ascending order"
                )

            identity = (
                point.pivot.pivot_type,
                point.pivot.pivot_at,
            )
            if identity in identities:
                raise ValueError(
                    "market-structure points must be unique"
                )

            identities.add(identity)
            previous_timestamp = point.pivot.pivot_at

        return self

    @computed_field
    @property
    def bias(self) -> MarketStructureBias:
        latest_high = None
        latest_low = None

        for point in self.points:
            if point.pivot.pivot_type is SwingPivotType.HIGH:
                latest_high = point.classification
            else:
                latest_low = point.classification

        if latest_high is None or latest_low is None:
            return MarketStructureBias.UNDETERMINED

        if (
            latest_high is MarketStructureClassification.HIGHER_HIGH
            and latest_low is MarketStructureClassification.HIGHER_LOW
        ):
            return MarketStructureBias.BULLISH

        if (
            latest_high is MarketStructureClassification.LOWER_HIGH
            and latest_low is MarketStructureClassification.LOWER_LOW
        ):
            return MarketStructureBias.BEARISH

        if (
            latest_high is MarketStructureClassification.EQUAL_HIGH
            and latest_low is MarketStructureClassification.EQUAL_LOW
        ):
            return MarketStructureBias.RANGE_BOUND

        return MarketStructureBias.MIXED


def _classify_market_structure(
    pivot_type: SwingPivotType,
    current_price: float,
    reference_price: float,
    equality_tolerance_percentage: float,
) -> MarketStructureClassification:
    if reference_price == 0:
        equal = current_price == 0
    else:
        difference_percentage = (
            abs(current_price - reference_price)
            / reference_price
            * 100
        )
        equal = difference_percentage <= equality_tolerance_percentage

    if pivot_type is SwingPivotType.HIGH:
        if equal:
            return MarketStructureClassification.EQUAL_HIGH
        if current_price > reference_price:
            return MarketStructureClassification.HIGHER_HIGH
        return MarketStructureClassification.LOWER_HIGH

    if equal:
        return MarketStructureClassification.EQUAL_LOW
    if current_price > reference_price:
        return MarketStructureClassification.HIGHER_LOW
    return MarketStructureClassification.LOWER_LOW


class StructureBreak(TechnicalModel):
    break_type: StructureBreakType
    direction: StructureBreakDirection
    occurred_at: datetime
    previous_close: float = Field(ge=0)
    close_price: float = Field(ge=0)
    broken_pivot: SwingPivot
    bias_before: MarketStructureBias

    @field_validator("occurred_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "structure-break timestamp must include timezone "
                "information"
            )

        return value

    @field_validator("previous_close", "close_price")
    @classmethod
    def require_finite_price(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("structure-break prices must be finite")

        return value

    @model_validator(mode="after")
    def validate_break(self) -> Self:
        if self.occurred_at < self.broken_pivot.confirmed_at:
            raise ValueError(
                "structure break cannot precede pivot confirmation"
            )

        if self.direction is StructureBreakDirection.BULLISH:
            if self.broken_pivot.pivot_type is not SwingPivotType.HIGH:
                raise ValueError(
                    "bullish structure breaks require a swing high"
                )
            if not (
                self.previous_close <= self.broken_pivot.price
                < self.close_price
            ):
                raise ValueError(
                    "bullish structure breaks require a close crossing "
                    "above the pivot"
                )
        else:
            if self.broken_pivot.pivot_type is not SwingPivotType.LOW:
                raise ValueError(
                    "bearish structure breaks require a swing low"
                )
            if not (
                self.previous_close >= self.broken_pivot.price
                > self.close_price
            ):
                raise ValueError(
                    "bearish structure breaks require a close crossing "
                    "below the pivot"
                )

        expected_type = _classify_structure_break(
            self.direction,
            self.bias_before,
        )
        if self.break_type is not expected_type:
            raise ValueError(
                "structure-break type does not match prior bias"
            )

        return self

    @computed_field
    @property
    def break_distance(self) -> float:
        return abs(self.close_price - self.broken_pivot.price)

    @computed_field
    @property
    def break_percentage(self) -> float | None:
        if self.broken_pivot.price == 0:
            return None

        return self.break_distance / self.broken_pivot.price * 100

    @computed_field
    @property
    def bias_after(self) -> MarketStructureBias:
        if self.break_type is StructureBreakType.CHANGE_OF_CHARACTER:
            if self.direction is StructureBreakDirection.BULLISH:
                return MarketStructureBias.BULLISH
            return MarketStructureBias.BEARISH

        return self.bias_before


class StructureBreakDetectionResult(TechnicalModel):
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_retrieved_at: datetime
    evaluated_at: datetime
    pivot_left_strength: int
    pivot_right_strength: int
    structure_equality_tolerance_percentage: float = Field(ge=0)
    events: list[StructureBreak] = Field(default_factory=list)

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "source",
    )
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("structure-break metadata cannot be blank")

        return value

    @field_validator("source_retrieved_at", "evaluated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "structure-break timestamps must include timezone "
                "information"
            )

        return value

    @field_validator(
        "pivot_left_strength",
        "pivot_right_strength",
        mode="before",
    )
    @classmethod
    def require_positive_strength(cls, value: int, info) -> int:
        return _require_positive_strength(
            info.field_name.replace("pivot_", "").replace("_", " "),
            value,
        )

    @field_validator("structure_equality_tolerance_percentage")
    @classmethod
    def require_finite_tolerance(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("structure-break tolerance must be finite")

        return value

    @model_validator(mode="after")
    def validate_events(self) -> Self:
        if self.evaluated_at > self.source_retrieved_at:
            raise ValueError(
                "structure-break evaluation cannot follow source "
                "retrieval"
            )

        broken_pivots: set[tuple[SwingPivotType, datetime]] = set()
        previous_timestamp: datetime | None = None
        for event in self.events:
            if event.occurred_at > self.evaluated_at:
                raise ValueError(
                    "structure-break result cannot contain future events"
                )

            if (
                event.broken_pivot.left_strength
                != self.pivot_left_strength
                or event.broken_pivot.right_strength
                != self.pivot_right_strength
            ):
                raise ValueError(
                    "structure-break pivots must use result strengths"
                )

            if (
                previous_timestamp is not None
                and event.occurred_at < previous_timestamp
            ):
                raise ValueError(
                    "structure-break events must be in ascending order"
                )

            identity = (
                event.broken_pivot.pivot_type,
                event.broken_pivot.pivot_at,
            )
            if identity in broken_pivots:
                raise ValueError(
                    "each swing pivot can be structurally broken once"
                )

            broken_pivots.add(identity)
            previous_timestamp = event.occurred_at

        return self


def _classify_structure_break(
    direction: StructureBreakDirection,
    bias_before: MarketStructureBias,
) -> StructureBreakType:
    if (
        direction is StructureBreakDirection.BULLISH
        and bias_before is MarketStructureBias.BULLISH
    ) or (
        direction is StructureBreakDirection.BEARISH
        and bias_before is MarketStructureBias.BEARISH
    ):
        return StructureBreakType.BREAK_OF_STRUCTURE

    if (
        direction is StructureBreakDirection.BULLISH
        and bias_before is MarketStructureBias.BEARISH
    ) or (
        direction is StructureBreakDirection.BEARISH
        and bias_before is MarketStructureBias.BULLISH
    ):
        return StructureBreakType.CHANGE_OF_CHARACTER

    return StructureBreakType.UNCLASSIFIED


class SupportResistanceZone(TechnicalModel):
    zone_type: PriceZoneType
    lower_price: float = Field(ge=0)
    upper_price: float = Field(ge=0)
    center_price: float = Field(ge=0)
    confirmed_at: datetime
    pivots: list[SwingPivot] = Field(min_length=1)

    @field_validator("confirmed_at")
    @classmethod
    def require_confirmation_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "support-resistance confirmation time must include "
                "timezone information"
            )

        return value

    @field_validator("lower_price", "upper_price", "center_price")
    @classmethod
    def require_finite_price(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError("support-resistance prices must be finite")

        return value

    @model_validator(mode="after")
    def validate_zone(self) -> Self:
        if self.upper_price < self.lower_price:
            raise ValueError(
                "support-resistance upper price cannot be below lower "
                "price"
            )

        if not self.lower_price <= self.center_price <= self.upper_price:
            raise ValueError(
                "support-resistance center price must be inside the zone"
            )

        expected_pivot_type = (
            SwingPivotType.LOW
            if self.zone_type is PriceZoneType.SUPPORT
            else SwingPivotType.HIGH
        )
        previous_timestamp: datetime | None = None
        identities: set[tuple[SwingPivotType, datetime]] = set()

        for pivot in self.pivots:
            if pivot.pivot_type is not expected_pivot_type:
                raise ValueError(
                    "support-resistance zone contains an incompatible "
                    "pivot type"
                )

            if (
                previous_timestamp is not None
                and pivot.pivot_at < previous_timestamp
            ):
                raise ValueError(
                    "support-resistance pivots must be in ascending order"
                )

            identity = (pivot.pivot_type, pivot.pivot_at)
            if identity in identities:
                raise ValueError(
                    "support-resistance pivots must be unique"
                )

            identities.add(identity)
            previous_timestamp = pivot.pivot_at

        pivot_prices = [pivot.price for pivot in self.pivots]
        if not isclose(
            self.lower_price,
            min(pivot_prices),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ) or not isclose(
            self.upper_price,
            max(pivot_prices),
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "support-resistance boundaries must match pivot prices"
            )

        expected_center = sum(pivot_prices) / len(pivot_prices)
        if not isclose(
            self.center_price,
            expected_center,
            rel_tol=1e-12,
            abs_tol=1e-12,
        ):
            raise ValueError(
                "support-resistance center must equal mean pivot price"
            )

        return self

    @computed_field
    @property
    def touch_count(self) -> int:
        return len(self.pivots)

    @computed_field
    @property
    def first_touched_at(self) -> datetime:
        return self.pivots[0].pivot_at

    @computed_field
    @property
    def last_touched_at(self) -> datetime:
        return self.pivots[-1].pivot_at


class SupportResistanceDetectionResult(TechnicalModel):
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_retrieved_at: datetime
    evaluated_at: datetime
    pivot_left_strength: int
    pivot_right_strength: int
    tolerance_percentage: float = Field(ge=0)
    minimum_touches: int
    zones: list[SupportResistanceZone] = Field(default_factory=list)

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "source",
    )
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "support-resistance metadata cannot be blank"
            )

        return value

    @field_validator("source_retrieved_at", "evaluated_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "support-resistance timestamps must include timezone "
                "information"
            )

        return value

    @field_validator(
        "pivot_left_strength",
        "pivot_right_strength",
        mode="before",
    )
    @classmethod
    def require_positive_strength(cls, value: int, info) -> int:
        return _require_positive_strength(
            info.field_name.replace("pivot_", "").replace("_", " "),
            value,
        )

    @field_validator("tolerance_percentage")
    @classmethod
    def require_finite_tolerance(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError(
                "support-resistance tolerance must be finite"
            )

        return value

    @field_validator("minimum_touches", mode="before")
    @classmethod
    def require_minimum_touches(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("minimum zone touches must be an integer")

        if value < 2:
            raise ValueError("minimum zone touches must be at least 2")

        return value

    @model_validator(mode="after")
    def validate_zones(self) -> Self:
        if self.evaluated_at > self.source_retrieved_at:
            raise ValueError(
                "support-resistance evaluation cannot follow source "
                "retrieval"
            )

        identities: set[tuple[PriceZoneType, float, float]] = set()
        for zone in self.zones:
            if zone.touch_count < self.minimum_touches:
                raise ValueError(
                    "support-resistance zones require minimum touches"
                )

            if zone.lower_price == 0:
                spread_percentage = (
                    0.0 if zone.upper_price == 0 else float("inf")
                )
            else:
                spread_percentage = (
                    (zone.upper_price - zone.lower_price)
                    / zone.lower_price
                    * 100
                )
            if spread_percentage > self.tolerance_percentage + 1e-12:
                raise ValueError(
                    "support-resistance zone exceeds result tolerance"
                )

            expected_confirmation = zone.pivots[
                self.minimum_touches - 1
            ].confirmed_at
            if zone.confirmed_at != expected_confirmation:
                raise ValueError(
                    "support-resistance confirmation must match the "
                    "minimum-touch pivot"
                )

            if any(
                pivot.confirmed_at > self.evaluated_at
                for pivot in zone.pivots
            ):
                raise ValueError(
                    "support-resistance zones cannot use future pivots"
                )

            for pivot in zone.pivots:
                if (
                    pivot.left_strength != self.pivot_left_strength
                    or pivot.right_strength
                    != self.pivot_right_strength
                ):
                    raise ValueError(
                        "support-resistance pivots must use result "
                        "strengths"
                    )

            identity = (
                zone.zone_type,
                zone.lower_price,
                zone.upper_price,
            )
            if identity in identities:
                raise ValueError(
                    "support-resistance zones must be unique"
                )

            identities.add(identity)

        return self


class SupportResistanceLifecycle(TechnicalModel):
    zone: SupportResistanceZone
    status: PriceZoneLifecycleStatus
    broken_at: datetime | None = None
    break_direction: PriceZoneBreakDirection | None = None
    previous_close: float | None = Field(default=None, ge=0)
    break_close_price: float | None = Field(default=None, ge=0)
    retested_at: datetime | None = None
    reversal_confirmed_at: datetime | None = None
    failed_at: datetime | None = None

    @field_validator(
        "broken_at",
        "retested_at",
        "reversal_confirmed_at",
        "failed_at",
    )
    @classmethod
    def require_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError(
                "zone-lifecycle timestamps must include timezone "
                "information"
            )

        return value

    @field_validator("previous_close", "break_close_price")
    @classmethod
    def require_finite_price(
        cls,
        value: float | None,
    ) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("zone-lifecycle prices must be finite")

        return value

    @model_validator(mode="after")
    def validate_lifecycle(self) -> Self:
        break_fields = (
            self.broken_at,
            self.break_direction,
            self.previous_close,
            self.break_close_price,
        )
        if self.status is PriceZoneLifecycleStatus.ACTIVE:
            if any(value is not None for value in break_fields):
                raise ValueError(
                    "active zones cannot contain break information"
                )
            if any(
                value is not None
                for value in (
                    self.retested_at,
                    self.reversal_confirmed_at,
                    self.failed_at,
                )
            ):
                raise ValueError(
                    "active zones cannot contain lifecycle events"
                )
            return self

        if any(value is None for value in break_fields):
            raise ValueError(
                "non-active zones require complete break information"
            )

        self._validate_break()
        self._validate_status_events()
        return self

    def _validate_break(self) -> None:
        if self.broken_at < self.zone.confirmed_at:
            raise ValueError(
                "zone break cannot precede zone confirmation"
            )

        if self.zone.zone_type is PriceZoneType.RESISTANCE:
            if self.break_direction is not PriceZoneBreakDirection.BULLISH:
                raise ValueError(
                    "resistance zones require a bullish break"
                )
            if not (
                self.previous_close <= self.zone.upper_price
                < self.break_close_price
            ):
                raise ValueError(
                    "resistance break requires a close crossing above "
                    "the zone"
                )
            return

        if self.break_direction is not PriceZoneBreakDirection.BEARISH:
            raise ValueError("support zones require a bearish break")
        if not (
            self.previous_close >= self.zone.lower_price
            > self.break_close_price
        ):
            raise ValueError(
                "support break requires a close crossing below the zone"
            )

    def _validate_status_events(self) -> None:
        if self.retested_at is not None:
            if self.retested_at <= self.broken_at:
                raise ValueError("zone retest must follow the break")

        if self.reversal_confirmed_at is not None:
            if self.retested_at is None:
                raise ValueError(
                    "role reversal requires a prior zone retest"
                )
            if self.reversal_confirmed_at <= self.retested_at:
                raise ValueError(
                    "role-reversal confirmation must follow the retest"
                )

        if self.failed_at is not None:
            if self.failed_at <= self.broken_at:
                raise ValueError(
                    "failed-break confirmation must follow the break"
                )
            if self.reversal_confirmed_at is not None:
                raise ValueError(
                    "a role-reversed zone cannot also have a failed break"
                )

        if self.status is PriceZoneLifecycleStatus.BROKEN:
            if any(
                value is not None
                for value in (
                    self.retested_at,
                    self.reversal_confirmed_at,
                    self.failed_at,
                )
            ):
                raise ValueError(
                    "broken status cannot contain later lifecycle events"
                )
        elif self.status is PriceZoneLifecycleStatus.RETESTED:
            if self.retested_at is None:
                raise ValueError("retested status requires a retest time")
            if (
                self.reversal_confirmed_at is not None
                or self.failed_at is not None
            ):
                raise ValueError(
                    "retested status cannot contain a terminal event"
                )
        elif self.status is PriceZoneLifecycleStatus.ROLE_REVERSED:
            if (
                self.retested_at is None
                or self.reversal_confirmed_at is None
            ):
                raise ValueError(
                    "role-reversed status requires retest and "
                    "confirmation times"
                )
            if self.failed_at is not None:
                raise ValueError(
                    "role-reversed status cannot contain a failed break"
                )
        elif self.status is PriceZoneLifecycleStatus.FAILED_BREAK:
            if self.failed_at is None:
                raise ValueError(
                    "failed-break status requires a failure time"
                )
            if self.reversal_confirmed_at is not None:
                raise ValueError(
                    "failed-break status cannot contain a reversal"
                )

    @computed_field
    @property
    def reversed_zone_type(self) -> PriceZoneType | None:
        if self.status is not PriceZoneLifecycleStatus.ROLE_REVERSED:
            return None

        if self.zone.zone_type is PriceZoneType.RESISTANCE:
            return PriceZoneType.SUPPORT
        return PriceZoneType.RESISTANCE

    @computed_field
    @property
    def break_distance(self) -> float | None:
        if self.break_close_price is None:
            return None

        boundary = (
            self.zone.upper_price
            if self.break_direction is PriceZoneBreakDirection.BULLISH
            else self.zone.lower_price
        )
        return abs(self.break_close_price - boundary)

    @computed_field
    @property
    def break_percentage(self) -> float | None:
        if self.break_distance is None:
            return None

        boundary = (
            self.zone.upper_price
            if self.break_direction is PriceZoneBreakDirection.BULLISH
            else self.zone.lower_price
        )
        if boundary == 0:
            return None

        return self.break_distance / boundary * 100


class SupportResistanceLifecycleResult(TechnicalModel):
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_retrieved_at: datetime
    zone_detection_evaluated_at: datetime
    evaluated_at: datetime
    pivot_left_strength: int
    pivot_right_strength: int
    tolerance_percentage: float = Field(ge=0)
    minimum_touches: int
    lifecycles: list[SupportResistanceLifecycle] = Field(
        default_factory=list
    )

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "source",
    )
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError(
                "support-resistance lifecycle metadata cannot be blank"
            )

        return value

    @field_validator(
        "source_retrieved_at",
        "zone_detection_evaluated_at",
        "evaluated_at",
    )
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "support-resistance lifecycle timestamps must include "
                "timezone information"
            )

        return value

    @field_validator(
        "pivot_left_strength",
        "pivot_right_strength",
        mode="before",
    )
    @classmethod
    def require_positive_strength(cls, value: int, info) -> int:
        return _require_positive_strength(
            info.field_name.replace("pivot_", "").replace("_", " "),
            value,
        )

    @field_validator("tolerance_percentage")
    @classmethod
    def require_finite_tolerance(cls, value: float) -> float:
        if not isfinite(value):
            raise ValueError(
                "support-resistance lifecycle tolerance must be finite"
            )

        return value

    @field_validator("minimum_touches", mode="before")
    @classmethod
    def require_minimum_touches(cls, value: int) -> int:
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("minimum zone touches must be an integer")

        if value < 2:
            raise ValueError("minimum zone touches must be at least 2")

        return value

    @model_validator(mode="after")
    def validate_lifecycles(self) -> Self:
        if self.zone_detection_evaluated_at > self.source_retrieved_at:
            raise ValueError(
                "zone-detection evaluation cannot follow source retrieval"
            )
        if self.evaluated_at < self.zone_detection_evaluated_at:
            raise ValueError(
                "lifecycle evaluation cannot precede zone detection"
            )
        if self.evaluated_at > self.source_retrieved_at:
            raise ValueError(
                "lifecycle evaluation cannot follow source retrieval"
            )

        identities: set[tuple[PriceZoneType, float, float]] = set()
        for lifecycle in self.lifecycles:
            zone = lifecycle.zone
            if zone.confirmed_at > self.evaluated_at:
                raise ValueError(
                    "zone lifecycle cannot use a future zone"
                )
            if any(
                pivot.confirmed_at > self.zone_detection_evaluated_at
                for pivot in zone.pivots
            ):
                raise ValueError(
                    "zone lifecycle cannot use pivots unavailable during "
                    "zone detection"
                )
            if zone.touch_count < self.minimum_touches:
                raise ValueError(
                    "zone lifecycle requires result minimum touches"
                )
            if zone.lower_price == 0:
                spread_percentage = (
                    0.0 if zone.upper_price == 0 else float("inf")
                )
            else:
                spread_percentage = (
                    (zone.upper_price - zone.lower_price)
                    / zone.lower_price
                    * 100
                )
            if spread_percentage > self.tolerance_percentage + 1e-12:
                raise ValueError(
                    "zone lifecycle exceeds result tolerance"
                )

            expected_confirmation = zone.pivots[
                self.minimum_touches - 1
            ].confirmed_at
            if zone.confirmed_at != expected_confirmation:
                raise ValueError(
                    "zone lifecycle confirmation must match the "
                    "minimum-touch pivot"
                )
            if any(
                pivot.left_strength != self.pivot_left_strength
                or pivot.right_strength != self.pivot_right_strength
                for pivot in zone.pivots
            ):
                raise ValueError(
                    "zone-lifecycle pivots must use result strengths"
                )

            timestamps = (
                lifecycle.broken_at,
                lifecycle.retested_at,
                lifecycle.reversal_confirmed_at,
                lifecycle.failed_at,
            )
            if any(
                timestamp is not None
                and timestamp > self.evaluated_at
                for timestamp in timestamps
            ):
                raise ValueError(
                    "zone lifecycle cannot contain future events"
                )

            identity = (
                zone.zone_type,
                zone.lower_price,
                zone.upper_price,
            )
            if identity in identities:
                raise ValueError("zone lifecycles must be unique")
            identities.add(identity)

        return self


class FairValueGap(TechnicalModel):
    direction: FairValueGapDirection
    first_candle_at: datetime
    impulse_candle_at: datetime
    detected_at: datetime
    lower_price: float = Field(ge=0)
    upper_price: float = Field(ge=0)
    atr_value: float | None = Field(default=None, ge=0)
    status: FairValueGapStatus = FairValueGapStatus.OPEN
    fill_percentage: float = Field(default=0, ge=0, le=100)
    first_touched_at: datetime | None = None
    resolved_at: datetime | None = None

    @field_validator(
        "first_candle_at",
        "impulse_candle_at",
        "detected_at",
        "first_touched_at",
        "resolved_at",
    )
    @classmethod
    def require_timezone(
        cls,
        value: datetime | None,
    ) -> datetime | None:
        if value is not None and (
            value.tzinfo is None or value.utcoffset() is None
        ):
            raise ValueError(
                "fair-value-gap timestamps must include timezone "
                "information"
            )

        return value

    @field_validator("lower_price", "upper_price", "atr_value")
    @classmethod
    def require_finite_value(
        cls,
        value: float | None,
    ) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError("fair-value-gap numeric values must be finite")

        return value

    @model_validator(mode="after")
    def validate_formation(self) -> Self:
        if not (
            self.first_candle_at
            < self.impulse_candle_at
            < self.detected_at
        ):
            raise ValueError(
                "fair-value-gap formation timestamps must be in "
                "ascending order"
            )

        if self.upper_price <= self.lower_price:
            raise ValueError(
                "fair-value-gap upper price must be greater than lower "
                "price"
            )

        if (
            self.first_touched_at is not None
            and self.first_touched_at <= self.detected_at
        ):
            raise ValueError(
                "fair-value-gap first touch must occur after detection"
            )

        if (
            self.resolved_at is not None
            and self.resolved_at <= self.detected_at
        ):
            raise ValueError(
                "fair-value-gap resolution must occur after detection"
            )

        if (
            self.first_touched_at is not None
            and self.resolved_at is not None
            and self.resolved_at < self.first_touched_at
        ):
            raise ValueError(
                "fair-value-gap resolution cannot precede first touch"
            )

        if self.status is FairValueGapStatus.OPEN:
            if self.fill_percentage != 0 or self.resolved_at is not None:
                raise ValueError(
                    "open fair-value gaps cannot be filled or resolved"
                )
        elif self.status is FairValueGapStatus.PARTIALLY_FILLED:
            if not 0 < self.fill_percentage < 100:
                raise ValueError(
                    "partially filled gaps require fill between 0 and 100"
                )
            if self.first_touched_at is None or self.resolved_at is not None:
                raise ValueError(
                    "partially filled gaps require an unresolved first touch"
                )
        elif self.status in (
            FairValueGapStatus.FILLED,
            FairValueGapStatus.INVALIDATED,
        ):
            if self.fill_percentage != 100:
                raise ValueError(
                    "filled or invalidated gaps require 100 percent fill"
                )
            if self.first_touched_at is None or self.resolved_at is None:
                raise ValueError(
                    "filled or invalidated gaps require lifecycle timestamps"
                )
        elif self.status is FairValueGapStatus.EXPIRED:
            if self.fill_percentage >= 100 or self.resolved_at is None:
                raise ValueError(
                    "expired gaps require resolution below 100 percent fill"
                )
            if self.fill_percentage > 0 and self.first_touched_at is None:
                raise ValueError(
                    "partially mitigated expired gaps require a first touch"
                )

        return self

    @computed_field
    @property
    def gap_size(self) -> float:
        return self.upper_price - self.lower_price

    @computed_field
    @property
    def gap_percentage(self) -> float:
        midpoint = (self.upper_price + self.lower_price) / 2
        return self.gap_size / midpoint * 100

    @computed_field
    @property
    def atr_multiple(self) -> float | None:
        if self.atr_value in (None, 0):
            return None

        return self.gap_size / self.atr_value


class FairValueGapDetectionResult(TechnicalModel):
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)
    source: str = Field(min_length=1)
    source_retrieved_at: datetime
    minimum_gap_percentage: float = Field(default=0, ge=0)
    minimum_atr_multiple: float | None = Field(default=None, ge=0)
    gaps: list[FairValueGap] = Field(default_factory=list)

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "interval",
        "source",
    )
    @classmethod
    def reject_blank_metadata(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("fair-value-gap metadata cannot be blank")

        return value

    @field_validator("source_retrieved_at")
    @classmethod
    def require_retrieval_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "fair-value-gap source retrieval time must include "
                "timezone information"
            )

        return value

    @field_validator(
        "minimum_gap_percentage",
        "minimum_atr_multiple",
    )
    @classmethod
    def require_finite_threshold(
        cls,
        value: float | None,
    ) -> float | None:
        if value is not None and not isfinite(value):
            raise ValueError(
                "fair-value-gap thresholds must be finite"
            )

        return value

    @model_validator(mode="after")
    def validate_gap_order(self) -> Self:
        identities: set[
            tuple[FairValueGapDirection, datetime, float, float]
        ] = set()
        previous_timestamp: datetime | None = None

        for gap in self.gaps:
            if (
                previous_timestamp is not None
                and gap.detected_at < previous_timestamp
            ):
                raise ValueError(
                    "detected fair-value gaps must be in ascending order"
                )

            identity = (
                gap.direction,
                gap.detected_at,
                gap.lower_price,
                gap.upper_price,
            )
            if identity in identities:
                raise ValueError("detected fair-value gaps must be unique")

            identities.add(identity)
            previous_timestamp = gap.detected_at

        return self
