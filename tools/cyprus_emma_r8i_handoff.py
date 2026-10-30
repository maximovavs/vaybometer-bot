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
    from .cyprus_cap_r8g1_contract import (inspect_captured_pair, FEED_URL, ATOM,
                                            CY_CAP_PATH, MAX_BYTES, _xml, _exact_https_url)
    from .cyprus_emma_pipeline_r8h6 import (apply_emma_preview_offline,
                                            guarded_preview_warning_capture)
except ImportError:
    from cyprus_cap_r8g1_contract import (inspect_captured_pair, FEED_URL, ATOM,
                                           CY_CAP_PATH, MAX_BYTES, _xml, _exact_https_url)
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
        'Accept': 'application/atom+xml, application/cap+xml',
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
    if not isinstance(content_type, str) or content_type.split(';')[0].strip().lower() != mime:
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
