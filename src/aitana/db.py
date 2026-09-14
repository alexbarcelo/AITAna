"""Beanie initialization, shared by the FastAPI app and the Celery worker.

Uses PyMongo's native async client (`pymongo.AsyncMongoClient`), not Motor:
Beanie's driver-metadata handshake (`database.client.append_metadata(...)`,
added for PyMongo >=4.14) misdetects Motor's client -- Motor's classic
`__getattr__` attribute-proxying (`client.anything` -> a Database named
"anything") makes `callable(database.client.append_metadata)` true even
though it isn't a real method, so Beanie ends up *calling* a Database object
and crashes with "MotorDatabase object is not callable". Verified against a
real mongod that the native async client has no such issue.
"""

import logging

from beanie import init_beanie
from pymongo import AsyncMongoClient

from .documents import DOCUMENT_MODELS
from .settings import get_settings

logger = logging.getLogger(__name__)

_client: AsyncMongoClient | None = None


async def init_db() -> None:
    """Idempotent: safe to call once per event loop (API startup, or once per
    Celery task run via asyncio.run -- client creation is cheap)."""
    global _client
    settings = get_settings()
    _client = AsyncMongoClient(settings.mongo_uri)
    await init_beanie(database=_client[settings.mongo_db], document_models=DOCUMENT_MODELS)
    logger.debug("Beanie initialized against %s/%s", settings.mongo_uri, settings.mongo_db)
