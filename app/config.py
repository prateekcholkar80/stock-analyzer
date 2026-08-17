import os
from dotenv import load_dotenv


load_dotenv()


class Settings:
    ANGEL_API_KEY = os.getenv("ANGEL_API_KEY")
    ANGEL_CLIENT_CODE = os.getenv("ANGEL_CLIENT_CODE")
    ANGEL_PIN = os.getenv("ANGEL_PIN")
    ANGEL_TOTP_SECRET = os.getenv("ANGEL_TOTP_SECRET")


settings = Settings()