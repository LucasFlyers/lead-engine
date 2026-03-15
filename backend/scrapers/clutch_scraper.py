"""
Lead scraper using startup job boards — companies hiring = active businesses.
Sources: We Work Remotely, Remote OK, Himalayas.app (all publicly accessible).
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def extract_domain(url: str) -> Optional[str]:
    try:
        return urlparse(url).netloc.replace("www.", "").lower() or None
    except Exception:
        return None


async def scrape_remoteok(client: httpx.AsyncClient) -> list[dict]:
    """Scrape Remote OK job board — companies are active businesses."""
    companies = []
    seen = set()
    try:
        resp = await client.get(
            "https://remoteok.com/api",
            headers={**HEADERS, "Accept": "application/json"},
            timeout=20,
        )
        if resp.status_code != 200:
            return companies
        jobs = resp.json()
        for job in jobs:
            if not isinstance(job, dict):
                continue
            company = job.get("company", "")
            if not company or company in seen:
                continue
            seen.add(company)
            # Use company_url (homepage) not url (job posting)
            website = job.get("company_url") or job.get("company_website") or None
            companies.append({
                "company_name": company,
                "website": website,
                "domain": extract_domain(website) if website else None,
                "location": "Remote",
                "industry": job.get("tags", ["Software"])[0] if job.get("tags") else "Software",
                "source": "clutch",
                "scraped_at": datetime.utcnow().isoformat(),
            })
    except Exception as exc:
        logger.debug("RemoteOK error: %s", exc)
    return companies


async def scrape_weworkremotely(client: httpx.AsyncClient) -> list[dict]:
    """Scrape We Work Remotely job listings."""
    companies = []
    seen = set()
    categories = [
        "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "https://weworkremotely.com/categories/remote-sales-and-marketing-jobs.rss",
    ]
    for url in categories:
        try:
            resp = await client.get(url, headers=HEADERS, timeout=20)
            if resp.status_code != 200:
                continue
            soup = BeautifulSoup(resp.text, "xml")
            for item in soup.find_all("item")[:30]:
                try:
                    title = item.find("title")
                    if not title:
                        continue
                    text = title.get_text()
                    # Format is "Company: Role"
                    if ":" in text:
                        company = text.split(":")[0].strip()
                    else:
                        company = text.strip()
                    if not company or company in seen or len(company) < 2:
                        continue
                    seen.add(company)
                    # WWR RSS has no company homepage — leave website blank
                    companies.append({
                        "company_name": company,
                        "website": None,
                        "domain": None,
                        "location": "Remote",
                        "industry": "Software & Technology",
                        "source": "clutch",
                        "scraped_at": datetime.utcnow().isoformat(),
                    })
                except Exception:
                    continue
            await asyncio.sleep(1)
        except Exception as exc:
            logger.debug("WWR error: %s", exc)
    return companies


async def scrape_clutch(pages_per_category: int = 2) -> list[dict]:
    """Main entry — scrapes job boards for active companies."""
    all_companies: list[dict] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=30) as client:
        for name, fn in [("RemoteOK", scrape_remoteok), ("WeWorkRemotely", scrape_weworkremotely)]:
            try:
                results = await fn(client)
                for c in results:
                    key = (c.get("company_name") or "").lower()
                    if key and key not in seen:
                        seen.add(key)
                        all_companies.append(c)
                logger.info("%s: %d companies", name, len(results))
                await asyncio.sleep(1)
            except Exception as exc:
                logger.warning("%s failed: %s", name, exc)

    logger.info("Clutch scraper total: %d companies", len(all_companies))
    return all_companies
