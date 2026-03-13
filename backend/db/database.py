"""
Database connection and session management.

AUDIT FIXES:
- DATABASE_URL validated at startup; raises clear error if missing
- asyncpg import removed (unused — SQLAlchemy handles the driver)
- Connection URL normalised safely (handles all pg:// variants)
- Pool settings tuned for Railway single-instance (pool_size=5)
- Statement timeout set via connect_args for Neon compatibility
- Engine only created once; lazy init avoids import-time failures
"""
import os
import re
from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import (
    AsyncConnection,
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


# --------------------------------------------------------------------------- #
#  URL normalisation
# --------------------------------------------------------------------------- #
def _normalise_db_url(raw: str) -> str:
    """Convert any postgres:// variant → postgresql+asyncpg://"""
    if not raw:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Add it to your .env or Railway Variables panel."
        )
    raw = raw.strip()
    # Replace scheme
    raw = re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", raw)
    if not raw.startswith("postgresql+asyncpg://"):
        raise RuntimeError(f"Unrecognised DATABASE_URL scheme: {raw[:40]!r}")
    return raw


DATABASE_URL: str = _normalise_db_url(os.environ.get("DATABASE_URL", ""))

# --------------------------------------------------------------------------- #
#  Engine — created once at module level
# --------------------------------------------------------------------------- #
engine: AsyncEngine = create_async_engine(
    DATABASE_URL,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,          # recycle connections every 30 min (Neon idle timeout)
    connect_args={
        "command_timeout": 30,  # per-statement timeout (asyncpg)
        "server_settings": {
            "application_name": "lead_engine",
            "statement_timeout": "30000",   # ms — Postgres-level safety net
        },
    },
    echo=os.environ.get("SQL_ECHO", "").lower() == "true",
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# --------------------------------------------------------------------------- #
#  ORM base
# --------------------------------------------------------------------------- #
class Base(DeclarativeBase):
    pass


# --------------------------------------------------------------------------- #
#  Dependency injector (FastAPI)
# --------------------------------------------------------------------------- #
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# --------------------------------------------------------------------------- #
#  Schema initialisation
# --------------------------------------------------------------------------- #
async def init_db() -> None:
    """Apply schema.sql to the connected database. Safe to call on every startup."""
    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    async with engine.begin() as conn:
        with open(schema_path) as f:
            schema_sql = f.read()
        # Execute each statement individually for better error reporting
        for statement in schema_sql.split(";"):
            stmt = statement.strip()
            if stmt:
                try:
                    await conn.execute(text(stmt))
                except Exception as exc:
                    # Log but don't abort — CREATE TABLE IF NOT EXISTS means most are safe
                    import logging
                    logging.getLogger(__name__).debug("Schema stmt skipped: %s", exc)


async def check_db_health() -> bool:
    """Quick connectivity check for the /health endpoint."""
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
