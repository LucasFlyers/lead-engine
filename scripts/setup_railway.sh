#!/usr/bin/env bash
# ============================================================
# Railway CLI setup script
# Prerequisites: railway CLI installed (npm i -g @railway/cli)
# Run: bash scripts/setup_railway.sh
# ============================================================
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'; BOLD='\033[1m'
info()    { echo -e "${CYAN}ℹ ${NC}$*"; }
success() { echo -e "${GREEN}✓ ${NC}$*"; }
warn()    { echo -e "${YELLOW}⚠ ${NC}$*"; }
error()   { echo -e "${RED}✗ ${NC}$*"; exit 1; }

echo -e "\n${BOLD}Lead Engine — Railway Setup${NC}\n"

# ── Pre-flight ──────────────────────────────────────────────
command -v railway >/dev/null 2>&1 || error "Railway CLI not found. Install: npm i -g @railway/cli"
command -v git     >/dev/null 2>&1 || error "git not found"

railway whoami >/dev/null 2>&1 || { info "Not logged in — starting auth..."; railway login; }
success "Railway CLI authenticated"

# ── Project ─────────────────────────────────────────────────
info "Creating Railway project..."
railway init --name "lead-engine" 2>/dev/null || warn "Project may already exist — continuing"

PROJECT_ID=$(railway status --json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('projectId',''))" 2>/dev/null || echo "")
if [ -z "$PROJECT_ID" ]; then
  warn "Could not auto-detect project ID — check Railway dashboard"
else
  success "Project ID: $PROJECT_ID"
fi

# ── Prompt for secrets ──────────────────────────────────────
echo -e "\n${BOLD}Environment Variables${NC}"
echo "Enter your secrets (press Enter to skip any optional ones):"
echo ""

read -rp "  DATABASE_URL (Neon postgres://...): " DB_URL
read -rsp "  OPENAI_API_KEY (sk-...): " OPENAI_KEY; echo
read -rsp "  API_SECRET_KEY (random string): " API_KEY; echo

echo -e "\n${BOLD}Inbox 1 Configuration${NC}"
read -rp "  INBOX_1_EMAIL: " INBOX_EMAIL
read -rp "  INBOX_1_SMTP_HOST (e.g. smtp.gmail.com): " SMTP_HOST
read -rp "  INBOX_1_SMTP_PORT (587 or 465): " SMTP_PORT
read -rsp "  INBOX_1_SMTP_PASSWORD: " SMTP_PASS; echo
read -rp "  INBOX_1_IMAP_HOST (e.g. imap.gmail.com): " IMAP_HOST
SMTP_USER="${INBOX_EMAIL}"
SENDER_NAME=""
read -rp "  SENDER_NAME (Your Name): " SENDER_NAME

# ── Set shared variables on all services ────────────────────
SERVICES=(
  "backend-api"
  "lead-scraper-worker"
  "pain-signal-worker"
  "email-sender-worker"
  "reply-monitor-worker"
  "analytics-worker"
  "deliverability-worker"
)

info "Setting shared secrets across all services..."

for SVC in "${SERVICES[@]}"; do
  railway variables set \
    DATABASE_URL="$DB_URL" \
    OPENAI_API_KEY="$OPENAI_KEY" \
    API_SECRET_KEY="$API_KEY" \
    INBOX_COUNT="1" \
    INBOX_1_EMAIL="$INBOX_EMAIL" \
    INBOX_1_SMTP_HOST="$SMTP_HOST" \
    INBOX_1_SMTP_PORT="$SMTP_PORT" \
    INBOX_1_SMTP_USER="$SMTP_USER" \
    INBOX_1_SMTP_PASSWORD="$SMTP_PASS" \
    INBOX_1_IMAP_HOST="$IMAP_HOST" \
    INBOX_1_IMAP_PORT="993" \
    SENDER_NAME="$SENDER_NAME" \
    --service "$SVC" 2>/dev/null && success "$SVC — secrets set" || warn "$SVC — service not found yet (create manually in dashboard)"
done

# ── Run migration ───────────────────────────────────────────
echo -e "\n${BOLD}Database Migration${NC}"
if [ -n "$DB_URL" ]; then
  info "Running migration..."
  DATABASE_URL="$DB_URL" python3 scripts/migrate.py && success "Migration complete"
else
  warn "DATABASE_URL not set — skipping migration (run manually: DATABASE_URL=... python scripts/migrate.py)"
fi

# ── Summary ─────────────────────────────────────────────────
echo -e "\n${BOLD}${GREEN}Setup complete!${NC}\n"
echo "Next steps:"
echo "  1. Open Railway dashboard: railway open"
echo "  2. Connect your GitHub repo to each service"
echo "  3. Configure ALLOWED_ORIGINS on backend-api with your frontend URL"
echo "  4. Deploy: railway up --service backend-api"
echo "  5. Verify: python scripts/health_check.py --api https://your-api.railway.app"
echo ""
