"""R8-H5 OFFLINE ONLY: one-way candidate rendering from existing R8-G1 CAP guard.

No HTTP, credentials, Telegram calls, publisher registration or authorization.
A rendered preview is NOT proof of authenticated CAP.sender, national coverage,
city clearance or water-sports safety. Never treat it as permission to post.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from html import escape
from typing import Any, Mapping
import re
import unicodedata
from zoneinfo import ZoneInfo
from xml.etree import ElementTree as ET

try:  # allow both `tools.cyprus_emma_preview_r8h5` and offline `tools/` tests
    from .cyprus_cap_r8g1_contract import (ATOM, CAP, FEED_URL, inspect_captured_pair)
except ImportError:
    from cyprus_cap_r8g1_contract import (ATOM, CAP, FEED_URL, inspect_captured_pair)

CY_TZ = ZoneInfo('Asia/Nicosia')
_CAP = f'{{{CAP}}}'
_ATOM = f'{{{ATOM}}}'

# Deliberately narrow, non-exhaustive category display: other hazards stay UNKNOWN.
_HAZARDS = (
    (('thunderstorm', 'thunder', 'καταιγ', 'гроз'), 'грозах'),
    (('heavy rain', 'rainfall', 'βροχ', 'ливн'), 'сильных осадках'),
    (('strong wind', 'high wind', 'windstorm', 'ανεμ', 'ветр'), 'сильном ветре'),
    (('high temperature', 'heatwave', 'extreme heat', 'καύσ', 'жар'), 'сильной жаре'),
    (('snowfall', 'snow', 'χιον', 'снег'), 'снегопаде'),
    (('fog', 'ομίχλ', 'туман'), 'тумане'),
)
_SEVERITY = {'Minor': 'незначительная', 'Moderate': 'умеренная',
             'Severe': 'серьёзная', 'Extreme': 'экстремальная'}


@dataclass(frozen=True)
class OfflinePreview:
    # This object is NOT a publication authorization, including on a match.
    classification: str = 'UNKNOWN'
    reason: str = 'UNVERIFIED'
    publication_action: str = 'NO_POST'
    text: str = ''
    official_warnings_checked: bool = False
    clean_verified: bool = False
    city_cleared: bool = False
    positive_water_guidance_allowed: bool = False
    national_inventory_complete: bool = False


def _time(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        t = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return t.astimezone(timezone.utc) if t.utcoffset() is not None else None
    except (TypeError, ValueError, OverflowError):
        return None


def _one(node: ET.Element, path: str) -> str | None:
    els = node.findall(path)
    if len(els) != 1 or els[0].text is None:
        return None
    return els[0].text.strip() or None


def _event_label(s: str | None) -> str | None:
    if not s or len(s) > 200:
        return None
    s = s.casefold()
    # Do not allow synthetic fixtures to masquerade as observed warnings.
    if any(token in s for token in ('synthetic', 'test-only', 'do-not-publish')):
        return None
    labels = [ru for tokens,ru in _HAZARDS if any(token in s for token in tokens)]
    return labels[0] if len(labels) == 1 else None



def _named_codes(info: ET.Element, tag: str) -> dict[str, str] | None:
    """Reject malformed or repeated names rather than silently losing codes."""
    result = {}
    for item in info.findall(_CAP+tag):
        name = _one(item, _CAP+'valueName')
        value = _one(item, _CAP+'value')
        if (not name or not value or name in result or len(item) != 2
                or any(len(child) for child in item)):
            return None
        result[name] = value
    return result


def _awareness_codes(info: ET.Element) -> dict[str, str] | None:
    codes = _named_codes(info, 'parameter')
    if codes is None:
        return None
    return {name: value for name, value in codes.items()
            if name in ('awareness_type', 'awareness_level')}


# Same first-release hazard names as the H6 taxonomy (kept equal by a test).
_COLOURED_TAXONOMY = frozenset({
    'thunderstorm', 'heavy rain', 'strong wind', 'high temperature', 'snowfall', 'fog',
})
_WARNING_COLOURS = ('yellow', 'orange', 'red')
# Only the code/label mappings evidenced by the captured Cyprus CAP and the
# already agreed K4 structured orange fixture. Do not infer other codes.
_AWARENESS_TYPES = {'thunderstorm': '3'}
_AWARENESS_LEVELS = {'yellow': ('2', 'Moderate'), 'orange': ('3', 'Severe')}
_GREEK_HAZARDS = {'thunderstorm': frozenset({'καταιγιδα', 'καταιγιδες'})}
_GREEK_EVENT_COLOURS = {'yellow': 'κιτρινο', 'orange': 'πορτοκαλι'}


def _coloured_event_contract(info: ET.Element) -> bool:
    """Self-consistent MeteoAlarm coloured event, from language-independent codes.

    The English event '<hazard> <colour>' must repeat the awareness_type name and
    the awareness_level colour, and the level's severity must equal CAP severity
    (e.g. 'Thunderstorm Yellow' / '3; Thunderstorm' / '2; yellow; Moderate').
    Nothing is translated or inferred; any disagreement is UNKNOWN.
    """
    event = ' '.join((_one(info, _CAP+'event') or '').split()).casefold()
    hazard, _, colour = event.rpartition(' ')
    codes = _awareness_codes(info)
    if (not codes or set(codes) != {'awareness_type', 'awareness_level'}
            or _one(info, _CAP+'category') != 'Met'):
        return False
    kind = [x.strip() for x in codes['awareness_type'].split(';')]
    level = [x.strip() for x in codes['awareness_level'].split(';')]
    return (len(kind) == 2 and len(level) == 3
            and hazard in _COLOURED_TAXONOMY and kind[1].casefold() == hazard
            and _AWARENESS_TYPES.get(hazard) == kind[0]
            and level[1].casefold() == colour
            and _AWARENESS_LEVELS.get(colour) == (level[0], level[2])
            and level[2] == _one(info, _CAP+'severity'))


# Free text (headline/description/instruction) is never displayed and never
# used as evidence that a hazard exists. It can only make a CAP UNKNOWN: when
# it is malformed or carries an explicit retraction/all-clear marker. This is a
# fail-closed screen, not a claim that the translations are equivalent.
_PROSE_FIELDS = ('headline', 'description', 'instruction')
_RETRACTION = re.compile(
    r'cancel|withdrawn|\blifted\b|no longer|all[ -]?clear|not expected|'
    r'\bno (?:warning|risk|danger|threat|hazard|thunderstorms?|rain|wind)\b|'
    r'no action (?:is )?(?:needed|required)|'
    r'ακυρ|\bαρση|δεν αναμεν|δεν υπαρχει κινδυν|χωρις κινδυν|καμια προειδοποιηση|'
    r'δεν ισχυ|παυει να ισχυ')
_SENTENCE_NEGATION = re.compile(r'(?:^|[.!?]\s+)(?:no|none)\b')
_GREEK_COLOURS = (('κιτριν', 'yellow'), ('πορτοκαλ', 'orange'), ('κοκκιν', 'red'),
                  ('πρασιν', 'green'))


def _fold(value: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFD', value.casefold())
                   if unicodedata.category(c) != 'Mn')


def _retracted(value: str | None) -> bool:
    folded = _fold(' '.join((value or '').split()))
    return bool(_RETRACTION.search(folded) or _SENTENCE_NEGATION.search(folded))


def _colour_words(text: str) -> set[str]:
    folded = _fold(text)
    found = {c for c in _WARNING_COLOURS if re.search(r'\b' + c + r'\b', folded)}
    return found | {c for stem, c in _GREEK_COLOURS if stem in folded}


def _event_colour(info: ET.Element) -> str | None:
    return (' '.join((_one(info, _CAP+'event') or '').split()).casefold().rpartition(' ')[2]
            if _coloured_event_contract(info) else None)


def _prose_contract(info: ET.Element, colour: str | None = None) -> bool:
    """Optional plain-text fields: at most one each, no markup, no retraction,
    and no warning colour other than the structured one (none for a plain event)."""
    for key in _PROSE_FIELDS:
        nodes = info.findall(_CAP+key)
        if not nodes:
            continue
        if len(nodes) != 1 or len(nodes[0]) or nodes[0].attrib or nodes[0].text is None:
            return False
        text = ' '.join(nodes[0].text.split())
        if not text or len(text) > 4000 or _retracted(text):
            return False
        if not _colour_words(text) <= ({colour} if colour else set()):
            return False
    return True


def _select_display_info(cap: ET.Element) -> ET.Element | None:
    """Select the English block of one structurally consistent warning.

    Decisions use structured CAP fields only; free text can only veto (UNKNOWN).
    UNKNOWN is never an all-clear or evidence that the warning is absent.
    """
    infos = cap.findall(_CAP+'info')
    if len(infos) == 1:
        info = infos[0]
        codes = _awareness_codes(info)
        # Legacy single-info plain labels remain supported; a structured code
        # must not bypass the bilingual consistency contract.
        return info if codes == {} and _prose_contract(info, _event_colour(info)) else None
    if len(infos) != 2:
        return None
    by_lang = {}
    for info in infos:
        lang = _one(info, _CAP+'language')
        role = ('en' if lang == 'en-GB' else
                'el' if lang in ('el-GR', 'el-CY') else None)
        if role is None or role in by_lang:
            return None
        by_lang[role] = info
    if set(by_lang) != {'en', 'el'}:
        return None
    en, el = by_lang['en'], by_lang['el']
    for key in ('category', 'urgency', 'severity', 'certainty', 'responseType'):
        if (any(len(i.findall(_CAP+key)) > 1 for i in (en, el))
                or _one(en, _CAP+key) != _one(el, _CAP+key)):
            return None
    if any(_one(en, _CAP+key) is None for key in ('urgency', 'severity', 'certainty')):
        return None
    for key in ('effective', 'onset', 'expires'):
        if (any(len(i.findall(_CAP+key)) > 1 for i in (en, el))
                or any(i.findall(_CAP+key) and _time(_one(i, _CAP+key)) is None
                       for i in (en, el))
                or _time(_one(en, _CAP+key)) != _time(_one(el, _CAP+key))):
            return None
    if any(_time(_one(en, _CAP+key)) is None for key in ('onset', 'expires')):
        return None

    # MeteoAlarm awareness codes are language-independent when present.
    # Never accept a code on one side but absent/different on the other.
    a, b = _awareness_codes(en), _awareness_codes(el)
    if a is None or a != b:
        return None
    # CAP eventCode is optional; when present it must be unambiguous and
    # identical in both languages, including the coding-system names.
    en_codes, el_codes = _named_codes(en, 'eventCode'), _named_codes(el, 'eventCode')
    if en_codes is None or en_codes != el_codes:
        return None

    # Only the observed national label pair is supported in the bilingual path.
    # This is not an EMMA_ID-to-city or marine-zone mapping.
    en_areas, el_areas = en.findall(_CAP+'area'), el.findall(_CAP+'area')
    if len(en_areas) != 1 or len(el_areas) != 1:
        return None
    if (_one(en_areas[0], _CAP+'areaDesc') != 'Cyprus'
            or _one(el_areas[0], _CAP+'areaDesc') not in ('Cyprus', 'Κύπρος')):
        return None
    def geography(area: ET.Element) -> tuple | None:
        codes = []
        for geo in area.findall(_CAP+'geocode'):
            if _one(geo, _CAP+'valueName') == 'EMMA_ID':
                value = _one(geo, _CAP+'value')
                if not value or value in codes:
                    return None
                codes.append(value)
        if not codes:
            return None
        geometry = tuple(tuple((e.text or '').strip() for e in area.findall(_CAP+tag))
                         for tag in ('polygon', 'circle'))
        return tuple(sorted(codes)), geometry
    if geography(en_areas[0]) is None or geography(en_areas[0]) != geography(el_areas[0]):
        return None
    if geography(en_areas[0]) != (('CY001',), ((), ())):
        return None

    # Free text may only veto; it is never displayed or used as evidence.
    english_event = _one(en, _CAP+'event')
    greek_event = _one(el, _CAP+'event')
    if (not _event_label(english_event) or not greek_event
            or _retracted(english_event) or _retracted(greek_event)
            or not _prose_contract(en, _event_colour(en))
            or not _prose_contract(el, _event_colour(en))):
        return None
    if _coloured_event_contract(en):
        # A colour match alone says nothing about the hazard. Accept only
        # explicitly supported complete Greek labels for the same coded event.
        hazard, _, colour = ' '.join(english_event.split()).casefold().rpartition(' ')
        greek_hazard, _, greek_colour = _fold(' '.join(greek_event.split())).rpartition(' ')
        return en if (greek_hazard in _GREEK_HAZARDS.get(hazard, ())
                      and greek_colour == _GREEK_EVENT_COLOURS.get(colour)) else None
    # Bare legacy fixture contract: exact unqualified labels, no awareness codes.
    return en if (english_event.casefold() == 'thunderstorm'
                  and greek_event.casefold() == 'Καταιγίδες'.casefold()
                  and not a) else None


def preview_warning_capture(*, atom: bytes, cap_by_url: Mapping[str, bytes],
                            source: Mapping[str, Any], at_utc: datetime,
                            forecast_date: date) -> OfflinePreview:
    """Create a TEST-ONLY display candidate for complete, attributed Alert pairs.

    The caller-provided transport metadata cannot authenticate remote origin.
    Unknown or mismatched evidence yields NO_POST with no preview text.
    Update/Cancel are not rendered until lifecycle history can be established.
    No city mapping or all-clear claims are made.
    """
    try:
        gate = inspect_captured_pair(atom=atom, cap_by_url=cap_by_url,
                                     source=source, at_utc=at_utc)
        if (gate.reason != 'CAP_MATCH_SENDER_UNVERIFIED_INVENTORY_UNKNOWN'
                or gate.publication_action != 'NO_POST' or gate.state != 'UNKNOWN'
                or any((gate.official_warnings_checked, gate.clean_verified,
                        gate.city_cleared, gate.positive_water_guidance_allowed,
                        gate.national_inventory_complete))):
            return OfflinePreview(reason='R8G1_'+gate.reason)
        if (not isinstance(forecast_date, date) or isinstance(forecast_date, datetime)
                or not isinstance(at_utc, datetime) or at_utc.utcoffset() is None):
            return OfflinePreview(reason='UNKNOWN_FORECAST_DATE')
        instant = at_utc.astimezone(timezone.utc)
        first = datetime.combine(forecast_date, time.min, tzinfo=CY_TZ).astimezone(timezone.utc)
        last = datetime.combine(forecast_date + timedelta(days=1), time.min,
                                tzinfo=CY_TZ).astimezone(timezone.utc)
        root = ET.fromstring(atom)
        rows: list[str] = []
        seen: set[tuple[str, str]] = set()
        for entry in root.findall(_ATOM+'entry'):
            links = [node.attrib.get('href') for node in entry.findall(_ATOM+'link')
                     if node.attrib.get('type') == 'application/cap+xml']
            if len(links) != 1:
                return OfflinePreview(reason='UNKNOWN_CAP_LINK')
            cap = ET.fromstring(cap_by_url[links[0]])
            # R8-G1 validates all documents, but a display interpretation must
            # separately reject lifecycle changes we cannot reconstruct.
            if _one(cap, _CAP+'msgType') != 'Alert':
                return OfflinePreview(reason='UNKNOWN_LIFECYCLE_EVENT')
            info = _select_display_info(cap)
            if info is None:
                return OfflinePreview(reason='UNKNOWN_MULTILINGUAL_OR_MULTIPLE_INFO')
            if (_coloured_event_contract(info)
                    and ' '.join((_one(entry, _CAP+'event') or '').split()).casefold()
                    != ' '.join((_one(info, _CAP+'event') or '').split()).casefold()):
                return OfflinePreview(reason='UNKNOWN_ATOM_CAP_EVENT_MISMATCH')
            areas = info.findall(_CAP+'area')
            if len(areas) != 1:
                return OfflinePreview(reason='UNKNOWN_MULTIPLE_AREAS')
            start = _time(_one(info, _CAP+'onset'))
            expires = _time(_one(info, _CAP+'expires'))
            issued = _time(_one(cap, _CAP+'sent'))
            severity = _one(info, _CAP+'severity')
            label = _event_label(_one(info, _CAP+'event'))
            area = _one(areas[0], _CAP+'areaDesc')
            if (start is None or expires is None or issued is None or
                    severity not in _SEVERITY or not label or not area or len(area)>90
                    or any(ord(x) < 32 or x in '\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069' for x in area)
                    or any(x in area.casefold() for x in ('synthetic', 'test-only'))):
                return OfflinePreview(reason='UNKNOWN_DISPLAY_FIELDS')
            if issued > instant or start >= last or expires <= first or expires <= instant:
                # Do not turn absent/expired/out-of-target items into all-clear.
                return OfflinePreview(reason='UNKNOWN_NO_RELEVANT_ACTIVE_ALERT')
            key = (_one(cap, _CAP+'identifier') or '', _one(cap, _CAP+'sent') or '')
            if key in seen:
                return OfflinePreview(reason='UNKNOWN_DUPLICATE_ALERT')
            seen.add(key)
            phase = 'действует' if instant >= start else 'ожидается'
            start_local, end_local = start.astimezone(CY_TZ), expires.astimezone(CY_TZ)
            rows.append(
                '⚠️ <b>По данным MeteoAlarm</b>: предупреждение о '+label+'. '
                'Уровень: '+_SEVERITY[severity]+'. '+
                'Территория по CAP: '+escape(area, quote=False)+'. '+
                'Период: '+start_local.strftime('%d.%m %H:%M')+'–'+end_local.strftime('%d.%m %H:%M')+
                ' (время Кипра); '+phase+'.'
            )
        if not rows or len(rows) > 8 or sum(map(len,rows)) > 2500:
            return OfflinePreview(reason='UNKNOWN_PREVIEW_LIMIT')
        return OfflinePreview(classification='OFFLINE_OBSERVED_ALERT_CANDIDATE',
                              reason='CAP_MATCH_UNAUTHENTICATED_SOURCE',
                              text='\n'.join(rows)+'\nИсточник: '+FEED_URL)
    except (TypeError, ValueError, AttributeError, KeyError, OverflowError, ET.ParseError):
        return OfflinePreview(reason='UNKNOWN_INVALID_PREVIEW_INPUT')


def insert_preview_after_format_v2(final_text: str, preview: OfflinePreview) -> str:
    """TEST-ONLY insertion seam AFTER FORMAT_V2 and sanitizer, BEFORE hashtags.

    Does not change any VayboMeter rating, forecast warning, or marine text.
    This helper is not wired into the publisher and does not grant posting.
    """
    if not isinstance(final_text, str) or not isinstance(preview, OfflinePreview):
        raise TypeError('offline preview requires typed inputs')
    if (preview.classification != 'OFFLINE_OBSERVED_ALERT_CANDIDATE'
            or not preview.text or preview.text in final_text):
        return final_text
    lines = final_text.splitlines()
    marker = next((i for i, s in enumerate(lines) if s.strip().startswith('#')),len(lines))
    lines[marker:marker] = [preview.text]
    return '\n'.join(lines)
