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


class LLMError(ExternalServiceError):
    """Raised when an LLM provider call cannot complete."""


class LLMResponseValidationError(LLMError):
    """Raised when an LLM response cannot be parsed into the expected schema."""


class StorageError(ApplicationError):
    """Raised when a persistence adapter cannot complete an operation."""


class StorageConflictError(StorageError):
    """Raised when an immutable stored identifier has conflicting data."""
