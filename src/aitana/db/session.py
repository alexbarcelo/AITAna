"""Engine/session setup, shared by the FastAPI app and the Celery worker.

The API creates one pooled engine for the process lifetime (`init_engine` in
its lifespan). The worker runs each Celery task in its own `asyncio.run(...)`
event loop, and asyncpg connections are bound to the loop that opened them,
so it builds a throwaway `NullPool` engine per task instead
(`task_session`).
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from ..settings import get_settings

_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def make_engine(*, pooled: bool = True) -> AsyncEngine:
    url = get_settings().database_url
    if pooled:
        return create_async_engine(url, pool_pre_ping=True)
    return create_async_engine(url, poolclass=NullPool)


def make_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    # expire_on_commit=False: objects stay readable after a commit (endpoints
    # commit, then serialize what they just wrote; the worker commits after
    # every graded question and keeps using the same objects).
    return async_sessionmaker(engine, expire_on_commit=False)


def init_engine() -> None:
    global _engine, _sessionmaker
    if _engine is None:
        _engine = make_engine()
        _sessionmaker = make_sessionmaker(_engine)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request. Endpoints commit
    explicitly; anything left uncommitted is rolled back on close."""
    if _sessionmaker is None:
        init_engine()
    assert _sessionmaker is not None
    async with _sessionmaker() as session:
        yield session


@asynccontextmanager
async def task_session() -> AsyncIterator[AsyncSession]:
    """A session on a fresh, unpooled engine -- for code running inside its
    own `asyncio.run(...)` loop (Celery tasks)."""
    engine = make_engine(pooled=False)
    try:
        async with make_sessionmaker(engine)() as session:
            yield session
    finally:
        await engine.dispose()
