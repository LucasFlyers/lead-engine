"""Google Maps business scraper."""
import asyncio
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import async_playwright, Page, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "digital marketing agency",
    "software development company",
    "IT consulting firm",
    "business automation services",
    "data analytics company",
]


def extract_domain(url: str) -> Optional[str]:
    try:
        return urlparse(url).netloc.replace("www.", "").lower()
    except Exception:
        return None


async def scrape_google_maps_query(page: Page, query: str, location: str = "United States") -> list[dict]:
    """Scrape Google Maps for a specific search query."""
    companies = []
    search_url = f"https://www.google.com/maps/search/{query.replace(' ', '+')}+{location.replace(' ', '+')}"

    try:
        await page.goto(search_url, wait_until="networkidle", timeout=30000)
        await asyncio.sleep(3)

        # Scroll to load more results
        results_panel = await page.query_selector('[role="feed"]')
        if results_panel:
            for _ in range(5):
                await results_panel.evaluate("el => el.scrollTop += 2000")
                await asyncio.sleep(1.5)

        items = await page.query_selector_all('[data-result-index]')
        for item in items[:20]:
            try:
                name_el = await item.query_selector(".fontHeadlineSmall")
                name = await name_el.inner_text() if name_el else None

                category_el = await item.query_selector(".fontBodyMedium:first-child")
                category = await category_el.inner_text() if category_el else None

                address_el = await item.query_selector('[data-item-id="address"]')
                address = await address_el.inner_text() if address_el else None

                website_el = await item.query_selector('[data-item-id="authority"]')
                website = await website_el.inner_text() if website_el else None

                if name:
                    companies.append({
                        "company_name": name.strip(),
                        "website": f"https://{website}" if website else None,
                        "domain": extract_domain(f"https://{website}") if website else None,
                        "industry": category.strip() if category else query,
                        "location": address.strip() if address else location,
                        "source": "google_maps",
                        "scraped_at": datetime.utcnow().isoformat(),
                    })
            except Exception as e:
                logger.debug(f"Error parsing Maps result: {e}")

    except PlaywrightTimeout:
        logger.warning(f"Timeout scraping Maps: {search_url}")
    except Exception as e:
        logger.error(f"Error scraping Maps: {e}")

    return companies


async def scrape_google_maps(locations: list[str] = None) -> list[dict]:
    """Main entry point to scrape Google Maps."""
    if locations is None:
        locations = ["New York", "San Francisco", "Chicago", "Austin", "Boston"]

    all_companies = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = await context.new_page()

        for query in SEARCH_QUERIES:
            for location in locations[:3]:
                logger.info(f"Scraping Maps: {query} in {location}")
                companies = await scrape_google_maps_query(page, query, location)
                all_companies.extend(companies)
                await asyncio.sleep(3)

        await browser.close()

    logger.info(f"Google Maps scraper: found {len(all_companies)} companies")
    return all_companies
