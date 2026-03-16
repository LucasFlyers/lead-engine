"""
Pain Signal Lead Scraper.
Uses targeting intelligence from pain signals to find companies
that match the profile of businesses in pain.
Sources: RemoteOK, Remotive, Himalayas — filtered by industry keywords
from pain signal intelligence.
"""
import asyncio
import logging
from datetime import datetime
from typing import Optional
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; research/1.0)",
    "Accept": "application/json",
}

# Default industries to target if no pain signal intelligence available
DEFAULT_INDUSTRIES = [
    "accounting", "bookkeeping", "finance",
    "ecommerce", "retail", "real estate",
    "marketing", "agency", "consulting",
    "healthcare", "legal", "logistics",
]


def extract_domain(url: str) -> Optional[str]:
    try:
        return urlparse(url).netloc.replace("www.", "").lower() or None
    except Exception:
        return None


async def scrape_remoteok_by_industry(
    client: httpx.AsyncClient,
    industry_keywords: list[str],
) -> list[dict]:
    """Scrape RemoteOK and filter by industry keywords from pain signals."""
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

            # Check if job tags or description match our target industries
            tags = " ".join(job.get("tags", [])).lower()
            description = job.get("description", "").lower()[:200]
            job_text = f"{tags} {description} {company.lower()}"

            # Only include if matches at least one industry keyword
            matched = any(kw.lower() in job_text for kw in industry_keywords)
            if not matched:
                continue

            seen.add(company)
            url = job.get("company_url") or job.get("url", "")
            companies.append({
                "company_name": company,
                "website": url or None,
                "domain": extract_domain(url) if url else None,
                "location": "Remote",
                "industry": next(
                    (kw for kw in industry_keywords if kw.lower() in job_text),
                    "Business Services"
                ),
                "source": "pain_signal_targeted",
                "scraped_at": datetime.utcnow().isoformat(),
            })

    except Exception as exc:
        logger.debug("RemoteOK pain-signal scrape error: %s", exc)

    return companies


async def scrape_remotive_by_industry(
    client: httpx.AsyncClient,
    industry_keywords: list[str],
) -> list[dict]:
    """Scrape Remotive and filter by target industries."""
    companies = []
    seen = set()

    try:
        resp = await client.get(
            "https://remotive.com/api/remote-jobs?limit=200",
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

            category = job.get("category", "").lower()
            title = job.get("title", "").lower()
            job_text = f"{category} {title} {company.lower()}"

            matched = any(kw.lower() in job_text for kw in industry_keywords)
            if not matched:
                continue

            seen.add(company)
            url = job.get("url", "")
            companies.append({
                "company_name": company,
                "website": url or None,
                "domain": extract_domain(url) if url else None,
                "location": "Remote",
                "industry": next(
                    (kw for kw in industry_keywords if kw.lower() in job_text),
                    "Business Services"
                ),
                "source": "pain_signal_targeted",
                "scraped_at": datetime.utcnow().isoformat(),
            })

    except Exception as exc:
        logger.debug("Remotive pain-signal scrape error: %s", exc)

    return companies


async def scrape_himalayas_by_industry(
    client: httpx.AsyncClient,
    industry_keywords: list[str],
) -> list[dict]:
    """Scrape Himalayas and filter by target industries."""
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
        for job in data.get("jobs", []):
            company = job.get("companyName", "")
            if not company or company in seen:
                continue

            categories = " ".join(job.get("categories", [])).lower()
            title = job.get("title", "").lower()
            job_text = f"{categories} {title} {company.lower()}"

            matched = any(kw.lower() in job_text for kw in industry_keywords)
            if not matched:
                continue

            seen.add(company)
            website = job.get("companyUrl", "")
            companies.append({
                "company_name": company,
                "website": website or None,
                "domain": extract_domain(website) if website else None,
                "location": job.get("location", "Remote"),
                "industry": next(
                    (kw for kw in industry_keywords if kw.lower() in job_text),
                    "Business Services"
                ),
                "source": "pain_signal_targeted",
                "scraped_at": datetime.utcnow().isoformat(),
            })

    except Exception as exc:
        logger.debug("Himalayas pain-signal scrape error: %s", exc)

    return companies


async def scrape_pain_signal_leads(
    intelligence: Optional[dict] = None,
) -> list[dict]:
    """
    Main entry — scrape companies matching pain signal intelligence.
    If no intelligence available, uses default industry keywords.
    """
    # Get industry keywords from pain signal intelligence
    if intelligence:
        keywords = (
            intelligence.get("top_industries", []) +
            intelligence.get("search_keywords", [])
        )
        logger.info(
            "Pain-signal targeted scrape: keywords=%s, angle=%s",
            keywords[:5],
            intelligence.get("outreach_angle", ""),
        )
    else:
        keywords = DEFAULT_INDUSTRIES
        logger.info("Pain-signal scrape: using default industry keywords")

    if not keywords:
        keywords = DEFAULT_INDUSTRIES

    all_companies: list[dict] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=30) as client:
        for name, fn in [
            ("RemoteOK", scrape_remoteok_by_industry),
            ("Remotive", scrape_remotive_by_industry),
            ("Himalayas", scrape_himalayas_by_industry),
        ]:
            try:
                results = await fn(client, keywords)
                for c in results:
                    key = (c.get("company_name") or "").lower()
                    if key and key not in seen:
                        seen.add(key)
                        # Attach outreach angle from intelligence
                        if intelligence:
                            c["outreach_angle"] = intelligence.get("outreach_angle", "")
                            c["pain_context"] = intelligence.get("company_profile", "")
                        all_companies.append(c)
                logger.info("Pain-signal %s: %d companies", name, len(results))
                await asyncio.sleep(1)
            except Exception as exc:
                logger.warning("Pain-signal %s failed: %s", name, exc)

    logger.info("Pain-signal targeted scrape total: %d companies", len(all_companies))
    return all_companies
