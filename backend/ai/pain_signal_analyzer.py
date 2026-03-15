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


ANALYSIS_PROMPT = """You are a B2B lead qualification expert specializing in automation and workflow optimization.

Analyze this discussion post and extract lead intelligence. You are looking for BUSINESSES that need automation help — NOT people sharing automation success stories or promoting tools.

CONTENT:
{content}

SOURCE: {source}
KEYWORDS MATCHED: {keywords}

Respond ONLY with valid JSON in this exact structure:
{{
  "industry": "string (best guess at industry this person/company is in)",
  "problem_description": "string (brief description of the operational pain they're experiencing)",
  "automation_opportunity": "string (specific automation or software solution that would solve this)",
  "lead_potential_score": integer (1-10, where 10 = perfect automation prospect),
  "reasoning": "string (1-2 sentences explaining your score)"
}}

Scoring criteria:
- 8-10: Business owner/operator expressing frustration with manual processes, wasted time on repetitive tasks, asking how to automate a specific workflow, mentions specific processes (invoicing, reporting, data entry, CRM, scheduling). HIGH SCORE.
- 6-7: Business context with some automation need but less specific or urgent.
- 1-5: Off-topic, consumer question, or any of the following DISQUALIFIERS.

DISQUALIFIERS — score 1-3 for:
- Posts sharing automation success stories ("we automated X and saved Y hours")
- Promoting or reviewing AI/automation tools or products
- Tutorial or how-to content about building automations
- Marketing or promotional content
- Developer discussing technical implementation
- No clear business operational pain expressed

BOOST SCORE for:
- Frustration language: "tired of", "sick of", "it takes forever", "wasting hours"
- Business owner or operations role mentioned
- Specific manual process named (invoicing, spreadsheet reports, data entry, CRM updates)
- Small or medium business context (not enterprise, not solo hobby project)
- Asking for help or recommendations to solve a workflow problem
"""


async def analyze_pain_signal(signal: dict) -> Optional[dict]:
    """Analyze a single pain signal using AI."""
    try:
        prompt = ANALYSIS_PROMPT.format(
            content=signal.get("content", "")[:1000],
            source=signal.get("source", ""),
            keywords=", ".join(signal.get("keywords_matched", [])),
        )

        response = await client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_tokens=400,
        )

        raw = response.choices[0].message.content.strip()
        # Strip markdown code blocks if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw)

        return {
            "industry": result.get("industry"),
            "problem_desc": result.get("problem_description"),
            "automation_opp": result.get("automation_opportunity"),
            "lead_potential": result.get("lead_potential_score", 0),
            "reasoning": result.get("reasoning"),
        }

    except json.JSONDecodeError as e:
        logger.error(f"JSON parse error in pain signal analysis: {e}")
        return None
    except Exception as e:
        logger.error(f"Error analyzing pain signal: {e}")
        return None


async def analyze_batch(signals: list[dict]) -> list[dict]:
    """Analyze a batch of pain signals, returning only high-score ones."""
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
