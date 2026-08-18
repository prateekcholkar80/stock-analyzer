from collections.abc import Callable
from typing import Any

import pyotp

from app.config import Settings, get_settings
from app.exceptions import (
    AuthenticationError,
    ClientNotInitializedError,
    MarketDataError,
)
from app.gateways.market_data import MarketResponse


ClientFactory = Callable[..., Any]


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
            raise AuthenticationError(
                "Unable to authenticate with Angel One"
            ) from exc

        if not isinstance(response, dict) or not response.get("status"):
            raise AuthenticationError(
                "Angel One rejected the authentication request"
            )

        self.session = response
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

        try:
            return self.client.ltpData(
                exchange,
                symbol,
                symbol_token,
            )
        except Exception as exc:
            raise MarketDataError(
                f"Unable to retrieve market quote for {symbol}"
            ) from exc

    def get_historical_candles(
        self,
        exchange: str,
        symbol_token: str,
        interval: str,
        from_date: str,
        to_date: str,
    ) -> MarketResponse:
        self._require_session()

        params = {
            "exchange": exchange,
            "symboltoken": symbol_token,
            "interval": interval,
            "fromdate": from_date,
            "todate": to_date,
        }

        try:
            return self.client.getCandleData(params)
        except Exception as exc:
            raise MarketDataError(
                "Unable to retrieve historical market data"
            ) from exc