"""AI-powered pain signal analysis and qualification."""
import json
import logging
import os
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")
SCORE_THRESHOLD = 7

ANALYSIS_PROMPT = """You are a B2B sales development expert. Your job is to identify
Reddit posts from REAL BUSINESS OWNERS who need automation or software help and
could become paying customers.

Analyze this post:

CONTENT:
{content}

SUBREDDIT: r/{subreddit}
SOURCE: {source}
POST ENGAGEMENT: {post_score} upvotes, {num_comments} comments

Respond ONLY with valid JSON:
{{
  "industry": "string",
  "problem_description": "string (the specific manual process causing pain)",
  "automation_opportunity": "string (exact software/automation that would solve this)",
  "lead_potential_score": integer (1-10),
  "reasoning": "string (one sentence)",
  "contact_worthy": boolean (true if we should reach out to this person)
}}

SCORING RULES — be strict:

SCORE 1-2 — REJECT immediately if ANY of these:
- Person is sharing something they built/automated ("I built", "I automated", "I created")
- Product announcement or launch post
- Tutorial or how-to guide
- Success story ("we saved X hours")
- Academic or student project
- The post is from a DEVELOPER building tools, not a business using tools

SCORE 3-5 — Weak signal:
- Vague frustration with no specific process named
- Large enterprise (they have IT budget)
- Hobby or personal project
- General question without business context

SCORE 6 — Moderate:
- Small business owner asking about software options
- Clear manual process but no urgency expressed

SCORE 7-8 — Strong lead:
- Small/medium business owner (1-50 employees)
- Specific named manual process: data entry, invoicing, reporting, CRM, scheduling, inventory, payroll, onboarding, order processing, email follow-up
- Expresses time cost OR asks for software recommendation
- Does NOT have a solution yet

SCORE 9-10 — Perfect lead:
- All of score 7-8 PLUS
- Mentions specific cost of the problem (time, money, staff hours)
- Asking for immediate help or recommendation
- Clear budget or willingness to pay for solution

Set contact_worthy=true ONLY for scores 7+.
"""


async def analyze_pain_signal(signal: dict) -> Optional[dict]:
    """Analyze a single pain signal using AI."""
    try:
        prompt = ANALYSIS_PROMPT.format(
            content=signal.get("content", "")[:2000],
            subreddit=signal.get("subreddit", "unknown"),
            source=signal.get("source", ""),
            post_score=signal.get("post_score", "?"),
            num_comments=signal.get("num_comments", "?"),
        )

        response = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=400,
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        raw = raw.strip()

        result = json.loads(raw)

        return {
            "industry": result.get("industry"),
            "problem_desc": result.get("problem_description"),
            "automation_opp": result.get("automation_opportunity"),
            "lead_potential": result.get("lead_potential_score", 0),
            "reasoning": result.get("reasoning"),
            "contact_worthy": result.get("contact_worthy", False),
        }

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error: {e}")
        return None
    except Exception as e:
        logger.error(f"Error analyzing pain signal: {e}")
        return None


async def analyze_batch(signals: list[dict]) -> list[dict]:
    """Analyze a batch of pain signals, returning only qualified ones."""
    qualified = []

    for signal in signals:
        result = await analyze_pain_signal(signal)
        if result and result.get("lead_potential", 0) >= SCORE_THRESHOLD:
            signal.update(result)
            signal["qualified"] = True
            qualified.append(signal)
        else:
            signal["qualified"] = False

    logger.info(
        f"Pain signal analysis: {len(qualified)}/{len(signals)} qualified (score >= {SCORE_THRESHOLD})"
    )
    return qualified
