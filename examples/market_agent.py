from app.agents.market_agent import MarketAgent


def main():
    agent = MarketAgent()

    quote = agent.get_stock_price(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
    )

    print(quote.model_dump(mode="json"))


if __name__ == "__main__":
    main()