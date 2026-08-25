from datetime import datetime
from typing import Self

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.technical import TechnicalModel


class IntradayCandleGap(TechnicalModel):
    """A definite missing candle interval within one IST trading day."""

    model_config = ConfigDict(frozen=True, strict=True)

    interval: str = Field(min_length=1)
    gap_after: datetime
    resumes_at: datetime
    cadence_minutes: int = Field(ge=1)
    missing_candle_count: int = Field(ge=1)

    @field_validator("gap_after", "resumes_at")
    @classmethod
    def require_timezone(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("candle-gap timestamps must include timezone")
        return value

    @model_validator(mode="after")
    def validate_gap_order(self) -> Self:
        if self.resumes_at <= self.gap_after:
            raise ValueError("candle gap must resume after its prior candle")
        return self
