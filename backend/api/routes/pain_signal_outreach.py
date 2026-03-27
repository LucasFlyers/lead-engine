"""Pain Signal Manual Outreach Queue — API routes."""
import logging
from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy import select, func, desc, or_, and_
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import (
    PainSignal,
    PainSignalOutreachQueue,
    SystemEvent,
    REVIEW_STATUSES,
    OUTREACH_STATUSES,
    OUTREACH_CHANNELS,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/pain-signal-outreach", tags=["pain_signal_outreach"])


# --------------------------------------------------------------------------- #
#  Helpers
# --------------------------------------------------------------------------- #

def _now() -> datetime:
    """UTC now — consistent across the module."""
    return datetime.now(timezone.utc)


def _has_contact(item: PainSignalOutreachQueue) -> bool:
    """True if at least one manual contact field has been filled in."""
    return bool(item.manual_contact_email or item.manual_contact_name)


# --------------------------------------------------------------------------- #
#  Pydantic schemas
# --------------------------------------------------------------------------- #

class OutreachItemUpdate(BaseModel):
    manual_company_name:     Optional[str] = None
    manual_contact_name:     Optional[str] = None
    manual_contact_role:     Optional[str] = None
    manual_contact_email:    Optional[str] = None
    manual_contact_phone:    Optional[str] = None
    manual_contact_linkedin: Optional[str] = None
    manual_website:          Optional[str] = None
    manual_notes:            Optional[str] = None
    review_status:           Optional[str] = None
    outreach_channel:        Optional[str] = None
    outreach_status:         Optional[str] = None

    @field_validator("review_status")
    @classmethod
    def validate_review_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in REVIEW_STATUSES:
            raise ValueError(
                f"review_status must be one of: {', '.join(sorted(REVIEW_STATUSES))}"
            )
        return v

    @field_validator("outreach_status")
    @classmethod
    def validate_outreach_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in OUTREACH_STATUSES:
            raise ValueError(
                f"outreach_status must be one of: {', '.join(sorted(OUTREACH_STATUSES))}"
            )
        return v

    @field_validator("outreach_channel")
    @classmethod
    def validate_outreach_channel(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in OUTREACH_CHANNELS:
            raise ValueError(
                f"outreach_channel must be one of: {', '.join(sorted(OUTREACH_CHANNELS))}"
            )
        return v


# --------------------------------------------------------------------------- #
#  Serialisers
# --------------------------------------------------------------------------- #

def _item_to_list_dict(item: PainSignalOutreachQueue) -> dict:
    """Serialise an outreach queue item for the list endpoint (no full AI text)."""
    return {
        "id":                 str(item.id),
        "pain_signal_id":     str(item.pain_signal_id),
        "source":             item.source,
        "source_url":         item.source_url,
        "author":             item.author,
        "industry":           item.industry,
        "problem_desc":       item.problem_desc,
        "automation_opp":     item.automation_opp,
        "lead_potential":     item.lead_potential,
        "target_contact_type": item.target_contact_type,
        "personalization_hook": item.personalization_hook,
        "suggested_subject":  item.suggested_subject,
        # Truncated previews — full text only in detail view
        "email_preview":      (item.suggested_email_message or "")[:120] or None,
        "dm_preview":         (item.suggested_dm_message or "")[:100] or None,
        "recommended_cta":    item.recommended_cta,
        # Manual fields visible in list (for quick scanning)
        "manual_company_name":  item.manual_company_name,
        "manual_contact_name":  item.manual_contact_name,
        "manual_contact_role":  item.manual_contact_role,
        "manual_contact_email": item.manual_contact_email,
        "has_contact":          _has_contact(item),
        # Workflow
        "review_status":    item.review_status,
        "outreach_channel": item.outreach_channel,
        "outreach_status":  item.outreach_status,
        # Timestamps
        "created_at":         item.created_at.isoformat() if item.created_at else None,
        "updated_at":         item.updated_at.isoformat() if item.updated_at else None,
        "reviewed_at":        item.reviewed_at.isoformat() if item.reviewed_at else None,
        "contact_found_at":   item.contact_found_at.isoformat() if item.contact_found_at else None,
        "outreach_marked_at": item.outreach_marked_at.isoformat() if item.outreach_marked_at else None,
    }


def _item_to_detail_dict(
    item: PainSignalOutreachQueue,
    pain_signal: Optional[PainSignal],
) -> dict:
    """Serialise a full outreach queue item for the detail endpoint."""
    base = _item_to_list_dict(item)
    # Overwrite previews with full content
    base.update({
        "email_preview":           None,
        "dm_preview":              None,
        "suggested_email_message": item.suggested_email_message,
        "suggested_dm_message":    item.suggested_dm_message,
        "ai_reasoning":            item.ai_reasoning,
        "message_model_used":      item.message_model_used,
        # Full manual fields
        "manual_contact_phone":    item.manual_contact_phone,
        "manual_contact_linkedin": item.manual_contact_linkedin,
        "manual_website":          item.manual_website,
        "manual_notes":            item.manual_notes,
    })
    if pain_signal:
        base["pain_signal"] = {
            "id":              str(pain_signal.id),
            "source":          pain_signal.source,
            "source_url":      pain_signal.source_url,
            "author":          pain_signal.author,
            "content":         pain_signal.content,
            "keywords_matched": pain_signal.keywords_matched,
            "industry":        pain_signal.industry,
            "problem_desc":    pain_signal.problem_desc,
            "automation_opp":  pain_signal.automation_opp,
            "lead_potential":  pain_signal.lead_potential,
            "scraped_at":      pain_signal.scraped_at.isoformat() if pain_signal.scraped_at else None,
        }
    return base


async def _log_event(
    db: AsyncSession,
    event_type: str,
    message: str,
    entity_id: Optional[UUID] = None,
    metadata: Optional[dict] = None,
) -> None:
    """Add a system event to the session — caller is responsible for commit."""
    db.add(SystemEvent(
        event_type=event_type,
        entity_type="pain_signal_outreach_queue",
        entity_id=entity_id,
        message=message,
        event_metadata=metadata or {},
    ))


# --------------------------------------------------------------------------- #
#  Routes
# --------------------------------------------------------------------------- #

@router.get("")
async def list_outreach_queue(
    page:           int           = Query(1, ge=1),
    per_page:       int           = Query(50, ge=1, le=200),
    review_status:  Optional[str] = None,
    outreach_status: Optional[str]= None,
    source:         Optional[str] = None,
    min_score:      float         = Query(0.0, ge=0, le=10),
    has_contact:    Optional[bool]= None,
    search:         Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    """
    List manual outreach queue items with filters.

    Filters are ANDed together. Sort: lead_potential DESC, created_at DESC.
    """
    # Validate enum filters early so callers get a clear error, not empty results
    if review_status and review_status not in REVIEW_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid review_status. Valid values: {sorted(REVIEW_STATUSES)}",
        )
    if outreach_status and outreach_status not in OUTREACH_STATUSES:
        raise HTTPException(
            status_code=422,
            detail=f"Invalid outreach_status. Valid values: {sorted(OUTREACH_STATUSES)}",
        )

    query = select(PainSignalOutreachQueue)

    if review_status:
        query = query.where(PainSignalOutreachQueue.review_status == review_status)
    if outreach_status:
        query = query.where(PainSignalOutreachQueue.outreach_status == outreach_status)
    if source:
        query = query.where(PainSignalOutreachQueue.source == source)
    if min_score > 0:
        query = query.where(PainSignalOutreachQueue.lead_potential >= min_score)

    if has_contact is True:
        query = query.where(
            or_(
                PainSignalOutreachQueue.manual_contact_email.isnot(None),
                PainSignalOutreachQueue.manual_contact_name.isnot(None),
            )
        )
    elif has_contact is False:
        # Both must be NULL — requires and_()
        query = query.where(
            and_(
                PainSignalOutreachQueue.manual_contact_email.is_(None),
                PainSignalOutreachQueue.manual_contact_name.is_(None),
            )
        )

    if search:
        term = f"%{search.strip()}%"
        query = query.where(
            or_(
                PainSignalOutreachQueue.problem_desc.ilike(term),
                PainSignalOutreachQueue.industry.ilike(term),
                PainSignalOutreachQueue.source_url.ilike(term),
                PainSignalOutreachQueue.manual_company_name.ilike(term),
                PainSignalOutreachQueue.manual_contact_name.ilike(term),
                PainSignalOutreachQueue.automation_opp.ilike(term),
            )
        )

    total = await db.scalar(select(func.count()).select_from(query.subquery()))
    query = (
        query
        .order_by(
            desc(PainSignalOutreachQueue.lead_potential),
            desc(PainSignalOutreachQueue.created_at),
        )
        .offset((page - 1) * per_page)
        .limit(per_page)
    )
    result = await db.execute(query)
    items = result.scalars().all()

    return {
        "items":    [_item_to_list_dict(i) for i in items],
        "total":    total or 0,
        "page":     page,
        "per_page": per_page,
    }


@router.get("/stats")
async def outreach_queue_stats(db: AsyncSession = Depends(get_db)):
    """Summary statistics for the manual outreach queue."""
    total = await db.scalar(select(func.count(PainSignalOutreachQueue.id)))

    by_review = await db.execute(
        select(
            PainSignalOutreachQueue.review_status,
            func.count(PainSignalOutreachQueue.id).label("count"),
        )
        .group_by(PainSignalOutreachQueue.review_status)
        .order_by(desc("count"))
    )
    by_outreach = await db.execute(
        select(
            PainSignalOutreachQueue.outreach_status,
            func.count(PainSignalOutreachQueue.id).label("count"),
        )
        .group_by(PainSignalOutreachQueue.outreach_status)
        .order_by(desc("count"))
    )

    # contacts_found = items where at least one contact field is filled
    # (matches _has_contact() logic used in list serialiser)
    contacts_found = await db.scalar(
        select(func.count(PainSignalOutreachQueue.id)).where(
            or_(
                PainSignalOutreachQueue.manual_contact_email.isnot(None),
                PainSignalOutreachQueue.manual_contact_name.isnot(None),
            )
        )
    )

    return {
        "total":           total or 0,
        "contacts_found":  contacts_found or 0,
        "by_review_status": [
            {"status": r.review_status, "count": r.count}
            for r in by_review.all()
        ],
        "by_outreach_status": [
            {"status": r.outreach_status, "count": r.count}
            for r in by_outreach.all()
        ],
    }


@router.get("/{item_id}")
async def get_outreach_item(item_id: UUID, db: AsyncSession = Depends(get_db)):
    """Full detail view for a single manual outreach queue item."""
    item = await db.get(PainSignalOutreachQueue, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Outreach queue item not found")

    pain_signal = await db.get(PainSignal, item.pain_signal_id)
    return _item_to_detail_dict(item, pain_signal)


@router.patch("/{item_id}")
async def update_outreach_item(
    item_id: UUID,
    payload: OutreachItemUpdate,
    db: AsyncSession = Depends(get_db),
):
    """
    Update manual fields and workflow state for an outreach queue item.

    Automatically sets timestamps on meaningful state transitions:
    - reviewed_at  when leaving 'unreviewed'
    - contact_found_at  when contact info is first added or status → contact_found
    - outreach_marked_at  when outreach_status → sent
    """
    item = await db.get(PainSignalOutreachQueue, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Outreach queue item not found")

    now = _now()
    prev_review   = item.review_status
    prev_outreach = item.outreach_status
    had_contact   = _has_contact(item)

    # Apply all provided fields
    update_data = payload.model_dump(exclude_unset=True)
    for field_name, value in update_data.items():
        setattr(item, field_name, value)

    # ---- Automatic timestamp transitions ----

    new_review   = update_data.get("review_status")
    new_outreach = update_data.get("outreach_status")

    # reviewed_at: first time status leaves 'unreviewed'
    if new_review and new_review != "unreviewed" and prev_review == "unreviewed":
        if not item.reviewed_at:
            item.reviewed_at = now

    # contact_found_at: first time contact info appears OR status explicitly set to contact_found
    now_has_contact = _has_contact(item)
    if now_has_contact and not had_contact and not item.contact_found_at:
        item.contact_found_at = now
    if new_review == "contact_found" and not item.contact_found_at:
        item.contact_found_at = now

    # outreach_marked_at: first time outreach_status transitions to 'sent'
    if new_outreach == "sent" and prev_outreach != "sent" and not item.outreach_marked_at:
        item.outreach_marked_at = now

    item.updated_at = now

    # ---- Build system event ----
    transitions: list[str] = []
    if new_review and new_review != prev_review:
        transitions.append(f"review_status: {prev_review} → {new_review}")
    if new_outreach and new_outreach != prev_outreach:
        transitions.append(f"outreach_status: {prev_outreach} → {new_outreach}")
    if now_has_contact and not had_contact:
        transitions.append("manual contact info added")

    if transitions:
        await _log_event(
            db,
            "pain_signal_outreach_updated",
            "; ".join(transitions),
            entity_id=item_id,
            metadata={
                "changes":        list(update_data.keys()),
                "review_status":  item.review_status,
                "outreach_status": item.outreach_status,
            },
        )

    await db.commit()
    pain_signal = await db.get(PainSignal, item.pain_signal_id)
    return _item_to_detail_dict(item, pain_signal)


@router.post("/{item_id}/regenerate-message")
async def regenerate_outreach_message(
    item_id: UUID,
    db: AsyncSession = Depends(get_db),
):
    """
    Regenerate AI outreach suggestions for an existing item.

    Uses the original pain signal content plus any manually saved company
    context. Existing manual contact / workflow fields are preserved.
    """
    item = await db.get(PainSignalOutreachQueue, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Outreach queue item not found")

    pain_signal = await db.get(PainSignal, item.pain_signal_id)
    if not pain_signal:
        raise HTTPException(status_code=404, detail="Linked pain signal not found")

    from ai.pain_signal_outreach_writer import generate_outreach_suggestions

    # Prefer pain_signal as source of truth; prepend manual company name if known
    content = pain_signal.content
    if item.manual_company_name:
        content = f"[Company: {item.manual_company_name}] " + content

    signal_dict = {
        "source":         pain_signal.source,
        "source_url":     pain_signal.source_url,
        "author":         pain_signal.author,
        "content":        content,
        "industry":       pain_signal.industry,
        "problem_desc":   pain_signal.problem_desc,
        "automation_opp": pain_signal.automation_opp,
        "lead_potential": pain_signal.lead_potential,
    }

    outreach_data = await generate_outreach_suggestions(signal_dict)
    if not outreach_data:
        raise HTTPException(
            status_code=502,
            detail="AI message generation failed — please try again",
        )

    for field_name, value in outreach_data.items():
        setattr(item, field_name, value)
    item.updated_at = _now()

    await _log_event(
        db,
        "pain_signal_outreach_regenerated",
        "Outreach message regenerated",
        entity_id=item_id,
        metadata={"model": outreach_data.get("message_model_used")},
    )
    await db.commit()
    await db.refresh(item)
    return _item_to_detail_dict(item, pain_signal)


@router.get("/{item_id}/copy-ready")
async def get_copy_ready(item_id: UUID, db: AsyncSession = Depends(get_db)):
    """
    Return a compact payload for quick copy/paste outreach use.

    Useful for building a clipboard-ready workflow without opening the full detail view.
    """
    item = await db.get(PainSignalOutreachQueue, item_id)
    if not item:
        raise HTTPException(status_code=404, detail="Outreach queue item not found")

    return {
        "subject":             item.suggested_subject,
        "email_message":       item.suggested_email_message,
        "dm_message":          item.suggested_dm_message,
        "personalization_hook": item.personalization_hook,
        "cta":                 item.recommended_cta,
        "source_url":          item.source_url,
    }
