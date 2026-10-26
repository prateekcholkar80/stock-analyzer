from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from threading import RLock
from time import monotonic, perf_counter, sleep
from typing import Any

import pyotp

from app.config import Settings, get_settings
from app.exceptions import (
    AuthenticationError,
    ClientNotInitializedError,
    MarketDataError,
)
from app.gateways.market_data import MarketResponse
from app.logging_config import get_logger


ClientFactory = Callable[..., Any]
logger = get_logger(__name__)
_EXPIRED_SESSION_ERROR_CODES = frozenset({"AG8001"})
_RATE_LIMIT_MARKERS = (
    "exceeding access rate",
    "exceeding rate limit",
    "rate limit exceeded",
    "too many requests",
)


@dataclass(frozen=True)
class AngelHistoricalRequestPolicy:
    """Published broker limits plus conservative bounded retry delays."""

    requests_per_second: int = 3
    requests_per_minute: int = 180
    requests_per_hour: int = 5_000
    retry_delays_seconds: tuple[float, ...] = (2.0, 5.0, 10.0)

    def __post_init__(self) -> None:
        limits = (
            self.requests_per_second,
            self.requests_per_minute,
            self.requests_per_hour,
        )
        if any(isinstance(value, bool) or value < 1 for value in limits):
            raise ValueError("historical request limits must be positive integers")
        if any(
            isinstance(delay, bool) or delay < 0
            for delay in self.retry_delays_seconds
        ):
            raise ValueError("historical retry delays must be non-negative")


class _HistoricalRequestLimiter:
    """Thread-safe sliding-window limiter for one broker client code."""

    def __init__(
        self,
        policy: AngelHistoricalRequestPolicy,
        *,
        clock: Callable[[], float],
        sleep_fn: Callable[[float], None],
    ) -> None:
        self._windows = (
            (1.0, policy.requests_per_second),
            (60.0, policy.requests_per_minute),
            (3_600.0, policy.requests_per_hour),
        )
        self._clock = clock
        self._sleep = sleep_fn
        self._request_times: deque[float] = deque()
        self._lock = RLock()

    def acquire(self) -> None:
        with self._lock:
            while True:
                now = self._clock()
                longest_window = self._windows[-1][0]
                while (
                    self._request_times
                    and self._request_times[0] <= now - longest_window
                ):
                    self._request_times.popleft()

                required_wait = 0.0
                request_times = tuple(self._request_times)
                for window_seconds, request_limit in self._windows:
                    in_window = tuple(
                        timestamp
                        for timestamp in request_times
                        if timestamp > now - window_seconds
                    )
                    if len(in_window) >= request_limit:
                        required_wait = max(
                            required_wait,
                            in_window[-request_limit] + window_seconds - now,
                        )

                if required_wait <= 0:
                    self._request_times.append(now)
                    return
                self._sleep(required_wait)


def _duration_ms(started_at: float) -> float:
    return round(
        (perf_counter() - started_at) * 1000,
        3,
    )


def _is_expired_session_response(response: object) -> bool:
    if not isinstance(response, dict) or response.get("status") is not False:
        return False
    error_code = response.get("errorCode")
    return (
        isinstance(error_code, str)
        and error_code.strip().upper() in _EXPIRED_SESSION_ERROR_CODES
    )


def _is_rate_limit_failure(value: object) -> bool:
    if isinstance(value, dict):
        message = " ".join(
            str(value.get(field, ""))
            for field in ("message", "errorCode", "errorcode")
        )
    else:
        message = str(value)
    normalized = message.casefold()
    return any(marker in normalized for marker in _RATE_LIMIT_MARKERS)


def _disable_unsafe_sdk_request_logging(sdk_module: object) -> None:
    """Stop SmartAPI from logging raw headers and authentication fields."""
    sdk_logger = getattr(sdk_module, "logger", None)
    if sdk_logger is not None and hasattr(sdk_logger, "disabled"):
        sdk_logger.disabled = True


class AngelOneClient:
    def __init__(
        self,
        settings: Settings | None = None,
        client_factory: ClientFactory | None = None,
        historical_request_policy: AngelHistoricalRequestPolicy | None = None,
        *,
        clock: Callable[[], float] = monotonic,
        sleep_fn: Callable[[float], None] = sleep,
    ):
        self.settings = settings or get_settings()

        if client_factory is None:
            from SmartApi import SmartConnect
            from SmartApi import smartConnect as smart_connect_module

            _disable_unsafe_sdk_request_logging(smart_connect_module)
            client_factory = SmartConnect

        self.client = client_factory(
            api_key=self.settings.angel_api_key.get_secret_value()
        )
        self.session: MarketResponse | None = None
        self._authentication_lock = RLock()
        self._session_generation = 0
        self._historical_request_policy = (
            historical_request_policy or AngelHistoricalRequestPolicy()
        )
        self._history_limiter = _HistoricalRequestLimiter(
            self._historical_request_policy,
            clock=clock,
            sleep_fn=sleep_fn,
        )
        self._sleep = sleep_fn

    def initialize(self) -> None:
        self.login()

    def login(self) -> MarketResponse:
        with self._authentication_lock:
            return self._login_locked()

    def _login_locked(self) -> MarketResponse:
        started_at = perf_counter()

        logger.info(
            "Angel One authentication started",
            extra={
                "event": "angel.authentication.started",
            },
        )

        try:
            totp = pyotp.TOTP(
                self.settings.angel_totp_secret.get_secret_value()
            ).now()

            response = self.client.generateSession(
                self.settings.angel_client_code.get_secret_value(),
                self.settings.angel_pin.get_secret_value(),
                totp,
            )
        except Exception as exc:
            logger.error(
                "Angel One authentication request failed",
                extra={
                    "event": "angel.authentication.failed",
                    "duration_ms": _duration_ms(started_at),
                    "error_type": type(exc).__name__,
                },
            )

            raise AuthenticationError(
                "Unable to authenticate with Angel One"
            ) from exc

        if not isinstance(response, dict) or not response.get("status"):
            logger.warning(
                "Angel One authentication was rejected",
                extra={
                    "event": "angel.authentication.rejected",
                    "duration_ms": _duration_ms(started_at),
                },
            )

            raise AuthenticationError(
                "Angel One rejected the authentication request"
            )

        self.session = response
        self._session_generation += 1

        logger.info(
            "Angel One authentication succeeded",
            extra={
                "event": "angel.authentication.succeeded",
                "duration_ms": _duration_ms(started_at),
            },
        )

        return response

    def _request_with_session_refresh(
        self,
        request: Callable[[], MarketResponse],
        *,
        operation: str,
    ) -> MarketResponse:
        """Retry one broker request after a synchronized session refresh."""
        self._require_session()
        observed_generation = self._session_generation
        response = request()
        if not _is_expired_session_response(response):
            return response

        logger.warning(
            "Angel One session expired; refreshing authentication",
            extra={
                "event": "angel.session.expired",
                "broker_operation": operation,
            },
        )
        with self._authentication_lock:
            if self._session_generation == observed_generation:
                self._login_locked()

        response = request()
        if _is_expired_session_response(response):
            logger.error(
                "Angel One session remained invalid after refresh",
                extra={
                    "event": "angel.session.refresh_failed",
                    "broker_operation": operation,
                },
            )
            raise MarketDataError(
                "Angel One session remained invalid after reauthentication"
            )

        logger.info(
            "Angel One request succeeded after session refresh",
            extra={
                "event": "angel.session.refreshed",
                "broker_operation": operation,
            },
        )
        return response

    def _require_session(self) -> None:
        if self.session is None:
            raise ClientNotInitializedError(
                "Angel One client is not authenticated"
            )

    def _request_historical_with_rate_policy(
        self,
        request: Callable[[], MarketResponse],
    ) -> MarketResponse:
        delays = self._historical_request_policy.retry_delays_seconds
        for attempt in range(len(delays) + 1):
            self._history_limiter.acquire()
            try:
                response = request()
            except Exception as exc:
                if not _is_rate_limit_failure(exc) or attempt >= len(delays):
                    raise
            else:
                if not _is_rate_limit_failure(response):
                    return response
                if attempt >= len(delays):
                    raise MarketDataError(
                        "Angel One historical rate-limit retry budget exhausted"
                    )

            delay = delays[attempt]
            logger.warning(
                "Angel One historical request rate limited; retrying",
                extra={
                    "event": "angel.history.rate_limited",
                    "retry_attempt": attempt + 1,
                    "retry_delay_seconds": delay,
                },
            )
            self._sleep(delay)

        raise MarketDataError(
            "Angel One historical rate-limit retry budget exhausted"
        )

    def get_ltp(
        self,
        exchange: str,
        symbol_token: str,
        symbol: str,
    ) -> MarketResponse:
        self._require_session()
        started_at = perf_counter()

        logger.debug(
            "Angel One quote request started",
            extra={
                "event": "angel.quote.started",
                "exchange": exchange,
                "symbol": symbol,
                "symbol_token": symbol_token,
            },
        )

        try:
            response = self._request_with_session_refresh(
                lambda: self.client.ltpData(
                    exchange,
                    symbol,
                    symbol_token,
                ),
                operation="quote",
            )
        except Exception as exc:
            logger.error(
                "Angel One quote request failed",
                extra={
                    "event": "angel.quote.failed",
                    "exchange": exchange,
                    "symbol": symbol,
                    "symbol_token": symbol_token,
                    "duration_ms": _duration_ms(started_at),
                    "error_type": type(exc).__name__,
                },
            )

            raise MarketDataError(
                f"Unable to retrieve market quote for {symbol}"
            ) from exc

        logger.debug(
            "Angel One quote request succeeded",
            extra={
                "event": "angel.quote.succeeded",
                "exchange": exchange,
                "symbol": symbol,
                "symbol_token": symbol_token,
                "duration_ms": _duration_ms(started_at),
            },
        )

        return response

    def get_historical_candles(
        self,
        exchange: str,
        symbol_token: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> MarketResponse:
        self._require_session()
        started_at = perf_counter()

        logger.debug(
            "Angel One historical request started",
            extra={
                "event": "angel.history.started",
                "exchange": exchange,
                "symbol_token": symbol_token,
                "interval": interval,
                "from_date": from_date,
                "to_date": to_date,
            },
        )

        params = {
            "exchange": exchange,
            "symboltoken": symbol_token,
            "interval": interval,
            "fromdate": from_date,
            "todate": to_date,
        }

        try:
            response = self._request_with_session_refresh(
                lambda: self._request_historical_with_rate_policy(
                    lambda: self.client.getCandleData(params)
                ),
                operation="historical_candles",
            )
        except Exception as exc:
            logger.error(
                "Angel One historical request failed",
                extra={
                    "event": "angel.history.failed",
                    "exchange": exchange,
                    "symbol_token": symbol_token,
                    "interval": interval,
                    "duration_ms": _duration_ms(started_at),
                    "error_type": type(exc).__name__,
                },
            )

            raise MarketDataError(
                "Unable to retrieve historical market data"
            ) from exc

        logger.debug(
            "Angel One historical request succeeded",
            extra={
                "event": "angel.history.succeeded",
                "exchange": exchange,
                "symbol_token": symbol_token,
                "interval": interval,
                "duration_ms": _duration_ms(started_at),
            },
        )

        return response
