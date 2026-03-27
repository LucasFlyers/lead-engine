"""
Pain signal freshness, ranking, and pre-AI selection layer.

All freshness windows, source confidence values, and ranking weights live here.
Everything else imports from this module so tuning requires only one file.

Pre-AI pipeline:
  annotate_candidate(signal)          → adds scoring fields in-place
  select_candidates_for_ai(signals)   → filters + ranks → (to_analyze, rejected)

Post-AI pipeline:
  compute_final_rank_score(signal)    → float 0–10, combines AI + freshness + engagement

Fresh install uses ENV vars to override any constant at deploy time.
"""
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Freshness windows (configurable via env)
# ---------------------------------------------------------------------------
FRESH_HOURS                  = int(os.getenv("PAIN_SIGNAL_FRESH_HOURS",                 "72"))
FALLBACK_DAYS                = int(os.getenv("PAIN_SIGNAL_FALLBACK_DAYS",                "7"))
HARD_MAX_DAYS                = int(os.getenv("PAIN_SIGNAL_HARD_MAX_DAYS",               "30"))
MIN_RESULTS_BEFORE_FALLBACK  = int(os.getenv("PAIN_SIGNAL_MIN_RESULTS_BEFORE_FALLBACK", "10"))

# ---------------------------------------------------------------------------
# Source confidence  (0–10 scale — used in pre-AI and final rank)
# ---------------------------------------------------------------------------
# Lower = less trustworthy as a prospecting source, also easier to get stale results
SOURCE_CONFIDENCE: dict[str, float] = {
    "reddit":       7.0,   # solid, real operators, recent posts via t=month
    "g2":           9.0,   # software review — highly commercial intent
    "capterra":     9.0,   # same
    "indiehackers": 7.0,   # founder community, good signal quality
    "hackernews":   5.0,   # tends to return old results; often developer-centric
    "forum":        6.0,   # generic catch-all
}
SOURCE_CONFIDENCE_DEFAULT = 5.0   # for unknown sources

# ---------------------------------------------------------------------------
# Pre-AI ranking weights  (must sum to 1.0)
# ---------------------------------------------------------------------------
PRE_AI_W_FRESHNESS   = 0.35
PRE_AI_W_RELEVANCE   = 0.35
PRE_AI_W_ENGAGEMENT  = 0.15
PRE_AI_W_CONFIDENCE  = 0.15

# ---------------------------------------------------------------------------
# Final rank weights  (post-AI; must sum to 1.0)
# ---------------------------------------------------------------------------
RANK_W_AI_SCORE     = 0.55
RANK_W_FRESHNESS    = 0.20
RANK_W_ENGAGEMENT   = 0.15
RANK_W_CONFIDENCE   = 0.10

# ---------------------------------------------------------------------------
# Selection thresholds
# ---------------------------------------------------------------------------
MIN_PRE_AI_SCORE  = float(os.getenv("PAIN_MIN_PRE_AI_SCORE",    "2.5"))   # 0–10
MAX_AI_CANDIDATES = int(os.getenv("PAIN_MAX_AI_CANDIDATES",     "200"))   # cap per run

# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

# ISO-8601 variants we might encounter
_ISO_FMTS = (
    "%Y-%m-%dT%H:%M:%S.%fZ",
    "%Y-%m-%dT%H:%M:%SZ",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%d",
)
# Strip trailing timezone offset like "+00:00" before trying bare formats
_TZ_SUFFIX = re.compile(r"[+\-]\d{2}:\d{2}$")


def normalize_source_timestamp(raw) -> Optional[datetime]:
    """
    Convert any timestamp representation to an aware UTC datetime.

    Handles:
    - int/float  → Unix seconds
    - datetime   → ensure tz-aware UTC
    - str        → various ISO-8601 variants
    Returns None on failure (never raises).
    """
    if raw is None:
        return None

    if isinstance(raw, datetime):
        if raw.tzinfo is None:
            return raw.replace(tzinfo=timezone.utc)
        return raw.astimezone(timezone.utc)

    if isinstance(raw, (int, float)):
        try:
            # Guard against millisecond timestamps mistakenly passed
            if raw > 9_999_999_999:
                raw = raw / 1000.0
            return datetime.fromtimestamp(raw, tz=timezone.utc)
        except (OSError, OverflowError, ValueError):
            return None

    if isinstance(raw, str):
        raw = raw.strip()
        # Try Python's fromisoformat first (handles +HH:MM offsets in 3.11+)
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return dt.astimezone(timezone.utc)
        except (ValueError, AttributeError):
            pass
        # Strip trailing tz offset and try bare formats
        s = _TZ_SUFFIX.sub("", raw).strip()
        for fmt in _ISO_FMTS:
            try:
                dt = datetime.strptime(s, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue

    return None


# ---------------------------------------------------------------------------
# Freshness helpers
# ---------------------------------------------------------------------------

def _age_hours(created_at: datetime) -> float:
    now = datetime.now(timezone.utc)
    delta = now - created_at
    return max(0.0, delta.total_seconds() / 3600.0)


def is_within_hard_max(created_at: Optional[datetime]) -> bool:
    """
    Returns True if the signal is young enough to be considered at all.
    Signals with no timestamp are allowed through (scored cautiously).
    """
    if created_at is None:
        return True   # unknown timestamp — pass, but get low freshness score
    return _age_hours(created_at) <= HARD_MAX_DAYS * 24


def compute_freshness_score(created_at: Optional[datetime]) -> float:
    """
    Returns freshness score on 0–10 scale.

    0–24 h  → 10
    24–72 h → 8
    3–7 d   → 5
    7–30 d  → 2
    >30 d   → 0   (should already have been rejected by is_within_hard_max)
    None    → 1.5 (no timestamp — treat cautiously but don't hard-reject)
    """
    if created_at is None:
        return 1.5

    age_h = _age_hours(created_at)

    if age_h <= 24:
        return 10.0
    if age_h <= 72:
        return 8.0
    if age_h <= 168:    # 7 days
        return 5.0
    if age_h <= 720:    # 30 days
        return 2.0
    return 0.0


# ---------------------------------------------------------------------------
# Engagement scoring  (0–10 scale)
# ---------------------------------------------------------------------------

def compute_engagement_score(
    post_score:   Optional[int],
    num_comments: Optional[int],
) -> float:
    """
    Score engagement from upvotes + comments on a 0–10 scale.
    Capped so viral posts don't dominate.
    """
    upvotes  = max(0, post_score   or 0)
    comments = max(0, num_comments or 0)

    # Upvote contribution (0–5)
    if upvotes >= 100:  uv = 5.0
    elif upvotes >= 50: uv = 4.0
    elif upvotes >= 20: uv = 3.0
    elif upvotes >= 5:  uv = 2.0
    elif upvotes >= 1:  uv = 1.0
    else:               uv = 0.0

    # Comment contribution (0–5)
    if comments >= 50:   cv = 5.0
    elif comments >= 20: cv = 4.0
    elif comments >= 10: cv = 3.0
    elif comments >= 5:  cv = 2.0
    elif comments >= 1:  cv = 1.0
    else:                cv = 0.0

    return min(10.0, uv + cv)


# ---------------------------------------------------------------------------
# Source confidence
# ---------------------------------------------------------------------------

def get_source_confidence(source: str) -> float:
    """Returns source confidence score on 0–10 scale."""
    return SOURCE_CONFIDENCE.get((source or "").lower(), SOURCE_CONFIDENCE_DEFAULT)


# ---------------------------------------------------------------------------
# Pre-AI annotation  (adds scoring fields to the signal dict in-place)
# ---------------------------------------------------------------------------

def annotate_candidate(signal: dict) -> dict:
    """
    Compute and attach all pre-AI scoring fields to a signal dict.
    Modifies and returns the same dict.

    Added fields:
        source_created_at     datetime | None  (normalized UTC)
        freshness_score       float 0–10
        engagement_score      float 0–10
        source_confidence_score float 0–10
        relevance_score       float 0–10
        pre_ai_score          float 0–10  (weighted composite)
    """
    # 1. Normalize timestamp
    raw_ts = signal.get("source_created_at") or signal.get("created_utc")
    created_at: Optional[datetime] = normalize_source_timestamp(raw_ts)
    signal["source_created_at"] = created_at

    # 2. Freshness
    freshness = compute_freshness_score(created_at)
    signal["freshness_score"] = round(freshness, 2)

    # 3. Engagement
    engagement = compute_engagement_score(
        signal.get("post_score"), signal.get("num_comments")
    )
    signal["engagement_score"] = round(engagement, 2)

    # 4. Source confidence
    confidence = get_source_confidence(signal.get("source", ""))
    signal["source_confidence_score"] = round(confidence, 2)

    # 5. Heuristic relevance  (Reddit scraper stores raw 0–13 score; others may not)
    raw_heuristic = signal.get("heuristic_score")
    if raw_heuristic is not None:
        relevance = min(10.0, float(raw_heuristic) * 10.0 / 13.0)
    else:
        relevance = 5.0   # neutral default for sources without a heuristic scorer
    signal["relevance_score"] = round(relevance, 2)

    # 6. Weighted pre-AI score
    pre_ai = (
        freshness  * PRE_AI_W_FRESHNESS  +
        relevance  * PRE_AI_W_RELEVANCE  +
        engagement * PRE_AI_W_ENGAGEMENT +
        confidence * PRE_AI_W_CONFIDENCE
    )
    signal["pre_ai_score"] = round(pre_ai, 3)

    return signal


# ---------------------------------------------------------------------------
# Selection function  (call before analyze_batch)
# ---------------------------------------------------------------------------

def select_candidates_for_ai(
    signals:        list[dict],
    max_candidates: int = MAX_AI_CANDIDATES,
) -> tuple[list[dict], list[dict]]:
    """
    Annotate, filter, and rank signal candidates before the AI call.

    Pipeline:
      1. annotate_candidate — adds freshness/engagement/pre_ai scores
      2. Hard-max age filter — rejects signals older than HARD_MAX_DAYS
      3. Min pre-AI score filter — rejects obviously weak candidates
      4. Sort by pre_ai_score descending
      5. Cap to max_candidates (budget control)

    Returns:
        to_analyze  — ordered best-first list to send to AI
        rejected    — list of discarded signals (with reject_reason attached)
    """
    annotated = []
    rejected:  list[dict] = []

    for s in signals:
        annotate_candidate(s)

        # Hard max age check (only applied when we have a concrete timestamp)
        if not is_within_hard_max(s["source_created_at"]):
            s["reject_reason"] = "too_old"
            rejected.append(s)
            continue

        # Pre-AI quality gate
        if s["pre_ai_score"] < MIN_PRE_AI_SCORE:
            s["reject_reason"] = "low_pre_ai_score"
            rejected.append(s)
            continue

        annotated.append(s)

    # Best candidates first
    annotated.sort(key=lambda x: x["pre_ai_score"], reverse=True)

    # Respect AI call budget
    to_analyze     = annotated[:max_candidates]
    over_budget    = annotated[max_candidates:]
    for s in over_budget:
        s["reject_reason"] = "over_ai_budget"
    rejected.extend(over_budget)

    return to_analyze, rejected


# ---------------------------------------------------------------------------
# Post-AI final rank score
# ---------------------------------------------------------------------------

def compute_final_rank_score(signal: dict) -> float:
    """
    Combine AI score with freshness/engagement/source-confidence into a
    single ranking float (0–10) stored in the DB as final_rank_score.

    Weights: AI 55% | freshness 20% | engagement 15% | confidence 10%
    """
    ai_score   = float(signal.get("lead_potential") or signal.get("score") or 0)
    freshness  = float(signal.get("freshness_score", 1.5))
    engagement = float(signal.get("engagement_score", 0.0))
    confidence = float(signal.get("source_confidence_score", SOURCE_CONFIDENCE_DEFAULT))

    final = (
        ai_score   * RANK_W_AI_SCORE  +
        freshness  * RANK_W_FRESHNESS +
        engagement * RANK_W_ENGAGEMENT +
        confidence * RANK_W_CONFIDENCE
    )
    return round(min(10.0, max(0.0, final)), 3)


# ---------------------------------------------------------------------------
# Pipeline logging helper
# ---------------------------------------------------------------------------

def log_selection_stats(
    raw_count:      int,
    to_analyze:     list[dict],
    rejected:       list[dict],
    qualified:      list[dict],
) -> None:
    """Log a structured summary of the selection run."""
    by_reason: dict[str, int] = {}
    oldest_accepted = newest_accepted = None

    for s in rejected:
        r = s.get("reject_reason", "unknown")
        by_reason[r] = by_reason.get(r, 0) + 1

    for s in to_analyze:
        ts = s.get("source_created_at")
        if isinstance(ts, datetime):
            if oldest_accepted is None or ts < oldest_accepted:
                oldest_accepted = ts
            if newest_accepted is None or ts > newest_accepted:
                newest_accepted = ts

    top5_scores = sorted(
        (s.get("final_rank_score", 0) for s in qualified),
        reverse=True,
    )[:5]

    logger.info(
        "Signal selection | raw=%d → candidates=%d → AI_qualified=%d | "
        "rejected: %s | oldest_accepted=%s | newest_accepted=%s | "
        "top5_final_scores=%s",
        raw_count,
        len(to_analyze),
        len(qualified),
        dict(by_reason),
        oldest_accepted.strftime("%Y-%m-%d") if oldest_accepted else "n/a",
        newest_accepted.strftime("%Y-%m-%d") if newest_accepted else "n/a",
        [round(x, 2) for x in top5_scores],
    )
