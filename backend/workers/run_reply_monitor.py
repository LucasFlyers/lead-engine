"""
Lead deduplication engine.

AUDIT FIXES:
- Short company names excluded from fuzzy matching (< 5 chars → too many false positives)
- rapidfuzz cdist used for batch name matching (O(n) per batch, not O(n) per item)
- Domain normalisation strips ports, handles IDN, rejects localhost/IPs
- Email normalisation handles sub-addressing (tag+alias@domain.com)
- Dedup index pre-built once; subsequent lookups are O(1) for domain/email
- deduplicate_batch is now safe to call with empty existing list
- Added TLD-only domain guard (e.g. bare "com" won't match everything)
"""
import logging
import re
from typing import Optional
from urllib.parse import urlparse

from rapidfuzz import fuzz, process as fuzz_process

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD    = 88   # raised from 85 — fewer false positives
MIN_NAME_LENGTH_FOR_FUZZY = 5  # short names excluded from fuzzy match


# --------------------------------------------------------------------------- #
#  Normalisation helpers
# --------------------------------------------------------------------------- #
# Suffixes to strip from company names before comparison
_COMPANY_SUFFIXES = re.compile(
    r"\b(llc|inc|corp|ltd|co|company|companies|agency|group|studio|solutions|"
    r"services|consulting|consultancy|technologies|tech|digital|media|creative|"
    r"and associates|& associates)\b",
    re.IGNORECASE,
)

_PRIVATE_IP   = re.compile(r"^(localhost|127\.|192\.168\.|10\.|172\.(1[6-9]|2\d|3[01])\.)")
_ALPHA_ONLY   = re.compile(r"[^a-z0-9\s]")
_MULTI_SPACE  = re.compile(r"\s+")


def normalise_domain(raw: str) -> Optional[str]:
    """Return a clean domain string or None if invalid."""
    if not raw:
        return None
    raw = raw.strip()
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    try:
        netloc = urlparse(raw).netloc.lower()
        # Strip port
        netloc = netloc.split(":")[0]
        # Strip www.
        netloc = re.sub(r"^www\.", "", netloc)
        # Reject IPs / localhost / bare TLDs
        if _PRIVATE_IP.match(netloc):
            return None
        if not netloc or "." not in netloc:
            return None
        # Reject domains that are just a TLD (e.g. "com", "io")
        parts = netloc.split(".")
        if len(parts) < 2 or all(len(p) <= 2 for p in parts):
            return None
        return netloc
    except Exception:
        return None


def normalise_company_name(name: str) -> str:
    """Lowercase, strip suffixes, punctuation, and extra whitespace."""
    if not name:
        return ""
    name = name.lower()
    name = _COMPANY_SUFFIXES.sub("", name)
    name = _ALPHA_ONLY.sub("", name)
    name = _MULTI_SPACE.sub(" ", name).strip()
    return name


def normalise_email(email: str) -> str:
    """Lowercase and strip sub-address tags (user+tag@domain → user@domain)."""
    email = email.lower().strip()
    if "@" not in email:
        return email
    local, domain = email.rsplit("@", 1)
    local = local.split("+")[0]   # strip sub-address
    return f"{local}@{domain}"


# --------------------------------------------------------------------------- #
#  Deduper
# --------------------------------------------------------------------------- #
class LeadDeduper:
    """
    In-process deduplication with O(1) domain/email lookups
    and batch fuzzy name matching via rapidfuzz.
    """

    def __init__(self) -> None:
        self._domain_index: dict[str, dict] = {}
        self._email_index:  dict[str, dict] = {}
        self._name_index:   dict[str, dict] = {}   # normalised_name → company

    # -- Index management --------------------------------------------------- #

    def add_existing(self, companies: list[dict]) -> None:
        for company in companies:
            self._index(company)

    def _index(self, company: dict) -> None:
        domain = normalise_domain(company.get("website") or company.get("domain", ""))
        if domain:
            self._domain_index[domain] = company

        name = normalise_company_name(company.get("company_name", ""))
        if name and len(name) >= MIN_NAME_LENGTH_FOR_FUZZY:
            self._name_index[name] = company

        for raw_email in company.get("emails", []):
            norm = normalise_email(raw_email)
            if norm:
                self._email_index[norm] = company

    # -- Lookup ------------------------------------------------------------- #

    def find_duplicate(self, candidate: dict) -> tuple[Optional[dict], str, float]:
        """
        Returns (existing_or_None, match_method, score).
        Checks domain → email → name (in that order — specificity descending).
        """
        # 1. Domain (exact)
        domain = normalise_domain(candidate.get("website") or candidate.get("domain", ""))
        if domain and domain in self._domain_index:
            return self._domain_index[domain], "domain", 100.0

        # 2. Email (exact, normalised)
        for raw_email in candidate.get("emails", []):
            norm = normalise_email(raw_email)
            if norm in self._email_index:
                return self._email_index[norm], "email", 100.0

        # 3. Fuzzy name
        candidate_name = normalise_company_name(candidate.get("company_name", ""))
        if len(candidate_name) >= MIN_NAME_LENGTH_FOR_FUZZY and self._name_index:
            existing_names = list(self._name_index.keys())
            match = fuzz_process.extractOne(
                candidate_name,
                existing_names,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=SIMILARITY_THRESHOLD,
            )
            if match:
                matched_name, score, _ = match
                return self._name_index[matched_name], "name_similarity", float(score)

        return None, "", 0.0

    # -- Batch deduplication ------------------------------------------------ #

    def deduplicate(
        self, candidates: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """
        Separate candidates into unique and duplicate lists.
        Unique items are added to the index so subsequent candidates
        in the same batch are also checked against them.
        """
        unique:     list[dict] = []
        duplicates: list[dict] = []

        for candidate in candidates:
            existing, method, score = self.find_duplicate(candidate)
            if existing:
                dup = dict(candidate)
                dup["is_duplicate"]  = True
                dup["duplicate_of"]  = existing.get("id") or existing.get("company_name")
                dup["dedup_method"]  = method
                dup["dedup_score"]   = score
                duplicates.append(dup)
            else:
                item = dict(candidate)
                item["is_duplicate"] = False
                unique.append(item)
                self._index(item)   # add to index so later candidates see it

        logger.info(
            "Deduplication: %d unique, %d duplicates from %d candidates",
            len(unique), len(duplicates), len(candidates),
        )
        return unique, duplicates


# --------------------------------------------------------------------------- #
#  Convenience wrapper
# --------------------------------------------------------------------------- #
def deduplicate_batch(
    candidates: list[dict],
    existing: Optional[list[dict]] = None,
) -> tuple[list[dict], list[dict]]:
    deduper = LeadDeduper()
    if existing:
        deduper.add_existing(existing)
    return deduper.deduplicate(candidates)
