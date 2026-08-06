"""Alembic environment — async engine, models imported for autogenerate."""

import asyncio
from logging.config import fileConfig

from alembic import context
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config
from sqlalchemy.pool import NullPool

import app.models  # noqa: F401 — registers every table on Base.metadata
from app.core.config import settings
from app.db.base import Base

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
target_metadata = Base.metadata

# Tables owned by LangGraph's AsyncPostgresSaver (created at runtime by
# checkpointer.setup(), not by our models). Autogenerate must never see them —
# without this filter it emits drop_table for each, which both breaks
# `upgrade head` on a clean DB and destroys live conversation checkpoints.
RUNTIME_OWNED_TABLES = {
    "checkpoints",
    "checkpoint_blobs",
    "checkpoint_writes",
    "checkpoint_migrations",
}


def _include_object(obj, name, type_, reflected, compare_to):  # noqa: ANN001, ANN202
    if type_ == "table" and name in RUNTIME_OWNED_TABLES:
        return False
    if type_ == "index" and getattr(obj, "table", None) is not None:
        if obj.table.name in RUNTIME_OWNED_TABLES:
            return False
    return True


def run_migrations_offline() -> None:
    """Emit SQL to stdout without a live DB connection."""
    context.configure(
        url=settings.DATABASE_URL,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    """Run migrations over the async engine (SQLAlchemy 2.0 recipe)."""
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(_do_run_migrations)
    await connectable.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
