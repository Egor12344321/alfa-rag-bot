"""Все параметры приложения читаются из .env в корне проекта."""

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL

ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )
    bot_token: SecretStr = SecretStr("")
    database_url: str = Field(default="", repr=False)
    # Compose передаёт поля отдельно, чтобы спецсимволы пароля не меняли hostname.
    db_host: str | None = None
    db_port: int = Field(default=5432, ge=1, le=65535)
    postgres_user: str = "alfa_bot"
    postgres_password: SecretStr = SecretStr("")
    postgres_db: str = "alfa_bot"
    source_database_url: str = Field(default="", repr=False)
    source_db_host: str | None = None
    source_db_port: int = Field(default=5432, ge=1, le=65535)
    source_db_user: str = "material_reader"
    source_db_password: SecretStr = SecretStr("")
    source_db_name: str = "materials"
    source_schema: str = "public"
    source_mapping_path: Path = ROOT / "config" / "source_mapping.yaml"
    deepseek_api_key: SecretStr = SecretStr("")
    deepseek_base_url: str = "https://api.deepseek.com"
    deepseek_model: str = "deepseek-flash"
    llm_timeout: float = Field(default=60, gt=0, le=240)
    llm_max_tokens: int = Field(default=4000, ge=256, le=16000)
    llm_retries: int = Field(default=2, ge=0, le=3)
    embedding_model: str = "BAAI/bge-m3"
    embedding_revision: str = "5617a9f61b028005a4858fdac845db406aefb181"
    embedding_dimension: int = Field(default=1024, gt=0, le=8192)
    embedding_device: str = "cpu"
    embedding_batch_size: int = Field(default=8, ge=1, le=128)
    embedding_threads: int = Field(default=4, ge=1, le=64)
    embedding_cache_dir: Path = ROOT / "private_data" / "models"
    chunk_tokens: int = Field(default=650, ge=100, le=2000)
    chunk_overlap: int = Field(default=50, ge=0, le=300)
    top_k: int = Field(default=5, ge=1, le=20)
    score_threshold: float = Field(default=0.6, ge=-1, le=1)
    recommendation_threshold: float = Field(default=0.65, ge=-1, le=1)
    recommendation_limit: int = Field(default=1, ge=0, le=3)
    history_limit: int = Field(default=10, ge=2, le=30)
    onboarding_enabled: bool = False
    default_timezone: str = "Europe/Moscow"
    reminder_interval: float = Field(default=15, gt=0)
    draft_ttl_minutes: int = Field(default=30, ge=1, le=1440)
    log_level: str = "INFO"
    article_url_template: str = "https://kurs.alfabank.ru/articles/{slug}/"
    document_url_template: str = "https://kurs.alfabank.ru/documents/{slug}/"
    glossary_url_template: str = "https://kurs.alfabank.ru/vocabulary/{slug}/"
    course_url_template: str = "https://kurs.alfabank.ru/courses/{slug}/"
    # Автоматическая внешняя трассировка отключается в bootstrap.
    langchain_tracing_v2: bool = False

    @field_validator(
        "article_url_template",
        "document_url_template",
        "glossary_url_template",
        "course_url_template",
        mode="before",
    )
    @classmethod
    def default_url_template(cls, value, info):
        # Старый .env может не содержать новых ключей, Compose передаст пустую строку.
        if value is None or (isinstance(value, str) and not value.strip()):
            return cls.model_fields[info.field_name].default
        return value

    @model_validator(mode="after")
    def overlap_smaller_than_chunk(self):
        if self.db_host:
            self.database_url = URL.create(
                "postgresql+asyncpg",
                username=self.postgres_user,
                password=self.postgres_password.get_secret_value(),
                host=self.db_host,
                port=self.db_port,
                database=self.postgres_db,
            ).render_as_string(hide_password=False)
        if self.source_db_host:
            self.source_database_url = URL.create(
                "postgresql+asyncpg",
                username=self.source_db_user,
                password=self.source_db_password.get_secret_value(),
                host=self.source_db_host,
                port=self.source_db_port,
                database=self.source_db_name,
            ).render_as_string(hide_password=False)
        if self.chunk_overlap >= self.chunk_tokens:
            raise ValueError("CHUNK_OVERLAP должен быть меньше CHUNK_TOKENS")
        return self

    def validate_runtime(self):
        for name, value in (
            ("DATABASE_URL", self.database_url),
            ("BOT_TOKEN", self.bot_token.get_secret_value()),
            ("DEEPSEEK_API_KEY", self.deepseek_api_key.get_secret_value()),
        ):
            if not value.strip():
                raise ValueError(f"Заполните {name} в .env")


@lru_cache
def load_config() -> Settings:
    return Settings()
