from app.angel.client import AngelOneClient
from app.logging_config import configure_logging
from app.services.market_data import MarketDataService


def main():
    configure_logging()

    market_service = MarketDataService(
        gateway=AngelOneClient()
    )
    market_service.initialize()

    series = market_service.get_historical_series(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
        interval="ONE_DAY",
        from_date="2026-08-01 09:15",
        to_date="2026-08-17 15:30",
    )

    print(series.model_dump(mode="json"))


if __name__ == "__main__":
    main()
