"""Construction-only coverage for grading/llm.py's get_chat_model -- no live
API calls (no keys available here, see AGENTS.md), just: does the right
class get built, with the right kwargs, for each of the four providers, and
does a missing optional provider package fail with a clear RuntimeError
instead of a bare ImportError.

Every provider's package is present in this project's own dev dependency
group (pyproject.toml's `dev` -> `aitana[openai,ollama,anthropic,openrouter]`)
specifically so this file can exercise all four construction paths for real,
even though none of the four is a base dependency of the installed package
itself.
"""

import sys

import pytest

from aitana.grading.llm import get_chat_model


def test_unknown_provider_raises_value_error():
    with pytest.raises(ValueError, match="Unknown provider"):
        get_chat_model("bogus", "some-model")


def test_invalid_reasoning_effort_raises_value_error():
    with pytest.raises(ValueError, match="reasoning_effort"):
        get_chat_model("openai", "gpt-4o-mini", reasoning_effort="extreme")


def test_openai_plain(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    model = get_chat_model("openai", "gpt-4o-mini")
    assert type(model).__name__ == "ChatOpenAI"
    assert model.temperature == 0.0
    assert model.reasoning is None


def test_openai_with_reasoning_uses_responses_api(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    model = get_chat_model("openai", "gpt-5", reasoning_effort="high")
    assert model.reasoning == {"effort": "high", "summary": "auto"}
    assert model.output_version == "responses/v1"


def test_ollama_plain(monkeypatch):
    model = get_chat_model("ollama", "llama3")
    assert type(model).__name__ == "ChatOllama"
    assert model.reasoning is None


def test_ollama_with_reasoning_passes_effort_through(monkeypatch):
    model = get_chat_model("ollama", "deepseek-r1", reasoning_effort="medium")
    assert model.reasoning == "medium"


def test_anthropic_plain(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    model = get_chat_model("anthropic", "claude-sonnet-5")
    assert type(model).__name__ == "ChatAnthropic"
    assert model.temperature == 0.0
    assert model.thinking is None


def test_anthropic_with_reasoning_enables_thinking_and_forces_temperature_1(monkeypatch):
    """Extended thinking requires temperature=1 -- the API rejects anything
    else once `thinking` is enabled, and langchain-anthropic doesn't
    self-guard that for every model family, so get_chat_model does."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    model = get_chat_model("anthropic", "claude-sonnet-5", reasoning_effort="low")
    assert model.temperature == 1
    assert model.thinking == {"type": "enabled", "budget_tokens": 1024}


def test_openrouter_requires_api_key(monkeypatch):
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    with pytest.raises(RuntimeError, match="OPENROUTER_API_KEY"):
        get_chat_model("openrouter", "z-ai/glm-4.5")


def test_openrouter_plain(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    model = get_chat_model("openrouter", "z-ai/glm-4.5")
    assert type(model).__name__ == "ChatOpenRouter"
    assert model.reasoning is None


def test_openrouter_with_reasoning(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    model = get_chat_model("openrouter", "z-ai/glm-4.5", reasoning_effort="high")
    assert model.reasoning == {"effort": "high"}


@pytest.mark.parametrize(
    ("provider", "module_name", "extra"),
    [
        ("openai", "langchain_openai", "openai"),
        ("anthropic", "langchain_anthropic", "anthropic"),
        ("ollama", "langchain_ollama", "ollama"),
        ("openrouter", "langchain_openrouter", "openrouter"),
    ],
)
def test_missing_provider_package_raises_actionable_runtime_error(monkeypatch, provider, module_name, extra):
    """Simulates the package not being installed at all (regardless of
    whether it actually is in this dev environment) by forcing the import to
    fail -- `sys.modules[name] = None` makes Python's own import machinery
    raise ImportError for `import <name>`/`from <name> import ...`,
    independent of what's actually on disk."""
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    monkeypatch.setitem(sys.modules, module_name, None)

    with pytest.raises(RuntimeError, match=rf"pip install 'aitana\[{extra}\]'"):
        get_chat_model(provider, "some-model")
