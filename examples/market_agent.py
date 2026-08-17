from app.agents.market_agent import MarketAgent


def main():

    agent = MarketAgent()

    result = agent.get_stock_price(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ"
    )

    print(result)


if __name__ == "__main__":
    main()
