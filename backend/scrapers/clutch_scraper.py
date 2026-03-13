"""Clutch.co company directory scraper."""
import asyncio
import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

CLUTCH_BASE = "https://clutch.co"
CATEGORIES = [
    "/agencies/digital-marketing",
    "/it-services",
    "/developers",
    "/agencies/seo",
    "/agencies/advertising",
]


def extract_domain(url: str) -> Optional[str]:
    try:
        parsed = urlparse(url)
        return parsed.netloc.replace("www.", "").lower()
    except Exception:
        return None


async def scrape_clutch_page(page: Page, url: str) -> list[dict]:
    """Scrape a single Clutch listing page."""
    companies = []
    try:
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_selector(".provider-list-item", timeout=10000)

        items = await page.query_selector_all(".provider-list-item")
        for item in items:
            try:
                name_el = await item.query_selector(".company_info h3")
                name = await name_el.inner_text() if name_el else None

                website_el = await item.query_selector('a[href*="website"]')
                website = await website_el.get_attribute("href") if website_el else None

                location_el = await item.query_selector(".locality")
                location = await location_el.inner_text() if location_el else None

                industry_el = await item.query_selector(".focus-areas")
                industry = await industry_el.inner_text() if industry_el else None

                if name:
                    companies.append({
                        "company_name": name.strip(),
                        "website": website,
                        "domain": extract_domain(website) if website else None,
                        "industry": industry.strip() if industry else "Agency",
                        "location": location.strip() if location else None,
                        "source": "clutch",
                        "scraped_at": datetime.utcnow().isoformat(),
                    })
            except Exception as e:
                logger.debug(f"Error parsing Clutch item: {e}")

    except PlaywrightTimeout:
        logger.warning(f"Timeout scraping Clutch page: {url}")
    except Exception as e:
        logger.error(f"Error scraping Clutch page {url}: {e}")

    return companies


async def scrape_clutch(max_pages: int = 5) -> list[dict]:
    """Main entry point to scrape Clutch directories."""
    all_companies = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = await context.new_page()

        for category in CATEGORIES:
            for pg in range(1, max_pages + 1):
                url = f"{CLUTCH_BASE}{category}?page={pg}"
                logger.info(f"Scraping Clutch: {url}")
                companies = await scrape_clutch_page(page, url)
                all_companies.extend(companies)
                if not companies:
                    break
                await asyncio.sleep(2)

        await browser.close()

    logger.info(f"Clutch scraper: found {len(all_companies)} companies")
    return all_companies
