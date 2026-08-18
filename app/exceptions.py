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