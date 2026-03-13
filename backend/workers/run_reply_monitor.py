"""
Reply Monitor Worker
Runs: IMAP polling across all inboxes, AI reply classification
Schedule: every REPLY_MONITOR_INTERVAL_MINUTES (default 30m)
"""
import asyncio
import logging
import os
import time

from db.database import AsyncSessionLocal, init_db
from utils.logging import configure_logging

logger = logging.getLogger(__name__)

INTERVAL_S = int(os.environ.get("REPLY_MONITOR_INTERVAL_MINUTES", "30")) * 60
RUN_ONCE   = os.environ.get("RUN_ONCE", "").lower() == "true"


async def run():
    configure_logging("reply-monitor")
    logger.info("Reply Monitor Worker starting (interval=%dm)", INTERVAL_S // 60)
    await init_db()

    while True:
        t0 = time.monotonic()
        try:
            from workers.inbox_monitor import monitor_all_inboxes
            async with AsyncSessionLocal() as db:
                count = await monitor_all_inboxes(db)
                if count > 0:
                    logger.info("Reply monitor: processed %d replies in %.1fs", count, time.monotonic() - t0)
        except Exception as exc:
            logger.error("Reply monitor cycle FAILED: %s", exc, exc_info=True)

        if RUN_ONCE:
            break
        await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(run())
