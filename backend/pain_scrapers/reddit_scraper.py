"""
Reddit pain signal scraper — high-signal discovery engine v2.

Architecture:
  Phase 0 — Connectivity probe: single test request to verify Reddit is
             reachable and returning valid JSON before launching 500+ tasks.
  Phase 1 — Staged concurrent fetch: feeds first (lower volume), then
             keyword searches in batches of BATCH_SIZE tasks each.
             Bounded by an async semaphore (max concurrent HTTP requests).
  Phase 2 — Heuristic scoring: lightweight keyword scorer filters noise
             before anything reaches the (paid) AI analysis stage.
  Phase 3 — Comment enrichment: top comments fetched concurrently for
             the highest-scoring posts, capped to avoid API hammering.
  Phase 4 — Signal assembly: structured dicts compatible with
             pain_signal_analyzer.analyze_batch().

Diagnostic counters emitted at INFO level at the end of each run:
  attempts, http_success, non_200_by_code, timeouts, parse_failures,
  schema_mismatches, empty_responses, posts_extracted

Backward-compatible output keys:
  source, source_url, author, content, subreddit, keywords_matched
  + enriched extras: title, body, top_comments_text, post_score, num_comments
"""
import asyncio
import logging
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
        "projectmanagement", "operations", "sysadmin",
    ],
    # Industry-specific communities
    "industry_specific": [
        "realestate", "legaladvice", "Dentistry", "MedicalPractice",
        "construction", "logistics", "trucking", "restaurantowners",
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

ALL_SUBREDDITS: list[str] = [
    sub for subs in SUBREDDIT_GROUPS.values() for sub in subs
]

# 3-layer query strategy — covers explicit, natural, and intent-based pain
QUERY_LAYERS: dict[str, list[str]] = {
    # Layer 1: direct pain keywords
    "direct_pain": [
        "manual process",
        "repetitive task",
        "time consuming",
        "tedious",
        "doing this manually",
        "manual data entry",
        "manually tracking",
        "manually sending",
        "tired of manually",
        "sick of manually",
        "doing this by hand",
        "overwhelmed",
        "bottleneck",
    ],
    # Layer 2: implied pain — natural language frustration not caught by keywords
    "implied_pain": [
        "this takes forever",
        "too much time",
        "wasting time on",
        "drowning in",
        "keeping track of",
        "hard to manage",
        "messy process",
        "things fall through the cracks",
        "we keep missing",
        "takes hours every week",
        "spreadsheet is killing",
        "too much admin",
        "can't keep up",
        "losing track of",
        "every single day",
    ],
    # Layer 3: solution-seeking — high purchase intent
    "solution_seeking": [
        "is there a tool for",
        "how do you manage",
        "what do you use for",
        "any software for",
        "looking for a system",
        "recommend software",
        "best tool for",
        "looking for automation",
        "how to automate",
        "automate my workflow",
        "need help with workflow",
    ],
}

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

# Heuristic keyword groups — used only for fast in-process scoring
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

# Tuning knobs — adjust without code changes
LIMITS: dict = {
    "semaphore":             4,      # max concurrent HTTP requests
    "batch_size":            20,     # tasks dispatched per asyncio.gather call
    "posts_per_feed":        30,     # posts fetched per hot/top feed
    "posts_per_search":      25,     # posts fetched per keyword search
    "min_relevance_score":   4,      # heuristic gate (0–13 scale)
    "max_comment_fetches":   120,    # cap total comment-fetch requests per run
    "max_comments_per_post": 4,      # top N comments to append per post
    "min_comment_len":       25,     # ignore stub comments shorter than this
    "request_delay_s":       0.5,    # courtesy sleep after each HTTP call
    "rate_limit_backoff_s":  15,     # extra sleep on HTTP 429
    "min_post_score":        -5,     # discard heavily downvoted posts
    "feeds_to_pull":         ["hot", "top"],  # feed sort modes
    "queries_per_layer":     4,      # queries used from each layer
    "feeds_threshold":       30,     # if feeds yield >= this, skip some searches
}

REDDIT_API = "https://www.reddit.com"

# A realistic browser User-Agent avoids Reddit's bot-detection 403s.
# The public JSON API rejects bots with custom UAs like "pain-signal-research/2.0".
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "application/json, text/javascript, */*",
    "Accept-Language": "en-US,en;q=0.9",
}


# ---------------------------------------------------------------------------
# DIAGNOSTIC COUNTERS
# ---------------------------------------------------------------------------

@dataclass
class FetchDiagnostics:
    """Mutable counters collected across all fetch tasks in a single run."""
    attempts:          int = 0
    http_success:      int = 0
    timeouts:          int = 0
    parse_failures:    int = 0
    schema_mismatches: int = 0
    empty_responses:   int = 0
    posts_extracted:   int = 0
    non_200_by_code:   dict = field(default_factory=lambda: defaultdict(int))

    def log_summary(self, label: str = "") -> None:
        prefix = f"[{label}] " if label else ""
        non_200_str = (
            ", ".join(f"HTTP {k}: {v}" for k, v in sorted(self.non_200_by_code.items()))
            or "none"
        )
        logger.info(
            "%sFetch diagnostics — attempts=%d success=%d posts=%d | "
            "non-200: %s | timeouts=%d parse_failures=%d schema_mismatches=%d empty=%d",
            prefix,
            self.attempts,
            self.http_success,
            self.posts_extracted,
            non_200_str,
            self.timeouts,
            self.parse_failures,
            self.schema_mismatches,
            self.empty_responses,
        )


# ---------------------------------------------------------------------------
# HEURISTIC SCORING
# ---------------------------------------------------------------------------

def score_post_relevance(post_data: dict) -> int:
    """
    Lightweight heuristic scorer.  Returns -99 for hard-disqualified posts,
    otherwise a score in roughly [0, 13].

    Scoring:
      +2  pain keywords
      +2  time-cost language
      +2  workflow / process words
      +2  emotional frustration
      +2  business-scale signals
      +1  num_comments > 5
      +1  post score > 5
      +1  genuine question form
      -99 hard disqualifier matched
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
    """
    Assemble the final signal dict from raw Reddit data + enriched comments.
    'content' is the structured text block forwarded to pain_signal_analyzer.
    """
    title          = post_data.get("title", "")
    body           = (post_data.get("selftext") or "").strip()
    body_truncated = body[:1000]

    parts = [f"TITLE: {title}"]
    if body_truncated:
        parts.append(f"POST:\n{body_truncated}")
    if comments_text:
        parts.append(f"TOP COMMENTS:\n{comments_text}")

    return {
        # backward-compatible core fields
        "source":            "reddit",
        "source_url":        f"https://reddit.com{post_data.get('permalink', '')}",
        "author":            post_data.get("author", ""),
        "content":           "\n\n".join(parts),
        "subreddit":         subreddit,
        "keywords_matched":  _extract_keywords(post_data),
        # enriched extras used by outreach writer and ranker
        "title":             title,
        "body":              body_truncated,
        "top_comments_text": comments_text,
        "post_score":        post_data.get("score", 0),
        "num_comments":      post_data.get("num_comments", 0),
        "heuristic_score":   heuristic_score,
        # timestamp — normalized to datetime | None by signal_ranker
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
    client: httpx.AsyncClient,
    url:    str,
    params: dict,
    sem:    asyncio.Semaphore,
    diag:   FetchDiagnostics,
) -> dict | list | None:
    """
    Rate-limited JSON GET with 429 back-off and structured diagnostics.

    All non-200 responses are logged at WARNING with status code, content-type,
    and the first 300 chars of the response body to aid debugging.
    """
    async with sem:
        diag.attempts += 1
        try:
            resp = await client.get(url, headers=HEADERS, params=params, timeout=20)
            await asyncio.sleep(LIMITS["request_delay_s"])

            if resp.status_code == 429:
                backoff = LIMITS["rate_limit_backoff_s"]
                logger.warning(
                    "Reddit 429 rate-limit on %s — backing off %ds", url, backoff
                )
                diag.non_200_by_code[429] += 1
                await asyncio.sleep(backoff)
                return None

            if resp.status_code != 200:
                ct   = resp.headers.get("content-type", "unknown")
                body = resp.text[:300].replace("\n", " ")
                logger.warning(
                    "Reddit non-200 response: HTTP %d | url=%s | content-type=%s | body=%r",
                    resp.status_code, url, ct, body,
                )
                diag.non_200_by_code[resp.status_code] += 1
                return None

            diag.http_success += 1
            try:
                data = resp.json()
            except Exception as exc:
                ct   = resp.headers.get("content-type", "unknown")
                body = resp.text[:300].replace("\n", " ")
                logger.warning(
                    "Reddit JSON parse failure: %s | url=%s | content-type=%s | body=%r",
                    exc, url, ct, body,
                )
                diag.parse_failures += 1
                return None

            return data

        except httpx.TimeoutException as exc:
            logger.warning("Reddit request timed out: %s — %s", url, exc)
            diag.timeouts += 1
            return None
        except Exception as exc:
            logger.warning("Reddit HTTP error [%s]: %s", url, exc)
            diag.timeouts += 1
            return None


# ---------------------------------------------------------------------------
# PHASE 1 — Raw post collection
# ---------------------------------------------------------------------------

async def _fetch_feed(
    client:    httpx.AsyncClient,
    subreddit: str,
    sort:      str,
    sem:       asyncio.Semaphore,
    diag:      FetchDiagnostics,
) -> list[tuple[dict, str]]:
    """
    Fetch a subreddit feed (hot / top).
    Returns list of (raw_post_data, subreddit) tuples.
    Both text posts (is_self=True) and link posts are included — link post
    titles are sufficient for heuristic scoring.
    """
    data = await _get_json(
        client,
        f"{REDDIT_API}/r/{subreddit}/{sort}.json",
        {"limit": LIMITS["posts_per_feed"], "t": "week"},
        sem,
        diag,
    )
    if not isinstance(data, dict):
        if data is not None:
            diag.schema_mismatches += 1
        return []

    children = data.get("data", {}).get("children", [])
    if not children:
        diag.empty_responses += 1

    out = []
    for child in children:
        d = child.get("data", {})
        if not d:
            continue
        if d.get("score", 0) >= LIMITS["min_post_score"]:
            out.append((d, subreddit))

    diag.posts_extracted += len(out)
    return out


async def _fetch_search(
    client:    httpx.AsyncClient,
    subreddit: str,
    query:     str,
    sem:       asyncio.Semaphore,
    diag:      FetchDiagnostics,
) -> list[tuple[dict, str]]:
    """
    Search a subreddit for a query string.
    Returns list of (raw_post_data, subreddit) tuples.
    """
    data = await _get_json(
        client,
        f"{REDDIT_API}/r/{subreddit}/search.json",
        {
            "q":           query,
            "sort":        "new",
            "limit":       LIMITS["posts_per_search"],
            "t":           "month",
            "restrict_sr": "true",
        },
        sem,
        diag,
    )
    if not isinstance(data, dict):
        if data is not None:
            diag.schema_mismatches += 1
        return []

    children = data.get("data", {}).get("children", [])
    if not children:
        diag.empty_responses += 1

    out = []
    for child in children:
        d = child.get("data", {})
        if not d:
            continue
        if d.get("score", 0) >= LIMITS["min_post_score"]:
            out.append((d, subreddit))

    diag.posts_extracted += len(out)
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
    """
    Fetch top N comments for a post.  Returns a joined string or '' on failure.
    """
    data = await _get_json(
        client,
        f"{REDDIT_API}{permalink}.json",
        {
            "limit": LIMITS["max_comments_per_post"] + 3,
            "depth": 1,
            "sort":  "top",
        },
        sem,
        diag,
    )
    if not isinstance(data, list) or len(data) < 2:
        return ""

    texts: list[str] = []
    for child in data[1].get("data", {}).get("children", []):
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
    """
    Run coroutines in batches to avoid creating thousands of simultaneous
    coroutine objects.  Returns a flat list of all results in order.
    """
    results = []
    for i in range(0, len(tasks), batch_size):
        chunk = tasks[i : i + batch_size]
        batch_results = await asyncio.gather(*chunk, return_exceptions=True)
        results.extend(batch_results)
    return results


# ---------------------------------------------------------------------------
# CONNECTIVITY PROBE
# ---------------------------------------------------------------------------

async def _probe_reddit(
    client: httpx.AsyncClient,
    sem:    asyncio.Semaphore,
    diag:   FetchDiagnostics,
) -> bool:
    """
    Fetch one small feed to verify Reddit is reachable and returning JSON.
    Logs the raw response on failure so the root cause is immediately visible.
    Returns True if the probe succeeded.
    """
    probe_sub = "smallbusiness"
    logger.info("Reddit probe: testing connectivity via /r/%s/hot.json", probe_sub)
    data = await _get_json(
        client,
        f"{REDDIT_API}/r/{probe_sub}/hot.json",
        {"limit": 3},
        sem,
        diag,
    )
    if isinstance(data, dict) and "data" in data:
        n = len(data["data"].get("children", []))
        logger.info("Reddit probe: OK — got %d posts from /r/%s", n, probe_sub)
        return True

    logger.warning(
        "Reddit probe FAILED — data type=%s, value preview=%r. "
        "Check HEADERS / User-Agent / network access.",
        type(data).__name__,
        str(data)[:200] if data else None,
    )
    return False


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

async def scrape_reddit(max_subreddits: int | None = None) -> list[dict]:
    """
    High-signal Reddit pain discovery engine.

    Returns signal dicts compatible with pain_signal_analyzer.analyze_batch().
    Each signal has an enriched 'content' field: title + body + top comments.
    """
    subreddits = ALL_SUBREDDITS[:]
    if max_subreddits is not None:
        subreddits = subreddits[:max_subreddits]

    sem  = asyncio.Semaphore(LIMITS["semaphore"])
    diag = FetchDiagnostics()

    # Build the selected query list (N per layer)
    n_q = LIMITS["queries_per_layer"]
    selected_queries: list[str] = []
    for layer_queries in QUERY_LAYERS.values():
        selected_queries.extend(layer_queries[:n_q])

    async with httpx.AsyncClient(follow_redirects=True) as client:

        # -------------------------------------------------------------------
        # Phase 0: Connectivity probe — abort early if Reddit is unreachable
        # -------------------------------------------------------------------
        probe_ok = await _probe_reddit(client, sem, diag)
        if not probe_ok:
            logger.error(
                "Reddit scraper: aborting — connectivity probe failed. "
                "Diagnose User-Agent / network before proceeding."
            )
            diag.log_summary("probe_failed")
            return []

        # -------------------------------------------------------------------
        # Phase 1a: Feeds (lower volume, run first)
        # -------------------------------------------------------------------
        feed_tasks = [
            _fetch_feed(client, sub, sort_mode, sem, diag)
            for sub in subreddits
            for sort_mode in LIMITS["feeds_to_pull"]
        ]
        n_feed = len(feed_tasks)
        logger.info(
            "Reddit scraper: %d subreddits | dispatching %d feed tasks in batches of %d",
            len(subreddits), n_feed, LIMITS["batch_size"],
        )
        feed_batches = await _gather_batched(feed_tasks, LIMITS["batch_size"])

        # Flatten feed results early so we can decide on searches
        feed_posts: list[tuple[dict, str]] = []
        for batch in feed_batches:
            if isinstance(batch, Exception):
                logger.warning("Feed task raised: %s", batch)
                continue
            if isinstance(batch, list):
                feed_posts.extend(batch)

        logger.info(
            "Reddit scraper: feeds done — %d raw posts from %d tasks",
            len(feed_posts), n_feed,
        )

        # -------------------------------------------------------------------
        # Phase 1b: Searches (higher volume, run after feeds)
        # -------------------------------------------------------------------
        search_tasks = [
            _fetch_search(client, sub, query, sem, diag)
            for sub in subreddits
            for query in selected_queries
        ]
        n_search = len(search_tasks)
        logger.info(
            "Reddit scraper: dispatching %d search tasks in batches of %d",
            n_search, LIMITS["batch_size"],
        )
        search_batches = await _gather_batched(search_tasks, LIMITS["batch_size"])

        search_posts: list[tuple[dict, str]] = []
        for batch in search_batches:
            if isinstance(batch, Exception):
                logger.warning("Search task raised: %s", batch)
                continue
            if isinstance(batch, list):
                search_posts.extend(batch)

        logger.info(
            "Reddit scraper: searches done — %d raw posts from %d tasks",
            len(search_posts), n_search,
        )

        # Emit detailed fetch diagnostics before heuristic stage
        diag.log_summary("fetch")

        # -------------------------------------------------------------------
        # Phase 2: Flatten → dedup → heuristic score → filter
        # -------------------------------------------------------------------
        all_raw = feed_posts + search_posts

        seen_urls:    set[str]                    = set()
        scored_posts: list[tuple[dict, str, int]] = []   # (post_data, subreddit, score)
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

        logger.info(
            "Reddit scraper: %d fetched → %d unique → %d disqualified "
            "→ %d below threshold → %d pass heuristic filter",
            total_fetched, len(seen_urls), disqualified,
            below_thresh, len(scored_posts),
        )

        if not scored_posts:
            logger.warning(
                "Reddit scraper: 0 posts passed heuristic filter. "
                "fetch_success=%d/%d, non_200=%s — check Reddit access.",
                diag.http_success,
                diag.attempts,
                dict(diag.non_200_by_code),
            )
            return []

        # Best-first so comment budget goes to highest-signal posts
        scored_posts.sort(key=lambda x: x[2], reverse=True)

        # -------------------------------------------------------------------
        # Phase 3: Comment enrichment (capped)
        # -------------------------------------------------------------------
        to_enrich = scored_posts[: LIMITS["max_comment_fetches"]]
        rest      = scored_posts[LIMITS["max_comment_fetches"] :]

        comment_tasks = [
            _fetch_top_comments(client, post_data.get("permalink", ""), sem, diag)
            for post_data, _, _ in to_enrich
        ]
        comment_results = await _gather_batched(comment_tasks, LIMITS["batch_size"])

        logger.info(
            "Reddit scraper: comments fetched for %d posts (%d skipped, no budget)",
            len(to_enrich), len(rest),
        )

        # -------------------------------------------------------------------
        # Phase 4: Assemble final signals
        # -------------------------------------------------------------------
        signals: list[dict] = []

        for (post_data, subreddit, rel_score), comments in zip(to_enrich, comment_results):
            comments_text = comments if isinstance(comments, str) else ""
            signals.append(_build_signal(post_data, subreddit, comments_text, rel_score))

        for post_data, subreddit, rel_score in rest:
            signals.append(_build_signal(post_data, subreddit, "", rel_score))

        logger.info(
            "Reddit scraper: %d signals ready for AI analysis "
            "(%d with enriched comments)",
            len(signals), len(to_enrich),
        )

    return signals
