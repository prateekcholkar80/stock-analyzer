from app.angel.client import AngelOneClient


class MarketDataService:

    def __init__(self):
        self.angel_client = AngelOneClient()
        self.client = None

    def initialize(self):
        self.angel_client.login()
        self.client = self.angel_client.client

    def get_ltp(self, exchange, symbol_token, symbol):

        if self.client is None:
            raise Exception(
                "Client not initialized. Call initialize() first."
            )

        response = self.client.ltpData(
            exchange,
            symbol,
            symbol_token
        )

        return response