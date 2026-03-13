"""Domain extraction and normalisation utilities."""
from typing import Optional
from urllib.parse import urlparse


def extract_domain(url: str) -> Optional[str]:
    """Extract clean domain from URL."""
    if not url:
        return None
    if not url.startswith("http"):
        url = "https://" + url
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower().lstrip("www.")
        return domain if domain else None
    except Exception:
        return None


def normalise_url(url: str) -> str:
    """Ensure URL has https scheme and no trailing slash."""
    if not url:
        return url
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url
    return url.rstrip("/")
