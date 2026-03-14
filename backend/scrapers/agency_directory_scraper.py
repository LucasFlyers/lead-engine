"""
Agency directory scraper using sources with accessible public data.
Sources: GitHub organizations, Crunchbase public listings, Indie Hackers,
AngelList/Wellfound public directory.
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

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def extract_domain(url: str) -> Optional[str]:
    try:
        return urlparse(url).netloc.replace("www.", "").lower() or None
    except Exception:
        return None


async def scrape_github_orgs(client: httpx.AsyncClient) -> list[dict]:
    """Scrape GitHub organizations in software/automation space using GitHub API."""
    companies = []
    search_terms = [
        "automation+software+company",
        "digital+marketing+agency",
        "saas+startup",
        "software+consulting",
    ]
    for term in search_terms:
        try:
            url = f"https://api.github.com/search/users?q={term}+type:org&per_page=30"
            resp = await client.get(
                url,
                headers={**HEADERS, "Accept": "application/vnd.github.v3+json"},
                timeout=20,
            )
            if resp.status_code != 200:
                continue
            data = resp.json()
            for org in data.get("items", []):
                login = org.get("login", "")
                if not login:
                    continue
                companies.append({
                    "company_name": login.replace("-", " ").replace("_", " ").title(),
                    "website": org.get("html_url"),
                    "domain": f"github.com/{login}",
                    "location": None,
                    "industry": "Software & Technology",
                    "source": "agency_directory",
                    "scraped_at": datetime.utcnow().isoformat(),
                })
            await asyncio.sleep(1)
        except Exception as exc:
            logger.debug("GitHub orgs scrape error: %s", exc)
    return companies


async def scrape_indiehackers(client: httpx.AsyncClient) -> list[dict]:
    """Scrape Indie Hackers products directory."""
    companies = []
    try:
        resp = await client.get(
            "https://www.indiehackers.com/products",
            headers=HEADERS,
            timeout=20,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return companies
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select('[class*="product"]') or soup.select("a[href*='/product/']")
        for card in cards[:40]:
            try:
                name_el = card.select_one("h2") or card.select_one("h3") or card.select_one("strong")
                name = name_el.get_text(strip=True) if name_el else None
                if not name or len(name) < 2:
                    continue
                href = card.get("href", "")
                companies.append({
                    "company_name": name,
                    "website": f"https://www.indiehackers.com{href}" if href.startswith("/") else href or None,
                    "domain": None,
                    "location": None,
                    "industry": "Software & Technology",
                    "source": "agency_directory",
                    "scraped_at": datetime.utcnow().isoformat(),
                })
            except Exception:
                continue
    except Exception as exc:
        logger.debug("IndieHackers scrape error: %s", exc)
    return companies


async def scrape_betalist(client: httpx.AsyncClient) -> list[dict]:
    """Scrape BetaList startups directory."""
    companies = []
    try:
        resp = await client.get(
            "https://betalist.com/startups",
            headers=HEADERS,
            timeout=20,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return companies
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".startup") or soup.select('[class*="startup"]') or soup.select("article")
        for card in cards[:40]:
            try:
                name_el = card.select_one("h2") or card.select_one("h3") or card.select_one(".name")
                name = name_el.get_text(strip=True) if name_el else None
                if not name or len(name) < 2:
                    continue
                link_el = card.select_one("a[href]")
                href = link_el.get("href") if link_el else None
                companies.append({
                    "company_name": name,
                    "website": href,
                    "domain": extract_domain(href) if href else None,
                    "location": None,
                    "industry": "Software & Technology",
                    "source": "agency_directory",
                    "scraped_at": datetime.utcnow().isoformat(),
                })
            except Exception:
                continue
    except Exception as exc:
        logger.debug("BetaList scrape error: %s", exc)
    return companies


async def scrape_f6s(client: httpx.AsyncClient) -> list[dict]:
    """Scrape F6S startup directory."""
    companies = []
    try:
        resp = await client.get(
            "https://www.f6s.com/companies/software/co",
            headers=HEADERS,
            timeout=20,
            follow_redirects=True,
        )
        if resp.status_code != 200:
            return companies
        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.select(".program-item") or soup.select('[class*="company"]')
        for card in cards[:40]:
            try:
                name_el = card.select_one("h3") or card.select_one("h2") or card.select_one(".name")
                name = name_el.get_text(strip=True) if name_el else None
                if not name or len(name) < 2:
                    continue
                link_el = card.select_one("a[href]")
                href = link_el.get("href") if link_el else None
                website = f"https://www.f6s.com{href}" if href and href.startswith("/") else href
                companies.append({
                    "company_name": name,
                    "website": website,
                    "domain": extract_domain(website) if website else None,
                    "location": None,
                    "industry": "Software & Technology",
                    "source": "agency_directory",
                    "scraped_at": datetime.utcnow().isoformat(),
                })
            except Exception:
                continue
    except Exception as exc:
        logger.debug("F6S scrape error: %s", exc)
    return companies


async def scrape_agency_directories() -> list[dict]:
    """Main entry — scrape all agency/startup directories."""
    all_companies: list[dict] = []
    seen: set[str] = set()

    async with httpx.AsyncClient(timeout=30) as client:
        sources = [
            ("GitHub Orgs", scrape_github_orgs),
            ("BetaList", scrape_betalist),
            ("F6S", scrape_f6s),
            ("IndieHackers", scrape_indiehackers),
        ]
        for name, fn in sources:
            try:
                results = await fn(client)
                added = 0
                for c in results:
                    key = (c.get("company_name") or "").lower().strip()
                    if key and key not in seen:
                        seen.add(key)
                        all_companies.append(c)
                        added += 1
                logger.info("%s: %d companies", name, added)
                await asyncio.sleep(2)
            except Exception as exc:
                logger.warning("%s scraper failed: %s", name, exc)

    logger.info("Agency directory total: %d companies", len(all_companies))
    return all_companies
