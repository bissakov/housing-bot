from typing import Any
from sqlalchemy import inspect, text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from bot.config import DATABASE_URL

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

# Lightweight additive migrations for SQLite: add columns that were introduced
# after initial schema. create_all() only creates missing tables, not columns,
# so we ALTER existing tables to keep older bot.db files compatible.
_ADDITIVE_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "requests": [
        ("urgency", "VARCHAR(10)"),
        ("raw_description", "TEXT"),
        ("llm_meta", "TEXT"),
    ],
}


def _migrate_sync(conn: Any) -> None:
    if conn.dialect.name != "sqlite":
        return
    insp = inspect(conn)
    for table, cols in _ADDITIVE_COLUMNS.items():
        if not insp.has_table(table):
            continue
        existing = {c["name"] for c in insp.get_columns(table)}
        for col, type_ in cols:
            if col not in existing:
                conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col} {type_}"))


async def init_db():
    """Create tables for local/dev convenience.

    Production deployments should run ``alembic upgrade head`` before the bot
    starts. create_all remains intentionally non-destructive for tests and
    lightweight SQLite installations.
    """
    from bot.models import Base
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await conn.run_sync(_migrate_sync)


async def close_db() -> None:
    await engine.dispose()


async def get_session() -> AsyncSession:
    async with async_session() as session:
        yield session
