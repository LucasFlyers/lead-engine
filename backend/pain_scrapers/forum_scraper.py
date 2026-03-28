"""Forum and community pain signal scraper (HackerNews).

Indie Hackers is now a standalone source in indiehackers_scraper.py and is
run directly from the orchestrator.  scrape_forums() covers HN only.
"""
import asyncio
import logging
from datetime import datetime, timezone, timedelta

import httpx

from pain_scrapers.signal_ranker import HARD_MAX_DAYS, normalize_source_timestamp

logger = logging.getLogger(__name__)

PAIN_KEYWORDS = [
    "manual process taking too long",
    "hours of manual data entry",
    "we do this manually",
    "need to automate",
    "too much manual work",
    "spending hours on spreadsheets",
    "our team manually",
    "automate our workflow",
    "repetitive tasks killing productivity",
    "no budget for software",
    "can't afford salesforce",
    "small business automation",
    "overwhelmed with manual",
    "tired of manually",
    "wasting hours on",
]

HEADERS = {"User-Agent": "Mozilla/5.0 AppleWebKit/537.36"}

# HN Algolia API — apply a hard date ceiling so we never return ancient results.
# numericFilters uses Unix timestamps; created_at_i is seconds since epoch.
_HN_BASE = (
    "https://hn.algolia.com/api/v1/search"
    "?query={query}"
    "&tags=story"
    "&hitsPerPage=25"
    "&numericFilters=created_at_i%3E{since_ts}"   # created_at_i > since_ts
)
def _hn_url(query: str) -> str:
    """Build a date-filtered HN Algolia search URL."""
    since_ts = int(
        (datetime.now(timezone.utc) - timedelta(days=HARD_MAX_DAYS)).timestamp()
    )
    return _HN_BASE.format(query=query.replace(" ", "+"), since_ts=since_ts)


async def scrape_hacker_news(client: httpx.AsyncClient) -> list[dict]:
    """Scrape HackerNews for pain signals — only within the freshness window."""
    signals = []
    queries = [
        "manual process too long",
        "need to automate workflow",
        "hours of manual work small business",
    ]

    for query in queries:
        try:
            resp = await client.get(_hn_url(query), timeout=15)
            if resp.status_code != 200:
                continue
            data = resp.json()

            for hit in data.get("hits", []):
                title      = hit.get("title", "")
                story_text = hit.get("story_text") or ""
                text       = f"{title} {story_text}".lower()

                matched = [kw for kw in PAIN_KEYWORDS if kw in text]
                if not matched:
                    continue

                # Extract real post timestamp (Unix seconds from Algolia)
                raw_ts      = hit.get("created_at_i")         # prefer numeric
                if raw_ts is None:
                    raw_ts  = hit.get("created_at")            # ISO string fallback
                created_at  = normalize_source_timestamp(raw_ts)

                signals.append({
                    "source":           "hackernews",
                    "source_url":       hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                    "author":           hit.get("author", ""),
                    "title":            title,
                    "body":             story_text[:800],
                    "content":          f"{title}\n\n{story_text[:800]}".strip(),
                    "keywords_matched": matched,
                    "post_score":       hit.get("points", 0),
                    "num_comments":     hit.get("num_comments", 0),
                    "source_created_at":created_at,     # normalized datetime | None
                    "scraped_at":       datetime.utcnow().isoformat(),
                })

        except Exception as exc:
            logger.error("Error scraping HN for '%s': %s", query, exc)

        await asyncio.sleep(1)

    return signals


async def scrape_forums() -> list[dict]:
    """HackerNews forum scraping.  Indie Hackers is handled by indiehackers_scraper.py."""
    async with httpx.AsyncClient(follow_redirects=True) as client:
        hn = await scrape_hacker_news(client)

    logger.info("Forum scraper: %d HN signals", len(hn))
    return hn
