"""
API Routes — companies.py
"""
from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from db.database import get_db
from db.models import Company

router = APIRouter()

@router.get("")
async def list_companies(
    page: int = Query(1, ge=1),
    limit: int = Query(50, le=200),
    source: str = None,
    industry: str = None,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Company).where(Company.is_duplicate == False)
    if source:
        stmt = stmt.where(Company.source == source)
    if industry:
        stmt = stmt.where(Company.industry.ilike(f"%{industry}%"))
    stmt = stmt.offset((page - 1) * limit).limit(limit).order_by(Company.scraped_at.desc())
    result = await db.execute(stmt)
    rows = result.scalars().all()
    return {"items": [{"id": str(r.id), "company_name": r.company_name, "website": r.website,
                       "industry": r.industry, "location": r.location, "source": r.source,
                       "scraped_at": r.scraped_at} for r in rows], "page": page, "limit": limit}

@router.get("/stats")
async def company_stats(db: AsyncSession = Depends(get_db)):
    total = (await db.execute(select(func.count()).select_from(Company))).scalar()
    by_source = (await db.execute(
        select(Company.source, func.count()).group_by(Company.source)
    )).all()
    return {"total": total, "by_source": {r[0]: r[1] for r in by_source}}
