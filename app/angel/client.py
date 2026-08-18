import pyotp
from SmartApi import SmartConnect

from app.config import Settings, get_settings
from app.exceptions import AuthenticationError


class AngelOneClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()

        self.client = SmartConnect(
            api_key=self.settings.angel_api_key.get_secret_value()
        )

        self.session = None

    def login(self):
        try:
            totp = pyotp.TOTP(
                self.settings.angel_totp_secret.get_secret_value()
            ).now()

            response = self.client.generateSession(
                self.settings.angel_client_code.get_secret_value(),
                self.settings.angel_pin.get_secret_value(),
                totp,
            )
        except Exception as exc:
            raise AuthenticationError(
                "Unable to authenticate with Angel One"
            ) from exc

        if not isinstance(response, dict) or not response.get("status"):
            raise AuthenticationError(
                "Angel One rejected the authentication request"
            )

        self.session = response
        return response