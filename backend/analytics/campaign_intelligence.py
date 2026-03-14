"""Campaign intelligence and performance analytics."""
import logging
from datetime import date, datetime, timedelta
from typing import Optional

from sqlalchemy import select, func, and_
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    EmailSent, Response, CampaignMetrics, Company, InboxHealth
)

logger = logging.getLogger(__name__)


async def compute_daily_metrics(db: AsyncSession, target_date: Optional[date] = None) -> dict:
    """Compute campaign metrics for a specific date."""
    if target_date is None:
        target_date = date.today()

    start = datetime.combine(target_date, datetime.min.time())
    end = datetime.combine(target_date, datetime.max.time())

    # Emails sent
    sent_result = await db.execute(
        select(
            EmailSent.from_inbox,
            func.count(EmailSent.id).label("count"),
            func.sum(func.cast(EmailSent.status == "bounced", int)).label("bounces"),
            func.sum(func.cast(EmailSent.status == "spam_complaint", int)).label("spam_complaints"),
        )
        .where(and_(EmailSent.sent_at >= start, EmailSent.sent_at <= end))
        .group_by(EmailSent.from_inbox)
    )
    sent_by_inbox = sent_result.all()

    # Responses
    resp_result = await db.execute(
        select(
            func.count(Response.id).label("total"),
            func.sum(func.cast(Response.classification == "interested", int)).label("interested"),
            func.sum(func.cast(Response.classification == "not_interested", int)).label("not_interested"),
            func.sum(func.cast(Response.classification == "unsubscribe", int)).label("unsubscribes"),
        )
        .where(and_(Response.received_at >= start, Response.received_at <= end))
    )
    resp_data = resp_result.one()

    metrics = {
        "date": target_date.isoformat(),
        "emails_sent": sum(r.count for r in sent_by_inbox),
        "bounces": sum(r.bounces or 0 for r in sent_by_inbox),
        "spam_complaints": sum(r.spam_complaints or 0 for r in sent_by_inbox),
        "replies": resp_data.total or 0,
        "interested": resp_data.interested or 0,
        "not_interested": resp_data.not_interested or 0,
        "unsubscribes": resp_data.unsubscribes or 0,
    }

    total_sent = metrics["emails_sent"]
    if total_sent > 0:
        metrics["reply_rate"] = round(metrics["replies"] / total_sent * 100, 2)
        metrics["positive_rate"] = round(metrics["interested"] / total_sent * 100, 2)
        metrics["bounce_rate"] = round(metrics["bounces"] / total_sent * 100, 2)
    else:
        metrics["reply_rate"] = 0
        metrics["positive_rate"] = 0
        metrics["bounce_rate"] = 0

    return metrics


async def get_best_subject_lines(db: AsyncSession, limit: int = 10) -> list[dict]:
    """Get best performing subject lines by reply rate."""
    result = await db.execute(
        select(
            EmailSent.subject,
            EmailSent.subject_variant,
            func.count(EmailSent.id).label("sent_count"),
            func.count(Response.id).label("reply_count"),
        )
        .outerjoin(Response, Response.email_sent_id == EmailSent.id)
        .group_by(EmailSent.subject, EmailSent.subject_variant)
        .having(func.count(EmailSent.id) >= 5)
        .order_by((func.count(Response.id) / func.count(EmailSent.id)).desc())
        .limit(limit)
    )
    rows = result.all()

    return [
        {
            "subject": row.subject,
            "variant": row.subject_variant,
            "sent": row.sent_count,
            "replies": row.reply_count,
            "reply_rate": round(row.reply_count / row.sent_count * 100, 2) if row.sent_count else 0,
        }
        for row in rows
    ]


async def get_best_industries(db: AsyncSession, limit: int = 10) -> list[dict]:
    """Get best performing industries by reply rate."""
    result = await db.execute(
        select(
            Company.industry,
            func.count(EmailSent.id).label("sent"),
            func.count(Response.id).label("replies"),
            func.sum(func.cast(Response.classification == "interested", int)).label("interested"),
        )
        .join(EmailSent, EmailSent.company_id == Company.id)
        .outerjoin(Response, Response.email_sent_id == EmailSent.id)
        .where(Company.industry.isnot(None))
        .group_by(Company.industry)
        .having(func.count(EmailSent.id) >= 3)
        .order_by(func.count(Response.id).desc())
        .limit(limit)
    )
    rows = result.all()

    return [
        {
            "industry": row.industry,
            "sent": row.sent,
            "replies": row.replies,
            "interested": row.interested or 0,
            "reply_rate": round(row.replies / row.sent * 100, 2) if row.sent else 0,
        }
        for row in rows
    ]


async def get_best_sources(db: AsyncSession) -> list[dict]:
    """Get best performing lead sources."""
    result = await db.execute(
        select(
            Company.source,
            func.count(EmailSent.id).label("sent"),
            func.count(Response.id).label("replies"),
        )
        .join(EmailSent, EmailSent.company_id == Company.id)
        .outerjoin(Response, Response.email_sent_id == EmailSent.id)
        .group_by(Company.source)
        .order_by(func.count(EmailSent.id).desc())
    )
    rows = result.all()

    return [
        {
            "source": row.source,
            "sent": row.sent,
            "replies": row.replies,
            "reply_rate": round(row.replies / row.sent * 100, 2) if row.sent else 0,
        }
        for row in rows
    ]


async def get_campaign_summary(db: AsyncSession, days: int = 30) -> dict:
    """Get overall campaign summary for the last N days."""
    since = datetime.utcnow() - timedelta(days=days)

    # Total sent
    total_sent = await db.scalar(
        select(func.count(EmailSent.id)).where(EmailSent.sent_at >= since)
    )

    # Total replies
    total_replies = await db.scalar(
        select(func.count(Response.id)).where(Response.received_at >= since)
    )

    # Interested
    interested = await db.scalar(
        select(func.count(Response.id)).where(
            and_(Response.received_at >= since, Response.classification == "interested")
        )
    )

    # Companies in queue
    from db.models import OutreachQueue
    in_queue = await db.scalar(
        select(func.count(OutreachQueue.id)).where(OutreachQueue.status == "pending")
    )

    total_sent = total_sent or 0
    total_replies = total_replies or 0
    interested = interested or 0

    return {
        "period_days": days,
        "total_sent": total_sent,
        "total_replies": total_replies,
        "interested": interested,
        "in_queue": in_queue or 0,
        "reply_rate": round(total_replies / total_sent * 100, 2) if total_sent else 0,
        "positive_rate": round(interested / total_sent * 100, 2) if total_sent else 0,
    }


async def update_campaign_metrics(db, full_rebuild: bool = False) -> int:
    """
    Aggregate daily metrics into campaign_metrics table.
    Called by analytics worker — safe to run repeatedly (upsert logic).
    """
    from datetime import date, timedelta
    from sqlalchemy import select, and_
    from db.models import CampaignMetrics

    days_back = 90 if full_rebuild else 3
    results = 0

    for i in range(days_back - 1, -1, -1):
        target_date = date.today() - timedelta(days=i)
        try:
            metrics = await compute_daily_metrics(db, target_date)

            existing = await db.execute(
                select(CampaignMetrics).where(
                    and_(
                        CampaignMetrics.date == target_date,
                        CampaignMetrics.inbox == None,
                    )
                )
            )
            rec = existing.scalar_one_or_none()

            if rec:
                rec.emails_sent     = metrics["emails_sent"]
                rec.bounces         = metrics["bounces"]
                rec.spam_complaints = metrics["spam_complaints"]
                rec.replies         = metrics["replies"]
                rec.interested      = metrics["interested"]
                rec.not_interested  = metrics["not_interested"]
                rec.unsubscribes    = metrics["unsubscribes"]
                rec.reply_rate      = metrics["reply_rate"]
                rec.positive_rate   = metrics["positive_rate"]
            else:
                db.add(CampaignMetrics(
                    date            = target_date,
                    emails_sent     = metrics["emails_sent"],
                    bounces         = metrics["bounces"],
                    spam_complaints = metrics["spam_complaints"],
                    replies         = metrics["replies"],
                    interested      = metrics["interested"],
                    not_interested  = metrics["not_interested"],
                    unsubscribes    = metrics["unsubscribes"],
                    reply_rate      = metrics["reply_rate"],
                    positive_rate   = metrics["positive_rate"],
                ))
            results += 1
        except Exception as exc:
            logger.warning("Failed to update metrics for %s: %s", target_date, exc)

    await db.commit()
    logger.info("Campaign metrics updated: %d days processed", results)
    return results
