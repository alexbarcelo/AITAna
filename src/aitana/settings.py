"""Centralized app configuration, loaded from the environment / .env file."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # MongoDB
    mongo_uri: str = "mongodb://localhost:27017"
    mongo_db: str = "aitana"

    # MinIO / S3-compatible object storage
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "exam-pdfs"
    minio_secure: bool = False

    # Celery / Redis
    redis_url: str = "redis://localhost:6379/0"

    # LLM provider credentials (OPENAI_API_KEY, ANTHROPIC_API_KEY,
    # OPENROUTER_API_KEY, OLLAMA_BASE_URL) are read directly from the
    # environment by grading/llm.py, same as before -- see .env.example.

    # Which models grade is stored in the database (documents/llm_model.py,
    # managed from the UI). These two only *seed* the first default model on
    # API startup when no model exists yet (`seed_llm_models_from_env`), so
    # an existing .env keeps working after the upgrade -- once the
    # collection has any model, they're ignored.
    llm_provider: str | None = None
    llm_model: str | None = None
    # Seeds the seeded model's reasoning effort ("low"/"medium"/"high"),
    # same seed-only role as the two above -- reasoning effort is otherwise a
    # per-model setting (LlmModel.reasoning_effort).
    llm_reasoning_effort: str | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
