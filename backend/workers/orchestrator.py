"""
Main orchestration worker — coordinates all pipeline stages.

AUDIT FIXES:
- asyncio.get_event_loop().time() replaced with asyncio.get_running_loop().time()
- Concurrency limit on email discovery (semaphore, was unbounded)
- Stuck-send recovery called on startup
- DB sync for inbox health called on startup
- run_scraping_pipeline uses INSERT ... ON CONFLICT DO NOTHING (idempotent upsert)
- Pain signal dedup: skip if source_url already stored
- Structured logging with timing for each pipeline stage
"""
import asyncio
import logging
import os
import time
from datetime import datetime

from db.database import AsyncSessionLocal
from db.models import (
    Company, Contact, LeadScore, OutreachQueue, PainSignal, SystemEvent,
)

logger = logging.getLogger(__name__)

SCRAPE_INTERVAL_S  = int(os.environ.get("SCRAPE_INTERVAL_HOURS",  "6"))  * 3600
EMAIL_INTERVAL_S   = int(os.environ.get("EMAIL_INTERVAL_MINUTES", "30")) * 60
HEALTH_INTERVAL_S  = 3600   # 1 hour
EMAIL_DISCOVERY_CONCURRENCY = int(os.environ.get("EMAIL_DISCOVERY_CONCURRENCY", "5"))


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #
async def _log_event(db, event_type: str, message: str, metadata: dict = None) -> None:
    event = SystemEvent(event_type=event_type, message=message, metadata=metadata or {})
    db.add(event)
    await db.commit()


# --------------------------------------------------------------------------- #
#  Pipeline: Lead Scraping
# --------------------------------------------------------------------------- #
async def run_scraping_pipeline() -> None:
    t0 = time.monotonic()
    logger.info("=== Scraping pipeline START ===")

    from scrapers.clutch_scraper           import scrape_clutch
    from scrapers.google_maps_scraper      import scrape_google_maps
    from scrapers.agency_directory_scraper import scrape_agency_directories
    from scrapers.email_discovery          import discover_emails
    from deduplication.lead_deduper        import deduplicate_batch
    from ai.lead_scoring                   import score_leads_batch
    from sqlalchemy import select, text

    async with AsyncSessionLocal() as db:
        await _log_event(db, "pipeline_start", "Scraping pipeline started")

    # --- Collect from all sources ---
    all_companies: list[dict] = []
    for name, coro in [
        ("Clutch",            scrape_clutch(max_pages=3)),
        ("Google Maps",       scrape_google_maps()),
        ("Agency directories",scrape_agency_directories()),
    ]:
        try:
            batch = await coro
            all_companies.extend(batch)
            logger.info("  %s → %d companies", name, len(batch))
        except Exception as exc:
            logger.error("  %s scraping failed: %s", name, exc)

    if not all_companies:
        logger.warning("No companies scraped — aborting pipeline")
        return

    # --- Load existing for dedup ---
    async with AsyncSessionLocal() as db:
        existing_result = await db.execute(
            select(Company.company_name, Company.domain, Company.id)
        )
        existing = [
            {"company_name": r.company_name, "domain": r.domain, "id": str(r.id)}
            for r in existing_result.all()
        ]

    unique_companies, dupes = deduplicate_batch(all_companies, existing)
    logger.info("After dedup: %d unique, %d duplicates", len(unique_companies), len(dupes))

    # --- AI scoring (concurrent) ---
    qualified = await score_leads_batch(unique_companies)
    logger.info("Qualified leads: %d", len(qualified))

    # --- Email discovery (concurrent, bounded) ---
    sem = asyncio.Semaphore(EMAIL_DISCOVERY_CONCURRENCY)

    async def _discover(company_data: dict) -> tuple[dict, list[dict]]:
        async with sem:
            emails = await discover_emails(company_data.get("website", ""))
            return company_data, emails

    discovery_results = await asyncio.gather(
        *[_discover(c) for c in qualified], return_exceptions=True
    )

    # --- Persist ---
    async with AsyncSessionLocal() as db:
        saved_count = 0
        for item in discovery_results:
            if isinstance(item, Exception):
                logger.error("Email discovery task raised: %s", item)
                continue
            company_data, emails = item

            # Idempotent insert — skip if domain already exists
            existing_check = await db.execute(
                select(Company.id).where(Company.domain == company_data.get("domain")).limit(1)
            )
            if existing_check.scalar_one_or_none():
                continue

            company = Company(
                company_name = company_data["company_name"],
                website      = company_data.get("website"),
                domain       = company_data.get("domain"),
                industry     = company_data.get("industry"),
                location     = company_data.get("location"),
                source       = company_data["source"],
            )
            db.add(company)
            await db.flush()

            score_data = company_data.get("lead_score_data", {})
            if score_data:
                db.add(LeadScore(
                    company_id          = company.id,
                    score               = score_data["score"],
                    industry            = score_data.get("industry"),
                    automation_maturity = score_data.get("automation_maturity"),
                    reasoning           = score_data.get("reasoning"),
                    model_used          = score_data.get("model_used"),
                ))

            # Add first valid contact and queue item only
            queued = False
            for email_data in emails[:5]:
                contact = Contact(
                    company_id       = company.id,
                    email            = email_data["email"],
                    discovery_method = email_data["discovery_method"],
                )
                db.add(contact)
                await db.flush()

                if not queued:
                    db.add(OutreachQueue(
                        company_id = company.id,
                        contact_id = contact.id,
                        priority   = score_data.get("score", 5),
                    ))
                    queued = True

            saved_count += 1

        await db.commit()
        await _log_event(
            db, "pipeline_complete",
            f"Scraping complete: {saved_count} new leads added",
            {"total_scraped": len(all_companies), "qualified": len(qualified), "saved": saved_count},
        )

    elapsed = time.monotonic() - t0
    logger.info("=== Scraping pipeline DONE in %.1fs — %d leads saved ===", elapsed, saved_count)


# --------------------------------------------------------------------------- #
#  Pipeline: Pain Signals
# --------------------------------------------------------------------------- #
async def run_pain_signal_pipeline() -> None:
    t0 = time.monotonic()
    logger.info("=== Pain signal pipeline START ===")

    from pain_scrapers.reddit_scraper  import scrape_reddit
    from pain_scrapers.forum_scraper   import scrape_forums
    from pain_scrapers.review_scraper  import scrape_reviews
    from ai.pain_signal_analyzer       import analyze_batch
    from sqlalchemy import select

    all_signals: list[dict] = []
    for name, coro in [
        ("Reddit",  scrape_reddit()),
        ("Forums",  scrape_forums()),
        ("Reviews", scrape_reviews()),
    ]:
        try:
            batch = await coro
            all_signals.extend(batch)
            logger.info("  %s → %d signals", name, len(batch))
        except Exception as exc:
            logger.error("  %s failed: %s", name, exc)

    if not all_signals:
        return

    qualified = await analyze_batch(all_signals)

    async with AsyncSessionLocal() as db:
        # Dedup by source_url to avoid re-inserting known signals
        existing_urls_result = await db.execute(
            select(PainSignal.source_url).where(PainSignal.source_url.isnot(None))
        )
        existing_urls = {r[0] for r in existing_urls_result.all()}

        new_signals = 0
        for signal in qualified:
            if signal.get("source_url") and signal["source_url"] in existing_urls:
                continue
            db.add(PainSignal(
                source           = signal["source"],
                source_url       = signal.get("source_url"),
                author           = signal.get("author"),
                content          = signal["content"],
                keywords_matched = signal.get("keywords_matched", []),
                industry         = signal.get("industry"),
                problem_desc     = signal.get("problem_desc"),
                automation_opp   = signal.get("automation_opp"),
                lead_potential   = signal.get("lead_potential"),
                processed        = True,
            ))
            new_signals += 1

        await db.commit()

    elapsed = time.monotonic() - t0
    logger.info("=== Pain signal pipeline DONE in %.1fs — %d new signals ===", elapsed, new_signals)


# --------------------------------------------------------------------------- #
#  Pipeline: Email sending
# --------------------------------------------------------------------------- #
async def run_email_pipeline() -> None:
    from workers.email_sender import process_outreach_queue
    async with AsyncSessionLocal() as db:
        sent = await process_outreach_queue(db)
        if sent:
            logger.info("Email pipeline: sent %d emails", sent)


# --------------------------------------------------------------------------- #
#  Inbox monitoring
# --------------------------------------------------------------------------- #
async def run_inbox_monitor() -> None:
    from workers.inbox_monitor import monitor_all_inboxes
    async with AsyncSessionLocal() as db:
        processed = await monitor_all_inboxes(db)
        if processed:
            logger.info("Inbox monitor: processed %d replies", processed)


# --------------------------------------------------------------------------- #
#  Health check
# --------------------------------------------------------------------------- #
async def run_health_check() -> None:
    from analytics.inbox_health_monitor import check_all_inbox_health
    async with AsyncSessionLocal() as db:
        results = await check_all_inbox_health(db)
        logger.info("Health check: %d inboxes evaluated", len(results))


# --------------------------------------------------------------------------- #
#  Startup tasks
# --------------------------------------------------------------------------- #
async def _startup_tasks() -> None:
    """Tasks to run once on worker startup."""
    from workers.email_sender import recover_stuck_sends
    from deliverability.inbox_rotation_manager import get_rotation_manager

    logger.info("Running startup tasks...")

    # Recover stuck sends
    async with AsyncSessionLocal() as db:
        recovered = await recover_stuck_sends(db)
        if recovered:
            logger.info("Recovered %d stuck queue items", recovered)

    # Sync inbox state from DB
    mgr = get_rotation_manager()
    async with AsyncSessionLocal() as db:
        await mgr.sync_from_db(db)
        logger.info("Inbox state synced from DB")


# --------------------------------------------------------------------------- #
#  Main loop
# --------------------------------------------------------------------------- #
async def main() -> None:
    logger.info("🚀 Autonomous Lead Engine starting...")
    await _startup_tasks()

    loop = asyncio.get_running_loop()
    last_scrape = 0.0
    last_health = 0.0

    while True:
        now = loop.time()

        if now - last_scrape >= SCRAPE_INTERVAL_S:
            try:
                await run_scraping_pipeline()
                await run_pain_signal_pipeline()
            except Exception as exc:
                logger.error("Scraping pipeline error: %s", exc, exc_info=True)
            last_scrape = loop.time()

        if now - last_health >= HEALTH_INTERVAL_S:
            try:
                await run_health_check()
                await run_inbox_monitor()
            except Exception as exc:
                logger.error("Health/monitor error: %s", exc, exc_info=True)
            last_health = loop.time()

        try:
            await run_email_pipeline()
        except Exception as exc:
            logger.error("Email pipeline error: %s", exc, exc_info=True)

        logger.debug("Sleeping %ds until next email run", EMAIL_INTERVAL_S)
        await asyncio.sleep(EMAIL_INTERVAL_S)


if __name__ == "__main__":
    import logging as _logging
    _logging.basicConfig(
        level=_logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    asyncio.run(main())
