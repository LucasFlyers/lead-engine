"""
AI response classifier.
Classifies incoming email replies as interested, not_interested, unsubscribe, ooo, or bounce.
"""
import json
import logging
import os
from typing import Dict, Optional

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)

CLASSIFICATION_PROMPT = """Classify this email reply from a cold outreach campaign.

Return JSON:
{
  "classification": "interested | not_interested | unsubscribe | ooo | bounce | other",
  "sentiment_score": float (-1.0 to 1.0, where 1.0 = very positive),
  "reasoning": "string",
  "next_action": "schedule_call | send_followup | remove_from_list | wait | no_action",
  "key_phrases": ["notable phrases that drove this classification"]
}

Definitions:
- interested: shows curiosity, asks questions, suggests a meeting, positive framing
- not_interested: politely or bluntly declines
- unsubscribe: requests to be removed / stop emails
- ooo: out of office auto-reply
- bounce: delivery failure / no such user
- other: anything else (spam filter notice, etc.)

Only JSON. No markdown."""


class ResponseClassifier:
    def __init__(self):
        self.client = AsyncOpenAI(api_key=os.environ["OPENAI_API_KEY"])
        self.model = os.getenv("AI_MODEL", "gpt-4o-mini")

    async def classify(self, reply_body: str, subject: str = "") -> Dict:
        """Classify a reply email body. Returns classification dict."""
        # Quick rule-based pre-check before hitting API
        quick = self._quick_classify(reply_body)
        if quick:
            return quick

        try:
            prompt = f"Subject: {subject}\n\nBody:\n{reply_body[:3000]}"
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": CLASSIFICATION_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=300,
                response_format={"type": "json_object"},
            )
            result = json.loads(response.choices[0].message.content)
            return result
        except Exception as exc:
            logger.error("Classification error: %s", exc)
            return {
                "classification": "other",
                "sentiment_score": 0.0,
                "reasoning": f"Error: {exc}",
                "next_action": "no_action",
                "key_phrases": [],
            }

    @staticmethod
    def _quick_classify(body: str) -> Optional[Dict]:
        """Fast rule-based classification to save API calls."""
        body_lower = body.lower()

        unsubscribe_phrases = [
            "unsubscribe", "remove me", "take me off", "stop emailing",
            "don't email", "do not email", "opt out",
        ]
        if any(p in body_lower for p in unsubscribe_phrases):
            return {
                "classification": "unsubscribe",
                "sentiment_score": -0.5,
                "reasoning": "Contains unsubscribe request",
                "next_action": "remove_from_list",
                "key_phrases": [],
            }

        ooo_phrases = [
            "out of office", "on vacation", "on leave", "away from the office",
            "will be back", "auto-reply", "automatic reply",
        ]
        if any(p in body_lower for p in ooo_phrases):
            return {
                "classification": "ooo",
                "sentiment_score": 0.0,
                "reasoning": "Out of office auto-reply",
                "next_action": "wait",
                "key_phrases": [],
            }

        bounce_phrases = [
            "delivery failed", "550", "no such user", "mailbox not found",
            "user unknown", "account does not exist",
        ]
        if any(p in body_lower for p in bounce_phrases):
            return {
                "classification": "bounce",
                "sentiment_score": 0.0,
                "reasoning": "Delivery failure",
                "next_action": "remove_from_list",
                "key_phrases": [],
            }

        return None
