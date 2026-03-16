"""Email discovery from company websites."""
import asyncio
import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

EMAIL_REGEX = re.compile(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}")
CONTACT_PATHS = ["/contact", "/contact-us", "/about", "/team", "/about-us", "/get-in-touch"]
FALLBACK_PREFIXES = ["info", "hello", "contact", "hi", "sales", "team"]

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
    "Accept": "text/html,application/xhtml+xml",
}


def guess_domain_from_name(company_name: str) -> str:
    """Guess a company domain from its name."""
    import re
    # Clean company name to domain format
    name = company_name.lower().strip()
    # Remove common suffixes
    for suffix in [" inc", " llc", " ltd", " corp", " co", " company",
                   " group", " agency", " studio", " solutions", " services",
                   " technologies", " tech", ".com", ".io"]:
        name = name.replace(suffix, "")
    # Replace spaces and special chars with nothing
    name = re.sub(r"[^a-z0-9]", "", name)
    if name:
        return f"{name}.com"
    return ""


def validate_email(email: str) -> bool:
    """Validate email format and filter out common false positives."""
    if not EMAIL_REGEX.match(email):
        return False
    # Filter out image files, CSS, JS artifacts often caught as "emails"
    invalid_extensions = [".png", ".jpg", ".gif", ".css", ".js", ".svg"]
    domain_part = email.split("@")[1].lower()
    if any(domain_part.endswith(ext) for ext in invalid_extensions):
        return False
    # Filter common no-reply patterns
    invalid_prefixes = ["noreply", "no-reply", "donotreply", "bounce", "mailer-daemon"]
    local_part = email.split("@")[0].lower()
    if any(local_part.startswith(p) for p in invalid_prefixes):
        return False
    return True


def extract_emails_from_html(html: str) -> list[str]:
    """Extract and validate emails from HTML content."""
    # Decode mailto: links
    soup = BeautifulSoup(html, "html.parser")
    emails = set()

    for a_tag in soup.find_all("a", href=True):
        href = a_tag["href"]
        if href.startswith("mailto:"):
            email = href.replace("mailto:", "").split("?")[0].strip()
            if validate_email(email):
                emails.add(email.lower())

    # Pattern match in visible text
    text = soup.get_text()
    found = EMAIL_REGEX.findall(text)
    for email in found:
        if validate_email(email):
            emails.add(email.lower())

    return list(emails)


async def fetch_page(client: httpx.AsyncClient, url: str) -> Optional[str]:
    """Fetch a page and return its HTML."""
    try:
        resp = await client.get(url, headers=HEADERS, timeout=15, follow_redirects=True)
        if resp.status_code == 200:
            return resp.text
    except Exception as e:
        logger.debug(f"Failed to fetch {url}: {e}")
    return None


async def discover_emails(website: str, company_name: str = "") -> list[dict]:
    """Discover emails from a company website."""
    # If website is a job board URL, clear it and use domain guessing
    job_board_domains = [
        "remoteok.com", "weworkremotely.com", "himalayas.app",
        "remotive.com", "linkedin.com", "indeed.com", "glassdoor.com",
        "github.com", "wellfound.com", "angel.co",
    ]
    
    if website:
        parsed = urlparse(website if website.startswith("http") else f"https://{website}")
        if any(jb in parsed.netloc for jb in job_board_domains):
            website = ""  # Clear job board URL, will use domain guessing
    
    # If no website, try to guess from company name
    if not website and company_name:
        guessed = guess_domain_from_name(company_name)
        if guessed:
            website = f"https://{guessed}"
            logger.debug(f"Guessed domain {guessed} for {company_name}")
    
    if not website:
        return []

    # Normalize URL
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"

    base_domain = urlparse(website).netloc
    emails_found = []

    async with httpx.AsyncClient(follow_redirects=True, timeout=15) as client:
        # Try homepage first
        html = await fetch_page(client, website)
        if html:
            for email in extract_emails_from_html(html):
                emails_found.append({
                    "email": email,
                    "discovery_method": "homepage",
                })

        # Try contact/about pages
        for path in CONTACT_PATHS:
            url = urljoin(website, path)
            html = await fetch_page(client, url)
            if html:
                for email in extract_emails_from_html(html):
                    emails_found.append({
                        "email": email,
                        "discovery_method": f"contact_page:{path}",
                    })

    # Deduplicate by email
    seen = set()
    unique_emails = []
    for e in emails_found:
        if e["email"] not in seen:
            seen.add(e["email"])
            unique_emails.append(e)

    # If no emails found, generate fallback patterns
    if not unique_emails:
        for prefix in FALLBACK_PREFIXES:
            email = f"{prefix}@{base_domain}"
            unique_emails.append({
                "email": email,
                "discovery_method": "pattern_fallback",
            })

    logger.info(f"Discovered {len(unique_emails)} emails for {website}")
    return unique_emails
