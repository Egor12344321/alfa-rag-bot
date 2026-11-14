from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: Literal["local", "test", "demo", "production"] = "local"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    database_url: SecretStr = SecretStr(
        "postgresql+psycopg://alfa:alfa_local_only@localhost:5432/assistant_db"
    )
    source_database_url: SecretStr | None = None
    telegram_bot_token: SecretStr | None = None
    telegram_webhook_secret: SecretStr | None = None
    llm_provider: str = "unconfigured"
    llm_model: str = ""
    llm_api_key: SecretStr | None = None
    embedding_model: str = "intfloat/multilingual-e5-small"
    default_timezone: str = "Europe/Moscow"

    @field_validator(
        "source_database_url",
        "telegram_bot_token",
        "telegram_webhook_secret",
        "llm_api_key",
        mode="before",
    )
    @classmethod
    def blank_secret(cls, value):
        return None if value == "" else value

    @field_validator("database_url", "source_database_url")
    @classmethod
    def postgres_url(cls, value: SecretStr | None):
        if value is not None:
            try:
                parsed = make_url(value.get_secret_value())
            except Exception:
                raise ValueError("Ожидается PostgreSQL URL с драйвером psycopg") from None
            if parsed.drivername != "postgresql+psycopg" or not parsed.database:
                raise ValueError("Ожидается postgresql+psycopg URL с именем БД")
        return value

    @field_validator("default_timezone")
    @classmethod
    def timezone_exists(cls, value: str):
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError:
            raise ValueError("Ожидается существующий IANA timezone") from None
        return value
