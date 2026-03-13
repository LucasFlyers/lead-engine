"""
Deliverability Worker
Runs: inbox health checks, warmup progression, bounce/spam rate monitoring
Schedule: every DELIVERABILITY_INTERVAL_MINUTES (default 60m)
"""
import asyncio
import logging
import os
import time

from db.database import AsyncSessionLocal, init_db
from utils.logging import configure_logging

logger = logging.getLogger(__name__)

INTERVAL_S = int(os.environ.get("DELIVERABILITY_INTERVAL_MINUTES", "60")) * 60
RUN_ONCE   = os.environ.get("RUN_ONCE", "").lower() == "true"


async def run():
    configure_logging("deliverability")
    logger.info("Deliverability Worker starting (interval=%dm)", INTERVAL_S // 60)
    await init_db()

    while True:
        t0 = time.monotonic()
        try:
            from ..analytics.inbox_health_monitor import check_all_inbox_health
            async with AsyncSessionLocal() as db:
                results = await check_all_inbox_health(db)
                logger.info("Deliverability check: %d inboxes evaluated in %.1fs",
                            len(results), time.monotonic() - t0)
                for r in results:
                    status = "PAUSED" if r.get("is_paused") else "ok"
                    logger.info("  inbox=%s bounce=%.2f%% spam=%.3f%% status=%s",
                                r.get("inbox"), r.get("bounce_rate", 0),
                                r.get("spam_rate", 0), status)
        except Exception as exc:
            logger.error("Deliverability cycle FAILED: %s", exc, exc_info=True)

        if RUN_ONCE:
            break
        await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(run())
