"""
IMAP inbox monitor for reply detection.

AUDIT FIXES:
- fetch_unread_emails() runs in asyncio.to_thread() — no longer blocks event loop
- Reply matching uses In-Reply-To / References headers, not from_email == to_email
- Unsubscribed contacts are now actually written to DB (is_unsubscribed column)
- Duplicate reply guard: skip if response with same message_id already exists
- IMAP connection timeout added (30s)
- process_reply no longer crashes if email_sent is None (orphan replies stored without company_id)
- Email address extracted with email.utils.parseaddr (handles "Name <addr>" format)
"""
import asyncio
import email
import email.utils
import imaplib
import logging
from datetime import datetime, timedelta
from typing import Optional

from ..ai.email_personalizer import classify_response
from ..deliverability.inbox_rotation_manager import get_rotation_manager_sync

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = int(__import__("os").environ.get("IMAP_POLL_INTERVAL", "300"))


# --------------------------------------------------------------------------- #
#  Synchronous IMAP fetch — runs in thread pool
# --------------------------------------------------------------------------- #
def _imap_fetch_sync(
    imap_host: str,
    imap_port: int,
    username: str,
    password: str,
    since_hours: int = 48,
) -> list[dict]:
    """Fetch unseen emails. Runs synchronously inside asyncio.to_thread()."""
    messages: list[dict] = []

    try:
        imap = imaplib.IMAP4_SSL(imap_host, imap_port)
        imap.socket().settimeout(30)
        imap.login(username, password)
        imap.select("INBOX", readonly=False)

        since_date = (datetime.utcnow() - timedelta(hours=since_hours)).strftime("%d-%b-%Y")
        _, nums = imap.search(None, f"UNSEEN SINCE {since_date}")

        for num in (nums[0].split() if nums[0] else []):
            try:
                _, msg_data = imap.fetch(num, "(RFC822)")
                if not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                msg = email.message_from_bytes(raw)

                # Extract plain text body
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        ct = part.get_content_type()
                        cd = str(part.get("Content-Disposition", ""))
                        if ct == "text/plain" and "attachment" not in cd:
                            charset = part.get_content_charset() or "utf-8"
                            body = part.get_payload(decode=True).decode(charset, errors="replace")
                            break
                else:
                    charset = msg.get_content_charset() or "utf-8"
                    payload = msg.get_payload(decode=True)
                    if payload:
                        body = payload.decode(charset, errors="replace")

                # Parse From address safely
                raw_from = msg.get("From", "")
                _, from_addr = email.utils.parseaddr(raw_from)
                from_addr = from_addr.lower().strip()

                messages.append({
                    "from_raw": raw_from,
                    "from": from_addr,
                    "subject": msg.get("Subject", ""),
                    "body": body[:2000],
                    "received_at": msg.get("Date", ""),
                    "message_id": (msg.get("Message-ID") or "").strip(),
                    "in_reply_to": (msg.get("In-Reply-To") or "").strip(),
                    "references": (msg.get("References") or "").strip(),
                })

                imap.store(num, "+FLAGS", "\\Seen")
            except Exception as exc:
                logger.debug("Error parsing message %s: %s", num, exc)

        imap.logout()
    except imaplib.IMAP4.error as exc:
        logger.error("IMAP auth/protocol error for %s@%s: %s", username, imap_host, exc)
    except OSError as exc:
        logger.error("IMAP connection error for %s@%s: %s", username, imap_host, exc)
    except Exception as exc:
        logger.error("Unexpected IMAP error for %s@%s: %s", username, imap_host, exc)

    return messages


# --------------------------------------------------------------------------- #
#  Reply matching & processing
# --------------------------------------------------------------------------- #
async def _find_matching_sent_email(reply: dict, db):
    """
    Match a reply to an outbound EmailSent record.
    Strategy (in order):
      1. In-Reply-To header matches EmailSent.message_id
      2. References header contains a known message_id
      3. from_email matches EmailSent.to_email (fallback)
    """
    from sqlalchemy import select
    from ..db.models import EmailSent

    in_reply_to = reply.get("in_reply_to", "")
    references  = reply.get("references", "")
    from_addr   = reply.get("from", "")

    # Strategy 1 & 2: header-based matching (most reliable)
    if in_reply_to or references:
        search_ids = set()
        if in_reply_to:
            search_ids.add(in_reply_to)
        if references:
            search_ids.update(references.split())

        for mid in search_ids:
            res = await db.execute(
                select(EmailSent).where(EmailSent.message_id == mid).limit(1)
            )
            sent = res.scalar_one_or_none()
            if sent:
                return sent

    # Strategy 3: email address fallback
    if from_addr:
        res = await db.execute(
            select(EmailSent)
            .where(EmailSent.to_email == from_addr)
            .order_by(EmailSent.sent_at.desc())
            .limit(1)
        )
        return res.scalar_one_or_none()

    return None


async def process_reply(reply: dict, db_session) -> Optional[dict]:
    """Process a single inbound reply email."""
    from sqlalchemy import select, update
    from ..db.models import EmailSent, Response, Contact

    from_addr   = reply.get("from", "")
    message_id  = reply.get("message_id", "")

    # Duplicate guard — skip if already processed this message_id
    if message_id:
        exists = await db_session.scalar(
            select(Response.id).where(Response.message_id == message_id).limit(1)
        )
        if exists:
            logger.debug("Reply %s already processed — skipping", message_id)
            return None

    # Find the matching sent email
    sent_email = await _find_matching_sent_email(reply, db_session)

    # Classify
    classification = await classify_response(
        reply.get("subject", ""),
        reply.get("body", ""),
    )

    response = Response(
        email_sent_id  = sent_email.id if sent_email else None,
        company_id     = sent_email.company_id if sent_email else None,
        from_email     = from_addr,
        subject        = reply.get("subject"),
        body           = reply.get("body", "")[:2000],
        message_id     = message_id or None,
        classification = classification.get("classification"),
        ai_confidence  = classification.get("confidence"),
        ai_reasoning   = classification.get("reasoning"),
        received_at    = datetime.utcnow(),
    )
    db_session.add(response)

    # Handle unsubscribe — actually write to DB
    if classification.get("classification") == "unsubscribe":
        logger.info("Unsubscribe from %s — marking contact", from_addr)
        await db_session.execute(
            update(Contact)
            .where(Contact.email == from_addr)
            .values(is_unsubscribed=True, updated_at=datetime.utcnow())
        )

    await db_session.commit()
    logger.info("Reply from %s → classified: %s (conf=%.2f)",
                from_addr, classification.get("classification"), classification.get("confidence", 0))
    return classification


# --------------------------------------------------------------------------- #
#  Monitor orchestration
# --------------------------------------------------------------------------- #
async def monitor_all_inboxes(db_session) -> int:
    """Poll all inboxes for new replies using thread pool for IMAP I/O."""
    mgr = get_rotation_manager_sync()
    total = 0

    for inbox in mgr.inboxes:
        try:
            replies = await asyncio.to_thread(
                _imap_fetch_sync,
                inbox.imap_host,
                inbox.imap_port,
                inbox.smtp_user,
                inbox.smtp_password,
            )
            logger.info("Inbox %s: %d new replies", inbox.email, len(replies))
            for reply in replies:
                await process_reply(reply, db_session)
                total += 1
        except Exception as exc:
            logger.error("Error monitoring inbox %s: %s", inbox.email, exc)

    return total


async def run_inbox_monitor_loop(db_session_factory) -> None:
    """Continuously monitor inboxes on a poll interval."""
    logger.info("Inbox monitor started (poll every %ds)", POLL_INTERVAL_SECONDS)
    while True:
        try:
            async with db_session_factory() as session:
                count = await monitor_all_inboxes(session)
                if count:
                    logger.info("Inbox monitor: %d replies processed", count)
        except Exception as exc:
            logger.error("Inbox monitor loop error: %s", exc)
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
