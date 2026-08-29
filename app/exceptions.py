from dataclasses import dataclass


class ApplicationError(Exception):
    """Base exception for expected application failures."""


class ConfigurationError(ApplicationError):
    """Raised when required application configuration is invalid."""


class ExternalServiceError(ApplicationError):
    """Raised when an external system cannot complete an operation."""


class AuthenticationError(ExternalServiceError):
    """Raised when authentication with an external system fails."""


class MarketDataError(ExternalServiceError):
    """Raised when market data cannot be retrieved or processed."""


class ClientNotInitializedError(MarketDataError):
    """Raised when a market operation is attempted before initialization."""


class DataValidationError(MarketDataError):
    """Raised when external market data does not match the expected format."""


class InvalidInstrumentError(MarketDataError):
    """Raised when an instrument or symbol cannot be resolved."""


class TechnicalAnalysisError(ApplicationError):
    """Base exception for technical-analysis failures."""


class InsufficientDataError(TechnicalAnalysisError):
    """Raised when an indicator does not have enough candle data."""


class IndicatorCalculationError(TechnicalAnalysisError):
    """Raised when an indicator provider cannot complete a calculation."""


class AgentSubmissionRejectedError(TechnicalAnalysisError):
    """Raised when Jarvis rejects an agent's workflow submission."""


class IntentRecognitionError(ApplicationError):
    """Raised when a user request cannot be mapped to a supported intent."""


class InstrumentResolutionError(ApplicationError):
    """Base failure for provider-neutral instrument resolution."""


class InstrumentNotFoundError(InstrumentResolutionError):
    """Raised when no instrument matches the requested identity."""


class AmbiguousInstrumentError(InstrumentResolutionError):
    """Raised when a request matches more than one instrument."""


class InstrumentMasterDownloadError(InstrumentResolutionError):
    """Raised when the external instrument catalog cannot be downloaded."""


class InstrumentMasterDataError(InstrumentResolutionError):
    """Raised when instrument-master data violates the expected contract."""


def _validated_optional_failure_identifier(
    field_name: str,
    value: str | None,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(f"LLM failure {field_name} must be a string")
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"LLM failure {field_name} must not be blank")
    return normalized


@dataclass(frozen=True, slots=True)
class LLMFailureContext:
    """Sanitized metadata safe to expose at application boundaries."""

    role: str | None = None
    provider: str | None = None
    model: str | None = None
    operation_id: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        for field_name in ("role", "provider", "model", "operation_id"):
            object.__setattr__(
                self,
                field_name,
                _validated_optional_failure_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )
        if not isinstance(self.retryable, bool):
            raise TypeError("LLM failure retryable must be a boolean")


class LLMError(ExternalServiceError):
    """Base exception for safe, classified LLM workflow failures."""

    default_retryable = False

    def __init__(
        self,
        message: str,
        *,
        role: str | None = None,
        provider: str | None = None,
        model: str | None = None,
        operation_id: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.context = LLMFailureContext(
            role=role,
            provider=provider,
            model=model,
            operation_id=operation_id,
            retryable=(
                self.default_retryable
                if retryable is None
                else retryable
            ),
        )


class LLMConfigurationError(LLMError):
    """Raised when mandatory LLM configuration is missing or invalid."""


class LLMAuthenticationError(LLMError):
    """Raised when an LLM provider rejects configured credentials."""


class LLMProviderUnavailableError(LLMError):
    """Raised when an LLM provider cannot currently be reached."""

    default_retryable = True


class LLMRateLimitError(LLMError):
    """Raised when an LLM provider throttles a request."""

    default_retryable = True


class LLMResponseValidationError(LLMError):
    """Raised when an LLM response cannot be parsed into the expected schema."""


@dataclass(frozen=True, slots=True)
class TTSFailureContext:
    """Sanitized metadata safe to expose at application boundaries."""

    provider: str | None = None
    voice: str | None = None
    operation_id: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        for field_name in ("provider", "voice", "operation_id"):
            object.__setattr__(
                self,
                field_name,
                _validated_optional_failure_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )
        if not isinstance(self.retryable, bool):
            raise TypeError("TTS failure retryable must be a boolean")


class TTSError(ExternalServiceError):
    """Base exception for safe, classified text-to-speech failures."""

    default_retryable = False

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        voice: str | None = None,
        operation_id: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.context = TTSFailureContext(
            provider=provider,
            voice=voice,
            operation_id=operation_id,
            retryable=(
                self.default_retryable
                if retryable is None
                else retryable
            ),
        )


class TTSConfigurationError(TTSError):
    """Raised when mandatory TTS configuration is missing or invalid."""


class TTSAuthenticationError(TTSError):
    """Raised when a TTS provider rejects configured credentials."""


class TTSProviderUnavailableError(TTSError):
    """Raised when a TTS provider cannot currently be reached."""

    default_retryable = True


class TTSSynthesisError(TTSError):
    """Raised when a TTS provider returns empty or invalid audio."""


@dataclass(frozen=True, slots=True)
class STTFailureContext:
    """Sanitized metadata safe to expose at application boundaries."""

    provider: str | None = None
    language_code: str | None = None
    operation_id: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        for field_name in ("provider", "language_code", "operation_id"):
            object.__setattr__(
                self,
                field_name,
                _validated_optional_failure_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )
        if not isinstance(self.retryable, bool):
            raise TypeError("STT failure retryable must be a boolean")


class STTError(ExternalServiceError):
    """Base exception for safe, classified speech-to-text failures."""

    default_retryable = False

    def __init__(
        self,
        message: str,
        *,
        provider: str | None = None,
        language_code: str | None = None,
        operation_id: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.context = STTFailureContext(
            provider=provider,
            language_code=language_code,
            operation_id=operation_id,
            retryable=(
                self.default_retryable
                if retryable is None
                else retryable
            ),
        )


class STTConfigurationError(STTError):
    """Raised when mandatory STT configuration is missing or invalid."""


class STTAuthenticationError(STTError):
    """Raised when an STT provider rejects configured credentials."""


class STTProviderUnavailableError(STTError):
    """Raised when an STT provider cannot currently be reached."""

    default_retryable = True


class STTTranscriptionError(STTError):
    """Raised when an STT provider returns a malformed response.

    Not raised for a genuinely empty/no-speech-detected result -- that is
    valid data (Transcription with an empty transcript), not a failure.
    """


def _validated_optional_fundamental_identifier(
    field_name: str,
    value: str | None,
) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise TypeError(
            f"fundamental failure {field_name} must be a string"
        )
    normalized = value.strip()
    if not normalized:
        raise ValueError(
            f"fundamental failure {field_name} must not be blank"
        )
    if len(normalized) > 160:
        raise ValueError(
            f"fundamental failure {field_name} must be bounded"
        )
    return normalized


@dataclass(frozen=True, slots=True)
class FundamentalGatewayFailureContext:
    """Sanitized provider metadata safe for logs and API boundaries."""

    capability: str | None = None
    provider: str | None = None
    provider_connection_id: str | None = None
    operation_id: str | None = None
    retryable: bool = False

    def __post_init__(self) -> None:
        for field_name in (
            "capability",
            "provider",
            "provider_connection_id",
            "operation_id",
        ):
            object.__setattr__(
                self,
                field_name,
                _validated_optional_fundamental_identifier(
                    field_name,
                    getattr(self, field_name),
                ),
            )
        if not isinstance(self.retryable, bool):
            raise TypeError(
                "fundamental failure retryable must be a boolean"
            )


class FundamentalGatewayError(ExternalServiceError):
    """Base error for safe, classified fundamental-provider failures."""

    default_retryable = False

    def __init__(
        self,
        message: str,
        *,
        capability: str | None = None,
        provider: str | None = None,
        provider_connection_id: str | None = None,
        operation_id: str | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.context = FundamentalGatewayFailureContext(
            capability=capability,
            provider=provider,
            provider_connection_id=provider_connection_id,
            operation_id=operation_id,
            retryable=(
                self.default_retryable
                if retryable is None
                else retryable
            ),
        )


class FundamentalGatewayConfigurationError(FundamentalGatewayError):
    """Raised when a fundamental adapter is absent or misconfigured."""


class FundamentalGatewayAuthenticationError(FundamentalGatewayError):
    """Raised when a provider rejects the user's scoped session."""


class FundamentalGatewayEntitlementError(FundamentalGatewayError):
    """Raised when the user's provider plan does not allow a capability."""


class FundamentalCapabilityUnavailableError(FundamentalGatewayError):
    """Raised when an adapter does not implement a requested capability."""


class FundamentalProviderUnavailableError(FundamentalGatewayError):
    """Raised when the provider cannot currently complete the request."""

    default_retryable = True


class FundamentalProviderRateLimitError(FundamentalGatewayError):
    """Raised when a fundamental provider throttles the scoped account."""

    default_retryable = True


class FundamentalResponseValidationError(FundamentalGatewayError):
    """Raised when untrusted provider data fails the evidence contract."""


class StorageError(ApplicationError):
    """Raised when a persistence adapter cannot complete an operation."""


class StorageConflictError(StorageError):
    """Raised when an immutable stored identifier has conflicting data."""


class WorkflowOperationError(ApplicationError):
    """Base failure for browser-session and asynchronous-operation state."""


class BrowserSessionNotFoundError(WorkflowOperationError):
    """Raised when a browser session does not exist."""


class BrowserOperationNotFoundError(WorkflowOperationError):
    """Raised when a browser operation does not exist."""


class BrowserOperationConflictError(WorkflowOperationError):
    """Raised when an operation violates identity or lifecycle constraints."""
