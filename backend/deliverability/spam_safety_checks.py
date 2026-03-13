"""
Spam safety checks and content analysis.

AUDIT FIXES:
- safe_to_send used correct logic: score-based threshold, not `not all_issues`
  (previously ANY issue — including minor ones — blocked sending)
- Spam trigger words scoped to word-boundary matches to avoid false positives
  (e.g. "free" no longer flags "feel free" or "free time")
- Scoring is graduated: minor issues reduce score; critical issues fail outright
- Explicit HARD BLOCK list for truly disqualifying content
- Returns structured result with severity levels
"""
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

logger = logging.getLogger(__name__)


class Severity(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass
class CheckIssue:
    message: str
    severity: Severity
    penalty: int


# Word-boundary pattern builder
def _wb(word: str) -> re.Pattern:
    return re.compile(rf"\b{re.escape(word)}\b", re.IGNORECASE)


# Spam trigger words with severity and score penalty
SPAM_TRIGGERS: list[tuple[re.Pattern, str, Severity, int]] = [
    # (pattern, label, severity, score_penalty)
    (_wb("guarantee"),       "guarantee",        Severity.HIGH,     20),
    (_wb("risk.free"),       "risk-free",        Severity.HIGH,     20),
    (_wb("no obligation"),   "no obligation",    Severity.HIGH,     20),
    (_wb("act now"),         "act now",          Severity.HIGH,     25),
    (_wb("click here"),      "click here",       Severity.HIGH,     20),
    (_wb("buy now"),         "buy now",          Severity.CRITICAL, 40),
    (_wb("order now"),       "order now",        Severity.CRITICAL, 40),
    (_wb("earn money"),      "earn money",       Severity.CRITICAL, 50),
    (_wb("make money"),      "make money",       Severity.CRITICAL, 50),
    (_wb("work from home"),  "work from home",   Severity.CRITICAL, 50),
    (_wb("no credit check"), "no credit check",  Severity.CRITICAL, 50),
    (_wb("winner"),          "winner",           Severity.HIGH,     25),
    (_wb("congratulations"), "congratulations",  Severity.MEDIUM,   10),
    (_wb("urgent"),          "urgent",           Severity.MEDIUM,   15),
    (re.compile(r"\$\$\$"),  "$$$",              Severity.CRITICAL, 50),
    (re.compile(r"!!!"),     "!!!",              Severity.HIGH,     20),
    (_wb("100% free"),       "100% free",        Severity.HIGH,     25),
    (_wb("limited time"),    "limited time",     Severity.MEDIUM,   15),
    (_wb("don't miss"),      "don't miss",       Severity.MEDIUM,   15),
]

PROMOTIONAL_PHRASES: list[tuple[str, int]] = [
    ("best deal",      15),
    ("lowest price",   15),
    ("huge discount",  20),
    ("special offer",  15),
    ("exclusive offer",15),
    ("today only",     20),
    ("expires soon",   15),
]

# Outright hard-block patterns — will always fail the check
HARD_BLOCK_PATTERNS: list[re.Pattern] = [
    re.compile(r"\bviagra\b",        re.IGNORECASE),
    re.compile(r"\bcasino\b",        re.IGNORECASE),
    re.compile(r"\bcrypto\s+invest", re.IGNORECASE),
    re.compile(r"\bmillion\s+dollar",re.IGNORECASE),
]

SAFE_TO_SEND_THRESHOLD = 60   # minimum overall score to be safe
SUBJECT_PASS_THRESHOLD  = 65


class SpamCheckResult(NamedTuple):
    overall_score: float
    subject_score: int
    body_score: int
    issues: list[CheckIssue]
    safe_to_send: bool
    hard_blocked: bool
    recommendation: str


class SpamSafetyChecker:
    """Analyzes email content for spam risk factors."""

    def check_subject(self, subject: str) -> tuple[int, list[CheckIssue]]:
        issues: list[CheckIssue] = []
        score = 100

        # Hard block
        for pat in HARD_BLOCK_PATTERNS:
            if pat.search(subject):
                return 0, [CheckIssue("Hard-blocked content in subject", Severity.CRITICAL, 100)]

        # Spam triggers
        for pat, label, severity, penalty in SPAM_TRIGGERS:
            if pat.search(subject):
                issues.append(CheckIssue(f"Spam trigger: '{label}'", severity, penalty))
                score -= penalty

        # Length
        length = len(subject)
        if length < 15:
            issues.append(CheckIssue("Subject too short (<15 chars)", Severity.LOW, 8))
            score -= 8
        elif length > 80:
            issues.append(CheckIssue("Subject too long (>80 chars)", Severity.LOW, 8))
            score -= 8

        # ALL CAPS
        if len(subject) > 4 and subject.upper() == subject:
            issues.append(CheckIssue("All-caps subject line", Severity.HIGH, 25))
            score -= 25

        # Excessive punctuation
        if subject.count("!") > 1:
            issues.append(CheckIssue("Multiple exclamation marks", Severity.MEDIUM, 15))
            score -= 15

        if subject.count("?") > 2:
            issues.append(CheckIssue("Multiple question marks", Severity.LOW, 5))
            score -= 5

        return max(0, score), issues

    def check_body(self, body: str) -> tuple[int, list[CheckIssue]]:
        issues: list[CheckIssue] = []
        score = 100

        # Hard block
        for pat in HARD_BLOCK_PATTERNS:
            if pat.search(body):
                return 0, [CheckIssue("Hard-blocked content in body", Severity.CRITICAL, 100)]

        body_lower = body.lower()

        # Spam triggers
        for pat, label, severity, penalty in SPAM_TRIGGERS:
            if pat.search(body):
                issues.append(CheckIssue(f"Spam trigger: '{label}'", severity, penalty))
                score -= penalty

        # Promotional phrases (exact substring — these are multi-word and safe to check literally)
        for phrase, penalty in PROMOTIONAL_PHRASES:
            if phrase in body_lower:
                issues.append(CheckIssue(f"Promotional phrase: '{phrase}'", Severity.MEDIUM, penalty))
                score -= penalty

        # Link count
        link_count = len(re.findall(r"https?://", body))
        if link_count > 3:
            extra = link_count - 3
            issues.append(CheckIssue(f"Too many links ({link_count})", Severity.MEDIUM, extra * 8))
            score -= extra * 8

        # Unsubscribe — CAN-SPAM requirement
        if "unsubscribe" not in body_lower:
            issues.append(CheckIssue("Missing unsubscribe option (CAN-SPAM)", Severity.MEDIUM, 12))
            score -= 12

        # Word count
        word_count = len(body.split())
        if word_count < 30:
            issues.append(CheckIssue(f"Email too short ({word_count} words)", Severity.LOW, 5))
            score -= 5
        elif word_count > 350:
            issues.append(CheckIssue(f"Email too long ({word_count} words)", Severity.LOW, 10))
            score -= 10

        # Heavy HTML
        html_tag_count = len(re.findall(r"<[^>]+>", body))
        if html_tag_count > 8:
            issues.append(CheckIssue(f"Heavy HTML ({html_tag_count} tags)", Severity.MEDIUM, 15))
            score -= 15

        # ALL CAPS words (more than 3 in a row)
        caps_words = re.findall(r"\b[A-Z]{4,}\b", body)
        if len(caps_words) > 2:
            issues.append(CheckIssue(f"Excessive caps words ({len(caps_words)})", Severity.LOW, 8))
            score -= 8

        return max(0, score), issues

    def full_check(self, subject: str, body: str) -> SpamCheckResult:
        """Run complete spam safety check. Returns structured result."""
        # Hard block pass first
        hard_blocked = any(
            pat.search(subject) or pat.search(body) for pat in HARD_BLOCK_PATTERNS
        )

        subject_score, subject_issues = self.check_subject(subject)
        body_score, body_issues = self.check_body(body)

        all_issues = subject_issues + body_issues
        overall_score = round((subject_score + body_score) / 2, 1)

        # Safe to send only if score passes threshold AND no CRITICAL/HARD issues
        critical_issues = [i for i in all_issues if i.severity in (Severity.CRITICAL,)]
        safe = (
            not hard_blocked
            and not critical_issues
            and overall_score >= SAFE_TO_SEND_THRESHOLD
        )

        if hard_blocked:
            recommendation = "BLOCKED: Hard-blocked content detected — do not send"
        elif critical_issues:
            recommendation = f"BLOCKED: {len(critical_issues)} critical issue(s) must be fixed"
        elif overall_score >= 85:
            recommendation = "Good to send"
        elif overall_score >= SAFE_TO_SEND_THRESHOLD:
            medium_up = [i for i in all_issues if i.severity != Severity.LOW]
            recommendation = (
                f"Send with caution — {len(medium_up)} non-trivial issue(s)"
                if medium_up else "Good to send"
            )
        else:
            recommendation = f"Do not send — score {overall_score:.0f} below threshold {SAFE_TO_SEND_THRESHOLD}"

        return SpamCheckResult(
            overall_score=overall_score,
            subject_score=subject_score,
            body_score=body_score,
            issues=all_issues,
            safe_to_send=safe,
            hard_blocked=hard_blocked,
            recommendation=recommendation,
        )


spam_checker = SpamSafetyChecker()
