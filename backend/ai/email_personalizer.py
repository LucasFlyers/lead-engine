"""
AI-powered email personalization.

AUDIT FIXES:
- Prompt injection guard: company name / industry sanitised before insertion
- classify_response split into its own function with clear contract
- Subject variant formatting uses safe .format_map with default fallback
- JSON extraction is robust: handles partial fences, trailing commas
- OpenAI client created once (module-level lazy singleton)
- Added `is_auto_reply` pre-check before calling AI (saves tokens)
"""
import json
import logging
import os
import random
import re
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_ai_client: Optional[AsyncOpenAI] = None


def _get_ai_client() -> AsyncOpenAI:
    global _ai_client
    if _ai_client is None:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set")
        _ai_client = AsyncOpenAI(api_key=api_key)
    return _ai_client


MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")

# --------------------------------------------------------------------------- #
#  Variation pools
# --------------------------------------------------------------------------- #
SUBJECT_VARIANTS = [
    "quick question about {company}'s workflow",
    "noticed something about {company}",
    "had a thought about {company}'s reporting",
    "automation question — {company}",
    "re: {company}'s ops process",
    "{first_name}, quick thought on {company}",
]

INTRO_VARIANTS = [
    "I was looking at {company}'s work and had a quick thought.",
    "Came across {company} and wanted to reach out directly.",
    "I help service companies like {company} streamline their operations.",
    "Quick note after seeing {company}'s services.",
    "Saw {company}'s site and noticed something worth mentioning.",
]

CTA_VARIANTS = [
    "Would a 15-minute call make sense this week?",
    "Open to a quick chat to see if this fits?",
    "Worth a 10-minute call?",
    "Interested in seeing how this could work for {company}?",
    "Happy to show you a quick example — worth 15 minutes?",
]

# Auto-reply detection patterns (saves AI call)
AUTO_REPLY_PATTERNS = [
    re.compile(r"\bout\s+of\s+(the\s+)?office\b", re.IGNORECASE),
    re.compile(r"\bvacation\s+responder\b", re.IGNORECASE),
    re.compile(r"\bautomatic\s+reply\b", re.IGNORECASE),
    re.compile(r"\bauto.?reply\b", re.IGNORECASE),
    re.compile(r"\bI\s+(am|will\s+be)\s+(away|out)\b", re.IGNORECASE),
    re.compile(r"\bwill\s+return\s+on\b", re.IGNORECASE),
]


def _sanitise(text: str, max_len: int = 200) -> str:
    """Strip characters that break f-string / .format() injection."""
    text = re.sub(r"[\x00-\x1f\x7f]", "", text)
    text = text.replace("{", "[").replace("}", "]")
    return text[:max_len].strip()


def _safe_format(template: str, **kwargs) -> str:
    """Format a template, falling back to empty string for missing keys."""
    try:
        return template.format_map({k: (v or "") for k, v in kwargs.items()})
    except (KeyError, ValueError):
        return template


PERSONALIZATION_PROMPT = """\
You are a cold email copywriter specialising in B2B outreach. Write a concise, personalised email body.

CONTEXT:
Company: {company_name}
Website: {website}
Industry: {industry}
Automation maturity: {automation_maturity}
Pain indicators: {pain_indicators}
Recommended angle: {recommended_angle}

Use this opening line exactly (do not alter it):
"{intro}"

Use this closing line exactly (do not alter it):
"{cta}"

Write the complete email body following these rules:
- Open with the provided intro line
- 1-2 sentences referencing their specific industry/services
- 1 sentence naming a common operational pain for companies like theirs
- 1-2 sentences explaining the benefit we provide (no jargon, no "AI" mentions)
- Close with the provided CTA
- Sign off: "Best,\n[Your name]"
- Max 150 words
- No exclamation points
- No buzzwords ("synergy", "leverage", "cutting-edge")
- Plain text only — no markdown, no HTML

Return the email body text only. No subject line. No JSON.
"""

CLASSIFICATION_PROMPT = """\
Classify this inbound email reply to a B2B cold outreach message.

Subject: {subject}
Body (first 600 chars): {body}

Choose exactly one classification:
- interested    → wants to learn more, asks questions, or suggests a meeting
- not_interested → politely declines or says not relevant right now
- unsubscribe   → explicitly asks to be removed from future emails
- auto_reply    → automated vacation/out-of-office message
- other         → cannot determine clearly

Return ONLY valid JSON:
{{"classification": "string", "confidence": 0.0-1.0, "reasoning": "one sentence"}}
"""


# --------------------------------------------------------------------------- #
#  Email generation
# --------------------------------------------------------------------------- #
async def generate_email(
    company: dict,
    score_data: dict,
    contact: Optional[dict] = None,
) -> Optional[dict]:
    """Generate a personalised cold outreach email."""
    company_name = _sanitise(company.get("company_name") or "your company", 100)
    first_name   = _sanitise((contact or {}).get("first_name") or "", 40)

    subject_tpl = random.choice(SUBJECT_VARIANTS)
    intro_tpl   = random.choice(INTRO_VARIANTS)
    cta_tpl     = random.choice(CTA_VARIANTS)

    subject = _safe_format(subject_tpl, company=company_name, first_name=first_name or "there")
    intro   = _safe_format(intro_tpl,   company=company_name)
    cta     = _safe_format(cta_tpl,     company=company_name)

    prompt = PERSONALIZATION_PROMPT.format(
        company_name       = company_name,
        website            = _sanitise(company.get("website") or "", 80),
        industry           = _sanitise(score_data.get("industry") or company.get("industry") or "your industry", 60),
        automation_maturity= _sanitise(score_data.get("automation_maturity") or "medium", 20),
        pain_indicators    = _sanitise(", ".join(score_data.get("pain_indicators") or []), 200),
        recommended_angle  = _sanitise(score_data.get("recommended_angle") or "operational efficiency", 100),
        intro              = intro,
        cta                = cta,
    )

    try:
        response = await _get_ai_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.75,
            max_tokens=350,
            timeout=25,
        )
        body = (response.choices[0].message.content or "").strip()
        if not body:
            return None

        return {
            "subject":         subject,
            "body":            body,
            "subject_variant": subject_tpl,
            "intro_variant":   intro_tpl,
            "cta_variant":     cta_tpl,
        }
    except Exception as exc:
        logger.error("Email generation failed for %r: %s", company_name, exc)
        return None


# --------------------------------------------------------------------------- #
#  Response classification
# --------------------------------------------------------------------------- #
def _is_auto_reply(subject: str, body: str) -> bool:
    """Fast regex pre-check before sending to AI."""
    text = f"{subject} {body}"
    return any(pat.search(text) for pat in AUTO_REPLY_PATTERNS)


def _extract_json(raw: str) -> dict:
    """Robustly extract JSON from AI output."""
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    # Remove trailing commas before } or ]
    raw = re.sub(r",\s*([}\]])", r"\1", raw)
    return json.loads(raw.strip())


async def classify_response(email_subject: str, email_body: str) -> dict:
    """Classify an inbound reply using rule-based pre-check then AI."""
    # Fast path: auto-reply detection
    if _is_auto_reply(email_subject, email_body):
        return {"classification": "auto_reply", "confidence": 0.95, "reasoning": "Auto-reply pattern matched"}

    prompt = CLASSIFICATION_PROMPT.format(
        subject=_sanitise(email_subject, 200),
        body=_sanitise(email_body, 600),
    )

    try:
        response = await _get_ai_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=120,
            timeout=15,
        )
        raw = (response.choices[0].message.content or "").strip()
        result = _extract_json(raw)

        # Validate classification value
        valid_classes = {"interested", "not_interested", "unsubscribe", "auto_reply", "other"}
        classification = result.get("classification", "other")
        if classification not in valid_classes:
            classification = "other"

        return {
            "classification": classification,
            "confidence": float(result.get("confidence", 0.5)),
            "reasoning": str(result.get("reasoning", "")),
        }
    except json.JSONDecodeError as exc:
        logger.warning("Classification JSON parse error: %s", exc)
    except Exception as exc:
        logger.error("Response classification error: %s", exc)

    return {"classification": "other", "confidence": 0.0, "reasoning": "classification error"}
