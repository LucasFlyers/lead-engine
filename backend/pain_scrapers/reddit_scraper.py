"""
Reddit pain signal scraper.
Targets posts from BUSINESS OWNERS asking for help with manual processes.
"""
import asyncio
import logging
from datetime import datetime
import httpx

logger = logging.getLogger(__name__)

SEARCH_QUERIES = [
    "how do I automate",
    "is there a way to automate",
    "looking for software to",
    "need help automating",
    "tired of manually",
    "sick of manually",
    "takes hours every week",
    "my team spends hours",
    "wasting hours on",
    "still doing this manually",
    "doing this by hand",
    "manual data entry",
    "manually copying",
    "manually updating",
    "manually sending",
    "manually creating invoices",
    "manually tracking",
    "recommend software for",
    "what software do you use for",
    "best tool for automating",
    "automate my workflow",
    "automate our process",
    "spreadsheet is killing",
    "drowning in paperwork",
    "too much admin work",
    "hate doing manually",
    "anyone else manually",
    "how to stop manually",
]

TARGET_SUBREDDITS = [
    "smallbusiness",
    "Entrepreneur",
    "startups",
    "business",
    "Bookkeeping",
    "accounting",
    "sales",
    "ecommerce",
    "humanresources",
    "projectmanagement",
    "marketing",
    "freelance",
    "agency",
    "realestate",
    "legaladvice",
    "restaurantowners",
    "Dentistry",
    "MedicalPractice",
    "logistics",
    "Manufacturing",
]

DISQUALIFY_KEYWORDS = [
    "show hn", "show reddit", "i built", "i made", "i created",
    "i automated", "i just launched", "i wrote", "my tool", "my app",
    "my saas", "here's how i", "how i automated", "how i built",
    "i saved", "we saved", "case study", "success story", "tutorial",
    "announcing", "introducing", "launch", "product hunt",
    "i've been building", "i'm building", "side project",
    "yc s", "yc w", "ycombinator", "techcrunch", "hacker news post",
]

REDDIT_API = "https://www.reddit.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; research/1.0)",
    "Accept": "application/json",
}


def is_genuine_pain(post_data: dict) -> tuple[bool, list[str]]:
    title = post_data.get("title", "").lower()
    body = post_data.get("selftext", "").lower()
    full_text = f"{title} {body}"

    for dq in DISQUALIFY_KEYWORDS:
        if dq in full_text:
            return False, []

    if len(full_text.strip()) < 40:
        return False, []

    matched = [kw for kw in SEARCH_QUERIES if kw in full_text]

    is_question = (
        "?" in title or
        any(title.startswith(w) for w in (
            "how", "is there", "what", "can i", "anyone",
            "does anyone", "looking for", "need", "help",
            "best way", "any software", "any tool", "any app",
            "recommend", "suggestion", "advice",
        ))
    )

    if matched and is_question:
        return True, matched
    if len(matched) >= 2:
        return True, matched

    return False, []


async def search_subreddit(
    client: httpx.AsyncClient,
    subreddit: str,
    keyword: str,
) -> list[dict]:
    signals = []
    url = f"{REDDIT_API}/r/{subreddit}/search.json"
    params = {
        "q": keyword,
        "sort": "new",
        "limit": 25,
        "t": "month",
        "restrict_sr": "true",
    }

    try:
        resp = await client.get(url, headers=HEADERS, params=params, timeout=15)
        if resp.status_code == 429:
            await asyncio.sleep(5)
            return signals
        if resp.status_code != 200:
            return signals

        data = resp.json()
        posts = data.get("data", {}).get("children", [])

        for post in posts:
            d = post.get("data", {})
            if d.get("score", 0) < -5:
                continue

            valid, matched = is_genuine_pain(d)
            if not valid:
                continue

            title = d.get("title", "")
            body = d.get("selftext", "")[:800]
            content = f"{title}\n\n{body}".strip()

            signals.append({
                "source": "reddit",
                "source_url": f"https://reddit.com{d.get('permalink', '')}",
                "author": d.get("author", ""),
                "content": content,
                "keywords_matched": matched,
                "subreddit": subreddit,
                "post_score": d.get("score", 0),
                "num_comments": d.get("num_comments", 0),
                "scraped_at": datetime.utcnow().isoformat(),
            })

    except Exception as e:
        logger.warning(f"Error searching r/{subreddit} for '{keyword}': {e}")

    return signals


async def scrape_reddit(max_subreddits: int = 12) -> list[dict]:
    all_signals = []
    subreddits = TARGET_SUBREDDITS[:max_subreddits]

    async with httpx.AsyncClient() as client:
        for subreddit in subreddits:
            for keyword in SEARCH_QUERIES[:12]:
                signals = await search_subreddit(client, subreddit, keyword)
                all_signals.extend(signals)
                await asyncio.sleep(1.2)

    seen = set()
    unique = []
    for s in all_signals:
        url = s["source_url"]
        if url not in seen:
            seen.add(url)
            unique.append(s)

    logger.info(f"Reddit scraper: found {len(unique)} pain signals")
    return unique
