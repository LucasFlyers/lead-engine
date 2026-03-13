"""scraper.py — Manual scraper trigger endpoints."""
import asyncio
import logging
from fastapi import APIRouter, BackgroundTasks, HTTPException
from pydantic import BaseModel
from typing import List, Optional

logger = logging.getLogger(__name__)
router = APIRouter()


class ScrapeRequest(BaseModel):
    sources: List[str] = ["clutch", "google_maps", "reddit"]
    keywords: Optional[List[str]] = None
    max_pages: int = 3


@router.post("/trigger")
async def trigger_scrape(req: ScrapeRequest, background_tasks: BackgroundTasks):
    """Trigger a scraping run in the background."""
    background_tasks.add_task(_run_scrape_pipeline, req)
    return {"status": "started", "sources": req.sources}


@router.get("/status")
async def scrape_status():
    """Return last scrape timestamps (simplified)."""
    return {"status": "idle", "last_run": None}


async def _run_scrape_pipeline(req: ScrapeRequest):
    """Background task that runs the full scrape → score → queue pipeline."""
    from ..scrapers.clutch_scraper import ClutchScraper
    from ..scrapers.google_maps_scraper import GoogleMapsScraper
    from ..scrapers.email_discovery import EmailDiscovery
    from ..pain_scrapers.reddit_scraper import RedditScraper
    from ..ai.pain_signal_analyzer import PainSignalAnalyzer
    from ..ai.lead_scoring import LeadScorer
    from ..deduplication.lead_deduper import LeadDeduper
    from ..db.database import AsyncSessionLocal
    from ..db.models import Company, Contact, LeadScore, PainSignal, OutreachQueue, SystemEvent
    from datetime import datetime

    logger.info("Scrape pipeline started: %s", req.sources)
    companies = []

    if "clutch" in req.sources:
        scraper = ClutchScraper(max_pages=req.max_pages)
        results = await scraper.run()
        companies.extend(results)

    if "google_maps" in req.sources:
        scraper = GoogleMapsScraper(max_results_per_query=15)
        results = await scraper.run()
        companies.extend(results)

    pain_signals = []
    if "reddit" in req.sources:
        reddit = RedditScraper(keywords=req.keywords)
        pain_signals = await reddit.run()

    async with AsyncSessionLocal() as session:
        # Dedup
        deduper = LeadDeduper()
        unique, dupes = deduper.deduplicate_batch(companies)
        logger.info("Dedup: %d unique / %d dupes", len(unique), len(dupes))

        # Persist companies
        for c in unique:
            company = Company(**{k: v for k, v in c.items()
                                 if k in ["company_name","website","domain","industry",
                                          "location","source","is_duplicate"]})
            session.add(company)
        await session.commit()

        # Pain signal analysis
        if pain_signals:
            analyzer = PainSignalAnalyzer()
            qualified = await analyzer.analyze_batch(pain_signals)
            for signal in qualified:
                ps = PainSignal(
                    source=signal.get("source", "reddit"),
                    source_url=signal.get("source_url"),
                    raw_text=signal.get("raw_text","")[:3000],
                    keywords=signal.get("keywords", []),
                    industry=signal.get("industry"),
                    problem_desc=signal.get("problem_description"),
                    automation_opp=signal.get("automation_opportunity"),
                    lead_potential=signal.get("lead_potential"),
                )
                session.add(ps)
            await session.commit()

        # Log completion event
        event = SystemEvent(
            event_type="scrape_complete",
            severity="info",
            title=f"Scrape complete: {len(unique)} companies, {len(pain_signals)} signals",
            description=f"Sources: {req.sources}",
        )
        session.add(event)
        await session.commit()

    logger.info("Scrape pipeline complete")
