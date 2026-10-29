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
            infos = cap.findall(_CAP+'info')
            if len(infos) != 1:
                return OfflinePreview(reason='UNKNOWN_MULTILINGUAL_OR_MULTIPLE_INFO')
            info = infos[0]
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
