"""
Reddit pain signal scraper — high-signal discovery engine v2.

Architecture:
  Phase 1 — Concurrent fetch: feeds (hot + top/week) + keyword searches
             across all subreddits using a bounded async semaphore.
  Phase 2 — Heuristic scoring: lightweight keyword scorer filters noise
             before anything reaches the (paid) AI analysis stage.
  Phase 3 — Comment enrichment: top comments fetched concurrently for
             the highest-scoring posts, capped to avoid API hammering.
  Phase 4 — Signal assembly: structured dicts compatible with
             pain_signal_analyzer.analyze_batch().

Backward-compatible output keys:
  source, source_url, author, content, subreddit, keywords_matched
  + enriched extras: title, body, top_comments_text, post_score, num_comments
"""
import asyncio
import logging
from datetime import datetime
from urllib.parse import urlparse, urlunparse

import httpx

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
    "posts_per_feed":        30,     # posts fetched per hot/top feed
    "posts_per_search":      25,     # posts fetched per keyword search
    "min_relevance_score":   4,      # heuristic gate (0–13 scale)
    "max_comment_fetches":   120,    # cap total comment-fetch requests per run
    "max_comments_per_post": 4,      # top N comments to append per post
    "min_comment_len":       25,     # ignore stub comments shorter than this
    "request_delay_s":       0.35,   # courtesy sleep after each HTTP call
    "rate_limit_backoff_s":  7,      # extra sleep on HTTP 429
    "min_post_score":        -5,     # discard heavily downvoted posts
    "feeds_to_pull":         ["hot", "top"],  # feed sort modes
    "queries_per_layer":     4,      # queries used from each layer
}

REDDIT_API = "https://www.reddit.com"
HEADERS    = {
    "User-Agent": "Mozilla/5.0 (compatible; pain-signal-research/2.0)",
    "Accept":     "application/json",
}


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


def _build_signal(post_data: dict, subreddit: str, comments_text: str = "") -> dict:
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
        # enriched extras used by outreach writer
        "title":             title,
        "body":              body_truncated,
        "top_comments_text": comments_text,
        "post_score":        post_data.get("score", 0),
        "num_comments":      post_data.get("num_comments", 0),
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
) -> dict | list | None:
    """Rate-limited JSON GET with 429 back-off."""
    async with sem:
        try:
            resp = await client.get(url, headers=HEADERS, params=params, timeout=15)
            await asyncio.sleep(LIMITS["request_delay_s"])
            if resp.status_code == 429:
                logger.debug("429 received — backing off %.0fs", LIMITS["rate_limit_backoff_s"])
                await asyncio.sleep(LIMITS["rate_limit_backoff_s"])
                return None
            if resp.status_code != 200:
                return None
            return resp.json()
        except Exception as exc:
            logger.debug("HTTP error [%s]: %s", url, exc)
            return None


# ---------------------------------------------------------------------------
# PHASE 1 — Raw post collection
# ---------------------------------------------------------------------------

async def _fetch_feed(
    client:    httpx.AsyncClient,
    subreddit: str,
    sort:      str,
    sem:       asyncio.Semaphore,
) -> list[tuple[dict, str]]:
    """
    Fetch a subreddit feed (hot / top).
    Returns list of (raw_post_data, subreddit) tuples.
    Only text posts (is_self=True) are returned — link posts lack body content.
    """
    data = await _get_json(
        client,
        f"{REDDIT_API}/r/{subreddit}/{sort}.json",
        {"limit": LIMITS["posts_per_feed"], "t": "week"},
        sem,
    )
    if not isinstance(data, dict):
        return []

    out = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        if (
            d.get("score", 0) >= LIMITS["min_post_score"]
            and d.get("is_self", False)
        ):
            out.append((d, subreddit))
    return out


async def _fetch_search(
    client:    httpx.AsyncClient,
    subreddit: str,
    query:     str,
    sem:       asyncio.Semaphore,
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
    )
    if not isinstance(data, dict):
        return []

    out = []
    for child in data.get("data", {}).get("children", []):
        d = child.get("data", {})
        if d.get("score", 0) >= LIMITS["min_post_score"]:
            out.append((d, subreddit))
    return out


# ---------------------------------------------------------------------------
# PHASE 3 — Comment enrichment
# ---------------------------------------------------------------------------

async def _fetch_top_comments(
    client:    httpx.AsyncClient,
    permalink: str,
    sem:       asyncio.Semaphore,
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

    sem = asyncio.Semaphore(LIMITS["semaphore"])

    # Build the selected query list (N per layer)
    n_q = LIMITS["queries_per_layer"]
    selected_queries: list[str] = []
    for layer_queries in QUERY_LAYERS.values():
        selected_queries.extend(layer_queries[:n_q])

    async with httpx.AsyncClient() as client:

        # -------------------------------------------------------------------
        # Phase 1: Dispatch all feed + search fetches concurrently
        # -------------------------------------------------------------------
        fetch_tasks = []
        for subreddit in subreddits:
            for sort_mode in LIMITS["feeds_to_pull"]:
                fetch_tasks.append(_fetch_feed(client, subreddit, sort_mode, sem))
            for query in selected_queries:
                fetch_tasks.append(_fetch_search(client, subreddit, query, sem))

        n_feed   = len(subreddits) * len(LIMITS["feeds_to_pull"])
        n_search = len(subreddits) * len(selected_queries)
        logger.info(
            "Reddit scraper: %d subreddits | %d feed + %d search = %d fetch tasks",
            len(subreddits), n_feed, n_search, len(fetch_tasks),
        )

        raw_batches = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        # -------------------------------------------------------------------
        # Phase 2: Flatten → dedup → heuristic score → filter
        # -------------------------------------------------------------------
        seen_urls:    set[str]                    = set()
        scored_posts: list[tuple[dict, str, int]] = []   # (post_data, subreddit, score)
        total_fetched = 0
        disqualified  = 0
        below_thresh  = 0

        for batch in raw_batches:
            if isinstance(batch, Exception):
                logger.debug("Fetch task raised: %s", batch)
                continue
            if not isinstance(batch, list):
                continue
            for post_data, subreddit in batch:
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
            return []

        # Best-first so comment budget goes to highest-signal posts
        scored_posts.sort(key=lambda x: x[2], reverse=True)

        # -------------------------------------------------------------------
        # Phase 3: Comment enrichment (capped)
        # -------------------------------------------------------------------
        to_enrich = scored_posts[: LIMITS["max_comment_fetches"]]
        rest      = scored_posts[LIMITS["max_comment_fetches"] :]

        comment_tasks = [
            _fetch_top_comments(client, post_data.get("permalink", ""), sem)
            for post_data, _, _ in to_enrich
        ]
        comment_results = await asyncio.gather(*comment_tasks, return_exceptions=True)

        logger.info(
            "Reddit scraper: comments fetched for %d posts (%d skipped, no budget)",
            len(to_enrich), len(rest),
        )

        # -------------------------------------------------------------------
        # Phase 4: Assemble final signals
        # -------------------------------------------------------------------
        signals: list[dict] = []

        for (post_data, subreddit, _), comments in zip(to_enrich, comment_results):
            comments_text = comments if isinstance(comments, str) else ""
            signals.append(_build_signal(post_data, subreddit, comments_text))

        for post_data, subreddit, _ in rest:
            signals.append(_build_signal(post_data, subreddit))

        logger.info(
            "Reddit scraper: %d signals ready for AI analysis "
            "(%d with enriched comments)",
            len(signals), len(to_enrich),
        )

    return signals
