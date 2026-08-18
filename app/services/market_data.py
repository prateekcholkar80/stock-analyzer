from datetime import datetime

from pydantic import ValidationError

from app.angel.client import AngelOneClient
from app.exceptions import (
    ClientNotInitializedError,
    DataValidationError,
    MarketDataError,
)
from app.models.market import Candle


class MarketDataService:
    def __init__(self):
        self.angel_client = AngelOneClient()
        self.client = None

    def initialize(self):
        self.angel_client.login()
        self.client = self.angel_client.client

    def _require_client(self):
        if self.client is None:
            raise ClientNotInitializedError(
                "Market data client is not initialized"
            )

        return self.client

    def get_ltp(
        self,
        exchange,
        symbol_token,
        symbol,
    ):
        client = self._require_client()

        try:
            return client.ltpData(
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
        exchange,
        symbol_token,
        interval,
        from_date,
        to_date,
    ):
        client = self._require_client()

        params = {
            "exchange": exchange,
            "symboltoken": symbol_token,
            "interval": interval,
            "fromdate": from_date,
            "todate": to_date,
        }

        try:
            return client.getCandleData(params)
        except Exception as exc:
            raise MarketDataError(
                "Unable to retrieve historical market data"
            ) from exc

    def convert_to_candles(self, candle_data):
        try:
            return [
                Candle(
                    timestamp=datetime.fromisoformat(item[0]),
                    open=item[1],
                    high=item[2],
                    low=item[3],
                    close=item[4],
                    volume=item[5],
                )
                for item in candle_data
            ]
        except (
            IndexError,
            KeyError,
            TypeError,
            ValueError,
            ValidationError,
        ) as exc:
            raise DataValidationError(
                "Historical candle data has an invalid format"
            ) from exc