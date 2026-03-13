"""
API Routes — analytics.py
Campaign performance, inbox health, and pipeline summary endpoints.
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from ..db.database import get_db
from ..analytics.campaign_intelligence import CampaignIntelligence
from ..analytics.inbox_health_monitor import InboxHealthMonitor

router = APIRouter()
intelligence = CampaignIntelligence()
health_monitor = InboxHealthMonitor()


@router.get("/overview")
async def campaign_overview(
    days: int = Query(30, ge=1, le=365),
    db: AsyncSession = Depends(get_db),
):
    return await intelligence.get_overview(db, days=days)


@router.get("/trend")
async def daily_trend(
    days: int = Query(14, ge=1, le=90),
    db: AsyncSession = Depends(get_db),
):
    return await intelligence.get_daily_trend(db, days=days)


@router.get("/subject-lines")
async def best_subject_lines(
    limit: int = Query(10, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
):
    return await intelligence.get_best_subject_lines(db, limit=limit)


@router.get("/industries")
async def best_industries(
    days: int = Query(30, ge=1, le=180),
    db: AsyncSession = Depends(get_db),
):
    return await intelligence.get_best_industries(db, days=days)


@router.get("/sources")
async def best_sources(db: AsyncSession = Depends(get_db)):
    return await intelligence.get_best_sources(db)


@router.get("/inbox-health")
async def inbox_health(db: AsyncSession = Depends(get_db)):
    return await health_monitor.run_health_check(db)


@router.get("/pipeline")
async def pipeline_summary(db: AsyncSession = Depends(get_db)):
    result = await db.execute(text("SELECT * FROM v_pipeline_summary"))
    row = result.fetchone()
    if row:
        return dict(row._mapping)
    return {}


@router.get("/responses")
async def response_breakdown(
    days: int = Query(30, ge=1, le=180),
    db: AsyncSession = Depends(get_db),
):
    stmt = text("""
        SELECT
            classification,
            COUNT(*) AS count,
            AVG(sentiment_score) AS avg_sentiment
        FROM responses
        WHERE received_at >= NOW() - INTERVAL ':days days'
        GROUP BY classification
        ORDER BY count DESC
    """).bindparams(days=days)
    result = await db.execute(stmt)
    return [{"classification": r.classification, "count": r.count,
             "avg_sentiment": round(r.avg_sentiment or 0, 3)} for r in result.fetchall()]
