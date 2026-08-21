from pydantic import ConfigDict, Field, field_validator, model_validator

from app.models.technical import TechnicalModel


class ResolvedInstrument(TechnicalModel):
    """Provider-neutral identity required by the market-data workflow."""

    model_config = ConfigDict(frozen=True, strict=True)

    exchange: str = Field(min_length=1, max_length=32)
    symbol_token: str = Field(min_length=1, max_length=100)
    symbol: str = Field(min_length=1, max_length=100)
    display_name: str = Field(min_length=1, max_length=200)
    aliases: tuple[str, ...] = ()

    @field_validator(
        "exchange",
        "symbol_token",
        "symbol",
        "display_name",
    )
    @classmethod
    def normalize_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("instrument identity must not be blank")
        return normalized

    @field_validator("aliases")
    @classmethod
    def normalize_aliases(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized = tuple(value.strip() for value in values)
        if any(not value for value in normalized):
            raise ValueError("instrument aliases must not be blank")
        folded = tuple(value.casefold() for value in normalized)
        if len(folded) != len(set(folded)):
            raise ValueError("instrument aliases must be unique")
        return normalized

    @model_validator(mode="after")
    def reject_redundant_aliases(self) -> "ResolvedInstrument":
        primary = {
            self.symbol.casefold(),
            self.display_name.casefold(),
            self.symbol_token.casefold(),
        }
        if any(alias.casefold() in primary for alias in self.aliases):
            raise ValueError(
                "instrument aliases must not duplicate primary identities"
            )
        return self
