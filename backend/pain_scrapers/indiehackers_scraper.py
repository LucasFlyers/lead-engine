"""
Indie Hackers pain-signal scraper — standalone source adapter.

Discovery strategy:
  Fetches IH search-result pages for a compact set of high-signal
  pain queries, then falls back to the forum listing page if searches
  yield few candidates.

  IH is a React SPA, but its search and forum pages server-side render
  enough HTML for SEO that we can extract post titles and URLs reliably.
  The main risk is CSS-class changes; multiple selector fallbacks + a
  link-pattern fallback keep this resilient.

Output:
  List of candidate dicts compatible with signal_ranker + pain_signal_analyzer.
  source = "indiehackers"

Fragility note:
  If IH changes their HTML structure, the TITLE_SELECTORS / BODY_SELECTORS
  lists below are the first thing to update.  The link-pattern fallback
  (any <a href="/post/..."> or <a href="/group/.../post/..."> element) is
  the last-resort and rarely breaks because URL patterns stay stable.

Config (env vars):
  IH_ENABLED                   (default: true)
  IH_MAX_PAGES_PER_RUN         (default: 6)
  IH_CONCURRENCY               (default: 2)
  IH_TIMEOUT_SECONDS           (default: 15)
  IH_MIN_CANDIDATES_TARGET     (default: 10)
  IH_MIN_HEURISTIC_SCORE       (default: 2)
  IH_REQUEST_DELAY_SECONDS     (default: 1.5)
"""
import asyncio
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlparse, urlunparse

import httpx
from bs4 import BeautifulSoup

from pain_scrapers.signal_ranker import normalize_source_timestamp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

IH_ENABLED        = os.getenv("IH_ENABLED",     "true").lower() in ("1", "true", "yes")
_CONCURRENCY      = int(os.getenv("IH_CONCURRENCY",               "2"))
_TIMEOUT          = float(os.getenv("IH_TIMEOUT_SECONDS",         "15"))
_MAX_PAGES        = int(os.getenv("IH_MAX_PAGES_PER_RUN",         "6"))
_TARGET_CANDS     = int(os.getenv("IH_MIN_CANDIDATES_TARGET",     "10"))
_MIN_HEURISTIC    = int(os.getenv("IH_MIN_HEURISTIC_SCORE",       "2"))
_REQUEST_DELAY    = float(os.getenv("IH_REQUEST_DELAY_SECONDS",   "1.5"))

IH_BASE = "https://www.indiehackers.com"

# Each entry is (url, label).  Labels appear in diagnostics.
# Search pages give the most targeted content — forum listing is fallback.
TARGET_PAGES: list[tuple[str, str]] = [
    (f"{IH_BASE}/search?query=manual+process+taking+too+long",  "search:manual_process"),
    (f"{IH_BASE}/search?query=need+to+automate+workflow",       "search:automate_workflow"),
    (f"{IH_BASE}/search?query=spreadsheet+process+problem",     "search:spreadsheet"),
    (f"{IH_BASE}/search?query=repetitive+tasks+business",       "search:repetitive_tasks"),
    (f"{IH_BASE}/search?query=wasting+time+on+admin",           "search:admin_waste"),
    (f"{IH_BASE}/search?query=hard+to+manage+clients",          "search:client_mgmt"),
    (f"{IH_BASE}/forum",                                        "forum:main"),
]

# CSS selectors tried in order to extract post titles.
# First selector that yields ≥1 result wins for that page.
TITLE_SELECTORS: list[str] = [
    "h2 a", "h3 a", "h4 a",
    ".post-title a", ".feed-item__title a",
    "article h2 a", "article h3 a",
    ".thread-title a", ".discussion-title a",
    ".story-title a", "[class*='title'] a",
]

# CSS selectors for body/snippet text adjacent to the post link.
BODY_SELECTORS: list[str] = [
    ".post-body", ".feed-item__description",
    "article p", ".excerpt", ".preview",
    ".snippet", ".thread-body", "[class*='body']",
    "[class*='description']", "[class*='excerpt']",
]

# Regex pattern for IH post / forum / group post URLs (path only)
_IH_POST_PATH_RE = re.compile(
    r"^(/post/|/forum/[^#?]+|/group/[^/]+/post/)",
    re.IGNORECASE,
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) "
        "Gecko/20100101 Firefox/125.0"
    ),
    "Accept":          "text/html,application/xhtml+xml,*/*",
    "Accept-Language": "en-US,en;q=0.5",
    "DNT":             "1",
}

# ---------------------------------------------------------------------------
# HEURISTIC KEYWORDS
# ---------------------------------------------------------------------------

DISQUALIFY_FRAGMENTS: list[str] = [
    # Pure growth bragging / MRR milestones without pain context
    "just hit $", "just crossed $", "we hit $", "reached $",
    "milestone:", "month 1:", "month 2:", "month 3:",
    # Launch announcements
    "we just launched", "just launched", "today we launched",
    "product hunt", "launching today", "announcing",
    # Self-promotional
    "i built", "i made", "i created", "check out my",
    "i help businesses", "i help founders", "we help companies",
    "book a demo", "free trial", "sign up",
    # Generic motivation / philosophy noise
    "hustle hard", "the grind", "failure is a lesson",
    "mindset shift", "lessons learned from",
    # Job postings
    "we're hiring", "now hiring", "join our team",
]

_PAIN_KW = [
    "manual", "manually", "repetitive", "tedious", "spreadsheet",
    "data entry", "automate", "automation", "bottleneck", "overwhelmed",
]
_TIME_KW = [
    "takes forever", "hours every", "all day", "too long",
    "wasting time", "wasting hours", "each week", "per week",
]
_WORKFLOW_KW = [
    "process", "workflow", "system", "pipeline", "crm",
    "follow-up", "follow up", "onboarding", "invoicing", "scheduling",
    "lead", "client", "customer", "operations", "admin",
]
_FRUSTRATION_KW = [
    "nightmare", "mess", "broken", "chaos", "chaotic",
    "frustrating", "hate", "annoying", "pain in", "killing",
]
_INTENT_KW = [
    "tool", "software", "app", "system", "platform",
    "recommendation", "what do you use", "how do you manage",
    "looking for", "need a better", "is there a way",
]
_QUESTION_STARTERS = (
    "how", "is there", "what", "can i", "anyone", "does anyone",
    "looking for", "need", "best way", "any tool", "recommend",
)


# ---------------------------------------------------------------------------
# DIAGNOSTICS
# ---------------------------------------------------------------------------

@dataclass
class IHDiagnostics:
    pages_attempted:   int = 0
    pages_succeeded:   int = 0
    pages_failed:      int = 0
    raw_candidates:    int = 0
    freshness_skipped: int = 0   # no timestamp — counted but not rejected
    heuristic_rejected:int = 0
    final_candidates:  int = 0
    parse_failures:    list = field(default_factory=list)   # labels that couldn't parse

    def log_summary(self) -> None:
        logger.info(
            "IH scraper done | pages=%d/%d (%d failed) | "
            "raw=%d heuristic_rejected=%d final=%d | "
            "parse_failures=%s",
            self.pages_succeeded, self.pages_attempted, self.pages_failed,
            self.raw_candidates,
            self.heuristic_rejected,
            self.final_candidates,
            self.parse_failures or "none",
        )


# ---------------------------------------------------------------------------
# HEURISTIC SCORING
# ---------------------------------------------------------------------------

def _is_disqualified(text: str) -> bool:
    lower = text.lower()
    return any(frag in lower for frag in DISQUALIFY_FRAGMENTS)


def score_post_relevance(title: str, body: str) -> int:
    """
    Returns -99 for clearly disqualified posts, otherwise roughly [0, 11].

      +2  pain keywords          +2  time-cost language
      +2  workflow/process words +2  frustration signals
      +2  solution-seeking intent +1  question form
      -99 hard disqualifier
    """
    full = f"{title} {body}".lower()

    if _is_disqualified(full):
        return -99

    if len(full.strip()) < 20:
        return 0

    score = 0
    if any(kw in full for kw in _PAIN_KW):        score += 2
    if any(kw in full for kw in _TIME_KW):        score += 2
    if any(kw in full for kw in _WORKFLOW_KW):    score += 2
    if any(kw in full for kw in _FRUSTRATION_KW): score += 2
    if any(kw in full for kw in _INTENT_KW):      score += 2
    if "?" in title or any(title.lower().startswith(w) for w in _QUESTION_STARTERS):
        score += 1

    return score


def _extract_keywords(title: str, body: str) -> list[str]:
    full = f"{title} {body}".lower()
    candidates = _PAIN_KW + _TIME_KW + _WORKFLOW_KW + _FRUSTRATION_KW
    return list(dict.fromkeys(kw for kw in candidates if kw in full))


# ---------------------------------------------------------------------------
# HTML PARSING
# ---------------------------------------------------------------------------

def _normalize_ih_url(href: str) -> str:
    """Convert a relative /post/... href to an absolute IH URL."""
    if href.startswith("http"):
        return href.rstrip("/").lower()
    return urljoin(IH_BASE, href).rstrip("/").lower()


def _parse_page(html: str, page_url: str, label: str) -> list[dict]:
    """
    Extract post candidates from an IH page.

    Multi-strategy:
      1. Try each TITLE_SELECTOR — first that yields results is used for titles.
      2. For each title <a>, look for adjacent body text via BODY_SELECTORS.
      3. Fallback: collect any <a> that matches IH post URL patterns.

    Returns list of raw candidate dicts (not yet heuristic-scored).
    """
    soup = BeautifulSoup(html, "html.parser")
    seen_urls: set[str] = set()
    candidates: list[dict] = []

    # --- Strategy 1: title selectors ---
    title_elements = []
    for sel in TITLE_SELECTORS:
        found = soup.select(sel)
        if found:
            title_elements = found
            logger.debug("IH [%s]: title selector %r matched %d elements", label, sel, len(found))
            break

    for el in title_elements:
        href  = el.get("href", "")
        title = el.get_text(strip=True)
        if not href or not title or len(title) < 8:
            continue

        # Only keep IH-internal post/forum/group links
        parsed = urlparse(href)
        if parsed.netloc and "indiehackers.com" not in parsed.netloc:
            continue
        path = parsed.path or href
        if not _IH_POST_PATH_RE.match(path) and "indiehackers.com" not in href:
            continue

        norm_url = _normalize_ih_url(href)
        if norm_url in seen_urls:
            continue
        seen_urls.add(norm_url)

        # Try to find body text near the title element
        body = _extract_nearby_body(el, soup)

        candidates.append({
            "title": title, "body": body,
            "source_url": urljoin(IH_BASE, href) if href.startswith("/") else href,
            "author": "", "source_created_at": None,
            "post_score": 0, "num_comments": 0,
        })

    # --- Strategy 2 (fallback): scan ALL IH-pattern links ---
    if not candidates:
        logger.debug("IH [%s]: title selectors yielded nothing — using link-pattern fallback", label)
        for a in soup.find_all("a", href=True):
            href = a.get("href", "")
            if not href:
                continue
            parsed = urlparse(href)
            path   = parsed.path or href
            if not _IH_POST_PATH_RE.match(path):
                continue
            title = a.get_text(strip=True)
            if not title or len(title) < 8:
                continue
            norm_url = _normalize_ih_url(href)
            if norm_url in seen_urls:
                continue
            seen_urls.add(norm_url)
            body = _extract_nearby_body(a, soup)
            candidates.append({
                "title": title, "body": body,
                "source_url": urljoin(IH_BASE, href) if href.startswith("/") else href,
                "author": "", "source_created_at": None,
                "post_score": 0, "num_comments": 0,
            })

    logger.debug("IH [%s]: extracted %d raw candidates", label, len(candidates))
    return candidates


def _extract_nearby_body(link_el, soup: BeautifulSoup) -> str:
    """
    Walk up the DOM from a link element and try BODY_SELECTORS on the parent.
    Falls back to the parent's full text (truncated).
    """
    # Try body selectors on the closest block ancestor
    ancestor = link_el.parent
    for _ in range(5):  # walk up max 5 levels
        if ancestor is None:
            break
        for sel in BODY_SELECTORS:
            hit = ancestor.select_one(sel)
            if hit:
                text = hit.get_text(separator=" ", strip=True)
                if len(text) > 30:
                    return text[:600]
        if ancestor.name in ("article", "section", "div", "li"):
            break
        ancestor = ancestor.parent

    # Fallback: raw text of the closest block ancestor
    if ancestor:
        text = ancestor.get_text(separator=" ", strip=True)
        if len(text) > 30:
            return text[:400]

    return ""


# ---------------------------------------------------------------------------
# HTTP LAYER
# ---------------------------------------------------------------------------

async def _fetch_page(
    client: httpx.AsyncClient,
    url:    str,
    label:  str,
    diag:   IHDiagnostics,
) -> str | None:
    """Fetch one IH page and return HTML string, or None on failure."""
    diag.pages_attempted += 1
    try:
        resp = await client.get(url, headers=HEADERS, timeout=_TIMEOUT)
        if resp.status_code == 200:
            diag.pages_succeeded += 1
            return resp.text
        logger.warning(
            "IH [%s]: HTTP %d — %s",
            label, resp.status_code, url,
        )
        diag.pages_failed += 1
        return None
    except httpx.TimeoutException:
        logger.warning("IH [%s]: timeout — %s", label, url)
        diag.pages_failed += 1
        return None
    except Exception as exc:
        logger.warning("IH [%s]: error fetching %s — %s", label, url, exc)
        diag.pages_failed += 1
        return None


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

async def scrape_indiehackers() -> list[dict]:
    """
    Fetch Indie Hackers pain signals.

    Returns candidate dicts compatible with signal_ranker + pain_signal_analyzer.
    Returns [] silently if IH_ENABLED is false.
    """
    if not IH_ENABLED:
        logger.debug("IH scraper: disabled (IH_ENABLED=false)")
        return []

    diag        = IHDiagnostics()
    sem         = asyncio.Semaphore(_CONCURRENCY)
    seen_urls:  set[str] = set()
    all_signals: list[dict] = []

    pages = TARGET_PAGES[:_MAX_PAGES]
    logger.info(
        "IH scraper: %d pages | concurrency=%d | heuristic_min=%d | target=%d candidates",
        len(pages), _CONCURRENCY, _MIN_HEURISTIC, _TARGET_CANDS,
    )

    async def _process_page(url: str, label: str) -> list[dict]:
        async with sem:
            html = await _fetch_page(client, url, label, diag)
            if html is None:
                diag.parse_failures.append(label)
                return []
            raw = _parse_page(html, url, label)
            diag.raw_candidates += len(raw)
            return raw

    async with httpx.AsyncClient(follow_redirects=True) as client:
        tasks = [_process_page(url, label) for url, label in pages]
        batch_results = await asyncio.gather(*tasks, return_exceptions=True)

        for result in batch_results:
            if isinstance(result, Exception):
                logger.warning("IH: page task raised: %s", result)
                continue
            if not isinstance(result, list):
                continue

            for raw in result:
                source_url = raw.get("source_url", "")
                norm_url   = source_url.rstrip("/").lower()
                if not norm_url or norm_url in seen_urls:
                    continue
                seen_urls.add(norm_url)

                title = raw.get("title", "")
                body  = raw.get("body",  "")

                h_score = score_post_relevance(title, body)
                if h_score == -99:
                    diag.heuristic_rejected += 1
                    continue
                if h_score < _MIN_HEURISTIC:
                    diag.heuristic_rejected += 1
                    continue

                if not raw.get("source_created_at"):
                    diag.freshness_skipped += 1

                content = f"{title}\n\n{body}".strip() if body else title

                all_signals.append({
                    # Core pipeline fields
                    "source":            "indiehackers",
                    "source_url":        source_url,
                    "author":            raw.get("author", ""),
                    "title":             title,
                    "body":              body,
                    "content":           content,
                    "keywords_matched":  _extract_keywords(title, body),
                    # Engagement
                    "post_score":        raw.get("post_score", 0),
                    "num_comments":      raw.get("num_comments", 0),
                    # Freshness — normalized_source_timestamp handles None safely
                    "source_created_at": normalize_source_timestamp(
                        raw.get("source_created_at")
                    ),
                    "scraped_at":        datetime.utcnow().isoformat(),
                    # Pre-AI hint
                    "heuristic_score":   h_score,
                })

            # Polite delay between batches
            await asyncio.sleep(_REQUEST_DELAY)

    diag.final_candidates = len(all_signals)
    diag.log_summary()

    if len(all_signals) < _TARGET_CANDS:
        logger.info(
            "IH scraper: %d candidates (below target %d) — "
            "consider expanding TARGET_PAGES or lowering IH_MIN_HEURISTIC_SCORE",
            len(all_signals), _TARGET_CANDS,
        )

    return all_signals
