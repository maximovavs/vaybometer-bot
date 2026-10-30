"""R8-I: fail-closed offline handoff; no provider or Telegram calls."""
from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from pathlib import Path
import socket
from email.utils import format_datetime
from http.client import IncompleteRead
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

# The real FORMAT_V2 builder, sanitizer and splitter live in the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from cyprus_emma_r8i_handoff import (inspect_offline_capture,
    acquire_registered_cyprus_pair, inspect_registered_cyprus_offline, CaptureRejected)
from cyprus_cap_r8g1_contract import CAP, ATOM, FEED_URL
from test_cyprus_emma_preview_r8h5 import pair, TARGET
from test_cyprus_cap_r8g1_contract import NOW, attrs, dt

BASE = ('<b>Кипр</b>\n'
        '⚠️ По прогнозу: сильный ветер\n'
        '🏄 SUP / кайт / сёрф: безопасность не подтверждена\n'
        '✨ VayboMeter: н/д — официальные предупреждения не проверены.\n'
        '#Кипр #погода')


def identity_sanitizer(s):
    return SimpleNamespace(text=s)


def evaluate(atom=None, docs=None, source=None, **kwargs):
    if atom is None:
        atom, docs = pair()
    if source is None:
        source = attrs(atom, docs)
    return inspect_offline_capture(final_sanitized_text=BASE,
        atom=atom, cap_by_url=docs, source=source,
        at_utc=NOW, forecast_date=TARGET,
        sanitizer=identity_sanitizer, **kwargs)


class OfflineHandoffTests(unittest.TestCase):
    def failclosed(self, result):
        self.assertEqual('UNKNOWN', result.state)
        self.assertEqual('NO_POST', result.publication_action)
        self.assertEqual(BASE, result.publisher_text)
        for name in ('official_warnings_checked', 'clean_verified',
                     'city_cleared', 'positive_water_guidance_allowed',
                     'national_inventory_complete'):
            self.assertIs(getattr(result, name), False, name)

    def test_production_default_noop_requires_no_provider(self):
        result = inspect_offline_capture(final_sanitized_text=BASE)
        self.failclosed(result)
        self.assertEqual('NO_CAPTURE', result.reason)
        self.assertFalse(result.offline_candidate)

    def test_one_and_two_valid_structural_alerts_only_create_review_candidate(self):
        for count in (1, 2):
            with self.subTest(count=count):
                atom, docs = pair(count=count)
                result = evaluate(atom, docs)
                self.failclosed(result)
                self.assertEqual('CAP_SENDER_AND_SOURCE_UNVERIFIED', result.reason)
                self.assertEqual(64, len(result.capture_digest))
                self.assertEqual(count, result.offline_candidate.count('По данным MeteoAlarm'))
                self.assertEqual(1, result.offline_candidate.count('Источник: https://feeds.meteoalarm.org/'))
                self.assertIn('⚠️ По прогнозу: сильный ветер', result.offline_candidate)
                self.assertIn('SUP / кайт / сёрф: безопасность не подтверждена', result.offline_candidate)
                self.assertIn('VayboMeter: н/д', result.offline_candidate)
                self.assertEqual(BASE, result.publisher_text)
                self.assertEqual(result.capture_digest, evaluate(atom, docs).capture_digest)

    def test_untrusted_sender_even_matching_metadata_never_grants_authority(self):
        atom, docs = pair()
        # A forged sender string is not authenticated by a matching Atom entry.
        url = next(iter(docs))
        root = ET.fromstring(docs[url])
        root.find(f'{{{CAP}}}sender').text = 'official-cyprus@example.invalid'
        docs[url] = ET.tostring(root, encoding='utf-8')
        result = evaluate(atom, docs)
        self.failclosed(result)
        self.assertEqual('CAP_SENDER_AND_SOURCE_UNVERIFIED', result.reason)
        self.assertFalse(result.official_warnings_checked)

    def test_source_spoof_stale_missing_cap_and_invalid_digest_rejected(self):
        atom, docs = pair()
        cases = [
            attrs(atom, docs, url='https://malicious.invalid/feed'),
            attrs(atom, docs, http_status=401),
            attrs(atom, docs, redirect_followed=True),
            attrs(atom, docs, sha256='0'*64),
            attrs(atom, docs, received_utc=dt(NOW-timedelta(hours=2)),
                  http_date_utc=dt(NOW-timedelta(hours=2))),
            attrs(atom, docs, cap_evidence={}),
        ]
        for source in cases:
            with self.subTest(source=source.get('url'), status=source.get('http_status')):
                result = evaluate(atom, docs, source=source)
                self.failclosed(result)
                self.assertFalse(result.offline_candidate)
        self.failclosed(evaluate(atom, {}))
        self.assertFalse(evaluate(atom, {}).offline_candidate)

    def test_missing_real_sanitizer_fails_closed_not_identity(self):
        atom, docs = pair()
        # Simulate unavailable dependency even in a full production checkout.
        with patch.dict(sys.modules, {'post_safety': None}):
            out = inspect_offline_capture(final_sanitized_text=BASE,
                atom=atom, cap_by_url=docs, source=attrs(atom, docs),
                at_utc=NOW, forecast_date=TARGET)
        self.failclosed(out)
        self.assertFalse(out.offline_candidate)
        self.assertEqual('OFFLINE_SANITIZER_UNAVAILABLE', out.reason)

    def test_empty_feed_does_not_mean_no_warnings(self):
        atom, docs = pair(count=0)
        result = evaluate(atom, docs)
        self.failclosed(result)
        self.assertEqual('EMPTY_UNVERIFIED', result.reason)
        self.assertFalse(result.offline_candidate)

    def test_update_cancel_and_ambiguous_event_never_render(self):
        for msg_type in ('Update', 'Cancel'):
            atom, docs = pair(msg_type=msg_type)
            result = evaluate(atom, docs)
            with self.subTest(msg_type=msg_type):
                self.failclosed(result)
                self.assertFalse(result.offline_candidate)
        for event in ('No thunderstorm warning', 'Thunderstorm warning CANCELLED',
                      'Thunderstorm forecast', 'Unknown Hazard'):
            atom, docs = pair(event=event)
            result = evaluate(atom, docs)
            with self.subTest(event=event):
                self.failclosed(result)
                self.assertFalse(result.offline_candidate)

    def test_unknown_geography_never_clears_city(self):
        atom, docs = pair(area='Unmapped Coastal Zone')
        result = evaluate(atom, docs)
        self.failclosed(result)
        self.assertNotIn('Лимассол безопасен', result.offline_candidate)
        self.assertFalse(result.city_cleared)
        self.assertEqual('CAP_SENDER_AND_SOURCE_UNVERIFIED', result.reason)

    def test_partial_sanitizer_discards_all_review_candidates(self):
        atom, docs = pair(count=2)
        def drop_one(s):
            parts = s.splitlines()
            return SimpleNamespace(text='\n'.join(x for x in parts if 'По данным MeteoAlarm' not in x))
        result = inspect_offline_capture(final_sanitized_text=BASE,
            atom=atom,cap_by_url=docs,source=attrs(atom, docs),
            at_utc=NOW,forecast_date=TARGET,sanitizer=drop_one)
        self.failclosed(result)
        self.assertFalse(result.offline_candidate)
        self.assertEqual('OFFLINE_SANITIZER_REJECTED', result.reason)

    def test_capture_fingerprint_changes_on_metadata_tamper(self):
        atom, docs = pair()
        initial = evaluate(atom, docs)
        modified = attrs(atom, docs)
        modified['verified_by'] = 'CLAIM_ONLY_NOT_PROOF'
        other = evaluate(atom, docs, source=modified)
        self.failclosed(initial)
        self.failclosed(other)
        self.assertNotEqual(initial.capture_digest, other.capture_digest)
        self.assertFalse(other.official_warnings_checked)

    def test_missing_or_malformed_metadata_never_enables_post(self):
        for kwargs in [dict(atom=b'bad',cap_by_url={}), dict(atom=b'',cap_by_url=None),
                       dict(atom=None,cap_by_url={}), dict(source={'url':'https://host.invalid'})]:
            with self.subTest(kwargs=tuple(kwargs.keys())):
                r=inspect_offline_capture(final_sanitized_text=BASE, **kwargs)
                self.failclosed(r)
                self.assertFalse(r.offline_candidate)
        with self.assertRaises(TypeError):
            inspect_offline_capture(final_sanitized_text=None)


    def test_existing_real_format_v2_sanitizer_and_split_in_full_checkout(self):
        # The full-checkout CI executes this; the isolated evidence bundle does
        # not contain production FORMAT_V2/post_safety or the real fixtures.
        if not (Path(__file__).resolve().parents[1] / 'post_safety.py').exists():
            self.skipTest('full repository required for real FORMAT_V2 E2E')
        import weather
        from format_v2 import build_format_v2
        from post_safety import sanitize_post_text, split_telegram_text, _SAFE_CHUNK_LIMIT
        from safe_test_post import finalize_hashtags_at_end, _CY_CANONICAL_DAILY_HASHTAGS
        from test_cyprus_emma_pipeline_r8h6 import _real_format_v2_cases
        atom, docs = pair()
        cases = _real_format_v2_cases()
        self.assertGreaterEqual(len(cases), 20)
        with patch.object(weather, 'get_weather', return_value={}):
            for name, mode, source in cases:
                with self.subTest(case=name):
                    base = sanitize_post_text(build_format_v2('Кипр', mode, source)).text
                    result = inspect_offline_capture(final_sanitized_text=base,
                        atom=atom, cap_by_url=docs, source=attrs(atom, docs),
                        at_utc=NOW, forecast_date=TARGET, sanitizer=sanitize_post_text)
                    self.assertEqual(base, result.publisher_text)
                    self.assertEqual('UNKNOWN', result.state)
                    self.assertEqual('NO_POST', result.publication_action)
                    self.assertEqual('CAP_SENDER_AND_SOURCE_UNVERIFIED', result.reason)
                    self.assertIn('По данным MeteoAlarm', result.offline_candidate)
                    original = [line for line in base.splitlines() if line.strip()]
                    new = [line for line in result.offline_candidate.splitlines() if line.strip()]
                    added = [line for line in result.offline_candidate.splitlines()
                             if 'По данным MeteoAlarm' in line or line.startswith('Источник:')]
                    self.assertEqual(2, len(added))
                    added_set = [line for line in result.offline_candidate.splitlines()
                                 if 'По данным MeteoAlarm' in line or line.startswith('Источник:')]
                    self.assertEqual(original, [line for line in new if line not in added_set])
                    text = finalize_hashtags_at_end(result.offline_candidate,
                        canonical_hashtags=_CY_CANONICAL_DAILY_HASHTAGS)
                    chunks = split_telegram_text(text)
                    self.assertTrue(chunks)
                    self.assertTrue(all(len(part) <= _SAFE_CHUNK_LIMIT for part in chunks))
                    self.assertEqual(1, sum(part.count('По данным MeteoAlarm') for part in chunks))
                    self.assertFalse(result.official_warnings_checked)
                    self.assertFalse(result.positive_water_guidance_allowed)

    def test_no_network_calls(self):
        atom, docs = pair(count=2)
        with patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK_FORBIDDEN')):
            self.failclosed(evaluate(atom, docs))



# Simulated HTTP responses only: the real HTTPS transport is NOT contacted.
def fake_official_transport(atom, docs, *, altered=None):
    from datetime import timezone
    stamp = format_datetime(NOW, usegmt=True)
    bodies = {FEED_URL: atom, **docs}
    changed = dict(altered or {})
    def fetch(url):
        if url not in bodies:
            raise AssertionError('UNEXPECTED_EXTERNAL_FETCH: '+url)
        headers = {'body': bodies[url], 'url': url, 'status': 200,
                   'content_type': ('application/atom+xml' if url == FEED_URL
                                    else 'application/cap+xml'),
                   'http_date': stamp, 'received_utc': NOW}
        headers.update(changed.get(url, {}))
        return headers
    return fetch


class RegisteredAggregatorTransportTests(unittest.TestCase):
    def test_wmo_registered_atom_and_two_cap_links_to_lossless_licensed_excerpt(self):
        atom, docs = pair(count=2)
        fake = fake_official_transport(atom, docs)
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('NETWORK')):
            returned_atom, returned_caps, source = acquire_registered_cyprus_pair(
                at_utc=NOW, fetch=fake)
            self.assertEqual(atom, returned_atom)
            self.assertEqual(docs, returned_caps)
            self.assertEqual(set(source['cap_evidence']), set(docs))
            result = inspect_registered_cyprus_offline(
                final_sanitized_text=BASE, at_utc=NOW, forecast_date=TARGET,
                sanitizer=identity_sanitizer, fetch=fake)
        self.assertEqual('UNKNOWN', result.state)
        self.assertEqual('NO_POST', result.publication_action)
        self.assertEqual(BASE, result.publisher_text)
        self.assertEqual('REGISTERED_METEOALARM_INFORMATION_PREVIEW_ONLY', result.reason)
        self.assertEqual(2, result.offline_candidate.count('По данным MeteoAlarm'))
        self.assertEqual(1, result.offline_candidate.count('Данные: MeteoAlarm'))
        self.assertIn('https://creativecommons.org/licenses/by/4.0/', result.offline_candidate)
        self.assertIn('Русский перевод и сокращение', result.offline_candidate)
        self.assertIn('Территория по CAP: Cyprus', result.offline_candidate)
        self.assertIn('Период:', result.offline_candidate)
        self.assertIn('SUP / кайт / сёрф: безопасность не подтверждена', result.offline_candidate)
        self.assertFalse(any([result.official_warnings_checked, result.clean_verified,
                               result.city_cleared, result.positive_water_guidance_allowed,
                               result.national_inventory_complete]))

    def test_unregistered_cross_host_or_cap_url_is_rejected_before_fetch(self):
        atom, docs = pair()
        r = ET.fromstring(atom)
        link = r.find(f'{{{ATOM}}}entry/{{{ATOM}}}link')
        link.attrib['href'] = 'https://evil.example.test/cap.xml'
        mutated = ET.tostring(r, encoding='utf-8')
        seen = []
        def fetch(url):
            seen.append(url)
            return fake_official_transport(mutated, docs)(url)
        with self.assertRaises(CaptureRejected):
            acquire_registered_cyprus_pair(at_utc=NOW, fetch=fetch)
        self.assertEqual([FEED_URL], seen)

    def test_transport_rejects_redirects_wrong_mime_401_stale_and_missing_cap(self):
        atom, docs = pair()
        cap_url = next(iter(docs))
        invalid = [
            {FEED_URL: {'url': 'https://evil.example.test/'}},
            {FEED_URL: {'status': 302}},
            {FEED_URL: {'status': 401}},
            {FEED_URL: {'content_type': 'text/html'}},
            {FEED_URL: {'body': b'X' * (256*1024+1)}},
            {FEED_URL: {'http_date': None}},
            {FEED_URL: {'http_date': 'Wed, 01 Jan 2020 00:00:00 GMT'}},
            {cap_url: {'status': 404}},
            {cap_url: {'content_type': 'text/plain'}},
            {cap_url: {'url': 'https://another.example.test/'}},
        ]
        for variation in invalid:
            with self.subTest(changes=variation):
                result = inspect_registered_cyprus_offline(
                    final_sanitized_text=BASE, at_utc=NOW,
                    forecast_date=TARGET, sanitizer=identity_sanitizer,
                    fetch=fake_official_transport(atom, docs, altered=variation))
                self.assertEqual(BASE, result.publisher_text)
                self.assertEqual('UNKNOWN', result.state)
                self.assertEqual('NO_POST', result.publication_action)
                self.assertFalse(result.offline_candidate)
                self.assertEqual('SOURCE_CAPTURE_REJECTED', result.reason)

    def test_http_partial_read_returns_unknown_no_post(self):
        # A real HTTPS transport may encounter an incomplete chunked response.
        def truncated(_url):
            raise IncompleteRead(b'<feed>', 99)
        result = inspect_registered_cyprus_offline(
            final_sanitized_text=BASE, at_utc=NOW, forecast_date=TARGET,
            sanitizer=identity_sanitizer, fetch=truncated)
        self.assertEqual(BASE, result.publisher_text)
        self.assertEqual('UNKNOWN', result.state)
        self.assertEqual('NO_POST', result.publication_action)
        self.assertEqual('SOURCE_CAPTURE_REJECTED', result.reason)
        self.assertFalse(result.offline_candidate)
        self.assertFalse(result.official_warnings_checked)

    def test_partial_missing_or_duplicate_cap_alerts_fail_closed(self):
        atom, docs = pair(count=2)
        omitted = {k:v for i,(k,v) in enumerate(docs.items()) if i==0}
        with self.assertRaises(AssertionError):
            acquire_registered_cyprus_pair(at_utc=NOW,
                fetch=fake_official_transport(atom, omitted))
        r = ET.fromstring(atom)
        entries = r.findall(f'{{{ATOM}}}entry')
        links = [e.find(f'{{{ATOM}}}link') for e in entries]
        links[1].attrib['href'] = links[0].attrib['href']
        with self.assertRaises(CaptureRejected):
            acquire_registered_cyprus_pair(at_utc=NOW,
                fetch=fake_official_transport(ET.tostring(r, encoding='utf-8'), docs))
        for kind in ('Update','Cancel'):
            a,c = pair(msg_type=kind)
            result = inspect_registered_cyprus_offline(
                final_sanitized_text=BASE, at_utc=NOW,
                forecast_date=TARGET, sanitizer=identity_sanitizer,
                fetch=fake_official_transport(a,c))
            self.assertEqual(BASE,result.publisher_text)
            self.assertEqual('NO_POST',result.publication_action)
            self.assertFalse(result.offline_candidate)

    def test_modified_attribution_is_rejected_by_full_block_integrity(self):
        atom, docs = pair()
        def tamper(text):
            return SimpleNamespace(text=text.replace(
                'Русский перевод и сокращение; неофициальная адаптация.',
                'Неподтверждённый перевод.'))
        result = inspect_registered_cyprus_offline(
            final_sanitized_text=BASE, at_utc=NOW, forecast_date=TARGET,
            sanitizer=tamper, fetch=fake_official_transport(atom, docs))
        self.assertEqual(BASE, result.publisher_text)
        self.assertFalse(result.offline_candidate)
        self.assertEqual('NO_POST', result.publication_action)
        self.assertFalse(result.official_warnings_checked)

    def test_unknown_area_does_not_create_city_claim_or_all_clear(self):
        atom, docs = pair(area='Coastal Sector West')
        result = inspect_registered_cyprus_offline(
            final_sanitized_text=BASE, at_utc=NOW, forecast_date=TARGET,
            sanitizer=identity_sanitizer, fetch=fake_official_transport(atom, docs))
        self.assertIn('Территория по CAP: Coastal Sector West',result.offline_candidate)
        self.assertNotIn('Лимассол',result.offline_candidate)
        self.assertFalse(result.city_cleared)
        a,c=pair(count=0)
        empty = inspect_registered_cyprus_offline(final_sanitized_text=BASE,
            at_utc=NOW, forecast_date=TARGET, sanitizer=identity_sanitizer,
            fetch=fake_official_transport(a,c))
        self.assertEqual('SOURCE_CAPTURE_REJECTED', empty.reason)
        self.assertEqual('',empty.offline_candidate)
        self.assertEqual(BASE,empty.publisher_text)



class PublisherWarningBridgeTests(unittest.TestCase):
    def _run(self, *, atom=None, docs=None, text=BASE, fake=None, clock=NOW, enabled=True):
        if atom is None:
            atom, docs = pair()
        if fake is None:
            fake = fake_official_transport(atom, docs)
        from cyprus_emma_r8i_handoff import prepare_registered_emma_warning_for_publisher
        with patch('cyprus_emma_r8i_handoff._verified_https_get', fake):
            return prepare_registered_emma_warning_for_publisher(
                final_sanitized_text=text, enabled=enabled, at_utc=clock,
                forecast_date=TARGET, sanitizer=identity_sanitizer)

    def test_evening_uses_next_day_cycle_identity_not_execution_date(self):
        from cyprus_emma_r8i_handoff import resolve_emma_publication_forecast_date
        today = TARGET - timedelta(days=1)
        cycle = SimpleNamespace(is_valid=True, expected_forecast_date=TARGET)
        self.assertEqual(TARGET, resolve_emma_publication_forecast_date(
            mode='evening', base_date=today, evening_cycle=cycle))
        self.assertEqual(today, resolve_emma_publication_forecast_date(
            mode='morning', base_date=today))
        self.assertIsNone(resolve_emma_publication_forecast_date(
            mode='evening', base_date=today, evening_cycle=None))
        self.assertIsNone(resolve_emma_publication_forecast_date(
            mode='evening', base_date=today,
            evening_cycle=SimpleNamespace(is_valid=False, expected_forecast_date=TARGET)))

    def test_off_never_fetches(self):
        def forbidden(_): raise AssertionError('NETWORK')
        result = self._run(fake=forbidden, enabled=False)
        self.assertEqual(BASE, result.publisher_text)
        self.assertFalse(result.warning_attached)
        self.assertFalse(result.block_publication)

    def test_real_bound_warning_overlay_and_marine_no_go(self):
        atom, docs = pair(count=2)
        result = self._run(atom=atom, docs=docs)
        self.assertTrue(result.warning_attached)
        self.assertFalse(result.block_publication)
        self.assertEqual(2, result.publisher_text.count('По данным MeteoAlarm'))
        self.assertIn('Территория по CAP: Cyprus', result.publisher_text)
        self.assertIn('Русский перевод и сокращение', result.publisher_text)
        self.assertIn('Источник: '+FEED_URL, result.publisher_text)
        self.assertIn('SUP / кайт / сёрф: безопасность не подтверждена', result.publisher_text)
        self.assertGreater(result.expires_utc, NOW)

    def test_marine_go_is_never_sent_beside_weather_alert(self):
        text = BASE.replace('безопасность не подтверждена', '✅ можно кататься')
        result = self._run(text=text)
        self.assertFalse(result.warning_attached)
        self.assertTrue(result.block_publication)
        self.assertEqual(text, result.publisher_text)
        self.assertEqual('ACTIVE_WARNING_CONFLICTS_WITH_MARINE_GUIDANCE', result.reason)

    def test_unknown_and_update_cancel_do_not_publish_official_claim(self):
        for kind in ('Update', 'Cancel'):
            atom,docs=pair(msg_type=kind)
            with self.subTest(kind=kind):
                r=self._run(atom=atom, docs=docs)
                self.assertFalse(r.warning_attached)
                self.assertEqual(BASE,r.publisher_text)
        r=self._run(fake=lambda _url: (_ for _ in ()).throw(IncompleteRead(b'', 20)))
        self.assertEqual('UNKNOWN_CAPTURE_FAILED',r.reason)
        self.assertFalse(r.warning_attached)
        self.assertFalse(r.block_publication)

        # Unverified official warnings cannot justify an affirmative water GO.
        affirmative = BASE.replace('безопасность не подтверждена', '✅ можно кататься')
        r = self._run(text=affirmative, fake=lambda _url: (_ for _ in ()).throw(
            IncompleteRead(b'', 20)))
        self.assertTrue(r.block_publication)
        self.assertFalse(r.warning_attached)
        self.assertEqual(affirmative, r.publisher_text)

    def test_source_cross_host_rejected_without_fetch(self):
        atom, docs = pair()
        root=ET.fromstring(atom)
        link=root.find(f'{{{ATOM}}}entry/{{{ATOM}}}link')
        link.set('href', 'https://evil.example/cap')
        r=self._run(atom=ET.tostring(root), docs=docs)
        self.assertFalse(r.warning_attached)
        self.assertEqual(BASE,r.publisher_text)

    def test_positive_marine_advice_on_following_line_is_blocked(self):
        text = BASE.replace('безопасность не подтверждена',
                            'не рекомендуется\n✅ можно выходить на воду')
        result = self._run(text=text)
        self.assertTrue(result.block_publication)
        self.assertFalse(result.warning_attached)

    def test_expiry_close_blocks_message(self):
        atom, docs = pair()
        # Valid at NOW and in the forecast day, but <15min until expires.
        revised={}
        for u,v in docs.items():
            root=ET.fromstring(v)
            root.find(f'{{{CAP}}}info/{{{CAP}}}expires').text=(NOW+timedelta(minutes=5)).isoformat()
            revised[u]=ET.tostring(root,encoding='utf-8')
        from cyprus_emma_r8i_handoff import WarningHandoff
        # Isolate the final horizon guard: the synthetic source's unchanged
        # onset date otherwise makes this modified expiry invalid at R8-G1.
        trusted_overlay = WarningHandoff(BASE, reason='CAP_SENDER_AND_SOURCE_UNVERIFIED',
            offline_candidate=BASE + '\n⚠️ <b>По данным MeteoAlarm</b>:\nИсточник: ' + FEED_URL +
            '\nДанные: MeteoAlarm, CC BY 4.0')
        with patch('cyprus_emma_r8i_handoff.inspect_offline_capture',
                   return_value=trusted_overlay):
            r=self._run(atom=atom, docs=revised)
        self.assertFalse(r.warning_attached)
        self.assertTrue(r.block_publication)

    def test_publisher_seam_is_behind_disabled_by_default_flag(self):
        source = ROOT / 'safe_test_post.py'
        if not source.exists():
            self.skipTest('publisher source requires complete checkout')
        value = source.read_text(encoding='utf-8')
        self.assertIn('_env_on("CY_EMMA_WARNING_OVERLAY")', value)
        self.assertIn('not args.image_only_recovery', value)
        self.assertIn('warning_overlay_survives_telegram_split', value)
        self.assertIn('warning_overlay_fresh_for_delivery', value)
        workflow = ROOT / '.github/workflows/daily_post.yml'
        if workflow.exists():
            content = workflow.read_text(encoding='utf-8')
            self.assertIn('CY_EMMA_WARNING_OVERLAY: "0"', content)

    def test_chunk_integrity_and_send_time_expiry_guard(self):
        from cyprus_emma_r8i_handoff import (
            warning_overlay_survives_telegram_split,
            warning_overlay_fresh_for_delivery)
        r = self._run()
        self.assertTrue(r.warning_attached)
        self.assertTrue(warning_overlay_survives_telegram_split(r.publisher_text,
                                                                 [r.publisher_text]))
        pivot = r.publisher_text.index('Источник: ')
        self.assertFalse(warning_overlay_survives_telegram_split(
            r.publisher_text, [r.publisher_text[:pivot], r.publisher_text[pivot:]]))
        self.assertTrue(warning_overlay_fresh_for_delivery(r, NOW))
        self.assertFalse(warning_overlay_fresh_for_delivery(r, r.expires_utc))

    def test_ambiguous_marine_line_is_blocked(self):
        r=self._run(text=BASE.replace('безопасность не подтверждена', 'Море сегодня'))
        self.assertTrue(r.block_publication)
        self.assertEqual(BASE.replace('безопасность не подтверждена','Море сегодня'),r.publisher_text)

if __name__ == '__main__':
    unittest.main(verbosity=2)
