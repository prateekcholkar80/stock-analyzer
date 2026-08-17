from app.services.market_data import MarketDataService


def main():

    market_service = MarketDataService()

    market_service.initialize()

    response = market_service.get_historical_candles(
        exchange="NSE",
        symbol_token="2885",
        interval="ONE_DAY",
        from_date="2026-08-01 09:15",
        to_date="2026-08-17 15:30"
    )

    print(response)


if __name__ == "__main__":
    main()
