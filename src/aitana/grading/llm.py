"""Chat model factory. Deliberately NOT using Anthropic -- OpenAI, OpenRouter
(OpenAI-compatible API) and local Ollama models only, per project brief.
"""

import logging
import os

from langchain_core.language_models.chat_models import BaseChatModel
from langchain_ollama import ChatOllama
from langchain_openai import ChatOpenAI

logger = logging.getLogger(__name__)

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


def get_chat_model(provider: str, model: str, temperature: float = 0.0) -> BaseChatModel:
    provider = provider.lower()
    logger.info("Setting up chat model: provider=%s model=%s temperature=%s", provider, model, temperature)

    if provider == "openai":
        # Expects OPENAI_API_KEY in the environment (langchain-openai reads it directly).
        logger.debug("OPENAI_API_KEY set: %s", bool(os.environ.get("OPENAI_API_KEY")))
        return ChatOpenAI(model=model, temperature=temperature)

    if provider == "openrouter":
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        # OpenRouter speaks the OpenAI API, so ChatOpenAI + a different base_url is enough.
        logger.debug("Using OpenRouter base_url=%s", OPENROUTER_BASE_URL)
        return ChatOpenAI(model=model, temperature=temperature, base_url=OPENROUTER_BASE_URL, api_key=api_key)

    if provider == "ollama":
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        logger.debug("Using Ollama base_url=%s", base_url)
        return ChatOllama(model=model, temperature=temperature, base_url=base_url)

    raise ValueError(f"Unknown provider {provider!r}. Use 'openai', 'openrouter', or 'ollama'.")
