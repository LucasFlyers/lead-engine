"""
Clutch.co scraper — HTTP only, no Playwright required.
Uses httpx + BeautifulSoup to scrape agency listings.
"""
import asyncio
import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

CLUTCH_BASE = "https://clutch.co"
CATEGORIES = [
    "/agencies/digital-marketing",
    "/it-services",
    "/developers",
    "/agencies/seo",
]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Cache-Control": "no-cache",
}


def extract_domain(url: str) -> Optional[str]:
    try:
        return urlparse(url).netloc.replace("www.", "").lower() or None
    except Exception:
        return None


async def scrape_clutch_category(
    client: httpx.AsyncClient, category: str, pages: int = 3
) -> list[dict]:
    companies = []
    for page in range(1, pages + 1):
        try:
            url = f"{CLUTCH_BASE}{category}?page={page}"
            resp = await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
            if resp.status_code != 200:
                logger.debug("Clutch %s page %d: HTTP %d", category, page, resp.status_code)
                break

            soup = BeautifulSoup(resp.text, "html.parser")

            # Try multiple selectors — Clutch updates its HTML periodically
            cards = (
                soup.select(".provider-list-item")
                or soup.select('[class*="provider"]')
                or soup.select("li.sg-provider")
                or soup.select('[data-uid]')
            )

            if not cards:
                logger.debug("Clutch %s page %d: no cards found", category, page)
                break

            for card in cards:
                try:
                    # Company name
                    name_el = (
                        card.select_one("h3")
                        or card.select_one(".company_info h3")
                        or card.select_one('[class*="company-name"]')
                    )
                    name = name_el.get_text(strip=True) if name_el else None
                    if not name:
                        continue

                    # Website
                    website_el = card.select_one('a[href*="http"]')
                    website = website_el.get("href") if website_el else None

                    # Location
                    loc_el = (
                        card.select_one(".locality")
                        or card.select_one('[class*="location"]')
                        or card.select_one('[class*="city"]')
                    )
                    location = loc_el.get_text(strip=True) if loc_el else None

                    companies.append({
                        "company_name": name,
                        "website": website,
                        "domain": extract_domain(website) if website else None,
                        "location": location,
                        "industry": "Marketing & Advertising",
                        "source": "clutch",
                        "scraped_at": datetime.utcnow().isoformat(),
                    })
                except Exception as exc:
                    logger.debug("Clutch card parse error: %s", exc)

            await asyncio.sleep(1.5)  # polite delay

        except Exception as exc:
            logger.warning("Clutch %s page %d error: %s", category, page, exc)
            break

    return companies


async def scrape_clutch(pages_per_category: int = 2) -> list[dict]:
    """Main entry point — scrape all Clutch categories."""
    all_companies: list[dict] = []
    seen_domains: set[str] = set()

    async with httpx.AsyncClient(timeout=30) as client:
        for category in CATEGORIES:
            try:
                companies = await scrape_clutch_category(client, category, pages_per_category)
                for c in companies:
                    domain = c.get("domain")
                    if domain and domain in seen_domains:
                        continue
                    if domain:
                        seen_domains.add(domain)
                    all_companies.append(c)
                logger.info("Clutch %s: %d companies", category, len(companies))
            except Exception as exc:
                logger.warning("Clutch category %s failed: %s", category, exc)
            await asyncio.sleep(2)

    logger.info("Clutch scraper total: %d companies", len(all_companies))
    return all_companies
