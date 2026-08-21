from typing import Generic, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.technical import TechnicalModel


ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class StructuredGeneration(TechnicalModel, Generic[ResponseModelT]):
    """A validated structured response and its execution metadata.

    Debate agents may record this metadata for audit purposes, but model and
    provider selection remain the responsibility of the gateway composition
    layer.
    """

    model_config = ConfigDict(
        frozen=True,
        strict=True,
        allow_inf_nan=False,
    )

    value: ResponseModelT
    provider: str = Field(min_length=1)
    model: str = Field(min_length=1)
    attempt_count: int = Field(ge=1)

    @field_validator("provider", "model")
    @classmethod
    def reject_blank_identity(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("generation identity must not be blank")
        return normalized


@runtime_checkable
class StructuredLLMGateway(Protocol):
    """Provider-neutral boundary for validated structured generation."""

    @property
    def configuration_fingerprint(self) -> str:
        ...

    def generate(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        response_model: type[ResponseModelT],
    ) -> StructuredGeneration[ResponseModelT]:
        ...
