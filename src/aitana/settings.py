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

    # LLM provider credentials (OPENAI_API_KEY, OPENROUTER_API_KEY,
    # OLLAMA_BASE_URL) are read directly from the environment by
    # grading/llm.py, same as before -- see .env.example.

    # Default grading provider/model, used by the worker task.
    llm_provider: str = "openai"
    llm_model: str = "gpt-4o-mini"


@lru_cache
def get_settings() -> Settings:
    return Settings()
