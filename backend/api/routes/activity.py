"""Activity feed API routes."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import SystemEvent

router = APIRouter(prefix="/activity", tags=["activity"])


@router.get("/feed")
async def activity_feed(
    limit: int = Query(50, ge=1, le=200),
    event_type: str = None,
    db: AsyncSession = Depends(get_db),
):
    """Get the system activity feed."""
    query = select(SystemEvent).order_by(desc(SystemEvent.created_at))
    if event_type:
        query = query.where(SystemEvent.event_type == event_type)
    query = query.limit(limit)

    result = await db.execute(query)
    events = result.scalars().all()

    return {
        "events": [
            {
                "id": str(e.id),
                "event_type": e.event_type,
                "entity_type": e.entity_type,
                "entity_id": str(e.entity_id) if e.entity_id else None,
                "message": e.message,
                "metadata": e.event_metadata,
                "created_at": e.created_at.isoformat(),
            }
            for e in events
        ]
    }
