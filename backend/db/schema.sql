-- ============================================================
-- Autonomous Lead Engine - Database Schema
-- ============================================================

CREATE EXTENSION IF NOT EXISTS "uuid-ossp";
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE companies (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_name    TEXT NOT NULL,
    website         TEXT,
    domain          TEXT,
    industry        TEXT,
    location        TEXT,
    description     TEXT,
    employee_count  INTEGER,
    source          TEXT NOT NULL,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    is_duplicate    BOOLEAN NOT NULL DEFAULT FALSE,
    canonical_id    UUID REFERENCES companies(id),
    UNIQUE(domain)
);

CREATE INDEX idx_companies_domain    ON companies(domain);
CREATE INDEX idx_companies_name_trgm ON companies USING gin(company_name gin_trgm_ops);
CREATE INDEX idx_companies_source    ON companies(source);
CREATE INDEX idx_companies_industry  ON companies(industry);

CREATE TABLE contacts (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    email           TEXT NOT NULL,
    first_name      TEXT,
    last_name       TEXT,
    role            TEXT,
    discovery_method TEXT,
    is_verified     BOOLEAN NOT NULL DEFAULT FALSE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(email)
);

CREATE INDEX idx_contacts_company ON contacts(company_id);

CREATE TABLE pain_signals (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    source          TEXT NOT NULL,
    source_url      TEXT,
    author          TEXT,
    content         TEXT NOT NULL,
    keywords_matched TEXT[],
    industry        TEXT,
    problem_desc    TEXT,
    automation_opp  TEXT,
    lead_potential  INTEGER CHECK(lead_potential BETWEEN 1 AND 10),
    company_id      UUID REFERENCES companies(id),
    processed       BOOLEAN NOT NULL DEFAULT FALSE,
    scraped_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_pain_signals_score     ON pain_signals(lead_potential);
CREATE INDEX idx_pain_signals_processed ON pain_signals(processed);

CREATE TABLE lead_scores (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    score           INTEGER NOT NULL CHECK(score BETWEEN 1 AND 10),
    industry        TEXT,
    automation_maturity TEXT,
    reasoning       TEXT,
    model_used      TEXT,
    scored_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(company_id)
);

CREATE TABLE outreach_queue (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    company_id      UUID NOT NULL REFERENCES companies(id) ON DELETE CASCADE,
    contact_id      UUID REFERENCES contacts(id),
    status          TEXT NOT NULL DEFAULT 'pending',
    priority        INTEGER NOT NULL DEFAULT 5,
    assigned_inbox  TEXT,
    scheduled_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_queue_status    ON outreach_queue(status);
CREATE INDEX idx_queue_scheduled ON outreach_queue(scheduled_at);

CREATE TABLE emails_sent (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    queue_id        UUID REFERENCES outreach_queue(id),
    company_id      UUID NOT NULL REFERENCES companies(id),
    contact_id      UUID REFERENCES contacts(id),
    from_inbox      TEXT NOT NULL,
    to_email        TEXT NOT NULL,
    subject         TEXT NOT NULL,
    body            TEXT NOT NULL,
    subject_variant TEXT,
    intro_variant   TEXT,
    cta_variant     TEXT,
    message_id      TEXT,
    sent_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    opened          BOOLEAN NOT NULL DEFAULT FALSE,
    opened_at       TIMESTAMPTZ,
    status          TEXT NOT NULL DEFAULT 'sent'
);

CREATE INDEX idx_emails_company  ON emails_sent(company_id);
CREATE INDEX idx_emails_inbox    ON emails_sent(from_inbox);
CREATE INDEX idx_emails_sent_at  ON emails_sent(sent_at);

CREATE TABLE responses (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    email_sent_id   UUID REFERENCES emails_sent(id),
    company_id      UUID NOT NULL REFERENCES companies(id),
    from_email      TEXT NOT NULL,
    subject         TEXT,
    body            TEXT,
    classification  TEXT,
    ai_confidence   FLOAT,
    ai_reasoning    TEXT,
    received_at     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    actioned        BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE INDEX idx_responses_classification ON responses(classification);
CREATE INDEX idx_responses_received_at    ON responses(received_at DESC);

CREATE TABLE campaign_metrics (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    date            DATE NOT NULL,
    inbox           TEXT,
    emails_sent     INTEGER NOT NULL DEFAULT 0,
    bounces         INTEGER NOT NULL DEFAULT 0,
    spam_complaints INTEGER NOT NULL DEFAULT 0,
    replies         INTEGER NOT NULL DEFAULT 0,
    interested      INTEGER NOT NULL DEFAULT 0,
    not_interested  INTEGER NOT NULL DEFAULT 0,
    unsubscribes    INTEGER NOT NULL DEFAULT 0,
    reply_rate      FLOAT,
    positive_rate   FLOAT,
    UNIQUE(date, inbox)
);

CREATE TABLE inbox_health (
    id              UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    inbox_email     TEXT NOT NULL,
    domain          TEXT NOT NULL,
    warmup_week     INTEGER NOT NULL DEFAULT 1,
    daily_limit     INTEGER NOT NULL DEFAULT 10,
    sent_today      INTEGER NOT NULL DEFAULT 0,
    bounce_rate     FLOAT NOT NULL DEFAULT 0,
    spam_rate       FLOAT NOT NULL DEFAULT 0,
    reply_rate      FLOAT NOT NULL DEFAULT 0,
    is_paused       BOOLEAN NOT NULL DEFAULT FALSE,
    pause_reason    TEXT,
    last_sent_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE(inbox_email)
);

CREATE TABLE system_events (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    event_type  TEXT NOT NULL,
    entity_type TEXT,
    entity_id   UUID,
    message     TEXT NOT NULL,
    metadata    JSONB,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX idx_system_events_created_at ON system_events(created_at DESC);

CREATE TABLE dedup_log (
    id          UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
    original_id UUID NOT NULL,
    duplicate_id UUID NOT NULL,
    match_method TEXT NOT NULL,
    similarity  FLOAT,
    resolved_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ============================================================
-- AUDIT ADDITIONS
-- ============================================================

-- Contacts: unsubscribe flag + updated_at
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS is_unsubscribed BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE contacts ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();
CREATE INDEX IF NOT EXISTS idx_contacts_unsubscribed ON contacts(is_unsubscribed) WHERE is_unsubscribed = TRUE;

-- Responses: message_id for dedup
ALTER TABLE responses ADD COLUMN IF NOT EXISTS message_id TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_responses_message_id ON responses(message_id) WHERE message_id IS NOT NULL;

-- InboxHealth: last_sent_at for daily reset tracking
ALTER TABLE inbox_health ADD COLUMN IF NOT EXISTS last_sent_at TIMESTAMPTZ;

-- OutreachQueue: updated_at index for monitoring
CREATE INDEX IF NOT EXISTS idx_queue_updated ON outreach_queue(updated_at);

-- Emails sent: composite index for idempotency check
CREATE INDEX IF NOT EXISTS idx_emails_to_email ON emails_sent(to_email);

-- System events: metadata index for filtering
CREATE INDEX IF NOT EXISTS idx_system_events_type_date ON system_events(event_type, created_at DESC);

-- Performance: partial index for active queue items
CREATE INDEX IF NOT EXISTS idx_queue_pending ON outreach_queue(priority DESC, created_at ASC)
    WHERE status = 'pending';
