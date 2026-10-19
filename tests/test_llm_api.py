"""The /llm router (providers, model registry, default/favorite flags) and
how grading endpoints pick a model. The `client` fixture already seeds one
default model (openai/gpt-4o-mini, with a fake OPENAI_API_KEY) -- see
conftest.py."""

import sys

from aitana.api.routers import llm as llm_router
from aitana.documents import Course, LlmModel, Rubric, UserSettings
from aitana.grading.llm import available_providers, provider_unavailable_reason
from aitana.grading.models import Question
from aitana.settings import Settings


def _clear_provider_env(monkeypatch):
    for var in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "OLLAMA_BASE_URL"):
        monkeypatch.delenv(var, raising=False)


# ---- Provider availability ----


def test_provider_needs_its_env_var(monkeypatch):
    _clear_provider_env(monkeypatch)
    assert available_providers() == []
    assert provider_unavailable_reason("anthropic") == "ANTHROPIC_API_KEY is not set"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "fake")
    monkeypatch.setenv("OLLAMA_BASE_URL", "http://localhost:11434")
    assert available_providers() == ["anthropic", "ollama"]


def test_provider_needs_its_package(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "fake")
    monkeypatch.setitem(sys.modules, "langchain_openrouter", None)
    assert "package not installed" in provider_unavailable_reason("openrouter")
    assert "openrouter" not in available_providers()


def test_unknown_provider_is_unavailable():
    assert provider_unavailable_reason("bogus") == "unknown provider 'bogus'"


async def test_list_providers(client, monkeypatch):
    _clear_provider_env(monkeypatch)
    monkeypatch.setenv("OPENAI_API_KEY", "fake")
    resp = await client.get("/llm/providers")
    assert resp.status_code == 200
    by_name = {p["name"]: p for p in resp.json()}
    assert set(by_name) == {"openai", "anthropic", "ollama", "openrouter"}
    assert by_name["openai"] == {"name": "openai", "available": True, "unavailable_reason": None}
    assert by_name["ollama"]["available"] is False
    assert by_name["ollama"]["unavailable_reason"] == "OLLAMA_BASE_URL is not set"


# ---- Model registry ----


async def test_list_models_includes_flags(client):
    resp = await client.get("/llm/models")
    assert resp.status_code == 200
    [model] = resp.json()
    assert model["id"] == client.default_llm_model_id
    assert model["provider"] == "openai"
    assert model["is_default"] is True
    assert model["is_favorite"] is False
    assert model["available"] is True


async def test_list_models_flags_unavailable_provider(client, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY")
    [model] = (await client.get("/llm/models")).json()
    assert model["available"] is False
    assert model["unavailable_reason"] == "OPENAI_API_KEY is not set"


async def test_create_model(client):
    resp = await client.post("/llm/models", json={"provider": "OpenAI", "model": " gpt-5 ", "is_favorite": True})
    assert resp.status_code == 201
    body = resp.json()
    assert (body["provider"], body["model"]) == ("openai", "gpt-5")
    assert body["is_default"] is False  # a default already exists
    assert body["is_favorite"] is True

    settings = await UserSettings.find_one()
    assert [str(i) for i in settings.favorite_model_ids] == [body["id"]]


async def test_create_model_duplicate_conflicts(client):
    resp = await client.post("/llm/models", json={"provider": "openai", "model": "gpt-4o-mini"})
    assert resp.status_code == 409
    # A different reasoning effort doesn't make it a different model -- edit the existing one instead.
    resp = await client.post("/llm/models", json={"provider": "openai", "model": "gpt-4o-mini", "reasoning_effort": "low"})
    assert resp.status_code == 409


async def test_create_model_unavailable_provider_rejected(client, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    resp = await client.post("/llm/models", json={"provider": "anthropic", "model": "claude-sonnet-5"})
    assert resp.status_code == 422
    assert "ANTHROPIC_API_KEY" in resp.json()["detail"]


async def test_create_model_empty_identifier_rejected(client):
    resp = await client.post("/llm/models", json={"provider": "openai", "model": "  "})
    assert resp.status_code == 422


async def test_first_model_becomes_default(client):
    await LlmModel.find_all().delete()
    resp = await client.post("/llm/models", json={"provider": "openai", "model": "gpt-5"})
    assert resp.json()["is_default"] is True


async def test_create_model_as_default_replaces_previous(client):
    resp = await client.post("/llm/models", json={"provider": "openai", "model": "gpt-5", "is_default": True})
    assert resp.json()["is_default"] is True
    defaults = await LlmModel.find(LlmModel.is_default == True).to_list()  # noqa: E712
    assert [str(m.id) for m in defaults] == [resp.json()["id"]]


async def test_set_default_model(client):
    new_id = (await client.post("/llm/models", json={"provider": "openai", "model": "gpt-5"})).json()["id"]
    resp = await client.put(f"/llm/models/{new_id}/default")
    assert resp.status_code == 200
    models = {m["id"]: m for m in (await client.get("/llm/models")).json()}
    assert models[new_id]["is_default"] is True
    assert models[client.default_llm_model_id]["is_default"] is False


async def test_favorite_toggle(client):
    model_id = client.default_llm_model_id
    resp = await client.put(f"/llm/models/{model_id}/favorite")
    assert resp.json()["is_favorite"] is True
    # Idempotent: favoriting twice doesn't duplicate it.
    await client.put(f"/llm/models/{model_id}/favorite")
    assert len((await UserSettings.find_one()).favorite_model_ids) == 1

    resp = await client.delete(f"/llm/models/{model_id}/favorite")
    assert resp.json()["is_favorite"] is False
    assert (await UserSettings.find_one()).favorite_model_ids == []


async def test_favorite_unknown_model_404(client):
    resp = await client.put("/llm/models/000000000000000000000000/favorite")
    assert resp.status_code == 404


async def test_delete_model_removes_favorite(client):
    new_id = (await client.post("/llm/models", json={"provider": "openai", "model": "gpt-5", "is_favorite": True})).json()[
        "id"
    ]
    resp = await client.delete(f"/llm/models/{new_id}")
    assert resp.status_code == 204
    assert await LlmModel.get(new_id) is None
    assert (await UserSettings.find_one()).favorite_model_ids == []


async def test_delete_default_model_with_others_conflicts(client):
    await client.post("/llm/models", json={"provider": "openai", "model": "gpt-5"})
    resp = await client.delete(f"/llm/models/{client.default_llm_model_id}")
    assert resp.status_code == 409


async def test_delete_last_model_allowed(client):
    resp = await client.delete(f"/llm/models/{client.default_llm_model_id}")
    assert resp.status_code == 204


# ---- Seeding from the environment ----


async def test_seed_from_env_when_empty(mongo_db, monkeypatch):
    monkeypatch.setattr(llm_router, "get_settings", lambda: Settings(llm_provider="OpenRouter", llm_model="z-ai/glm-4.5"))
    await llm_router.seed_llm_models_from_env()
    [model] = await LlmModel.find_all().to_list()
    assert (model.provider, model.model, model.is_default) == ("openrouter", "z-ai/glm-4.5", True)

    # Idempotent: nothing more once any model exists.
    await llm_router.seed_llm_models_from_env()
    assert await LlmModel.find_all().count() == 1


async def test_seed_from_env_skipped_without_settings(mongo_db, monkeypatch):
    monkeypatch.setattr(llm_router, "get_settings", lambda: Settings(llm_provider=None, llm_model=None))
    await llm_router.seed_llm_models_from_env()
    assert await LlmModel.find_all().count() == 0


# ---- Grading endpoints pick a model ----


async def _make_submission(client) -> tuple[str, str]:
    student_id = (await client.post("/students", json={"student_id": "s1", "name": "Ada"})).json()["_id"]
    course_id = (await client.post("/courses", json={"name": "BDM"})).json()["_id"]
    edition_id = (await client.post("/editions", json={"name": "2026/27"})).json()["_id"]
    rubric = Rubric(
        slug="lab",
        title="Lab",
        course=await Course.get(course_id),
        questions=[Question(id="q1", title="Q1", question="?", rubric="R")],
    )
    await rubric.insert()
    resp = await client.post(
        "/submissions",
        data={"student_id": student_id, "rubric_id": str(rubric.id), "edition_id": edition_id},
        files={"file": ("a.pdf", b"%PDF", "application/pdf")},
    )
    assert resp.status_code == 202, resp.text
    return resp.json()["_id"], str(rubric.id)


async def test_grading_uses_default_model_when_none_given(client):
    await _make_submission(client)
    assert client.delayed_model_ids == [client.default_llm_model_id]


async def test_grading_uses_requested_model(client):
    submission_id, _ = await _make_submission(client)
    other_id = (await client.post("/llm/models", json={"provider": "openai", "model": "gpt-5"})).json()["id"]

    resp = await client.post(f"/submissions/{submission_id}/regrade", params={"model_id": other_id})
    assert resp.status_code == 202
    resp = await client.post("/submissions/regrade", params={"model_id": other_id}, json={"submission_ids": [submission_id]})
    assert resp.status_code == 202
    assert client.delayed_model_ids == [client.default_llm_model_id, other_id, other_id]


async def test_grading_unknown_model_404(client):
    submission_id, _ = await _make_submission(client)
    resp = await client.post(f"/submissions/{submission_id}/regrade", params={"model_id": "000000000000000000000000"})
    assert resp.status_code == 404


async def test_grading_without_default_422(client):
    submission_id, _ = await _make_submission(client)
    await LlmModel.find_all().delete()
    resp = await client.post(f"/submissions/{submission_id}/regrade")
    assert resp.status_code == 422
    assert "No default LLM model" in resp.json()["detail"]


async def test_grading_with_unavailable_model_422(client, monkeypatch):
    submission_id, _ = await _make_submission(client)
    monkeypatch.delenv("OPENAI_API_KEY")
    resp = await client.post(f"/submissions/{submission_id}/regrade")
    assert resp.status_code == 422
    assert "OPENAI_API_KEY" in resp.json()["detail"]


async def test_test_answer_uses_requested_model(client, monkeypatch):
    _, rubric_id = await _make_submission(client)
    other_id = (await client.post("/llm/models", json={"provider": "openai", "model": "gpt-5"})).json()["id"]
    seen = []

    def fake_get_chat_model(provider, model, **kwargs):
        seen.append((provider, model))
        raise RuntimeError("stop here")

    monkeypatch.setattr("aitana.api.routers.rubrics.get_chat_model", fake_get_chat_model)
    resp = await client.post(
        f"/rubrics/{rubric_id}/test-answer", params={"model_id": other_id}, json={"question_id": "q1", "answer": "x"}
    )
    assert resp.status_code == 502
    assert seen == [("openai", "gpt-5")]


# ---- Per-model reasoning effort ----


async def test_create_model_with_reasoning_effort(client):
    resp = await client.post("/llm/models", json={"provider": "openai", "model": "gpt-5", "reasoning_effort": "high"})
    assert resp.status_code == 201
    assert resp.json()["reasoning_effort"] == "high"


async def test_create_model_invalid_reasoning_effort_rejected(client):
    resp = await client.post("/llm/models", json={"provider": "openai", "model": "gpt-5", "reasoning_effort": "extreme"})
    assert resp.status_code == 422


async def test_update_reasoning_effort(client):
    model_id = client.default_llm_model_id
    resp = await client.patch(f"/llm/models/{model_id}", json={"reasoning_effort": "medium"})
    assert resp.status_code == 200
    assert resp.json()["reasoning_effort"] == "medium"
    assert (await LlmModel.get(model_id)).reasoning_effort == "medium"

    resp = await client.patch(f"/llm/models/{model_id}", json={"reasoning_effort": None})
    assert resp.json()["reasoning_effort"] is None


async def test_update_reasoning_effort_requires_the_field(client):
    resp = await client.patch(f"/llm/models/{client.default_llm_model_id}", json={})
    assert resp.status_code == 422


async def test_seed_from_env_includes_reasoning_effort(mongo_db, monkeypatch):
    monkeypatch.setattr(
        llm_router,
        "get_settings",
        lambda: Settings(llm_provider="openai", llm_model="gpt-5", llm_reasoning_effort="HIGH"),
    )
    await llm_router.seed_llm_models_from_env()
    assert (await LlmModel.find_one()).reasoning_effort == "high"


async def test_seed_from_env_ignores_invalid_reasoning_effort(mongo_db, monkeypatch):
    monkeypatch.setattr(
        llm_router,
        "get_settings",
        lambda: Settings(llm_provider="openai", llm_model="gpt-5", llm_reasoning_effort="extreme"),
    )
    await llm_router.seed_llm_models_from_env()
    assert (await LlmModel.find_one()).reasoning_effort is None


async def test_test_answer_passes_models_reasoning_effort(client, monkeypatch):
    _, rubric_id = await _make_submission(client)
    await client.patch(f"/llm/models/{client.default_llm_model_id}", json={"reasoning_effort": "low"})
    seen = []

    def fake_get_chat_model(provider, model, **kwargs):
        seen.append(kwargs.get("reasoning_effort"))
        raise RuntimeError("stop here")

    monkeypatch.setattr("aitana.api.routers.rubrics.get_chat_model", fake_get_chat_model)
    await client.post(f"/rubrics/{rubric_id}/test-answer", json={"question_id": "q1", "answer": "x"})
    assert seen == ["low"]
