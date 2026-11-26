"""Alembic environment: runs migrations over the app's own async engine
settings (`DATABASE_URL`).

Holds a PostgreSQL advisory lock for the whole run, so several API replicas
starting at once (each running `alembic upgrade head` before serving)
migrate one at a time instead of racing."""

import asyncio

from alembic import context
from sqlalchemy import text
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from aitana.db.models import Base
from aitana.db.types import PydanticJSONB
from aitana.settings import get_settings

target_metadata = Base.metadata

# Arbitrary, fixed: identifies "AITAna schema migration" among advisory locks.
_MIGRATION_LOCK_ID = 727_001


def _url() -> str:
    # Tests (and other callers) can pass a URL through the Alembic config.
    return context.config.attributes.get("database_url") or get_settings().database_url


def _render_item(type_: str, obj: object, autogen_context) -> str | bool:
    """Autogenerate writes app column types as what they are in the
    database, so revisions never import app code: `PydanticJSONB` -> JSONB."""
    if type_ == "type" and isinstance(obj, PydanticJSONB):
        autogen_context.imports.add("from sqlalchemy.dialects import postgresql")
        return "postgresql.JSONB()"
    return False


_CONFIGURE = {"target_metadata": target_metadata, "compare_type": True, "render_item": _render_item}


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, **_CONFIGURE)
    with context.begin_transaction():
        context.run_migrations()


def _run(connection: Connection) -> None:
    connection.execute(text("SELECT pg_advisory_lock(:id)"), {"id": _MIGRATION_LOCK_ID})
    try:
        context.configure(connection=connection, **_CONFIGURE)
        with context.begin_transaction():
            context.run_migrations()
    except BaseException:
        # A failed statement leaves the transaction aborted, so the unlock
        # below would fail too and replace the real error with "current
        # transaction is aborted". A session-level advisory lock survives
        # the rollback, so the unlock still has something to release.
        connection.rollback()
        raise
    finally:
        connection.execute(text("SELECT pg_advisory_unlock(:id)"), {"id": _MIGRATION_LOCK_ID})
        connection.commit()


async def run_migrations_online() -> None:
    connection = context.config.attributes.get("connection")
    if connection is not None:
        _run(connection)
        return
    engine = create_async_engine(_url(), poolclass=NullPool)
    try:
        async with engine.connect() as conn:
            await conn.run_sync(_run)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
