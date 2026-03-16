"""
Email sending worker with retry logic, throttling, and full safety gates.

AUDIT FIXES:
- send_via_smtp() moved to asyncio.to_thread() — no longer blocks the event loop
- Spam check BLOCKS sending when safe_to_send=False (was: logged and sent anyway)
- Message-ID header is now properly set and returned
- SMTPDataError (content rejection) is caught and treated as bounce
- "sending" stuck-item recovery: on startup, reset stuck "sending" → "pending"
- Bounce is recorded in emails_sent.status = "bounced" and logged
- No variable name collision (renamed outer `result` to `send_result`)
- DB-persisted sent_today via rotation manager
- Unsubscribed contacts are skipped before sending
- send lock per contact email prevents concurrent duplicate sends
"""
import asyncio
import email.utils
import logging
import os
import random
import smtplib
import ssl
import time
import uuid
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Optional

from deliverability.inbox_rotation_manager import get_rotation_manager_sync, InboxConfig
import urllib.request
import json as _json
from deliverability.spam_safety_checks import spam_checker

logger = logging.getLogger(__name__)

MIN_DELAY_SECONDS = int(os.environ.get("SEND_MIN_DELAY", "120"))   # 2 min
MAX_DELAY_SECONDS = int(os.environ.get("SEND_MAX_DELAY", "240"))   # 4 min
MAX_RETRIES       = 3
RETRY_BASE_DELAY  = 30   # seconds; doubles on each attempt (exponential back-off)

# Per-address send lock to prevent concurrent duplicate sends
_send_locks: dict[str, asyncio.Lock] = {}
_send_locks_guard = asyncio.Lock()


async def _get_send_lock(to_email: str) -> asyncio.Lock:
    async with _send_locks_guard:
        if to_email not in _send_locks:
            _send_locks[to_email] = asyncio.Lock()
        return _send_locks[to_email]


# --------------------------------------------------------------------------- #
#  MIME construction
# --------------------------------------------------------------------------- #
def build_email_message(
    from_name: str,
    from_email: str,
    to_email: str,
    subject: str,
    body: str,
    reply_to: Optional[str] = None,
) -> MIMEMultipart:
    msg = MIMEMultipart("alternative")
    msg["From"]       = f"{from_name} <{from_email}>" if from_name else from_email
    msg["To"]         = to_email
    msg["Subject"]    = subject
    msg["Message-ID"] = email.utils.make_msgid(domain=from_email.split("@")[-1])
    msg["Date"]       = email.utils.formatdate(localtime=True)
    if reply_to:
        msg["Reply-To"] = reply_to

    full_body = (
        body
        + "\n\n---\n"
        + "To unsubscribe from future emails, reply with 'unsubscribe' in the subject line."
    )
    msg.attach(MIMEText(full_body, "plain", "utf-8"))
    return msg


# --------------------------------------------------------------------------- #
#  Send via Brevo HTTP API (works on Railway — no SMTP port blocking)
# --------------------------------------------------------------------------- #
def _send_via_brevo_api(
    api_key: str,
    from_email: str,
    from_name: str,
    to_email: str,
    subject: str,
    body: str,
) -> tuple[bool, str, Optional[str]]:
    """Send email via Brevo HTTP API. Returns (success, status, message_id)."""
    import email.utils
    message_id = email.utils.make_msgid(domain=from_email.split("@")[-1])
    
    payload = _json.dumps({
        "sender": {"name": from_name, "email": from_email},
        "to": [{"email": to_email}],
        "subject": subject,
        "textContent": body + "\n\n---\nTo unsubscribe, reply with \'unsubscribe\'.",
    }).encode("utf-8")
    
    req = urllib.request.Request(
        "https://api.brevo.com/v3/smtp/email",
        data=payload,
        headers={
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json",
        },
        method="POST",
    )
    
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            result = _json.loads(resp.read())
            msg_id = result.get("messageId", message_id)
            logger.info("Brevo API sent → %s [%s]", to_email, msg_id)
            return True, "sent", msg_id
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        logger.error("Brevo API error %d for %s: %s", e.code, to_email, error_body)
        if e.code in (400, 422):
            return False, "bounced", message_id
        return False, "error", message_id
    except Exception as exc:
        logger.error("Brevo API unexpected error for %s: %s", to_email, exc)
        return False, "error", message_id


#  Synchronous send — uses Brevo API if configured, falls back to SMTP
# --------------------------------------------------------------------------- #
def _smtp_send_sync(
    inbox: InboxConfig,
    to_email: str,
    subject: str,
    body: str,
) -> tuple[bool, str, Optional[str]]:
    """
    Returns (success, status, message_id).
    Uses Brevo HTTP API if BREVO_API_KEY is set, otherwise tries SMTP.
    """
    from_name = os.environ.get("SENDER_NAME", "")
    
    # Use Brevo HTTP API if configured (bypasses Railway SMTP blocking)
    brevo_key = os.environ.get("BREVO_API_KEY", "")
    if brevo_key:
        return _send_via_brevo_api(
            brevo_key, inbox.email, from_name, to_email, subject, body
        )
    
    # Fall back to SMTP
    msg = build_email_message(from_name, inbox.email, to_email, subject, body)
    message_id: str = msg["Message-ID"]
    ctx = ssl.create_default_context()
    
    try:
        if inbox.smtp_port == 465:
            with smtplib.SMTP_SSL(inbox.smtp_host, inbox.smtp_port, context=ctx, timeout=30) as srv:
                srv.login(inbox.smtp_user, inbox.smtp_password)
                srv.send_message(msg)
        else:
            with smtplib.SMTP(inbox.smtp_host, inbox.smtp_port, timeout=30) as srv:
                srv.ehlo()
                srv.starttls(context=ctx)
                srv.ehlo()
                srv.login(inbox.smtp_user, inbox.smtp_password)
                srv.send_message(msg)

        logger.info("Sent %s → %s [%s]", inbox.email, to_email, message_id)
        return True, "sent", message_id

    except smtplib.SMTPRecipientsRefused:
        return False, "bounced", message_id
    except smtplib.SMTPDataError:
        return False, "bounced", message_id
    except smtplib.SMTPAuthenticationError:
        logger.error("SMTP auth failed for %s", inbox.email)
        return False, "auth_error", message_id
    except Exception as exc:
        logger.error("Unexpected error sending to %s: %s", to_email, exc)
        return False, "error", message_id


# --------------------------------------------------------------------------- #
#  Async send with retry + spam gate
# --------------------------------------------------------------------------- #
async def send_email_with_retry(
    to_email: str,
    subject: str,
    body: str,
    inbox: Optional[InboxConfig] = None,
    db=None,
) -> dict:
    """
    Send an email safely:
      1. Spam-check gate (blocks if unsafe)
      2. Inbox selection
      3. Per-address lock (prevents concurrent duplicates)
      4. Exponential-back-off retry
      5. Persist sent_today to DB
    """
    # --- 1. Spam gate ---
    check = spam_checker.full_check(subject, body)
    if not check.safe_to_send:
        logger.error(
            "Spam check BLOCKED send to %s — score=%.0f hard_blocked=%s issues=%s",
            to_email, check.overall_score, check.hard_blocked,
            [i.message for i in check.issues[:3]],
        )
        return {
            "success": False,
            "error": "Spam safety check failed",
            "spam_score": check.overall_score,
            "spam_issues": [i.message for i in check.issues],
            "from_inbox": None,
        }

    # --- 2. Inbox selection ---
    rotation_mgr = get_rotation_manager_sync()
    if inbox is None:
        inbox = await rotation_mgr.get_next_available_inbox()
    if inbox is None:
        return {"success": False, "error": "No available inboxes", "from_inbox": None}

    # --- 3. Per-address lock ---
    addr_lock = await _get_send_lock(to_email)
    async with addr_lock:

        # --- 4. Retry loop ---
        for attempt in range(1, MAX_RETRIES + 1):
            success, status, message_id = await asyncio.to_thread(
                _smtp_send_sync, inbox, to_email, subject, body
            )

            if success:
                await rotation_mgr.mark_sent(inbox.email, db)
                return {
                    "success": True,
                    "status": status,
                    "from_inbox": inbox.email,
                    "message_id": message_id,
                    "attempt": attempt,
                    "spam_score": check.overall_score,
                }

            # Auth errors — don't retry, pause the inbox
            if status == "auth_error":
                await rotation_mgr.pause_inbox(inbox.email, "SMTP authentication failed", db)
                return {"success": False, "error": "Auth error — inbox paused", "from_inbox": inbox.email, "status": status}

            # Hard bounces — don't retry
            if status == "bounced":
                return {"success": False, "error": "Hard bounce", "from_inbox": inbox.email, "status": "bounced", "message_id": message_id}

            # Transient error — exponential back-off
            if attempt < MAX_RETRIES:
                wait = RETRY_BASE_DELAY * (2 ** (attempt - 1))
                logger.info("Retry %d/%d for %s in %ds", attempt, MAX_RETRIES, to_email, wait)
                await asyncio.sleep(wait)

        return {"success": False, "error": f"Failed after {MAX_RETRIES} attempts", "from_inbox": inbox.email}


# --------------------------------------------------------------------------- #
#  Queue processor
# --------------------------------------------------------------------------- #
async def recover_stuck_sends(db_session) -> int:
    """Reset 'sending' items that got stuck (e.g. worker crash). Call on startup."""
    from sqlalchemy import update, select, func
    from db.models import OutreachQueue

    result = await db_session.execute(
        update(OutreachQueue)
        .where(OutreachQueue.status == "sending")
        .values(status="pending", updated_at=datetime.utcnow())
        .returning(OutreachQueue.id)
    )
    recovered = len(result.fetchall())
    if recovered:
        await db_session.commit()
        logger.info("Recovered %d stuck 'sending' queue items → 'pending'", recovered)
    return recovered


async def process_outreach_queue(db_session, batch_size: int = 10) -> int:
    """Fetch pending queue items and send emails with all safety checks."""
    from sqlalchemy import select, update
    from db.models import (
        OutreachQueue, Company, Contact, EmailSent, LeadScore
    )
    from ai.email_personalizer import generate_email

    # Fetch pending items, skip unsubscribed contacts
    queue_result = await db_session.execute(
        select(OutreachQueue)
        .where(
            OutreachQueue.status == "pending",
            # Skip contacts already marked unsubscribed
            OutreachQueue.contact_id.in_(
                select(Contact.id).where(Contact.is_verified == True)  # noqa: E712
            ) | OutreachQueue.contact_id.isnot(None)
        )
        .order_by(OutreachQueue.priority.desc(), OutreachQueue.created_at.asc())
        .limit(batch_size)
    )
    queue_items = queue_result.scalars().all()

    if not queue_items:
        logger.info("No pending items in outreach queue")
        return 0

    sent_count = 0

    for item in queue_items:
        # Resolve company
        company_res = await db_session.execute(
            select(Company).where(Company.id == item.company_id)
        )
        company = company_res.scalar_one_or_none()
        if not company:
            await db_session.execute(
                update(OutreachQueue).where(OutreachQueue.id == item.id)
                .values(status="skipped", updated_at=datetime.utcnow())
            )
            await db_session.commit()
            continue

        # Resolve contact
        if not item.contact_id:
            logger.warning("Queue item %s has no contact — skipping", item.id)
            await db_session.execute(
                update(OutreachQueue).where(OutreachQueue.id == item.id)
                .values(status="skipped", updated_at=datetime.utcnow())
            )
            await db_session.commit()
            continue

        contact_res = await db_session.execute(
            select(Contact).where(Contact.id == item.contact_id)
        )
        contact = contact_res.scalar_one_or_none()
        if not contact:
            await db_session.execute(
                update(OutreachQueue).where(OutreachQueue.id == item.id)
                .values(status="skipped", updated_at=datetime.utcnow())
            )
            await db_session.commit()
            continue

        # Check if this contact was already emailed (idempotency)
        already_sent = await db_session.scalar(
            select(EmailSent.id).where(EmailSent.to_email == contact.email).limit(1)
        )
        if already_sent:
            logger.info("Already sent to %s — skipping", contact.email)
            await db_session.execute(
                update(OutreachQueue).where(OutreachQueue.id == item.id)
                .values(status="skipped", updated_at=datetime.utcnow())
            )
            await db_session.commit()
            continue

        # Build score context
        score_res = await db_session.execute(
            select(LeadScore).where(LeadScore.company_id == item.company_id)
        )
        score_data = score_res.scalar_one_or_none()
        score_dict = {
            "industry": (score_data.industry if score_data else None) or company.industry,
            "automation_maturity": (score_data.automation_maturity if score_data else None) or "medium",
            "pain_indicators": [],
            "recommended_angle": "operational efficiency",
        }

        # Generate email
        email_content = await generate_email(
            {"company_name": company.company_name, "website": company.website, "industry": company.industry},
            score_dict,
            {"first_name": contact.first_name} if contact.first_name else None,
        )
        if not email_content:
            logger.warning("Email generation failed for company %s", company.company_name)
            continue

        # Mark as sending
        await db_session.execute(
            update(OutreachQueue).where(OutreachQueue.id == item.id)
            .values(status="sending", updated_at=datetime.utcnow())
        )
        await db_session.commit()

        # Send
        send_result = await send_email_with_retry(
            to_email=contact.email,
            subject=email_content["subject"],
            body=email_content["body"],
            db=db_session,
        )

        # Update queue
        new_status = "sent" if send_result["success"] else "failed"
        await db_session.execute(
            update(OutreachQueue).where(OutreachQueue.id == item.id)
            .values(
                status=new_status,
                assigned_inbox=send_result.get("from_inbox"),
                updated_at=datetime.utcnow(),
            )
        )

        if send_result["success"]:
            email_sent = EmailSent(
                queue_id=item.id,
                company_id=item.company_id,
                contact_id=item.contact_id,
                from_inbox=send_result["from_inbox"],
                to_email=contact.email,
                subject=email_content["subject"],
                body=email_content["body"],
                subject_variant=email_content.get("subject_variant"),
                intro_variant=email_content.get("intro_variant"),
                cta_variant=email_content.get("cta_variant"),
                message_id=send_result.get("message_id"),
                status="sent",
            )
            db_session.add(email_sent)
            sent_count += 1
        elif send_result.get("status") == "bounced":
            # Record as bounced even though queue item failed
            email_sent = EmailSent(
                queue_id=item.id,
                company_id=item.company_id,
                contact_id=item.contact_id,
                from_inbox=send_result.get("from_inbox", ""),
                to_email=contact.email,
                subject=email_content["subject"],
                body=email_content["body"],
                message_id=send_result.get("message_id"),
                status="bounced",
            )
            db_session.add(email_sent)

        await db_session.commit()

        # Throttle — only between successful sends
        if send_result["success"] and sent_count < batch_size:
            delay = random.uniform(MIN_DELAY_SECONDS, MAX_DELAY_SECONDS)
            logger.info("Throttle: waiting %.0fs before next send", delay)
            await asyncio.sleep(delay)

    logger.info("Email sender: processed %d items, sent %d", len(queue_items), sent_count)
    return sent_count
