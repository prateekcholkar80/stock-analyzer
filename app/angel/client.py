import pyotp
from SmartApi import SmartConnect

from app.config import Settings, get_settings


class AngelOneClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

        self.client = SmartConnect(
            api_key=self.settings.angel_api_key.get_secret_value()
        )

        self.session = None

    def login(self):
        totp = pyotp.TOTP(
            self.settings.angel_totp_secret.get_secret_value()
        ).now()

        response = self.client.generateSession(
            self.settings.angel_client_code.get_secret_value(),
            self.settings.angel_pin.get_secret_value(),
            totp,
        )

        if not response.get("status"):
            raise Exception(
                f"Angel One login failed: {response}"
            )

        self.session = response
        return response