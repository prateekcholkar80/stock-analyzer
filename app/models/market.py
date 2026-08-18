from datetime import datetime
from typing import Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)


class MarketModel(BaseModel):
    model_config = ConfigDict(frozen=True)


class Candle(MarketModel):
    timestamp: datetime
    open: float = Field(ge=0)
    high: float = Field(ge=0)
    low: float = Field(ge=0)
    close: float = Field(ge=0)
    volume: int = Field(ge=0)

    @field_validator("timestamp")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "candle timestamp must include timezone information"
            )

        return value

    @model_validator(mode="after")
    def validate_price_range(self) -> Self:
        if self.high < self.low:
            raise ValueError(
                "candle high must be greater than or equal to low"
            )

        if self.high < max(self.open, self.close):
            raise ValueError(
                "candle high cannot be below open or close"
            )

        if self.low > min(self.open, self.close):
            raise ValueError(
                "candle low cannot be above open or close"
            )

        return self


class MarketQuote(MarketModel):
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)

    price: float = Field(ge=0)
    open: float = Field(ge=0)
    high: float = Field(ge=0)
    low: float = Field(ge=0)
    previous_close: float = Field(ge=0)

    observed_at: datetime
    source: str = Field(default="angel_one", min_length=1)

    @field_validator("observed_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "quote observation time must include timezone information"
            )

        return value

    @model_validator(mode="after")
    def validate_price_range(self) -> Self:
        if self.high < self.low:
            raise ValueError(
                "quote high must be greater than or equal to low"
            )

        return self


class HistoricalCandleSeries(MarketModel):
    exchange: str = Field(min_length=1)
    symbol_token: str = Field(min_length=1)
    symbol: str = Field(min_length=1)
    interval: str = Field(min_length=1)

    candles: list[Candle]
    retrieved_at: datetime
    source: str = Field(default="angel_one", min_length=1)

    @field_validator("retrieved_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(
                "retrieval time must include timezone information"
            )

        return value