from collections.abc import Callable
from time import perf_counter
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


def _duration_ms(started_at: float) -> float:
    return round(
        (perf_counter() - started_at) * 1000,
        3,
    )


class AngelOneClient:
    def __init__(
        self,
        settings: Settings | None = None,
        client_factory: ClientFactory | None = None,
    ):
        self.settings = settings or get_settings()

        if client_factory is None:
            from SmartApi import SmartConnect

            client_factory = SmartConnect

        self.client = client_factory(
            api_key=self.settings.angel_api_key.get_secret_value()
        )
        self.session: MarketResponse | None = None

    def initialize(self) -> None:
        self.login()

    def login(self) -> MarketResponse:
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

        logger.info(
            "Angel One authentication succeeded",
            extra={
                "event": "angel.authentication.succeeded",
                "duration_ms": _duration_ms(started_at),
            },
        )

        return response

    def _require_session(self) -> None:
        if self.session is None:
            raise ClientNotInitializedError(
                "Angel One client is not authenticated"
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
            response = self.client.ltpData(
                exchange,
                symbol,
                symbol_token,
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
            response = self.client.getCandleData(params)
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
