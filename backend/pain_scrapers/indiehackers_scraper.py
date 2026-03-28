"""
Indie Hackers pain-signal scraper — Algolia API backend.

IH is an Ember.js SPA; every route returns the same HTML shell with no
server-rendered post content.  HTML scraping is therefore useless.

Instead we call the Algolia search index that IH uses internally.  The
application ID and public search-only API key are embedded in the page
meta config that IH ships to every visitor, so using them is equivalent
to any browser that loads the site.

Endpoint: https://{APP_ID}-dsn.algolia.net/1/indexes/discussions
  • partNumber=1 filter   — avoids duplicate chunks of multi-part posts
  • relevance ordering    — Algolia's default; most-relevant first
  • no auth beyond the public search-only key

Field mapping (two post schemas exist):
  itemType == "post"      → username (str), numUpvotes, numReplies
  itemType == "new-post"  → usernames (list), numLikes, numComments
  Both use itemId for the URL: /post/{itemId}
  createdTimestamp is Unix milliseconds.

Config (env vars):
  IH_ENABLED                  (default: true)
  IH_ALGOLIA_APP_ID           (default: N86T1R3OWZ)
  IH_ALGOLIA_API_KEY          (default: 5140dac5e87f47346abbda1a34ee70c3)
  IH_HITS_PER_QUERY           (default: 20)
  IH_MAX_QUERIES_PER_RUN      (default: 8)
  IH_QUERY_DELAY_SECONDS      (default: 1.0)
  IH_MIN_HEURISTIC_SCORE      (default: 0)   — negative = spam-filter only
  IH_MIN_CANDIDATES_TARGET    (default: 10)
"""
import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime
from urllib.parse import urlencode

import httpx

from pain_scrapers.signal_ranker import normalize_source_timestamp

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# CONFIG
# ---------------------------------------------------------------------------

IH_ENABLED      = os.getenv("IH_ENABLED", "true").lower() in ("1", "true", "yes")

# Public credentials embedded in IH page meta — override if they rotate
_ALGOLIA_APP_ID = os.getenv("IH_ALGOLIA_APP_ID",  "N86T1R3OWZ")
_ALGOLIA_KEY    = os.getenv("IH_ALGOLIA_API_KEY",  "5140dac5e87f47346abbda1a34ee70c3")

_HITS_PER_QUERY   = int(os.getenv("IH_HITS_PER_QUERY",          "20"))
_MAX_QUERIES      = int(os.getenv("IH_MAX_QUERIES_PER_RUN",      "8"))
_QUERY_DELAY      = float(os.getenv("IH_QUERY_DELAY_SECONDS",    "1.0"))
_MIN_HEURISTIC    = int(os.getenv("IH_MIN_HEURISTIC_SCORE",      "0"))
_TARGET_CANDS     = int(os.getenv("IH_MIN_CANDIDATES_TARGET",    "10"))

IH_BASE         = "https://www.indiehackers.com"
_ALGOLIA_BASE   = f"https://{_ALGOLIA_APP_ID}-dsn.algolia.net/1/indexes/discussions"

_ALGOLIA_HEADERS = {
    "X-Algolia-Application-Id": _ALGOLIA_APP_ID,
    "X-Algolia-API-Key":        _ALGOLIA_KEY,
    "Accept":                   "application/json",
}

# ---------------------------------------------------------------------------
# SEARCH QUERIES
# Ordered roughly by expected signal quality.  Capped at _MAX_QUERIES.
# ---------------------------------------------------------------------------

SEARCH_QUERIES: list[str] = [
    "manual process taking too long",
    "wasting hours every week",
    "need to automate workflow",
    "spreadsheet hell",
    "repetitive tasks killing productivity",
    "overwhelmed with admin work",
    "how do you manage clients",
    "looking for a tool to automate",
    "too much manual data entry",
    "small business operations problem",
    "tired of doing manually",
    "is there software for",
]

# ---------------------------------------------------------------------------
# HEURISTIC KEYWORDS
# ---------------------------------------------------------------------------

DISQUALIFY_FRAGMENTS: list[str] = [
    "just hit $", "just crossed $", "we hit $", "reached $",
    "milestone:", "month 1:", "month 2:", "month 3:",
    "we just launched", "just launched", "today we launched",
    "product hunt", "launching today",
    "i built", "i made", "i created", "check out my",
    "book a demo", "free trial", "sign up now",
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


def _is_disqualified(text: str) -> bool:
    lower = text.lower()
    return any(frag in lower for frag in DISQUALIFY_FRAGMENTS)


def score_post_relevance(title: str, body: str) -> int:
    """
    Returns -99 for spam/promo posts, otherwise [0, 11].

      +2  pain keywords        +2  time-cost language
      +2  workflow words       +2  frustration signals
      +2  solution-seeking     +1  question form
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
    pool = _PAIN_KW + _TIME_KW + _WORKFLOW_KW + _FRUSTRATION_KW
    return list(dict.fromkeys(kw for kw in pool if kw in full))


# ---------------------------------------------------------------------------
# DIAGNOSTICS
# ---------------------------------------------------------------------------

@dataclass
class IHDiagnostics:
    queries_attempted:  int = 0
    queries_ok:         int = 0
    queries_failed:     int = 0
    raw_hits:           int = 0
    deduped_out:        int = 0
    heuristic_rejected: int = 0
    final_candidates:   int = 0
    failed_queries:     list = field(default_factory=list)

    def log_summary(self) -> None:
        logger.info(
            "IH scraper done | queries=%d/%d (%d failed) | "
            "raw_hits=%d deduped_out=%d heuristic_rejected=%d final=%d | "
            "failed_queries=%s",
            self.queries_ok, self.queries_attempted, self.queries_failed,
            self.raw_hits, self.deduped_out,
            self.heuristic_rejected, self.final_candidates,
            self.failed_queries or "none",
        )


# ---------------------------------------------------------------------------
# ALGOLIA FETCH
# ---------------------------------------------------------------------------

async def _algolia_search(
    client:  httpx.AsyncClient,
    query:   str,
    diag:    IHDiagnostics,
) -> list[dict]:
    """
    Run one Algolia query against the IH discussions index.
    Returns a list of raw hit dicts (Algolia format).
    """
    params = urlencode({
        "query":          query,
        "hitsPerPage":    _HITS_PER_QUERY,
        "filters":        "partNumber=1",   # one hit per post — no duplicate chunks
    })
    url = f"{_ALGOLIA_BASE}?{params}"
    diag.queries_attempted += 1

    try:
        resp = await client.get(url, headers=_ALGOLIA_HEADERS, timeout=15)
        if resp.status_code != 200:
            logger.warning(
                "IH Algolia query %r → HTTP %d", query, resp.status_code,
            )
            diag.queries_failed += 1
            diag.failed_queries.append(query)
            return []

        data   = resp.json()
        hits   = data.get("hits", [])
        nb     = data.get("nbHits", "?")
        diag.queries_ok    += 1
        diag.raw_hits      += len(hits)
        logger.info(
            "IH Algolia | query=%r → %d hits returned (nbHits=%s)",
            query, len(hits), nb,
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
    """
    Convert one Algolia hit into a pipeline-compatible candidate dict.
    Returns None if essential fields are missing.
    """
    title   = (hit.get("title") or "").strip()
    body    = (hit.get("body")  or "").strip()
    item_id = hit.get("itemId") or ""

    if not title or not item_id:
        return None

    # URL — both old and new post types use /post/{itemId}
    source_url = f"{IH_BASE}/post/{item_id}"

    # Author — old posts: username (str); new posts: usernames (list)
    username = hit.get("username") or ""
    if not username:
        usernames = hit.get("usernames") or []
        username  = usernames[0] if usernames else ""

    # Engagement — two schemas
    post_score = (
        hit.get("numUpvotes") or hit.get("numLikes") or 0
    )
    num_comments = (
        hit.get("numReplies") or hit.get("numComments") or 0
    )

    # Timestamp (milliseconds) — normalize_source_timestamp handles ms→s conversion
    created_ts = hit.get("createdTimestamp") or hit.get("publishedTimestamp")

    return {
        "title":            title,
        "body":             body[:600],
        "source_url":       source_url,
        "author":           username,
        "source_created_at":created_ts,
        "post_score":       int(post_score)    if isinstance(post_score,    (int, float)) else 0,
        "num_comments":     int(num_comments)  if isinstance(num_comments,  (int, float)) else 0,
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
    all_signals: list[dict] = []

    queries = SEARCH_QUERIES[:_MAX_QUERIES]
    logger.info(
        "IH scraper: Algolia index=discussions | %d queries | hits_per_query=%d | "
        "heuristic_min=%d | target=%d candidates",
        len(queries), _HITS_PER_QUERY, _MIN_HEURISTIC, _TARGET_CANDS,
    )

    async with httpx.AsyncClient(follow_redirects=True) as client:
        for query in queries:
            hits = await _algolia_search(client, query, diag)

            for hit in hits:
                raw = _normalize_hit(hit)
                if raw is None:
                    continue

                norm_url = raw["source_url"].rstrip("/").lower()
                if norm_url in seen_urls:
                    diag.deduped_out += 1
                    continue
                seen_urls.add(norm_url)

                title = raw["title"]
                body  = raw["body"]

                h_score = score_post_relevance(title, body)
                if h_score == -99:
                    diag.heuristic_rejected += 1
                    continue
                if h_score < _MIN_HEURISTIC:
                    diag.heuristic_rejected += 1
                    continue

                content = f"{title}\n\n{body}".strip() if body else title

                all_signals.append({
                    "source":            "indiehackers",
                    "source_url":        raw["source_url"],
                    "author":            raw["author"],
                    "title":             title,
                    "body":              body,
                    "content":           content,
                    "keywords_matched":  _extract_keywords(title, body),
                    "post_score":        raw["post_score"],
                    "num_comments":      raw["num_comments"],
                    "source_created_at": normalize_source_timestamp(
                        raw["source_created_at"]
                    ),
                    "scraped_at":        datetime.utcnow().isoformat(),
                    "heuristic_score":   h_score,
                })

            await asyncio.sleep(_QUERY_DELAY)

    diag.final_candidates = len(all_signals)
    diag.log_summary()

    # Sample log — first 5 candidates
    if all_signals:
        logger.info("IH scraper: sample output (first %d):", min(5, len(all_signals)))
        for i, s in enumerate(all_signals[:5], 1):
            logger.info(
                "  [%d] h=%d score=%d comments=%d  %r  %s",
                i,
                s.get("heuristic_score", 0),
                s.get("post_score", 0),
                s.get("num_comments", 0),
                s["title"][:70],
                s["source_url"],
            )
    else:
        logger.warning(
            "IH scraper: ZERO final candidates — "
            "raw_hits=%d deduped=%d rejected=%d | "
            "check IH_ALGOLIA_APP_ID / IH_ALGOLIA_API_KEY env vars",
            diag.raw_hits, diag.deduped_out, diag.heuristic_rejected,
        )

    if 0 < len(all_signals) < _TARGET_CANDS:
        logger.info(
            "IH scraper: %d candidates (below target %d)",
            len(all_signals), _TARGET_CANDS,
        )

    return all_signals
