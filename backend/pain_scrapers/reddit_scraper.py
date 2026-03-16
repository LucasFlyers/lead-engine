"""
Reddit pain signal scraper.
Targets posts from BUSINESS OWNERS asking for help with manual processes.
Focuses on question posts and help requests — not tutorials or success stories.
"""
import asyncio
import logging
from datetime import datetime
import httpx

logger = logging.getLogger(__name__)

# Search queries designed to find QUESTIONS and HELP REQUESTS from business owners
# Format: queries that a business owner in pain would actually type
SEARCH_QUERIES = [
    # Direct help requests
    "how do I automate",
    "is there a way to automate",
    "looking for software to",
    "need help automating",
    "anyone know how to stop manually",
    "tired of manually",
    "sick of manually entering",
    # Time/cost pain
    "takes hours every week",
    "my team spends hours",
    "wasting hours on",
    "still doing this manually",
    "doing this by hand",
    # Specific process pain
    "manual data entry is killing",
    "manually copying data",
    "manually updating spreadsheet",
    "manually sending emails",
    "manually creating invoices",
    "manually tracking",
    # Looking for solutions
    "recommend software for",
    "what software do you use for",
    "best tool for automating",
    "how to stop doing manually",
    "automate my workflow",
    "automate our process",
]

# Subreddits where BUSINESS OWNERS ask for operational help
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
]

# Keywords that DISQUALIFY a post (skip these)
DISQUALIFY_KEYWORDS = [
    "i built", "i made", "i created", "i automated", "i just launched",
    "show hn", "show reddit", "i wrote", "my tool", "my app", "my saas",
    "here's how i", "how i automated", "how i built", "i saved",
    "we saved", "case study", "success story", "tutorial", "guide",
    "announcing", "introducing", "launch", "product hunt",
    "i've been building", "i'm building", "side project",
]

REDDIT_API = "https://www.reddit.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; research/1.0)",
    "Accept": "application/json",
}


def is_genuine_pain(post_data: dict) -> tuple[bool, list[str]]:
    """
    Check if a post is a genuine business pain signal.
    Returns (is_valid, keywords_matched).
    """
    title = post_data.get("title", "").lower()
    body = post_data.get("selftext", "").lower()
    full_text = f"{title} {body}"

    # Skip if it looks like a promotion or success story
    for dq in DISQUALIFY_KEYWORDS:
        if dq in full_text:
            return False, []

    # Skip link posts with no body (usually articles/promotions)
    if not body and not title.endswith("?"):
        return False, []

    # Skip very short posts (likely spam)
    if len(full_text) < 50:
        return False, []

    # Must match at least one pain keyword
    matched = []
    for kw in SEARCH_QUERIES:
        if kw in full_text:
            matched.append(kw)

    # Extra weight for question posts
    is_question = (
        "?" in title or
        title.startswith(("how", "is there", "what", "can i", "anyone",
                         "does anyone", "looking for", "need", "help"))
    )

    if matched and is_question:
        return True, matched
    elif len(matched) >= 2:  # Multiple keyword matches even without question
        return True, matched

    return False, []


async def search_subreddit(
    client: httpx.AsyncClient,
    subreddit: str,
    keyword: str,
) -> list[dict]:
    """Search a subreddit for genuine business pain posts."""
    signals = []
    url = f"{REDDIT_API}/r/{subreddit}/search.json"
    params = {
        "q": keyword,
        "sort": "new",
        "limit": 25,
        "t": "month",
        "restrict_sr": "true",  # Only search this subreddit
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

            # Skip deleted/removed posts
            if d.get("selftext") in ("[deleted]", "[removed]", ""):
                if "?" not in d.get("title", ""):
                    continue

            # Skip posts with very low engagement (likely bots/spam)
            if d.get("score", 0) < -5:
                continue

            valid, matched = is_genuine_pain(d)
            if not valid:
                continue

            title = d.get("title", "")
            body = d.get("selftext", "")[:600]
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


async def scrape_reddit(max_subreddits: int = 8) -> list[dict]:
    """
    Scrape Reddit for genuine business pain signals.
    Only returns posts where business owners are asking for help.
    """
    all_signals = []
    subreddits = TARGET_SUBREDDITS[:max_subreddits]

    async with httpx.AsyncClient() as client:
        for subreddit in subreddits:
            # Use first 10 queries per subreddit
            for keyword in SEARCH_QUERIES[:10]:
                signals = await search_subreddit(client, subreddit, keyword)
                all_signals.extend(signals)
                await asyncio.sleep(1.5)  # Respect Reddit rate limits

    # Deduplicate by source_url
    seen = set()
    unique = []
    for s in all_signals:
        url = s["source_url"]
        if url not in seen:
            seen.add(url)
            unique.append(s)

    logger.info(f"Reddit scraper: found {len(unique)} pain signals")
    return unique
