"""system.py"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from db.database import get_db
from db.models import SystemEvent

router = APIRouter()

@router.get("/events")
async def list_events(
    limit: int = Query(50, le=200),
    severity: str = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(SystemEvent).order_by(desc(SystemEvent.occurred_at))
    if severity:
        stmt = stmt.where(SystemEvent.severity == severity)
    stmt = stmt.limit(limit)
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return [
        {"id": str(r.id), "type": r.event_type, "severity": r.severity,
         "title": r.title, "description": r.description,
         "occurred_at": r.occurred_at} for r in rows
    ]

@router.get("/health")
async def system_health():
    import psutil
    return {
        "status": "healthy",
        "cpu_percent": psutil.cpu_percent(),
        "memory_percent": psutil.virtual_memory().percent,
        "disk_percent": psutil.disk_usage("/").percent,
    }
