from dataclasses import dataclass

from app.exceptions import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMError,
    LLMProviderUnavailableError,
    LLMRateLimitError,
    LLMResponseValidationError,
)
from app.llm.config import LLMRole
from app.models.llm import JarvisLLMFailureResponse, LLMFailureCode


@dataclass(frozen=True, slots=True)
class _FailureCopy:
    code: LLMFailureCode
    title: str
    display_message: str
    spoken_message: str
    recovery_action: str


_CONFIGURATION_COPY = _FailureCopy(
    code=LLMFailureCode.CONFIGURATION,
    title="My thinking cap is not configured",
    display_message=(
        "Jarvis cannot start the mandatory Bull-Bear-Judge debate because "
        "the LLM configuration is incomplete. No investment conclusion "
        "was produced."
    ),
    spoken_message=(
        "My Bull, Bear, and Judge are dressed for the debate, but someone "
        "forgot to plug in my thinking cap. Configure the LLM and I will "
        "try again."
    ),
    recovery_action=(
        "Configure the required LLM model and provider credential, then "
        "retry the request."
    ),
)

_AUTHENTICATION_COPY = _FailureCopy(
    code=LLMFailureCode.AUTHENTICATION,
    title="My credentials were shown the door",
    display_message=(
        "The LLM provider rejected the configured credential, so the "
        "mandatory debate was stopped. No investment conclusion was "
        "produced."
    ),
    spoken_message=(
        "The model provider declined my credentials. Slightly awkward, "
        "but better than inventing an answer. Please check the credential."
    ),
    recovery_action=(
        "Verify the provider credential and its model permissions, then "
        "retry the request."
    ),
)

_PROVIDER_UNAVAILABLE_COPY = _FailureCopy(
    code=LLMFailureCode.PROVIDER_UNAVAILABLE,
    title="The cloud brain is temporarily off the air",
    display_message=(
        "The LLM provider is currently unavailable, so Jarvis could not "
        "complete the mandatory debate. No investment conclusion was "
        "produced."
    ),
    spoken_message=(
        "My cloud brain appears to be taking an unscheduled tea break. "
        "I have not guessed the answer; please try again shortly."
    ),
    recovery_action=(
        "Retry after a short delay. If the problem continues, check the "
        "provider service status and network connectivity."
    ),
)

_RATE_LIMIT_COPY = _FailureCopy(
    code=LLMFailureCode.RATE_LIMITED,
    title="The debate chamber is at capacity",
    display_message=(
        "The LLM provider is rate-limiting requests, so Jarvis could not "
        "complete the mandatory debate. No investment conclusion was "
        "produced."
    ),
    spoken_message=(
        "The provider has asked my debaters to wait in line. Even Bulls "
        "and Bears need queue discipline. Please try again shortly."
    ),
    recovery_action=(
        "Retry after the provider cooldown period or review the account's "
        "request limits."
    ),
)

_INVALID_RESPONSE_COPY = _FailureCopy(
    code=LLMFailureCode.INVALID_RESPONSE,
    title="The model went off-script",
    display_message=(
        "The LLM response did not satisfy Jarvis's required structured "
        "schema, so it was rejected. No investment conclusion was "
        "produced."
    ),
    spoken_message=(
        "The model answered in what I can only describe as interpretive "
        "dance. I rejected it instead of pretending it was analysis."
    ),
    recovery_action=(
        "Retry the request. If it repeats, review model compatibility and "
        "the structured-output configuration."
    ),
)

_UNKNOWN_COPY = _FailureCopy(
    code=LLMFailureCode.UNKNOWN,
    title="The debate could not be completed",
    display_message=(
        "Jarvis encountered a classified LLM failure and stopped the "
        "mandatory debate. No investment conclusion was produced."
    ),
    spoken_message=(
        "The debate hit a problem I can classify only as inconvenient. "
        "I stopped rather than manufacture a recommendation."
    ),
    recovery_action=(
        "Use the operation reference to inspect application logs, then "
        "retry when the underlying issue is resolved."
    ),
)


def present_llm_failure(
    error: LLMError,
    *,
    operation_id: str | None = None,
) -> JarvisLLMFailureResponse:
    """Convert a typed LLM failure into deterministic UI and voice copy."""
    if not isinstance(error, LLMError):
        raise ValueError("Jarvis LLM failure presenter requires an LLMError")
    if operation_id is not None and (
        not isinstance(operation_id, str) or not operation_id.strip()
    ):
        raise ValueError("boundary operation ID must be a non-blank string")

    copy = _copy_for(error)
    return JarvisLLMFailureResponse(
        code=copy.code,
        title=copy.title,
        display_message=copy.display_message,
        spoken_message=copy.spoken_message,
        recovery_action=copy.recovery_action,
        retryable=error.context.retryable,
        analysis_available=False,
        failed_role=_known_role(error.context.role),
        operation_id=(
            operation_id.strip()
            if operation_id is not None
            else error.context.operation_id
        ),
    )


def _copy_for(error: LLMError) -> _FailureCopy:
    if isinstance(error, LLMConfigurationError):
        return _CONFIGURATION_COPY
    if isinstance(error, LLMAuthenticationError):
        return _AUTHENTICATION_COPY
    if isinstance(error, LLMRateLimitError):
        return _RATE_LIMIT_COPY
    if isinstance(error, LLMProviderUnavailableError):
        return _PROVIDER_UNAVAILABLE_COPY
    if isinstance(error, LLMResponseValidationError):
        return _INVALID_RESPONSE_COPY
    return _UNKNOWN_COPY


def _known_role(role: str | None) -> LLMRole | None:
    if role is None:
        return None
    try:
        return LLMRole(role.strip().lower())
    except ValueError:
        return None
