"""
Reddit pain signal scraper — high-signal discovery engine v2.

Architecture:
  Phase 0 — Connectivity probe: single test request before launching any tasks.
  Phase 1 — Feed scraping (primary): hot + new feeds for all eligible subreddits.
             If feed candidates already meet REDDIT_MIN_FEED_CANDIDATES, search
             is skipped entirely.
  Phase 1b — Search (fallback/augmentation): only runs when feeds are below
             threshold.  Scoped to SEARCH_SUBREDDITS only, capped at
             REDDIT_MAX_SEARCH_TASKS total tasks, with a per-subreddit and
             global 429 circuit-breaker.
  Phase 2 — Heuristic scoring: lightweight scorer filters noise before AI.
  Phase 3 — Comment enrichment: top comments for highest-scoring posts.
  Phase 4 — Signal assembly: output compatible with pain_signal_analyzer.

Key protections:
  - INVALID_SUBREDDITS static denylist (banned / private — never fetched)
  - Runtime bad-subreddit detection: 403/404 adds subreddit to in-run denylist
  - 429 circuit-breaker: per-subreddit strike counter + global search abort
  - Separate semaphores for feeds (higher) and search (lower)
  - All failures logged at WARNING with status / content-type / body preview

Backward-compatible output keys:
  source, source_url, author, content, subreddit, keywords_matched
  + enriched extras: title, body, top_comments_text, post_score, num_comments
"""
import asyncio
import logging
import os
import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlparse, urlunparse

import httpx

from pain_scrapers.signal_ranker import normalize_source_timestamp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CONFIGURATION — change these without touching logic
# ---------------------------------------------------------------------------

SUBREDDIT_GROUPS: dict[str, list[str]] = {
    # Core business operators — highest-value pain signals
    "business_ops": [
        "smallbusiness", "Entrepreneur", "startups", "SaaS", "ecommerce",
        "dropshipping", "consulting", "marketing", "sales", "business",
    ],
    # Functional roles that drive workflow pain
    "functional_roles": [
        "accounting", "Bookkeeping", "humanresources", "recruiting",
        "projectmanagement", "sysadmin",
    ],
    # Industry-specific communities
    "industry_specific": [
        "realestate", "legaladvice", "Dentistry", "MedicalPractice",
        "construction", "logistics", "restaurantowners",
        "Manufacturing", "insurance",
    ],
    # Solo operators and agency owners
    "solo_operators": [
        "freelance", "freelancing", "sidehustle", "workonline",
        "digitalnomad", "agency",
    ],
    # Tech-adjacent with workflow pain
    "tech_adjacent": [
        "nocode", "webdev", "dataengineering", "analytics",
        "businessintelligence",
    ],
}

# Subreddits that are known to be banned, private, or consistently unavailable.
# These are never fetched, saving HTTP budget for viable communities.
INVALID_SUBREDDITS: set[str] = {
    "operations",   # banned
    "trucking",     # private
}

ALL_SUBREDDITS: list[str] = [
    sub for subs in SUBREDDIT_GROUPS.values() for sub in subs
    if sub.lower() not in INVALID_SUBREDDITS
]

# Search is limited to these high-value subreddits only.
# Other subreddits are feed-only, reducing total search volume dramatically.
SEARCH_SUBREDDITS: list[str] = [
    "smallbusiness", "Entrepreneur", "startups", "SaaS", "ecommerce",
    "Bookkeeping", "humanresources", "recruiting", "sales", "marketing",
]

# Compact high-signal search phrases — highest yield per query.
# Replaces the 39-query Cartesian product that produced 456 search tasks.
SEARCH_QUERIES: list[str] = [
    "doing this manually",
    "manual data entry",
    "how do you manage",
    "automate my workflow",
    "is there a tool for",
    "takes hours every week",
    "wasting time on",
    "spreadsheet is killing",
]

# Posts matching any of these are immediately rejected (not sent to AI)
DISQUALIFY_KEYWORDS: list[str] = [
    "i built", "i made", "i created", "i automated", "i just launched",
    "i wrote a", "my tool", "my app", "my saas", "here's how i",
    "how i automated", "how i built", "i saved", "we saved",
    "case study", "success story", "tutorial", "show hn", "show reddit",
    "announcing", "introducing", "launch day", "product hunt",
    "i've been building", "i'm building",
    "yc s", "yc w", "ycombinator",
    "just got fired", "got laid off",
]

# Heuristic keyword groups — fast in-process scoring only
_PAIN_KW     = ["manual", "repetitive", "time consuming", "tedious",
                "overwhelmed", "bottleneck", "automate", "automation"]
_TIME_KW     = ["hours", "days", "every day", "all day", "takes forever",
                "too long", "wasting", "each week", "per week"]
_WORKFLOW_KW = ["process", "system", "workflow", "spreadsheet", "excel",
                "csv", "data entry", "pipeline", "tracker"]
_FRUSTRATION = ["tired", "frustrated", "annoying", "hate", "sick of",
                "killing me", "nightmare", "pain in", "ugh", "drives me"]
_SCALE_KW    = ["team", "clients", "leads", "customers", "employees",
                "staff", "our company", "my business", "we have"]
_QUESTION_STARTERS = (
    "how", "is there", "what", "can i", "anyone", "does anyone",
    "looking for", "need", "best way", "any software", "any tool",
    "recommend", "suggestion", "advice",
)


# ---------------------------------------------------------------------------
# ENV-CONFIGURABLE FETCH CONTROLS
# ---------------------------------------------------------------------------
# Override any of these in Railway / .env without touching code.
_FEED_CONCURRENCY    = int(os.getenv("REDDIT_FEED_CONCURRENCY",             "6"))
_SEARCH_CONCURRENCY  = int(os.getenv("REDDIT_SEARCH_CONCURRENCY",           "2"))
_TIMEOUT             = float(os.getenv("REDDIT_REQUEST_TIMEOUT_SECONDS",    "12"))
_FEED_JITTER         = float(os.getenv("REDDIT_FEED_JITTER_SECONDS",        "0.4"))
_SEARCH_JITTER       = float(os.getenv("REDDIT_SEARCH_JITTER_SECONDS",      "1.5"))
_MIN_FEED_CANDS      = int(os.getenv("REDDIT_MIN_FEED_CANDIDATES",          "80"))
_MAX_SEARCH_TASKS    = int(os.getenv("REDDIT_MAX_SEARCH_TASKS",             "30"))
_MAX_SEARCH_QUERIES  = int(os.getenv("REDDIT_MAX_SEARCH_QUERIES_PER_SUB",   "2"))
_MAX_SEARCH_SUBS     = int(os.getenv("REDDIT_MAX_SEARCH_SUBREDDITS",        "10"))
_TARGET_POOL         = int(os.getenv("REDDIT_TARGET_CANDIDATE_POOL",        "150"))
_SUB_429_LIMIT       = int(os.getenv("REDDIT_SUBREDDIT_429_LIMIT",          "2"))
_GLOBAL_429_LIMIT    = int(os.getenv("REDDIT_GLOBAL_429_LIMIT",             "10"))

LIMITS: dict = {
    # Concurrency — feeds get more, search gets less
    "feed_semaphore":        _FEED_CONCURRENCY,
    "search_semaphore":      _SEARCH_CONCURRENCY,
    "batch_size":            10,      # tasks per asyncio.gather chunk
    # Volume
    "posts_per_feed":        30,
    "posts_per_search":      25,
    # Quality gate
    "min_relevance_score":   4,       # heuristic 0–13 scale
    "min_post_score":        -5,      # discard heavily downvoted
    # Comment enrichment
    "max_comment_fetches":   80,
    "max_comments_per_post": 4,
    "min_comment_len":       25,
    # Timing
    "request_timeout_s":     _TIMEOUT,
    "feed_delay_s":          0.5,     # base sleep after each feed request
    "feed_jitter_s":         _FEED_JITTER,
    "search_delay_s":        1.0,     # base sleep after each search request
    "search_jitter_s":       _SEARCH_JITTER,
    "rate_limit_backoff_s":  25,      # extra sleep on 429
    # Feed-first thresholds
    "min_feed_candidates":   _MIN_FEED_CANDS,   # skip search if feeds yield this many
    "target_candidate_pool": _TARGET_POOL,       # stop early if pool already large
    # Search circuit-breaker
    "max_search_tasks":      _MAX_SEARCH_TASKS,
    "max_search_queries_per_sub": _MAX_SEARCH_QUERIES,
    "max_search_subreddits": _MAX_SEARCH_SUBS,
    "subreddit_429_limit":   _SUB_429_LIMIT,
    "global_429_limit":      _GLOBAL_429_LIMIT,
    # Feeds
    "feeds_to_pull":         ["hot", "new"],
}

# old.reddit.com serves the same JSON API but bypasses www.reddit.com bot-detection.
REDDIT_BASE = "https://old.reddit.com"

REDDIT_USER_AGENT = os.getenv(
    "REDDIT_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
)

FEED_HEADERS = {
    "User-Agent":      REDDIT_USER_AGENT,
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "DNT":             "1",
}

# Search requests reuse the same headers; defined separately for clarity
SEARCH_HEADERS = FEED_HEADERS


def _reddit_url(path: str) -> str:
    """Build a full old.reddit.com URL from a /r/... path."""
    return f"{REDDIT_BASE}{path}"


# ---------------------------------------------------------------------------
# PERMANENT-ERROR SENTINEL
# ---------------------------------------------------------------------------

class _PermError:
    """
    Returned by _get_json when the response is a permanent error (403 / 404).
    Callers use `isinstance(result, _PermError)` to detect and record bad subreddits.
    """
    __slots__ = ("status_code",)

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


# ---------------------------------------------------------------------------
# DIAGNOSTIC COUNTERS
# ---------------------------------------------------------------------------

@dataclass
class FetchDiagnostics:
    """Mutable counters collected across all fetch tasks in a single run."""
    # HTTP-layer counters
    attempts:                  int = 0
    http_success:              int = 0
    timeouts:                  int = 0
    parse_failures:            int = 0
    schema_mismatches:         int = 0
    empty_responses:           int = 0
    non_200_by_code:           dict = field(default_factory=lambda: defaultdict(int))
    # Extraction counters
    feed_posts_extracted:      int = 0
    search_posts_extracted:    int = 0
    # Search health
    search_tasks_attempted:    int = 0
    search_skipped_feed_ok:    int = 0   # tasks skipped because feeds were sufficient
    search_skipped_cap:        int = 0   # tasks skipped due to max_search_tasks cap
    search_aborted_early:      bool = False
    subreddits_blocked_by_429: list = field(default_factory=list)
    # Subreddit hygiene
    invalid_subreddits_skipped: list = field(default_factory=list)

    @property
    def total_429s(self) -> int:
        return self.non_200_by_code.get(429, 0)

    def log_final_summary(
        self,
        feed_tasks: int,
        search_tasks_built: int,
        total_fetched: int,
        unique: int,
        disqualified: int,
        below_thresh: int,
        final_candidates: int,
    ) -> None:
        non_200_str = (
            ", ".join(f"HTTP {k}×{v}" for k, v in sorted(self.non_200_by_code.items()))
            or "none"
        )
        logger.info(
            "Reddit scraper COMPLETE\n"
            "  feeds  : %d tasks → %d posts extracted\n"
            "  search : %d tasks attempted (of %d built) → %d posts extracted"
            " | skipped feed-ok=%d cap=%d aborted=%s\n"
            "  dedup  : %d total → %d unique\n"
            "  heuristic: %d disqualified, %d below threshold, %d pass\n"
            "  subreddits: %d invalid skipped, %d blocked by 429: %s\n"
            "  rate-limits: 429×%d total | non-200: %s\n"
            "  network: %d attempts, %d success, %d timeouts",
            feed_tasks, self.feed_posts_extracted,
            self.search_tasks_attempted, search_tasks_built,
            self.search_posts_extracted,
            self.search_skipped_feed_ok, self.search_skipped_cap,
            self.search_aborted_early,
            total_fetched, unique,
            disqualified, below_thresh, final_candidates,
            len(self.invalid_subreddits_skipped),
            len(self.subreddits_blocked_by_429),
            self.subreddits_blocked_by_429,
            self.total_429s, non_200_str,
            self.attempts, self.http_success, self.timeouts,
        )


# ---------------------------------------------------------------------------
# HEURISTIC SCORING
# ---------------------------------------------------------------------------

def score_post_relevance(post_data: dict) -> int:
    """
    Lightweight heuristic scorer.  Returns -99 for hard-disqualified posts,
    otherwise a score in roughly [0, 13].
    """
    title     = post_data.get("title", "").lower()
    body      = post_data.get("selftext", "").lower()
    full_text = f"{title} {body}"

    for dq in DISQUALIFY_KEYWORDS:
        if dq in full_text:
            return -99

    if len(full_text.strip()) < 40:
        return 0

    score = 0
    if any(kw in full_text for kw in _PAIN_KW):     score += 2
    if any(kw in full_text for kw in _TIME_KW):     score += 2
    if any(kw in full_text for kw in _WORKFLOW_KW): score += 2
    if any(kw in full_text for kw in _FRUSTRATION): score += 2
    if any(kw in full_text for kw in _SCALE_KW):    score += 2
    if post_data.get("num_comments", 0) > 5:        score += 1
    if post_data.get("score", 0) > 5:               score += 1
    if "?" in title or any(title.startswith(w) for w in _QUESTION_STARTERS):
        score += 1

    return score


def _extract_keywords(post_data: dict) -> list[str]:
    """Return matched pain keywords from the post (for downstream labelling)."""
    title     = post_data.get("title", "").lower()
    body      = post_data.get("selftext", "").lower()
    full_text = f"{title} {body}"
    candidates = _PAIN_KW + _TIME_KW + _WORKFLOW_KW + _FRUSTRATION
    return list(dict.fromkeys(kw for kw in candidates if kw in full_text))


def _build_signal(
    post_data:       dict,
    subreddit:       str,
    comments_text:   str = "",
    heuristic_score: int = 0,
) -> dict:
    """Assemble the final signal dict from raw Reddit data + enriched comments."""
    title          = post_data.get("title", "")
    body           = (post_data.get("selftext") or "").strip()
    body_truncated = body[:1000]

    parts = [f"TITLE: {title}"]
    if body_truncated:
        parts.append(f"POST:\n{body_truncated}")
    if comments_text:
        parts.append(f"TOP COMMENTS:\n{comments_text}")

    return {
        "source":            "reddit",
        "source_url":        f"https://reddit.com{post_data.get('permalink', '')}",
        "author":            post_data.get("author", ""),
        "content":           "\n\n".join(parts),
        "subreddit":         subreddit,
        "keywords_matched":  _extract_keywords(post_data),
        "title":             title,
        "body":              body_truncated,
        "top_comments_text": comments_text,
        "post_score":        post_data.get("score", 0),
        "num_comments":      post_data.get("num_comments", 0),
        "heuristic_score":   heuristic_score,
        "source_created_at": normalize_source_timestamp(post_data.get("created_utc")),
        "scraped_at":        datetime.utcnow().isoformat(),
    }


# ---------------------------------------------------------------------------
# HTTP LAYER
# ---------------------------------------------------------------------------

def _normalize_url(url: str) -> str:
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", "")).lower()
    except Exception:
        return url.lower()


async def _get_json(
    client:      httpx.AsyncClient,
    url:         str,
    params:      dict,
    sem:         asyncio.Semaphore,
    diag:        FetchDiagnostics,
    base_delay:  float,
    jitter:      float,
) -> dict | list | _PermError | None:
    """
    Rate-limited JSON GET.

    Returns:
      dict / list   — success
      _PermError    — permanent failure (403 / 404); caller should denylist the subreddit
      None          — transient failure (429, timeout, parse error, other non-200)
    """
    merged_params = {"raw_json": "1", **params}

    async with sem:
        diag.attempts += 1
        try:
            resp = await client.get(
                url,
                headers=FEED_HEADERS,
                params=merged_params,
                timeout=LIMITS["request_timeout_s"],
            )
            delay = base_delay + random.uniform(0, jitter)
            await asyncio.sleep(delay)

            if resp.status_code == 429:
                backoff = LIMITS["rate_limit_backoff_s"]
                logger.warning("Reddit 429 rate-limit: %s — backing off %ds", url, backoff)
                diag.non_200_by_code[429] += 1
                await asyncio.sleep(backoff)
                return None

            if resp.status_code in (403, 404):
                ct   = resp.headers.get("content-type", "unknown")
                body = resp.text[:200].replace("\n", " ")
                logger.warning(
                    "Reddit permanent error HTTP %d: %s | ct=%s | body=%r",
                    resp.status_code, url, ct, body,
                )
                diag.non_200_by_code[resp.status_code] += 1
                return _PermError(resp.status_code)

            if resp.status_code != 200:
                ct   = resp.headers.get("content-type", "unknown")
                body = resp.text[:200].replace("\n", " ")
                logger.warning(
                    "Reddit non-200 HTTP %d: %s | ct=%s | body=%r",
                    resp.status_code, url, ct, body,
                )
                diag.non_200_by_code[resp.status_code] += 1
                return None

            diag.http_success += 1
            ct = resp.headers.get("content-type", "")

            if "text/html" in ct:
                body = resp.text[:200].replace("\n", " ")
                logger.warning(
                    "Reddit returned HTML on 200 (login redirect?): %s | ct=%s | body=%r",
                    url, ct, body,
                )
                diag.parse_failures += 1
                return None

            try:
                return resp.json()
            except Exception as exc:
                body = resp.text[:200].replace("\n", " ")
                logger.warning(
                    "Reddit JSON parse failure: %s | %s | ct=%s | body=%r",
                    exc, url, ct, body,
                )
                diag.parse_failures += 1
                return None

        except httpx.TimeoutException as exc:
            logger.warning("Reddit timeout: %s — %s", url, exc)
            diag.timeouts += 1
            return None
        except Exception as exc:
            logger.warning("Reddit HTTP error [%s]: %s", url, exc)
            diag.timeouts += 1
            return None


# ---------------------------------------------------------------------------
# PHASE 1 — Feed collection
# ---------------------------------------------------------------------------

async def _fetch_feed(
    client:         httpx.AsyncClient,
    subreddit:      str,
    sort:           str,
    sem:            asyncio.Semaphore,
    diag:           FetchDiagnostics,
    runtime_invalid: set[str],
) -> list[tuple[dict, str]]:
    """Fetch a subreddit feed. Adds subreddit to runtime_invalid on 403/404."""
    url    = _reddit_url(f"/r/{subreddit}/{sort}.json")
    result = await _get_json(
        client, url,
        {"limit": LIMITS["posts_per_feed"], "t": "week"},
        sem, diag,
        base_delay=LIMITS["feed_delay_s"],
        jitter=LIMITS["feed_jitter_s"],
    )

    if isinstance(result, _PermError):
        runtime_invalid.add(subreddit.lower())
        logger.info("Reddit: /r/%s marked invalid (HTTP %d)", subreddit, result.status_code)
        return []

    if not isinstance(result, dict):
        if result is not None:
            diag.schema_mismatches += 1
        return []

    children = result.get("data", {}).get("children", [])
    if not children:
        diag.empty_responses += 1

    out = []
    for child in children:
        d = child.get("data", {})
        if d and d.get("score", 0) >= LIMITS["min_post_score"]:
            out.append((d, subreddit))

    diag.feed_posts_extracted += len(out)
    return out


# ---------------------------------------------------------------------------
# PHASE 1b — Search (fallback)
# ---------------------------------------------------------------------------

async def _fetch_search(
    client:          httpx.AsyncClient,
    subreddit:       str,
    query:           str,
    sem:             asyncio.Semaphore,
    diag:            FetchDiagnostics,
    runtime_invalid: set[str],
) -> list[tuple[dict, str]]:
    """Search a subreddit for a query. Adds subreddit to runtime_invalid on 403/404."""
    url    = _reddit_url(f"/r/{subreddit}/search.json")
    result = await _get_json(
        client, url,
        {
            "q":           query,
            "sort":        "new",
            "limit":       LIMITS["posts_per_search"],
            "t":           "month",
            "restrict_sr": "true",
        },
        sem, diag,
        base_delay=LIMITS["search_delay_s"],
        jitter=LIMITS["search_jitter_s"],
    )

    if isinstance(result, _PermError):
        runtime_invalid.add(subreddit.lower())
        logger.info(
            "Reddit: /r/%s marked invalid during search (HTTP %d)",
            subreddit, result.status_code,
        )
        return []

    if not isinstance(result, dict):
        if result is not None:
            diag.schema_mismatches += 1
        return []

    children = result.get("data", {}).get("children", [])
    if not children:
        diag.empty_responses += 1

    out = []
    for child in children:
        d = child.get("data", {})
        if d and d.get("score", 0) >= LIMITS["min_post_score"]:
            out.append((d, subreddit))

    diag.search_posts_extracted += len(out)
    return out


# ---------------------------------------------------------------------------
# PHASE 3 — Comment enrichment
# ---------------------------------------------------------------------------

async def _fetch_top_comments(
    client:    httpx.AsyncClient,
    permalink: str,
    sem:       asyncio.Semaphore,
    diag:      FetchDiagnostics,
) -> str:
    """Fetch top N comments for a post. Returns joined string or '' on failure."""
    result = await _get_json(
        client,
        _reddit_url(f"{permalink}.json"),
        {"limit": LIMITS["max_comments_per_post"] + 3, "depth": 1, "sort": "top"},
        sem, diag,
        base_delay=LIMITS["feed_delay_s"],
        jitter=LIMITS["feed_jitter_s"],
    )
    if not isinstance(result, list) or len(result) < 2:
        return ""

    texts: list[str] = []
    for child in result[1].get("data", {}).get("children", []):
        body = child.get("data", {}).get("body", "").strip()
        if (
            body
            and body not in ("[deleted]", "[removed]")
            and len(body) >= LIMITS["min_comment_len"]
        ):
            texts.append(body[:350])
        if len(texts) >= LIMITS["max_comments_per_post"]:
            break

    return "\n---\n".join(texts)


# ---------------------------------------------------------------------------
# BATCH DISPATCH HELPER
# ---------------------------------------------------------------------------

async def _gather_batched(tasks: list, batch_size: int) -> list:
    """Run coroutines in chunks. Returns flat list of all results in order."""
    results = []
    for i in range(0, len(tasks), batch_size):
        chunk = tasks[i : i + batch_size]
        results.extend(await asyncio.gather(*chunk, return_exceptions=True))
    return results


# ---------------------------------------------------------------------------
# CONNECTIVITY PROBE
# ---------------------------------------------------------------------------

async def _probe_reddit(
    client: httpx.AsyncClient,
    sem:    asyncio.Semaphore,
    diag:   FetchDiagnostics,
) -> bool:
    """Test connectivity before launching bulk tasks."""
    probe_sub = "smallbusiness"
    probe_url = _reddit_url(f"/r/{probe_sub}/hot.json")
    logger.info("Reddit probe: GET %s?raw_json=1&limit=3", probe_url)

    result = await _get_json(
        client, probe_url, {"limit": 3},
        sem, diag,
        base_delay=LIMITS["feed_delay_s"],
        jitter=0.0,
    )

    if isinstance(result, dict) and "data" in result:
        n = len(result["data"].get("children", []))
        logger.info(
            "Reddit probe SUCCESS — %d posts from /r/%s via %s",
            n, probe_sub, REDDIT_BASE,
        )
        return True

    logger.warning(
        "Reddit probe FAILED — type=%s preview=%r | "
        "base=%s ua=%r | attempts=%d success=%d non200=%s",
        type(result).__name__,
        str(result)[:200] if result else None,
        REDDIT_BASE,
        REDDIT_USER_AGENT[:60],
        diag.attempts,
        diag.http_success,
        dict(diag.non_200_by_code),
    )
    return False


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

async def scrape_reddit(max_subreddits: int | None = None) -> list[dict]:
    """
    High-signal Reddit pain discovery engine.

    Returns signal dicts compatible with pain_signal_analyzer.analyze_batch().
    """
    subreddits = ALL_SUBREDDITS[:]
    if max_subreddits is not None:
        subreddits = subreddits[:max_subreddits]

    feed_sem   = asyncio.Semaphore(LIMITS["feed_semaphore"])
    search_sem = asyncio.Semaphore(LIMITS["search_semaphore"])
    diag           = FetchDiagnostics()
    runtime_invalid: set[str] = set()

    async with httpx.AsyncClient(follow_redirects=True) as client:

        # -------------------------------------------------------------------
        # Phase 0: Connectivity probe
        # -------------------------------------------------------------------
        probe_ok = await _probe_reddit(client, feed_sem, diag)
        if not probe_ok:
            logger.error(
                "Reddit scraper: aborting — probe failed. "
                "Check User-Agent / network / old.reddit.com access."
            )
            return []

        # -------------------------------------------------------------------
        # Phase 1a: Feeds — primary discovery mechanism
        # -------------------------------------------------------------------
        feed_tasks = [
            _fetch_feed(client, sub, sort_mode, feed_sem, diag, runtime_invalid)
            for sub in subreddits
            for sort_mode in LIMITS["feeds_to_pull"]
        ]
        n_feed_tasks = len(feed_tasks)
        logger.info(
            "Reddit scraper: %d subreddits | %d feed tasks | batch=%d",
            len(subreddits), n_feed_tasks, LIMITS["batch_size"],
        )

        feed_batches = await _gather_batched(feed_tasks, LIMITS["batch_size"])
        feed_posts: list[tuple[dict, str]] = []
        for batch in feed_batches:
            if isinstance(batch, Exception):
                logger.warning("Feed task raised: %s", batch)
            elif isinstance(batch, list):
                feed_posts.extend(batch)

        # Record newly discovered bad subreddits
        if runtime_invalid:
            diag.invalid_subreddits_skipped = sorted(runtime_invalid)
            logger.info(
                "Reddit: %d subreddits auto-invalidated during feeds: %s",
                len(runtime_invalid), sorted(runtime_invalid),
            )

        logger.info(
            "Reddit feeds done: %d tasks → %d raw posts",
            n_feed_tasks, len(feed_posts),
        )

        # -------------------------------------------------------------------
        # Phase 1b: Feed-first decision gate
        # Quickly count how many feed posts would pass the heuristic filter.
        # If already enough, skip search entirely.
        # -------------------------------------------------------------------
        feed_pass_count = sum(
            1 for post_data, _ in feed_posts
            if (s := score_post_relevance(post_data)) != -99
            and s >= LIMITS["min_relevance_score"]
        )

        search_posts: list[tuple[dict, str]] = []
        search_tasks_built = 0

        if feed_pass_count >= LIMITS["min_feed_candidates"]:
            skipped_tasks = len(SEARCH_SUBREDDITS) * len(SEARCH_QUERIES[:LIMITS["max_search_queries_per_sub"]])
            diag.search_skipped_feed_ok = skipped_tasks
            logger.info(
                "Reddit: feed candidates %d >= threshold %d — search skipped (%d tasks saved)",
                feed_pass_count, LIMITS["min_feed_candidates"], skipped_tasks,
            )
        else:
            # -------------------------------------------------------------------
            # Phase 1b: Search — fallback / augmentation
            # -------------------------------------------------------------------
            logger.info(
                "Reddit: feed candidates %d < threshold %d — running search fallback",
                feed_pass_count, LIMITS["min_feed_candidates"],
            )

            eligible_search_subs = [
                s for s in SEARCH_SUBREDDITS
                if s in subreddits
                and s.lower() not in runtime_invalid
                and s.lower() not in INVALID_SUBREDDITS
            ][:LIMITS["max_search_subreddits"]]

            queries = SEARCH_QUERIES[:LIMITS["max_search_queries_per_sub"]]
            sub_429_strikes: dict[str, int] = defaultdict(int)
            tasks_issued = 0

            logger.info(
                "Reddit search: %d eligible subs × %d queries (cap=%d tasks)",
                len(eligible_search_subs), len(queries), LIMITS["max_search_tasks"],
            )

            for sub in eligible_search_subs:
                # Global circuit-breaker
                if diag.total_429s >= LIMITS["global_429_limit"]:
                    logger.warning(
                        "Reddit search: global 429 limit (%d) reached — aborting search phase",
                        LIMITS["global_429_limit"],
                    )
                    diag.search_aborted_early = True
                    break

                # Per-subreddit circuit-breaker
                if sub_429_strikes[sub] >= LIMITS["subreddit_429_limit"]:
                    if sub not in diag.subreddits_blocked_by_429:
                        diag.subreddits_blocked_by_429.append(sub)
                    diag.search_skipped_cap += len(queries)
                    logger.info(
                        "Reddit search: /r/%s at 429 limit (%d strikes) — skipping",
                        sub, sub_429_strikes[sub],
                    )
                    continue

                # Hard cap on total search tasks
                remaining_cap = LIMITS["max_search_tasks"] - tasks_issued
                if remaining_cap <= 0:
                    diag.search_skipped_cap += len(queries)
                    continue

                sub_queries = queries[:remaining_cap]
                search_tasks_built += len(sub_queries)

                prev_429s = diag.total_429s
                sub_tasks = [
                    _fetch_search(client, sub, q, search_sem, diag, runtime_invalid)
                    for q in sub_queries
                ]
                results = await asyncio.gather(*sub_tasks, return_exceptions=True)

                # Detect new 429s for this subreddit
                new_429s = diag.total_429s - prev_429s
                if new_429s > 0:
                    sub_429_strikes[sub] += new_429s

                tasks_issued += len(sub_queries)
                diag.search_tasks_attempted += len(sub_queries)

                for r in results:
                    if isinstance(r, list):
                        search_posts.extend(r)
                    elif isinstance(r, Exception):
                        logger.warning("Search task raised: %s", r)

                # Early-exit if candidate pool already large enough
                rough_pool = feed_pass_count + len(search_posts)
                if rough_pool >= LIMITS["target_candidate_pool"]:
                    diag.search_aborted_early = True
                    logger.info(
                        "Reddit search: candidate pool ~%d >= target %d — stopping search early",
                        rough_pool, LIMITS["target_candidate_pool"],
                    )
                    break

            logger.info(
                "Reddit search done: %d tasks → %d raw posts",
                diag.search_tasks_attempted, len(search_posts),
            )

        # -------------------------------------------------------------------
        # Phase 2: Dedup → heuristic score → filter
        # -------------------------------------------------------------------
        all_raw       = feed_posts + search_posts
        seen_urls:    set[str] = set()
        scored_posts: list[tuple[dict, str, int]] = []
        total_fetched = 0
        disqualified  = 0
        below_thresh  = 0

        for post_data, subreddit in all_raw:
            total_fetched += 1
            norm_url = _normalize_url(
                f"https://reddit.com{post_data.get('permalink', '')}"
            )
            if norm_url in seen_urls:
                continue
            seen_urls.add(norm_url)

            rel_score = score_post_relevance(post_data)
            if rel_score == -99:
                disqualified += 1
                continue
            if rel_score < LIMITS["min_relevance_score"]:
                below_thresh += 1
                continue

            scored_posts.append((post_data, subreddit, rel_score))

        diag.log_final_summary(
            feed_tasks=n_feed_tasks,
            search_tasks_built=search_tasks_built,
            total_fetched=total_fetched,
            unique=len(seen_urls),
            disqualified=disqualified,
            below_thresh=below_thresh,
            final_candidates=len(scored_posts),
        )

        if not scored_posts:
            logger.warning(
                "Reddit scraper: 0 posts passed heuristic — "
                "http_success=%d/%d non_200=%s",
                diag.http_success, diag.attempts, dict(diag.non_200_by_code),
            )
            return []

        scored_posts.sort(key=lambda x: x[2], reverse=True)

        # -------------------------------------------------------------------
        # Phase 3: Comment enrichment (capped, best-first)
        # -------------------------------------------------------------------
        to_enrich = scored_posts[: LIMITS["max_comment_fetches"]]
        rest      = scored_posts[LIMITS["max_comment_fetches"] :]

        comment_tasks = [
            _fetch_top_comments(client, post_data.get("permalink", ""), feed_sem, diag)
            for post_data, _, _ in to_enrich
        ]
        comment_results = await _gather_batched(comment_tasks, LIMITS["batch_size"])

        logger.info(
            "Reddit: comments fetched for %d posts (%d skipped — no budget)",
            len(to_enrich), len(rest),
        )

        # -------------------------------------------------------------------
        # Phase 4: Assemble signals
        # -------------------------------------------------------------------
        signals: list[dict] = []

        for (post_data, subreddit, rel_score), comments in zip(to_enrich, comment_results):
            comments_text = comments if isinstance(comments, str) else ""
            signals.append(_build_signal(post_data, subreddit, comments_text, rel_score))

        for post_data, subreddit, rel_score in rest:
            signals.append(_build_signal(post_data, subreddit, "", rel_score))

        logger.info(
            "Reddit: %d signals ready for AI analysis (%d with comments)",
            len(signals), len(to_enrich),
        )

    return signals
