"""Chat model factory.

Four providers: OpenAI, Anthropic, OpenRouter (an OpenAI-compatible proxy in
front of many backends), and local Ollama. **All four are pluggable
extras** (`pip install aitana[openai]` / `aitana[ollama]` /
`aitana[anthropic]` / `aitana[openrouter]`, see `pyproject.toml`) -- none is
a base dependency, so installing bare `aitana` pulls in no provider SDK at
all. Every provider's package is imported lazily, inside the matching
`_import_chat_*` helper below, specifically so importing this module never
requires any of them to be installed; picking a provider whose package isn't
installed raises a clear `RuntimeError` naming the fix, not an opaque
`ImportError` from deep inside a request.
"""

import logging
import os
from typing import TYPE_CHECKING, Any

from langchain_core.language_models.chat_models import BaseChatModel

if TYPE_CHECKING:
    from langchain_anthropic import ChatAnthropic
    from langchain_ollama import ChatOllama
    from langchain_openai import ChatOpenAI
    from langchain_openrouter import ChatOpenRouter

logger = logging.getLogger(__name__)

# The three-level "how hard should it think" dial `get_chat_model`'s
# `reasoning_effort` accepts -- see its docstring for the per-provider
# mapping. `None` (the default everywhere this is threaded through, e.g.
# Settings.llm_reasoning_effort) means "leave the provider's own default
# reasoning behavior alone", not "low effort".
_REASONING_EFFORTS = ("low", "medium", "high")

# Anthropic's extended thinking wants an explicit *token budget*, not a named
# effort level -- there's no "low/medium/high" concept on their API, so this
# maps the same three-level dial onto conservative budgets. Chosen to sit
# comfortably inside every current Claude model's default `max_tokens`
# (`ChatAnthropic` fills that in from the model's own profile when unset --
# verified in langchain-anthropic 1.7.2's `set_default_max_tokens`), not
# tuned against real grading traffic -- revisit these numbers if a rubric's
# questions warrant deeper thinking than "high" currently allows.
_ANTHROPIC_THINKING_BUDGET_TOKENS = {"low": 1024, "medium": 4096, "high": 10000}


def _missing_extra_error(extra: str, package: str) -> RuntimeError:
    return RuntimeError(
        f"The {extra!r} provider needs the optional {package} package, which isn't "
        f"installed by default. Install it with: pip install 'aitana[{extra}]' "
        f"(or `uv sync --extra {extra}`)."
    )


def _import_chat_openai() -> "type[ChatOpenAI]":
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as exc:
        raise _missing_extra_error("openai", "langchain-openai") from exc
    return ChatOpenAI


def _import_chat_anthropic() -> "type[ChatAnthropic]":
    try:
        from langchain_anthropic import ChatAnthropic
    except ImportError as exc:
        raise _missing_extra_error("anthropic", "langchain-anthropic") from exc
    return ChatAnthropic


def _import_chat_ollama() -> "type[ChatOllama]":
    try:
        from langchain_ollama import ChatOllama
    except ImportError as exc:
        raise _missing_extra_error("ollama", "langchain-ollama") from exc
    return ChatOllama


def _import_chat_openrouter() -> "type[ChatOpenRouter]":
    try:
        from langchain_openrouter import ChatOpenRouter
    except ImportError as exc:
        raise _missing_extra_error("openrouter", "langchain-openrouter") from exc
    return ChatOpenRouter


def get_chat_model(
    provider: str,
    model: str,
    temperature: float = 0.0,
    reasoning_effort: str | None = None,
) -> BaseChatModel:
    """Build a chat model for the given provider.

    `reasoning_effort` (`"low"`/`"medium"`/`"high"`, or `None` -- the
    default, meaning "don't ask for reasoning at all") is a coarse,
    provider-agnostic dial. It exists because none of these four providers
    exposes its reasoning/"thinking" content unless the request explicitly
    asks for it, each in its own shape -- `grading.py`'s `GradeTrace.thinking`
    extraction can only capture what a provider actually decided to send
    back, so this is what turns reasoning *on* in the first place. Verified
    against each provider's actual installed client source (`langchain-openai`
    1.6.0, `langchain-anthropic` 1.7.2, `langchain-ollama` 1.1.0,
    `langchain-openrouter` 0.2.9), not guessed -- see AGENTS.md's
    "Reasoning/thinking capture across providers" for the write-up:

      - **openai**: Chat Completions (the default API mode) never exposes
        raw reasoning text at all, only the Responses API does, and only
        when both `reasoning={"effort": ..., "summary": "auto"}` and
        `output_version="responses/v1"` are set. `temperature` is passed
        through unchanged either way -- `ChatOpenAI`'s own
        `validate_temperature` already drops/normalizes it for the specific
        model families (o1, non-chat gpt-5) that reject a non-default value
        once reasoning is active, so this function doesn't special-case that.
      - **anthropic**: extended thinking is off unless `thinking={"type":
        "enabled", "budget_tokens": ...}` is set explicitly, and Anthropic's
        API rejects any `temperature` other than `1` once thinking is
        enabled -- unlike OpenAI, `ChatAnthropic` doesn't self-guard that for
        every model family (only a couple of specific ones), so this
        function overrides `temperature` to `1` itself whenever
        `reasoning_effort` is set, rather than passing through the caller's
        value and letting a live 400 be the first sign of the conflict.
      - **ollama**: a local reasoning-capable model (deepseek-r1, qwq, ...)
        either leaks `<think>` tags into the main response text, or -- if
        `reasoning=` is set -- returns it separately in
        `additional_kwargs["reasoning_content"]`. Unlike the others, Ollama
        also accepts `reasoning=True` (no graded effort) for a model with no
        notion of effort levels; passing one of this function's three named
        levels works for the models that do support graded effort and is
        otherwise just forwarded as-is (`ChatOllama` accepts any string).
      - **openrouter**: reasoning is off unless `reasoning={"effort": ...}`
        is set -- OpenRouter's own unified convention, the same shape
        regardless of which backend model it's actually proxying to.
    """
    provider = provider.lower()
    if reasoning_effort is not None and reasoning_effort not in _REASONING_EFFORTS:
        raise ValueError(f"reasoning_effort must be one of {_REASONING_EFFORTS} or None, got {reasoning_effort!r}")

    logger.info(
        "Setting up chat model: provider=%s model=%s temperature=%s reasoning_effort=%s",
        provider,
        model,
        temperature,
        reasoning_effort,
    )

    if provider == "openai":
        chat_openai_cls = _import_chat_openai()
        # Expects OPENAI_API_KEY in the environment (langchain-openai reads it directly).
        logger.debug("OPENAI_API_KEY set: %s", bool(os.environ.get("OPENAI_API_KEY")))
        if reasoning_effort is None:
            return chat_openai_cls(model=model, temperature=temperature)
        return chat_openai_cls(
            model=model,
            temperature=temperature,
            reasoning={"effort": reasoning_effort, "summary": "auto"},
            output_version="responses/v1",
        )

    if provider == "anthropic":
        chat_anthropic_cls = _import_chat_anthropic()
        # Expects ANTHROPIC_API_KEY in the environment (langchain-anthropic reads it directly).
        logger.debug("ANTHROPIC_API_KEY set: %s", bool(os.environ.get("ANTHROPIC_API_KEY")))
        if reasoning_effort is None:
            return chat_anthropic_cls(model=model, temperature=temperature)
        return chat_anthropic_cls(
            model=model,
            temperature=1,  # required by the API once `thinking` is enabled -- see docstring above
            thinking={"type": "enabled", "budget_tokens": _ANTHROPIC_THINKING_BUDGET_TOKENS[reasoning_effort]},
        )

    if provider == "ollama":
        chat_ollama_cls = _import_chat_ollama()
        base_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        logger.debug("Using Ollama base_url=%s", base_url)
        # `reasoning=None` (the default) is what ChatOllama itself already
        # defaults to, so this is a no-op change from before whenever
        # reasoning_effort isn't set -- no behavior change for existing
        # Ollama-based setups that never touch this parameter.
        return chat_ollama_cls(model=model, temperature=temperature, base_url=base_url, reasoning=reasoning_effort)

    if provider == "openrouter":
        chat_openrouter_cls = _import_chat_openrouter()
        api_key = os.environ.get("OPENROUTER_API_KEY")
        if not api_key:
            raise RuntimeError("OPENROUTER_API_KEY is not set")
        extra_kwargs: dict[str, Any] = {}
        if reasoning_effort is not None:
            extra_kwargs["reasoning"] = {"effort": reasoning_effort}
        return chat_openrouter_cls(model=model, temperature=temperature, api_key=api_key, **extra_kwargs)

    raise ValueError(f"Unknown provider {provider!r}. Use 'openai', 'anthropic', 'ollama', or 'openrouter'.")
