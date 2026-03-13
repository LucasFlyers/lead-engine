"""Inbox health monitoring and automatic pause logic."""
import logging
from datetime import datetime, timedelta

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import EmailSent, Response, InboxHealth
from deliverability.inbox_rotation_manager import get_rotation_manager

logger = logging.getLogger(__name__)

# Thresholds that trigger automatic inbox pause
BOUNCE_RATE_THRESHOLD = 0.05    # 5%
SPAM_RATE_THRESHOLD = 0.001     # 0.1%
MIN_SENDS_FOR_EVALUATION = 10   # Need at least 10 sends to evaluate


async def update_inbox_health(db: AsyncSession, inbox_email: str) -> dict:
    """Recompute and update inbox health metrics."""
    since = datetime.utcnow() - timedelta(days=7)

    # Query sent metrics
    result = await db.execute(
        select(
            func.count(EmailSent.id).label("total"),
            func.sum(func.cast(EmailSent.status == "bounced", int)).label("bounces"),
            func.sum(func.cast(EmailSent.status == "spam_complaint", int)).label("spam"),
        )
        .where(
            and_(EmailSent.from_inbox == inbox_email, EmailSent.sent_at >= since)
        )
    )
    sent_data = result.one()

    # Query reply metrics
    replies = await db.scalar(
        select(func.count(Response.id))
        .join(EmailSent, EmailSent.id == Response.email_sent_id)
        .where(and_(EmailSent.from_inbox == inbox_email, EmailSent.sent_at >= since))
    ) or 0

    total = sent_data.total or 0
    bounces = sent_data.bounces or 0
    spam = sent_data.spam or 0

    bounce_rate = bounces / total if total > 0 else 0
    spam_rate = spam / total if total > 0 else 0
    reply_rate = replies / total if total > 0 else 0

    # Check thresholds
    should_pause = False
    pause_reason = None

    if total >= MIN_SENDS_FOR_EVALUATION:
        if bounce_rate > BOUNCE_RATE_THRESHOLD:
            should_pause = True
            pause_reason = f"Bounce rate {bounce_rate:.1%} exceeds {BOUNCE_RATE_THRESHOLD:.1%} threshold"
        elif spam_rate > SPAM_RATE_THRESHOLD:
            should_pause = True
            pause_reason = f"Spam complaint rate {spam_rate:.2%} exceeds threshold"

    # Upsert inbox health record
    existing = await db.execute(
        select(InboxHealth).where(InboxHealth.inbox_email == inbox_email)
    )
    health_record = existing.scalar_one_or_none()

    if health_record:
        health_record.bounce_rate = round(bounce_rate, 4)
        health_record.spam_rate = round(spam_rate, 4)
        health_record.reply_rate = round(reply_rate, 4)
        health_record.sent_today = total  # Approximate
        health_record.updated_at = datetime.utcnow()

        if should_pause and not health_record.is_paused:
            health_record.is_paused = True
            health_record.pause_reason = pause_reason
            get_rotation_manager().pause_inbox(inbox_email, pause_reason)
            logger.warning(f"Auto-paused inbox {inbox_email}: {pause_reason}")
    else:
        health_record = InboxHealth(
            inbox_email=inbox_email,
            domain=inbox_email.split("@")[-1],
            bounce_rate=round(bounce_rate, 4),
            spam_rate=round(spam_rate, 4),
            reply_rate=round(reply_rate, 4),
            is_paused=should_pause,
            pause_reason=pause_reason,
        )
        db.add(health_record)

    await db.commit()

    return {
        "inbox": inbox_email,
        "total_7d": total,
        "bounce_rate": f"{bounce_rate:.2%}",
        "spam_rate": f"{spam_rate:.3%}",
        "reply_rate": f"{reply_rate:.2%}",
        "is_paused": should_pause,
        "pause_reason": pause_reason,
    }


async def check_all_inbox_health(db: AsyncSession) -> list[dict]:
    """Check health of all configured inboxes."""
    rotation_manager = get_rotation_manager()
    results = []

    for inbox in rotation_manager.inboxes:
        health = await update_inbox_health(db, inbox.email)
        results.append(health)

    return results
