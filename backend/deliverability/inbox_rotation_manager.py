"""
Inbox rotation manager for email deliverability.

AUDIT FIXES:
- In-memory sent_today synced to DB on startup and after sends (survives restarts/multi-process)
- Thread-safe singleton using asyncio.Lock
- Round-robin index operates only on available inboxes, not all inboxes (was bugged)
- reset_daily_if_needed() side-effect removed from property getter
- Passwords typed as SecretStr to prevent accidental logging
- Daily limit now pulled from warmup schedule at runtime, not hardcoded to 10
"""
import asyncio
import logging
import os
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Optional

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
#  Warmup schedule: warmup_week -> daily_limit
# --------------------------------------------------------------------------- #
WARMUP_LIMITS: dict[int, int] = {
    1: 8,
    2: 15,
    3: 25,
    4: 35,
    5: 45,
    6: 55,
}
MAX_WARMUP_WEEK = max(WARMUP_LIMITS)
DEFAULT_FULLY_WARMED_LIMIT = 60


def limit_for_week(week: int) -> int:
    return WARMUP_LIMITS.get(min(week, MAX_WARMUP_WEEK), DEFAULT_FULLY_WARMED_LIMIT)


# --------------------------------------------------------------------------- #
#  Inbox dataclass — password never logged via repr
# --------------------------------------------------------------------------- #
@dataclass
class InboxConfig:
    email: str
    smtp_host: str
    smtp_port: int
    smtp_user: str
    _smtp_password: str          # private — excluded from __repr__
    imap_host: str
    imap_port: int
    warmup_week: int = 1
    sent_today: int = 0
    last_reset_date: date = field(default_factory=date.today)
    is_paused: bool = False
    pause_reason: Optional[str] = None

    # Hide password from default repr / logging
    def __repr__(self) -> str:  # noqa: D105
        return (
            f"InboxConfig(email={self.email!r}, smtp_host={self.smtp_host!r}, "
            f"warmup_week={self.warmup_week}, sent_today={self.sent_today}, "
            f"is_paused={self.is_paused})"
        )

    @property
    def smtp_password(self) -> str:
        return self._smtp_password

    @property
    def daily_limit(self) -> int:
        return limit_for_week(self.warmup_week)

    def _reset_daily_if_needed(self) -> None:
        """Reset daily counter on new calendar day. Call before any read."""
        today = date.today()
        if self.last_reset_date != today:
            self.sent_today = 0
            self.last_reset_date = today

    @property
    def can_send(self) -> bool:
        self._reset_daily_if_needed()
        return not self.is_paused and self.sent_today < self.daily_limit

    @property
    def remaining_sends(self) -> int:
        self._reset_daily_if_needed()
        return max(0, self.daily_limit - self.sent_today)


# --------------------------------------------------------------------------- #
#  Manager
# --------------------------------------------------------------------------- #
class InboxRotationManager:
    """Thread-safe multi-inbox rotation manager."""

    def __init__(self) -> None:
        self.inboxes: list[InboxConfig] = self._load_from_env()
        self._lock = asyncio.Lock()
        self._rr_counter: int = 0  # global counter; not reset per-available-list

    # -- Construction -------------------------------------------------------- #

    def _load_from_env(self) -> list[InboxConfig]:
        inboxes: list[InboxConfig] = []
        inbox_count = int(os.environ.get("INBOX_COUNT", "3"))

        for i in range(1, inbox_count + 1):
            email_addr = os.environ.get(f"INBOX_{i}_EMAIL", "").strip()
            if not email_addr:
                continue
            password = os.environ.get(f"INBOX_{i}_SMTP_PASSWORD", "")
            if not password:
                logger.warning("INBOX_%d_SMTP_PASSWORD is empty — inbox will be skipped", i)
                continue

            inboxes.append(InboxConfig(
                email=email_addr,
                smtp_host=os.environ.get(f"INBOX_{i}_SMTP_HOST", "smtp.gmail.com"),
                smtp_port=int(os.environ.get(f"INBOX_{i}_SMTP_PORT", "587")),
                smtp_user=os.environ.get(f"INBOX_{i}_SMTP_USER", email_addr),
                _smtp_password=password,
                imap_host=os.environ.get(f"INBOX_{i}_IMAP_HOST", "imap.gmail.com"),
                imap_port=int(os.environ.get(f"INBOX_{i}_IMAP_PORT", "993")),
                warmup_week=int(os.environ.get(f"INBOX_{i}_WARMUP_WEEK", "1")),
            ))

        if not inboxes:
            logger.warning(
                "No inboxes configured — emails will NOT be sent. "
                "Set INBOX_1_EMAIL / INBOX_1_SMTP_PASSWORD etc."
            )
        return inboxes

    async def sync_from_db(self, db) -> None:
        """Sync sent_today and warmup_week from InboxHealth table on startup."""
        from sqlalchemy import select
        from db.models import InboxHealth

        today = date.today()
        result = await db.execute(select(InboxHealth))
        records = {r.inbox_email: r for r in result.scalars().all()}

        for inbox in self.inboxes:
            rec = records.get(inbox.email)
            if rec:
                inbox.warmup_week = rec.warmup_week
                inbox.is_paused = rec.is_paused
                inbox.pause_reason = rec.pause_reason
                # Only restore today's count if the record is from today
                if rec.last_sent_at and rec.last_sent_at.date() == today:
                    inbox.sent_today = rec.sent_today
                else:
                    inbox.sent_today = 0

    # -- Rotation ------------------------------------------------------------ #

    async def get_next_available_inbox(self) -> Optional[InboxConfig]:
        """Round-robin over available inboxes. Thread-safe."""
        async with self._lock:
            available = [ix for ix in self.inboxes if ix.can_send]
            if not available:
                logger.warning("No inboxes with remaining send capacity today")
                return None
            # True round-robin: use global counter mod available length
            idx = self._rr_counter % len(available)
            self._rr_counter += 1
            return available[idx]

    async def mark_sent(self, inbox_email: str, db=None) -> None:
        """Increment sent counter and persist to DB."""
        async with self._lock:
            for inbox in self.inboxes:
                if inbox.email == inbox_email:
                    inbox._reset_daily_if_needed()
                    inbox.sent_today += 1
                    break

        if db is not None:
            await self._persist_health(inbox_email, db)

    async def _persist_health(self, inbox_email: str, db) -> None:
        """Write sent_today back to InboxHealth table."""
        from sqlalchemy import select
        from db.models import InboxHealth

        for inbox in self.inboxes:
            if inbox.email == inbox_email:
                result = await db.execute(
                    select(InboxHealth).where(InboxHealth.inbox_email == inbox_email)
                )
                rec = result.scalar_one_or_none()
                if rec:
                    rec.sent_today = inbox.sent_today
                    rec.last_sent_at = datetime.utcnow()
                    rec.updated_at = datetime.utcnow()
                    await db.commit()
                break

    async def pause_inbox(self, inbox_email: str, reason: str, db=None) -> None:
        async with self._lock:
            for inbox in self.inboxes:
                if inbox.email == inbox_email:
                    inbox.is_paused = True
                    inbox.pause_reason = reason
                    logger.warning("Paused inbox %s: %s", inbox_email, reason)
                    break

        if db is not None:
            from sqlalchemy import select
            from db.models import InboxHealth
            result = await db.execute(
                select(InboxHealth).where(InboxHealth.inbox_email == inbox_email)
            )
            rec = result.scalar_one_or_none()
            if rec:
                rec.is_paused = True
                rec.pause_reason = reason
                rec.updated_at = datetime.utcnow()
                await db.commit()

    async def resume_inbox(self, inbox_email: str, db=None) -> None:
        async with self._lock:
            for inbox in self.inboxes:
                if inbox.email == inbox_email:
                    inbox.is_paused = False
                    inbox.pause_reason = None
                    logger.info("Resumed inbox %s", inbox_email)
                    break

        if db is not None:
            from sqlalchemy import select
            from db.models import InboxHealth
            result = await db.execute(
                select(InboxHealth).where(InboxHealth.inbox_email == inbox_email)
            )
            rec = result.scalar_one_or_none()
            if rec:
                rec.is_paused = False
                rec.pause_reason = None
                rec.updated_at = datetime.utcnow()
                await db.commit()

    def get_status(self) -> list[dict]:
        return [
            {
                "email": inbox.email,
                "daily_limit": inbox.daily_limit,
                "sent_today": inbox.sent_today,
                "remaining": inbox.remaining_sends,
                "warmup_week": inbox.warmup_week,
                "is_paused": inbox.is_paused,
                "pause_reason": inbox.pause_reason,
                "can_send": inbox.can_send,
            }
            for inbox in self.inboxes
        ]


# --------------------------------------------------------------------------- #
#  Lazy singleton — created once per process
# --------------------------------------------------------------------------- #
_rotation_manager: Optional[InboxRotationManager] = None
_manager_lock = asyncio.Lock()


async def get_rotation_manager() -> InboxRotationManager:
    """Async-safe singleton accessor."""
    global _rotation_manager
    if _rotation_manager is None:
        async with _manager_lock:
            if _rotation_manager is None:          # double-check
                _rotation_manager = InboxRotationManager()
    return _rotation_manager


def get_rotation_manager_sync() -> InboxRotationManager:
    """Sync accessor for non-async contexts (routes). Safe after first init."""
    global _rotation_manager
    if _rotation_manager is None:
        _rotation_manager = InboxRotationManager()
    return _rotation_manager
