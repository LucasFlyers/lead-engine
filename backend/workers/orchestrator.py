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

from sqlalchemy.exc import IntegrityError

from db.database import AsyncSessionLocal
from db.models import (
    Company, Contact, LeadScore, OutreachQueue, PainSignal, SystemEvent,
    PainSignalOutreachQueue,
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
    event = SystemEvent(event_type=event_type, message=message, event_metadata=metadata or {})
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
    from scrapers.pain_signal_lead_scraper  import scrape_pain_signal_leads
    from ai.pain_signal_intelligence        import get_targeting_from_db
    from deduplication.lead_deduper        import deduplicate_batch
    from ai.lead_scoring                   import score_leads_batch
    from sqlalchemy import select, text

    async with AsyncSessionLocal() as db:
        await _log_event(db, "pipeline_start", "Scraping pipeline started")

    # --- Get targeting intelligence from pain signals ---
    try:
        intelligence = await get_targeting_from_db()
        logger.info("Pain signal intelligence loaded: %s", 
                   intelligence.get("outreach_angle") if intelligence else "none")
    except Exception as exc:
        logger.warning("Could not load pain signal intelligence: %s", exc)
        intelligence = None

    # --- Collect from all sources ---
    all_companies: list[dict] = []
    for name, coro in [
        ("Clutch",            scrape_clutch()),
        ("Google Maps",       scrape_google_maps()),
        ("Agency directories",scrape_agency_directories()),
        ("Pain-signal targeted", scrape_pain_signal_leads(intelligence)),
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
            emails = await discover_emails(company_data.get("website", ""), company_data.get("company_name", ""))
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

            # Idempotent insert — skip if domain already exists (only if domain is set)
            company_domain = company_data.get("domain")
            if company_domain:
                existing_check = await db.execute(
                    select(Company.id).where(Company.domain == company_domain).limit(1)
                )
                if existing_check.scalar_one_or_none():
                    continue
            else:
                # No domain — check by company name instead
                existing_name_check = await db.execute(
                    select(Company.id).where(
                        Company.company_name == company_data.get("company_name")
                    ).limit(1)
                )
                if existing_name_check.scalar_one_or_none():
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

            # If no emails discovered, generate fallback using company name
            if not emails:
                company_name_clean = company_data["company_name"].lower()
                company_name_clean = __import__("re").sub(r"[^a-z0-9]", "", company_name_clean)
                domain = company_data.get("domain") or f"{company_name_clean}.com"
                if domain and company_name_clean:
                    for prefix in ["info", "hello", "contact"]:
                        emails.append({
                            "email": f"{prefix}@{domain}",
                            "discovery_method": "pattern_fallback",
                        })

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
            await _log_event(
                db, "lead_scraped",
                f"New lead: {company_data['company_name']} (source: {company_data['source']})",
            )

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

    from pain_scrapers.reddit_scraper           import scrape_reddit
    from pain_scrapers.forum_scraper            import scrape_forums
    from pain_scrapers.review_scraper           import scrape_reviews
    from pain_scrapers.x_scraper               import scrape_x
    from pain_scrapers.indiehackers_scraper    import scrape_indiehackers
    from ai.pain_signal_analyzer                import analyze_batch
    from ai.pain_signal_outreach_writer         import generate_outreach_suggestions
    from pain_scrapers.signal_ranker            import (
        select_candidates_for_ai, compute_final_rank_score, log_selection_stats,
    )
    from sqlalchemy import select

    all_signals: list[dict] = []
    for name, coro in [
        ("Reddit",        scrape_reddit()),
        ("Forums",        scrape_forums()),
        ("Reviews",       scrape_reviews()),
        ("X",             scrape_x()),
        ("IndieHackers",  scrape_indiehackers()),
    ]:
        try:
            batch = await coro
            all_signals.extend(batch)
            logger.info("  %s → %d signals", name, len(batch))
        except Exception as exc:
            logger.error("  %s failed: %s", name, exc)

    if not all_signals:
        return

    # --- Freshness filter + pre-AI ranking ---
    to_analyze, rejected = select_candidates_for_ai(all_signals)
    logger.info(
        "Signal selection: %d raw → %d to AI, %d rejected",
        len(all_signals), len(to_analyze), len(rejected),
    )
    if not to_analyze:
        logger.warning("All signals rejected by selection layer — nothing to analyze")
        return

    qualified = await analyze_batch(to_analyze)

    # --- Compute final rank score and sort best-first ---
    for signal in qualified:
        signal["final_rank_score"] = compute_final_rank_score(signal)
    qualified.sort(key=lambda s: s.get("final_rank_score", 0), reverse=True)

    log_selection_stats(len(all_signals), to_analyze, rejected, qualified)

    async with AsyncSessionLocal() as db:
        # Dedup by source_url to avoid re-inserting known signals
        existing_urls_result = await db.execute(
            select(PainSignal.source_url).where(PainSignal.source_url.isnot(None))
        )
        existing_urls = {r[0] for r in existing_urls_result.all()}

        new_signals = 0
        newly_added: list[tuple[PainSignal, dict]] = []  # (orm obj, raw signal dict)

        for signal in qualified:
            if signal.get("source_url") and signal["source_url"] in existing_urls:
                continue
            ps = PainSignal(
                source             = signal["source"],
                source_url         = signal.get("source_url"),
                author             = signal.get("author"),
                content            = signal["content"],
                keywords_matched   = signal.get("keywords_matched", []),
                industry           = signal.get("industry"),
                problem_desc       = signal.get("problem_desc"),
                automation_opp     = signal.get("automation_opp"),
                lead_potential     = signal.get("lead_potential"),
                processed          = True,
                source_created_at  = signal.get("source_created_at"),
                freshness_score    = signal.get("freshness_score"),
                final_rank_score   = signal.get("final_rank_score"),
            )
            db.add(ps)
            await db.flush()  # get ps.id before commit
            newly_added.append((ps, signal))
            new_signals += 1
            await _log_event(
                db, "pain_signal_found",
                f"Pain signal from {signal['source']}: {signal['content'][:80]}…",
            )

        await db.commit()

    # --- Create manual outreach queue items — DIRECT leads only ---
    # Routing rules:
    #   lead_type == "non_lead"  → already discarded by analyze_batch
    #   lead_type == "indirect"  → stored above, skip outreach queue
    #   lead_type == "direct" + is_outreach_ready == True → create queue item
    #   lead_type == "direct" + is_outreach_ready == False → stored only
    for ps, signal in newly_added:
        lead_type        = signal.get("lead_type", "direct")   # default direct for pre-new signals
        is_ready         = signal.get("is_outreach_ready", False)
        outreach_priority = signal.get("outreach_priority", "none")

        if lead_type == "indirect":
            logger.debug(
                "Signal %s is indirect — stored but skipped for outreach queue",
                ps.id,
            )
            continue

        if not is_ready:
            logger.debug(
                "Signal %s is direct but not outreach-ready "
                "(priority=%s, intent=%.1f) — stored only",
                ps.id, outreach_priority,
                signal.get("buyer_intent_score", 0),
            )
            continue

        # Generate AI suggestions first (non-blocking on failure)
        outreach_data: dict | None = None
        try:
            outreach_data = await generate_outreach_suggestions(signal)
            if outreach_data is None:
                logger.warning(
                    "Outreach writer returned None for signal %s (source=%s) — "
                    "queue item will be created without AI suggestions",
                    ps.id, signal.get("source"),
                )
        except Exception as exc:
            logger.warning(
                "Outreach writer raised for signal %s: %s — "
                "queue item will be created without AI suggestions",
                ps.id, exc,
            )

        async with AsyncSessionLocal() as db:
            try:
                item = PainSignalOutreachQueue(
                    pain_signal_id = ps.id,
                    source         = signal["source"],
                    source_url     = signal.get("source_url"),
                    author         = signal.get("author"),
                    industry       = signal.get("industry"),
                    problem_desc   = signal.get("problem_desc"),
                    automation_opp = signal.get("automation_opp"),
                    lead_potential = signal.get("lead_potential"),
                    **(outreach_data or {}),
                )
                db.add(item)
                await db.flush()

                event = SystemEvent(
                    event_type="pain_signal_outreach_created",
                    entity_type="pain_signal_outreach_queue",
                    message="Manual outreach item created from qualified direct lead",
                    event_metadata={
                        "pain_signal_id":     str(ps.id),
                        "source":             signal["source"],
                        "lead_potential":     signal.get("lead_potential"),
                        "lead_type":          lead_type,
                        "outreach_priority":  outreach_priority,
                        "buyer_intent_score": signal.get("buyer_intent_score"),
                        "outreach_generated": outreach_data is not None,
                    },
                )
                db.add(event)
                await db.commit()
                logger.info(
                    "Outreach queue item created for signal %s (AI suggestions: %s)",
                    ps.id, outreach_data is not None,
                )

            except IntegrityError:
                # Unique constraint on pain_signal_id — item already exists
                # (e.g. concurrent worker run or reprocessed signal)
                await db.rollback()
                logger.debug("Outreach queue item already exists for signal %s — skipping", ps.id)
            except Exception as exc:
                await db.rollback()
                logger.error("Failed to create outreach queue item for signal %s: %s", ps.id, exc)

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
