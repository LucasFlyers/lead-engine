#!/usr/bin/env python3
"""
Database migration — uses raw asyncpg (no SQLAlchemy connection pool issues).
Each statement runs in autocommit mode so one failure never blocks the rest.
"""
import asyncio, logging, os, re, ssl, sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("migrate")

SCHEMA_PATH = Path(__file__).parent.parent / "backend" / "db" / "schema.sql"


def parse_url(raw: str):
    """Return (dsn, ssl_ctx) for asyncpg.connect()."""
    needs_ssl = "sslmode=require" in raw or "neon.tech" in raw
    # asyncpg wants postgresql:// not postgres://
    dsn = re.sub(r"^postgres(ql)?://", "postgresql://", raw.strip())
    # Strip all query params — pass ssl separately
    dsn = dsn.split("?")[0].rstrip("/")
    ctx = None
    if needs_ssl:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return dsn, ctx


async def main():
    import asyncpg

    raw_url = os.environ.get("DATABASE_URL", "")
    if not raw_url:
        logger.error("DATABASE_URL is not set")
        sys.exit(1)

    dsn, ssl_ctx = parse_url(raw_url)

    # Connect
    try:
        conn = await asyncpg.connect(dsn, ssl=ssl_ctx, command_timeout=30)
        logger.info("Database connection OK")
    except Exception as exc:
        logger.error("Cannot connect: %s", exc)
        sys.exit(1)

    if not SCHEMA_PATH.exists():
        logger.error("Schema not found: %s", SCHEMA_PATH)
        await conn.close()
        sys.exit(1)

    # Enable uuid-ossp extension first (needed for uuid_generate_v4())
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS \"uuid-ossp\"")
        logger.info("uuid-ossp extension enabled")
    except Exception as exc:
        logger.warning("uuid-ossp: %s", exc)

    # Enable pg_trgm for fuzzy search
    try:
        await conn.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
        logger.info("pg_trgm extension enabled")
    except Exception as exc:
        logger.warning("pg_trgm: %s", exc)

    statements = [s.strip() for s in SCHEMA_PATH.read_text().split(";") if s.strip()]
    logger.info("Applying %d statements...", len(statements))

    applied = skipped = errors = 0
    for stmt in statements:
        # Skip comment-only statements
        clean = re.sub(r'--[^\n]*', '', stmt).strip()
        if not clean:
            skipped += 1
            continue
        try:
            await conn.execute(stmt)
            applied += 1
        except asyncpg.exceptions.DuplicateTableError:
            skipped += 1
        except asyncpg.exceptions.DuplicateObjectError:
            skipped += 1
        except asyncpg.exceptions.DuplicateColumnError:
            skipped += 1
        except Exception as exc:
            err = str(exc).lower()
            if any(x in err for x in ("already exists", "duplicate")):
                skipped += 1
            else:
                logger.warning("Error on statement: %s\n  -> %s", clean[:80], exc)
                errors += 1

    await conn.close()
    logger.info("Done: %d applied, %d skipped, %d errors", applied, skipped, errors)

    if errors > 0:
        logger.warning("%d statements had unexpected errors — check above", errors)
    else:
        logger.info("Migration complete — all tables ready")


if __name__ == "__main__":
    asyncio.run(main())
