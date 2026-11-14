from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, model_validator
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
    source_database_url: str = Field(default="", repr=False)
    knowledge_backend: Literal["pgvector", "legacy"] = "pgvector"
    knowledge_index: Path = PROJECT_ROOT / "private_data" / "index"
    ollama_base_url: str = "http://127.0.0.1:11434"
    llm_model: str = "qwen2.5:3b"
    llm_timeout: float = Field(default=90, gt=0, le=600)
    ollama_context_length: int = Field(default=8192, ge=4096, le=32768)
    embedding_model: str = "bge-m3:567m"
    embedding_timeout: float = Field(default=180, gt=0, le=900)
    chunk_size_tokens: int = Field(default=600, ge=50, le=1200)
    chunk_overlap_tokens: int = Field(default=50, ge=0)
    kb_score_threshold: float = Field(default=0.6, ge=-1, le=1)
    kb_catalog_score_threshold: float = Field(default=0.55, ge=-1, le=1)
    kb_top_k: int = Field(default=5, ge=1, le=5)
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    reminder_interval: float = Field(default=15, gt=0, le=300)

    article_url_template: str = "https://kurs.alfabank.ru/articles/{slug}/"
    document_url_template: str = "https://kurs.alfabank.ru/documents/{slug}/"
    glossary_url_template: str = "https://kurs.alfabank.ru/vocabulary/{slug}/"
    course_url_template: str = "https://kurs.alfabank.ru/courses/{slug}/"

    @model_validator(mode="after")
    def check_settings(self):
        if self.chunk_overlap_tokens >= self.chunk_size_tokens:
            raise ValueError("CHUNK_OVERLAP_TOKENS должен быть меньше CHUNK_SIZE_TOKENS")
        parsed = urlsplit(self.ollama_base_url)
        if (
            parsed.scheme != "http"
            or parsed.hostname
            not in {"127.0.0.1", "::1", "localhost", "ollama", "host.docker.internal"}
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "OLLAMA_BASE_URL должен указывать на локальный Ollama или сервис Compose"
            )
        if any(
            not name.strip() or ":cloud" in name.casefold() or name.endswith("-cloud")
            for name in (self.llm_model, self.embedding_model)
        ):
            raise ValueError("Выберите локальные модели для Ollama")
        return self

    @property
    def source_templates(self) -> dict:
        return {
            "article": self.article_url_template,
            "document": self.document_url_template,
            "glossary": self.glossary_url_template,
            "course": self.course_url_template,
        }

    def validate_runtime(self, *, telegram: bool = True) -> None:
        missing = []
        if telegram and not self.bot_token.strip():
            missing.append("BOT_TOKEN")
        if not self.database_url.strip():
            missing.append("DATABASE_URL")
        if missing:
            raise ValueError("Заполните .env: " + ", ".join(missing))


@lru_cache
def load_config() -> Settings:
    return Settings()
