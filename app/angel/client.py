import pyotp
from SmartApi import SmartConnect

from app.config import settings


class AngelOneClient:

    def __init__(self):
        self.client = SmartConnect(
            api_key=settings.ANGEL_API_KEY
        )

        self.session = None

    def login(self):

        totp = pyotp.TOTP(
            settings.ANGEL_TOTP_SECRET
        ).now()

        response = self.client.generateSession(
            settings.ANGEL_CLIENT_CODE,
            settings.ANGEL_PIN,
            totp,
        )

        if not response.get("status"):
            raise Exception(
                f"Angel One login failed: {response}"
            )

        self.session = response

        return response