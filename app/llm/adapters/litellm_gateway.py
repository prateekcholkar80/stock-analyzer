from collections.abc import Callable
from hashlib import sha256
from typing import TypeVar

import litellm
from pydantic import BaseModel, ValidationError

from app.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMResponseValidationError,
)
from app.llm.config import LLMRoleSettings
from app.llm.gateway import StructuredGeneration
from app.logging_config import get_operation_id


ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)
CompletionFunction = Callable[..., object]
ProviderResolver = Callable[..., tuple[str, str, str | None, str | None]]

_MAX_ATTEMPTS = 2


class _StructuredResponseError(Exception):
    """Internal marker; its details never cross the adapter boundary."""


class LiteLLMStructuredGateway:
    """StructuredLLMGateway implementation backed by LiteLLM."""

    def __init__(
        self,
        settings: LLMRoleSettings,
        *,
        completion_fn: CompletionFunction = litellm.completion,
        provider_resolver: ProviderResolver = litellm.get_llm_provider,
    ) -> None:
        if not isinstance(settings, LLMRoleSettings):
            raise ValueError(
                "LiteLLM gateway requires validated role settings"
            )
        if not callable(completion_fn):
            raise ValueError("LiteLLM completion function must be callable")
        if not callable(provider_resolver):
            raise ValueError("LiteLLM provider resolver must be callable")

        self.settings = settings
        self._completion_fn = completion_fn
        try:
            _, provider, _, _ = provider_resolver(model=settings.model)
        except Exception as exc:
            raise LLMConfigurationError(
                "The configured LLM model could not be resolved",
                role=settings.role.value,
                model=settings.model,
                operation_id=get_operation_id(),
            ) from exc
        if not isinstance(provider, str) or not provider.strip():
            raise LLMConfigurationError(
                "The configured LLM provider could not be resolved",
                role=settings.role.value,
                model=settings.model,
                operation_id=get_operation_id(),
            )
        self.provider = provider.strip()

    @property
    def configuration_fingerprint(self) -> str:
        serialized = self.settings.model_dump_json()
        return sha256(serialized.encode("utf-8")).hexdigest()

    def generate(
        self,
        *,
        system: str,
        messages: list[dict[str, str]],
        response_model: type[ResponseModelT],
    ) -> StructuredGeneration[ResponseModelT]:
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
            *(dict(message) for message in messages),
        ]

        for attempt_count in range(1, _MAX_ATTEMPTS + 1):
            try:
                response = self._completion_fn(
                    model=self.settings.model,
                    temperature=self.settings.temperature,
                    max_tokens=self.settings.max_tokens,
                    messages=conversation,
                    tools=[tool],
                    tool_choice={
                        "type": "function",
                        "function": {"name": tool_name},
                    },
                )
            except Exception as exc:
                raise self._translate_provider_error(exc) from exc

            try:
                arguments = _extract_tool_arguments(response, tool_name)
                value = response_model.model_validate_json(arguments)
            except (
                _StructuredResponseError,
                ValidationError,
                TypeError,
                ValueError,
            ):
                if attempt_count == _MAX_ATTEMPTS:
                    break
                conversation = [
                    *conversation,
                    {
                        "role": "user",
                        "content": (
                            "The previous response did not match the "
                            "required schema. Call the required tool again "
                            "with a corrected payload."
                        ),
                    },
                ]
                continue

            actual_model = _response_model(response) or self.settings.model
            return StructuredGeneration[response_model](
                value=value,
                provider=self.provider,
                model=actual_model,
                attempt_count=attempt_count,
            )

        raise LLMResponseValidationError(
            "LLM response did not match the required structured schema "
            "after retry",
            role=self.settings.role.value,
            provider=self.provider,
            model=self.settings.model,
            operation_id=get_operation_id(),
        )

    def _translate_provider_error(self, error: Exception) -> LLMError:
        context = {
            "role": self.settings.role.value,
            "provider": self.provider,
            "model": self.settings.model,
            "operation_id": get_operation_id(),
        }
        if isinstance(
            error,
            (litellm.AuthenticationError, litellm.PermissionDeniedError),
        ):
            return LLMAuthenticationError(
                "The LLM provider rejected its configured credential",
                **context,
            )
        if isinstance(error, litellm.RateLimitError):
            return LLMRateLimitError(
                "The LLM provider is rate-limiting requests",
                **context,
            )
        if isinstance(
            error,
            (
                litellm.Timeout,
                litellm.APIConnectionError,
                litellm.ServiceUnavailableError,
            ),
        ):
            return LLMProviderUnavailableError(
                "The LLM provider is currently unavailable",
                **context,
            )
        if isinstance(
            error,
            (litellm.BadRequestError, litellm.NotFoundError),
        ):
            return LLMConfigurationError(
                "The configured LLM provider or model was rejected",
                **context,
            )
        return LLMProviderUnavailableError(
            "The LLM provider could not complete the request",
            **context,
        )


def _extract_tool_arguments(response: object, tool_name: str) -> str:
    try:
        tool_calls = response.choices[0].message.tool_calls
    except (AttributeError, IndexError, TypeError) as exc:
        raise _StructuredResponseError from exc
    if not tool_calls:
        raise _StructuredResponseError

    try:
        call = tool_calls[0]
        function_name = call.function.name
        arguments = call.function.arguments
    except (AttributeError, IndexError, TypeError) as exc:
        raise _StructuredResponseError from exc
    if function_name != tool_name or not isinstance(arguments, str):
        raise _StructuredResponseError
    return arguments


def _response_model(response: object) -> str | None:
    model = getattr(response, "model", None)
    if not isinstance(model, str):
        return None
    normalized = model.strip()
    return normalized or None
