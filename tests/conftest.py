"""Shared fixtures for the API test suite.

Everything runs against an in-memory Mongo (mongomock-motor) and in-process
fakes for MinIO/Celery -- no live mongo/minio/redis needed. Requests go
through httpx's ASGITransport directly (not FastAPI's lifespan-aware
TestClient), so the whole request stays on the *same* asyncio event loop as
the `mongo_db` fixture -- Motor/mongomock clients are bound to the loop that
created them.
"""

from pathlib import Path

import mongomock
import mongomock_motor
import pytest
from beanie import init_beanie
from httpx import ASGITransport, AsyncClient
from mongomock_motor import AsyncMongoMockClient

from aitana.api.main import app
from aitana.documents import DOCUMENT_MODELS

# mongomock-motor 0.0.36 lags current pymongo/motor in two ways Beanie relies
# on. Both shims are test-only; production code never touches mongomock.

# 1) mongomock's Database.list_collection_names() doesn't yet accept the
#    authorizedCollections/nameOnly kwargs current pymongo/motor pass through
#    -- needed for Beanie's startup collection scan.
_original_list_collection_names = mongomock.Database.list_collection_names


def _compat_list_collection_names(self, filter=None, session=None, **_ignored):
    return _original_list_collection_names(self, filter=filter, session=session)


mongomock.Database.list_collection_names = _compat_list_collection_names

# 2) Real motor's Collection.aggregate() is itself a coroutine that resolves
#    to a command cursor; mongomock-motor's is a plain sync method, so
#    `await collection.aggregate(...)` (used by Beanie's fetch_links=True
#    queries, which compile to a $lookup aggregation) breaks with
#    "can't be used in 'await' expression".
_original_collection_aggregate = mongomock_motor.AsyncMongoMockCollection.aggregate


async def _awaitable_aggregate(self, *args, **kwargs):
    return _original_collection_aggregate(self, *args, **kwargs)


mongomock_motor.AsyncMongoMockCollection.aggregate = _awaitable_aggregate

# 3) mongomock's $lookup handler hard-rejects a 'pipeline' key with
#    NotImplementedError even when the pipeline is empty. Beanie adds that
#    key whenever a fetch_links'd field's target type has its own Link
#    fields (e.g. Submission.rubric -> Rubric.course), regardless of whether
#    resolution is actually requested that deep -- production code limits
#    that depth (`nesting_depths_per_field`, see submissions.py) so the
#    pipeline Beanie builds is genuinely empty; this shim just stops
#    mongomock from rejecting the (harmless, empty) key outright.
_original_handle_lookup_stage = mongomock.aggregate._handle_lookup_stage


def _handle_lookup_stage_tolerating_empty_pipeline(in_collection, database, options):
    if options.get("pipeline") == []:
        options = {k: v for k, v in options.items() if k != "pipeline"}
    return _original_handle_lookup_stage(in_collection, database, options)


mongomock.aggregate._handle_lookup_stage = _handle_lookup_stage_tolerating_empty_pipeline
mongomock.aggregate._PIPELINE_HANDLERS["$lookup"] = _handle_lookup_stage_tolerating_empty_pipeline


@pytest.fixture
async def mongo_db():
    """A fresh isolated mongomock database, with Beanie initialized against it."""
    db = AsyncMongoMockClient()["aitana_test"]
    await init_beanie(database=db, document_models=DOCUMENT_MODELS)
    return db


@pytest.fixture
async def client(monkeypatch, mongo_db):
    fake_objects: dict[str, bytes] = {}

    def fake_upload_file(key: str, data: bytes, content_type: str) -> None:
        fake_objects[key] = data

    def fake_download_to_path(key: str, dest: Path) -> None:
        dest.write_bytes(fake_objects[key])

    monkeypatch.setattr("aitana.storage.upload_file", fake_upload_file)
    monkeypatch.setattr("aitana.storage.download_to_path", fake_download_to_path)

    delayed_submission_ids: list[str] = []
    monkeypatch.setattr(
        "aitana.api.routers.submissions.grade_submission.delay",
        lambda submission_id: delayed_submission_ids.append(submission_id),
    )

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        ac.delayed_submission_ids = delayed_submission_ids  # type: ignore[attr-defined]
        ac.fake_objects = fake_objects  # type: ignore[attr-defined]
        yield ac
