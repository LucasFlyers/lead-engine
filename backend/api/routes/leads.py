"""Leads API routes."""
from datetime import datetime
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ...db.database import get_db
from ...db.models import Company, Contact, LeadScore, OutreachQueue

router = APIRouter(prefix="/leads", tags=["leads"])


@router.get("/")
async def list_leads(
    page: int = Query(1, ge=1),
    per_page: int = Query(50, ge=1, le=200),
    min_score: int = Query(0, ge=0, le=10),
    industry: Optional[str] = None,
    source: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """List all leads with optional filtering."""
    query = (
        select(Company, LeadScore)
        .outerjoin(LeadScore, LeadScore.company_id == Company.id)
        .where(Company.is_duplicate == False)
    )

    if min_score > 0:
        query = query.where(LeadScore.score >= min_score)
    if industry:
        query = query.where(Company.industry.ilike(f"%{industry}%"))
    if source:
        query = query.where(Company.source == source)

    # Count
    count_result = await db.scalar(
        select(func.count()).select_from(query.subquery())
    )

    # Paginate
    query = query.order_by(desc(LeadScore.score), desc(Company.scraped_at))
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    rows = result.all()

    leads = []
    for company, score in rows:
        leads.append({
            "id": str(company.id),
            "company_name": company.company_name,
            "website": company.website,
            "industry": company.industry,
            "location": company.location,
            "source": company.source,
            "score": score.score if score else None,
            "automation_maturity": score.automation_maturity if score else None,
            "scraped_at": company.scraped_at.isoformat() if company.scraped_at else None,
        })

    return {
        "leads": leads,
        "total": count_result or 0,
        "page": page,
        "per_page": per_page,
    }


@router.get("/{lead_id}")
async def get_lead(lead_id: UUID, db: AsyncSession = Depends(get_db)):
    """Get a single lead by ID."""
    result = await db.execute(
        select(Company, LeadScore)
        .outerjoin(LeadScore, LeadScore.company_id == Company.id)
        .where(Company.id == lead_id)
    )
    row = result.first()
    if not row:
        raise HTTPException(status_code=404, detail="Lead not found")

    company, score = row

    contacts_result = await db.execute(
        select(Contact).where(Contact.company_id == lead_id)
    )
    contacts = contacts_result.scalars().all()

    return {
        "id": str(company.id),
        "company_name": company.company_name,
        "website": company.website,
        "domain": company.domain,
        "industry": company.industry,
        "location": company.location,
        "source": company.source,
        "description": company.description,
        "score": score.score if score else None,
        "reasoning": score.reasoning if score else None,
        "automation_maturity": score.automation_maturity if score else None,
        "contacts": [
            {"email": c.email, "role": c.role, "method": c.discovery_method}
            for c in contacts
        ],
        "scraped_at": company.scraped_at.isoformat() if company.scraped_at else None,
    }


@router.get("/stats/summary")
async def lead_stats(db: AsyncSession = Depends(get_db)):
    """Get lead pipeline statistics."""
    total = await db.scalar(select(func.count(Company.id)).where(Company.is_duplicate == False))
    scored = await db.scalar(select(func.count(LeadScore.id)).where(LeadScore.score >= 7))
    in_queue = await db.scalar(
        select(func.count(OutreachQueue.id)).where(OutreachQueue.status == "pending")
    )

    industry_result = await db.execute(
        select(Company.industry, func.count(Company.id).label("count"))
        .where(Company.is_duplicate == False, Company.industry.isnot(None))
        .group_by(Company.industry)
        .order_by(desc("count"))
        .limit(5)
    )
    top_industries = [{"industry": r.industry, "count": r.count} for r in industry_result.all()]

    source_result = await db.execute(
        select(Company.source, func.count(Company.id).label("count"))
        .group_by(Company.source)
        .order_by(desc("count"))
    )
    by_source = [{"source": r.source, "count": r.count} for r in source_result.all()]

    return {
        "total_leads": total or 0,
        "qualified_leads": scored or 0,
        "in_queue": in_queue or 0,
        "top_industries": top_industries,
        "by_source": by_source,
    }
