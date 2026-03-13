#!/usr/bin/env python3
"""
Database migration script — safe to run on every deploy.
Applies schema.sql idempotently (CREATE TABLE IF NOT EXISTS, ALTER TABLE ADD COLUMN IF NOT EXISTS).

Usage:
  python scripts/migrate.py                    # apply all migrations
  python scripts/migrate.py --check            # verify connectivity only
  python scripts/migrate.py --seed             # apply + seed demo data
  DATABASE_URL=postgres://... python scripts/migrate.py
"""
import argparse
import asyncio
import logging
import os
import sys
import time
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate")

SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "db" / "schema.sql"
MAX_RETRIES = 10
RETRY_DELAY = 3   # seconds


async def wait_for_db(engine) -> bool:
    """Retry DB connection — Neon may take a moment to wake from sleep."""
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
    import re
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy import text

    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        logger.error("DATABASE_URL environment variable is not set")
        return False

    # Normalise URL scheme
    url = re.sub(r"^postgres(ql)?://", "postgresql+asyncpg://", raw_url.strip())
    engine = create_async_engine(url, pool_size=2, connect_args={"command_timeout": 30})

    try:
        if not await wait_for_db(engine):
            return False

        if check_only:
            logger.info("--check passed: database is reachable")
            return True

        if not SCHEMA_PATH.exists():
            logger.error("Schema file not found: %s", SCHEMA_PATH)
            return False

        schema_sql = SCHEMA_PATH.read_text()
        statements = [s.strip() for s in schema_sql.split(";") if s.strip()]
        logger.info("Applying %d schema statements...", len(statements))

        applied = 0
        skipped = 0
        async with engine.begin() as conn:
            # Enable pg_trgm extension if available
            try:
                await conn.execute(text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))
                logger.info("pg_trgm extension enabled")
            except Exception:
                logger.warning("Could not enable pg_trgm extension (non-fatal)")

            for stmt in statements:
                try:
                    await conn.execute(text(stmt))
                    applied += 1
                except Exception as exc:
                    err = str(exc).lower()
                    # These are expected on re-runs — schema is idempotent
                    if any(x in err for x in ("already exists", "duplicate", "does not exist")):
                        skipped += 1
                    else:
                        logger.warning("Statement skipped (%s): %.80s", exc, stmt)
                        skipped += 1

        logger.info("Migration complete: %d applied, %d skipped/idempotent", applied, skipped)
        return True
    finally:
        await engine.dispose()


async def seed_demo(seed_script: Path) -> None:
    if not seed_script.exists():
        logger.warning("Seed script not found: %s", seed_script)
        return
    import importlib.util
    spec = importlib.util.spec_from_file_location("seed", seed_script)
    mod  = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    if hasattr(mod, "seed"):
        await mod.seed()
        logger.info("Demo data seeded")
    else:
        logger.warning("Seed script has no seed() function")


async def main() -> int:
    parser = argparse.ArgumentParser(description="Lead Engine — database migration")
    parser.add_argument("--check", action="store_true", help="Only test DB connectivity")
    parser.add_argument("--seed",  action="store_true", help="Seed demo data after migration")
    args = parser.parse_args()

    success = await run_migrations(check_only=args.check)
    if not success:
        return 1

    if args.seed and not args.check:
        seed_path = Path(__file__).parent / "seed_demo_data.py"
        await seed_demo(seed_path)

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
