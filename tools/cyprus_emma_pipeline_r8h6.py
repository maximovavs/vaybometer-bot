"""Offline-only Cyprus EMMA preview seam for the real post finalization boundary.

The production CLI invokes this with no preview and always receives the original
text. This helper never fetches data or authorizes a Telegram publication.
"""
from __future__ import annotations

from dataclasses import replace
from typing import Callable, Any, Mapping
from datetime import date, datetime
from xml.etree import ElementTree as ET


# Restrict CAP.event to an exact, conservative first-release taxonomy.
# The H5 substring classifier is intentionally unchanged; it must not turn
# "no thunderstorm" or "warning cancelled" into a candidate warning.
_ALLOWED_EVENT_NAMES = frozenset({
    "thunderstorm", "heavy rain", "strong wind",
    "high temperature", "snowfall", "fog",
})


def guarded_preview_warning_capture(*, atom: bytes,
                                    cap_by_url: Mapping[str, bytes],
                                    source: Mapping[str, Any],
                                    at_utc: datetime,
                                    forecast_date: date):
    """Tighten unchanged H5 preview against ambiguous CAP.event strings.

    UNKNOWN / NO_POST is retained for ALL outcomes. No live fetch is allowed.
    """
    try:
        from cyprus_emma_preview_r8h5 import (
            OfflinePreview, preview_warning_capture, CAP)
    except ImportError:
        from tools.cyprus_emma_preview_r8h5 import (
            OfflinePreview, preview_warning_capture, CAP)

    result = preview_warning_capture(atom=atom, cap_by_url=cap_by_url,
        source=source, at_utc=at_utc, forecast_date=forecast_date)
    if result.classification != "OFFLINE_OBSERVED_ALERT_CANDIDATE":
        return result
    try:
        for xml in cap_by_url.values():
            root = ET.fromstring(xml)
            events = root.findall(f"{{{CAP}}}info/{{{CAP}}}event")
            if (len(events) != 1 or events[0].text is None
                    or events[0].text.strip().casefold() not in _ALLOWED_EVENT_NAMES):
                return OfflinePreview(reason="UNKNOWN_EVENT_TAXONOMY")
    except (TypeError, ValueError, AttributeError, ET.ParseError):
        return OfflinePreview(reason="UNKNOWN_EVENT_TAXONOMY")
    return replace(result, reason="R8H6_EVENT_TAXONOMY_OK_OFFLINE")


def apply_emma_preview_offline(final_sanitized_text: str, preview: object = None,
                               *, offline_preview: bool = False,
                               sanitizer: Callable[[str], object] | None = None) -> str:
    """For *offline review only*, insert a candidate, sanitize again, fail closed.

    The caller must first finish FORMAT_V2 and its usual sanitizer. The output
    remains a preview, and must not be interpreted as publication permission.
    The default production invocation is an exact no-op even with untrusted data.
    """
    if not isinstance(final_sanitized_text, str):
        raise TypeError('expected finalized sanitized text')
    if not offline_preview or preview is None:
        return final_sanitized_text

    try:
        from cyprus_emma_preview_r8h5 import OfflinePreview, insert_preview_after_format_v2
    except ImportError:
        from tools.cyprus_emma_preview_r8h5 import OfflinePreview, insert_preview_after_format_v2

    if (not isinstance(preview, OfflinePreview)
            or preview.publication_action != 'NO_POST'
            or preview.classification != 'OFFLINE_OBSERVED_ALERT_CANDIDATE'
            or preview.reason != 'R8H6_EVENT_TAXONOMY_OK_OFFLINE'
            or any((preview.official_warnings_checked, preview.clean_verified,
                    preview.city_cleared, preview.positive_water_guidance_allowed,
                    preview.national_inventory_complete))
            or not preview.text.startswith('⚠️ <b>По данным MeteoAlarm</b>:')
            or '\nИсточник: https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-cyprus' not in preview.text):
        return final_sanitized_text

    # Avoid trusting a forged dataclass for safety authorizations. The object
    # can only be inspected, never sent by this helper.
    if sanitizer is None:
        from post_safety import sanitize_post_text
        sanitizer = sanitize_post_text

    candidate = insert_preview_after_format_v2(final_sanitized_text, preview)
    if candidate == final_sanitized_text:
        return final_sanitized_text
    checked = sanitizer(candidate)
    if not isinstance(getattr(checked, 'text', None), str):
        return final_sanitized_text
    safe = checked.text
    # An altered/incomplete warning must not be accepted as a rendered warning.
    expected_alert_count = preview.text.count('⚠️ <b>По данным MeteoAlarm</b>:')
    if (not 1 <= expected_alert_count <= 8
            or safe.count(preview.text) != 1
            or safe.count('По данным MeteoAlarm') != expected_alert_count):
        return final_sanitized_text
    # If final sanitization removes/reorders preexisting content (particularly
    # weather warnings, ratings and marine guards), reject rather than patch it.
    original_lines = [line for line in final_sanitized_text.splitlines() if line.strip()]
    new_lines = [line for line in safe.splitlines() if line.strip()]
    cursor = 0
    for existing in original_lines:
        try:
            cursor = new_lines.index(existing, cursor) + 1
        except ValueError:
            return final_sanitized_text
    return safe
