from app.agents.market_agent import MarketAgent
from app.runtime import run_entrypoint


def main():
    agent = MarketAgent()

    quote = agent.get_stock_price(
        exchange="NSE",
        symbol_token="2885",
        symbol="RELIANCE-EQ",
    )

    print(quote.model_dump(mode="json"))


if __name__ == "__main__":
    raise SystemExit(
        run_entrypoint(
            main,
            logger_name="examples.market_agent",
        )
    )
