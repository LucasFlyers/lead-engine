"""SQLAlchemy ORM models — updated to match audited schema."""
import uuid
from datetime import datetime, date
from typing import Optional, List

from sqlalchemy import (
    String, Text, Integer, Float, Boolean, DateTime, Date,
    ForeignKey, UniqueConstraint, Index
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY

from .database import Base


class Company(Base):
    __tablename__ = "companies"

    id:           Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_name: Mapped[str]            = mapped_column(Text, nullable=False)
    website:      Mapped[Optional[str]]  = mapped_column(Text)
    domain:       Mapped[Optional[str]]  = mapped_column(Text, unique=True)
    industry:     Mapped[Optional[str]]  = mapped_column(Text)
    location:     Mapped[Optional[str]]  = mapped_column(Text)
    description:  Mapped[Optional[str]]  = mapped_column(Text)
    employee_count: Mapped[Optional[int]]= mapped_column(Integer)
    source:       Mapped[str]            = mapped_column(Text, nullable=False)
    scraped_at:   Mapped[datetime]       = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at:   Mapped[datetime]       = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)
    is_duplicate: Mapped[bool]           = mapped_column(Boolean, default=False)
    canonical_id: Mapped[Optional[uuid.UUID]] = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))

    contacts:    Mapped[List["Contact"]]       = relationship("Contact",      back_populates="company", cascade="all, delete-orphan")
    lead_score:  Mapped[Optional["LeadScore"]] = relationship("LeadScore",    back_populates="company", uselist=False)
    pain_signals:Mapped[List["PainSignal"]]    = relationship("PainSignal",   back_populates="company")
    queue_items: Mapped[List["OutreachQueue"]] = relationship("OutreachQueue",back_populates="company")
    emails:      Mapped[List["EmailSent"]]     = relationship("EmailSent",    back_populates="company")
    responses:   Mapped[List["Response"]]      = relationship("Response",     back_populates="company")


class Contact(Base):
    __tablename__ = "contacts"

    id:               Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id:       Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False)
    email:            Mapped[str]           = mapped_column(Text, nullable=False, unique=True)
    first_name:       Mapped[Optional[str]] = mapped_column(Text)
    last_name:        Mapped[Optional[str]] = mapped_column(Text)
    role:             Mapped[Optional[str]] = mapped_column(Text)
    discovery_method: Mapped[Optional[str]] = mapped_column(Text)
    is_verified:      Mapped[bool]          = mapped_column(Boolean, default=False)
    is_unsubscribed:  Mapped[bool]          = mapped_column(Boolean, default=False)  # AUDIT: added
    created_at:       Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at:       Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    company: Mapped["Company"] = relationship("Company", back_populates="contacts")


class PainSignal(Base):
    __tablename__ = "pain_signals"

    id:              Mapped[uuid.UUID]          = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    source:          Mapped[str]                = mapped_column(Text, nullable=False)
    source_url:      Mapped[Optional[str]]      = mapped_column(Text)
    author:          Mapped[Optional[str]]      = mapped_column(Text)
    content:         Mapped[str]                = mapped_column(Text, nullable=False)
    keywords_matched:Mapped[Optional[List[str]]]= mapped_column(ARRAY(Text))
    industry:        Mapped[Optional[str]]      = mapped_column(Text)
    problem_desc:    Mapped[Optional[str]]      = mapped_column(Text)
    automation_opp:  Mapped[Optional[str]]      = mapped_column(Text)
    lead_potential:  Mapped[Optional[int]]      = mapped_column(Integer)
    company_id:      Mapped[Optional[uuid.UUID]]= mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    processed:       Mapped[bool]               = mapped_column(Boolean, default=False)
    scraped_at:      Mapped[datetime]           = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    company: Mapped[Optional["Company"]] = relationship("Company", back_populates="pain_signals")


class LeadScore(Base):
    __tablename__ = "lead_scores"

    id:                 Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id:         Mapped[uuid.UUID]     = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), unique=True)
    score:              Mapped[int]           = mapped_column(Integer, nullable=False)
    industry:           Mapped[Optional[str]] = mapped_column(Text)
    automation_maturity:Mapped[Optional[str]] = mapped_column(Text)
    reasoning:          Mapped[Optional[str]] = mapped_column(Text)
    model_used:         Mapped[Optional[str]] = mapped_column(Text)
    scored_at:          Mapped[datetime]      = mapped_column(DateTime(timezone=True), default=datetime.utcnow)

    company: Mapped["Company"] = relationship("Company", back_populates="lead_score")


class OutreachQueue(Base):
    __tablename__ = "outreach_queue"

    id:             Mapped[uuid.UUID]          = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id:     Mapped[uuid.UUID]          = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"))
    contact_id:     Mapped[Optional[uuid.UUID]]= mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id"))
    status:         Mapped[str]                = mapped_column(Text, default="pending")
    priority:       Mapped[int]                = mapped_column(Integer, default=5)
    assigned_inbox: Mapped[Optional[str]]      = mapped_column(Text)
    scheduled_at:   Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    created_at:     Mapped[datetime]           = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at:     Mapped[datetime]           = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)

    company: Mapped["Company"] = relationship("Company", back_populates="queue_items")


class EmailSent(Base):
    __tablename__ = "emails_sent"

    id:              Mapped[uuid.UUID]          = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    queue_id:        Mapped[Optional[uuid.UUID]]= mapped_column(UUID(as_uuid=True), ForeignKey("outreach_queue.id"))
    company_id:      Mapped[uuid.UUID]          = mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))
    contact_id:      Mapped[Optional[uuid.UUID]]= mapped_column(UUID(as_uuid=True), ForeignKey("contacts.id"))
    from_inbox:      Mapped[str]                = mapped_column(Text, nullable=False)
    to_email:        Mapped[str]                = mapped_column(Text, nullable=False)
    subject:         Mapped[str]                = mapped_column(Text, nullable=False)
    body:            Mapped[str]                = mapped_column(Text, nullable=False)
    subject_variant: Mapped[Optional[str]]      = mapped_column(Text)
    intro_variant:   Mapped[Optional[str]]      = mapped_column(Text)
    cta_variant:     Mapped[Optional[str]]      = mapped_column(Text)
    message_id:      Mapped[Optional[str]]      = mapped_column(Text)
    sent_at:         Mapped[datetime]           = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    opened:          Mapped[bool]               = mapped_column(Boolean, default=False)
    opened_at:       Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    status:          Mapped[str]                = mapped_column(Text, default="sent")  # sent|bounced|spam_complaint

    company: Mapped["Company"] = relationship("Company", back_populates="emails")
    response: Mapped[Optional["Response"]] = relationship("Response", back_populates="email_sent", uselist=False)


class Response(Base):
    __tablename__ = "responses"

    id:             Mapped[uuid.UUID]          = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email_sent_id:  Mapped[Optional[uuid.UUID]]= mapped_column(UUID(as_uuid=True), ForeignKey("emails_sent.id"))
    company_id:     Mapped[Optional[uuid.UUID]]= mapped_column(UUID(as_uuid=True), ForeignKey("companies.id"))  # nullable — orphan replies
    from_email:     Mapped[str]                = mapped_column(Text, nullable=False)
    subject:        Mapped[Optional[str]]      = mapped_column(Text)
    body:           Mapped[Optional[str]]      = mapped_column(Text)
    message_id:     Mapped[Optional[str]]      = mapped_column(Text)  # AUDIT: dedup
    classification: Mapped[Optional[str]]      = mapped_column(Text)
    ai_confidence:  Mapped[Optional[float]]    = mapped_column(Float)
    ai_reasoning:   Mapped[Optional[str]]      = mapped_column(Text)
    received_at:    Mapped[datetime]           = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    actioned:       Mapped[bool]               = mapped_column(Boolean, default=False)

    company:    Mapped[Optional["Company"]]    = relationship("Company", back_populates="responses")
    email_sent: Mapped[Optional["EmailSent"]]  = relationship("EmailSent", back_populates="response")


class CampaignMetrics(Base):
    __tablename__ = "campaign_metrics"

    id:              Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    date:            Mapped[date]           = mapped_column(Date, nullable=False)
    inbox:           Mapped[Optional[str]]  = mapped_column(Text)
    emails_sent:     Mapped[int]            = mapped_column(Integer, default=0)
    bounces:         Mapped[int]            = mapped_column(Integer, default=0)
    spam_complaints: Mapped[int]            = mapped_column(Integer, default=0)
    replies:         Mapped[int]            = mapped_column(Integer, default=0)
    interested:      Mapped[int]            = mapped_column(Integer, default=0)
    not_interested:  Mapped[int]            = mapped_column(Integer, default=0)
    unsubscribes:    Mapped[int]            = mapped_column(Integer, default=0)
    reply_rate:      Mapped[Optional[float]]= mapped_column(Float)
    positive_rate:   Mapped[Optional[float]]= mapped_column(Float)

    __table_args__ = (UniqueConstraint("date", "inbox"),)


class InboxHealth(Base):
    __tablename__ = "inbox_health"

    id:           Mapped[uuid.UUID]      = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    inbox_email:  Mapped[str]            = mapped_column(Text, nullable=False, unique=True)
    domain:       Mapped[str]            = mapped_column(Text, nullable=False)
    warmup_week:  Mapped[int]            = mapped_column(Integer, default=1)
    daily_limit:  Mapped[int]            = mapped_column(Integer, default=10)
    sent_today:   Mapped[int]            = mapped_column(Integer, default=0)
    bounce_rate:  Mapped[float]          = mapped_column(Float, default=0.0)
    spam_rate:    Mapped[float]          = mapped_column(Float, default=0.0)
    reply_rate:   Mapped[float]          = mapped_column(Float, default=0.0)
    is_paused:    Mapped[bool]           = mapped_column(Boolean, default=False)
    pause_reason: Mapped[Optional[str]]  = mapped_column(Text)
    last_sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))  # AUDIT: daily reset tracking
    created_at:   Mapped[datetime]       = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
    updated_at:   Mapped[datetime]       = mapped_column(DateTime(timezone=True), default=datetime.utcnow, onupdate=datetime.utcnow)


class SystemEvent(Base):
    __tablename__ = "system_events"

    id:          Mapped[uuid.UUID]          = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    event_type:  Mapped[str]               = mapped_column(Text, nullable=False)
    entity_type: Mapped[Optional[str]]     = mapped_column(Text)
    entity_id:   Mapped[Optional[uuid.UUID]]= mapped_column(UUID(as_uuid=True))
    message:     Mapped[str]               = mapped_column(Text, nullable=False)
    metadata:    Mapped[Optional[dict]]    = mapped_column(JSONB)
    created_at:  Mapped[datetime]          = mapped_column(DateTime(timezone=True), default=datetime.utcnow)
