"""
Pain signal analyzer — qualification layer for workflow automation prospecting.

Receives raw signal candidates from Reddit, forum, and review scrapers.
Applies:
  1. Rule-based pre-filter  — rejects obvious garbage before burning tokens.
  2. AI analysis            — multi-dimension qualification via GPT.
  3. Output validation      — normalises, coerces, and defaults all fields.
  4. Concurrent batching    — semaphore-bounded asyncio.gather for speed.

Backward-compatible output keys (used by orchestrator + DB models):
  lead_potential, industry, problem_desc, automation_opp, reasoning, contact_worthy

Enriched output keys (additive, stored or used downstream):
  score, buyer_role_hint, pain_type, pain_severity,
  business_relevance, automation_fit, actionability,
  should_keep, model_used
"""
import asyncio
import json
import logging
import os
import re
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
client          = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL           = os.environ.get("AI_MODEL", "gpt-4o-mini")
SCORE_THRESHOLD = int(os.environ.get("PAIN_SCORE_THRESHOLD", "6"))   # persist when score >= this
AI_CONCURRENCY  = int(os.environ.get("PAIN_AI_CONCURRENCY",  "5"))   # parallel OpenAI calls

# ---------------------------------------------------------------------------
# Taxonomy helpers
# ---------------------------------------------------------------------------
VALID_PAIN_TYPES = {
    "lead_management", "follow_up", "scheduling", "onboarding",
    "reporting", "data_entry", "spreadsheet_ops", "customer_support",
    "internal_handoffs", "document_workflow", "billing_admin",
    "general_ops", "other",
}

VALID_SEVERITY_LEVELS = {"low", "medium", "high"}

# Maps pain_type → most likely target contact when none is obvious from the post
_BUYER_ROLE_DEFAULTS: dict[str, str] = {
    "lead_management":    "founder / sales manager",
    "follow_up":          "founder / ops lead",
    "scheduling":         "operations manager / office manager",
    "onboarding":         "operations manager / HR lead",
    "reporting":          "ops manager / founder",
    "data_entry":         "operations manager / founder",
    "spreadsheet_ops":    "founder / ops lead",
    "customer_support":   "support manager / ops lead",
    "internal_handoffs":  "ops manager / department head",
    "document_workflow":  "admin manager / founder",
    "billing_admin":      "finance manager / practice admin",
    "general_ops":        "founder / operations manager",
    "other":              "founder / business owner",
}


def _default_buyer_role(pain_type: str) -> str:
    return _BUYER_ROLE_DEFAULTS.get(pain_type, "founder / business owner")


# ---------------------------------------------------------------------------
# Rule-based pre-filter  (runs before AI call — zero token cost)
# ---------------------------------------------------------------------------

# Patterns that immediately disqualify a post (case-insensitive substring match)
_HARD_REJECT_PATTERNS: list[tuple[str, str]] = [
    # Job seeking / career
    ("job_seeking",  "looking for a job"),
    ("job_seeking",  "my resume"),
    ("job_seeking",  "got laid off"),
    ("job_seeking",  "job hunting"),
    ("job_seeking",  "applying for jobs"),
    # Hiring posts (from employers, not pain signals)
    ("hiring",       "we are hiring"),
    ("hiring",       "we're hiring"),
    ("hiring",       "job opening"),
    ("hiring",       "join our team"),
    # Builder / launch posts (creator, not buyer)
    ("builder",      "i built"),
    ("builder",      "i made"),
    ("builder",      "i created"),
    ("builder",      "i automated"),
    ("builder",      "i just launched"),
    ("builder",      "i wrote a"),
    ("builder",      "my tool"),
    ("builder",      "my app"),
    ("builder",      "my saas"),
    ("builder",      "how i automated"),
    ("builder",      "how i built"),
    # Product announcements
    ("promo",        "product hunt"),
    ("promo",        "show hn"),
    ("promo",        "announcing"),
    ("promo",        "we just launched"),
    # Academic
    ("student",      "for my class"),
    ("student",      "my homework"),
    ("student",      "my assignment"),
    ("student",      "for school"),
    # Success stories (not pain)
    ("success",      "we saved"),
    ("success",      "i saved"),
    ("success",      "case study"),
    ("success",      "success story"),
    # Personal / unrelated
    ("personal",     "my relationship"),
    ("personal",     "my girlfriend"),
    ("personal",     "my boyfriend"),
    ("personal",     "divorce"),
    ("personal",     "breakup"),
]

# Positive signal hints — strengthen prompt context when found
_STRONG_POSITIVE_HINTS: list[str] = [
    "team", "clients", "staff", "employees", "manual", "spreadsheet",
    "process", "workflow", "follow-up", "data entry", "every week",
    "every day", "takes hours", "looking for software", "any tool",
    "what do you use", "recommend", "automate", "integration",
]


def _pre_filter(signal: dict) -> tuple[bool, str]:
    """
    Rule-based pre-filter run before the AI call.

    Returns:
        (reject, context_hint)
        reject=True  → skip AI, return low-score fallback immediately
        context_hint → optional string appended to the prompt for borderline cases
    """
    title     = (signal.get("title") or "").lower()
    body      = (signal.get("body") or signal.get("content") or "").lower()
    full_text = f"{title} {body}"

    for category, pattern in _HARD_REJECT_PATTERNS:
        if pattern in full_text:
            logger.debug("Pre-filter REJECT [%s]: matched '%s'", category, pattern)
            return True, ""

    # Very short content is unlikely to be actionable
    if len(full_text.strip()) < 50:
        return True, ""

    # Collect positive hints to give the AI useful context
    found_hints = [h for h in _STRONG_POSITIVE_HINTS if h in full_text]
    hint_str = ""
    if found_hints:
        hint_str = f"[Pre-filter hints: {', '.join(found_hints[:6])}]"

    return False, hint_str


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a B2B signal qualification specialist for a small workflow automation agency.

Your job: decide whether a Reddit post is a genuine BUSINESS WORKFLOW PAIN SIGNAL
worth persisting for manual outreach. Most posts are NOT worth keeping — be strict.

=== INPUT ===

SUBREDDIT: r/{subreddit}
ENGAGEMENT: {post_score} upvotes, {num_comments} comments
HEURISTIC KEYWORDS: {keywords}
{pre_filter_hint}
TITLE:
{title}

POST BODY:
{body}

TOP COMMENTS:
{comments}

=== EVALUATION FRAMEWORK ===

Evaluate across these four dimensions:

1. BUSINESS RELEVANCE
   Is this about a real business operation, workflow, team, or client management?
   NOT: personal problems, student projects, pure technical debugging, job seeking.

2. PAIN SEVERITY
   How serious, frequent, and costly is the pain?
   low  = mild annoyance, rare occurrence
   medium = recurring inconvenience, occasional real cost
   high = serious bottleneck, frequent, affects team or revenue

3. AUTOMATION FIT
   Is there a realistic workflow automation opportunity?
   Could this be solved with: CRM, process automation, integrations, scheduling,
   onboarding tools, reporting dashboards, lead routing, spreadsheet replacement?
   NOT: vague frustration with no concrete process, pure human-relations issues.

4. ACTIONABILITY
   Does this suggest a real decision-maker at an SMB with a solvable operational problem?
   Can a small automation agency realistically help this person?

=== SCORING RUBRIC ===

1–3  REJECT: Not business-relevant, purely personal, no automation angle,
     or the post is from someone BUILDING tools (not needing them).
4–5  WEAK: Some business context but low confidence; mild or vague pain.
6    MODERATE: Clear business pain, specific process, but not highly compelling.
7–8  STRONG: Clear SMB workflow pain, specific recurring process, obvious solution path.
9–10 EXCELLENT: Concrete recurring operational pain, likely decision-maker,
     specific costs or urgency mentioned, highly actionable.

=== RULES ===
- Do NOT invent company details not in the post.
- Infer industry only from concrete evidence; use "general business" if unclear.
- A developer BUILDING tools scores 1–2 — they are not a buyer.
- Comments can reveal context but should not rescue a clearly weak post.
- Upvotes and comment count are supporting evidence, not primary.

=== OUTPUT ===

Respond ONLY with valid JSON, no markdown fences, no extra keys:
{{
  "score": <number 1-10>,
  "industry": "<specific industry or 'general business'>",
  "problem_desc": "<1-2 sentences, concrete, in plain business language>",
  "automation_opp": "<specific tool/workflow opportunity, or null>",
  "reasoning": "<one concise sentence explaining the score>",
  "buyer_role_hint": "<founder | ops manager | practice manager | clinic admin | etc.>",
  "pain_type": "<lead_management|follow_up|scheduling|onboarding|reporting|data_entry|spreadsheet_ops|customer_support|internal_handoffs|document_workflow|billing_admin|general_ops|other>",
  "pain_severity": "<low|medium|high>",
  "business_relevance": "<low|medium|high>",
  "automation_fit": "<low|medium|high>",
  "actionability": "<low|medium|high>",
  "should_keep": <true if score >= 6, else false>
}}
"""

# ---------------------------------------------------------------------------
# JSON parsing helpers
# ---------------------------------------------------------------------------

_JSON_BLOCK    = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def _extract_json(raw: str) -> str:
    raw = raw.strip()
    m   = _JSON_BLOCK.search(raw)
    if m:
        return m.group(1).strip()
    if raw.startswith("{"):
        return raw
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        return raw[start : end + 1]
    return raw


def _coerce_level(val: object, default: str = "medium") -> str:
    s = str(val).lower().strip() if val else default
    return s if s in VALID_SEVERITY_LEVELS else default


def _coerce_pain_type(val: object) -> str:
    s = str(val).lower().strip() if val else "other"
    return s if s in VALID_PAIN_TYPES else "other"


def _validate_output(raw: dict, signal: dict) -> dict:
    """
    Validate, coerce, and normalise the model's raw JSON output.
    Never raises — missing or invalid fields fall back to safe defaults.
    """
    # Score: float, clamped to [1, 10]
    try:
        score = float(raw.get("score", 3))
        score = max(1.0, min(10.0, score))
    except (TypeError, ValueError):
        score = 3.0

    pain_type  = _coerce_pain_type(raw.get("pain_type"))
    should_keep = bool(raw.get("should_keep", score >= SCORE_THRESHOLD))
    # Enforce rule: should_keep must be False when score < threshold
    if score < SCORE_THRESHOLD:
        should_keep = False

    buyer_role = str(raw.get("buyer_role_hint") or "").strip()
    if not buyer_role or buyer_role.lower() in ("unknown", "n/a", ""):
        buyer_role = _default_buyer_role(pain_type)

    def _s(key: str, max_len: int = 500) -> Optional[str]:
        v = raw.get(key)
        if not v:
            return None
        s = _CONTROL_CHARS.sub("", str(v)).strip()
        return (s[: max_len - 1].rstrip() + "…") if len(s) > max_len else s or None

    return {
        # --- backward-compatible fields (orchestrator + DB) ---
        "lead_potential":    int(round(score)),
        "industry":          _s("industry", 150)  or "general business",
        "problem_desc":      _s("problem_desc", 400),
        "automation_opp":    _s("automation_opp", 400),
        "reasoning":         _s("reasoning", 300),
        "contact_worthy":    should_keep,           # legacy alias
        # --- enriched fields ---
        "score":             score,
        "buyer_role_hint":   buyer_role[:200],
        "pain_type":         pain_type,
        "pain_severity":     _coerce_level(raw.get("pain_severity")),
        "business_relevance":_coerce_level(raw.get("business_relevance")),
        "automation_fit":    _coerce_level(raw.get("automation_fit")),
        "actionability":     _coerce_level(raw.get("actionability")),
        "should_keep":       should_keep,
        "model_used":        MODEL,
    }


def _fallback_result(reason: str = "pre_filter") -> dict:
    """Return a safe low-score result when analysis is skipped or fails."""
    return {
        "lead_potential":    1,
        "industry":          None,
        "problem_desc":      None,
        "automation_opp":    None,
        "reasoning":         f"Skipped: {reason}",
        "contact_worthy":    False,
        "score":             1.0,
        "buyer_role_hint":   "unknown",
        "pain_type":         "other",
        "pain_severity":     "low",
        "business_relevance":"low",
        "automation_fit":    "low",
        "actionability":     "low",
        "should_keep":       False,
        "model_used":        MODEL,
    }


# ---------------------------------------------------------------------------
# Context builder — normalises scraper fields into structured prompt sections
# ---------------------------------------------------------------------------

def _build_context(signal: dict) -> tuple[str, str, str, str, str]:
    """
    Extract (title, body, comments, keywords_str, pre_filter_hint) from a signal dict.

    Handles both:
    - New scraper format: title / body / top_comments_text as separate keys
    - Old / other-source format: single 'content' key
    """
    title    = (signal.get("title") or "").strip()
    body     = (signal.get("body")  or "").strip()
    comments = (signal.get("top_comments_text") or "").strip()

    # Fall back: parse structured content field if separate keys aren't present
    if not title and not body:
        content = signal.get("content", "")
        # The scraper formats content as "TITLE: …\n\nPOST:\n…\n\nTOP COMMENTS:\n…"
        if "TITLE:" in content:
            parts = content.split("\n\n", 2)
            for part in parts:
                if part.startswith("TITLE:"):
                    title = part.replace("TITLE:", "").strip()
                elif part.startswith("POST:"):
                    body = part.replace("POST:", "").strip()
                elif part.startswith("TOP COMMENTS:"):
                    comments = part.replace("TOP COMMENTS:", "").strip()
        else:
            body = content  # last resort: treat entire content as body

    kw_list     = signal.get("keywords_matched") or []
    keywords_str = ", ".join(kw_list[:10]) if kw_list else "none"

    _reject, hint = _pre_filter(signal)   # hint already computed; we reuse it
    return (
        title[:300],
        body[:1200],
        comments[:600],
        keywords_str,
        hint,
    )


# ---------------------------------------------------------------------------
# Core analysis function
# ---------------------------------------------------------------------------

async def analyze_pain_signal(signal: dict) -> Optional[dict]:
    """
    Analyze a single pain signal candidate.

    1. Run rule-based pre-filter; return low-score fallback if rejected.
    2. Build structured prompt context.
    3. Call GPT.
    4. Parse, validate, and return normalized result dict.

    Returns None only on unrecoverable exceptions.
    """
    # Step 1: pre-filter
    reject, pre_filter_hint = _pre_filter(signal)
    if reject:
        logger.debug(
            "Pre-filter rejected signal from %s: %s",
            signal.get("source", "?"),
            (signal.get("title") or signal.get("content", ""))[:60],
        )
        return _fallback_result("pre_filter")

    # Step 2: build context
    title, body, comments, keywords_str, _ = _build_context(signal)

    prompt = _PROMPT_TEMPLATE.format(
        subreddit        = signal.get("subreddit", "unknown"),
        post_score       = signal.get("post_score", "?"),
        num_comments     = signal.get("num_comments", "?"),
        keywords         = keywords_str,
        pre_filter_hint  = pre_filter_hint,
        title            = title or "(no title)",
        body             = body  or "(no body)",
        comments         = comments or "(none)",
    )

    # Step 3: call AI
    raw_text = ""
    try:
        response = await client.chat.completions.create(
            model     = MODEL,
            messages  = [{"role": "user", "content": prompt}],
            temperature = 0.1,
            max_tokens  = 500,
        )
        raw_text = response.choices[0].message.content or ""

    except Exception as exc:
        logger.error(
            "OpenAI call failed for signal [%s]: %s",
            signal.get("source_url", "?")[:60], exc,
        )
        return _fallback_result("api_error")

    # Step 4: parse + validate
    try:
        json_str = _extract_json(raw_text)
        raw_dict = json.loads(json_str)
        if not isinstance(raw_dict, dict):
            raise ValueError(f"Expected dict, got {type(raw_dict)}")
        return _validate_output(raw_dict, signal)

    except (json.JSONDecodeError, ValueError) as exc:
        logger.error(
            "JSON parse error for signal [%s]: %s | raw=%r",
            signal.get("source_url", "?")[:60], exc, raw_text[:200],
        )
        return _fallback_result("parse_error")


# ---------------------------------------------------------------------------
# Batch analysis with bounded concurrency
# ---------------------------------------------------------------------------

async def analyze_batch(signals: list[dict]) -> list[dict]:
    """
    Analyze a batch of pain signal candidates concurrently.

    Returns only the signals that qualified (score >= SCORE_THRESHOLD).
    Mutates each signal dict in-place with the analysis result fields.
    """
    if not signals:
        return []

    sem = asyncio.Semaphore(AI_CONCURRENCY)

    async def _analyze_one(signal: dict) -> tuple[dict, Optional[dict]]:
        async with sem:
            result = await analyze_pain_signal(signal)
        return signal, result

    pairs = await asyncio.gather(
        *[_analyze_one(s) for s in signals],
        return_exceptions=True,
    )

    qualified: list[dict] = []

    for item in pairs:
        if isinstance(item, Exception):
            logger.warning("analyze_one task raised: %s", item)
            continue

        signal, result = item

        if result is None:
            signal["qualified"] = False
            continue

        if result.get("lead_potential", 0) >= SCORE_THRESHOLD:
            signal.update(result)
            signal["qualified"] = True
            qualified.append(signal)
        else:
            signal.update(result)
            signal["qualified"] = False

    logger.info(
        "Pain signal analysis: %d/%d qualified (score >= %d) | model=%s",
        len(qualified), len(signals), SCORE_THRESHOLD, MODEL,
    )
    return qualified
