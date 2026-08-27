from datetime import datetime
from typing import Literal

from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.technical import TechnicalModel


_AFFIRMATIVE_WORDS = frozenset(
    {
        "yes",
        "y",
        "yeah",
        "yep",
        "sure",
        "correct",
        "confirm",
        "confirmed",
        "affirmative",
        "ok",
        "okay",
    }
)


def classify_yes_no(text: str) -> Literal["yes", "no"]:
    """Deterministic, non-LLM yes/no classification for a confirmation
    reply. Anything not recognized as affirmative is treated as "no" --
    a missed "yes" is safer than a falsely-accepted one here. Shared by
    every conversation engine that can enter a pending-confirmation
    state (JarvisConversationSession, BrowserConversationCoordinator).
    """
    normalized = text.strip().lower().strip(".!?,;:")
    return "yes" if normalized in _AFFIRMATIVE_WORDS else "no"


class PendingConfirmation(TechnicalModel):
    """State retained across one turn boundary while Jarvis waits for a
    yes/no answer, either to confirm an LLM-guessed ticker symbol or to
    confirm refreshing the instrument catalogs. Always carries the
    original command/to_date so the underlying request can be resumed
    (or re-attempted) after a "yes".
    """

    model_config = ConfigDict(frozen=True, strict=True)

    kind: Literal["ticker_guess", "catalog_refresh"]
    original_command: str = Field(min_length=1, max_length=2_000)
    to_date: datetime | None = None
    chosen_symbol: str | None = Field(default=None, min_length=1)
    exchange: str | None = Field(default=None, min_length=1)

    @field_validator("original_command", "chosen_symbol", "exchange")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not normalized:
            raise ValueError("pending confirmation text must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_symbol_and_exchange_for_ticker_guess(self) -> "PendingConfirmation":
        if self.kind == "ticker_guess" and (
            self.chosen_symbol is None or self.exchange is None
        ):
            raise ValueError(
                "a ticker-guess confirmation requires a chosen symbol "
                "and exchange"
            )
        return self
