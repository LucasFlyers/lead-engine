"""Domain warmup scheduler to gradually increase sending volume."""
import logging
from datetime import datetime, timedelta
from typing import Optional

logger = logging.getLogger(__name__)

# Warmup schedule: week -> (min_per_day, max_per_day)
WARMUP_SCHEDULE = {
    1: (5, 10),
    2: (10, 20),
    3: (20, 30),
    4: (30, 40),
    5: (40, 50),
    6: (50, 60),
}

DEFAULT_DAILY_LIMIT = 40


def get_daily_limit_for_week(week: int) -> int:
    """Get the daily send limit for a warmup week."""
    schedule = WARMUP_SCHEDULE.get(week, WARMUP_SCHEDULE[max(WARMUP_SCHEDULE.keys())])
    # Use the midpoint of the range
    return (schedule[0] + schedule[1]) // 2


def calculate_warmup_week(created_at: datetime) -> int:
    """Calculate which warmup week an inbox is in based on creation date."""
    days_active = (datetime.utcnow() - created_at).days
    week = (days_active // 7) + 1
    return min(week, max(WARMUP_SCHEDULE.keys()))


class DomainWarmupScheduler:
    """Manages domain warmup schedules."""

    def __init__(self):
        self.warmup_records: dict[str, dict] = {}

    def register_inbox(self, inbox_email: str, created_at: Optional[datetime] = None):
        """Register an inbox for warmup tracking."""
        if created_at is None:
            created_at = datetime.utcnow()

        self.warmup_records[inbox_email] = {
            "email": inbox_email,
            "created_at": created_at,
            "week": calculate_warmup_week(created_at),
        }

    def get_daily_limit(self, inbox_email: str) -> int:
        """Get the current daily limit for an inbox."""
        record = self.warmup_records.get(inbox_email)
        if not record:
            return DEFAULT_DAILY_LIMIT

        week = calculate_warmup_week(record["created_at"])
        return get_daily_limit_for_week(week)

    def get_warmup_status(self, inbox_email: str) -> dict:
        """Get warmup status for an inbox."""
        record = self.warmup_records.get(inbox_email)
        if not record:
            return {"week": 1, "daily_limit": 10, "fully_warmed": False}

        week = calculate_warmup_week(record["created_at"])
        daily_limit = get_daily_limit_for_week(week)
        max_week = max(WARMUP_SCHEDULE.keys())

        return {
            "email": inbox_email,
            "week": week,
            "daily_limit": daily_limit,
            "range": WARMUP_SCHEDULE.get(week, (40, 50)),
            "fully_warmed": week >= max_week,
            "days_until_next_week": 7 - (datetime.utcnow() - record["created_at"]).days % 7,
        }

    def get_all_status(self) -> list[dict]:
        """Get warmup status for all registered inboxes."""
        return [self.get_warmup_status(email) for email in self.warmup_records]
