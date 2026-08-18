from app.logging_config import configure_logging
from app.services.market_data import MarketDataService


def main():
    configure_logging()

    raw_data = [
        [
            "2026-08-17T00:00:00+05:30",
            1314.0,
            1320.8,
            1298.1,
            1320.0,
            13090231,
        ]
    ]

    candles = MarketDataService.convert_to_candles(raw_data)

    print(candles[0])
    print(type(candles[0]))


if __name__ == "__main__":
    main()
