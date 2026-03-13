"""Seed demo data for testing the dashboard."""
import asyncio
import os
import random
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

os.environ.setdefault("DATABASE_URL", "postgresql+asyncpg://postgres:postgres@localhost/leadengine")


async def seed():
    from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
    from backend.db.models import (
        Company, Contact, LeadScore, PainSignal, OutreachQueue,
        EmailSent, Response, CampaignMetrics, InboxHealth, SystemEvent
    )

    engine = create_async_engine(os.environ["DATABASE_URL"])
    SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with SessionLocal() as db:
        # Seed companies
        industries = ["Digital Marketing Agency", "IT Consulting", "E-commerce", "SaaS", "Accounting"]
        sources = ["clutch", "google_maps", "goodfirms", "pain_signal"]
        companies = []

        for i in range(20):
            company = Company(
                company_name=f"Company {i+1} {'LLC' if i % 2 == 0 else 'Inc'}",
                website=f"https://company{i+1}.com",
                domain=f"company{i+1}.com",
                industry=random.choice(industries),
                location=random.choice(["New York, NY", "San Francisco, CA", "Austin, TX", "Chicago, IL"]),
                source=random.choice(sources),
            )
            db.add(company)
            companies.append(company)

        await db.flush()

        for company in companies:
            score = LeadScore(
                company_id=company.id,
                score=random.randint(6, 10),
                industry=company.industry,
                automation_maturity=random.choice(["low", "medium", "high"]),
                reasoning="Company shows clear automation opportunity based on service mix.",
                model_used="gpt-4o-mini",
            )
            db.add(score)

            contact = Contact(
                company_id=company.id,
                email=f"hello@{company.domain}",
                first_name=random.choice(["Sarah", "Mike", "Emma", "James", "Lisa"]),
                discovery_method="contact_page",
            )
            db.add(contact)

        await db.flush()

        # Seed pain signals
        for i in range(10):
            ps = PainSignal(
                source=random.choice(["reddit", "hackernews", "g2", "capterra"]),
                source_url=f"https://reddit.com/r/entrepreneur/post{i}",
                content=f"We spend {random.randint(5, 20)} hours a week manually compiling reports in spreadsheets. There has to be a better way.",
                keywords_matched=["manually compiling", "spreadsheets", "hours"],
                industry=random.choice(industries),
                problem_desc="Manual report compilation taking excessive time",
                automation_opp="Automated reporting and data aggregation workflow",
                lead_potential=random.randint(7, 10),
                processed=True,
            )
            db.add(ps)

        # Seed inbox health
        for i in range(3):
            ih = InboxHealth(
                inbox_email=f"outreach{i+1}@yourdomain.com",
                domain="yourdomain.com",
                warmup_week=random.randint(1, 4),
                daily_limit=10 * (i + 1),
                sent_today=random.randint(0, 15),
                bounce_rate=round(random.uniform(0, 0.03), 4),
                spam_rate=round(random.uniform(0, 0.001), 4),
                reply_rate=round(random.uniform(0.02, 0.15), 4),
            )
            db.add(ih)

        # Seed campaign metrics for last 14 days
        for days_ago in range(14, 0, -1):
            d = (datetime.utcnow() - timedelta(days=days_ago)).date()
            sent = random.randint(10, 35)
            replies = random.randint(1, max(1, sent // 8))
            interested = random.randint(0, max(1, replies // 2))
            cm = CampaignMetrics(
                date=d,
                emails_sent=sent,
                replies=replies,
                interested=interested,
                bounces=random.randint(0, 2),
                reply_rate=round(replies / sent * 100, 2),
                positive_rate=round(interested / sent * 100, 2),
            )
            db.add(cm)

        # Seed system events
        event_types = [
            ("lead_scraped", "Scraped 12 companies from Clutch"),
            ("pain_detected", "Detected automation pain signal on Reddit"),
            ("email_sent", "Sent outreach email to hello@company5.com"),
            ("reply_received", "Received interested reply from hello@company2.com"),
            ("pipeline_complete", "Scraping pipeline completed: 8 new leads added"),
        ]
        for event_type, message in event_types:
            ev = SystemEvent(event_type=event_type, message=message)
            db.add(ev)

        await db.commit()
        print(f"✓ Seeded {len(companies)} companies, 10 pain signals, 3 inboxes, 14 days metrics")


if __name__ == "__main__":
    asyncio.run(seed())
