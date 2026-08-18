from app.agents.market_agent import MarketAgent
from app.logging_config import configure_logging


def main():
    configure_logging()

    agent = MarketAgent()

    quote = agent.get_stock_price(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
    )

    print(quote.model_dump(mode="json"))


if __name__ == "__main__":
    main()
