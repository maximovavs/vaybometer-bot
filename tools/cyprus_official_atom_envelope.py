"""R8-B0: OFFLINE-ONLY MeteoAlarm Cyprus Atom envelope inspection.

No HTTP, CAP parsing, provider integration or authority to mark warnings checked.
This is intentionally not imported into the publication path. Transport TLS,
provenance and feed completeness cannot be authenticated by Atom XML alone.

An empty and apparently fresh feed is EMPTY_UNVERIFIED, NOT no warnings.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from enum import Enum
import re
from xml.etree import ElementTree as ET


OFFICIAL_CYPRUS_ATOM_URL = (
    "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-cyprus"
)
ATOM_NS = "http://www.w3.org/2005/Atom"
ATOM_FEED_TAG = f"{{{ATOM_NS}}}feed"
ATOM_ENTRY_TAG = f"{{{ATOM_NS}}}entry"
EXPECTED_FEED_ID = "tag:meteoalarm.org,2021-02-19:CY"
EXPECTED_GENERATOR = "MeteoAlarm Producer Server"
EXPECTED_AUTHOR_NAME = "meteoalarm.org"
EXPECTED_AUTHOR_URI = "https://meteoalarm.org"
MAX_XML_BYTES = 256 * 1024  # Local guard, not a provider-specified limit.
MAX_AGE = timedelta(minutes=30)  # Local freshness ceiling; not a feed SLA.
MAX_CLOCK_SKEW = timedelta(minutes=5)  # Local safeguard; not a feed SLA.
_DECLARATION_RE = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class AtomEnvelopeState(str, Enum):
    UNKNOWN = "UNKNOWN"
    EMPTY_UNVERIFIED = "EMPTY_UNVERIFIED"
    ENTRIES_UNVERIFIED = "ENTRIES_UNVERIFIED"


@dataclass(frozen=True)
class AtomEnvelopeResult:
    state: AtomEnvelopeState
    reason: str
    entry_count: int | None = None
    updated: datetime | None = None
    feed_id: str | None = None

    @property
    def official_warnings_checked(self) -> bool:
        """B0 must never supply affirmative provenance to the rating."""
        return False

    @property
    def clean_verified(self) -> bool:
        return False


def _utc_time(value: datetime) -> datetime | None:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        return None
    return value.astimezone(timezone.utc)


def _atom_time(raw: str | None) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        date = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return _utc_time(date)


def _http_date(raw: str | None) -> datetime | None:
    if not isinstance(raw, str):
        return None
    try:
        return _utc_time(parsedate_to_datetime(raw))
    except (TypeError, ValueError, IndexError, OverflowError):
        return None


def _unique_text(root: ET.Element, path: str) -> str | None:
    matches = root.findall(path)
    if len(matches) != 1 or not matches[0].text:
        return None
    return matches[0].text.strip()


def parse_cyprus_atom_envelope(
    xml_bytes: bytes,
    *,
    source_url: str,
    http_status: int,
    content_type: str,
    http_date: str | None,
    received_at: datetime,
) -> AtomEnvelopeResult:
    """Validate the envelope of a *captured* response, without trusting it as a complete alert inventory.

    source_url/http_status/headers must come from a separately validated HTTPS
    transport. Passing strings to this parser cannot authenticate a source.
    """
    def unknown(reason: str) -> AtomEnvelopeResult:
        return AtomEnvelopeResult(AtomEnvelopeState.UNKNOWN, reason)

    if source_url != OFFICIAL_CYPRUS_ATOM_URL:
        return unknown("UNTRUSTED_SOURCE_URL")
    if type(http_status) is not int or http_status != 200:
        return unknown("HTTP_NOT_200")
    if not isinstance(content_type, str) or content_type.split(";", 1)[0].strip().lower() != "application/atom+xml":
        return unknown("UNEXPECTED_CONTENT_TYPE")
    recv_utc = _utc_time(received_at)
    if recv_utc is None:
        return unknown("INVALID_RECEIVED_AT")
    server_utc = _http_date(http_date)
    if server_utc is None:
        return unknown("INVALID_HTTP_DATE")
    if abs(recv_utc - server_utc) > MAX_CLOCK_SKEW:
        return unknown("HTTP_DATE_OUT_OF_RANGE")
    if not isinstance(xml_bytes, bytes):
        return unknown("INVALID_PAYLOAD_TYPE")
    if not xml_bytes:
        return unknown("EMPTY_PAYLOAD")
    if len(xml_bytes) > MAX_XML_BYTES:
        return unknown("OVERSIZED_XML")
    if _DECLARATION_RE.search(xml_bytes):
        return unknown("DTD_OR_ENTITIES_FORBIDDEN")
    if b"\x00" in xml_bytes:
        return unknown("INVALID_XML")
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return unknown("INVALID_XML")
    if root.tag != ATOM_FEED_TAG:
        return unknown("WRONG_ATOM_NAMESPACE_OR_ROOT")
    feed_id = _unique_text(root, f"{{{ATOM_NS}}}id")
    if feed_id != EXPECTED_FEED_ID:
        return unknown("WRONG_COUNTRY_FEED_ID")
    links = root.findall(f"{{{ATOM_NS}}}link")
    self_links = [link for link in links if link.get("rel") == "self"]
    if (len(self_links) != 1 or self_links[0].get("href") != OFFICIAL_CYPRUS_ATOM_URL
            or self_links[0].get("type") != "application/atom+xml"):
        return unknown("INVALID_SELF_LINK")
    if _unique_text(root, f"{{{ATOM_NS}}}generator") != EXPECTED_GENERATOR:
        return unknown("INVALID_GENERATOR")
    if _unique_text(root, f"{{{ATOM_NS}}}title") is None:
        return unknown("MISSING_TITLE")
    authors = root.findall(f"{{{ATOM_NS}}}author")
    if (len(authors) != 1 or
            _unique_text(authors[0], f"{{{ATOM_NS}}}name") != EXPECTED_AUTHOR_NAME or
            _unique_text(authors[0], f"{{{ATOM_NS}}}uri") != EXPECTED_AUTHOR_URI):
        return unknown("INVALID_AUTHOR")
    updated = _atom_time(_unique_text(root, f"{{{ATOM_NS}}}updated"))
    if updated is None:
        return unknown("INVALID_UPDATED")
    if updated - server_utc > MAX_CLOCK_SKEW:
        return unknown("FUTURE_UPDATED")
    if server_utc - updated > MAX_AGE:
        return unknown("STALE_UPDATED")
    entries = root.findall(ATOM_ENTRY_TAG)
    # B0 intentionally does not interpret entry, CAP issuer, severity or cancellation.
    if entries:
        return AtomEnvelopeResult(
            AtomEnvelopeState.ENTRIES_UNVERIFIED, "CAP_NOT_VALIDATED",
            len(entries), updated, feed_id,
        )
    return AtomEnvelopeResult(
        AtomEnvelopeState.EMPTY_UNVERIFIED, "EMPTY_FEED_NOT_COMPLETENESS_PROOF",
        0, updated, feed_id,
    )
