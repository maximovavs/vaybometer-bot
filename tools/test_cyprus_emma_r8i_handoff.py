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
        self.assertEqual('SOURCE_UNAVAILABLE',r.reason)
        self.assertFalse(r.warning_attached)
        self.assertFalse(r.block_publication)

        # Unverified official warnings cannot justify an affirmative water GO.
        affirmative = BASE.replace('безопасность не подтверждена', '✅ можно кататься')
        r = self._run(text=affirmative, fake=lambda _url: (_ for _ in ()).throw(
            IncompleteRead(b'', 20)))
        # A source outage preserves forecast facts but must not retain
        # unsupported marine GO or add unverified warning/all-clear claims.
        self.assertFalse(r.block_publication)
        self.assertFalse(r.warning_attached)
        self.assertEqual('SOURCE_UNAVAILABLE', r.reason)
        self.assertNotIn('✅ можно кататься', r.publisher_text)
        self.assertIn('безопасность не подтверждена', r.publisher_text)
        self.assertIn('⚠️ По прогнозу: сильный ветер', r.publisher_text)

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
            warning_first_for_delivery, warning_overlay_survives_telegram_split,
            warning_overlay_fresh_for_delivery)
        r = self._run()
        self.assertTrue(r.warning_attached)
        first = warning_first_for_delivery(r.publisher_text)
        self.assertIsNotNone(first)
        self.assertTrue(warning_overlay_survives_telegram_split(first, [first]))
        # The old late warning must not be considered safe for streaming.
        self.assertFalse(warning_overlay_survives_telegram_split(
            r.publisher_text, [r.publisher_text]))
        pivot = first.index('Источник: ')
        self.assertFalse(warning_overlay_survives_telegram_split(
            first, [first[:pivot], first[pivot:]]))
        self.assertTrue(warning_overlay_fresh_for_delivery(r, NOW))
        self.assertFalse(warning_overlay_fresh_for_delivery(r, r.expires_utc))

    def test_ambiguous_marine_line_is_blocked(self):
        r=self._run(text=BASE.replace('безопасность не подтверждена', 'Море сегодня'))
        self.assertTrue(r.block_publication)
        self.assertEqual(BASE.replace('безопасность не подтверждена','Море сегодня'),r.publisher_text)

    def test_publisher_text_first_gate_and_off_path_are_present(self):
        # Source-level regression for the send ordering. End-to-end publisher
        # integration must also pass canonical CI on the exact PR head.
        source = (ROOT / 'safe_test_post.py').read_text(encoding='utf-8')
        self.assertIn('if emma_image_deferred:', source)
        self.assertIn('image_result, image_status = await _cy_generate_image_for_current_post()', source)
        self.assertIn("CY_EMMA_WARNING_BLOCKED: TEXT_DELIVERY_UNCONFIRMED", source)
        self.assertLess(source.index('sent_message_ids = await _send_telegram_text_chunks('),
                        source.rindex('image_result, image_status = await _cy_generate_image_for_current_post()'))
        self.assertLess(source.index('CY_TEXT_RECEIPT_WRITTEN:'),
                        source.rindex('image_result, image_status = await _cy_generate_image_for_current_post()'))
        self.assertIn('if not emma_image_deferred:', source)
        self.assertLess(source.index('CY_EMMA_WARNING_BLOCKED: TEXT_DELIVERY_UNCONFIRMED'),
                        source.index('CY_TEXT_RECEIPT_WRITTEN:'))

    def test_optional_feed_outage_vs_malformed_evidence_preserves_safety(self):
        positive = BASE.replace('безопасность не подтверждена', '✅ можно кататься')
        ambiguous = BASE.replace('безопасность не подтверждена', 'Море сегодня')
        for text in (positive, ambiguous):
            with self.subTest(kind='transport_unavailable', text=text[-24:]):
                out = self._run(text=text,
                    fake=lambda _url: (_ for _ in ()).throw(TimeoutError('offline')))
                self.assertNotEqual(text, out.publisher_text)
                self.assertIn('безопасность не подтверждена', out.publisher_text)
                self.assertNotIn('✅ можно кататься', out.publisher_text)
                self.assertNotIn('Море сегодня', out.publisher_text)
                self.assertIn('⚠️ По прогнозу: сильный ветер', out.publisher_text)
                self.assertFalse(out.warning_attached)
                self.assertFalse(out.block_publication)
                self.assertEqual('SOURCE_UNAVAILABLE', out.reason)
            with self.subTest(kind='malformed_capture', text=text[-24:]):
                out = self._run(text=text, fake=lambda url: {'url':url, 'status':200})
                self.assertEqual(text, out.publisher_text)
                self.assertFalse(out.warning_attached)
                self.assertTrue(out.block_publication)
                self.assertEqual('UNKNOWN_CAPTURE_FAILED', out.reason)
            with self.subTest(kind='verified_warning', text=text[-24:]):
                out = self._run(text=text)
                self.assertTrue(out.block_publication)
                self.assertFalse(out.warning_attached)
                self.assertEqual(text, out.publisher_text)
                self.assertEqual('ACTIVE_WARNING_CONFLICTS_WITH_MARINE_GUIDANCE', out.reason)

    def test_warning_first_blocks_second_chunk_partial_delivery(self):
        from cyprus_emma_r8i_handoff import (
            warning_first_for_delivery, warning_overlay_survives_telegram_split)
        from post_safety import split_telegram_text
        # Before the fix, the warning sits behind this long weather text.
        long_post = BASE.replace('#Кипр #погода', ('Прогноз по берегу. ' * 250) + '\n#Кипр #погода')
        original = self._run(text=long_post)
        self.assertTrue(original.warning_attached)
        late_chunks = split_telegram_text(original.publisher_text)
        self.assertGreaterEqual(len(late_chunks), 2)
        self.assertFalse(late_chunks[0].startswith('⚠️ <b>По данным MeteoAlarm</b>:'))
        first = warning_first_for_delivery(original.publisher_text)
        self.assertIsNotNone(first)
        chunks = split_telegram_text(first)
        self.assertTrue(warning_overlay_survives_telegram_split(first, chunks))
        self.assertTrue(chunks[0].startswith('⚠️ <b>По данным MeteoAlarm</b>:'))
        self.assertEqual(1, first.count('Данные: MeteoAlarm, CC BY 4.0'))
        self.assertEqual(1, first.count('Источник: ' + FEED_URL))
        self.assertEqual(1, first.count('Прогноз по берегу. ' * 250))
        # Source and credit must never be detached from the warning block.
        self.assertIsNone(warning_first_for_delivery(first.replace('Источник: '+FEED_URL, '')))

    def test_warning_first_requires_confirmed_telegram_ids_and_live_expiry(self):
        import asyncio
        from unittest.mock import AsyncMock
        from cyprus_emma_r8i_handoff import (
            warning_first_for_delivery, warning_overlay_fresh_for_delivery)
        from post_safety import split_telegram_text
        import safe_test_post
        long_post = BASE.replace('#Кипр #погода', ('Прогноз по берегу. ' * 250) + '\n#Кипр #погода')
        result = self._run(text=long_post)
        first = warning_first_for_delivery(result.publisher_text)
        chunks = split_telegram_text(first)
        self.assertGreaterEqual(len(chunks), 2)
        async def run_case(responses, clock):
            sent = []
            async def mock_send(_bot, **kwargs):
                sent.append(kwargs['text'])
                answer = responses[len(sent)-1]
                if isinstance(answer, BaseException):
                    raise answer
                return SimpleNamespace(message_id=answer)
            def before_chunk():
                if not warning_overlay_fresh_for_delivery(result, clock[len(sent)]):
                    raise RuntimeError('CY_EMMA_WARNING_BLOCKED: EXPIRED_DURING_TEXT_SEND')
            with patch.object(safe_test_post, '_send_telegram_message_with_retry',
                              new=mock_send):
                await safe_test_post._send_telegram_text_chunks(
                    object(), chat_id=1, chunks=chunks, add_test_label=False,
                    before_chunk=before_chunk, require_confirmed_ids=True)
            return sent
        # First warning delivered, but next chunk fails: warning is not lost.
        with self.assertRaises(OSError):
            asyncio.run(run_case([101, OSError('partial delivery')], [NOW, NOW]))
        # Warning without an acknowledged ID must not be followed by weather.
        with self.assertRaisesRegex(RuntimeError, 'TEXT_DELIVERY_UNCONFIRMED'):
            asyncio.run(run_case([None], [NOW, NOW]))
        # CAP expires between chunks: refuse subsequent text transmissions.
        with self.assertRaisesRegex(RuntimeError, 'EXPIRED_DURING_TEXT_SEND'):
            asyncio.run(run_case([101], [NOW, result.expires_utc]))

    def test_multiple_alerts_keep_source_and_credit_with_first_chunk(self):
        from cyprus_emma_r8i_handoff import (
            warning_first_for_delivery, warning_overlay_survives_telegram_split)
        from post_safety import split_telegram_text
        atom, docs = pair(count=2)
        result = self._run(atom=atom, docs=docs)
        self.assertTrue(result.warning_attached)
        first = warning_first_for_delivery(result.publisher_text)
        self.assertEqual(2, first.count('⚠️ <b>По данным MeteoAlarm</b>:'))
        chunks = split_telegram_text(first)
        self.assertTrue(warning_overlay_survives_telegram_split(first, chunks))
        self.assertTrue(chunks[0].startswith('⚠️ <b>По данным MeteoAlarm</b>:'))
        self.assertEqual(1, chunks[0].count('Данные: MeteoAlarm, CC BY 4.0'))
        self.assertEqual(1, chunks[0].count('Источник: ' + FEED_URL))

    def test_delivery_reordering_does_not_modify_visual_or_receipt_text(self):
        source = (ROOT / 'safe_test_post.py').read_text(encoding='utf-8')
        self.assertIn('delivery_text = final_result.text', source)
        self.assertIn('delivery_text = warning_first_for_delivery(delivery_text)', source)
        self.assertIn('chunks = split_telegram_text(delivery_text)', source)
        self.assertIn('warning_overlay_survives_telegram_split(delivery_text, chunks)', source)
        self.assertIn('image_result = ', source)
        self.assertIn('            final_result.text,', source)
        self.assertIn('    text_target_date = _cy_extract_receipt_date(\n        final_result.text,', source)
        self.assertIn('if emma_warning_send else {}', source)
        self.assertIn('**emma_send_kwargs,', source)

    def test_main_m8_expiry_guard_is_wired_before_every_chunk(self):
        """Real main(): expiry after warning chunk must stop subsequent sends.

        Regression for M8 (removing before_chunk from main's sender wiring).
        The fake source supplies only a synthetic warning; no HTTP/Telegram
        request or actual publication can occur.
        """
        import ast
        import inspect
        import os
        import tempfile
        from datetime import datetime, timedelta, timezone
        from test_cy_cycle_date import Harness, Clock, local
        import safe_test_post as safe_module
        import tools.cyprus_emma_r8i_handoff as live_handoff

        # Structural part of M8: main must pass its expiry callback into
        # the chunk sender, not merely define an unused callback.
        main_tree = ast.parse(inspect.getsource(safe_module.main))
        wired = any(
            isinstance(node, ast.Dict) and any(
                isinstance(k, ast.Constant) and k.value == 'before_chunk'
                and isinstance(v, ast.Name) and v.id == '_emma_before_chunk'
                for k, v in zip(node.keys, node.values)
            ) for node in ast.walk(main_tree)
        )
        self.assertTrue(wired, 'M8: per-chunk expiry callback not wired in main()')

        warning = (
            '⚠️ <b>По данным MeteoAlarm</b>: предупреждение о грозах. '
            'Уровень: серьёзная. Территория по CAP: Cyprus.\n'
            'Источник: ' + FEED_URL + '\n'
            'Данные: MeteoAlarm, CC BY 4.0 '
            '(https://creativecommons.org/licenses/by/4.0/). '
            'Русский перевод и сокращение; неофициальная адаптация.'
        )
        expires = datetime.now(timezone.utc) + timedelta(minutes=30)
        valid_time = expires - timedelta(minutes=10)
        real_freshness = live_handoff.warning_overlay_fresh_for_delivery

        with tempfile.TemporaryDirectory() as temp_dir:
            # The harness runs the actual publisher with fake Bot and offline
            # image backends; the flag is restored after this one test.
            with patch.dict(os.environ, {'CY_EMMA_WARNING_OVERLAY': '1'}):
                with Harness(Path(temp_dir), Clock(local(2026, 10, 28, 9, 0))) as harness:
                    def long_weather(**_kwargs):
                        return (
                            '🌅 Кипр сегодня (28.10.2026)\n'
                            '✨ VayboMeter: 5.0/10 — с оговорками.\n'
                            '🏄 SUP / кайт / сёрф: безопасность не подтверждена.\n'
                            + '🏙 Прогноз побережья: ветер 7 м/с, волны 1 м.\n' * 130
                            + '#Кипр #погода'
                        )

                    def fake_warning(*, final_sanitized_text, **_kwargs):
                        from tools.cyprus_emma_r8i_handoff import PublisherWarningDecision
                        return PublisherWarningDecision(
                            publisher_text=final_sanitized_text + '\n' + warning,
                            warning_attached=True,
                            expires_utc=expires,
                        )

                    fresh_checks = []

                    def advancing_freshness(decision, _actual_now):
                        # The time changes only AFTER Telegram acknowledged the
                        # first chunk, so pre-send checks must initially pass.
                        count = len(harness.messages)
                        fresh_checks.append(count)
                        simulated_now = valid_time if count == 0 else expires
                        return real_freshness(decision, simulated_now)

                    safe_module.build_message = long_weather
                    with patch.object(live_handoff, 'prepare_registered_emma_warning_for_publisher',
                                      side_effect=fake_warning), \
                         patch.object(live_handoff, 'warning_overlay_fresh_for_delivery',
                                      side_effect=advancing_freshness), \
                         patch.object(live_handoff, '_verified_https_get',
                                      side_effect=AssertionError('unexpected HTTPS')):
                        with self.assertRaisesRegex(RuntimeError, 'EXPIRED_DURING_TEXT_SEND'):
                            harness.run([
                                'safe_test_post.py', '--mode', 'morning',
                                '--date', '2026-10-28', '--chat-id', '777',
                                '--send', '--no-test-label',
                            ], event='workflow_dispatch', anchor=False)

                    self.assertGreaterEqual(len(fresh_checks), 3, fresh_checks)
                    self.assertEqual(1, sum(count > 0 for count in fresh_checks))
                    self.assertEqual(1, len(harness.messages))
                    self.assertTrue(harness.messages[0]['text'].startswith(
                        '⚠️ <b>По данным MeteoAlarm</b>:'), harness.messages)
                    self.assertEqual([], harness.photos)
                    self.assertEqual({}, harness.receipts('text'))
                    self.assertEqual({}, harness.receipts('image'))


    def test_outage_preserves_cautious_marine_and_unrelated_weather(self):
        outage = lambda _url: (_ for _ in ()).throw(TimeoutError('offline'))
        cases = (
            '🏄 SUP лучше отложить: порывы до 15 м/с',
            '🏄 SUP: только опытным; порывы до 12 м/с',
            '🏄 SUP: не рекомендуется',
        )
        for marine in cases:
            text = BASE + '\n' + marine + '\n🏙 Лимассол: хорошая погода\n🌡 Вода 24 °C — отлично\n✅ План завтра: прогулки утром'
            with self.subTest(marine=marine):
                out = self._run(text=text, fake=outage)
                self.assertFalse(out.block_publication)
                self.assertEqual(text, out.publisher_text)
                self.assertEqual('SOURCE_UNAVAILABLE', out.reason)
        positive = BASE.replace('безопасность не подтверждена', '✅ можно кататься')
        out = self._run(text=positive, fake=outage)
        self.assertFalse(out.block_publication)
        self.assertNotIn('✅ можно кататься', out.publisher_text)
        self.assertIn('⚠️ По прогнозу: сильный ветер', out.publisher_text)
        followup = BASE.replace('безопасность не подтверждена',
                                'безопасность не подтверждена\n✅ Отлично: можно кататься')
        out = self._run(text=followup, fake=outage)
        self.assertFalse(out.block_publication)
        self.assertNotIn('✅ Отлично: можно кататься', out.publisher_text)
        self.assertIn('⚠️ По прогнозу: сильный ветер', out.publisher_text)
        self.assertNotIn('официальные предупреждения проверены', out.publisher_text)
        # A daily plan is unrelated unless it actually gives marine GO advice.
        sea_plan = BASE.replace('безопасность не подтверждена',
                                'безопасность не подтверждена\n✅ План завтра: кататься')
        out = self._run(text=sea_plan, fake=outage)
        self.assertFalse(out.block_publication)
        self.assertNotIn('✅ План завтра: кататься', out.publisher_text)

    def test_retry_after_expiry_and_unknown_delivery_on_warning_path(self):
        import asyncio
        from datetime import datetime, timedelta, timezone
        import safe_test_post as publisher
        from cyprus_emma_r8i_handoff import PublisherWarningDecision, warning_overlay_fresh_for_delivery
        # Regression: PTB 20.x has RetryAfter, but not necessarily ServerError.
        self.assertIsInstance(publisher._CY_RETRY_AFTER, type)
        start = datetime(2026, 10, 28, 9, 0, tzinfo=timezone.utc)
        decision = PublisherWarningDecision('synthetic', warning_attached=True,
                                            expires_utc=start + timedelta(minutes=5))

        def case(events, *, sleep_minutes=0, warning=True, chunks=None):
            now = [start]
            sent = []
            ids = []
            sleeps = []
            class FakeBot:
                async def send_message(self, **kwargs):
                    sent.append(kwargs['text'])
                    event = events[len(sent)-1]
                    if isinstance(event, BaseException):
                        raise event
                    return SimpleNamespace(message_id=event)
            async def fake_sleep(seconds):
                sleeps.append(seconds)
                now[0] += timedelta(minutes=sleep_minutes)
            def before_chunk():
                if not warning_overlay_fresh_for_delivery(decision, now[0]):
                    raise RuntimeError('CY_EMMA_WARNING_BLOCKED: EXPIRED_DURING_TEXT_SEND')
            async def run():
                return await publisher._send_telegram_text_chunks(
                    FakeBot(), chat_id=1, chunks=chunks or ['⚠️ warning', 'weather'],
                    add_test_label=False, partial_message_ids=ids,
                    before_chunk=before_chunk if warning else None,
                    require_confirmed_ids=warning)
            with patch.object(publisher.asyncio, 'sleep', new=fake_sleep):
                try:
                    return ('ok', asyncio.run(run()), sent, sleeps, ids)
                except Exception as exc:
                    return (exc, None, sent, sleeps, ids)

        exc, _, sent, sleeps, ids = case([publisher._CY_RETRY_AFTER(2)], sleep_minutes=7)
        self.assertIsInstance(exc, RuntimeError)
        self.assertIn('EXPIRED_DURING_TEXT_SEND', str(exc))
        self.assertEqual(1, len(sent))
        self.assertEqual([], ids)
        self.assertEqual(1, len(sleeps))

        result, got, sent, sleeps, ids = case([publisher._CY_RETRY_AFTER(2), 101, 102], sleep_minutes=1)
        self.assertEqual('ok', result)
        self.assertEqual([101, 102], got)
        self.assertEqual(3, len(sent))
        self.assertEqual([101, 102], ids)

        for failure in (TimeoutError('unknown'), OSError('network unknown')):
            with self.subTest(failure=type(failure).__name__):
                exc, _, sent, sleeps, ids = case([failure])
                self.assertIsInstance(exc, type(failure))
                self.assertEqual(1, len(sent))
                self.assertEqual([], sleeps)
                self.assertEqual([], ids)

        # First confirmed chunk followed by ambiguous second-chunk timeout:
        # do not send the photo or write complete delivery receipts in main().
        exc, _, sent, sleeps, ids = case([201, TimeoutError('unknown')])
        self.assertIsInstance(exc, TimeoutError)
        self.assertEqual(2, len(sent))
        self.assertEqual([201], ids)

        # OFF call still retries TimedOut/OSError as before.
        result, got, sent, sleeps, ids = case([TimeoutError('legacy'), 301, 302], warning=False)
        self.assertEqual('ok', result)
        self.assertEqual([301, 302], got)
        self.assertEqual(3, len(sent))

    def test_outage_keeps_explicit_danger_and_wind_direction(self):
        outage = lambda _url: (_ for _ in ()).throw(TimeoutError('offline'))
        mixed = BASE.replace('безопасность не подтверждена',
                             'опасно — ветер 12 м/с; ✅ можно кататься')
        out = self._run(text=mixed, fake=outage)
        self.assertFalse(out.block_publication)
        self.assertIn('опасно', out.publisher_text)
        self.assertIn('ветер 12 м/с', out.publisher_text)
        self.assertNotIn('✅ можно кататься', out.publisher_text)
        self.assertIn('SOURCE_UNAVAILABLE', out.reason)

        # A benign visibility observation must not erase a no-go sentence.
        caution = BASE.replace('безопасность не подтверждена',
                               'опасно — ветер 12 м/с, хорошая видимость')
        out = self._run(text=caution, fake=outage)
        self.assertFalse(out.block_publication)
        self.assertEqual(caution, out.publisher_text)

        direction = BASE.replace('безопасность не подтверждена',
                                '✅ Отлично: Серф (западный ветер, вдоль берега)')
        out = self._run(text=direction, fake=outage)
        self.assertFalse(out.block_publication)
        self.assertNotIn('✅ Отлично: Серф', out.publisher_text)
        self.assertIn('западный ветер, вдоль берега', out.publisher_text)

        # `опасн` must not accidentally match the substring in `безопасно`.
        unsupported = BASE.replace('безопасность не подтверждена',
                                   'безопасно, хорошая видимость')
        out = self._run(text=unsupported, fake=outage)
        self.assertFalse(out.block_publication)
        self.assertNotIn('безопасно, хорошая видимость', out.publisher_text)
        self.assertIn('безопасность не подтверждена', out.publisher_text)

        unsafe = BASE.replace('безопасность не подтверждена',
                              'небезопасно: порывы до 14 м/с')
        out = self._run(text=unsafe, fake=outage)
        self.assertFalse(out.block_publication)
        self.assertEqual(unsafe, out.publisher_text)

    def test_outage_respects_city_boundary_and_each_marine_line(self):
        # Read two existing FORMAT_V2 fixtures without importing its side-effectful
        # module (it installs isolated Telegram/pendulum stubs).
        import ast
        fixture_src = (ROOT / 'tools/test_format_v2_cy.py').read_text(encoding='utf-8')
        fixtures = {
            target.id: ast.literal_eval(node.value)
            for node in ast.parse(fixture_src).body
            if isinstance(node, ast.Assign) and len(node.targets) == 1
            for target in node.targets
            if isinstance(target, ast.Name)
            and target.id in ('GROUPED_WATER_SPORTS_EVENING', 'SURF_CARRY_OVER_EVENING')
        }
        self.assertEqual(2, len(fixtures))
        outage = lambda _url: (_ for _ in ()).throw(TimeoutError('offline'))
        for name, text in fixtures.items():
            with self.subTest(fixture=name):
                out = self._run(text=text, fake=outage)
                self.assertFalse(out.block_publication)
                self.assertEqual('SOURCE_UNAVAILABLE', out.reason)
                original_marine = [line for line in text.splitlines()
                                   if '🧜‍♂️ Отлично:' in line]
                self.assertTrue(original_marine)
                self.assertNotIn('🧜‍♂️ Отлично:', out.publisher_text)
                self.assertEqual(len(original_marine),
                    out.publisher_text.count('По прогнозу: западный ветер, вдоль берега.'))
                if name == 'GROUPED_WATER_SPORTS_EVENING':
                    self.assertIn('SUP лучше отложить: порывы до 15 м/с',
                                  out.publisher_text)

    def test_outage_followup_positive_and_mixed_danger(self):
        outage = lambda _url: (_ for _ in ()).throw(TimeoutError('offline'))
        for advice in ('Хорошие условия, чтобы кататься вечером',
                       'Хорошие волны для сёрфа'):
            with self.subTest(advice=advice):
                text = BASE.replace('безопасность не подтверждена',
                                    'не рекомендуется\n' + advice)
                out = self._run(text=text, fake=outage)
                self.assertFalse(out.block_publication)
                self.assertNotIn(advice, out.publisher_text)
                self.assertIn('безопасность не подтверждена', out.publisher_text)
        text = BASE.replace('безопасность не подтверждена',
                            'опасно, хорошие волны для сёрфа')
        out = self._run(text=text, fake=outage)
        self.assertFalse(out.block_publication)
        self.assertNotIn('хорошие волны для сёрфа', out.publisher_text)
        self.assertIn('Ограничения прогноза: опасно', out.publisher_text)
        mixed_hazard = BASE.replace('безопасность не подтверждена',
            'шторм, сильный ветер 16 м/с; ✅ можно кататься')
        out = self._run(text=mixed_hazard, fake=outage)
        self.assertFalse(out.block_publication)
        self.assertIn('шторм', out.publisher_text)
        self.assertIn('сильный ветер', out.publisher_text)
        self.assertIn('16 м/с', out.publisher_text)
        self.assertNotIn('✅ можно кататься', out.publisher_text)
        gust = BASE.replace('безопасность не подтверждена',
            '✅ Отлично: Кайт (порывы до 10 м/с, северо-западный ветер к берегу)')
        out = self._run(text=gust, fake=outage)
        self.assertFalse(out.block_publication)
        self.assertEqual(1, out.publisher_text.count('порывы до 10 м/с'))
        self.assertIn('северо-западный ветер к берегу', out.publisher_text)

if __name__ == '__main__':
    unittest.main(verbosity=2)
