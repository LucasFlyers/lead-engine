"""Inbox and deliverability API routes."""
from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...db.models import InboxHealth
from ...deliverability.inbox_rotation_manager import get_rotation_manager
from ...analytics.inbox_health_monitor import check_all_inbox_health

router = APIRouter(prefix="/inbox", tags=["inbox"])


@router.get("/status")
async def inbox_status():
    """Get current inbox rotation status."""
    return {"inboxes": get_rotation_manager().get_status()}


@router.get("/health")
async def inbox_health(db: AsyncSession = Depends(get_db)):
    """Get inbox health metrics."""
    result = await db.execute(select(InboxHealth))
    records = result.scalars().all()

    return {
        "health": [
            {
                "inbox": r.inbox_email,
                "warmup_week": r.warmup_week,
                "daily_limit": r.daily_limit,
                "sent_today": r.sent_today,
                "bounce_rate": f"{r.bounce_rate:.2%}",
                "spam_rate": f"{r.spam_rate:.3%}",
                "reply_rate": f"{r.reply_rate:.2%}",
                "is_paused": r.is_paused,
                "pause_reason": r.pause_reason,
                "last_sent_at": r.last_sent_at.isoformat() if r.last_sent_at else None,
            }
            for r in records
        ]
    }


@router.post("/health/refresh")
async def refresh_health(db: AsyncSession = Depends(get_db)):
    """Refresh inbox health metrics."""
    results = await check_all_inbox_health(db)
    return {"updated": results}


@router.post("/{inbox_email}/pause")
async def pause_inbox(inbox_email: str, reason: str = "Manual pause"):
    """Manually pause an inbox."""
    get_rotation_manager().pause_inbox(inbox_email, reason)
    return {"status": "paused", "inbox": inbox_email}


@router.post("/{inbox_email}/resume")
async def resume_inbox(inbox_email: str):
    """Resume a paused inbox."""
    get_rotation_manager().resume_inbox(inbox_email)
    return {"status": "resumed", "inbox": inbox_email}
