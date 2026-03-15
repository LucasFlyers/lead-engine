"""Reddit pain signal scraper."""
import asyncio
import logging
from datetime import datetime
from typing import Optional
import httpx

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "manually entering data",
    "too much manual work",
    "hours copying spreadsheet",
    "can't afford software",
    "we do this by hand",
    "need to automate",
    "drowning in admin",
    "repetitive tasks",
    "how do i automate",
    "manual process killing",
    "so much time on",
    "wasting time on",
    "spreadsheet nightmare",
    "manual data entry",
    "our team spends hours",
]

# Keep alias for keyword matching in post content
PAIN_KEYWORDS = SEARCH_QUERIES

TARGET_SUBREDDITS = [
    "smallbusiness",
    "Entrepreneur",
    "startups",
    "business",
    "sales",
    "marketing",
    "accounting",
    "humanresources",
    "projectmanagement",
    "ecommerce",
    "realestate",
    "legaladvice",
    "Bookkeeping",
]

REDDIT_API = "https://www.reddit.com"
HEADERS = {"User-Agent": "LeadEngine/1.0 (research bot)"}


async def search_subreddit(client: httpx.AsyncClient, subreddit: str, keyword: str) -> list[dict]:
    """Search a subreddit for a keyword."""
    signals = []
    url = f"{REDDIT_API}/r/{subreddit}/search.json"
    params = {"q": keyword, "sort": "new", "limit": 25, "t": "month"}

    try:
        resp = await client.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code != 200:
            return signals

        data = resp.json()
        posts = data.get("data", {}).get("children", [])

        for post in posts:
            d = post.get("data", {})
            text = f"{d.get('title', '')} {d.get('selftext', '')}".lower()

            matched = [kw for kw in PAIN_KEYWORDS if kw in text]
            if not matched:
                continue

            signals.append({
                "source": "reddit",
                "source_url": f"https://reddit.com{d.get('permalink', '')}",
                "author": d.get("author", ""),
                "content": f"{d.get('title', '')} {d.get('selftext', '')[:500]}",
                "keywords_matched": matched,
                "scraped_at": datetime.utcnow().isoformat(),
            })

    except Exception as e:
        logger.error(f"Error searching r/{subreddit} for '{keyword}': {e}")

    return signals


async def scrape_reddit(max_subreddits: int = 5) -> list[dict]:
    """Scrape Reddit for pain signals."""
    all_signals = []
    subreddits = TARGET_SUBREDDITS[:max_subreddits]

    async with httpx.AsyncClient() as client:
        for subreddit in subreddits:
            for keyword in SEARCH_QUERIES[:8]:  # Limit API calls
                signals = await search_subreddit(client, subreddit, keyword)
                all_signals.extend(signals)
                await asyncio.sleep(1.5)  # Respect Reddit rate limits

    # Deduplicate by source_url
    seen = set()
    unique = []
    for s in all_signals:
        if s["source_url"] not in seen:
            seen.add(s["source_url"])
            unique.append(s)

    logger.info(f"Reddit scraper: found {len(unique)} pain signals")
    return unique
