from app.angel.client import AngelOneClient
from app.services.market_data import MarketDataService


def main():
    market_service = MarketDataService(
        gateway=AngelOneClient()
    )
    market_service.initialize()

    quote = market_service.get_quote(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
    )

    print(quote.model_dump(mode="json"))


if __name__ == "__main__":
    main()