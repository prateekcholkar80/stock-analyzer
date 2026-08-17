from app.services.market_data import MarketDataService


_market_service = None


def get_market_service():

    global _market_service

    if _market_service is None:
        _market_service = MarketDataService()
        _market_service.initialize()

    return _market_service


def get_current_price(
    exchange: str,
    symbol_token: str,
    symbol: str
):

    market_service = get_market_service()

    response = market_service.get_ltp(
        exchange,
        symbol_token,
        symbol
    )

    return response