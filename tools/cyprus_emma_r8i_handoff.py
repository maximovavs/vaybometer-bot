"""R8-I: bounded, OFFLINE-ONLY warning handoff for Cyprus MeteoAlarm.

This is NOT a trusted publisher feed. G1 verifies structural coherence,
caller-supplied transport metadata, and hashes, NOT authoritative CAP.sender,
true network provenance, completeness or city mapping. Neither this module
nor the existing H6 publisher no-op may promote those assertions.

Until an independently verified, pinned Cyprus sender/source binding is
provided in a separately authorised change, all publication outputs remain
UNCHANGED and every decision remains UNKNOWN / NO_POST. The optional candidate
is REVIEW-ONLY evidence, never a publication payload.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
import hashlib
from http.client import HTTPException
import json
import ssl
import urllib.request
from email.utils import parsedate_to_datetime
from xml.etree import ElementTree as ET
from typing import Any, Callable, Mapping

try:
    from .cyprus_cap_r8g1_contract import (inspect_captured_pair, FEED_URL, ATOM, CAP,
                                            CY_CAP_PATH, MAX_BYTES, _xml, _exact_https_url,
                                            CAP_MEDIA_TYPES)
    from .cyprus_emma_pipeline_r8h6 import (apply_emma_preview_offline,
                                            guarded_preview_warning_capture)
except ImportError:
    from cyprus_cap_r8g1_contract import (inspect_captured_pair, FEED_URL, ATOM, CAP,
                                           CY_CAP_PATH, MAX_BYTES, _xml, _exact_https_url,
                                           CAP_MEDIA_TYPES)
    from cyprus_emma_pipeline_r8h6 import (apply_emma_preview_offline,
                                          guarded_preview_warning_capture)


@dataclass(frozen=True)
class WarningHandoff:
    """Safe publication projection and separate non-publishable review evidence."""
    publisher_text: str
    state: str = 'UNKNOWN'
    publication_action: str = 'NO_POST'
    reason: str = 'NO_CAPTURE'
    offline_candidate: str = ''
    capture_digest: str = ''
    official_warnings_checked: bool = False
    clean_verified: bool = False
    city_cleared: bool = False
    positive_water_guidance_allowed: bool = False
    national_inventory_complete: bool = False


def _capture_digest(atom: bytes, caps: Mapping[str, bytes],
                    source: Mapping[str, Any]) -> str:
    """Fingerprint raw bytes plus claimed transport metadata; NOT source proof."""
    h = hashlib.sha256()
    for url, blob in [('atom', atom), *sorted(caps.items())]:
        name = url.encode('utf-8')
        h.update(len(name).to_bytes(4, 'big'))
        h.update(name)
        h.update(len(blob).to_bytes(8, 'big'))
        h.update(blob)
    manifest = json.dumps(source, sort_keys=True, ensure_ascii=False,
                          separators=(',', ':'), allow_nan=False).encode('utf-8')
    h.update(len(manifest).to_bytes(8, 'big'))
    h.update(manifest)
    return h.hexdigest()


def inspect_offline_capture(*, final_sanitized_text: str, atom: bytes | None = None,
                            cap_by_url: Mapping[str, bytes] | None = None,
                            source: Mapping[str, Any] | None = None,
                            at_utc: datetime | None = None,
                            forecast_date: date | None = None,
                            sanitizer: Callable[[str], object] | None = None,
                            attribute_translation: bool = False
                            ) -> WarningHandoff:
    """Re-use G1/H5/H6; never transform publisher text into an official claim.

    Only an explicit offline caller supplying a complete capture can obtain a
    non-publishable review candidate. The publisher is always unchanged.
    """
    if not isinstance(final_sanitized_text, str):
        raise TypeError('finalized text must be str')
    safe = WarningHandoff(publisher_text=final_sanitized_text)
    if all(v is None for v in (atom, cap_by_url, source, at_utc, forecast_date)):
        return safe
    if (not isinstance(atom, bytes) or not isinstance(cap_by_url, Mapping)
            or not isinstance(source, Mapping) or not isinstance(at_utc, datetime)
            or not isinstance(forecast_date, date)
            or isinstance(forecast_date, datetime)
            or at_utc.utcoffset() is None):
        return WarningHandoff(final_sanitized_text, reason='INVALID_CAPTURE_ENVELOPE')
    try:
        if any(not isinstance(k, str) or not isinstance(v, bytes)
               for k, v in cap_by_url.items()):
            return WarningHandoff(final_sanitized_text, reason='INVALID_CAPTURE_BYTES')
        digest = _capture_digest(atom, cap_by_url, source)
        gate = inspect_captured_pair(atom=atom, cap_by_url=cap_by_url,
                                     source=source, at_utc=at_utc)
        if (gate.reason != 'CAP_MATCH_SENDER_UNVERIFIED_INVENTORY_UNKNOWN'
                or gate.state != 'UNKNOWN' or gate.publication_action != 'NO_POST'
                or any((gate.official_warnings_checked, gate.clean_verified,
                        gate.city_cleared, gate.positive_water_guidance_allowed,
                        gate.national_inventory_complete))):
            return WarningHandoff(final_sanitized_text, reason=gate.reason,
                                  capture_digest=digest)
        preview = guarded_preview_warning_capture(
            atom=atom, cap_by_url=cap_by_url, source=source,
            at_utc=at_utc, forecast_date=forecast_date)
        if (preview.classification != 'OFFLINE_OBSERVED_ALERT_CANDIDATE'
                or preview.publication_action != 'NO_POST'
                or preview.reason != 'R8H6_EVENT_TAXONOMY_OK_OFFLINE'
                or any((preview.official_warnings_checked, preview.clean_verified,
                        preview.city_cleared, preview.positive_water_guidance_allowed,
                        preview.national_inventory_complete))):
            return WarningHandoff(final_sanitized_text, reason=preview.reason,
                                  capture_digest=digest)
        if sanitizer is None:
            # A missing real sanitizer must fail closed, never fall back to an
            # identity sanitizer or allow a non-sanitized warning candidate.
            try:
                from post_safety import sanitize_post_text
            except ImportError:
                return WarningHandoff(final_sanitized_text,
                                      reason='OFFLINE_SANITIZER_UNAVAILABLE',
                                      capture_digest=digest)
            sanitizer = sanitize_post_text
        if attribute_translation:
            # Exact source/CC BY attribution, and disclosure of adaptation.
            # H6 sanitizes and checks the complete block INCLUDING this footer.
            preview = replace(preview, text=preview.text +
                '\nДанные: MeteoAlarm, CC BY 4.0 '
                '(https://creativecommons.org/licenses/by/4.0/). '
                'Русский перевод и сокращение; неофициальная адаптация.')
        candidate = apply_emma_preview_offline(
            final_sanitized_text, preview, offline_preview=True,
            sanitizer=sanitizer)
        if candidate == final_sanitized_text:
            return WarningHandoff(final_sanitized_text,
                                  reason='OFFLINE_SANITIZER_REJECTED',
                                  capture_digest=digest)
        # Even a structurally flawless captured alert cannot be called
        # authenticated, nor can it authorize a positive marine recommendation.
        return WarningHandoff(final_sanitized_text,
                              reason='CAP_SENDER_AND_SOURCE_UNVERIFIED',
                              offline_candidate=candidate, capture_digest=digest)
    except (TypeError, ValueError, AttributeError, KeyError, OverflowError):
        return WarningHandoff(final_sanitized_text, reason='INVALID_CAPTURE_ENVELOPE')


# R8-I public aggregator acquisition. Registered feed: WMO Alerting Authorities
# recId=40 and the MeteoAlarm country feed directory. This verifies the
# distribution CHANNEL, never the cryptographic origin of CAP.sender, national
# warning completeness or independently established EMMA-to-city mappings.
# The publisher does NOT call this in production. No network I/O occurs at import.
class CaptureRejected(ValueError):
    pass


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _verified_https_get(url: str) -> dict[str, Any]:
    """HTTPS with normal CA/hostname verification; no redirects or credentials."""
    opener = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ssl.create_default_context()),
        _RejectRedirects())
    req = urllib.request.Request(url, headers={
        'Accept': '*/*',  # Feed negotiates Atom/CAP using this header.
        'User-Agent': 'VayboMeter-Cyprus-EMMA/1.0',
    })
    with opener.open(req, timeout=7) as res:
        content = res.read(MAX_BYTES + 1)
        received = datetime.now(timezone.utc)
        return {'body': content, 'url': res.geturl(),
                'status': res.status, 'content_type': res.headers.get('Content-Type'),
                'http_date': res.headers.get('Date'), 'received_utc': received}


def _capture_one(url: str, mime: str, now: datetime,
                 fetch: Callable[[str], Mapping[str, Any]]) -> tuple[bytes, dict[str, Any]]:
    # Enforce URL policy *before* a network request: no SSRF, port, query,
    # credentials, encoded host, downgrade, or foreign CAP cross-link.
    if not (url == FEED_URL if mime == 'application/atom+xml'
            else _exact_https_url(url, CY_CAP_PATH)):
        raise CaptureRejected('UNTRUSTED_ENDPOINT')
    res = fetch(url)
    if not isinstance(res, Mapping) or res.get('url') != url or res.get('status') != 200:
        raise CaptureRejected('HTTP_OR_REDIRECT')
    body = res.get('body')
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_BYTES:
        raise CaptureRejected('BODY_MISSING_OR_TOO_LARGE')
    content_type = res.get('content_type')
    actual_mime = (content_type.split(';', 1)[0].strip().lower()
                   if isinstance(content_type, str) else '')
    # Only CAP XML gets the narrow compatibility allowance; Atom stays strict.
    if actual_mime not in (CAP_MEDIA_TYPES if mime == 'application/cap+xml' else {mime}):
        raise CaptureRejected('CONTENT_TYPE')
    received = res.get('received_utc')
    try:
        server = parsedate_to_datetime(res.get('http_date'))
        if (not isinstance(received, datetime) or received.utcoffset() is None
                or server.utcoffset() is None or now.utcoffset() is None):
            raise ValueError('missing timezone')
        # Bound clock skew, delay, stale and future replay. G1 also validates
        # feed.updated and every CAP document against its source metadata.
        if (abs((received-now).total_seconds()) > 300
                or abs((server-received).total_seconds()) > 300):
            raise ValueError('stale/crossed clocks')
    except (TypeError, ValueError, OverflowError, IndexError) as e:
        raise CaptureRejected('HTTP_DATE_OR_FRESHNESS') from e
    meta = {'http_status': 200, 'redirect_followed': False,
            'credentials_sent': False, 'content_type': content_type,
            'sha256': hashlib.sha256(body).hexdigest(),
            'received_utc': received.astimezone(timezone.utc).isoformat(),
            'http_date_utc': server.astimezone(timezone.utc).isoformat()}
    return body, meta


def acquire_registered_cyprus_pair(*, at_utc: datetime,
                                   fetch: Callable[[str], Mapping[str, Any]] | None = None
                                   ) -> tuple[bytes, dict[str, bytes], dict[str, Any]]:
    """Capture exact public Atom/CAP resources; reject any incomplete set.

    `fetch` is exclusively an offline test seam. The default transport performs
    real HTTPS validation. Neither the caller's forged metadata nor CAP.sender
    proves who originally authored a warning. Do not interpret empty feeds as
    an all-clear; this function fails closed on empty/partial results.
    """
    if not isinstance(at_utc, datetime) or at_utc.utcoffset() is None:
        raise CaptureRejected('UNVERIFIED_CLOCK')
    transport = _verified_https_get if fetch is None else fetch
    atom, feed_meta = _capture_one(FEED_URL, 'application/atom+xml', at_utc, transport)
    root = _xml(atom, f'{{{ATOM}}}feed')
    if root is None:
        raise CaptureRejected('INVALID_ATOM_XML')
    entries = root.findall(f'{{{ATOM}}}entry')
    if not 1 <= len(entries) <= 8:
        raise CaptureRejected('EMPTY_OR_UNBOUNDED_FEED')
    cap_urls = []
    for ent in entries:
        links = [el.get('href') for el in ent.findall(f'{{{ATOM}}}link')
                 if el.get('type') == 'application/cap+xml']
        if len(links) != 1 or not _exact_https_url(links[0], CY_CAP_PATH):
            raise CaptureRejected('INVALID_CAP_LINK')
        cap_urls.append(links[0])
    if len(set(cap_urls)) != len(cap_urls):
        raise CaptureRejected('DUPLICATE_CAP_LINK')
    caps, evidence = {}, {}
    for url in cap_urls:
        caps[url], evidence[url] = _capture_one(url, 'application/cap+xml', at_utc, transport)
    source = dict(feed_meta, url=FEED_URL, cap_evidence=evidence)
    return atom, caps, source


def inspect_registered_cyprus_offline(*, final_sanitized_text: str,
                                     at_utc: datetime, forecast_date: date,
                                     sanitizer: Callable[[str], object] | None = None,
                                     fetch: Callable[[str], Mapping[str, Any]] | None = None
                                     ) -> WarningHandoff:
    """Complete source-bound informational warning PREVIEW; production OFF.

    A success is an accurate *MeteoAlarm-distributed* warning excerpt, NOT
    authority-sender authentication, inventory clearance, or permission to post.
    All errors preserve caller text and UNKNOWN/NO_POST.
    """
    if not isinstance(final_sanitized_text, str):
        raise TypeError('finalized text must be str')
    try:
        atom, caps, source = acquire_registered_cyprus_pair(
            at_utc=at_utc, fetch=fetch)
        handoff = inspect_offline_capture(
            final_sanitized_text=final_sanitized_text, atom=atom,
            cap_by_url=caps, source=source, at_utc=at_utc,
            forecast_date=forecast_date, sanitizer=sanitizer,
            attribute_translation=True)
        if handoff.offline_candidate:
            return replace(handoff, reason='REGISTERED_METEOALARM_INFORMATION_PREVIEW_ONLY')
        return handoff
    except (CaptureRejected, TypeError, ValueError, AttributeError, KeyError,
            OverflowError, OSError, TimeoutError, HTTPException):
        return WarningHandoff(final_sanitized_text,
                              reason='SOURCE_CAPTURE_REJECTED')


# R8-J: bounded publisher integration of source-bound MeteoAlarm-distributed alerts.
# It is never called when the feature flag in safe_test_post.py is OFF.
from datetime import timedelta
from xml.etree import ElementTree as ET
import re


@dataclass(frozen=True)
class PublisherWarningDecision:
    publisher_text: str
    warning_attached: bool = False
    block_publication: bool = False
    reason: str = 'DISABLED'
    expires_utc: datetime | None = None


_MARINE_ACTIVITY = re.compile(r'\bSUP\b|\bkite\b|\bsurf\b|кайт|с[её]рф|сап|виндс[её]рф', re.I)
_MARINE_NO_GO = re.compile(r'не\s+(?:рекоменд|подход|подтвержден)|опасн|запрещ|\bSTOP\b|\bNO_GO\b|н/д|🚫|⛔|❌', re.I)
_MARINE_GO = re.compile(r'✅|🟢|\bGO\b|\bможно\b|\bподходит\b|\bотличн|\bхорош|\bидеальн', re.I)

_MARINE_TAGS = re.compile(r'<[^>]+>|[^\w\s/]', re.U)
_MARINE_HEADING = re.compile(
    r'(?:sup|kite|surf|кайт|с[её]рф|сап|винг|винд|виндс[её]рф)'
    r'(?:\s*/\s*(?:sup|kite|surf|кайт|с[её]рф|сап|винг|винд|виндс[её]рф))*', re.I)
_MARINE_UNKNOWN = re.compile(r'данных[^\n]{0,40}недостаточно|недостаточно данных', re.I)


# Positive advice remains a GO even when a caution or a hazard is present.
# Remove explicit negations only for this positive-claim check.
_MARINE_AFFIRMATIVE = re.compile(
    r'рабочи[а-яё]*\s+окн|рекоменд(?:у[а-яё]+|ован[а-яё]*)|только\s+(?:для\s+)?опытн|'
    r'коротк[а-яё]*\s+сесси|безопасн[а-яё]*|разреш[а-яё]*|'
    r'подход[а-яё]*|катани[а-яё]*\s+возможн', re.I)
_MARINE_NEGATED = re.compile(
    r'не\s+(?:рекоменд[а-яё]*|подход[а-яё]*|безопасн[а-яё]*|разреш[а-яё]*)|'
    r'безопасность\s+не\s+подтвержден[а-яё]*|небезопасн[а-яё]*|не\s+выход[а-яё]*', re.I)
_MARINE_EXPLICIT_CAUTION = re.compile(
    r'лучше\s+отлож|отложить|не\s+выход[а-яё]*|не\s+безопасн|небезопасн', re.I)


def _marine_line_conflict(line: str, *, outage: bool = False) -> bool:
    plain = _MARINE_TAGS.sub(' ', line).strip()
    # Tags/punctuation must not conceal positive advice. Keep emoji GO markers.
    probe = _MARINE_NEGATED.sub('', re.sub(r'<[^>]+>', '', line))
    if (_outage_positive(line) if outage else
            _MARINE_GO.search(probe) or _MARINE_AFFIRMATIVE.search(probe)):
        return True
    return not (_MARINE_NO_GO.search(plain) or _MARINE_UNKNOWN.search(plain)
                or _MARINE_EXPLICIT_CAUTION.search(plain))


def _marine_contradiction(text: str, *, outage: bool = False) -> bool:
    """Veto affirmative/ambiguous marine advice, including heading descendants.

    Hazard words and experience qualifications are not safety authorization.
    Only genuinely restrictive/unknown advice is allowed beside a warning.
    """
    def positive(line: str) -> bool:
        if outage:
            return _outage_positive(line)
        probe = _MARINE_NEGATED.sub('', re.sub(r'<[^>]+>', '', line))
        return bool(_MARINE_GO.search(probe) or _MARINE_AFFIRMATIVE.search(probe))

    in_section = False
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if _MARINE_ACTIVITY.search(line):
            if _MARINE_HEADING.fullmatch(_MARINE_TAGS.sub('', line).strip()):
                if (_outage_positive(line) if outage else _MARINE_GO.search(line)):
                    return True
                in_section = True
                continue
            if _marine_line_conflict(line, outage=outage):
                return True
            # Retain K4's inline lookahead as well as section descendants.
            for following in lines[i + 1:i + 3]:
                if _outage_boundary(following) or _MARINE_ACTIVITY.search(following):
                    break
                if (positive(following)
                        or (_OUTAGE_MARINE_FOLLOWUP.search(following)
                            and _marine_line_conflict(following, outage=outage))):
                    return True
        elif in_section:
            if not line.strip():
                continue
            if _outage_boundary(line):
                # A city prefix in a marine section is not an escape hatch
                # for a recommendation on that very line.
                if _OUTAGE_CITY.match(line.lstrip()):
                    if positive(line):
                        return True
                in_section = False
            elif _marine_line_conflict(line, outage=outage):
                return True
    return False


_OUTAGE_MARINE_CAUTION = re.compile(
    r'не\s+(?:рекоменд|подход|подтвержден)|'
    r'лучше\s+отлож|отложить|только\s+опытн|'
    r'шторм|шквал|гроз|сильн\w*\s+ветер|не\s+выход\w*|'
    r'\b(?:опасн|небезопасн)|не\s+безопасн|риск|запрещ|\bSTOP\b|\bNO_GO\b|н/д|🚫|⛔|❌', re.I)
_OUTAGE_MARINE_GO = re.compile(
    r'✅|🟢|\bGO\b|\bможно\b|\bподходит\b|'
    # A good-visibility observation does not turn a caution into marine GO.
    # Require a nearby explicit water-sport cue; ordinary good visibility is
    # not an affirmative marine recommendation.
    r'\bотличн|\bхорош\w*(?=[^\n]{0,80}\b(?:кататься|катания|'
    r'с[её]рф\w*|кайт\w*|на\s+вод[уе]|выходить\s+на))|'
    r'\bидеальн|\bразрешен', re.I)
_OUTAGE_MARINE_FOLLOWUP = re.compile(
    r'кататься|на\s+воду|выходить\s+на|\bSUP\b|кайт|с[её]рф|\bсап\b|'
    r'доск(?:е|ах|у)|парус', re.I)
_OUTAGE_SECTION = ('🏙', '🏖', '🏞', '📍', '🌆', '🏘', '🗺', '🌡', '💬',
                   '✨', '☀️', '🌇', '🌊', '#', '⚠️', '🚨', '❗', '🛑')
_OUTAGE_CITY = re.compile(
    r'^(?:Лимассол|Ларнака|Пафос|Паралимни|Айя-Напа|Протарас|'
    r'Никосия|Тродос|Полис|Пейя|Кирения)\s*:', re.I)
_OUTAGE_SAFE_MARINE = ('🏄 SUP / кайт / сёрф: безопасность не подтверждена; '
                       'источник официальных предупреждений недоступен.')


def _outage_positive(line: str) -> bool:
    """Outage GO is unsupported, but benign visibility is not water advice."""
    probe = _MARINE_NEGATED.sub('', re.sub(r'<[^>]+>', '', line))
    probe = re.sub(r'\bхорош[а-яё]*\s+видимост[а-яё]*\b', '', probe, flags=re.I)
    return bool(_MARINE_GO.search(probe) or _OUTAGE_MARINE_GO.search(probe)
                or _MARINE_AFFIRMATIVE.search(probe))


def _outage_boundary(line: str) -> bool:
    """Stop at next city, section, or independent non-marine advice."""
    s = line.lstrip()
    return (not s or s.startswith(_OUTAGE_SECTION)
            or bool(_OUTAGE_CITY.match(s))
            or (s.startswith(('✅ План', '✅ Прогулк', '✅ Совет'))
                and not _OUTAGE_MARINE_FOLLOWUP.search(s)))


def _outage_replace_marine_advice(line: str) -> str:
    """Remove unsupported GO without erasing hazard, wind, or direction."""
    measures = re.findall(
        r'(?i)(?:ветер|порывы|волн[аы])[^;\n.]{0,65}?\d+(?:[.,]\d+)?\s*(?:м/с|км/ч|м)\b',
        line)
    cautions = []
    for marker, label in (
        (r'\bопасно\b', 'опасно'),
        (r'\bриск\b', 'риск'),
        (r'лучше\s+отлож', 'лучше отложить'),
        (r'не\s+рекоменд', 'не рекомендуется'),
        (r'шторм\w*', 'шторм'),
        (r'шквал\w*', 'шквалы'),
        (r'гроз\w*', 'гроза'),
        (r'сильн\w*\s+ветер', 'сильный ветер'),
        (r'не\s+выход\w*', 'не выходить на воду'),
    ):
        if re.search(marker, line, re.I):
            cautions.append(label)
    directions = re.findall(
        r'\(([^()\n]*(?:ветер|берег|волны)[^()\n]*)\)', line, re.I)
    directions = [d.strip() for d in directions
                  if not _outage_positive(d)]
    # Do not repeat a gust value already present within a preserved direction.
    facts = [m for m in measures if not any(m.casefold() in d.casefold()
                                          for d in directions)] + directions
    return _OUTAGE_SAFE_MARINE + (
        ' Ограничения прогноза: ' + ', '.join(cautions) + '.'
        if cautions else '') + (
        ' По прогнозу: ' + '; '.join(dict.fromkeys(facts)) + '.'
        if facts else '')


# The newly recognized experience-qualified GO may be rewritten only when its
# advice is a discrete clause. Preserve the whole suffix (facts, direction,
# time, location); let the final veto block unrecognized mixed prose instead
# of inventing a lossy summary of it.
_OUTAGE_EXPERIENCE_CLAUSE = re.compile(
    r'(?P<lead>:\s*)только\s+(?:для\s+)?опытным'
    r'(?:\s+и\s+короткая\s+сессия)?(?=\s*(?:[;•.]|$))', re.I)


# FORMAT_V2 tests document this complete, fact-free recommendation clause. Only
# neutralize this known shape: retain the activity and the spot-check advice
# verbatim. A different/mixed clause must still reach the existing final veto.
_OUTAGE_SURF_WINDOWS_CLAUSE = re.compile(
    r'(?P<lead>^\s*(?:•\s*[^:\n•—]+?\s+—\s*)?🏄\s*с[её]рф:\s*)'
    r'есть рабочие окна по волне'
    r'(?=;\s*проверить конкретный спот\.\s*$)', re.I)


def _outage_rewrite_marine(line: str) -> str:
    rewritten = _OUTAGE_SURF_WINDOWS_CLAUSE.sub(
        r'\g<lead>безопасность не подтверждена', line, count=1)
    if rewritten != line:
        return rewritten
    probe = _MARINE_NEGATED.sub('', re.sub(r'<[^>]+>', '', line))
    if _OUTAGE_MARINE_GO.search(probe) or not _outage_positive(line):
        return _outage_replace_marine_advice(line)
    # K5's additional affirmative vocabulary must not cause a lossy rewrite.
    # Unsupported forms remain in diagnostic text and fail the final veto.
    rewritten = _OUTAGE_EXPERIENCE_CLAUSE.sub(
        r'\g<lead>безопасность не подтверждена', line, count=1)
    return re.sub(r'(:\s*)безопасно(?=\s*(?:[,;.—]|$))',
                  r'\1безопасность не подтверждена', rewritten, count=1, flags=re.I)


def _outage_neutralizes_marine_go(text: str) -> str:
    """Conservatively neutralize marine GO inside its own city/section."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not _MARINE_ACTIVITY.search(line):
            continue
        cautious = bool(_OUTAGE_MARINE_CAUTION.search(line))
        if _MARINE_HEADING.fullmatch(_MARINE_TAGS.sub('', line).strip()):
            # Keep even a positive heading for the final veto and its descendants.
            continue
        if _outage_positive(line) or not cautious:
            lines[i] = _outage_rewrite_marine(line)
        # Stop at each new city's weather, section, or a new marine activity.
        # That next marine line must receive its OWN fact-preserving pass.
        for j in range(i + 1, min(i + 3, len(lines))):
            following = lines[j]
            if _MARINE_ACTIVITY.search(following) or _outage_boundary(following):
                break
            if (_OUTAGE_MARINE_FOLLOWUP.search(following)
                    and (_outage_positive(following)
                         or not _OUTAGE_MARINE_CAUTION.search(following))):
                lines[j] = _outage_rewrite_marine(following)
    return '\n'.join(lines)


def _outage_unresolved_marine_go(text: str) -> bool:
    """Fail closed if a marine GO or ambiguous claim survives sanitization."""
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if not _MARINE_ACTIVITY.search(line):
            continue
        if _MARINE_HEADING.fullmatch(_MARINE_TAGS.sub('', line).strip()) and not _outage_positive(line):
            continue
        if (_outage_positive(line)
                or not _OUTAGE_MARINE_CAUTION.search(line)):
            return True
        for following in lines[i + 1:i + 3]:
            if _MARINE_ACTIVITY.search(following) or _outage_boundary(following):
                break
            if (_OUTAGE_MARINE_FOLLOWUP.search(following)
                    and (_outage_positive(following)
                         or not _OUTAGE_MARINE_CAUTION.search(following))):
                return True
    return False


# ON-path editorial projection beside an active or unresolved CAP warning.
# Weather facts, cautions, marine lines and hashtags are kept verbatim. Only
# reassuring/positive editorial claims are removed, and a numeric VayboMeter
# score (an assessment, not a weather fact) is replaced by an explicit "н/д".
# Cautious FORMAT_V2 advice is retained; an inseparable positive claim and
# fact must block rather than lose the fact. No rating or threshold changes.
_EMMA_SCORE_RE = re.compile(r'^✨ VayboMeter(?: сегодня| завтра)?:\s*\d+(?:[.,]\d+)?/10\b')
_EMMA_REASSURANCE_RE = re.compile(
    r'комфортно для|держится ровно|без (?:заметных |резких )?(?:перепадов|сюрпризов)|'
    r'сюрпризов погода не обещает|предсказуем|спокойн[а-яё]* (?:день|погод|фон)|'
    r'(?<!не )\bобычный день\b|обычные дела и прогулки|в плюсе|благоприятн[а-яё]* (?:погод|день|услов)|'
    r'день можно планировать|можно использовать для планирования|'
    r'погода располагает к|прекрасн|отличн[а-яё]* (?:погод|день|услов)|идеальн|чудесн|замечательн|'
    r'хорош[а-яё]* (?:погод|день|услов)|для отдыха на улице|'
    r'можно (?:смело |спокойно )?(?:гулять|купаться|отправляться|выходить|планировать)|'
    r'предупреждений нет|нет предупреждений|без (?:опасности|риска)|'
    r'безопасн[а-яё]* (?:погод|день|услов)|погода не обещает', re.I)
_EMMA_CLAUSE_SPLIT = re.compile(r'\s+—\s+|;\s+|,\s+(?:но|а)\s+|(?<=[.!?])\s+(?=[А-ЯЁ])')
# A positive clause containing forecast data or warnings cannot be discarded.
# If it cannot be separated without changing meaning, fail closed instead.
_EMMA_FACT_OR_WARNING = re.compile(
    r'\d|⚠|🚨|❗|гроз|порыв|шквал|шторм|ливн|осадк|дожд|снег|град|'
    r'ветер|ветр|видимост|туман|дымк|пыл[ьи]|жар[ауые]|температур|'
    r'волн|давлени[ея]|\bUV\b|\bAQI\b|\bPM(?:2\.5|10)?\b|'
    r'опасност|предупреждени|риск|не\s+(?:выход|куп|рекоменд)|'
    r'облач|пасмур|ясно|солнеч|влажност|мороз|гололед|гололёд|сухо|тепло|прохлад|'
    r'ливень|ливня|пыль|замороз|накат|прибой|направлени|восточн|западн|северн|южн', re.I)
_EMMA_CITY_MENTION = re.compile(
    r'лимассол|ларнак|пафос|никоси|тродос|айя-нап|протарас|полис|пейя|паралимни|кирен', re.I)
_EMMA_CITY_PREFIX = re.compile(
    r'^(?:(?:⚠️?|🚨|❗|🏙️?|🏖️?|📍)\s*)*'
    r'(?:Лимассол|Ларнака|Пафос|Паралимни|Айя-Напа|Протарас|Никосия|Тродос|Полис|Пейя|Кирения)\s*:\s*', re.I)
_EMMA_NEUTRAL_SCORE = '✨ VayboMeter: н/д — официальные предупреждения требуют проверки.'
_EMMA_UNVERIFIED = (
    '⚠️ Статус официальных предупреждений не подтверждён. '
    'Это не означает отсутствия опасности.')
_EMMA_FORECAST_CAUTION = (
    '⚠️ Показатели прогноза модели не исключают опасных явлений; '
    'сверяйте официальные предупреждения и местные условия перед выходом.')


def _without_reassurance(value: str) -> str | None:
    """Remove separable editorial claims; None means inseparable warning/fact."""
    # Keep a restrictive qualifier or an explicit check rather than dropping it.
    value = re.sub(r',\s*(кроме\s+[^;]+)$', r'; Оговорка: \1', value, flags=re.I)
    value = re.sub(r',\s*с короткой проверкой ([^;]+)$',
                   r'; Рекомендация: короткая проверка \1', value, flags=re.I)
    city = _EMMA_CITY_PREFIX.match(value)
    prefix = city.group() if city else ''
    parts = _EMMA_CLAUSE_SPLIT.split(value[len(prefix):])
    kept = []
    removed_location = False
    for part in parts:
        if _EMMA_REASSURANCE_RE.search(part):
            # A leading warning icon is retained below on the surviving fact.
            content = re.sub(r'^⚠️?\s*', '', part)
            if _EMMA_FACT_OR_WARNING.search(content):
                return None
            removed_location |= bool(_EMMA_CITY_MENTION.search(content))
        else:
            kept.append(part)
    if not kept:
        # Never silently delete a warning marker, even with no parsed fact.
        return None if value.startswith(('⚠', '🚨', '❗')) else ''
    # Do not detach a surviving fact from a location embedded in discarded prose.
    if removed_location:
        return None
    result = prefix + ' — '.join(part.rstrip(' .;,') for part in kept) + '.'
    if value.startswith('⚠') and not result.startswith('⚠'):
        result = '⚠️ ' + result
    return result


def _warning_context_projection(text: str) -> str | None:
    """Preserve facts and warnings, or block if safe projection is ambiguous."""
    out = []
    changed = False
    for line in text.splitlines():
        value = line.strip()
        if not value or value.startswith('#') or _MARINE_ACTIVITY.search(value):
            out.append(line)
        elif _EMMA_SCORE_RE.match(value):
            if _EMMA_NEUTRAL_SCORE not in out:
                out.append(_EMMA_NEUTRAL_SCORE)
            tail = _EMMA_SCORE_RE.sub('', value, count=1).lstrip(' —:;')
            if tail:
                kept = _without_reassurance(tail) if _EMMA_REASSURANCE_RE.search(tail) else tail
                if kept is None:
                    return None
                if kept:
                    out.append('Оговорки прогноза: ' + kept)
            changed = True
        elif _EMMA_REASSURANCE_RE.search(value):
            kept = _without_reassurance(value)
            if kept is None:
                return None
            if kept:
                out.append(kept)
            changed = True
        else:
            out.append(line)
    if not changed:
        return text
    marker = next((i for i, line in enumerate(out) if line.lstrip().startswith('#')), len(out))
    out.insert(marker, _EMMA_FORECAST_CAUTION)
    return '\n'.join(out)


def _sanitize_projection(projected: str, sanitizer: Callable[[str], object]) -> str | None:
    try:
        checked = sanitizer(projected)
        safe = getattr(checked, 'text', None)
        if not isinstance(safe, str):
            return None
        # Real sanitizer can normalize blank spacing (morning FORMAT_V2).
        # It must retain the exact sequence of all non-empty lines.
        if ([x for x in safe.splitlines() if x.strip()]
                != [x for x in projected.splitlines() if x.strip()]):
            return None
        return safe
    except (TypeError, ValueError, AttributeError, OverflowError):
        return None


def _checked_warning_context(text: str, sanitizer: Callable[[str], object]) -> str | None:
    projected = _warning_context_projection(text)
    if projected is None or projected == text:
        return projected
    return _sanitize_projection(projected, sanitizer)


def _unverified_context(text: str, sanitizer: Callable[[str], object]) -> str | None:
    # Existing explicit UNKNOWN score text already disclaims verification.
    # Otherwise surface source uncertainty, never an all-clear. For the outage
    # path keep ordinary model forecast facts and existing marine neutralization.
    if any(line.strip() in (_EMMA_UNVERIFIED, _EMMA_NEUTRAL_SCORE,
                            '✨ VayboMeter: н/д — официальные предупреждения не проверены.')
           for line in text.splitlines()):
        return text
    lines = text.splitlines()
    marker = next((i for i, line in enumerate(lines) if line.lstrip().startswith('#')), len(lines))
    lines.insert(marker, _EMMA_UNVERIFIED)
    return _sanitize_projection('\n'.join(lines), sanitizer)


def prepare_registered_emma_warning_for_publisher(*,
        final_sanitized_text: str, enabled: bool, at_utc: datetime,
        forecast_date: date, sanitizer: Callable[[str], object]) -> PublisherWarningDecision:
    """Build warning-bearing *existing weather post*, not an independent CAP post.

    The only acquisition route uses the built-in TLS/hostname-verified HTTPS
    fetcher, exact registered Cyprus URLs and all R8-G1/H5/H6 guards.
    No injectable transport; offline tests patch the internal HTTPS function.
    Never upgrade the older G1 UNKNOWN/NO_POST fields, never assert an all-clear.
    """
    if not isinstance(final_sanitized_text, str):
        raise TypeError('finalized text must be str')
    original = PublisherWarningDecision(final_sanitized_text)
    if not enabled:
        return original
    # Feed uncertainty must never coexist with new positive SUP/kite/surf advice.
    # This guard is only in the future opt-in path, not existing production.
    marine_conflict = _marine_contradiction(final_sanitized_text)
    fallback = replace(original, block_publication=True)
    projected = None
    try:
        projected = _checked_warning_context(final_sanitized_text, sanitizer)
        # An unresolved nonempty CAP must not publish reassuring editorial claims.
        # Retain original text only as diagnostics when publication is blocked.
        uncertain = _unverified_context(projected, sanitizer) if projected is not None else None
        if uncertain is not None and not marine_conflict:
            fallback = replace(original, publisher_text=uncertain)
        if not isinstance(at_utc, datetime) or at_utc.utcoffset() is None:
            return replace(fallback, reason='UNKNOWN_CLOCK')
        atom, caps, source = acquire_registered_cyprus_pair(at_utc=at_utc)
        inspected = inspect_offline_capture(
            final_sanitized_text=(projected if projected is not None else final_sanitized_text), atom=atom,
            cap_by_url=caps, source=source, at_utc=at_utc,
            forecast_date=forecast_date, sanitizer=sanitizer,
            attribute_translation=True)
        if (inspected.state != 'UNKNOWN' or inspected.publication_action != 'NO_POST'
                or any((inspected.official_warnings_checked,
                        inspected.clean_verified, inspected.city_cleared,
                        inspected.positive_water_guidance_allowed,
                        inspected.national_inventory_complete))
                or inspected.reason != 'CAP_SENDER_AND_SOURCE_UNVERIFIED'
                or inspected.publisher_text != (projected if projected is not None else final_sanitized_text)
                or inspected.offline_candidate == ''
                or inspected.offline_candidate.count('Данные: MeteoAlarm, CC BY 4.0') != 1
                or inspected.offline_candidate.count('Источник: '+FEED_URL) != 1):
            # Missing/incomplete/unknown source never creates a positive claim.
            return replace(fallback, reason='UNKNOWN_OR_UNSUPPORTED_CAP')
        # Do not pretend that an unresolved EMMA_ID authorizes a local marine GO.
        if marine_conflict:
            return replace(original, block_publication=True,
                           reason='ACTIVE_WARNING_CONFLICTS_WITH_MARINE_GUIDANCE')
        if projected is None:
            # Only a sanitizer that rewrites/drops the projected lines lands here.
            return replace(original, block_publication=True,
                           reason='WARNING_CONTEXT_SANITIZER_REJECTED')
        # The warning may expire while an image is generated; caller must check
        # expires_utc again immediately before Telegram delivery.
        try:
            from .cyprus_emma_preview_r8h5 import _select_display_info
        except ImportError:
            from cyprus_emma_preview_r8h5 import _select_display_info
        expiries = []
        for xml in caps.values():
            root = ET.fromstring(xml)
            info = _select_display_info(root)
            if info is None:
                return replace(original, block_publication=True,
                               reason='CAP_INFO_AMBIGUOUS')
            expires_raw = info.findtext(f'{{{CAP}}}expires')
            expires = datetime.fromisoformat(expires_raw.replace('Z', '+00:00'))
            if expires.utcoffset() is None:
                raise ValueError('naive CAP expiry')
            expiries.append(expires.astimezone(timezone.utc))
        end = min(expiries)
        if end <= at_utc.astimezone(timezone.utc) + timedelta(minutes=15):
            return replace(original, block_publication=True,
                           reason='CAP_NEAR_EXPIRY')
        return PublisherWarningDecision(inspected.offline_candidate,
            warning_attached=True, reason='METEOALARM_DISTRIBUTION_VERIFIED_WARNING',
            expires_utc=end)
    except (OSError, TimeoutError, HTTPException):
        # Keep the ordinary forecast; CAP outage proves neither safety nor
        # hazard. Suppress only unsupported marine GO/ambiguous guidance.
        safe_text = _outage_neutralizes_marine_go(final_sanitized_text)
        projected_outage = _unverified_context(safe_text, sanitizer)
        # Confirmed-alert projection is not the outage contract: it may
        # reject unrelated editorial prose. Validate the actual outage text,
        # retaining the affirmative/section veto without treating a benign
        # visibility observation as marine GO.
        unsafe_outage = _marine_contradiction(safe_text, outage=True)
        all_clear = re.search(r'предупреждений нет|нет предупреждений|официальные предупреждения проверены', safe_text, re.I)
        return replace(original, publisher_text=(projected_outage if projected_outage is not None else safe_text),
                       block_publication=(unsafe_outage or projected_outage is None or bool(all_clear)
                                          or _outage_unresolved_marine_go(safe_text)),
                       reason='SOURCE_UNAVAILABLE')
    except (CaptureRejected, ET.ParseError, TypeError, ValueError,
            AttributeError, KeyError, OverflowError):
        # Malformed/partial/untrusted warning evidence is NOT a mere outage:
        # keep the conservative marine-conflict veto for this branch.
        return replace(fallback, reason='UNKNOWN_CAPTURE_FAILED')



def warning_first_for_delivery(text: str) -> str | None:
    """Move only the complete validated CAP excerpt to the front of an ON post.

    A partial or ambiguous warning block is never split or reordered. The
    ordinary weather text remains in its original relative order; OFF bypasses
    this function entirely.
    """
    if not isinstance(text, str):
        return None
    marker = '⚠️ <b>По данным MeteoAlarm</b>:'
    source = 'Источник: ' + FEED_URL
    credit = 'Данные: MeteoAlarm, CC BY 4.0'
    lines = text.splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(marker)]
    sources = [i for i, line in enumerate(lines) if line == source]
    credits = [i for i, line in enumerate(lines) if line.startswith(credit)]
    if (not 1 <= len(starts) <= 8 or text.count(marker) != len(starts)
            or len(sources) != 1 or len(credits) != 1
            or text.count(source) != 1 or text.count(credit) != 1):
        return None
    first = starts[0]
    src = sources[0]
    end = credits[0]
    if (starts != list(range(first, first + len(starts)))
            or src != first + len(starts) or end != src + 1):
        return None
    block = '\n'.join(lines[first:end + 1])
    ordinary = '\n'.join(lines[:first] + lines[end + 1:]).strip()
    if not ordinary:
        return None
    return block + '\n\n' + ordinary


def warning_overlay_survives_telegram_split(text: str, chunks: list[str]) -> bool:
    """Warning and attribution must precede all other Telegram text chunks."""
    marker = '⚠️ <b>По данным MeteoAlarm</b>:'
    total = text.count(marker)
    if not (1 <= total <= 8) or not chunks:
        return False
    sources = 'Источник: ' + FEED_URL
    credit = 'Данные: MeteoAlarm, CC BY 4.0'
    if text.count(sources) != 1 or text.count(credit) != 1:
        return False
    return (chunks[0].startswith(marker)
            and chunks[0].count(marker) == total
            and sources in chunks[0] and credit in chunks[0]
            and sum(chunk.count(marker) for chunk in chunks) == total)


def warning_overlay_fresh_for_delivery(decision: PublisherWarningDecision,
                                       at_utc: datetime) -> bool:
    """Do not send a CAP excerpt if the earliest alert is about to expire."""
    return bool(decision.warning_attached and decision.expires_utc
                and isinstance(at_utc, datetime) and at_utc.utcoffset() is not None
                and at_utc.astimezone(timezone.utc) + timedelta(minutes=1)
                < decision.expires_utc)


def resolve_emma_publication_forecast_date(*, mode: str, base_date: date,
                                           evening_cycle: object = None) -> date | None:
    """Use the verified forecast date, never the evening execution date."""
    if not isinstance(base_date, date) or isinstance(base_date, datetime):
        return None
    if mode == 'morning':
        return base_date
    if mode == 'evening' and getattr(evening_cycle, 'is_valid', False):
        expected = getattr(evening_cycle, 'expected_forecast_date', None)
        if isinstance(expected, date) and not isinstance(expected, datetime):
            return expected
    return None
