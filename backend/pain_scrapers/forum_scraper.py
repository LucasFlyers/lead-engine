"""Forum and community pain signal scraper (Indie Hackers, HackerNews, etc.)."""
import asyncio
import logging
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

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

HN_SEARCH = "https://hn.algolia.com/api/v1/search?query={query}&tags=story&hitsPerPage=20"
IH_SEARCH = "https://www.indiehackers.com/search?query={query}"


async def scrape_hacker_news(client: httpx.AsyncClient) -> list[dict]:
    """Scrape HackerNews for pain signals."""
    signals = []
    queries = ["manual process too long", "need to automate workflow", "hours of manual work small business"]

    for query in queries:
        try:
            resp = await client.get(
                HN_SEARCH.format(query=query.replace(" ", "+")),
                timeout=15,
            )
            data = resp.json()
            for hit in data.get("hits", []):
                text = f"{hit.get('title', '')} {hit.get('story_text', '') or ''}".lower()
                matched = [kw for kw in PAIN_KEYWORDS if kw in text]
                if matched:
                    signals.append({
                        "source": "hackernews",
                        "source_url": hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}",
                        "author": hit.get("author", ""),
                        "content": f"{hit.get('title', '')} {(hit.get('story_text') or '')[:300]}",
                        "keywords_matched": matched,
                        "scraped_at": datetime.utcnow().isoformat(),
                    })
        except Exception as e:
            logger.error(f"Error scraping HN: {e}")
        await asyncio.sleep(1)

    return signals


async def scrape_indie_hackers(client: httpx.AsyncClient) -> list[dict]:
    """Scrape Indie Hackers for pain signals."""
    signals = []
    queries = ["manual process taking too long", "need to automate", "repetitive tasks"]

    for query in queries:
        try:
            resp = await client.get(
                IH_SEARCH.format(query=query.replace(" ", "+")),
                headers=HEADERS, timeout=15,
            )
            soup = BeautifulSoup(resp.text, "html.parser")
            for post in soup.select(".post-preview"):
                text = post.get_text(strip=True).lower()
                matched = [kw for kw in PAIN_KEYWORDS if kw in text]
                if matched:
                    link_el = post.select_one("a")
                    href = link_el.get("href") if link_el else ""
                    signals.append({
                        "source": "indiehackers",
                        "source_url": f"https://www.indiehackers.com{href}",
                        "author": "anonymous",
                        "content": post.get_text(strip=True)[:500],
                        "keywords_matched": matched,
                        "scraped_at": datetime.utcnow().isoformat(),
                    })
        except Exception as e:
            logger.error(f"Error scraping IH: {e}")
        await asyncio.sleep(1)

    return signals


async def scrape_forums() -> list[dict]:
    """Main entry point for forum scraping."""
    all_signals = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        hn = await scrape_hacker_news(client)
        ih = await scrape_indie_hackers(client)
        all_signals.extend(hn)
        all_signals.extend(ih)

    logger.info(f"Forum scraper: found {len(all_signals)} pain signals")
    return all_signals
