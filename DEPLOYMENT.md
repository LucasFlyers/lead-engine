# Lead Engine — Railway Deployment Guide

> **Stack**: FastAPI · Next.js · Neon PostgreSQL · Railway  
> **Services**: 8 (1 API + 1 frontend + 6 workers)  
> **Estimated deploy time**: 20–30 minutes

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        Railway Project                           │
│                                                                  │
│  ┌──────────────┐    ┌─────────────────────────────────────┐    │
│  │   frontend   │───▶│           backend-api               │    │
│  │  (Next.js)   │    │          (FastAPI + Uvicorn)         │    │
│  │   Port 3000  │    │            Port 8000                 │    │
│  └──────────────┘    └──────────────┬──────────────────────┘    │
│                                     │                            │
│  ┌──────────────────────────────────▼──────────────────────┐    │
│  │                    Neon PostgreSQL                       │    │
│  │              (Serverless, auto-scaling)                  │    │
│  └──────────────────────────────────┬──────────────────────┘    │
│                                     │                            │
│  ┌──────────────┐  ┌────────────────┴┐  ┌───────────────────┐   │
│  │ lead-scraper │  │ pain-signal     │  │  email-sender     │   │
│  │   worker     │  │   worker        │  │    worker         │   │
│  │  (6h cycle)  │  │  (8h cycle)     │  │  (30m cycle)      │   │
│  └──────────────┘  └─────────────────┘  └───────────────────┘   │
│                                                                  │
│  ┌──────────────┐  ┌─────────────────┐  ┌───────────────────┐   │
│  │reply-monitor │  │   analytics     │  │ deliverability    │   │
│  │   worker     │  │    worker       │  │    worker         │   │
│  │  (30m cycle) │  │   (1h + 2am)    │  │   (60m cycle)     │   │
│  └──────────────┘  └─────────────────┘  └───────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

### Worker Responsibilities

| Service | Dockerfile | Schedule | Purpose |
|---------|-----------|----------|---------|
| `backend-api` | `Dockerfile` | Always on | REST API + health endpoint |
| `frontend-dashboard` | `Dockerfile` | Always on | Next.js dashboard |
| `lead-scraper-worker` | `Dockerfile.worker` | Every 6h | Clutch, Google Maps, directories |
| `pain-signal-worker` | `Dockerfile.worker` | Every 8h | Reddit, HN, forums, G2 |
| `email-sender-worker` | `Dockerfile.worker-lite` | Every 30m | SMTP outreach queue |
| `reply-monitor-worker` | `Dockerfile.worker-lite` | Every 30m | IMAP reply detection |
| `analytics-worker` | `Dockerfile.worker-lite` | Every 1h + 2am | Campaign metrics |
| `deliverability-worker` | `Dockerfile.worker-lite` | Every 60m | Bounce/spam/warmup checks |

> **Note**: `Dockerfile.worker` includes Playwright (Chromium) for browser-based scraping.  
> `Dockerfile.worker-lite` is ~400MB smaller — no browser installed.

---

## Prerequisites

```bash
# 1. Install Railway CLI
npm install -g @railway/cli

# 2. Install Railway CLI auth
railway login

# 3. Verify Python + pip (for running migration locally)
python3 --version   # needs 3.11+
```

---

## Step 1 — Create Neon Database

1. Go to **[neon.tech](https://neon.tech)** → New project → name it `lead-engine`
2. Select region closest to your Railway deployment (e.g. **US East**)
3. Go to **Dashboard → Connection Details**
4. Set **Connection type** to **Pooled connection** (PgBouncer)
5. Copy the connection string — it looks like:
   ```
   postgresql://user:password@ep-xxx.us-east-2.aws.neon.tech/neondb?sslmode=require
   ```
6. **Save this** — you'll need it in Step 4

> ⚠️ Use the **pooled** connection string for the app, not the direct one.  
> Workers are long-running processes — pooling prevents connection exhaustion.

---

## Step 2 — Create Railway Project

```bash
# From the project root
cd lead-engine
railway init
# → Name: lead-engine
# → Environment: production
```

Or via dashboard: **[railway.app/new](https://railway.app/new)** → Empty project

---

## Step 3 — Connect GitHub Repository

1. Push your code to GitHub if not already:
   ```bash
   git init && git add . && git commit -m "Initial commit"
   git remote add origin https://github.com/your-org/lead-engine.git
   git push -u origin main
   ```
2. In Railway dashboard: **New Service → GitHub Repo → Select your repo**
3. Railway will detect `railway.toml` and auto-configure all 8 services

---

## Step 4 — Configure Environment Variables

### Option A: Automated (recommended)

```bash
# Interactive setup script — sets all variables across all services
bash scripts/setup_railway.sh
```

### Option B: Manual via Railway dashboard

Set these on **every service** (use Railway's "Shared Variables" feature):

#### Required — All Services
| Variable | Example | Notes |
|----------|---------|-------|
| `DATABASE_URL` | `postgresql://user:pass@ep-xxx...` | Neon pooled URL |
| `OPENAI_API_KEY` | `sk-proj-...` | GPT-4o-mini for scoring + emails |
| `API_SECRET_KEY` | `(random 40 chars)` | Dashboard auth key |

#### Required — Email Workers Only
| Variable | Example | Notes |
|----------|---------|-------|
| `INBOX_COUNT` | `1` | Number of sending inboxes |
| `INBOX_1_EMAIL` | `outreach@yourdomain.com` | Sending address |
| `INBOX_1_SMTP_HOST` | `smtp.gmail.com` | SMTP server |
| `INBOX_1_SMTP_PORT` | `587` | 587=STARTTLS, 465=SSL |
| `INBOX_1_SMTP_USER` | `outreach@yourdomain.com` | Usually same as email |
| `INBOX_1_SMTP_PASSWORD` | `xxxx xxxx xxxx xxxx` | Gmail: use App Password |
| `INBOX_1_IMAP_HOST` | `imap.gmail.com` | IMAP server |
| `INBOX_1_IMAP_PORT` | `993` | Always 993 for SSL |
| `INBOX_1_WARMUP_WEEK` | `1` | Start at 1, increases automatically |
| `SENDER_NAME` | `Alex Johnson` | Display name in From: header |

#### Required — Frontend Only
| Variable | Example | Notes |
|----------|---------|-------|
| `NEXT_PUBLIC_API_URL` | `https://backend-api-xxx.up.railway.app/api/v1` | API URL for client |

#### Required — Backend API Only
| Variable | Example | Notes |
|----------|---------|-------|
| `ALLOWED_ORIGINS` | `https://frontend-xxx.up.railway.app` | CORS whitelist |
| `ALLOWED_HOSTS` | `backend-api-xxx.up.railway.app` | Trusted hosts |

Generate a secure API key:
```bash
python3 -c "import secrets; print(secrets.token_urlsafe(40))"
```

---

## Step 5 — Run Database Migration

```bash
# From project root
DATABASE_URL="postgresql://user:password@ep-xxx..." python3 scripts/migrate.py

# Expected output:
# [INFO] Database connection OK
# [INFO] Applying 42 schema statements...
# [INFO] Migration complete: 38 applied, 4 skipped/idempotent
```

The migration is **idempotent** — safe to re-run on every deploy.

---

## Step 6 — Deploy Backend API

```bash
railway up --service backend-api
```

Verify it's live:
```bash
curl https://your-api.up.railway.app/health
# {"status":"ok","database":"connected","env":"production"}
```

---

## Step 7 — Deploy Workers

Deploy workers in this order (scrapers before senders):

```bash
railway up --service lead-scraper-worker
railway up --service pain-signal-worker
railway up --service email-sender-worker
railway up --service reply-monitor-worker
railway up --service analytics-worker
railway up --service deliverability-worker
```

Or deploy all at once:
```bash
for svc in lead-scraper-worker pain-signal-worker email-sender-worker \
           reply-monitor-worker analytics-worker deliverability-worker; do
  railway up --service "$svc" &
done
wait
echo "All workers deployed"
```

---

## Step 8 — Deploy Frontend

First, set the API URL:
```bash
railway variables set \
  NEXT_PUBLIC_API_URL="https://backend-api-xxx.up.railway.app/api/v1" \
  --service frontend-dashboard
```

Then deploy:
```bash
railway up --service frontend-dashboard
```

Update backend CORS after getting frontend URL:
```bash
railway variables set \
  ALLOWED_ORIGINS="https://frontend-dashboard-xxx.up.railway.app" \
  --service backend-api
railway redeploy --service backend-api
```

---

## Step 9 — Verify System Health

```bash
# Run all checks
API_URL=https://your-api.up.railway.app \
API_SECRET_KEY=your-secret-key \
python3 scripts/health_check.py

# Expected output:
# Lead Engine — System Health Check
# ✓ Api Health             12ms  db=connected
# ✓ Leads Stats            24ms
# ✓ Campaign Summary       18ms
# ✓ Inbox Status           15ms
# ✓ Activity Feed          11ms
# All 5 checks passed ✓
```

---

## Step 10 — Configure Custom Domain (optional)

1. Railway dashboard → your service → **Settings → Networking → Custom Domain**
2. Add your domain (e.g. `app.yourdomain.com`)
3. Add the CNAME record at your DNS provider
4. Update `ALLOWED_ORIGINS` and `ALLOWED_HOSTS` on backend-api

---

## Gmail Setup for SMTP/IMAP

Gmail requires an **App Password** (not your account password).

1. Enable 2-Factor Authentication on the Gmail account
2. Go to **Google Account → Security → App Passwords**
3. Create a new app password → name it "Lead Engine"
4. Copy the 16-character password (format: `xxxx xxxx xxxx xxxx`)
5. Use that as `INBOX_1_SMTP_PASSWORD`

SMTP settings for Gmail:
```
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587           (STARTTLS — recommended)
IMAP_HOST=imap.gmail.com
IMAP_PORT=993
```

For other providers:
| Provider | SMTP Host | SMTP Port | IMAP Host | IMAP Port |
|----------|-----------|-----------|-----------|-----------|
| Outlook/Hotmail | smtp-mail.outlook.com | 587 | outlook.office365.com | 993 |
| Yahoo Mail | smtp.mail.yahoo.com | 587 | imap.mail.yahoo.com | 993 |
| Zoho Mail | smtp.zoho.com | 587 | imap.zoho.com | 993 |
| Custom / Postfix | your-mail-server.com | 587 | your-mail-server.com | 993 |
| SendGrid | smtp.sendgrid.net | 587 | — | — |

---

## Multiple Inboxes

Add additional inboxes by incrementing the number:

```bash
railway variables set \
  INBOX_COUNT=3 \
  INBOX_2_EMAIL=outreach2@yourdomain.com \
  INBOX_2_SMTP_HOST=smtp.gmail.com \
  INBOX_2_SMTP_PORT=587 \
  INBOX_2_SMTP_USER=outreach2@yourdomain.com \
  INBOX_2_SMTP_PASSWORD="xxxx xxxx xxxx xxxx" \
  INBOX_2_IMAP_HOST=imap.gmail.com \
  INBOX_2_IMAP_PORT=993 \
  INBOX_2_WARMUP_WEEK=1 \
  INBOX_3_EMAIL=outreach3@yourdomain.com \
  --service email-sender-worker
# repeat for reply-monitor-worker and deliverability-worker
```

---

## Warmup Schedule

The deliverability worker automatically tracks and enforces warmup limits per inbox.
Daily send limits by week:

| Week | Daily Limit | Notes |
|------|-------------|-------|
| 1 | 8 | Start here for new inboxes |
| 2 | 15 | |
| 3 | 25 | |
| 4 | 35 | |
| 5 | 45 | |
| 6 | 55 | |
| 7+ | 60 | Fully warmed |

Set `INBOX_X_WARMUP_WEEK=1` for all new inboxes.
The deliverability worker updates this automatically based on inbox age.

---

## Logs

```bash
# Stream live logs per service
railway logs --service backend-api
railway logs --service email-sender-worker
railway logs --service lead-scraper-worker

# All logs are JSON in production — filter with jq:
railway logs --service email-sender-worker | jq 'select(.level == "ERROR")'
railway logs --service lead-scraper-worker | jq '{ts, msg, level}'
```

---

## Environment Variable Quick Reference

```bash
# Generate API secret key
python3 -c "import secrets; print(secrets.token_urlsafe(40))"

# Check all set variables for a service
railway variables --service backend-api

# Set a single variable
railway variables set LOG_LEVEL=DEBUG --service backend-api

# Restart a service after env changes
railway redeploy --service backend-api
```

---

## Troubleshooting

### API returns 401 Unauthorized
- Set `X-API-Key: your-api-secret-key` header in all requests
- Or temporarily set `API_SECRET_KEY=""` to disable auth during debugging

### Workers aren't sending emails
1. Check `EMAIL_INTERVAL_MINUTES` — default 30m, may not have triggered yet
2. Force a one-shot run: `railway variables set RUN_ONCE=true --service email-sender-worker` then redeploy
3. Check logs: `railway logs --service email-sender-worker`
4. Verify inbox config: `GET /api/v1/inbox/status`

### Database connection errors
1. Ensure you're using the **pooled** Neon connection string
2. Add `?sslmode=require` to the connection string
3. Run connectivity check: `DATABASE_URL=... python3 scripts/migrate.py --check`

### SMTP authentication failures
- Gmail: ensure App Password is used, not account password
- Check the inbox is not paused: `GET /api/v1/inbox/status`
- SMTP errors auto-pause the inbox — check `pause_reason` in response

### Frontend can't reach API
1. Verify `NEXT_PUBLIC_API_URL` is set correctly on `frontend-dashboard`
2. Verify `ALLOWED_ORIGINS` includes the frontend URL on `backend-api`
3. Redeploy both services after changing these variables

### Scraper hitting rate limits
- Increase `SCRAPE_INTERVAL_HOURS` (try 12h or 24h)
- Playwright workers automatically add delays between requests
- Clutch/Google Maps may require proxies for high-volume scraping

---

## Cost Estimate (Railway + Neon)

| Service | Railway Plan | Monthly Cost |
|---------|-------------|-------------|
| backend-api | Hobby ($5 credit) | ~$5–10 |
| frontend-dashboard | Hobby | ~$2–5 |
| lead-scraper-worker | Hobby | ~$3–8 |
| pain-signal-worker | Hobby | ~$2–5 |
| email-sender-worker | Hobby | ~$2–5 |
| reply-monitor-worker | Hobby | ~$1–3 |
| analytics-worker | Hobby | ~$1–3 |
| deliverability-worker | Hobby | ~$1–3 |
| Neon PostgreSQL | Free tier | $0 (up to 0.5GB) |
| **Total** | | **~$17–42/mo** |

> Workers sleep between cycles — Railway only bills for active compute.
> Neon free tier includes 500MB storage and 190 compute hours/month.

