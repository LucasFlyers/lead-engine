import os
import subprocess

# Ensure Playwright browsers are installed at runtime
browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "/ms-playwright")
os.environ["PLAYWRIGHT_BROWSERS_PATH"] = browsers_path
chrome = os.path.join(browsers_path, "chromium-1117/chrome-linux/chrome")
if not os.path.exists(chrome):
    subprocess.run(["playwright", "install", "chromium"], check=False)

"""
Lead Scraper Worker
Runs: scraping pipeline (Clutch, Google Maps, agency directories)
Schedule: every SCRAPE_INTERVAL_HOURS (default 6h)
"""
import asyncio
import logging
import time

from db.database import AsyncSessionLocal, init_db
from utils.logging import configure_logging

logger = logging.getLogger(__name__)

INTERVAL_S = int(os.environ.get("SCRAPE_INTERVAL_HOURS", "6")) * 3600
RUN_ONCE   = os.environ.get("RUN_ONCE", "").lower() == "true"


async def run():
    configure_logging("lead-scraper")
    logger.info("Lead Scraper Worker starting (interval=%dh)", INTERVAL_S // 3600)
    await init_db()

    while True:
        t0 = time.monotonic()
        logger.info("=== Lead scraping cycle START ===")
        try:
            from workers.orchestrator import run_scraping_pipeline
            await run_scraping_pipeline()
            logger.info("=== Lead scraping cycle DONE in %.1fs ===", time.monotonic() - t0)
        except Exception as exc:
            logger.error("Lead scraping cycle FAILED: %s", exc, exc_info=True)

        if RUN_ONCE:
            logger.info("RUN_ONCE=true — exiting after first cycle")
            break
        logger.info("Next scraping run in %dh", INTERVAL_S // 3600)
        await asyncio.sleep(INTERVAL_S)


if __name__ == "__main__":
    asyncio.run(run())
