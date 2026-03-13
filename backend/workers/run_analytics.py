"""
Analytics Worker
Runs: campaign metrics aggregation, daily/weekly reports
Schedule: every ANALYTICS_INTERVAL_HOURS (default 1h, nightly full rebuild)
"""
import asyncio
import logging
import os
import time
from datetime import datetime

from ..db.database import AsyncSessionLocal, init_db
from ..utils.logging import configure_logging

logger = logging.getLogger(__name__)

INTERVAL_S    = int(os.environ.get("ANALYTICS_INTERVAL_HOURS", "1")) * 3600
RUN_ONCE      = os.environ.get("RUN_ONCE", "").lower() == "true"
NIGHTLY_HOUR  = int(os.environ.get("ANALYTICS_NIGHTLY_HOUR", "2"))   # 2am UTC


async def run_analytics_cycle(full: bool = False):
    from ..analytics.campaign_intelligence import update_campaign_metrics
    async with AsyncSessionLocal() as db:
        updated = await update_campaign_metrics(db, full_rebuild=full)
        logger.info("Analytics: updated %d metric records (full=%s)", updated, full)


async def run():
    configure_logging("analytics")
    logger.info("Analytics Worker starting (interval=%dh, nightly full rebuild at %d:00 UTC)",
                INTERVAL_S // 3600, NIGHTLY_HOUR)
    await init_db()

    while True:
        now = datetime.utcnow()
        is_nightly = (now.hour == NIGHTLY_HOUR and now.minute < (INTERVAL_S // 60))
        t0 = time.monotonic()

        try:
            await run_analytics_cycle(full=is_nightly)
            if is_nightly:
                logger.info("Nightly full analytics rebuild complete in %.1fs", time.monotonic() - t0)
        except Exception as exc:
            logger.error("Analytics cycle FAILED: %s", exc, exc_info=True)

        if RUN_ONCE:
            break
        await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(run())
