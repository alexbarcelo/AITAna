"""Celery application bound to Redis, used for the slow LLM grading calls."""

from celery import Celery
from dotenv import load_dotenv

from ..settings import get_settings

# Populate OPENAI_API_KEY / OPENROUTER_API_KEY / OLLAMA_BASE_URL for
# grading/llm.py, same as the old CLI's load_dotenv() call.
load_dotenv()

_settings = get_settings()

celery_app = Celery("aitana", broker=_settings.redis_url, backend=_settings.redis_url)
celery_app.conf.update(task_serializer="json", result_serializer="json", accept_content=["json"])
celery_app.autodiscover_tasks(["aitana.worker"])
