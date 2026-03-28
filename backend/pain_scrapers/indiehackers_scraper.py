"""
Indie Hackers pain-signal scraper — Algolia API backend.

IH is an Ember.js SPA; every route returns the same HTML shell.
We call the public Algolia 'discussions' index directly.

KEY FRESHNESS DESIGN
  Algolia's default ranking is relevance, not recency.  Without a time
  filter it returns evergreen top-voted posts from years ago that will be
  hard-rejected by the global signal_ranker (HARD_MAX_DAYS=30).
  To avoid this waste we push the 30-day filter INTO the Algolia query
  via numericFilters=createdTimestamp>{since_ms}.  Only genuinely fresh
  posts reach the pipeline.

  Source-level priority score (0–10) is computed before candidates leave
  this module, combining freshness + heuristic relevance + engagement.
  Hits below _MIN_SOURCE_SCORE are discarded here, not downstream.

  Result: fewer candidates, but almost all survive the global selector.

Field mapping (two post schemas):
  itemType == "post"      → username (str), numUpvotes, numReplies
  itemType == "new-post"  → usernames (list), numLikes, numComments
  Both: itemId → /post/{itemId}
  createdTimestamp is Unix milliseconds.

Config (env vars):
  IH_ENABLED                  (default: true)
  IH_ALGOLIA_APP_ID           (default: N86T1R3OWZ)
  IH_ALGOLIA_API_KEY          (default: 5140dac5e87f47346abbda1a34ee70c3)
  IH_HITS_PER_QUERY           (default: 15)
  IH_MAX_QUERIES_PER_RUN      (default: 10)
  IH_QUERY_DELAY_SECONDS      (default: 1.0)
  IH_MIN_HEURISTIC_SCORE      (default: 2)   — 0–11 scale; 2 = needs ≥1 pain signal
  IH_MIN_SOURCE_SCORE         (default: 2.0) — 0–10 weighted priority score
  IH_MAX_CANDIDATES           (default: 25)  — hard cap on output per run
  IH_MIN_CANDIDATES_TARGET    (default: 8)
  IH_FRESHNESS_WINDOW_DAYS    (default: matches PAIN_SIGNAL_HARD_MAX_DAYS=30)
"""
import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from urllib.parse import urlencode

import httpx

from pain_scrapers.signal_ranker import (
    normalize_source_timestamp,
    HARD_MAX_DAYS,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

IH_ENABLED      = os.getenv("IH_ENABLED", "true").lower() in ("1", "true", "yes")

_ALGOLIA_APP_ID = os.getenv("IH_ALGOLIA_APP_ID",  "N86T1R3OWZ")
_ALGOLIA_KEY    = os.getenv("IH_ALGOLIA_API_KEY",  "5140dac5e87f47346abbda1a34ee70c3")

_HITS_PER_QUERY    = int(os.getenv("IH_HITS_PER_QUERY",          "15"))
_MAX_QUERIES       = int(os.getenv("IH_MAX_QUERIES_PER_RUN",     "10"))
_QUERY_DELAY       = float(os.getenv("IH_QUERY_DELAY_SECONDS",   "1.0"))
_MIN_HEURISTIC     = int(os.getenv("IH_MIN_HEURISTIC_SCORE",     "2"))
_MIN_SOURCE_SCORE  = float(os.getenv("IH_MIN_SOURCE_SCORE",      "2.0"))
_MAX_CANDIDATES    = int(os.getenv("IH_MAX_CANDIDATES",          "25"))
_TARGET_CANDS      = int(os.getenv("IH_MIN_CANDIDATES_TARGET",   "8"))

# Source-level freshness window in days.  Defaults to the global hard max so
# we never fetch posts that the global selector would immediately discard.
_FRESHNESS_DAYS = int(os.getenv("IH_FRESHNESS_WINDOW_DAYS", str(HARD_MAX_DAYS)))

IH_BASE       = "https://www.indiehackers.com"
_ALGOLIA_BASE = f"https://{_ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/discussions"

_ALGOLIA_HEADERS = {
    "X-Algolia-Application-Id": _ALGOLIA_APP_ID,
    "X-Algolia-API-Key":        _ALGOLIA_KEY,
    "Accept":                   "application/json",
}

# ---------------------------------------------------------------------------
# SEARCH QUERIES
# Tight, operational-pain focused.  Avoids broad/evergreen phrasing that
# surfaces old tutorials, launch announcements, or developer lifestyle posts.
# Ordered best-first; capped at _MAX_QUERIES per run.
# ---------------------------------------------------------------------------

SEARCH_QUERIES: list[str] = [
    "manual invoicing taking too long",
    "client follow up slipping through cracks",
    "onboarding process is broken",
    "admin taking over my time",
    "managing clients is a mess",
    "spreadsheet workflow breaking down",
    "CRM not working for small business",
    "scheduling problems with clients",
    "overwhelmed by admin tasks",
    "missing leads no system",
    "follow up system not working",
    "repetitive operations every week",
    "still doing this manually",
    "no tool for this problem",
    "wasting hours every week on",
]

# ---------------------------------------------------------------------------
# HEURISTIC FILTERS
# ---------------------------------------------------------------------------

# Hard disqualifiers — any match → score -99 → immediate reject
DISQUALIFY_FRAGMENTS: list[str] = [
    # Revenue milestones / brag posts
    "just hit $", "just crossed $", "we hit $", "reached $",
    "passed $1k mrr", "crossed $5k mrr", "hit $10k mrr",
    "first dollar", "first customer", "first sale",
    # Progress updates / recaps
    "milestone:", "month 1:", "month 2:", "month 3:", "month 4:",
    "year in review", "annual review", "year 1 recap", "q1 recap",
    # Origin / story posts
    "the story of", "the story behind", "how i started", "started as a side",
    "here's my journey", "my journey to", "lessons learned from",
    "from idea to", "how we got to",
    # Launch announcements
    "we just launched", "just launched", "today we launched",
    "product hunt", "launching today", "show ih:", "show hn:",
    "i just shipped", "we just shipped", "just released", "v2 is live",
    # Self-promotion / sales
    "i built", "i made", "i created", "check out my",
    "book a demo", "free trial", "sign up now",
    "we help companies", "i help businesses", "we help founders",
    # Hiring
    "we're hiring", "now hiring", "join our team",
    # Lifestyle / generic dev
    "how many hours do you", "how long do you work",
    "morning routine", "daily routine", "work-life balance",
    "remote work tips", "productivity tips", "focus tips",
]

# Positive current-pain phrases — each match earns a bonus
_CURRENT_PAIN_PHRASES: list[str] = [
    "still manually", "we still do", "currently using spreadsheet",
    "no system for", "nothing exists for", "can't find a tool",
    "can't find software", "cobbled together", "duct tape",
    "paying someone to", "hiring va to",
    "falling through the cracks", "slipping through",
    "missing follow ups", "losing track of",
    "spending hours on", "takes me hours",
    "eats up my", "kills my whole day",
    "no good way to", "struggled to find",
]

_PAIN_KW: list[str] = [
    "manual", "manually", "repetitive", "tedious", "spreadsheet",
    "data entry", "automate", "automation", "bottleneck", "overwhelmed",
]
_TIME_KW: list[str] = [
    "takes forever", "hours every", "all day", "too long",
    "wasting time", "wasting hours", "each week", "per week",
]
_WORKFLOW_KW: list[str] = [
    "process", "workflow", "system", "pipeline", "crm",
    "follow-up", "follow up", "onboarding", "invoicing", "scheduling",
    "lead", "client", "customer", "operations", "admin",
]
_FRUSTRATION_KW: list[str] = [
    "nightmare", "mess", "broken", "chaos", "chaotic",
    "frustrating", "hate", "annoying", "pain in", "killing",
]
_INTENT_KW: list[str] = [
    "tool", "software", "app", "system", "platform",
    "recommendation", "what do you use", "how do you manage",
    "looking for", "need a better", "is there a way",
]
_QUESTION_STARTERS = (
    "how", "is there", "what", "can i", "anyone", "does anyone",
    "looking for", "need", "best way", "any tool", "recommend",
)


def _is_disqualified(text: str) -> bool:
    lower = text.lower()
    return any(frag in lower for frag in DISQUALIFY_FRAGMENTS)


def score_post_relevance(title: str, body: str) -> int:
    """
    Returns -99 for spam/promo/story posts.
    Otherwise returns [0, 13]:

      +2  pain keywords          +2  time-cost language
      +2  workflow/process words +2  frustration signals
      +2  solution-seeking       +1  question form
      +2  current-pain phrases   (bonus — active frustration language)
      -99 hard disqualifier
    """
    full = f"{title} {body}".lower()
    if _is_disqualified(full):
        return -99
    if len(full.strip()) < 20:
        return 0
    score = 0
    if any(kw in full for kw in _PAIN_KW):             score += 2
    if any(kw in full for kw in _TIME_KW):             score += 2
    if any(kw in full for kw in _WORKFLOW_KW):         score += 2
    if any(kw in full for kw in _FRUSTRATION_KW):      score += 2
    if any(kw in full for kw in _INTENT_KW):           score += 2
    if any(p in full for p in _CURRENT_PAIN_PHRASES):  score += 2
    if "?" in title or any(title.lower().startswith(w) for w in _QUESTION_STARTERS):
        score += 1
    return score


def _extract_keywords(title: str, body: str) -> list[str]:
    full = f"{title} {body}".lower()
    pool = _PAIN_KW + _TIME_KW + _WORKFLOW_KW + _FRUSTRATION_KW
    return list(dict.fromkeys(kw for kw in pool if kw in full))


# ---------------------------------------------------------------------------
# SOURCE-LEVEL FRESHNESS
# ---------------------------------------------------------------------------

def _freshness_score(ts_ms) -> tuple[float, str]:
    """
    Given a createdTimestamp in milliseconds, returns (score 0–10, label).

    Scoring:
      0–72 h  → 10.0  "fresh_72h"
      3–7 d   →  8.0  "fresh_7d"
      7–30 d  →  4.0  "fresh_30d"
      > 30 d  →  0.0  "stale"
      None    →  0.0  "no_timestamp"  (discarded at source — never reaches pipeline)
    """
    if ts_ms is None:
        return 0.0, "no_timestamp"

    created_at = normalize_source_timestamp(ts_ms)  # handles ms > 9_999_999_999
    if created_at is None:
        return 0.0, "no_timestamp"

    age_h = (datetime.now(timezone.utc) - created_at).total_seconds() / 3600.0

    if age_h > _FRESHNESS_DAYS * 24:
        return 0.0, "stale"
    if age_h <= 72:
        return 10.0, "fresh_72h"
    if age_h <= 168:   # 7 days
        return 8.0, "fresh_7d"
    return 4.0, "fresh_30d"


# ---------------------------------------------------------------------------
# SOURCE-LEVEL PRIORITY SCORE
# ---------------------------------------------------------------------------

def _source_priority_score(
    h_score:      int,
    fresh_score:  float,
    post_score:   int,
    num_comments: int,
) -> float:
    """
    Lightweight 0–10 priority score used to rank and cap candidates
    before they leave this module.

    Weights: freshness 40% | heuristic relevance 40% | engagement 20%

    This is NOT a replacement for global signal_ranker scoring.
    It is a source-level preselection gate only.
    """
    # Normalize heuristic (0–13 scale) to 0–10
    h_norm = min(10.0, max(0.0, h_score * 10.0 / 13.0))

    # Engagement: upvotes worth more than comments (engagement cap 10)
    engagement = min(10.0, post_score * 0.3 + num_comments * 0.4)

    return round(h_norm * 0.4 + fresh_score * 0.4 + engagement * 0.2, 2)


# ---------------------------------------------------------------------------
# DIAGNOSTICS
# ---------------------------------------------------------------------------

@dataclass
class IHDiagnostics:
    queries_attempted:      int = 0
    queries_ok:             int = 0
    queries_failed:         int = 0
    raw_hits:               int = 0
    deduped_out:            int = 0
    stale_rejected:         int = 0   # has timestamp, older than _FRESHNESS_DAYS
    missing_ts_rejected:    int = 0   # no/unparseable timestamp → discarded
    heuristic_rejected:     int = 0   # score == -99 or < _MIN_HEURISTIC
    weak_score_rejected:    int = 0   # below _MIN_SOURCE_SCORE
    over_cap_rejected:      int = 0   # trimmed to _MAX_CANDIDATES
    fresh_kept:             int = 0   # passed freshness check
    final_candidates:       int = 0
    failed_queries:         list = field(default_factory=list)
    query_contributions:    dict = field(default_factory=dict)  # query→count

    def log_summary(self) -> None:
        logger.info(
            "IH scraper done | "
            "queries=%d/%d (%d failed) | "
            "raw=%d → deduped=-%d stale=-%d no_ts=-%d heuristic=-%d weak=-%d cap=-%d → final=%d",
            self.queries_ok, self.queries_attempted, self.queries_failed,
            self.raw_hits,
            self.deduped_out,
            self.stale_rejected,
            self.missing_ts_rejected,
            self.heuristic_rejected,
            self.weak_score_rejected,
            self.over_cap_rejected,
            self.final_candidates,
        )
        if self.query_contributions:
            top = sorted(self.query_contributions.items(), key=lambda x: -x[1])[:5]
            logger.info("IH top query contributors: %s", top)
        if self.failed_queries:
            logger.warning("IH failed queries: %s", self.failed_queries)


# ---------------------------------------------------------------------------
# ALGOLIA FETCH
# ---------------------------------------------------------------------------

def _since_ms() -> int:
    """Unix timestamp in milliseconds for _FRESHNESS_DAYS ago."""
    return int(
        (datetime.now(timezone.utc) - timedelta(days=_FRESHNESS_DAYS)).timestamp() * 1000
    )


async def _algolia_search(
    client: httpx.AsyncClient,
    query:  str,
    diag:   IHDiagnostics,
) -> list[dict]:
    """
    Run one Algolia query with source-level freshness filter baked in.

    numericFilters=createdTimestamp>{since_ms} tells Algolia to only return
    posts created within _FRESHNESS_DAYS.  This prevents stale evergreen
    results from entering the pipeline at all.
    """
    params = urlencode({
        "query":          query,
        "hitsPerPage":    _HITS_PER_QUERY,
        "filters":        "partNumber=1",
        "numericFilters": f"createdTimestamp>{_since_ms()}",
    })
    url = f"{_ALGOLIA_BASE}?{params}"
    diag.queries_attempted += 1

    try:
        resp = await client.get(url, headers=_ALGOLIA_HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning("IH Algolia query %r → HTTP %d", query, resp.status_code)
            diag.queries_failed += 1
            diag.failed_queries.append(query)
            return []

        data  = resp.json()
        hits  = data.get("hits", [])
        nb    = data.get("nbHits", "?")
        diag.queries_ok  += 1
        diag.raw_hits    += len(hits)
        logger.info(
            "IH Algolia | query=%r → %d hits (nbHits=%s, window=%dd)",
            query, len(hits), nb, _FRESHNESS_DAYS,
        )
        return hits

    except Exception as exc:
        logger.warning("IH Algolia query %r failed: %s", query, exc)
        diag.queries_failed += 1
        diag.failed_queries.append(query)
        return []


# ---------------------------------------------------------------------------
# HIT → CANDIDATE NORMALIZATION
# ---------------------------------------------------------------------------

def _normalize_hit(hit: dict) -> dict | None:
    """Convert one Algolia hit to a pipeline-compatible dict. Returns None if unusable."""
    title   = (hit.get("title") or "").strip()
    body    = (hit.get("body")  or "").strip()
    item_id = hit.get("itemId") or ""

    if not title or not item_id:
        return None
    if len(title) < 15:   # filter noise titles that are too short to be meaningful
        return None

    # Author
    username = hit.get("username") or ""
    if not username:
        usernames = hit.get("usernames") or []
        username  = usernames[0] if usernames else ""

    # Engagement (two schemas)
    post_score   = hit.get("numUpvotes")  or hit.get("numLikes")    or 0
    num_comments = hit.get("numReplies")  or hit.get("numComments") or 0

    # Timestamp (milliseconds)
    created_ts = hit.get("createdTimestamp") or hit.get("publishedTimestamp")

    return {
        "title":            title,
        "body":             body[:600],
        "source_url":       f"{IH_BASE}/post/{item_id}",
        "author":           username,
        "source_created_at":created_ts,
        "post_score":       int(post_score)   if isinstance(post_score,   (int, float)) else 0,
        "num_comments":     int(num_comments) if isinstance(num_comments, (int, float)) else 0,
    }


# ---------------------------------------------------------------------------
# MAIN ENTRY POINT
# ---------------------------------------------------------------------------

async def scrape_indiehackers() -> list[dict]:
    """
    Fetch Indie Hackers pain signals via the Algolia search API.

    Returns candidate dicts compatible with signal_ranker + pain_signal_analyzer.
    Returns [] silently if IH_ENABLED is false.
    """
    if not IH_ENABLED:
        logger.info("IH scraper: disabled (IH_ENABLED=false)")
        return []

    diag         = IHDiagnostics()
    seen_urls:   set[str] = set()
    pre_cap:     list[dict] = []   # accumulate before applying _MAX_CANDIDATES cap

    queries = SEARCH_QUERIES[:_MAX_QUERIES]
    logger.info(
        "IH scraper: Algolia discussions | %d queries | hits/query=%d | "
        "freshness_window=%dd | min_heuristic=%d | min_source_score=%.1f | cap=%d",
        len(queries), _HITS_PER_QUERY, _FRESHNESS_DAYS,
        _MIN_HEURISTIC, _MIN_SOURCE_SCORE, _MAX_CANDIDATES,
    )

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for query in queries:
            hits = await _algolia_search(client, query, diag)
            kept_this_query = 0

            for hit in hits:
                raw = _normalize_hit(hit)
                if raw is None:
                    continue

                norm_url = raw["source_url"].rstrip("/").lower()
                if norm_url in seen_urls:
                    diag.deduped_out += 1
                    continue
                seen_urls.add(norm_url)

                # --- Source-level freshness gate ---
                fresh_score, fresh_label = _freshness_score(raw["source_created_at"])

                if fresh_label == "no_timestamp":
                    # No timestamp → discard; can't verify freshness
                    diag.missing_ts_rejected += 1
                    logger.debug(
                        "IH reject no_timestamp: %r", raw["title"][:60]
                    )
                    continue

                if fresh_label == "stale":
                    # Timestamp present but beyond window — should be rare
                    # because Algolia filter catches most, but handle edge cases
                    diag.stale_rejected += 1
                    logger.debug(
                        "IH reject stale: %r", raw["title"][:60]
                    )
                    continue

                diag.fresh_kept += 1

                # --- Heuristic relevance gate ---
                title   = raw["title"]
                body    = raw["body"]
                h_score = score_post_relevance(title, body)

                if h_score == -99:
                    diag.heuristic_rejected += 1
                    logger.debug("IH reject disqualified: %r", title[:60])
                    continue

                if h_score < _MIN_HEURISTIC:
                    diag.heuristic_rejected += 1
                    logger.debug(
                        "IH reject low_heuristic (h=%d): %r", h_score, title[:60]
                    )
                    continue

                # --- Source-level priority score gate ---
                src_score = _source_priority_score(
                    h_score, fresh_score,
                    raw["post_score"], raw["num_comments"],
                )
                if src_score < _MIN_SOURCE_SCORE:
                    diag.weak_score_rejected += 1
                    logger.debug(
                        "IH reject weak_score (%.2f): %r", src_score, title[:60]
                    )
                    continue

                content = f"{title}\n\n{body}".strip() if body else title

                pre_cap.append({
                    "source":             "indiehackers",
                    "source_url":         raw["source_url"],
                    "author":             raw["author"],
                    "title":              title,
                    "body":               body,
                    "content":            content,
                    "keywords_matched":   _extract_keywords(title, body),
                    "post_score":         raw["post_score"],
                    "num_comments":       raw["num_comments"],
                    "source_created_at":  normalize_source_timestamp(
                        raw["source_created_at"]
                    ),
                    "scraped_at":         datetime.utcnow().isoformat(),
                    "heuristic_score":    h_score,
                    "_ih_source_score":   src_score,    # internal — not forwarded
                    "_ih_fresh_label":    fresh_label,  # internal — not forwarded
                })
                kept_this_query += 1

            diag.query_contributions[query] = kept_this_query
            await asyncio.sleep(_QUERY_DELAY)

    # Sort by source priority score descending, then apply hard cap
    pre_cap.sort(key=lambda x: x.get("_ih_source_score", 0), reverse=True)

    if len(pre_cap) > _MAX_CANDIDATES:
        diag.over_cap_rejected = len(pre_cap) - _MAX_CANDIDATES
        pre_cap = pre_cap[:_MAX_CANDIDATES]

    # Strip internal scoring fields before handing to pipeline
    all_signals = []
    for s in pre_cap:
        s.pop("_ih_source_score", None)
        s.pop("_ih_fresh_label",  None)
        all_signals.append(s)

    diag.final_candidates = len(all_signals)
    diag.log_summary()

    if all_signals:
        logger.info("IH scraper: top candidates by source score:")
        for i, s in enumerate(all_signals[:5], 1):
            created = s.get("source_created_at")
            age_str = (
                f"{int((datetime.now(timezone.utc) - created).total_seconds() / 3600)}h ago"
                if created else "no_ts"
            )
            logger.info(
                "  [%d] h=%d  age=%s  score=%d comments=%d  %r",
                i,
                s.get("heuristic_score", 0),
                age_str,
                s.get("post_score", 0),
                s.get("num_comments", 0),
                s["title"][:70],
            )
    else:
        logger.warning(
            "IH scraper: ZERO final candidates | "
            "raw=%d stale=-%d no_ts=-%d heuristic=-%d weak=-%d | "
            "IH may not have fresh pain posts this run — normal if activity is low",
            diag.raw_hits, diag.stale_rejected, diag.missing_ts_rejected,
            diag.heuristic_rejected, diag.weak_score_rejected,
        )

    if 0 < len(all_signals) < _TARGET_CANDS:
        logger.info(
            "IH scraper: %d candidates (below soft target %d) — "
            "consider widening IH_FRESHNESS_WINDOW_DAYS or adding queries",
            len(all_signals), _TARGET_CANDS,
        )

    return all_signals
