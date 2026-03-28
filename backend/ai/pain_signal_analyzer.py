"""
Pain signal analyzer — qualification + lead-type classification layer.

Pipeline per signal:
  1. Rule-based pre-filter  — hard-rejects garbage/sellers/builders (zero token cost)
  2. AI analysis            — pain qualification + lead-type classification
  3. Output validation      — normalises, coerces, enforces business rules
  4. Concurrent batching    — semaphore-bounded asyncio.gather

Backward-compatible output keys (orchestrator + DB):
  lead_potential, industry, problem_desc, automation_opp, reasoning, contact_worthy

Enriched keys (additive):
  score, buyer_role_hint, pain_type, pain_severity,
  business_relevance, automation_fit, actionability, should_keep, model_used

Lead-qualification keys (new):
  lead_type           — "direct" | "indirect" | "non_lead"
  buyer_intent_score  — float 1–10
  outreach_priority   — "high" | "medium" | "low" | "none"
  is_outreach_ready   — bool (enforced: direct + score>=6 + intent>=6)
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
SCORE_THRESHOLD = int(os.environ.get("PAIN_SCORE_THRESHOLD",       "6"))
AI_CONCURRENCY  = int(os.environ.get("PAIN_AI_CONCURRENCY",        "5"))
INTENT_THRESHOLD= int(os.environ.get("PAIN_INTENT_THRESHOLD",      "6"))   # for is_outreach_ready

# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------
VALID_PAIN_TYPES = {
    "lead_management", "follow_up", "scheduling", "onboarding",
    "reporting", "data_entry", "spreadsheet_ops", "customer_support",
    "internal_handoffs", "document_workflow", "billing_admin",
    "general_ops", "other",
}
VALID_SEVERITY_LEVELS  = {"low", "medium", "high"}
VALID_LEAD_TYPES       = {"direct", "indirect", "non_lead"}
VALID_OUTREACH_PRIORITIES = {"high", "medium", "low", "none"}

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
# Rule-based pre-filter
# ---------------------------------------------------------------------------

# Absolute hard rejects — checked against full title+body text.
# Keep this list tight: only patterns where NO pain context can redeem them.
_HARD_REJECT_PATTERNS: list[tuple[str, str]] = [
    # Job seeking
    ("job_seeking",  "looking for a job"),
    ("job_seeking",  "my resume"),
    ("job_seeking",  "got laid off"),
    ("job_seeking",  "job hunting"),
    ("job_seeking",  "applying for jobs"),
    # Hiring
    ("hiring",       "we are hiring"),
    ("hiring",       "we're hiring"),
    ("hiring",       "job opening"),
    ("hiring",       "join our team"),
    # Selling / agency flex — these are sellers, not buyers
    ("selling",      "i helped a client"),
    ("selling",      "i helped my client"),
    ("selling",      "we helped our client"),
    ("selling",      "for a client of mine"),
    ("selling",      "our clients love"),
    ("selling",      "we charge"),
    ("selling",      "book a call"),
    ("selling",      "dm me for"),
    # Academic
    ("student",      "for my class"),
    ("student",      "my homework"),
    ("student",      "my assignment"),
    ("student",      "for school"),
    # Personal / unrelated
    ("personal",     "my relationship"),
    ("personal",     "my girlfriend"),
    ("personal",     "my boyfriend"),
    ("personal",     "divorce"),
    ("personal",     "breakup"),
]

# Patterns checked against TITLE ONLY.
# If found in the title AND the full text has no pain context, reject.
# If pain context exists → keep and hint AI that this is an indirect signal.
_TITLE_LAUNCH_PATTERNS: list[str] = [
    "i launched", "we launched", "just launched",
    "introducing",
    "just shipped", "i just shipped", "we just shipped",
    "show ih:", "show hn:",
    "v2 is live", "v2 launch",
]

_TITLE_BUILDER_PATTERNS: list[str] = [
    "i built", "i made", "i created", "i automated",
    "how i automated", "how i built",
    "my tool", "my app", "my saas",
]

# If ANY of these appear in full text, a launch/builder title is kept
# as an INDIRECT signal rather than rejected.
_PAIN_EXCEPTION_PHRASES: list[str] = [
    "because", "struggled", "problem", "couldn't find",
    "manual", "took too long", "wasting", "needed",
    "missing", "broken", "frustrating", "chaos",
    "hours", "spreadsheet", "follow up", "clients",
    "process", "admin", "workflow", "repetitive",
]

_STRONG_POSITIVE_HINTS: list[str] = [
    "team", "clients", "staff", "employees", "manual", "spreadsheet",
    "process", "workflow", "follow-up", "data entry", "every week",
    "every day", "takes hours", "looking for software", "any tool",
    "what do you use", "recommend", "automate", "integration",
]


def _pre_filter(signal: dict) -> tuple[bool, str]:
    """
    Returns (reject, hint_str).
    reject=True  → skip AI entirely, return fallback with lead_type="non_lead".
    hint_str     → appended to prompt for context on borderline signals.

    Strategy:
    - Absolute hard rejects (full text): job seeking, hiring, selling — no exception.
    - Launch/builder patterns: checked on TITLE only.
        - If title matches AND body has pain context → keep as INDIRECT hint.
        - If title matches AND no pain context → reject.
    - Builder phrases in BODY only (not title) → not rejected; AI decides.
    """
    title     = (signal.get("title") or "").lower()
    body      = (signal.get("body") or signal.get("content") or "").lower()
    full_text = f"{title} {body}"

    # Absolute rejects
    for category, pattern in _HARD_REJECT_PATTERNS:
        if pattern in full_text:
            logger.debug("Pre-filter REJECT [%s]: '%s'", category, pattern)
            return True, ""

    if len(full_text.strip()) < 50:
        return True, ""

    # Title-based launch / builder filter with pain exception
    is_launch_title  = any(p in title for p in _TITLE_LAUNCH_PATTERNS)
    is_builder_title = any(p in title for p in _TITLE_BUILDER_PATTERNS)

    if is_launch_title or is_builder_title:
        has_pain = any(p in full_text for p in _PAIN_EXCEPTION_PHRASES)
        if not has_pain:
            logger.debug(
                "Pre-filter REJECT [launch/builder, no pain context]: %r", title[:70]
            )
            return True, ""
        # Pain context present — keep but tell AI this is likely indirect
        found = [h for h in _STRONG_POSITIVE_HINTS if h in full_text]
        hint  = (
            "[Indirect signal: launch/builder title but pain context present. "
            f"Pain hints: {', '.join(found[:6]) or 'see body'}]"
        )
        return False, hint

    found = [h for h in _STRONG_POSITIVE_HINTS if h in full_text]
    hint  = f"[Pre-filter hints: {', '.join(found[:6])}]" if found else ""
    return False, hint


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

_PROMPT_TEMPLATE = """\
You are a B2B lead qualification specialist for a small workflow automation agency.

Your job: determine whether this post represents a VIABLE OUTREACH OPPORTUNITY.
Most posts from founder communities describe real operational problems — lean toward
finding the signal rather than dismissing it.

=== INPUT ===

SOURCE: {subreddit}
ENGAGEMENT: {post_score} upvotes, {num_comments} comments
HEURISTIC KEYWORDS: {keywords}
{pre_filter_hint}
TITLE:
{title}

POST BODY:
{body}

TOP COMMENTS:
{comments}

=== STEP 1 — LEAD TYPE (classify first, before scoring) ===

Classify into EXACTLY ONE of:

DIRECT:
The author is CURRENTLY experiencing an ONGOING business workflow problem they
have NOT fully solved. They are actively living with the pain, asking for help,
or seeking a better solution right now.
  Strong signals: present-tense frustration, asking for tool recommendations,
  describing a broken current process, saying "I need", "we struggle with",
  "still doing manually", "keeps falling through the cracks"
  Examples: "I'm drowning in invoices", "we keep missing follow-ups",
  "our onboarding is completely manual and it's killing us",
  "I need a better system for tracking clients"

INDIRECT:
The author personally experienced a workflow pain and built or found a solution —
OR describes a problem in past tense. The pain was real but is now addressed.
  These are still VALUABLE: the author validated a real market problem, may have
  an incomplete solution, and is likely open to discussing better tooling.
  Examples: "I built X because our invoicing was a nightmare",
  "we used to spend hours on spreadsheets until we found Y",
  "I automated our follow-up process because we kept losing leads"

NON_LEAD:
No actionable outreach opportunity. Use this ONLY for:
  - Selling a product/service to others (they are the vendor, not the buyer)
  - Flexing achievements with no workflow problem mentioned
  - Pure general advice/commentary with zero personal operational pain
  - Hiring posts or job seeking
  - Personal/lifestyle content unrelated to business operations
  Examples: "I saved a client 10 hours/week" (selling),
  "here's my framework for success" (advice, no pain),
  "book a call with me" (promo)

CRITICAL DISTINCTIONS:
- "I built X because [pain]" = INDIRECT (they had real pain; built their own fix)
- "I built X for my clients" or "I help businesses with X" = NON_LEAD (seller)
- Active, unresolved, personal pain = DIRECT
- Past pain that author resolved themselves = INDIRECT
- Pure promotion / selling to others = NON_LEAD
- When uncertain between DIRECT and INDIRECT → choose DIRECT if current pain language exists

=== STEP 2 — PAIN EVALUATION ===

For DIRECT and INDIRECT leads, evaluate:

BUSINESS RELEVANCE: Is this a real business workflow problem (not a personal hobby)?
PAIN SEVERITY: low = rare annoyance | medium = recurring weekly burden | high = ongoing bottleneck costing time/money
AUTOMATION FIT: Can workflow automation / integrations / tooling realistically solve this?
ACTIONABILITY: Is there a real SMB operator or decision-maker with a concrete, solvable problem?

Strong pain indicators (boost score when present):
  manual, repetitive, time-consuming, spreadsheet, follow-up, clients, admin,
  workflow, inefficiency, every week, takes hours, falling through cracks,
  no system, cobbled together, keeping track, losing leads

=== STEP 3 — SCORING (1–10) ===

DIRECT leads:
  1–3  Not business-relevant, personal, or no automation angle.
  4–5  Weak: vague or low-confidence business context.
  6    Moderate: real business pain, specific, borderline actionable.
  7–8  Strong: clear SMB workflow pain, recurring process problem, good fit.
  9–10 Excellent: concrete ongoing pain, decision-maker, specific costs or urgency.

INDIRECT leads:
  1–3  Pain was minor or the solution completely addresses it.
  4–5  Real pain was present; solution exists but may be incomplete or scalable.
  6–7  Strong indirect: significant operational pain, built own workaround, likely open to better tooling.

NON_LEAD → score 1–3 only.

=== STEP 4 — BUYER INTENT SCORE (1–10) ===

How likely is this person to want and accept a workflow automation solution RIGHT NOW?

8–10 HIGH: Actively struggling, frustrated, explicitly asking for alternatives.
5–7  MEDIUM: Problem exists or existed, exploring options, open to conversation.
1–4  LOW: Fully solved, seller, not a decision-maker, or topic irrelevant.

=== RULES ===
- Do NOT invent company details not stated in the post.
- Infer industry only from concrete evidence; use "general business" if unclear.
- A developer selling tools to others = NON_LEAD. A founder who built a tool for their OWN problem = INDIRECT.
- Comments provide useful context — factor them in.

=== OUTPUT ===

Respond ONLY with valid JSON, no markdown fences, no extra keys:
{{
  "score": <number 1-10>,
  "lead_type": "<direct|indirect|non_lead>",
  "buyer_intent_score": <number 1-10>,
  "industry": "<specific industry or 'general business'>",
  "problem_desc": "<1-2 sentences, concrete, in business language, or null>",
  "automation_opp": "<specific opportunity, or null>",
  "reasoning": "<one sentence explaining the score and lead type>",
  "buyer_role_hint": "<founder|ops manager|practice manager|etc.>",
  "pain_type": "<lead_management|follow_up|scheduling|onboarding|reporting|data_entry|spreadsheet_ops|customer_support|internal_handoffs|document_workflow|billing_admin|general_ops|other>",
  "pain_severity": "<low|medium|high>",
  "business_relevance": "<low|medium|high>",
  "automation_fit": "<low|medium|high>",
  "actionability": "<low|medium|high>"
}}
"""

# ---------------------------------------------------------------------------
# JSON helpers
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


def _coerce_lead_type(val: object) -> str:
    s = str(val).lower().strip() if val else "non_lead"
    # Accept minor variants
    if s in ("non-lead", "nonlead", "none"):
        return "non_lead"
    return s if s in VALID_LEAD_TYPES else "non_lead"


def _derive_outreach_priority(
    lead_type: str,
    score: float,
    intent: float,
) -> str:
    """
    Compute outreach_priority from validated values.
    Enforced in code so it is always consistent with lead_type and scores.

    Direct leads:   high/medium/low based on score+intent
    Indirect leads: medium/low only (they had real pain; worth a gentle reach-out)
    Non_lead:       none always
    """
    if lead_type == "non_lead":
        return "none"
    if lead_type == "direct":
        if score >= 8 and intent >= 7:
            return "high"
        if score >= 6 and intent >= 5:
            return "medium"
        if score >= 4:
            return "low"
        return "none"
    if lead_type == "indirect":
        if score >= 6 and intent >= 5:
            return "medium"
        if score >= 4:
            return "low"
        return "none"
    return "none"


def _validate_output(raw: dict, signal: dict) -> dict:
    """
    Validate, coerce, and normalise model output.
    Never raises. Missing/invalid fields fall back to safe defaults.
    Business rules (is_outreach_ready, outreach_priority) are enforced here,
    not trusted from the model.
    """
    # Score
    try:
        score = float(raw.get("score", 3))
        score = max(1.0, min(10.0, score))
    except (TypeError, ValueError):
        score = 3.0

    # Lead type
    lead_type = _coerce_lead_type(raw.get("lead_type"))

    # Buyer intent
    try:
        intent = float(raw.get("buyer_intent_score", 3))
        intent = max(1.0, min(10.0, intent))
    except (TypeError, ValueError):
        intent = 3.0

    # Enforce score ceilings per lead type
    if lead_type == "non_lead" and score > 4:
        score = min(score, 4.0)
    if lead_type == "indirect" and score > 7:
        # Indirect leads can score up to 7 — they had real pain even if solved
        score = min(score, 7.0)

    pain_type  = _coerce_pain_type(raw.get("pain_type"))
    should_keep = score >= SCORE_THRESHOLD and lead_type != "non_lead"

    buyer_role = str(raw.get("buyer_role_hint") or "").strip()
    if not buyer_role or buyer_role.lower() in ("unknown", "n/a", ""):
        buyer_role = _default_buyer_role(pain_type)

    # Derived / enforced fields
    outreach_priority = _derive_outreach_priority(lead_type, score, intent)
    # Direct leads: full threshold required.
    # Indirect leads: slightly relaxed — they validated real pain; score>=6 + intent>=5.
    is_outreach_ready = (
        lead_type == "direct"
        and score  >= SCORE_THRESHOLD
        and intent >= INTENT_THRESHOLD
    ) or (
        lead_type == "indirect"
        and score  >= SCORE_THRESHOLD
        and intent >= 5
    )

    def _s(key: str, max_len: int = 500) -> Optional[str]:
        v = raw.get(key)
        if not v:
            return None
        s = _CONTROL_CHARS.sub("", str(v)).strip()
        return (s[: max_len - 1].rstrip() + "…") if len(s) > max_len else s or None

    return {
        # --- backward-compatible ---
        "lead_potential":    int(round(score)),
        "industry":          _s("industry", 150) or "general business",
        "problem_desc":      _s("problem_desc", 400),
        "automation_opp":    _s("automation_opp", 400),
        "reasoning":         _s("reasoning", 300),
        "contact_worthy":    is_outreach_ready,     # legacy alias
        # --- enriched (existing) ---
        "score":             score,
        "buyer_role_hint":   buyer_role[:200],
        "pain_type":         pain_type,
        "pain_severity":     _coerce_level(raw.get("pain_severity")),
        "business_relevance":_coerce_level(raw.get("business_relevance")),
        "automation_fit":    _coerce_level(raw.get("automation_fit")),
        "actionability":     _coerce_level(raw.get("actionability")),
        "should_keep":       should_keep,
        "model_used":        MODEL,
        # --- lead qualification (new) ---
        "lead_type":         lead_type,
        "buyer_intent_score":intent,
        "outreach_priority": outreach_priority,
        "is_outreach_ready": is_outreach_ready,
    }


def _fallback_result(reason: str = "pre_filter") -> dict:
    """Safe low-score result for pre-filter rejections and AI failures."""
    return {
        # backward-compatible
        "lead_potential":    1,
        "industry":          None,
        "problem_desc":      None,
        "automation_opp":    None,
        "reasoning":         f"Skipped: {reason}",
        "contact_worthy":    False,
        # enriched
        "score":             1.0,
        "buyer_role_hint":   "unknown",
        "pain_type":         "other",
        "pain_severity":     "low",
        "business_relevance":"low",
        "automation_fit":    "low",
        "actionability":     "low",
        "should_keep":       False,
        "model_used":        MODEL,
        # lead qualification
        "lead_type":         "non_lead",
        "buyer_intent_score":1.0,
        "outreach_priority": "none",
        "is_outreach_ready": False,
    }


# ---------------------------------------------------------------------------
# Context builder
# ---------------------------------------------------------------------------

def _build_context(signal: dict) -> tuple[str, str, str, str, str]:
    title    = (signal.get("title") or "").strip()
    body     = (signal.get("body")  or "").strip()
    comments = (signal.get("top_comments_text") or "").strip()

    if not title and not body:
        content = signal.get("content", "")
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
            body = content

    kw_list      = signal.get("keywords_matched") or []
    keywords_str = ", ".join(kw_list[:10]) if kw_list else "none"
    _reject, hint = _pre_filter(signal)
    return (title[:300], body[:1200], comments[:600], keywords_str, hint)


# ---------------------------------------------------------------------------
# Core analysis function
# ---------------------------------------------------------------------------

async def analyze_pain_signal(signal: dict) -> Optional[dict]:
    """
    Analyze one candidate signal.
    Returns a validated result dict, or _fallback_result on pre-filter / failure.
    Never returns None.
    """
    reject, pre_filter_hint = _pre_filter(signal)
    if reject:
        logger.debug(
            "Pre-filter rejected [%s]: %s",
            signal.get("source", "?"),
            (signal.get("title") or signal.get("content", ""))[:60],
        )
        return _fallback_result("pre_filter")

    title, body, comments, keywords_str, _ = _build_context(signal)

    prompt = _PROMPT_TEMPLATE.format(
        subreddit       = signal.get("subreddit", "unknown"),
        post_score      = signal.get("post_score", "?"),
        num_comments    = signal.get("num_comments", "?"),
        keywords        = keywords_str,
        pre_filter_hint = pre_filter_hint,
        title           = title    or "(no title)",
        body            = body     or "(no body)",
        comments        = comments or "(none)",
    )

    raw_text = ""
    try:
        response = await client.chat.completions.create(
            model       = MODEL,
            messages    = [{"role": "user", "content": prompt}],
            temperature = 0.1,
            max_tokens  = 650,      # increased from 500 to fit new fields
        )
        raw_text = response.choices[0].message.content or ""
    except Exception as exc:
        logger.error(
            "OpenAI call failed [%s]: %s",
            signal.get("source_url", "?")[:60], exc,
        )
        return _fallback_result("api_error")

    try:
        json_str = _extract_json(raw_text)
        raw_dict = json.loads(json_str)
        if not isinstance(raw_dict, dict):
            raise ValueError(f"Expected dict, got {type(raw_dict)}")
        return _validate_output(raw_dict, signal)
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error(
            "JSON parse error [%s]: %s | raw=%r",
            signal.get("source_url", "?")[:60], exc, raw_text[:200],
        )
        return _fallback_result("parse_error")


# ---------------------------------------------------------------------------
# Batch analysis with bounded concurrency
# ---------------------------------------------------------------------------

async def analyze_batch(signals: list[dict]) -> list[dict]:
    """
    Analyze a batch of candidates concurrently.

    Returns signals that passed the threshold AND are not non_lead.
    Both "direct" and "indirect" leads are returned — the orchestrator
    routes them differently (only direct+is_outreach_ready go to the queue).

    Mutates each signal dict in-place with analysis result fields.
    """
    if not signals:
        return []

    sem = asyncio.Semaphore(AI_CONCURRENCY)

    async def _analyze_one(sig: dict) -> tuple[dict, dict]:
        async with sem:
            return sig, await analyze_pain_signal(sig)

    pairs = await asyncio.gather(
        *[_analyze_one(s) for s in signals],
        return_exceptions=True,
    )

    qualified: list[dict] = []
    counts = {"direct": 0, "indirect": 0, "non_lead": 0, "below_threshold": 0, "error": 0}

    for item in pairs:
        if isinstance(item, Exception):
            counts["error"] += 1
            logger.warning("analyze_one raised: %s", item)
            continue

        signal, result = item
        signal.update(result)

        lead_type = result.get("lead_type", "non_lead")
        score     = result.get("lead_potential", 0)

        if lead_type == "non_lead":
            counts["non_lead"] += 1
            signal["qualified"] = False
            continue                    # discard — never persisted

        if score < SCORE_THRESHOLD:
            counts["below_threshold"] += 1
            signal["qualified"] = False
            continue

        signal["qualified"] = True
        qualified.append(signal)
        counts[lead_type] = counts.get(lead_type, 0) + 1

    outreach_ready = sum(1 for s in qualified if s.get("is_outreach_ready"))
    logger.info(
        "Pain signal analysis: %d/%d kept | "
        "direct=%d indirect=%d non_lead=%d(discarded) "
        "below_threshold=%d errors=%d | outreach_ready=%d | model=%s",
        len(qualified), len(signals),
        counts["direct"], counts["indirect"], counts["non_lead"],
        counts["below_threshold"], counts["error"],
        outreach_ready, MODEL,
    )
    return qualified
