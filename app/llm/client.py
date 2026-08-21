from collections.abc import Callable
from typing import TypeVar

import litellm
from pydantic import BaseModel, Field, ValidationError

from app.exceptions import LLMResponseValidationError
from app.models.technical import TechnicalModel


ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)

_MAX_ATTEMPTS = 2


class LLMGenerationConfig(TechnicalModel):
    """Provider/model choice for one LLM-backed agent role."""

    model: str = Field(min_length=1)
    temperature: float = Field(ge=0, le=2)
    max_tokens: int = Field(gt=0)


class LLMClient:
    """Provider-agnostic structured-output boundary, backed by litellm.

    A model string like ``"anthropic/claude-sonnet-4-5"`` or
    ``"openai/gpt-4o"`` picks the provider; litellm reads that provider's
    API key from the process environment itself, so no key handling lives
    here.
    """

    def __init__(
        self,
        completion_fn: Callable[..., object] = litellm.completion,
    ) -> None:
        self._completion_fn = completion_fn

    def complete_structured(
        self,
        *,
        config: LLMGenerationConfig,
        system: str,
        messages: list[dict[str, str]],
        response_model: type[ResponseModelT],
    ) -> ResponseModelT:
        """Force a tool-call matching response_model, retrying once."""
        tool_name = f"emit_{response_model.__name__.lower()}"
        tool = {
            "type": "function",
            "function": {
                "name": tool_name,
                "description": (
                    f"Emit a {response_model.__name__} matching the "
                    "required schema."
                ),
                "parameters": response_model.model_json_schema(),
            },
        }
        conversation = [
            {"role": "system", "content": system},
            *messages,
        ]

        last_error: Exception | None = None
        for _ in range(_MAX_ATTEMPTS):
            if last_error is not None:
                conversation = [
                    *conversation,
                    {
                        "role": "user",
                        "content": (
                            "Your previous response did not match the "
                            f"required schema: {last_error}. Call "
                            f"{tool_name} again with a corrected payload."
                        ),
                    },
                ]
            response = self._completion_fn(
                model=config.model,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                messages=conversation,
                tools=[tool],
                tool_choice={
                    "type": "function",
                    "function": {"name": tool_name},
                },
            )
            try:
                arguments = _extract_tool_arguments(response, tool_name)
                return response_model.model_validate_json(arguments)
            except (LLMResponseValidationError, ValidationError) as error:
                last_error = error
                continue

        raise LLMResponseValidationError(
            f"LLM response did not match {response_model.__name__} schema "
            f"after retry: {last_error}"
        )


def _extract_tool_arguments(response: object, tool_name: str) -> str:
    try:
        tool_calls = response.choices[0].message.tool_calls
    except (AttributeError, IndexError) as error:
        raise LLMResponseValidationError(
            "LLM response did not include a tool call"
        ) from error

    if not tool_calls:
        raise LLMResponseValidationError(
            "LLM response did not include a tool call"
        )

    call = tool_calls[0]
    if call.function.name != tool_name:
        raise LLMResponseValidationError(
            f"LLM response called an unexpected tool: {call.function.name}"
        )
    return call.function.arguments
