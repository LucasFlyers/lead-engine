"""Generate manual outreach suggestions from a qualified pain signal."""
import json
import logging
import os
import re
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")

OUTREACH_PROMPT = """You are a B2B outreach specialist helping a small automation agency
do personalised manual outreach to prospects who have shown pain signals online.

You are given details about a pain signal discovered from {source}.

PAIN SIGNAL DETAILS:
Industry: {industry}
Problem described: {problem_desc}
Automation opportunity: {automation_opp}
Lead potential score: {lead_potential}/10
Author handle/name: {author}
Content snippet: {content_snippet}

Your task is to generate practical outreach assistance for a human SDR who will:
1. Manually research the author to find the right decision-maker
2. Personally review the post before reaching out
3. Send a single personalised message — NOT a bulk blast

Respond ONLY with valid JSON, no markdown fences, no extra keys:
{{
  "target_contact_type": "who to reach out to, e.g. Founder or Operations Manager at a logistics SMB",
  "personalization_hook": "1-2 sentences — what detail from the post the SDR can reference naturally",
  "suggested_subject": "email subject line, max 8 words, no spam trigger words",
  "suggested_email_message": "complete email body, plain text, 120-160 words",
  "suggested_dm_message": "LinkedIn or Reddit DM, max 280 characters, conversational tone",
  "recommended_cta": "single soft call-to-action, e.g. happy to share a quick idea",
  "ai_reasoning": "one sentence — why this signal is worth manual follow-up"
}}

OUTREACH WRITING RULES:
- Do NOT say "I saw your post" or "I noticed you posted" — be natural, not surveillance-y
- Use phrases like: "came across a discussion about", "noticed a pattern common in", "saw a challenge that sounded familiar"
- Write as if you are aware of the pain pattern, not of the specific post
- Do NOT make claims you cannot verify about their company
- Do NOT use hype, pressure, or guarantee language
- Do NOT include any URLs or links inside the messages themselves
- Email body must be under 160 words — be concise
- DM must be under 280 characters — count carefully
- Avoid: free, guarantee, limited time, act now, click here, buy now, earn money
- Tone: professional, direct, human, low-pressure
- CTA must be a single easy-to-answer ask
"""

# Matches a JSON object inside optional markdown code fences
_JSON_BLOCK = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Hard limits applied after generation
_SUBJECT_MAX = 100       # characters
_EMAIL_MAX   = 2000      # characters (~300 words safety ceiling)
_DM_MAX      = 300       # characters


def _sanitize(text: str) -> str:
    """Strip control characters from text going into prompts."""
    return _CONTROL_CHARS.sub("", text) if text else ""


def _extract_json(raw: str) -> str:
    """
    Extract JSON from model output.

    Handles three cases:
    - Plain JSON object (no fences)
    - ```json ... ``` fenced block
    - ``` ... ``` fenced block (no language tag)
    """
    raw = raw.strip()
    # Try fence extraction first
    m = _JSON_BLOCK.search(raw)
    if m:
        return m.group(1).strip()
    # Fall back: assume the whole response is JSON (common when temperature is low)
    # If it starts with '{', trust it
    if raw.startswith("{"):
        return raw
    # Last resort: find first '{' and last '}'
    start = raw.find("{")
    end = raw.rfind("}")
    if start != -1 and end != -1 and end > start:
        return raw[start : end + 1]
    return raw  # will fail json.loads and be caught


def _trim(value: Optional[str], max_len: int) -> Optional[str]:
    """Trim a string field to max_len, adding ellipsis if truncated."""
    if not value:
        return value
    value = value.strip()
    if len(value) > max_len:
        return value[: max_len - 1].rstrip() + "…"
    return value


def _validate_output(result: dict) -> dict:
    """
    Validate and normalise the fields returned by the model.

    - Ensures all expected keys are present (falls back to None)
    - Coerces non-string values to strings
    - Trims fields to safe lengths
    - Rejects values that are clearly placeholder/empty
    """
    def _str(val: object) -> Optional[str]:
        if val is None:
            return None
        s = str(val).strip()
        return s if s else None

    return {
        "target_contact_type":     _trim(_str(result.get("target_contact_type")), 200),
        "personalization_hook":    _trim(_str(result.get("personalization_hook")), 500),
        "suggested_subject":       _trim(_str(result.get("suggested_subject")), _SUBJECT_MAX),
        "suggested_email_message": _trim(_str(result.get("suggested_email_message")), _EMAIL_MAX),
        "suggested_dm_message":    _trim(_str(result.get("suggested_dm_message")), _DM_MAX),
        "recommended_cta":         _trim(_str(result.get("recommended_cta")), 200),
        "ai_reasoning":            _trim(_str(result.get("ai_reasoning")), 500),
        "message_model_used":      MODEL,
    }


async def generate_outreach_suggestions(signal: dict) -> Optional[dict]:
    """
    Generate outreach suggestions for a qualified pain signal.

    Args:
        signal: dict with keys: source, source_url, author, content,
                industry, problem_desc, automation_opp, lead_potential

    Returns:
        dict with normalised outreach fields, or None on failure.
    """
    try:
        prompt = OUTREACH_PROMPT.format(
            source        = _sanitize(signal.get("source", "unknown")),
            author        = _sanitize(signal.get("author", "unknown")),
            industry      = _sanitize(signal.get("industry") or "not specified"),
            problem_desc  = _sanitize(signal.get("problem_desc") or "not specified"),
            automation_opp= _sanitize(signal.get("automation_opp") or "not specified"),
            lead_potential= signal.get("lead_potential") or 0,
            content_snippet=_sanitize(signal.get("content", ""))[:600],
        )

        response = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=900,
        )

        raw = response.choices[0].message.content or ""
        json_str = _extract_json(raw)
        result = json.loads(json_str)

        if not isinstance(result, dict):
            logger.error("Outreach writer returned non-dict JSON: %r", result)
            return None

        return _validate_output(result)

    except json.JSONDecodeError as exc:
        logger.error("Outreach writer JSON parse error: %s | raw=%r", exc, raw[:200])
        return None
    except Exception as exc:
        logger.error("Outreach writer error: %s", exc)
        return None
