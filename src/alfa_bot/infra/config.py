from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=PROJECT_ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    bot_token: str = Field(default="", repr=False)
    database_url: str = Field(default="", repr=False)
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    llm_timeout: float = Field(default=45, gt=0, le=180)
    knowledge_index: Path = PROJECT_ROOT / "private_data" / "index"
    article_url_template: str = "https://kurs.alfabank.ru/articles/{slug}/"

    def validate_runtime(self) -> None:
        missing = [
            name
            for name, value in (
                ("BOT_TOKEN", self.bot_token),
                ("DATABASE_URL", self.database_url),
                ("DEEPSEEK_API_KEY", self.deepseek_api_key.get_secret_value()),
            )
            if not value.strip()
        ]
        if missing:
            raise ValueError("Заполните .env: " + ", ".join(missing))


@lru_cache
def load_config() -> Settings:
    return Settings()
