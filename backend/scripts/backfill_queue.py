"""
Backfill outreach queue for leads that have no contacts or queue items.
Run this once to fix existing leads that were saved without emails.
"""
import asyncio
import os
import re
import ssl

async def main():
    import asyncpg
    
    raw = os.environ["DATABASE_URL"]
    dsn = raw.split("?")[0].replace("postgres://", "postgresql://")
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    conn = await asyncpg.connect(dsn, ssl=ctx)
    
    # Find leads with no contacts
    leads = await conn.fetch("""
        SELECT c.id, c.company_name, c.domain, c.website
        FROM companies c
        LEFT JOIN contacts ct ON ct.company_id = c.id
        WHERE ct.id IS NULL
        AND c.is_duplicate = FALSE
        LIMIT 50
    """)
    
    print(f"Found {len(leads)} leads with no contacts")
    
    added = 0
    for lead in leads:
        company_id = lead["id"]
        company_name = lead["company_name"]
        domain = lead["domain"]
        website = lead["website"]
        
        # Skip job board URLs
        job_boards = ["remoteok", "weworkremotely", "himalayas", "remotive", "linkedin", "github"]
        if website and any(jb in (website or "") for jb in job_boards):
            domain = None
            website = None
        
        # Guess domain from company name if needed
        if not domain:
            clean = re.sub(r"[^a-z0-9]", "", company_name.lower())
            if clean:
                domain = f"{clean}.com"
        
        if not domain:
            continue
        
        # Create contact and queue item
        for prefix in ["info", "hello", "contact"]:
            email = f"{prefix}@{domain}"
            try:
                contact_id = await conn.fetchval("""
                    INSERT INTO contacts (company_id, email, discovery_method, is_verified)
                    VALUES ($1, $2, 'pattern_fallback', FALSE)
                    ON CONFLICT (email) DO NOTHING
                    RETURNING id
                """, company_id, email)
                
                if contact_id:
                    await conn.execute("""
                        INSERT INTO outreach_queue (company_id, contact_id, status, priority)
                        VALUES ($1, $2, 'pending', 5)
                    """, company_id, contact_id)
                    print(f"Added {email} for {company_name}")
                    added += 1
                    break
            except Exception as e:
                print(f"Error for {company_name}: {e}")
    
    print(f"\nBackfilled {added} queue items")
    await conn.close()

asyncio.run(main())
