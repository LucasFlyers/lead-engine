"""Pain signals API routes."""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, desc, func, nulls_last
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import PainSignal

router = APIRouter(prefix="/pain-signals", tags=["pain_signals"])


@router.get("")
async def list_pain_signals(
    page:      int = Query(1, ge=1),
    per_page:  int = Query(50, ge=1, le=200),
    min_score: int = Query(0, ge=0),
    source:    str = None,
    db: AsyncSession = Depends(get_db),
):
    """List detected pain signals, ordered best-and-freshest first."""
    query = select(PainSignal)
    if min_score > 0:
        query = query.where(PainSignal.lead_potential >= min_score)
    if source:
        query = query.where(PainSignal.source == source)

    total = await db.scalar(select(func.count()).select_from(query.subquery()))

    # Primary sort: final_rank_score (null-safe — rows without score go last)
    # Secondary: recency of the original source post, then scrape time
    query = query.order_by(
        nulls_last(desc(PainSignal.final_rank_score)),
        nulls_last(desc(PainSignal.source_created_at)),
        desc(PainSignal.scraped_at),
    )
    query = query.offset((page - 1) * per_page).limit(per_page)

    result  = await db.execute(query)
    signals = result.scalars().all()

    return {
        "signals": [
            {
                "id":               str(s.id),
                "source":           s.source,
                "source_url":       s.source_url,
                "content":          s.content[:200],
                "keywords_matched": s.keywords_matched,
                "industry":         s.industry,
                "problem_desc":     s.problem_desc,
                "automation_opp":   s.automation_opp,
                "lead_potential":   s.lead_potential,
                "processed":        s.processed,
                "scraped_at":       s.scraped_at.isoformat(),
                # Freshness / ranking metadata
                "source_created_at": s.source_created_at.isoformat() if s.source_created_at else None,
                "freshness_score":   s.freshness_score,
                "final_rank_score":  s.final_rank_score,
            }
            for s in signals
        ],
        "total": total or 0,
        "page":  page,
    }


@router.get("/stats")
async def pain_signal_stats(db: AsyncSession = Depends(get_db)):
    """Get pain signal statistics."""
    total     = await db.scalar(select(func.count(PainSignal.id)))
    qualified = await db.scalar(
        select(func.count(PainSignal.id)).where(PainSignal.lead_potential >= 6)
    )
    by_source = await db.execute(
        select(PainSignal.source, func.count(PainSignal.id).label("count"))
        .group_by(PainSignal.source)
        .order_by(desc("count"))
    )

    return {
        "total":     total or 0,
        "qualified": qualified or 0,
        "by_source": [{"source": r.source, "count": r.count} for r in by_source.all()],
    }
