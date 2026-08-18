import os
from collections.abc import Mapping
from functools import lru_cache

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator


load_dotenv()


class Settings(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="ignore",
        populate_by_name=True,
    )

    angel_api_key: SecretStr = Field(alias="ANGEL_API_KEY")
    angel_client_code: SecretStr = Field(alias="ANGEL_CLIENT_CODE")
    angel_pin: SecretStr = Field(alias="ANGEL_PIN")
    angel_totp_secret: SecretStr = Field(alias="ANGEL_TOTP_SECRET")

    @field_validator(
        "angel_api_key",
        "angel_client_code",
        "angel_pin",
        "angel_totp_secret",
    )
    @classmethod
    def reject_blank_secrets(cls, value: SecretStr) -> SecretStr:
        secret = value.get_secret_value().strip()

        if not secret:
            raise ValueError("configuration value must not be empty")

        return SecretStr(secret)

    @classmethod
    def from_environment(
        cls,
        environment: Mapping[str, str] | None = None,
    ) -> "Settings":
        source = environment if environment is not None else os.environ
        return cls.model_validate(source)


@lru_cache
def get_settings() -> Settings:
    return Settings.from_environment()