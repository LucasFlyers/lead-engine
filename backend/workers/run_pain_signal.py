"""
Pain Signal Worker
Runs: Reddit, HackerNews, forum, review site scraping + AI analysis
Schedule: every PAIN_INTERVAL_HOURS (default 8h)
"""
import asyncio
import logging
import os
import time

from ..db.database import init_db
from ..utils.logging import configure_logging

logger = logging.getLogger(__name__)

INTERVAL_S = int(os.environ.get("PAIN_INTERVAL_HOURS", "8")) * 3600
RUN_ONCE   = os.environ.get("RUN_ONCE", "").lower() == "true"


async def run():
    configure_logging("pain-signal")
    logger.info("Pain Signal Worker starting (interval=%dh)", INTERVAL_S // 3600)
    await init_db()

    while True:
        t0 = time.monotonic()
        logger.info("=== Pain signal cycle START ===")
        try:
            from .orchestrator import run_pain_signal_pipeline
            await run_pain_signal_pipeline()
            logger.info("=== Pain signal cycle DONE in %.1fs ===", time.monotonic() - t0)
        except Exception as exc:
            logger.error("Pain signal cycle FAILED: %s", exc, exc_info=True)

        if RUN_ONCE:
            break
        logger.info("Next pain signal run in %dh", INTERVAL_S // 3600)
        await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(run())
