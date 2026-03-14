"""
Email Sender Worker
Runs: outreach queue processor — generates + sends emails via SMTP
Schedule: every EMAIL_INTERVAL_MINUTES (default 30m)
"""
import asyncio
import logging
import os
import time

from db.database import AsyncSessionLocal, init_db
from utils.logging import configure_logging
from workers.email_sender import recover_stuck_sends

logger = logging.getLogger(__name__)

INTERVAL_S = int(os.environ.get("EMAIL_INTERVAL_MINUTES", "30")) * 60
BATCH_SIZE = int(os.environ.get("EMAIL_BATCH_SIZE", "10"))
RUN_ONCE   = os.environ.get("RUN_ONCE", "").lower() == "true"


async def run():
    configure_logging("email-sender")
    logger.info("Email Sender Worker starting (interval=%dm, batch=%d)", INTERVAL_S // 60, BATCH_SIZE)
    await init_db()

    # Recover any sends stuck from a previous crash
    async with AsyncSessionLocal() as db:
        recovered = await recover_stuck_sends(db)
        if recovered:
            logger.info("Startup: recovered %d stuck queue items", recovered)

    # Sync inbox state
    from deliverability.inbox_rotation_manager import get_rotation_manager
    mgr = get_rotation_manager()
    async with AsyncSessionLocal() as db:
        await mgr.sync_from_db(db)

    while True:
        t0 = time.monotonic()
        try:
            from workers.email_sender import process_outreach_queue
            async with AsyncSessionLocal() as db:
                sent = await process_outreach_queue(db, batch_size=BATCH_SIZE)
                if sent > 0:
                    logger.info("Email cycle: sent %d emails in %.1fs", sent, time.monotonic() - t0)
        except Exception as exc:
            logger.error("Email sender cycle FAILED: %s", exc, exc_info=True)

        if RUN_ONCE:
            break
        await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(run())
