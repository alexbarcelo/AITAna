"""Shared fixtures for the API test suite.

Runs against a real PostgreSQL: `TEST_DATABASE_URL` if set, otherwise a
throwaway `postgres:17-alpine` container (testcontainers) started once per
session. The schema comes from the real Alembic migrations. Each test runs
inside one outer transaction that is rolled back at the end: the app's
commits become savepoints (`join_transaction_mode="create_savepoint"`), so
tests are isolated without recreating anything.

Object storage and Celery are in-process fakes. Requests go through httpx's
ASGITransport directly (not FastAPI's lifespan-aware TestClient), on the same
event loop as the `db` fixture -- asyncpg connections are loop-bound.
"""

import io
import os
import zipfile
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import NullPool

# A developer's real .env may point GOTENBERG_URL at a live converter; the
# suite must never call it (environment beats .env in pydantic-settings, and
# this runs before anything calls the cached get_settings()).
os.environ["GOTENBERG_URL"] = ""

from aitana.api.main import app  # noqa: E402
from aitana.db import LlmModel  # noqa: E402
from aitana.db.session import get_session  # noqa: E402
from aitana.settings import Settings  # noqa: E402

_REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="session")
def database_url():
    """A migrated, empty database for the whole session."""
    url = os.environ.get("TEST_DATABASE_URL")
    container = None
    if not url:
        from testcontainers.postgres import PostgresContainer

        container = PostgresContainer("postgres:17-alpine", driver=None)
        container.start()
        url = container.get_connection_url()
    url = Settings(database_url=url).database_url  # normalized to the asyncpg driver

    config = Config(str(_REPO_ROOT / "alembic.ini"))
    config.attributes["database_url"] = url
    command.upgrade(config, "head")
    try:
        yield url
    finally:
        if container is not None:
            container.stop()


@pytest.fixture
async def db(database_url):
    """A session inside a transaction that's rolled back after the test.
    Shared with the app (see `client`), so data a test creates here is what
    the endpoints see, and vice versa."""
    engine = create_async_engine(database_url, poolclass=NullPool)
    async with engine.connect() as connection:
        transaction = await connection.begin()
        session = AsyncSession(bind=connection, join_transaction_mode="create_savepoint", expire_on_commit=False)
        try:
            yield session
        finally:
            await session.close()
            await transaction.rollback()
    await engine.dispose()


_W = "http://schemas.openxmlformats.org"


def _minimal_docx(text: str) -> bytes:
    """The smallest .docx MuPDF opens: one paragraph of `text`."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "[Content_Types].xml",
            f'<?xml version="1.0" encoding="UTF-8"?><Types xmlns="{_W}/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            '<Override PartName="/word/document.xml" ContentType="application/'
            'vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>',
        )
        zf.writestr(
            "_rels/.rels",
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_W}/package/2006/relationships">'
            f'<Relationship Id="rId1" Type="{_W}/officeDocument/2006/relationships/officeDocument" '
            'Target="word/document.xml"/></Relationships>',
        )
        zf.writestr(
            "word/_rels/document.xml.rels",
            f'<?xml version="1.0" encoding="UTF-8"?><Relationships xmlns="{_W}/package/2006/relationships"/>',
        )
        zf.writestr(
            "word/document.xml",
            f'<?xml version="1.0" encoding="UTF-8"?><w:document xmlns:w="{_W}/wordprocessingml/2006/main">'
            f"<w:body><w:p><w:r><w:t>{text}</w:t></w:r></w:p></w:body></w:document>",
        )
    return buf.getvalue()


@pytest.fixture
def minimal_docx():
    """Builder for a tiny real .docx (generic-document uploads convert it)."""
    return _minimal_docx


@pytest.fixture
async def client(monkeypatch, db):
    fake_objects: dict[str, bytes] = {}

    def fake_upload_file(key: str, data: bytes, content_type: str) -> None:
        fake_objects[key] = data

    def fake_download_to_path(key: str, dest: Path) -> None:
        dest.write_bytes(fake_objects[key])

    monkeypatch.setattr("aitana.storage.upload_file", fake_upload_file)
    monkeypatch.setattr("aitana.storage.download_to_path", fake_download_to_path)
    monkeypatch.setattr("aitana.storage.download_bytes", lambda key: fake_objects[key])

    def fake_delete_submission_files(file_object_key: str) -> None:
        prefix = file_object_key.rsplit(".", 1)[0] + "/"
        for key in [k for k in fake_objects if k == file_object_key or k.startswith(prefix)]:
            del fake_objects[key]

    monkeypatch.setattr("aitana.storage.delete_submission_files", fake_delete_submission_files)

    delayed_submission_ids: list[int] = []
    delayed_question_ids: list[str | None] = []
    delayed_model_ids: list[int | None] = []

    def fake_delay(submission_id: int, only_question_id: str | None = None, model_id: int | None = None) -> None:
        delayed_submission_ids.append(submission_id)
        delayed_question_ids.append(only_question_id)
        delayed_model_ids.append(model_id)

    monkeypatch.setattr("aitana.api.routers.submissions.grade_submission.delay", fake_delay)

    # Generic-document tasks (worker/tasks.py's prepare_submission /
    # suggest_segments_task): (submission_id, model_id) per call.
    delayed_prepares: list[tuple[int, int | None]] = []
    delayed_suggestions: list[tuple[int, int | None]] = []
    monkeypatch.setattr(
        "aitana.api.routers.submissions.prepare_submission.delay",
        lambda submission_id, segment_model_id=None: delayed_prepares.append((submission_id, segment_model_id)),
    )
    monkeypatch.setattr(
        "aitana.api.routers.submissions.suggest_segments_task.delay",
        lambda submission_id, model_id=None: delayed_suggestions.append((submission_id, model_id)),
    )

    # Every grading-triggering endpoint needs a usable model to resolve
    # (api/routers/llm.py's require_llm_model): seed a default one whose
    # provider counts as available (package installed in dev, fake key set).
    monkeypatch.setenv("OPENAI_API_KEY", "fake-key")
    default_llm_model = LlmModel(provider="openai", model="gpt-4o-mini", is_default=True)
    db.add(default_llm_model)
    await db.commit()

    async def override_get_session():
        # Like a fresh per-request session: nothing cached from the test's
        # own setup or from earlier requests; uncommitted work is dropped.
        db.expunge_all()
        try:
            yield db
        finally:
            await db.rollback()

    app.dependency_overrides[get_session] = override_get_session

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.delayed_submission_ids = delayed_submission_ids  # type: ignore[attr-defined]
        ac.delayed_question_ids = delayed_question_ids  # type: ignore[attr-defined]
        ac.delayed_model_ids = delayed_model_ids  # type: ignore[attr-defined]
        ac.default_llm_model_id = default_llm_model.id  # type: ignore[attr-defined]
        ac.fake_objects = fake_objects  # type: ignore[attr-defined]
        ac.delayed_prepares = delayed_prepares  # type: ignore[attr-defined]
        ac.delayed_suggestions = delayed_suggestions  # type: ignore[attr-defined]
        ac.db = db  # type: ignore[attr-defined]
        try:
            yield ac
        finally:
            app.dependency_overrides.pop(get_session, None)
