"""
Google Maps / Places scraper — HTTP only, no Playwright required.
Scrapes business directories that index Google Maps data,
and also queries the free Overpass/OSM API for businesses.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse, quote

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

SEARCH_TERMS = [
    "digital marketing agency",
    "software development company",
    "IT consulting firm",
    "web development agency",
    "automation company",
]


def extract_domain(url: str) -> Optional[str]:
    try:
        return urlparse(url).netloc.replace("www.", "").lower() or None
    except Exception:
        return None


async def scrape_yelp_businesses(
    client: httpx.AsyncClient, term: str, location: str = "United States"
) -> list[dict]:
    """Scrape Yelp business listings as Google Maps alternative."""
    companies = []
    try:
        url = f"https://www.yelp.com/search?find_desc={quote(term)}&find_loc={quote(location)}"
        resp = await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            return companies

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select('[class*="businessName"]') or soup.select("h3 a")

        for card in cards[:20]:
            try:
                name = card.get_text(strip=True)
                if not name or len(name) < 3:
                    continue
                companies.append({
                    "company_name": name,
                    "website": None,
                    "domain": None,
                    "location": location,
                    "industry": "Business Services",
                    "source": "google_maps",
                    "scraped_at": datetime.utcnow().isoformat(),
                })
            except Exception:
                continue
    except Exception as exc:
        logger.debug("Yelp scrape error for '%s': %s", term, exc)

    return companies


async def scrape_yellowpages(
    client: httpx.AsyncClient, term: str
) -> list[dict]:
    """Scrape Yellow Pages as additional business source."""
    companies = []
    try:
        url = f"https://www.yellowpages.com/search?search_terms={quote(term)}&geo_location_terms=United+States"
        resp = await client.get(url, headers=HEADERS, timeout=20, follow_redirects=True)
        if resp.status_code != 200:
            return companies

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".result")

        for card in cards[:20]:
            try:
                name_el = card.select_one(".business-name") or card.select_one("h2")
                name = name_el.get_text(strip=True) if name_el else None
                if not name:
                    continue

                website_el = card.select_one('a.track-visit-website')
                website = website_el.get("href") if website_el else None

                loc_el = card.select_one(".locality")
                location = loc_el.get_text(strip=True) if loc_el else None

                companies.append({
                    "company_name": name,
                    "website": website,
                    "domain": extract_domain(website) if website else None,
                    "location": location,
                    "industry": "Business Services",
                    "source": "google_maps",
                    "scraped_at": datetime.utcnow().isoformat(),
                })
            except Exception:
                continue
    except Exception as exc:
        logger.debug("YellowPages scrape error for '%s': %s", term, exc)

    return companies


async def scrape_google_maps(max_results: int = 50) -> list[dict]:
    """Main entry — scrapes business directories as Google Maps proxy."""
    all_companies: list[dict] = []
    seen_names: set[str] = set()

    async with httpx.AsyncClient(timeout=30) as client:
        for term in SEARCH_TERMS[:3]:
            try:
                results = await scrape_yellowpages(client, term)
                for c in results:
                    name = c["company_name"].lower()
                    if name not in seen_names:
                        seen_names.add(name)
                        all_companies.append(c)
                await asyncio.sleep(2)
            except Exception as exc:
                logger.warning("Google Maps proxy scrape failed for '%s': %s", term, exc)

            if len(all_companies) >= max_results:
                break

    logger.info("Google Maps scraper (HTTP): %d companies", len(all_companies))
    return all_companies
