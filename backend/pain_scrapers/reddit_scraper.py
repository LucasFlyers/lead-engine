"""
Reddit pain-signal scraper — low-volume anonymous mode (default).

Design philosophy:
  - Small, curated subreddit list (8 subs).
  - Feed endpoints only by default (new + optionally hot).
  - Search disabled unless explicitly enabled via REDDIT_ENABLE_SEARCH=true.
  - Concurrency=2 so requests are gentle and non-bursty.
  - Per-request jitter (0.8–1.5s) to avoid detectable patterns.
  - Early-stop: halts once enough raw posts OR heuristic candidates are found.
  - Fails cleanly on probe failure — rest of pipeline continues unaffected.

To re-enable search later:  set REDDIT_ENABLE_SEARCH=true in Railway env vars.
To expand subreddits:       raise REDDIT_MAX_SUBREDDITS or edit ACTIVE_SUBREDDITS.
To increase volume:         raise REDDIT_MAX_RAW_POSTS_PER_RUN / reduce jitter.

Backward-compatible output keys (unchanged):
  source, source_url, author, content, subreddit, keywords_matched,
  title, body, top_comments_text, post_score, num_comments,
  heuristic_score, source_created_at, scraped_at
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
# CURATED SUBREDDIT LIST
# Keep this small and high-yield.  Add/remove here to tune coverage.
# ---------------------------------------------------------------------------
ACTIVE_SUBREDDITS: list[str] = [
    "smallbusiness",   # highest density of real operator pain
    "Entrepreneur",    # founders describing workflow problems
    "startups",        # early-stage ops friction
    "SaaS",            # tool-seeking + process pain
    "Bookkeeping",     # manual admin, spreadsheet pain
    "sales",           # CRM / follow-up chaos
    "marketing",       # campaign/process overhead
    "recruiting",      # hiring workflow frustration
]

# Subreddits confirmed banned, private, or consistently unavailable.
# Skipped before any HTTP request is made.
INVALID_SUBREDDITS: set[str] = {
    "operations",   # banned
    "trucking",     # private
}

# ---------------------------------------------------------------------------
# SEARCH QUERIES (inactive by default — only used when REDDIT_ENABLE_SEARCH=true)
# ---------------------------------------------------------------------------
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

# ---------------------------------------------------------------------------
# DISQUALIFY / HEURISTIC KEYWORDS (unchanged from previous version)
# ---------------------------------------------------------------------------
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
# ENV-CONFIGURABLE KNOBS — override in Railway without touching code
# ---------------------------------------------------------------------------
_ENABLE_SEARCH   = os.getenv("REDDIT_ENABLE_SEARCH",   "false").lower() in ("1", "true", "yes")
_CONCURRENCY     = int(os.getenv("REDDIT_FEED_CONCURRENCY",             "2"))
_MAX_SUBS        = int(os.getenv("REDDIT_MAX_SUBREDDITS",               "8"))
_MAX_RAW         = int(os.getenv("REDDIT_MAX_RAW_POSTS_PER_RUN",        "120"))
_TARGET_CANDS    = int(os.getenv("REDDIT_TARGET_HEURISTIC_CANDIDATES",  "25"))
_USE_HOT         = os.getenv("REDDIT_USE_HOT_FEED", "true").lower() in ("1", "true", "yes")
_JITTER_MIN      = float(os.getenv("REDDIT_JITTER_MIN",                 "0.8"))
_JITTER_MAX      = float(os.getenv("REDDIT_JITTER_MAX",                 "1.5"))
_TIMEOUT         = float(os.getenv("REDDIT_REQUEST_TIMEOUT_SECONDS",    "12"))
_POSTS_PER_FEED  = int(os.getenv("REDDIT_POSTS_PER_FEED",               "25"))
_MAX_COMMENTS    = int(os.getenv("REDDIT_MAX_COMMENT_FETCHES",          "20"))

LIMITS: dict = {
    "concurrency":           _CONCURRENCY,
    "max_subreddits":        _MAX_SUBS,
    "max_raw_posts":         _MAX_RAW,
    "target_heuristic":      _TARGET_CANDS,
    "feeds_to_pull":         ["new", "hot"] if _USE_HOT else ["new"],
    "posts_per_feed":        _POSTS_PER_FEED,
    "min_relevance_score":   4,       # heuristic 0–13 gate
    "min_post_score":        -5,
    "max_comment_fetches":   _MAX_COMMENTS,
    "max_comments_per_post": 3,
    "min_comment_len":       25,
    "request_timeout_s":     _TIMEOUT,
    "delay_min_s":           _JITTER_MIN,
    "delay_max_s":           _JITTER_MAX,
    "rate_limit_backoff_s":  30,
    # Search (inactive unless REDDIT_ENABLE_SEARCH=true)
    "enable_search":         _ENABLE_SEARCH,
    "max_search_queries":    int(os.getenv("REDDIT_MAX_SEARCH_QUERIES", "3")),
}

# old.reddit.com bypasses www.reddit.com bot-detection layer.
REDDIT_BASE = "https://old.reddit.com"

REDDIT_USER_AGENT = os.getenv(
    "REDDIT_USER_AGENT",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
)

HEADERS = {
    "User-Agent":      REDDIT_USER_AGENT,
    "Accept":          "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.5",
    "DNT":             "1",
}


def _reddit_url(path: str) -> str:
    """Build a full old.reddit.com URL from a /r/... path."""
    return f"{REDDIT_BASE}{path}"


def _jitter_sleep() -> float:
    """Return a random delay in [delay_min_s, delay_max_s]."""
    return random.uniform(LIMITS["delay_min_s"], LIMITS["delay_max_s"])


# ---------------------------------------------------------------------------
# PERMANENT-ERROR SENTINEL
# ---------------------------------------------------------------------------

class _PermError:
    """Returned by _get_json for 403/404 so callers can denylist the subreddit."""
    __slots__ = ("status_code",)

    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


# ---------------------------------------------------------------------------
# DIAGNOSTICS
# ---------------------------------------------------------------------------

@dataclass
class RunDiagnostics:
    """Lightweight run-level counters for low-volume mode."""
    attempts:           int = 0
    http_success:       int = 0
    timeouts:           int = 0
    parse_failures:     int = 0
    non_200_by_code:    dict = field(default_factory=lambda: defaultdict(int))
    feed_posts:         int = 0
    search_posts:       int = 0
    invalid_skipped:    list = field(default_factory=list)
    early_stop:         bool = False

    @property
    def total_429s(self) -> int:
        return self.non_200_by_code.get(429, 0)

    def log_summary(
        self,
        subreddit_count:  int,
        feed_tasks:       int,
        unique:           int,
        disqualified:     int,
        below_thresh:     int,
        final_candidates: int,
    ) -> None:
        non_200 = (
            ", ".join(f"HTTP {k}×{v}" for k, v in sorted(self.non_200_by_code.items()))
            or "none"
        )
        logger.info(
            "Reddit scraper done | "
            "subreddits=%d feed_tasks=%d early_stop=%s | "
            "feed_posts=%d search_posts=%d unique=%d | "
            "disqualified=%d below_thresh=%d heuristic_passed=%d | "
            "invalid_skipped=%s 429s=%d non200=%s | "
            "http=%d/%d timeouts=%d",
            subreddit_count, feed_tasks, self.early_stop,
            self.feed_posts, self.search_posts, unique,
            disqualified, below_thresh, final_candidates,
            self.invalid_skipped or "none",
            self.total_429s, non_200,
            self.http_success, self.attempts, self.timeouts,
        )


# ---------------------------------------------------------------------------
# HEURISTIC SCORING
# ---------------------------------------------------------------------------

def score_post_relevance(post_data: dict) -> int:
    """
    Returns -99 for disqualified posts, otherwise roughly [0, 13].

      +2  pain keywords      +2  time-cost language
      +2  workflow/process   +2  frustration signals
      +2  business scale     +1  comment engagement
      +1  upvote signal      +1  question form
      -99 hard disqualifier
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
    client:  httpx.AsyncClient,
    url:     str,
    params:  dict,
    sem:     asyncio.Semaphore,
    diag:    RunDiagnostics,
) -> dict | list | _PermError | None:
    """
    Single rate-limited JSON GET.

    Returns:
      dict/list   — success
      _PermError  — permanent 403/404 (caller should denylist subreddit)
      None        — transient failure (429, timeout, parse error)
    """
    merged = {"raw_json": "1", **params}

    async with sem:
        diag.attempts += 1
        try:
            resp = await client.get(
                url,
                headers=HEADERS,
                params=merged,
                timeout=LIMITS["request_timeout_s"],
            )
            # Jittered delay INSIDE semaphore so concurrent tasks don't collapse
            await asyncio.sleep(_jitter_sleep())

            if resp.status_code == 429:
                backoff = LIMITS["rate_limit_backoff_s"]
                logger.warning("Reddit 429 on %s — backing off %ds", url, backoff)
                diag.non_200_by_code[429] += 1
                await asyncio.sleep(backoff)
                return None

            if resp.status_code in (403, 404):
                ct   = resp.headers.get("content-type", "?")
                body = resp.text[:200].replace("\n", " ")
                logger.warning(
                    "Reddit permanent HTTP %d: %s | ct=%s | body=%r",
                    resp.status_code, url, ct, body,
                )
                diag.non_200_by_code[resp.status_code] += 1
                return _PermError(resp.status_code)

            if resp.status_code != 200:
                ct   = resp.headers.get("content-type", "?")
                body = resp.text[:200].replace("\n", " ")
                logger.warning(
                    "Reddit HTTP %d: %s | ct=%s | body=%r",
                    resp.status_code, url, ct, body,
                )
                diag.non_200_by_code[resp.status_code] += 1
                return None

            diag.http_success += 1
            ct = resp.headers.get("content-type", "")

            if "text/html" in ct:
                body = resp.text[:200].replace("\n", " ")
                logger.warning(
                    "Reddit returned HTML on 200 (login redirect?): %s | body=%r", url, body,
                )
                diag.parse_failures += 1
                return None

            try:
                return resp.json()
            except Exception as exc:
                logger.warning("Reddit JSON parse failure: %s | %s", exc, url)
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
# FEED FETCH
# ---------------------------------------------------------------------------

async def _fetch_feed(
    client:          httpx.AsyncClient,
    subreddit:       str,
    sort:            str,
    sem:             asyncio.Semaphore,
    diag:            RunDiagnostics,
    runtime_invalid: set[str],
) -> list[tuple[dict, str]]:
    """Fetch one subreddit feed. Adds subreddit to runtime_invalid on 403/404."""
    url    = _reddit_url(f"/r/{subreddit}/{sort}.json")
    result = await _get_json(
        client, url,
        {"limit": LIMITS["posts_per_feed"], "t": "week"},
        sem, diag,
    )

    if isinstance(result, _PermError):
        runtime_invalid.add(subreddit.lower())
        logger.info("Reddit: /r/%s invalid (HTTP %d) — skipping rest of run",
                    subreddit, result.status_code)
        return []

    if not isinstance(result, dict):
        return []

    children = result.get("data", {}).get("children", [])
    out = []
    for child in children:
        d = child.get("data", {})
        if d and d.get("score", 0) >= LIMITS["min_post_score"]:
            out.append((d, subreddit))

    diag.feed_posts += len(out)
    return out


# ---------------------------------------------------------------------------
# SEARCH FETCH (inactive by default)
# ---------------------------------------------------------------------------

async def _fetch_search(
    client:          httpx.AsyncClient,
    subreddit:       str,
    query:           str,
    sem:             asyncio.Semaphore,
    diag:            RunDiagnostics,
    runtime_invalid: set[str],
) -> list[tuple[dict, str]]:
    """Search a subreddit — only called when REDDIT_ENABLE_SEARCH=true."""
    url    = _reddit_url(f"/r/{subreddit}/search.json")
    result = await _get_json(
        client, url,
        {
            "q": query, "sort": "new", "limit": 15,
            "t": "month", "restrict_sr": "true",
        },
        sem, diag,
    )

    if isinstance(result, _PermError):
        runtime_invalid.add(subreddit.lower())
        return []

    if not isinstance(result, dict):
        return []

    out = []
    for child in result.get("data", {}).get("children", []):
        d = child.get("data", {})
        if d and d.get("score", 0) >= LIMITS["min_post_score"]:
            out.append((d, subreddit))

    diag.search_posts += len(out)
    return out


# ---------------------------------------------------------------------------
# COMMENT ENRICHMENT
# ---------------------------------------------------------------------------

async def _fetch_top_comments(
    client:    httpx.AsyncClient,
    permalink: str,
    sem:       asyncio.Semaphore,
    diag:      RunDiagnostics,
) -> str:
    result = await _get_json(
        client,
        _reddit_url(f"{permalink}.json"),
        {"limit": LIMITS["max_comments_per_post"] + 2, "depth": 1, "sort": "top"},
        sem, diag,
    )
    if not isinstance(result, list) or len(result) < 2:
        return ""

    texts: list[str] = []
    for child in result[1].get("data", {}).get("children", []):
        body = child.get("data", {}).get("body", "").strip()
        if body and body not in ("[deleted]", "[removed]") and len(body) >= LIMITS["min_comment_len"]:
            texts.append(body[:300])
        if len(texts) >= LIMITS["max_comments_per_post"]:
            break

    return "\n---\n".join(texts)


# ---------------------------------------------------------------------------
# CONNECTIVITY PROBE
# ---------------------------------------------------------------------------

async def _probe(
    client: httpx.AsyncClient,
    sem:    asyncio.Semaphore,
    diag:   RunDiagnostics,
) -> bool:
    url = _reddit_url("/r/smallbusiness/new.json")
    logger.info("Reddit probe: GET %s?raw_json=1&limit=2", url)

    result = await _get_json(client, url, {"limit": 2}, sem, diag)

    if isinstance(result, dict) and "data" in result:
        n = len(result["data"].get("children", []))
        logger.info("Reddit probe OK — %d posts returned", n)
        return True

    logger.warning(
        "Reddit probe FAILED — type=%s | attempts=%d success=%d non200=%s | "
        "ua=%r",
        type(result).__name__,
        diag.attempts, diag.http_success,
        dict(diag.non_200_by_code),
        REDDIT_USER_AGENT[:60],
    )
    return False


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

async def scrape_reddit(max_subreddits: int | None = None) -> list[dict]:
    """
    Low-volume Reddit pain-signal scraper.

    Fetches feed posts from a small curated subreddit list, stops as soon as
    enough candidates are collected, and optionally augments with search.

    Returns signal dicts compatible with signal_ranker + pain_signal_analyzer.
    """
    # Build active subreddit list
    limit = max_subreddits if max_subreddits is not None else LIMITS["max_subreddits"]
    subreddits = [
        s for s in ACTIVE_SUBREDDITS
        if s.lower() not in INVALID_SUBREDDITS
    ][:limit]

    sem             = asyncio.Semaphore(LIMITS["concurrency"])
    diag            = RunDiagnostics()
    runtime_invalid: set[str] = set()

    logger.info(
        "Reddit scraper (low-volume): %d subs | feeds=%s | search=%s | "
        "concurrency=%d | jitter=%.1f–%.1fs | stop_at raw=%d heuristic=%d",
        len(subreddits),
        LIMITS["feeds_to_pull"],
        "enabled" if LIMITS["enable_search"] else "disabled",
        LIMITS["concurrency"],
        LIMITS["delay_min_s"], LIMITS["delay_max_s"],
        LIMITS["max_raw_posts"], LIMITS["target_heuristic"],
    )

    async with httpx.AsyncClient(follow_redirects=True) as client:

        # ------------------------------------------------------------------
        # Phase 0: Probe
        # ------------------------------------------------------------------
        if not await _probe(client, sem, diag):
            logger.error(
                "Reddit scraper: probe failed — skipping Reddit for this run. "
                "Pipeline continues with other sources."
            )
            return []

        # ------------------------------------------------------------------
        # Phase 1: Feeds — one subreddit at a time with early-stop check
        # ------------------------------------------------------------------
        all_raw: list[tuple[dict, str]] = []
        feed_tasks_count = 0

        for sub in subreddits:
            if sub.lower() in runtime_invalid:
                diag.invalid_skipped.append(sub)
                continue

            # Fetch all feed modes for this subreddit in parallel (just 1–2 feeds)
            sub_tasks = [
                _fetch_feed(client, sub, sort, sem, diag, runtime_invalid)
                for sort in LIMITS["feeds_to_pull"]
            ]
            feed_tasks_count += len(sub_tasks)
            results = await asyncio.gather(*sub_tasks, return_exceptions=True)

            for r in results:
                if isinstance(r, list):
                    all_raw.extend(r)
                elif isinstance(r, Exception):
                    logger.warning("Feed task raised: %s", r)

            # Early stop: raw post volume cap
            if len(all_raw) >= LIMITS["max_raw_posts"]:
                logger.info(
                    "Reddit: raw post cap %d reached after /r/%s — stopping feeds early",
                    LIMITS["max_raw_posts"], sub,
                )
                diag.early_stop = True
                break

            # Early stop: quick heuristic count (avoids full dedup/scoring loop)
            quick_pass = sum(
                1 for pd, _ in all_raw
                if (s := score_post_relevance(pd)) != -99
                and s >= LIMITS["min_relevance_score"]
            )
            if quick_pass >= LIMITS["target_heuristic"]:
                logger.info(
                    "Reddit: heuristic target %d reached (%d candidates) after /r/%s — stopping feeds early",
                    LIMITS["target_heuristic"], quick_pass, sub,
                )
                diag.early_stop = True
                break

        logger.info(
            "Reddit feeds: %d tasks across %d subs → %d raw posts",
            feed_tasks_count, len(subreddits), len(all_raw),
        )

        # ------------------------------------------------------------------
        # Phase 1b: Search (only if enabled AND not early-stopped)
        # ------------------------------------------------------------------
        if LIMITS["enable_search"] and not diag.early_stop:
            queries   = SEARCH_QUERIES[:LIMITS["max_search_queries"]]
            search_subs = [
                s for s in subreddits
                if s.lower() not in runtime_invalid
            ]
            logger.info(
                "Reddit search: enabled — %d queries × %d subs",
                len(queries), len(search_subs),
            )
            for sub in search_subs:
                prev_429s = diag.total_429s
                for q in queries:
                    r = await _fetch_search(client, sub, q, sem, diag, runtime_invalid)
                    all_raw.extend(r)
                new_429s = diag.total_429s - prev_429s
                if new_429s >= 2:
                    logger.warning("Reddit: /r/%s getting 429s — stopping search", sub)
                    break
                if diag.total_429s >= 8:
                    logger.warning("Reddit: global 429 limit — aborting search phase")
                    break
        elif LIMITS["enable_search"] and diag.early_stop:
            logger.info("Reddit: search skipped — early stop already triggered")

        # ------------------------------------------------------------------
        # Phase 2: Dedup → heuristic score → filter
        # ------------------------------------------------------------------
        seen_urls:    set[str]                    = set()
        scored_posts: list[tuple[dict, str, int]] = []
        total_raw    = 0
        disqualified = 0
        below_thresh = 0

        for post_data, subreddit in all_raw:
            total_raw += 1
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

        diag.log_summary(
            subreddit_count=len(subreddits),
            feed_tasks=feed_tasks_count,
            unique=len(seen_urls),
            disqualified=disqualified,
            below_thresh=below_thresh,
            final_candidates=len(scored_posts),
        )

        if not scored_posts:
            logger.warning(
                "Reddit: 0 posts passed heuristic — http=%d/%d non200=%s",
                diag.http_success, diag.attempts, dict(diag.non_200_by_code),
            )
            return []

        scored_posts.sort(key=lambda x: x[2], reverse=True)

        # ------------------------------------------------------------------
        # Phase 3: Comment enrichment (best-first, capped)
        # ------------------------------------------------------------------
        to_enrich = scored_posts[:LIMITS["max_comment_fetches"]]
        rest      = scored_posts[LIMITS["max_comment_fetches"]:]

        comment_tasks = [
            _fetch_top_comments(client, pd.get("permalink", ""), sem, diag)
            for pd, _, _ in to_enrich
        ]
        comment_results = await asyncio.gather(*comment_tasks, return_exceptions=True)

        # ------------------------------------------------------------------
        # Phase 4: Assemble signals
        # ------------------------------------------------------------------
        signals: list[dict] = []

        for (pd, sub, score), comments in zip(to_enrich, comment_results):
            ct = comments if isinstance(comments, str) else ""
            signals.append(_build_signal(pd, sub, ct, score))

        for pd, sub, score in rest:
            signals.append(_build_signal(pd, sub, "", score))

        logger.info(
            "Reddit: %d signals ready for analysis (%d with comments, %d without)",
            len(signals), len(to_enrich), len(rest),
        )

    return signals
