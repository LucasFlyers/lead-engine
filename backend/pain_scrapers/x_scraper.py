"""
X (Twitter) pain-signal scraper — official API v2 adapter.

Uses the X API v2 recent-search endpoint (Bearer token auth).
No anonymous scraping.  Fails gracefully when credentials are absent.

Flow:
  1. Check X_ENABLED and X_API_BEARER_TOKEN — skip cleanly if absent.
  2. Run a compact set of high-signal business-pain search queries.
  3. Prefilter obviously noisy tweets (promos, jobs, RT-junk) before AI.
  4. Normalize to the same candidate shape used by the rest of the pipeline.
  5. Return signal dicts compatible with signal_ranker + pain_signal_analyzer.

Rate-limit notes:
  • X Basic tier: 10 requests / 15 min per app for recent search.
  • X_MAX_QUERIES_PER_RUN defaults to 8 — well within basic limits.
  • Serial execution (no concurrency) with X_QUERY_DELAY_SECONDS between requests.
  • Set X_MAX_RESULTS_PER_QUERY=10 for free/Essential tier,
    up to 100 for Basic+ tier.

Required env var:
  X_API_BEARER_TOKEN   — your app Bearer token from developer.x.com

Optional env vars:
  X_ENABLED                  (default: false)
  X_MAX_RESULTS_PER_QUERY    (default: 10)
  X_MAX_QUERIES_PER_RUN      (default: 8)
  X_LOOKBACK_DAYS            (default: 3)
  X_REQUEST_TIMEOUT_SECONDS  (default: 15)
  X_QUERY_DELAY_SECONDS      (default: 2.0)
  X_MIN_HEURISTIC_SCORE      (default: 3)
"""
import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from urllib.parse import urlparse, urlunparse

import httpx

from pain_scrapers.signal_ranker import normalize_source_timestamp

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------------------------

X_ENABLED   = os.getenv("X_ENABLED", "false").lower() in ("1", "true", "yes")
BEARER_TOKEN = os.getenv("X_API_BEARER_TOKEN", "").strip()

X_API_BASE             = os.getenv("X_API_BASE_URL", "https://api.twitter.com/2")
MAX_RESULTS_PER_QUERY  = int(os.getenv("X_MAX_RESULTS_PER_QUERY",   "10"))   # 10=free, up to 100=Basic+
MAX_QUERIES_PER_RUN    = int(os.getenv("X_MAX_QUERIES_PER_RUN",     "8"))
LOOKBACK_DAYS          = int(os.getenv("X_LOOKBACK_DAYS",           "3"))
REQUEST_TIMEOUT        = float(os.getenv("X_REQUEST_TIMEOUT_SECONDS","15"))
QUERY_DELAY            = float(os.getenv("X_QUERY_DELAY_SECONDS",   "2.0"))   # courtesy sleep between queries
MIN_HEURISTIC_SCORE    = int(os.getenv("X_MIN_HEURISTIC_SCORE",     "3"))


# ---------------------------------------------------------------------------
# SEARCH QUERY GROUPS
# High-signal, narrow queries — no spray-and-pray.
# Each query includes -is:retweet and lang:en automatically (appended below).
# ---------------------------------------------------------------------------

QUERY_GROUPS: dict[str, list[str]] = {
    # Layer 1: explicit workflow / admin pain
    "direct_pain": [
        '"manual process" (founder OR business OR team OR clients)',
        '"manual data entry" (team OR business OR clients OR workflow)',
        '"too much admin" (business OR clients OR team)',
        '"spreadsheet" (nightmare OR killing OR overwhelming) business',
        '"repetitive tasks" (business OR operations OR team)',
        '"manual workflow" team OR clients OR leads',
    ],
    # Layer 2: implied operational pain — natural-language frustration
    "implied_pain": [
        '"things fall through the cracks" (clients OR leads OR team)',
        '"need a better system" (clients OR leads OR business OR workflow)',
        '"our process is broken" OR "process is a mess"',
        '"hard to manage" (clients OR leads OR team OR sales)',
        '"keeping track of" (clients OR leads OR invoices OR tasks)',
        '"wasting time" (admin OR manual OR spreadsheet OR workflow)',
    ],
    # Layer 3: solution-seeking / tool-seeking — highest buyer intent
    "solution_seeking": [
        '"what tool do you use" (clients OR leads OR workflow OR CRM)',
        '"looking for a system" (clients OR business OR sales OR onboarding)',
        '"how do you manage" (clients OR leads OR invoices OR scheduling)',
        '"any software for" (small business OR team OR clients)',
        '"recommendation for managing" (clients OR leads OR operations)',
    ],
}

# All queries flattened — used to select the first MAX_QUERIES_PER_RUN
ALL_QUERIES: list[str] = [
    q for qs in QUERY_GROUPS.values() for q in qs
]

# Appended to every query to reduce noise
_QUERY_SUFFIX = "-is:retweet lang:en"


# ---------------------------------------------------------------------------
# PREFILTER — fast rejection before AI
# ---------------------------------------------------------------------------

# Case-insensitive fragment matches that immediately discard a tweet
_DISQUALIFY_FRAGMENTS: list[str] = [
    # Self-promotion / sales flex
    "i built", "i made", "i created", "i automated",
    "my tool", "my app", "my saas", "my service",
    "link in bio", "check out my", "dm me for",
    "we help businesses", "we help clients", "i help clients",
    "book a call", "schedule a demo", "free consultation",
    "case study", "success story", "results we got",
    # Job postings
    "we're hiring", "we are hiring", "join our team", "now hiring",
    "job opening", "apply now", "career opportunity",
    # Giveaways / spam
    "giveaway", "follow and retweet", "win a",
    # Generic motivational noise
    "hustle every day", "grind never stops",
    # News / media
    "breaking:", "just in:", "thread:", "🧵",
]

# Positive signals that boost tweet relevance
_POSITIVE_FRAGMENTS: list[str] = [
    "manual", "spreadsheet", "follow-up", "follow up", "workflow",
    "process", "admin", "automate", "automation",
    "wasting time", "takes forever", "too slow",
    "cracks", "missing", "messy", "chaotic", "broken",
    "tool", "software", "system", "crm", "manage",
    "clients", "leads", "invoices", "scheduling", "onboarding",
    "team", "operations", "sales", "small business", "founder",
]

# Pain keyword groups for heuristic scoring (mirrors reddit_scraper style)
_PAIN_KW     = ["manual", "repetitive", "tedious", "automate", "automation",
                "spreadsheet", "data entry", "bottleneck", "overwhelmed"]
_TIME_KW     = ["takes forever", "wasting time", "hours every", "all day",
                "too long", "weeks to", "slow process", "days to"]
_WORKFLOW_KW = ["workflow", "process", "system", "pipeline", "crm",
                "follow-up", "onboarding", "invoices", "scheduling"]
_FRUSTRATION = ["nightmare", "mess", "broken", "chaos", "chaotic",
                "frustrating", "hate", "annoying", "killing me"]
_INTENT_KW   = ["tool", "software", "app", "system", "platform",
                "recommendation", "suggestion", "what do you use"]


# ---------------------------------------------------------------------------
# DIAGNOSTICS
# ---------------------------------------------------------------------------

@dataclass
class XDiagnostics:
    """Run-level counters for X scraping."""
    queries_attempted:   int = 0
    queries_success:     int = 0
    auth_failures:       int = 0
    rate_limit_hits:     int = 0
    other_errors:        int = 0
    tweets_returned:     int = 0
    tweets_normalized:   int = 0
    tweets_prefiltered:  int = 0   # rejected by prefilter
    tweets_below_thresh: int = 0   # rejected by heuristic gate
    tweets_passed:       int = 0   # forwarded to pipeline

    def log_summary(self) -> None:
        logger.info(
            "X scraper summary — queries: %d attempted / %d success | "
            "auth_failures=%d rate_limits=%d errors=%d | "
            "tweets: %d returned → %d normalized → %d prefiltered → "
            "%d below heuristic → %d passed to pipeline",
            self.queries_attempted, self.queries_success,
            self.auth_failures, self.rate_limit_hits, self.other_errors,
            self.tweets_returned,
            self.tweets_normalized,
            self.tweets_prefiltered,
            self.tweets_below_thresh,
            self.tweets_passed,
        )


# ---------------------------------------------------------------------------
# HEURISTIC SCORING
# ---------------------------------------------------------------------------

def _is_disqualified(text: str) -> bool:
    """Return True if tweet matches any hard-reject pattern."""
    lower = text.lower()
    return any(frag in lower for frag in _DISQUALIFY_FRAGMENTS)


def score_tweet_relevance(text: str) -> int:
    """
    Lightweight heuristic scorer for X posts.  Returns -99 for disqualified
    tweets, otherwise a score roughly in [0, 11].

      +2  pain keywords
      +2  time-cost language
      +2  workflow / process words
      +2  frustration language
      +2  solution-seeking intent
      +1  question mark present
      -99 hard disqualifier matched
    """
    if _is_disqualified(text):
        return -99

    lower = text.lower()
    if len(lower.strip()) < 20:
        return 0

    score = 0
    if any(kw in lower for kw in _PAIN_KW):     score += 2
    if any(kw in lower for kw in _TIME_KW):     score += 2
    if any(kw in lower for kw in _WORKFLOW_KW): score += 2
    if any(kw in lower for kw in _FRUSTRATION): score += 2
    if any(kw in lower for kw in _INTENT_KW):   score += 2
    if "?" in text:                              score += 1
    return score


def _extract_keywords(text: str) -> list[str]:
    """Return matched pain keywords present in tweet text."""
    lower = text.lower()
    candidates = _PAIN_KW + _TIME_KW + _WORKFLOW_KW + _FRUSTRATION + _INTENT_KW
    return list(dict.fromkeys(kw for kw in candidates if kw in lower))


# ---------------------------------------------------------------------------
# NORMALIZATION
# ---------------------------------------------------------------------------

def _build_engagement_score(metrics: dict) -> int:
    """
    Derive a single integer 'post_score' from X public metrics.
    Likes weighted most, then reposts, then replies.
    Capped at 100 to prevent viral outliers from dominating rankings.
    """
    likes     = metrics.get("like_count",     0) or 0
    reposts   = metrics.get("retweet_count",  0) or 0
    replies   = metrics.get("reply_count",    0) or 0
    quotes    = metrics.get("quote_count",    0) or 0
    raw = likes + (reposts * 2) + replies + quotes
    return min(raw, 100)


def _normalize_tweet(
    tweet:        dict,
    users_by_id:  dict[str, dict],
    query_used:   str,
) -> dict | None:
    """
    Convert an X API v2 tweet object into the pipeline candidate shape.
    Returns None if the tweet should be skipped.
    """
    tweet_id   = tweet.get("id", "")
    text       = (tweet.get("text") or "").strip()
    created_at = tweet.get("created_at")           # RFC3339 string
    author_id  = tweet.get("author_id", "")
    metrics    = tweet.get("public_metrics") or {}

    if not tweet_id or not text:
        return None

    # Resolve username from expanded user data
    user        = users_by_id.get(author_id, {})
    username    = user.get("username", "")
    source_url  = f"https://x.com/i/web/status/{tweet_id}"

    # Normalize timestamp
    source_created_at = normalize_source_timestamp(created_at)

    post_score  = _build_engagement_score(metrics)
    num_replies = metrics.get("reply_count", 0) or 0

    heuristic = score_tweet_relevance(text)

    keywords = _extract_keywords(text)

    return {
        # Core pipeline fields
        "source":            "x",
        "source_url":        source_url,
        "author":            username,
        "title":             "",         # X has no titles
        "body":              text,
        "content":           text,
        "keywords_matched":  keywords,
        # Engagement — feeds into signal_ranker engagement scoring
        "post_score":        post_score,
        "num_comments":      num_replies,
        # Freshness — feeds into signal_ranker freshness scoring
        "source_created_at": source_created_at,
        "scraped_at":        datetime.utcnow().isoformat(),
        # Pre-AI quality hint
        "heuristic_score":   heuristic,
        # Debug / traceability
        "query_used":        query_used,
        "tweet_id":          tweet_id,
    }


# ---------------------------------------------------------------------------
# API LAYER
# ---------------------------------------------------------------------------

async def _search_recent(
    client:   httpx.AsyncClient,
    query:    str,
    diag:     XDiagnostics,
    since_dt: datetime,
) -> list[dict]:
    """
    Call the X API v2 recent-search endpoint for one query.
    Returns a list of normalized candidate dicts (may be empty).
    """
    full_query = f"{query} {_QUERY_SUFFIX}"
    start_time = since_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

    params = {
        "query":         full_query,
        "max_results":   MAX_RESULTS_PER_QUERY,
        "start_time":    start_time,
        "tweet.fields":  "created_at,author_id,public_metrics,text,lang",
        "expansions":    "author_id",
        "user.fields":   "username,name",
    }

    diag.queries_attempted += 1
    try:
        resp = await client.get(
            f"{X_API_BASE}/tweets/search/recent",
            params=params,
            timeout=REQUEST_TIMEOUT,
        )
    except httpx.TimeoutException as exc:
        logger.warning("X API timeout for query %r: %s", query[:60], exc)
        diag.other_errors += 1
        return []
    except Exception as exc:
        logger.warning("X API request error for query %r: %s", query[:60], exc)
        diag.other_errors += 1
        return []

    if resp.status_code == 401:
        logger.error(
            "X API auth failure (401) — check X_API_BEARER_TOKEN. "
            "Stopping X scraping for this run."
        )
        diag.auth_failures += 1
        raise _XAuthError("401 Unauthorized")

    if resp.status_code == 403:
        logger.error(
            "X API forbidden (403) — Bearer token may lack required permissions "
            "or the account tier does not support this endpoint."
        )
        diag.auth_failures += 1
        raise _XAuthError("403 Forbidden")

    if resp.status_code == 429:
        retry_after = int(resp.headers.get("x-rate-limit-reset", 60))
        logger.warning(
            "X API rate-limited (429) — rate limit resets at epoch %d. "
            "Skipping remaining queries for this run.",
            retry_after,
        )
        diag.rate_limit_hits += 1
        raise _XRateLimitError("429 Too Many Requests")

    if resp.status_code != 200:
        body = resp.text[:200].replace("\n", " ")
        logger.warning(
            "X API non-200 HTTP %d for query %r | body=%r",
            resp.status_code, query[:60], body,
        )
        diag.other_errors += 1
        return []

    diag.queries_success += 1

    try:
        payload = resp.json()
    except Exception as exc:
        logger.warning("X API JSON parse failure: %s", exc)
        diag.other_errors += 1
        return []

    tweets     = payload.get("data") or []
    users_list = (payload.get("includes") or {}).get("users") or []
    users_by_id: dict[str, dict] = {u["id"]: u for u in users_list if "id" in u}

    diag.tweets_returned += len(tweets)

    candidates: list[dict] = []
    for tweet in tweets:
        c = _normalize_tweet(tweet, users_by_id, query)
        if c is None:
            continue
        diag.tweets_normalized += 1

        # Prefilter
        if c["heuristic_score"] == -99:
            diag.tweets_prefiltered += 1
            continue

        if c["heuristic_score"] < MIN_HEURISTIC_SCORE:
            diag.tweets_below_thresh += 1
            continue

        candidates.append(c)

    return candidates


class _XAuthError(Exception):
    """Raised on 401/403 — signals the caller to abort all queries."""


class _XRateLimitError(Exception):
    """Raised on 429 — signals the caller to abort all queries."""


# ---------------------------------------------------------------------------
# DEDUPLICATION
# ---------------------------------------------------------------------------

def _normalize_tweet_url(url: str) -> str:
    """Normalise a tweet URL for dedup comparison."""
    try:
        p = urlparse(url)
        return urlunparse((p.scheme, p.netloc, p.path.rstrip("/"), "", "", "")).lower()
    except Exception:
        return url.lower()


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

async def scrape_x() -> list[dict]:
    """
    Fetch recent business pain signals from X via the official API v2.

    Returns a list of candidate dicts compatible with:
        signal_ranker.select_candidates_for_ai()
        pain_signal_analyzer.analyze_batch()

    Returns [] silently if X is disabled or credentials are missing.
    """
    if not X_ENABLED:
        logger.debug("X scraper: X_ENABLED is false — skipping.")
        return []

    if not BEARER_TOKEN:
        logger.warning(
            "X scraper: X_API_BEARER_TOKEN is not set — skipping X source. "
            "Set X_ENABLED=true and X_API_BEARER_TOKEN=<token> to enable."
        )
        return []

    diag = XDiagnostics()
    since_dt = datetime.now(timezone.utc) - timedelta(days=LOOKBACK_DAYS)

    queries = ALL_QUERIES[:MAX_QUERIES_PER_RUN]
    logger.info(
        "X scraper: %d queries | lookback=%dd | max_results=%d/query",
        len(queries), LOOKBACK_DAYS, MAX_RESULTS_PER_QUERY,
    )

    headers = {
        "Authorization": f"Bearer {BEARER_TOKEN}",
        "User-Agent":    "lead-engine-pain-scraper/1.0",
    }

    seen_tweet_ids: set[str] = set()
    all_candidates: list[dict] = []

    async with httpx.AsyncClient(headers=headers, follow_redirects=True) as client:
        for i, query in enumerate(queries):
            try:
                candidates = await _search_recent(client, query, diag, since_dt)
            except _XAuthError:
                # Auth failed — no point retrying any queries
                logger.error("X scraper: aborting all queries due to auth failure.")
                break
            except _XRateLimitError:
                # Rate limited — stop gracefully, use what we have
                logger.warning("X scraper: stopping early due to rate limit.")
                break

            # Dedup by tweet_id within this run
            for c in candidates:
                tid = c.get("tweet_id", "")
                if tid and tid in seen_tweet_ids:
                    continue
                if tid:
                    seen_tweet_ids.add(tid)
                all_candidates.append(c)

            diag.tweets_passed = len(all_candidates)

            # Courtesy delay between queries (except after the last one)
            if i < len(queries) - 1:
                await asyncio.sleep(QUERY_DELAY)

    diag.tweets_passed = len(all_candidates)
    diag.log_summary()

    logger.info(
        "X scraper: %d candidates ready for pipeline",
        len(all_candidates),
    )
    return all_candidates
