from pydantic import ConfigDict, Field, field_validator

from app.models.technical import TechnicalModel


class TickerResolutionChoice(TechnicalModel):
    """A constrained, structurally-groundable ticker-resolution draft.

    ``chosen_symbols`` is shaped as a tuple (0 or 1 items) rather than an
    optional single string so it can be validated by the same grounding
    mechanism (``app.agents._debate_support.generate_grounded``) used for
    debate citations -- every value in it must be a real candidate symbol
    from the shortlist the LLM was shown, or the call is rejected/retried.
    This is a proposal only: the caller must still ask the user to
    confirm before treating a non-empty result as a resolution.
    """

    model_config = ConfigDict(frozen=True, strict=True)

    chosen_symbols: tuple[str, ...] = Field(default=(), max_length=1)
    is_ambiguous: bool = False

    @field_validator("chosen_symbols")
    @classmethod
    def reject_blank_symbol(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not symbol.strip() for symbol in value):
            raise ValueError("chosen ticker symbol must not be blank")
        return value
