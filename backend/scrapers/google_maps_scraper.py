"""
Company scraper using Himalayas.app and Remotive APIs — both publicly accessible.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)


def extract_domain(url: str) -> Optional[str]:
    try:
        return urlparse(url).netloc.replace("www.", "").lower() or None
    except Exception:
        return None


async def scrape_himalayas(client: httpx.AsyncClient) -> list[dict]:
    """Scrape Himalayas.app remote jobs API."""
    companies = []
    seen = set()
    try:
        resp = await client.get(
            "https://himalayas.app/jobs/api",
            headers={"Accept": "application/json"},
            timeout=20,
        )
        if resp.status_code != 200:
            return companies
        data = resp.json()
        for job in data.get("jobs", [])[:100]:
            company = job.get("companyName", "")
            if not company or company in seen:
                continue
            seen.add(company)
            website = job.get("companyUrl", "")
            companies.append({
                "company_name": company,
                "website": website or None,
                "domain": extract_domain(website) if website else None,
                "location": job.get("location", "Remote"),
                "industry": ", ".join(job.get("categories", ["Software"])[:2]),
                "source": "google_maps",
                "scraped_at": datetime.utcnow().isoformat(),
            })
    except Exception as exc:
        logger.debug("Himalayas error: %s", exc)
    return companies


async def scrape_remotive(client: httpx.AsyncClient) -> list[dict]:
    """Scrape Remotive.com jobs API."""
    companies = []
    seen = set()
    try:
        resp = await client.get(
            "https://remotive.com/api/remote-jobs?limit=100",
            headers={"Accept": "application/json"},
            timeout=20,
        )
        if resp.status_code != 200:
            return companies
        data = resp.json()
        for job in data.get("jobs", []):
            company = job.get("company_name", "")
            if not company or company in seen:
                continue
            seen.add(company)
            # Use company_url (homepage) not url (job posting)
            website = job.get("company_url") or None
            companies.append({
                "company_name": company,
                "website": website,
                "domain": extract_domain(website) if website else None,
                "location": "Remote",
                "industry": job.get("category", "Software"),
                "source": "google_maps",
                "scraped_at": datetime.utcnow().isoformat(),
            })
    except Exception as exc:
        logger.debug("Remotive error: %s", exc)
    return companies


async def scrape_google_maps(max_results: int = 50) -> list[dict]:
    """Main entry — scrapes remote job boards for company data."""
    all_companies: list[dict] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=30) as client:
        for name, fn in [("Himalayas", scrape_himalayas), ("Remotive", scrape_remotive)]:
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

    logger.info("Google Maps scraper total: %d companies", len(all_companies))
    return all_companies
