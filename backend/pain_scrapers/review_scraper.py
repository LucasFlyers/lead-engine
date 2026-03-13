"""G2 and Capterra review pain signal scraper."""
import asyncio
import logging
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

PAIN_KEYWORDS = [
    "manual", "spreadsheet", "time consuming", "automate", "wish",
    "tedious", "inefficient", "clunky", "workaround", "export", "import",
    "copy paste", "repetitive", "hours", "error prone",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
}

G2_SEARCH_URL = "https://www.g2.com/search?utf8=%E2%9C%93&query={query}"
CAPTERRA_SEARCH_URL = "https://www.capterra.com/p/search/?search_term={query}"


async def scrape_g2_reviews(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Scrape G2 for reviews mentioning pain signals."""
    signals = []
    url = G2_SEARCH_URL.format(query=query.replace(" ", "+"))

    try:
        resp = await client.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")

        for review in soup.select(".review-text"):
            text = review.get_text(strip=True).lower()
            matched = [kw for kw in PAIN_KEYWORDS if kw in text]
            if matched:
                signals.append({
                    "source": "g2",
                    "source_url": url,
                    "author": "anonymous",
                    "content": review.get_text(strip=True)[:600],
                    "keywords_matched": matched,
                    "scraped_at": datetime.utcnow().isoformat(),
                })
    except Exception as e:
        logger.error(f"Error scraping G2: {e}")

    return signals


async def scrape_capterra_reviews(client: httpx.AsyncClient, query: str) -> list[dict]:
    """Scrape Capterra for pain signal reviews."""
    signals = []
    url = CAPTERRA_SEARCH_URL.format(query=query.replace(" ", "+"))

    try:
        resp = await client.get(url, headers=HEADERS, timeout=20)
        soup = BeautifulSoup(resp.text, "html.parser")

        for review in soup.select(".review-snippet"):
            text = review.get_text(strip=True).lower()
            matched = [kw for kw in PAIN_KEYWORDS if kw in text]
            if matched:
                signals.append({
                    "source": "capterra",
                    "source_url": url,
                    "author": "anonymous",
                    "content": review.get_text(strip=True)[:600],
                    "keywords_matched": matched,
                    "scraped_at": datetime.utcnow().isoformat(),
                })
    except Exception as e:
        logger.error(f"Error scraping Capterra: {e}")

    return signals


REVIEW_QUERIES = [
    "reporting automation", "data entry", "workflow automation",
    "manual process", "spreadsheet replacement",
]


async def scrape_reviews() -> list[dict]:
    """Main entry point for review scraping."""
    all_signals = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        for query in REVIEW_QUERIES:
            g2 = await scrape_g2_reviews(client, query)
            cap = await scrape_capterra_reviews(client, query)
            all_signals.extend(g2)
            all_signals.extend(cap)
            await asyncio.sleep(2)

    logger.info(f"Review scraper: found {len(all_signals)} pain signals")
    return all_signals
