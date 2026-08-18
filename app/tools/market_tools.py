from app.services.market_data import MarketDataService


_market_service: MarketDataService | None = None


def _create_market_service() -> MarketDataService:
    from app.angel.client import AngelOneClient

    gateway = AngelOneClient()
    service = MarketDataService(gateway=gateway)
    service.initialize()

    return service


def get_market_service() -> MarketDataService:
    global _market_service

    if _market_service is None:
        _market_service = _create_market_service()

    return _market_service


def get_current_price(
    exchange: str,
    symbol_token: str,
    symbol: str,
):
    market_service = get_market_service()

    return market_service.get_ltp(
        exchange=exchange,
        symbol_token=symbol_token,
        symbol=symbol,
    )