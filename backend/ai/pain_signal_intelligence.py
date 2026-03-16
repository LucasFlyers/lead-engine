"""
Pain Signal Intelligence — extracts targeting patterns from pain signals
and uses them to make the lead scraper smarter.

How it works:
1. Reads recent high-score pain signals from the database
2. Extracts common industries, problems, and company profiles
3. Returns targeting context used by the lead scraper and email personalizer
"""
import logging
import os
import json
from typing import Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
MODEL = os.environ.get("AI_MODEL", "gpt-4o-mini")


INTELLIGENCE_PROMPT = """You are a B2B sales strategist. 
Analyze these pain signals from business owners and extract targeting intelligence.

PAIN SIGNALS:
{signals}

Return ONLY valid JSON:
{{
  "top_industries": ["industry1", "industry2", "industry3"],
  "top_problems": ["problem1", "problem2", "problem3"],
  "company_profile": "1-2 sentence description of the ideal target company based on these signals",
  "outreach_angle": "The most compelling reason to reach out — specific pain point to address",
  "search_keywords": ["keyword1", "keyword2", "keyword3"]
}}

Be specific. For example:
- top_industries: ["Accounting firms", "E-commerce stores", "Real estate agencies"]
- top_problems: ["Manual invoice entry", "Spreadsheet data entry", "Manual receipt processing"]
- outreach_angle: "Small business owners spending 5+ hours/week on manual data entry"
- search_keywords: ["bookkeeping firm", "small accounting practice", "ecommerce store"]
"""


async def extract_targeting_intelligence(signals: list[dict]) -> Optional[dict]:
    """
    Extract targeting intelligence from a list of pain signals.
    Returns a dict with industries, problems, and search keywords.
    """
    if not signals:
        return None

    # Format signals for the prompt
    signal_texts = []
    for s in signals[:20]:  # Use top 20 signals
        text = f"- [{s.get('industry', 'Unknown')}] {s.get('content', '')[:200]}"
        if s.get('problem_desc'):
            text += f" | Problem: {s['problem_desc']}"
        signal_texts.append(text)

    try:
        response = await client.chat.completions.create(
            model=MODEL,
            messages=[{
                "role": "user",
                "content": INTELLIGENCE_PROMPT.format(
                    signals="\n".join(signal_texts)
                )
            }],
            temperature=0.2,
            max_tokens=500,
        )

        raw = response.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]

        result = json.loads(raw.strip())
        logger.info(
            "Pain signal intelligence: industries=%s, angle=%s",
            result.get("top_industries"),
            result.get("outreach_angle"),
        )
        return result

    except Exception as e:
        logger.error("Error extracting pain signal intelligence: %s", e)
        return None


async def get_targeting_from_db() -> Optional[dict]:
    """
    Read recent qualified pain signals from DB and extract targeting intelligence.
    Called by the lead scraper before each run.
    """
    try:
        from db.database import AsyncSessionLocal
        from db.models import PainSignal
        from sqlalchemy import select, desc

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(PainSignal)
                .where(PainSignal.lead_potential >= 7)
                .order_by(desc(PainSignal.scraped_at))
                .limit(30)
            )
            signals = result.scalars().all()

        if not signals:
            logger.info("No qualified pain signals found for intelligence extraction")
            return None

        signal_dicts = [
            {
                "content": s.content,
                "industry": s.industry,
                "problem_desc": s.problem_desc,
                "automation_opp": s.automation_opp,
                "lead_potential": s.lead_potential,
            }
            for s in signals
        ]

        intelligence = await extract_targeting_intelligence(signal_dicts)
        return intelligence

    except Exception as e:
        logger.error("Error getting targeting from DB: %s", e)
        return None
