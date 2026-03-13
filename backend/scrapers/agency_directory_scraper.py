"""Agency directory scraper (UpCity, GoodFirms, etc.)."""
import asyncio
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DIRECTORIES = {
    "upcity": "https://upcity.com/local-marketing-agencies/united-states/",
    "goodfirms": "https://www.goodfirms.co/directory/category/digital-marketing",
    "designrush": "https://www.designrush.com/agency/digital-marketing",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def extract_domain(url: str) -> Optional[str]:
    try:
        return urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        return None


async def scrape_goodfirms(client: httpx.AsyncClient, pages: int = 3) -> list[dict]:
    """Scrape GoodFirms directory."""
    companies = []
    for page in range(1, pages + 1):
        try:
            url = f"{DIRECTORIES['goodfirms']}?page={page}"
            resp = await client.get(url, headers=HEADERS, timeout=20)
            soup = BeautifulSoup(resp.text, "html.parser")

            for card in soup.select(".cl-detail-tile"):
                try:
                    name_el = card.select_one(".company-name")
                    name = name_el.get_text(strip=True) if name_el else None

                    website_el = card.select_one('a[href*="//"]')
                    website = website_el.get("href") if website_el else None

                    location_el = card.select_one(".city-country-info")
                    location = location_el.get_text(strip=True) if location_el else None

                    if name:
                        companies.append({
                            "company_name": name,
                            "website": website,
                            "domain": extract_domain(website) if website else None,
                            "industry": "Digital Marketing",
                            "location": location,
                            "source": "goodfirms",
                            "scraped_at": datetime.utcnow().isoformat(),
                        })
                except Exception:
                    pass

            await asyncio.sleep(1.5)
        except Exception as e:
            logger.error(f"Error scraping GoodFirms page {page}: {e}")

    return companies


async def scrape_designrush(client: httpx.AsyncClient, pages: int = 3) -> list[dict]:
    """Scrape DesignRush directory."""
    companies = []
    for page in range(1, pages + 1):
        try:
            url = f"{DIRECTORIES['designrush']}?page={page}"
            resp = await client.get(url, headers=HEADERS, timeout=20)
            soup = BeautifulSoup(resp.text, "html.parser")

            for card in soup.select(".agency-card"):
                try:
                    name_el = card.select_one(".agency-card__name")
                    name = name_el.get_text(strip=True) if name_el else None

                    website_el = card.select_one('a.agency-card__website')
                    website = website_el.get("href") if website_el else None

                    location_el = card.select_one(".agency-card__location")
                    location = location_el.get_text(strip=True) if location_el else None

                    if name:
                        companies.append({
                            "company_name": name,
                            "website": website,
                            "domain": extract_domain(website) if website else None,
                            "industry": "Agency",
                            "location": location,
                            "source": "designrush",
                            "scraped_at": datetime.utcnow().isoformat(),
                        })
                except Exception:
                    pass

            await asyncio.sleep(1.5)
        except Exception as e:
            logger.error(f"Error scraping DesignRush page {page}: {e}")

    return companies


async def scrape_agency_directories() -> list[dict]:
    """Main entry point for agency directory scraping."""
    all_companies = []
    async with httpx.AsyncClient(follow_redirects=True) as client:
        gf = await scrape_goodfirms(client)
        dr = await scrape_designrush(client)
        all_companies.extend(gf)
        all_companies.extend(dr)

    logger.info(f"Agency directory scraper: found {len(all_companies)} companies")
    return all_companies
