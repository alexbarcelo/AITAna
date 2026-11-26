"""Centralized app configuration, loaded from the environment / .env file."""

from functools import lru_cache

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # PostgreSQL. Any `postgres://`/`postgresql://` URI works (e.g. the
    # `uri` key of a CloudNativePG app secret, used verbatim): it's rewritten
    # to the asyncpg driver.
    database_url: str = "postgresql+asyncpg://aitana:aitana@localhost:5432/aitana"

    # MinIO / S3-compatible object storage
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "exam-pdfs"
    minio_secure: bool = False

    # Celery / Redis
    redis_url: str = "redis://localhost:6379/0"

    # Office document (.docx) -> PDF conversion via a Gotenberg service
    # (LibreOffice). Unset/blank falls back to PyMuPDF's own, much rougher
    # conversion -- see generic_document.document_to_pdf.
    gotenberg_url: str | None = None
    gotenberg_timeout: float = 60.0

    # LLM provider credentials (OPENAI_API_KEY, ANTHROPIC_API_KEY,
    # OPENROUTER_API_KEY, OLLAMA_BASE_URL) are read directly from the
    # environment by grading/llm.py -- see .env.example.

    # Which models grade is stored in the database (`LlmModel`, managed from
    # the UI). These two only *seed* the first default model on API startup
    # when no model exists yet (`seed_llm_models_from_env`) -- once any model
    # exists, they're ignored.
    llm_provider: str | None = None
    llm_model: str | None = None
    # Seeds the seeded model's reasoning effort ("low"/"medium"/"high"),
    # same seed-only role as the two above -- reasoning effort is otherwise a
    # per-model setting (LlmModel.reasoning_effort).
    llm_reasoning_effort: str | None = None

    # How many of one submission's questions the worker grades at once
    # (concurrent LLM calls). Total in flight is this x the Celery worker's
    # --concurrency. 1 = one question at a time (e.g. a local Ollama).
    grading_concurrency: int = Field(default=4, ge=1)

    @field_validator("database_url")
    @classmethod
    def _asyncpg_driver(cls, value: str) -> str:
        for prefix in ("postgresql+asyncpg://", "postgresql://", "postgres://"):
            if value.startswith(prefix):
                return "postgresql+asyncpg://" + value[len(prefix) :]
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()
