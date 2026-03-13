"""Campaign analytics API routes."""
from datetime import date, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from analytics.campaign_intelligence import (
    get_campaign_summary,
    get_best_subject_lines,
    get_best_industries,
    get_best_sources,
    compute_daily_metrics,
)

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


@router.get("/summary")
async def campaign_summary(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    """Get campaign performance summary."""
    return await get_campaign_summary(db, days)


@router.get("/metrics/daily")
async def daily_metrics(
    days: int = Query(14, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    """Get daily campaign metrics for the last N days."""
    metrics = []
    for i in range(days - 1, -1, -1):
        target_date = date.today() - timedelta(days=i)
        day_metrics = await compute_daily_metrics(db, target_date)
        metrics.append(day_metrics)
    return {"metrics": metrics}


@router.get("/subject-lines/best")
async def best_subject_lines(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Get best performing subject lines."""
    return {"subject_lines": await get_best_subject_lines(db, limit)}


@router.get("/industries/best")
async def best_industries(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    """Get best performing industries."""
    return {"industries": await get_best_industries(db, limit)}


@router.get("/sources/best")
async def best_sources(db: AsyncSession = Depends(get_db)):
    """Get best performing lead sources."""
    return {"sources": await get_best_sources(db)}
