#!/usr/bin/env python3
"""
Database migration script — safe to run on every deploy.
Usage:
  python scripts/migrate.py
  python scripts/migrate.py --check
"""
import argparse
import asyncio
import logging
import os
import re
import ssl
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate")

SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "db" / "schema.sql"
MAX_RETRIES = 10
RETRY_DELAY = 3


def _build_engine(raw_url: str):
    from sqlalchemy.ext.asyncio import create_async_engine

    # Strip ALL query params — asyncpg doesn't accept them in the URL
    needs_ssl = "sslmode=require" in raw_url or "neon.tech" in raw_url
    url = re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", raw_url.strip())
    url = url.split("?")[0].rstrip("/")

    connect_args = {"command_timeout": 30}
    if needs_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        connect_args["ssl"] = ctx

    return create_async_engine(url, pool_size=2, connect_args=connect_args)


async def wait_for_db(engine) -> bool:
    from sqlalchemy import text
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            logger.info("Database connection OK")
            return True
        except Exception as exc:
            if attempt < MAX_RETRIES:
                logger.warning("DB not ready (attempt %d/%d): %s — retrying in %ds",
                               attempt, MAX_RETRIES, exc, RETRY_DELAY)
                await asyncio.sleep(RETRY_DELAY)
            else:
                logger.error("Database unreachable after %d attempts: %s", MAX_RETRIES, exc)
                return False
    return False


async def run_migrations(check_only: bool = False) -> bool:
    from sqlalchemy import text

    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        logger.error("DATABASE_URL environment variable is not set")
        return False

    engine = _build_engine(raw_url)

    try:
        if not await wait_for_db(engine):
            return False

        if check_only:
            logger.info("--check passed: database is reachable")
            return True

        if not SCHEMA_PATH.exists():
            logger.error("Schema file not found: %s", SCHEMA_PATH)
            return False

        statements = [s.strip() for s in SCHEMA_PATH.read_text().split(";") if s.strip()]
        logger.info("Applying %d schema statements...", len(statements))

        applied = skipped = 0
        async with engine.begin() as conn:
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
            except Exception:
                pass

            for stmt in statements:
                try:
                    await conn.execute(text(stmt))
                    applied += 1
                except Exception as exc:
                    skipped += 1
                    err = str(exc).lower()
                    if not any(x in err for x in ("already exists", "duplicate", "does not exist")):
                        logger.warning("Statement skipped (%s): %.80s", exc, stmt)

        logger.info("Migration complete: %d applied, %d skipped/idempotent", applied, skipped)
        return True
    finally:
        await engine.dispose()


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    return 0 if await run_migrations(check_only=args.check) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
