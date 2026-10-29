"""Cyprus R8-G1: offline-only CAP/Atom warning evidence contract.

NOT a publisher, collector, source authentication system, or proof of national
coverage. No I/O except processing caller-supplied bytes; no network imports.
Even structurally matching evidence must not clear warnings or authorize marine
sports. The caller must not treat UNKNOWN as an absence of warnings.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import hashlib
import re
from typing import Any, Mapping, Sequence
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

ATOM = 'http://www.w3.org/2005/Atom'
CAP = 'urn:oasis:names:tc:emergency:cap:1.2'
FEED_URL = 'https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-cyprus'
FEED_ID = 'tag:meteoalarm.org,2021-02-19:CY'
AUTHORITY_OID = '2.49.0.0.196.0'
CY_CAP_PATH = re.compile(r'/api/v1/warnings/feeds-cyprus/[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\Z')
DECL = re.compile(br'<!\s*(?:DOCTYPE|ENTITY)\b', re.IGNORECASE)
MAX_BYTES = 256 * 1024  # local safety guard, not provider SLA
MAX_AGE = timedelta(minutes=30)  # local safety guard, not completeness SLA
MAX_FUTURE = timedelta(minutes=5)
HEX64 = re.compile(r'[0-9a-f]{64}\Z')
REF_ITEM = re.compile(r'([^\s,]+),([^\s,]+),([^\s,]+)\Z')


@dataclass(frozen=True)
class ContractDecision:
    state: str = 'UNKNOWN'
    reason: str = 'UNVERIFIED'
    publication_action: str = 'NO_POST'
    official_warnings_checked: bool = False
    clean_verified: bool = False
    city_cleared: bool = False
    positive_water_guidance_allowed: bool = False
    national_inventory_complete: bool = False


def unknown(reason: str) -> ContractDecision:
    return ContractDecision(reason=reason)


def _time(s: Any) -> datetime | None:
    if not isinstance(s, str) or not s.strip():
        return None
    try:
        dt = datetime.fromisoformat(s.strip().replace('Z', '+00:00'))
        return dt.astimezone(timezone.utc) if dt.utcoffset() is not None else None
    except (ValueError, OverflowError):
        return None


def _xml(raw: bytes, root_tag: str) -> ET.Element | None:
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES or DECL.search(raw):
        return None
    try:
        node = ET.fromstring(raw)
    except (ET.ParseError, ValueError):
        return None
    return node if node.tag == root_tag else None


def _one_text(node: ET.Element, tag: str) -> str | None:
    nodes = node.findall(tag)
    if len(nodes) != 1 or nodes[0].text is None:
        return None
    return nodes[0].text.strip() or None


def _exact_https_url(url: Any, required_path: str | re.Pattern[str]) -> bool:
    if not isinstance(url, str):
        return False
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == 'https' and parsed.hostname == 'feeds.meteoalarm.org'
                and parsed.netloc == 'feeds.meteoalarm.org' and not parsed.query
                and not parsed.fragment and
                (parsed.path == required_path if isinstance(required_path, str)
                 else bool(required_path.fullmatch(parsed.path))))
    except ValueError:
        return False


def registered_endpoint(url: Any) -> str:
    return ('REGISTERED_ENDPOINT_ONLY' if url == FEED_URL else 'UNKNOWN_ENDPOINT')


def authority_sender_status(oid: Any, cap_sender: Any = None) -> str:
    if oid != AUTHORITY_OID:
        return 'UNKNOWN_AUTHORITY'
    if cap_sender is not None:
        # Registry authority OID is NOT an authenticated CAP.sender identity.
        return 'CAP_SENDER_UNVERIFIED'
    return 'AUTHORITY_REGISTERED_SENDER_UNPROVEN'


def emma_city_status(emma_id: Any, city: Any) -> str:
    # No independently authenticated Cyprus EMMA_ID-to-city table exists here.
    return 'UNKNOWN_EMMA_CITY_MAPPING'


def geography_status(product: Any, distance_km: Any, direction: Any, city: Any) -> str:
    match = ((product == 'land_weather_warnings' and distance_km == 10 and direction == 'inland')
             or (product == 'coastal_waters_forecast' and distance_km == 8 and direction == 'seaward'))
    if not match:
        return 'UNKNOWN_PRODUCT_CONFLATION'
    return 'GEOGRAPHY_TAXONOMY_ONLY' if city is None else 'UNKNOWN_LOCAL_ATTRIBUTION'


def lifecycle_status(message_type: Any, references: Any, history_resolved: bool,
                     document_complete: bool, duplicate_identifier: bool = False,
                     expires: Any = 'unverified') -> str:
    if not document_complete:
        return 'UNKNOWN_CAP_DOCUMENT'
    if duplicate_identifier:
        return 'UNKNOWN_DUPLICATE_IDENTIFIER'
    if message_type not in ('Alert', 'Update', 'Cancel'):
        return 'UNKNOWN_CAP_MESSAGE_TYPE'
    if message_type in ('Update', 'Cancel') and (not references or not history_resolved):
        return 'UNKNOWN_UNRESOLVED_REFERENCES'
    # A resolved reference does NOT authenticate a CAP message or Cancel.
    return 'UNKNOWN_CAP_AUTHENTICITY'


def _reference_tuples(raw: str | None, own_sender: str, sent: datetime) -> tuple[tuple[str, str, str], ...] | None:
    if not raw:
        return None
    refs = []
    for item in raw.split():
        m = REF_ITEM.fullmatch(item)
        if not m or m.group(1) != own_sender or _time(m.group(3)) is None:
            return None
        if _time(m.group(3)) >= sent:
            return None
        refs.append((m.group(1), m.group(2), _time(m.group(3)).isoformat()))
    if not refs or len(refs) != len(set(refs)):
        return None
    return tuple(refs)


def _inspect_captured_pair(*, atom: bytes, cap_by_url: Mapping[str, bytes],
                           source: Mapping[str, Any], at_utc: datetime,
                           history: Mapping[tuple[str, str, str], Mapping[str, Any]] | None = None) -> ContractDecision:
    """Inspect *all* supplied feed entries and their CAPs; NEVER assert a complete inventory.

    'source' is supplied capture metadata, not proof of HTTPS server identity.
    'history' is optional *untrusted* historical metadata, not independent auth.
    """
    if at_utc.utcoffset() is None:
        return unknown('UNKNOWN_EVALUATION_TIME')
    at = at_utc.astimezone(timezone.utc)
    if not isinstance(source, Mapping) or source.get('url') != FEED_URL:
        return unknown('UNKNOWN_SOURCE_URL')
    if (source.get('http_status') != 200 or source.get('redirect_followed') is not False
            or source.get('credentials_sent') is not False):
        return unknown('UNKNOWN_SOURCE_TRANSPORT')
    digest = source.get('sha256')
    if not isinstance(digest, str) or not HEX64.fullmatch(digest) or hashlib.sha256(atom).hexdigest() != digest:
        return unknown('UNKNOWN_SOURCE_INTEGRITY')
    if source.get('content_type', "").split(';', 1)[0].strip().lower() != 'application/atom+xml':
        return unknown('UNKNOWN_SOURCE_CONTENT_TYPE')
    received = _time(source.get('received_utc'))
    server_date = _time(source.get('http_date_utc'))
    if server_date is None or received is None or abs((server_date - received).total_seconds()) > 300:
        return unknown('UNKNOWN_SOURCE_SERVER_CLOCK')
    if received is None or received > at + MAX_FUTURE or at - received > MAX_AGE:
        return unknown('UNKNOWN_SOURCE_FRESHNESS')
    root = _xml(atom, f'{{{ATOM}}}feed')
    if root is None:
        return unknown('UNKNOWN_ATOM_DOCUMENT')
    if _one_text(root, f'{{{ATOM}}}id') != FEED_ID:
        return unknown('UNKNOWN_ATOM_COUNTRY')
    if (_one_text(root, f'{{{ATOM}}}title') is None
            or _one_text(root, f'{{{ATOM}}}generator') != 'MeteoAlarm Producer Server'
            or _one_text(root, f'{{{ATOM}}}author/{{{ATOM}}}name') != 'meteoalarm.org'
            or _one_text(root, f'{{{ATOM}}}author/{{{ATOM}}}uri') != 'https://meteoalarm.org'):
        return unknown('UNKNOWN_ATOM_METADATA')
    updated = _time(_one_text(root, f'{{{ATOM}}}updated'))
    if updated is None or updated > at + MAX_FUTURE or at - updated > MAX_AGE:
        return unknown('UNKNOWN_ATOM_UPDATED')
    links = [e.attrib.get('href') for e in root.findall(f'{{{ATOM}}}link') if e.attrib.get('rel') == 'self']
    if links != [FEED_URL]:
        return unknown('UNKNOWN_ATOM_SELF_LINK')
    entries = root.findall(f'{{{ATOM}}}entry')
    if not entries:
        return unknown('EMPTY_UNVERIFIED')
    evidence = source.get('cap_evidence')
    if not isinstance(evidence, Mapping) or set(evidence) != set(cap_by_url):
        return unknown('UNKNOWN_CAP_PROVENANCE_SET')
    used = set()
    identifiers = set()
    entry_ids = set()
    if not isinstance(cap_by_url, Mapping):
        return unknown('UNKNOWN_CAP_SET')
    for entry in entries:
        entry_id = _one_text(entry, f'{{{ATOM}}}id')
        if not entry_id or entry_id in entry_ids:
            return unknown('UNKNOWN_ATOM_ENTRY_ID')
        entry_ids.add(entry_id)
        cap_links = [e.attrib.get('href') for e in entry.findall(f'{{{ATOM}}}link')
                     if e.attrib.get('type') == 'application/cap+xml']
        if len(cap_links) != 1 or not _exact_https_url(cap_links[0], CY_CAP_PATH):
            return unknown('UNKNOWN_CAP_LINK')
        url = cap_links[0]
        if url in used or url not in cap_by_url:
            return unknown('UNKNOWN_INCOMPLETE_CAP_SET')
        used.add(url)
        cap_source = evidence.get(url)
        cap_received = _time(cap_source.get('received_utc')) if isinstance(cap_source, Mapping) else None
        cap_server = _time(cap_source.get('http_date_utc')) if isinstance(cap_source, Mapping) else None
        if (cap_received is None or cap_server is None
                or cap_received > at + MAX_FUTURE or at - cap_received > MAX_AGE
                or abs((cap_server - cap_received).total_seconds()) > 300):
            return unknown('UNKNOWN_CAP_SOURCE_FRESHNESS')
        if (not isinstance(cap_source, Mapping)
                or cap_source.get('http_status') != 200
                or cap_source.get('redirect_followed') is not False
                or cap_source.get('credentials_sent') is not False
                or cap_source.get('content_type', '').split(';', 1)[0].strip().lower() != 'application/cap+xml'
                or cap_source.get('sha256') != hashlib.sha256(cap_by_url[url]).hexdigest()
                or not HEX64.fullmatch(str(cap_source.get('sha256', '')))):
            return unknown('UNKNOWN_CAP_SOURCE_PROVENANCE')
        doc = _xml(cap_by_url[url], f'{{{CAP}}}alert')
        if doc is None:
            return unknown('UNKNOWN_CAP_DOCUMENT')
        identifier = _one_text(doc, f'{{{CAP}}}identifier')
        sender = _one_text(doc, f'{{{CAP}}}sender')
        sent = _time(_one_text(doc, f'{{{CAP}}}sent'))
        status = _one_text(doc, f'{{{CAP}}}status')
        msg_type = _one_text(doc, f'{{{CAP}}}msgType')
        scope = _one_text(doc, f'{{{CAP}}}scope')
        if not identifier or not sender or sent is None or status != 'Actual' or scope != 'Public':
            return unknown('UNKNOWN_CAP_IDENTITY')
        if sent > at + MAX_FUTURE:
            return unknown('UNKNOWN_CAP_FUTURE_SENT')
        atom_id = _one_text(entry, f'{{{CAP}}}identifier')
        atom_type = _one_text(entry, f'{{{CAP}}}message_type')
        atom_status = _one_text(entry, f'{{{CAP}}}status')
        if (atom_id is None or atom_id != identifier or
                atom_type != msg_type or atom_status != status):
            return unknown('UNKNOWN_ATOM_CAP_MISMATCH')
        atom_sent = _one_text(entry, f'{{{CAP}}}sent')
        if atom_sent is not None and _time(atom_sent) != sent:
            return unknown('UNKNOWN_ATOM_CAP_SENT_MISMATCH')
        if identifier in identifiers:
            return unknown('UNKNOWN_DUPLICATE_IDENTIFIER')
        identifiers.add(identifier)
        if msg_type not in ('Alert', 'Update', 'Cancel'):
            return unknown('UNKNOWN_CAP_MESSAGE_TYPE')
        raw_refs = _one_text(doc, f'{{{CAP}}}references')
        if msg_type in ('Update', 'Cancel'):
            parsed = _reference_tuples(raw_refs, sender, sent)
            if not parsed:
                return unknown('UNKNOWN_UNRESOLVED_REFERENCES')
            if history is None or any(ref not in history for ref in parsed):
                return unknown('UNKNOWN_UNRESOLVED_REFERENCES')
            # Even all refs present in historical metadata cannot prove authenticity.
        elif raw_refs:
            return unknown('UNKNOWN_ALERT_WITH_REFERENCES')
        infos = doc.findall(f'{{{CAP}}}info')
        if not infos:
            return unknown('UNKNOWN_CAP_INFO')
        known_emma = set()
        for info in infos:
            effective_raw = _one_text(info, f'{{{CAP}}}effective')
            effective = _time(effective_raw) if effective_raw else sent
            starts = [effective, _time(_one_text(info, f'{{{CAP}}}onset'))]
            expires = _time(_one_text(info, f'{{{CAP}}}expires'))
            if expires is None or any(t is None for t in starts):
                return unknown('UNKNOWN_CAP_TIME_WINDOW')
            if not (sent <= starts[0] <= starts[1] < expires):
                return unknown('UNKNOWN_CAP_TIME_ORDER')
            event = _one_text(info, f'{{{CAP}}}event')
            severity = _one_text(info, f'{{{CAP}}}severity')
            if event is None or severity not in ('Extreme', 'Severe', 'Moderate', 'Minor', 'Unknown'):
                return unknown('UNKNOWN_CAP_HAZARD')
            atom_severity = _one_text(entry, f'{{{CAP}}}severity')
            if atom_severity and atom_severity != severity:
                return unknown('UNKNOWN_ATOM_CAP_SEVERITY_MISMATCH')
            areas = info.findall(f'{{{CAP}}}area')
            if not areas:
                return unknown('UNKNOWN_CAP_AREA')
            info_emma = set()
            for area in areas:
                if not _one_text(area, f'{{{CAP}}}areaDesc'):
                    return unknown('UNKNOWN_CAP_AREA')
                for geo in area.findall(f'{{{CAP}}}geocode'):
                    name = _one_text(geo, f'{{{CAP}}}valueName')
                    value = _one_text(geo, f'{{{CAP}}}value')
                    if name == 'EMMA_ID' and value:
                        known_emma.add(value)
                        info_emma.add(value)
            if not info_emma:
                return unknown('UNKNOWN_CAP_EMMA_ID')
        atom_geocodes = entry.findall(f'{{{CAP}}}geocode')
        if atom_geocodes:
            atom_emma = set()
            for geo in atom_geocodes:
                name = _one_text(geo, f'{{{ATOM}}}valueName')
                value = _one_text(geo, f'{{{ATOM}}}value')
                if name == 'EMMA_ID' and value:
                    atom_emma.add(value)
            if not atom_emma or atom_emma != known_emma:
                return unknown('UNKNOWN_ATOM_CAP_GEOCODE_MISMATCH')
        # The OID and the mere presence of a sender string do not authenticate it.
        # We cannot establish full national inventory or EMMA city polygons.
    if set(cap_by_url) != used:
        return unknown('UNKNOWN_CAP_SET_EXTRANEOUS')
    return unknown('CAP_MATCH_SENDER_UNVERIFIED_INVENTORY_UNKNOWN')


def classify_golden_contract_case(case: Mapping[str, Any]) -> str:
    """Compatibility classifier for R8-C2A declarative offline Golden Set."""
    kind = case.get('kind')
    if kind == 'authority':
        return authority_sender_status(case.get('oid'), case.get('claimed_cap_sender'))
    if kind == 'endpoint':
        return registered_endpoint(case.get('url'))
    if kind == 'geocode':
        return emma_city_status(case.get('emma_id'), case.get('city'))
    if kind == 'geography':
        return geography_status(case.get('product'), case.get('distance_km'), case.get('direction'), case.get('city'))
    if kind == 'national_html':
        return 'CAP_LIFECYCLE_UNVERIFIED'
    if kind == 'cap_metadata':
        return lifecycle_status(case.get('message_type'), case.get('references'),
                                bool(case.get('history_resolved')), bool(case.get('document_complete')),
                                bool(case.get('duplicate_identifier')), case.get('expires', 'unverified'))
    if kind == 'coverage':
        return 'UNKNOWN_NATIONAL_COMPLETENESS'
    return 'UNKNOWN_CONTRACT_CASE'


def warning_phase(issued: str, onset: str, end_exclusive: str, instant: str) -> str:
    """Historical interval classification only; NEVER an authorization to publish."""
    t, a, b, c = (_time(x) for x in (instant, issued, onset, end_exclusive))
    if any(x is None for x in (t, a, b, c)) or not a < b < c:
        return 'UNKNOWN'
    if t < a:
        return 'NOT_YET_ISSUED'
    if t < b:
        return 'UPCOMING'
    if t < c:
        return 'ACTIVE'
    return 'EXPIRED_HISTORY'


def inspect_captured_pair(*, atom: bytes, cap_by_url: Mapping[str, bytes],
                          source: Mapping[str, Any], at_utc: datetime,
                          history: Mapping[tuple[str, str, str], Mapping[str, Any]] | None = None) -> ContractDecision:
    """Public never-raise gate for supplied, potentially malformed offline evidence."""
    try:
        return _inspect_captured_pair(atom=atom, cap_by_url=cap_by_url, source=source,
                                      at_utc=at_utc, history=history)
    except (TypeError, ValueError, AttributeError, KeyError, OverflowError, ET.ParseError):
        return unknown('UNKNOWN_INVALID_EVIDENCE')
