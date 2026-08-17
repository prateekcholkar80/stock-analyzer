from app.services.market_data import MarketDataService


def main():

    market_service = MarketDataService()

    market_service.initialize()

    response = market_service.get_ltp(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ"
    )

    print(response)


if __name__ == "__main__":
    main()
