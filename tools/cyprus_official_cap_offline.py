"""R8-B2 offline-only structure checks for captured MeteoAlarm Atom -> CAP 1.2.

Evidence: Lithuanian samples, 2026-10-22. NEVER authenticates Cyprus alerts,
completeness, online transport, or source-to-NMS timeliness. No network calls.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
import re
from urllib.parse import urlsplit, parse_qs
from xml.etree import ElementTree as ET

ATOM = "http://www.w3.org/2005/Atom"
CAP = "urn:oasis:names:tc:emergency:cap:1.2"
ATOM_TAG = f"{{{ATOM}}}"
CAP_TAG = f"{{{CAP}}}"
LT_FEED = "https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-lithuania"
LT_FEED_ID = "tag:meteoalarm.org,2021-02-19:LT"
CAP_HOST = "feeds.meteoalarm.org"
LT_PATH = re.compile(r"/api/v1/warnings/feeds-lithuania/[a-f0-9]{8}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{4}-[a-f0-9]{12}\Z")
MAX_ATOM_BYTES = 256 * 1024  # Local policy, not provider SLA.
MAX_CAP_BYTES = 256 * 1024
MAX_AGE = timedelta(minutes=30)  # Preserve existing local safety gate.
MAX_CLOCK_SKEW = timedelta(minutes=5)
_DECL = re.compile(br"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)


class SampleState(str, Enum):
    UNKNOWN = "UNKNOWN"
    PARTIAL = "PARTIAL"
    STALE_UNVERIFIED = "STALE_UNVERIFIED"
    CAP_STRUCTURE_VALID_UNTRUSTED_FOR_CYPRUS = "CAP_STRUCTURE_VALID_UNTRUSTED_FOR_CYPRUS"


@dataclass(frozen=True)
class CAPSample:
    identifier: str
    sender: str
    status: str
    msg_type: str
    sent_utc: datetime
    onset_utc: datetime
    expires_utc: datetime
    event_en: str
    severity: str
    awareness_level: str
    awareness_type: str
    emma_id: str
    source_url: str
    language_count: int


@dataclass(frozen=True)
class CAPSampleResult:
    state: SampleState
    reason: str
    sample_count: int = 0
    entry_count: int | None = None
    samples: tuple[CAPSample, ...] = ()
    updated_utc: datetime | None = None

    @property
    def official_warnings_checked(self) -> bool:
        return False

    @property
    def clean_verified(self) -> bool:
        return False


def _ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        x = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
        if x.tzinfo is None or x.utcoffset() is None:
            return None
        return x.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        return None


def _text(node: ET.Element, tag: str) -> str | None:
    matches = node.findall(tag)
    if len(matches) != 1 or not matches[0].text or not matches[0].text.strip():
        return None
    return matches[0].text.strip()


def _xml(raw: bytes, max_bytes: int, tag: str) -> ET.Element:
    if not isinstance(raw, bytes) or not raw or len(raw) > max_bytes or b"\x00" in raw or _DECL.search(raw):
        raise ValueError("INVALID_XML_BYTES")
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise ValueError("MALFORMED_XML") from exc
    if root.tag != tag:
        raise ValueError("WRONG_XML_ROOT")
    return root


def _cap_link(entry: ET.Element) -> str:
    links = [x.get("href") for x in entry.findall(ATOM_TAG + "link") if x.get("type") == "application/cap+xml"]
    if len(links) != 1 or not links[0]:
        raise ValueError("CAP_LINK_MISSING_OR_AMBIGUOUS")
    url = links[0]
    u = urlsplit(url)
    if (u.scheme != "https" or u.netloc != CAP_HOST or u.query or u.fragment
            or not LT_PATH.fullmatch(u.path) or u.username or u.password):
        raise ValueError("CAP_LINK_UNTRUSTED")
    entry_id = _text(entry, ATOM_TAG + "id")
    if not entry_id:
        raise ValueError("ENTRY_ID_MISSING")
    id_url = urlsplit(entry_id)
    if (id_url.scheme != "https" or id_url.netloc != CAP_HOST or id_url.path != u.path
            or id_url.fragment or id_url.username or id_url.password
            or parse_qs(id_url.query, keep_blank_values=True) != {
                "index_info": ["1"], "index_area": ["0"], "index_geocode": ["0"]}):
        raise ValueError("ENTRY_ID_LINK_MISMATCH")
    return url


def _info(entry: ET.Element) -> dict[str, str | datetime]:
    result: dict[str, str | datetime] = {}
    for name in ("identifier", "message_type", "status", "scope", "event", "severity",
                 "urgency", "certainty", "areaDesc"):
        val = _text(entry, CAP_TAG + name)
        if not val:
            raise ValueError("ATOM_CAP_FIELD_MISSING_" + name)
        result[name] = val
    for key in ("sent", "effective", "onset", "expires"):
        val = _ts(_text(entry, CAP_TAG + key))
        if val is None:
            raise ValueError("ATOM_CAP_TIME_MISSING_" + key)
        result[key] = val
    geocodes = entry.findall(CAP_TAG + "geocode")
    if len(geocodes) != 1:
        raise ValueError("ATOM_GEOCODE_MISSING")
    # MeteoAlarm's Atom geocode has *Atom-namespaced* valueName/value children.
    if (_text(geocodes[0], ATOM_TAG + "valueName") != "EMMA_ID"
            or _text(geocodes[0], ATOM_TAG + "value") is None):
        raise ValueError("ATOM_EMMA_ID_MISSING")
    result["emma_id"] = _text(geocodes[0], ATOM_TAG + "value") or ""
    return result


def _cap_sample(raw: bytes, entry: ET.Element, url: str) -> CAPSample:
    root = _xml(raw, MAX_CAP_BYTES, CAP_TAG + "alert")
    atom = _info(entry)
    identifier = _text(root, CAP_TAG + "identifier")
    sender = _text(root, CAP_TAG + "sender")
    msgtype = _text(root, CAP_TAG + "msgType")
    status = _text(root, CAP_TAG + "status")
    scope = _text(root, CAP_TAG + "scope")
    sent = _ts(_text(root, CAP_TAG + "sent"))
    if not identifier or not sender or not msgtype or not status or not scope or sent is None:
        raise ValueError("CAP_REQUIRED_HEADER_MISSING")
    if identifier != atom["identifier"] or msgtype != atom["message_type"] or status != atom["status"] or scope != atom["scope"] or sent != atom["sent"]:
        raise ValueError("ATOM_CAP_HEADER_MISMATCH")
    if status != "Actual" or scope != "Public":
        raise ValueError("CAP_NOT_ACTUAL_PUBLIC")
    if msgtype != "Alert" or root.findall(CAP_TAG + "references"):
        # Update/Cancel semantics are not established from the LT samples.
        raise ValueError("UPDATE_CANCEL_HISTORY_UNVERIFIED")
    if sender != "LHMS":
        # LT-only fixture identity; deliberately *not* a Cyprus issuer mapping.
        raise ValueError("LT_SENDER_UNVERIFIED")
    infos = root.findall(CAP_TAG + "info")
    if len(infos) < 1:
        raise ValueError("MISSING_CAP_INFO")
    langs: set[str] = set()
    details = []
    for info in infos:
        lang = _text(info, CAP_TAG + "language")
        if not lang or lang in langs:
            raise ValueError("INFO_LANGUAGE_DUPLICATE_OR_MISSING")
        langs.add(lang)
        event = _text(info, CAP_TAG + "event")
        urgency = _text(info, CAP_TAG + "urgency")
        severity = _text(info, CAP_TAG + "severity")
        certainty = _text(info, CAP_TAG + "certainty")
        times = [_ts(_text(info, CAP_TAG + key)) for key in ("effective", "onset", "expires")]
        if not event or not all((urgency, severity, certainty)) or any(v is None for v in times):
            raise ValueError("INFO_FIELDS_MISSING")
        effective, onset, expires = times
        if expires <= onset or expires <= effective:
            raise ValueError("INFO_TIME_ORDER_INVALID")
        for k,v in (("urgency",urgency),( "severity",severity),("certainty",certainty),("effective",effective),("onset",onset),("expires",expires)):
            if atom[k] != v:
                raise ValueError("ATOM_CAP_INFO_MISMATCH_" + k)
        areas = info.findall(CAP_TAG + "area")
        if len(areas) != 1:
            raise ValueError("CAP_AREA_AMBIGUOUS")
        area = areas[0]
        area_desc = _text(area, CAP_TAG + "areaDesc")
        geocodes = area.findall(CAP_TAG + "geocode")
        if not area_desc or len(geocodes) != 1 or _text(geocodes[0], CAP_TAG + "valueName") != "EMMA_ID":
            raise ValueError("CAP_AREA_GEOCODE_MISSING")
        emma_id = _text(geocodes[0], CAP_TAG + "value")
        if emma_id != atom["emma_id"]:
            raise ValueError("ATOM_CAP_GEOGRAPHY_MISMATCH")
        # Compare localized Atom event/area only against English CAP block.
        if lang == "en-GB" and (event != atom["event"] or area_desc != atom["areaDesc"]):
            raise ValueError("ATOM_CAP_ENGLISH_MISMATCH")
        params = {}
        for p in info.findall(CAP_TAG + "parameter"):
            key = _text(p, CAP_TAG + "valueName")
            val = _text(p, CAP_TAG + "value")
            if not key or not val or key in params:
                raise ValueError("CAP_PARAMETER_INVALID")
            params[key] = val
        level = params.get("awareness_level")
        kind = params.get("awareness_type")
        if not level or not kind:
            raise ValueError("CAP_AWARENESS_MISSING")
        # Only observed LT sample value is validated here; no universal mapping claimed.
        if level != "2; yellow; Moderate" or kind != "1; Wind":
            raise ValueError("UNOBSERVED_AWARENESS_MAPPING")
        details.append((severity, onset, expires, emma_id, level, kind, event))
    if "en-GB" not in langs or "lt-LT" not in langs:
        raise ValueError("LT_LANGUAGE_SET_UNVERIFIED")
    if len(set(tuple(row[:6]) for row in details)) != 1:
        raise ValueError("MULTILINGUAL_INFO_INCONSISTENT")
    severity, onset, expires, emma_id, level, kind, _ = details[0]
    return CAPSample(identifier, sender, status, msgtype, sent, onset, expires,
                     str(atom["event"]), severity, level, kind, emma_id, url, len(infos))


def inspect_lt_atom_cap_samples(
    atom_xml: bytes,
    cap_by_url: dict[str, bytes],
    *,
    source_url: str,
    received_at: datetime,
    http_date_utc: datetime,
) -> CAPSampleResult:
    """Inspect offline LT samples only; never promote to verified Cyprus evidence.

    A missing/partial CAP set is not a complete warning inventory.
    Even complete structural consistency never authenticates HTTPS or feed completeness.
    """
    def result(state: SampleState, reason: str, n=0, total=None, samples=(), updated=None):
        return CAPSampleResult(state, reason, n, total, tuple(samples), updated)
    if source_url != LT_FEED:
        return result(SampleState.UNKNOWN, "UNTRUSTED_SOURCE")
    if not isinstance(received_at, datetime) or not isinstance(http_date_utc, datetime):
        return result(SampleState.UNKNOWN, "INVALID_CAPTURE_TIME")
    if received_at.tzinfo is None or http_date_utc.tzinfo is None:
        return result(SampleState.UNKNOWN, "NAIVE_CAPTURE_TIME")
    recv, server = received_at.astimezone(timezone.utc), http_date_utc.astimezone(timezone.utc)
    if abs(recv - server) > MAX_CLOCK_SKEW:
        return result(SampleState.UNKNOWN, "CAPTURE_CLOCK_SKEW")
    try:
        root = _xml(atom_xml, MAX_ATOM_BYTES, ATOM_TAG + "feed")
        if _text(root, ATOM_TAG + "id") != LT_FEED_ID:
            raise ValueError("WRONG_FEED_ID")
        updated = _ts(_text(root, ATOM_TAG + "updated"))
        if updated is None or updated - server > MAX_CLOCK_SKEW:
            raise ValueError("INVALID_FEED_UPDATED")
        self_links = [x for x in root.findall(ATOM_TAG + "link") if x.get("rel") == "self"]
        if len(self_links) != 1 or self_links[0].get("href") != LT_FEED:
            raise ValueError("INVALID_FEED_SELF_LINK")
        if _text(root, ATOM_TAG + "generator") != "MeteoAlarm Producer Server":
            raise ValueError("INVALID_FEED_GENERATOR")
        entries = root.findall(ATOM_TAG + "entry")
        if not entries:
            return result(SampleState.PARTIAL, "EMPTY_FEED_UNVERIFIED", total=0, updated=updated)
        urls = []
        for entry in entries:
            urls.append(_cap_link(entry))
        if len(urls) != len(set(urls)):
            raise ValueError("DUPLICATE_CAP_LINK")
        if not isinstance(cap_by_url, dict):
            raise ValueError("INVALID_CAP_MAPPING")
        if set(cap_by_url) != set(urls):
            return result(SampleState.PARTIAL, "CAP_SET_INCOMPLETE_OR_EXTRANEOUS", total=len(entries), updated=updated)
        samples = []
        for entry,url in zip(entries,urls):
            samples.append(_cap_sample(cap_by_url[url], entry, url))
        if len({s.identifier for s in samples}) != len(samples):
            raise ValueError("DUPLICATE_CAP_IDENTIFIER")
        if server - updated > MAX_AGE:
            return result(SampleState.STALE_UNVERIFIED, "FEED_UPDATED_OLDER_THAN_30M",
                          len(samples), len(entries), samples, updated)
        return result(SampleState.CAP_STRUCTURE_VALID_UNTRUSTED_FOR_CYPRUS,
                      "LT_CAP_STRUCTURE_ONLY", len(samples), len(entries), samples, updated)
    except (ValueError, TypeError, AttributeError) as exc:
        return result(SampleState.UNKNOWN, str(exc))
