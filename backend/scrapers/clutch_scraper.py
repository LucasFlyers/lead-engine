"""Clutch.co company directory scraper."""
import asyncio
import logging
import re
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

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


async def scrape_clutch_page(page, url: str) -> list[dict]:
    """Scrape a single Clutch listing page using a Playwright page object."""
    from playwright.async_api import TimeoutError as PlaywrightTimeout
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


async def _scrape_clutch_httpx(max_pages: int = 5) -> list[dict]:
    """HTTP-only fallback scraper for when Playwright is unavailable."""
    import httpx

    all_companies = []
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept": "text/html,application/xhtml+xml",
    }

    async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=20) as client:
        for category in CATEGORIES[:2]:
            for pg in range(1, min(max_pages, 3) + 1):
                url = f"{CLUTCH_BASE}{category}?page={pg}"
                try:
                    resp = await client.get(url)
                    if resp.status_code != 200:
                        break
                    names = re.findall(
                        r'class="company_info"[^>]*>.*?<h3[^>]*>(.*?)</h3>',
                        resp.text, re.DOTALL
                    )
                    for name in names[:20]:
                        clean = re.sub(r'<[^>]+>', '', name).strip()
                        if clean:
                            all_companies.append({
                                "company_name": clean,
                                "website": None,
                                "domain": None,
                                "industry": "Agency",
                                "location": None,
                                "source": "clutch",
                                "scraped_at": datetime.utcnow().isoformat(),
                            })
                    await asyncio.sleep(2)
                except Exception as e:
                    logger.warning(f"HTTP fallback error for {url}: {e}")

    return all_companies


async def scrape_clutch(max_pages: int = 5) -> list[dict]:
    """Main entry point to scrape Clutch directories."""
    try:
        from playwright.async_api import async_playwright
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

    except Exception as e:
        logger.warning(f"Playwright unavailable ({e}), falling back to HTTP scraping")
        companies = await _scrape_clutch_httpx(max_pages)
        logger.info(f"Clutch HTTP fallback: found {len(companies)} companies")
        return companies
