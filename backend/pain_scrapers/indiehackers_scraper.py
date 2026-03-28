"""
Indie Hackers pain-signal scraper — standalone source adapter.

Discovery strategy:
  Fetches IH forum-category listing pages.  IH is a Next.js app whose forum
  pages server-side render post data inside a <script id="__NEXT_DATA__"> tag.
  We extract that JSON first; CSS-selector and link-pattern passes are kept as
  fallbacks in case the JSON shape changes.

  NOTE: IH *search* pages (/search?query=...) use client-side Algolia and
  return an empty SSR shell — they will never yield candidates and have been
  removed from TARGET_PAGES.

Output:
  List of candidate dicts compatible with signal_ranker + pain_signal_analyzer.
  source = "indiehackers"

Config (env vars):
  IH_ENABLED                   (default: true)
  IH_MAX_PAGES_PER_RUN         (default: 7)
  IH_CONCURRENCY               (default: 2)
  IH_TIMEOUT_SECONDS           (default: 15)
  IH_MIN_CANDIDATES_TARGET     (default: 10)
  IH_MIN_HEURISTIC_SCORE       (default: 0)   ← relaxed; negative = spam filter only
  IH_REQUEST_DELAY_SECONDS     (default: 1.5)
"""
import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from pain_scrapers.signal_ranker import normalize_source_timestamp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

IH_ENABLED     = os.getenv("IH_ENABLED",     "true").lower() in ("1", "true", "yes")
_CONCURRENCY   = int(os.getenv("IH_CONCURRENCY",               "2"))
_TIMEOUT       = float(os.getenv("IH_TIMEOUT_SECONDS",         "15"))
_MAX_PAGES     = int(os.getenv("IH_MAX_PAGES_PER_RUN",         "7"))
_TARGET_CANDS  = int(os.getenv("IH_MIN_CANDIDATES_TARGET",     "10"))
_MIN_HEURISTIC = int(os.getenv("IH_MIN_HEURISTIC_SCORE",       "0"))   # 0 = spam-filter only
_REQUEST_DELAY = float(os.getenv("IH_REQUEST_DELAY_SECONDS",   "1.5"))

IH_BASE = "https://www.indiehackers.com"

# Forum listing pages — these have SSR content in __NEXT_DATA__.
# Search pages (/search?query=...) intentionally excluded (client-side Algolia).
TARGET_PAGES: list[tuple[str, str]] = [
    (f"{IH_BASE}/forum",                    "forum:main"),
    (f"{IH_BASE}/forum/growing-a-business", "forum:growing"),
    (f"{IH_BASE}/forum/help-and-advice",    "forum:help"),
    (f"{IH_BASE}/forum/ask-ih",             "forum:ask"),
    (f"{IH_BASE}/forum/automation",         "forum:automation"),
    (f"{IH_BASE}/forum/general",            "forum:general"),
    (f"{IH_BASE}/forum/share-your-ideas",   "forum:ideas"),
]

# CSS selectors tried in order when __NEXT_DATA__ yields nothing.
TITLE_SELECTORS: list[str] = [
    "h2 a", "h3 a", "h4 a",
    ".post-title a", ".feed-item__title a",
    "article h2 a", "article h3 a",
    ".thread-title a", ".discussion-title a",
    ".story-title a", "[class*='title'] a",
]

BODY_SELECTORS: list[str] = [
    ".post-body", ".feed-item__description",
    "article p", ".excerpt", ".preview",
    ".snippet", ".thread-body", "[class*='body']",
    "[class*='description']", "[class*='excerpt']",
]

# Valid IH post/forum/group URL path patterns
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
    "just hit $", "just crossed $", "we hit $", "reached $",
    "milestone:", "month 1:", "month 2:", "month 3:",
    "we just launched", "just launched", "today we launched",
    "product hunt", "launching today", "announcing",
    "i built", "i made", "i created", "check out my",
    "i help businesses", "i help founders", "we help companies",
    "book a demo", "free trial", "sign up",
    "hustle hard", "the grind", "failure is a lesson",
    "mindset shift", "lessons learned from",
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
    pages_attempted:    int = 0
    pages_succeeded:    int = 0
    pages_failed:       int = 0
    next_data_found:    int = 0   # pages where __NEXT_DATA__ was present
    next_data_posts:    int = 0   # candidates extracted via __NEXT_DATA__
    html_fallback_posts:int = 0   # candidates extracted via HTML selectors
    raw_candidates:     int = 0
    freshness_skipped:  int = 0
    heuristic_rejected: int = 0
    final_candidates:   int = 0
    parse_failures:     list = field(default_factory=list)

    def log_summary(self) -> None:
        logger.info(
            "IH scraper done | pages=%d/%d (%d failed) | "
            "__next_data__=%d/%d posts | html_fallback=%d posts | "
            "raw=%d heuristic_rejected=%d final=%d | parse_failures=%s",
            self.pages_succeeded, self.pages_attempted, self.pages_failed,
            self.next_data_found, self.next_data_posts,
            self.html_fallback_posts,
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
# __NEXT_DATA__ EXTRACTION  (primary strategy)
# ---------------------------------------------------------------------------

def _extract_next_data(html: str) -> dict | None:
    """Parse Next.js __NEXT_DATA__ JSON from page HTML. Returns None if absent/invalid."""
    soup = BeautifulSoup(html, "html.parser")
    script = soup.find("script", id="__NEXT_DATA__")
    if not script:
        return None
    try:
        return json.loads(script.string or "")
    except (json.JSONDecodeError, TypeError):
        return None


def _candidates_from_next_data(data: dict, label: str) -> list[dict]:
    """
    Walk Next.js pageProps to find a post/thread array.

    Tries multiple paths because IH uses different structures across page types.
    Returns raw candidate dicts (not yet heuristic-scored).
    """
    candidates: list[dict] = []
    page_props = data.get("props", {}).get("pageProps", {})

    # Collect post arrays from several possible locations
    posts: list = []

    # Path 1: direct posts/threads key
    for key in ("posts", "threads", "items", "discussions"):
        val = page_props.get(key)
        if isinstance(val, list) and val:
            posts = val
            logger.info("IH [%s]: __NEXT_DATA__ posts at pageProps.%s (%d items)", label, key, len(val))
            break

    # Path 2: nested one level deeper
    if not posts:
        for outer_key, outer_val in page_props.items():
            if not isinstance(outer_val, dict):
                continue
            for key in ("posts", "threads", "items", "discussions"):
                val = outer_val.get(key)
                if isinstance(val, list) and val:
                    posts = val
                    logger.info(
                        "IH [%s]: __NEXT_DATA__ posts at pageProps.%s.%s (%d items)",
                        label, outer_key, key, len(val),
                    )
                    break
            if posts:
                break

    # Path 3: look for any list-valued key that contains dicts with a "title" field
    if not posts:
        for key, val in page_props.items():
            if isinstance(val, list) and val and isinstance(val[0], dict) and val[0].get("title"):
                posts = val
                logger.info("IH [%s]: __NEXT_DATA__ posts via heuristic key %r (%d items)", label, key, len(val))
                break

    if not posts:
        logger.info("IH [%s]: __NEXT_DATA__ present but no post array found — keys: %s",
                    label, list(page_props.keys())[:12])
        return []

    for post in posts:
        if not isinstance(post, dict):
            continue

        title = post.get("title") or post.get("subject") or ""
        body  = post.get("body") or post.get("content") or post.get("excerpt") or ""
        if not title:
            continue

        # Build absolute URL
        url = post.get("url") or post.get("link") or ""
        if not url:
            slug = post.get("slug") or post.get("id") or ""
            if slug:
                url = f"{IH_BASE}/post/{slug}"
        if not url:
            continue
        if not url.startswith("http"):
            url = urljoin(IH_BASE, url)

        # Author
        user   = post.get("user") or post.get("author") or {}
        author = (user.get("username") or user.get("name") or "") if isinstance(user, dict) else str(user)

        # Timestamp
        created_at = (
            post.get("createdAt") or post.get("created_at") or
            post.get("publishedAt") or post.get("timestamp")
        )

        # Engagement
        votes    = post.get("votes")         or post.get("score")        or post.get("likes")    or 0
        comments = post.get("commentsCount") or post.get("num_comments") or post.get("comments") or 0

        candidates.append({
            "title":            str(title)[:200],
            "body":             str(body)[:600] if body else "",
            "source_url":       url,
            "author":           str(author),
            "source_created_at":created_at,
            "post_score":       int(votes)    if isinstance(votes,    (int, float)) else 0,
            "num_comments":     int(comments) if isinstance(comments, (int, float)) else 0,
        })

    return candidates


# ---------------------------------------------------------------------------
# HTML FALLBACK PARSING  (used when __NEXT_DATA__ yields nothing)
# ---------------------------------------------------------------------------

def _normalize_ih_url(href: str) -> str:
    if href.startswith("http"):
        return href.rstrip("/").lower()
    return urljoin(IH_BASE, href).rstrip("/").lower()


def _extract_nearby_body(link_el, soup: BeautifulSoup) -> str:
    ancestor = link_el.parent
    for _ in range(5):
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
    if ancestor:
        text = ancestor.get_text(separator=" ", strip=True)
        if len(text) > 30:
            return text[:400]
    return ""


def _html_fallback_parse(html: str, label: str) -> list[dict]:
    """
    CSS-selector + link-pattern fallback when __NEXT_DATA__ has no posts.
    Logs at INFO so failures are visible in Railway.
    """
    soup = BeautifulSoup(html, "html.parser")
    seen_urls: set[str] = set()
    candidates: list[dict] = []

    # Strategy 1: title selectors
    title_elements = []
    for sel in TITLE_SELECTORS:
        found = soup.select(sel)
        if found:
            title_elements = found
            logger.info("IH [%s]: HTML title selector %r matched %d elements", label, sel, len(found))
            break

    for el in title_elements:
        href  = el.get("href", "")
        title = el.get_text(strip=True)
        if not href or not title or len(title) < 8:
            continue
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
        body = _extract_nearby_body(el, soup)
        candidates.append({
            "title": title, "body": body,
            "source_url": urljoin(IH_BASE, href) if href.startswith("/") else href,
            "author": "", "source_created_at": None,
            "post_score": 0, "num_comments": 0,
        })

    # Strategy 2: any IH-pattern <a> link
    if not candidates:
        logger.info("IH [%s]: HTML title selectors yielded nothing — scanning all IH-pattern links", label)
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

    logger.info("IH [%s]: HTML fallback extracted %d candidates", label, len(candidates))
    return candidates


# ---------------------------------------------------------------------------
# PAGE PARSER  (orchestrates __NEXT_DATA__ → HTML fallback)
# ---------------------------------------------------------------------------

def _parse_page(html: str, page_url: str, label: str, diag: IHDiagnostics) -> list[dict]:
    """
    Extract post candidates from an IH page.

    Priority:
      1. __NEXT_DATA__ JSON (SSR data — most reliable for forum pages)
      2. CSS-selector HTML pass
      3. Any IH-pattern <a> link scan

    Logs HTML title + first 800 chars at INFO if zero candidates found.
    """
    soup_for_title = BeautifulSoup(html, "html.parser")
    page_title_el  = soup_for_title.find("title")
    page_title     = page_title_el.get_text(strip=True) if page_title_el else "(no <title>)"

    logger.info(
        "IH [%s]: parsing — url=%s  html_len=%d  title=%r",
        label, page_url, len(html), page_title[:80],
    )

    # --- Strategy 1: __NEXT_DATA__ ---
    next_data = _extract_next_data(html)
    if next_data is not None:
        diag.next_data_found += 1
        candidates = _candidates_from_next_data(next_data, label)
        if candidates:
            diag.next_data_posts += len(candidates)
            logger.info("IH [%s]: __NEXT_DATA__ → %d candidates", label, len(candidates))
            return candidates
        logger.info("IH [%s]: __NEXT_DATA__ present but yielded 0 candidates — trying HTML fallback", label)
    else:
        logger.info("IH [%s]: no __NEXT_DATA__ script tag found — trying HTML fallback", label)

    # --- Strategy 2 & 3: HTML ---
    candidates = _html_fallback_parse(html, label)
    diag.html_fallback_posts += len(candidates)

    if not candidates:
        # Dump first 800 chars so we can see what we're dealing with
        snippet = html[:800].replace("\n", " ").replace("\r", "")
        logger.warning(
            "IH [%s]: ZERO candidates extracted from %s\n"
            "  title:   %r\n"
            "  html[:800]: %s",
            label, page_url, page_title, snippet,
        )

    return candidates


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
            logger.info("IH [%s]: HTTP 200 — %s (%d bytes)", label, url, len(resp.content))
            return resp.text
        logger.warning("IH [%s]: HTTP %d — %s", label, resp.status_code, url)
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
        logger.info("IH scraper: disabled (IH_ENABLED=false)")
        return []

    diag         = IHDiagnostics()
    sem          = asyncio.Semaphore(_CONCURRENCY)
    seen_urls:   set[str] = set()
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
            raw = _parse_page(html, url, label, diag)
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
                    "source":            "indiehackers",
                    "source_url":        source_url,
                    "author":            raw.get("author", ""),
                    "title":             title,
                    "body":              body,
                    "content":           content,
                    "keywords_matched":  _extract_keywords(title, body),
                    "post_score":        raw.get("post_score", 0),
                    "num_comments":      raw.get("num_comments", 0),
                    "source_created_at": normalize_source_timestamp(
                        raw.get("source_created_at")
                    ),
                    "scraped_at":        datetime.utcnow().isoformat(),
                    "heuristic_score":   h_score,
                })

        await asyncio.sleep(_REQUEST_DELAY)

    diag.final_candidates = len(all_signals)
    diag.log_summary()

    # Sample output — first 5 candidates
    if all_signals:
        logger.info("IH scraper: sample candidates (first %d):", min(5, len(all_signals)))
        for i, s in enumerate(all_signals[:5], 1):
            logger.info(
                "  [%d] h=%d  %r  %s",
                i, s.get("heuristic_score", 0), s["title"][:80], s["source_url"],
            )
    else:
        logger.warning(
            "IH scraper: ZERO final candidates — "
            "raw=%d rejected=%d | check page structure at indiehackers.com/forum",
            diag.raw_candidates, diag.heuristic_rejected,
        )

    if len(all_signals) < _TARGET_CANDS:
        logger.info(
            "IH scraper: %d candidates (below target %d) — "
            "consider expanding TARGET_PAGES or checking __NEXT_DATA__ structure",
            len(all_signals), _TARGET_CANDS,
        )

    return all_signals
