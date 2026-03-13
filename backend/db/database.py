"""
Database connection and session management.

FIXES:
- asyncpg ssl: strip ?sslmode=require from URL, pass ssl=True in connect_args
- DATABASE_URL validated at startup with clear error message  
- Pool settings tuned for Railway + Neon (pool_recycle, pre_ping)
"""
import os
import re
import ssl as ssl_module
from typing import AsyncGenerator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


# ── URL normalisation ────────────────────────────────────────────────────────
def _normalise_db_url(raw: str) -> tuple[str, bool]:
    """
    Convert any postgres:// variant to postgresql+asyncpg://.
    Returns (clean_url, needs_ssl).
    Strips ?sslmode= from URL — asyncpg needs ssl passed via connect_args.
    """
    if not raw:
        raise RuntimeError(
            "DATABASE_URL environment variable is not set. "
            "Add it to Railway Variables on all backend services."
        )
    raw = raw.strip()
    raw = re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", raw)
    if not raw.startswith("postgresql+asyncpg://"):
        raise RuntimeError(f"Unrecognised DATABASE_URL scheme: {raw[:50]!r}")

    # Detect if SSL was requested, then strip it from URL
    needs_ssl = "sslmode" in raw or "neon.tech" in raw
    raw = re.sub(r"[?&]sslmode=[^&]*", "", raw)
    raw = re.sub(r"[?&]ssl=[^&]*", "", raw)
    raw = raw.rstrip("?&")
    return raw, needs_ssl


_db_url, _needs_ssl = _normalise_db_url(os.environ.get("DATABASE_URL", ""))

# ── SSL context for Neon ─────────────────────────────────────────────────────
_connect_args: dict = {
    "command_timeout": 30,
    "server_settings": {
        "application_name": "lead_engine",
        "statement_timeout": "30000",
    },
}
if _needs_ssl:
    _ssl_ctx = ssl_module.create_default_context()
    _ssl_ctx.check_hostname = False
    _ssl_ctx.verify_mode = ssl_module.CERT_NONE
    _connect_args["ssl"] = _ssl_ctx

# ── Engine ───────────────────────────────────────────────────────────────────
engine: AsyncEngine = create_async_engine(
    _db_url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    pool_recycle=1800,
    connect_args=_connect_args,
    echo=os.environ.get("SQL_ECHO", "").lower() == "true",
)

AsyncSessionLocal: async_sessionmaker[AsyncSession] = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ── ORM base ─────────────────────────────────────────────────────────────────
class Base(DeclarativeBase):
    pass


# ── FastAPI dependency ────────────────────────────────────────────────────────
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


# ── Schema init ───────────────────────────────────────────────────────────────
async def init_db() -> None:
    """Apply schema.sql idempotently. Safe to call on every startup."""
    import logging
    logger = logging.getLogger(__name__)

    schema_path = os.path.join(os.path.dirname(__file__), "schema.sql")
    if not os.path.exists(schema_path):
        logger.warning("schema.sql not found at %s — skipping init_db", schema_path)
        return

    async with engine.begin() as conn:
        try:
            await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
        except Exception:
            pass

        with open(schema_path) as f:
            schema_sql = f.read()

        for stmt in schema_sql.split(";"):
            stmt = stmt.strip()
            if stmt:
                try:
                    await conn.execute(text(stmt))
                except Exception as exc:
                    logger.debug("Schema stmt skipped: %s", exc)


# ── Health check ──────────────────────────────────────────────────────────────
async def check_db_health() -> bool:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
