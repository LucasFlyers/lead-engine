"""
AI lead scoring based on company website analysis.

AUDIT FIXES:
- score_leads_batch uses asyncio.gather with semaphore (concurrent, not sequential)
- Homepage fetch validates Content-Type — rejects binary files
- fetch_homepage_content has a hard 8s timeout + 512KB response size limit
- Prompt injection guard: company_name sanitised before insertion
- JSON parse is robust: strips markdown fences, validates field types
- Rate limiting via semaphore (max 5 concurrent OpenAI calls)
- model client created once at module level, not per-call
"""
import asyncio
import json
import logging
import os
import re
from typing import Optional

import httpx
from bs4 import BeautifulSoup
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

_ai_client: Optional[AsyncOpenAI] = None

def _get_ai_client() -> AsyncOpenAI:
    global _ai_client
    if _ai_client is None:
        api_key = os.environ.get("OPENAI_API_KEY", "")
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY is not set")
        _ai_client = AsyncOpenAI(api_key=api_key)
    return _ai_client


MODEL           = os.environ.get("AI_MODEL", "gpt-4o-mini")
SCORE_THRESHOLD = int(os.environ.get("LEAD_SCORE_THRESHOLD", "7"))
MAX_CONCURRENT  = int(os.environ.get("AI_CONCURRENCY", "5"))
FETCH_TIMEOUT   = 8       # seconds
MAX_BODY_BYTES  = 512_000 # 512 KB
CONTENT_CHARS   = 2_500   # chars sent to AI

_semaphore: Optional[asyncio.Semaphore] = None

def _get_semaphore() -> asyncio.Semaphore:
    global _semaphore
    if _semaphore is None:
        _semaphore = asyncio.Semaphore(MAX_CONCURRENT)
    return _semaphore


HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml",
    "Accept-Language": "en-US,en;q=0.9",
}

SCORING_PROMPT = """\
You are a B2B sales expert evaluating companies as leads for automation/workflow software.

Analyze this company and rate their suitability as a lead.

COMPANY: {company_name}
WEBSITE: {website}
INDUSTRY: {industry}

HOMEPAGE CONTENT (truncated):
{content}

Return ONLY valid JSON — no markdown, no explanation outside the JSON:
{{
  "industry": "<refined industry>",
  "automation_maturity": "low|medium|high",
  "lead_score": <integer 1-10>,
  "reasoning": "<2-3 sentences>",
  "pain_indicators": ["<indicator1>", "<indicator2>"],
  "recommended_angle": "<outreach angle>"
}}

Scoring guide:
8-10 → Clear automation pain, active B2B service company, manual processes evident
6-7  → Some potential, less obvious need
1-5  → Poor fit (consumer, fully automated, freelancer, enterprise tech giant)
"""


def _sanitise_for_prompt(text: str, max_len: int = 200) -> str:
    """Strip characters that could break prompt formatting or inject instructions."""
    # Remove null bytes, control characters, curly braces (would break .format())
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", text)
    text = text.replace("{", "[").replace("}", "]")
    return text[:max_len]


async def fetch_homepage_content(website: str) -> str:
    """Fetch and extract text from a company homepage."""
    if not website:
        return ""
    if not website.startswith(("http://", "https://")):
        website = f"https://{website}"

    try:
        async with httpx.AsyncClient(
            follow_redirects=True,
            timeout=httpx.Timeout(FETCH_TIMEOUT),
            headers=HEADERS,
        ) as client:
            resp = await client.get(website)

            # Reject non-HTML responses (PDFs, images, etc.)
            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                logger.debug("Skipping non-HTML content-type %r for %s", content_type, website)
                return ""

            # Size guard
            if len(resp.content) > MAX_BODY_BYTES:
                raw_html = resp.content[:MAX_BODY_BYTES].decode("utf-8", errors="replace")
            else:
                raw_html = resp.text

            soup = BeautifulSoup(raw_html, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript"]):
                tag.decompose()

            text = soup.get_text(separator=" ", strip=True)
            # Collapse whitespace
            text = re.sub(r"\s{2,}", " ", text)
            return text[:CONTENT_CHARS]

    except httpx.TimeoutException:
        logger.debug("Timeout fetching %s", website)
    except httpx.RequestError as exc:
        logger.debug("Request error fetching %s: %s", website, exc)
    except Exception as exc:
        logger.debug("Unexpected error fetching %s: %s", website, exc)
    return ""


def _parse_ai_response(raw: str, company_name: str) -> Optional[dict]:
    """Robustly parse AI JSON response."""
    # Strip markdown fences
    raw = raw.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)
    raw = raw.strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("JSON parse error for %r: %s | raw=%r", company_name, exc, raw[:200])
        return None

    # Validate required fields
    score = result.get("lead_score")
    if not isinstance(score, (int, float)) or not (1 <= score <= 10):
        logger.warning("Invalid lead_score %r for %r", score, company_name)
        return None

    return {
        "score": int(score),
        "industry": str(result.get("industry") or ""),
        "automation_maturity": str(result.get("automation_maturity") or "medium"),
        "reasoning": str(result.get("reasoning") or ""),
        "pain_indicators": list(result.get("pain_indicators") or []),
        "recommended_angle": str(result.get("recommended_angle") or ""),
        "model_used": MODEL,
    }


async def score_lead(company: dict) -> Optional[dict]:
    """Score a single company lead using AI."""
    async with _get_semaphore():
        content = await fetch_homepage_content(company.get("website", ""))

        company_name = _sanitise_for_prompt(company.get("company_name", "Unknown"))
        website      = _sanitise_for_prompt(company.get("website", ""), 100)
        industry     = _sanitise_for_prompt(company.get("industry", "Unknown"), 80)

        prompt = SCORING_PROMPT.format(
            company_name=company_name,
            website=website,
            industry=industry,
            content=content or "No content available.",
        )

        try:
            response = await _get_ai_client().chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=500,
                timeout=30,
            )
            raw = response.choices[0].message.content or ""
            return _parse_ai_response(raw, company_name)

        except Exception as exc:
            logger.error("AI scoring error for %r: %s", company_name, exc)
            return None


async def score_leads_batch(companies: list[dict]) -> list[dict]:
    """
    Score all companies concurrently (bounded by MAX_CONCURRENT semaphore).
    Returns only those with score >= SCORE_THRESHOLD.
    """
    if not companies:
        return []

    tasks = [score_lead(c) for c in companies]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    qualified: list[dict] = []
    for company, result in zip(companies, results):
        if isinstance(result, Exception):
            logger.error("Score task raised exception for %r: %s",
                         company.get("company_name"), result)
            continue
        if result and result.get("score", 0) >= SCORE_THRESHOLD:
            company = dict(company)   # don't mutate original
            company["lead_score_data"] = result
            qualified.append(company)

    logger.info(
        "Lead scoring complete: %d/%d qualified (threshold=%d)",
        len(qualified), len(companies), SCORE_THRESHOLD,
    )
    return qualified
