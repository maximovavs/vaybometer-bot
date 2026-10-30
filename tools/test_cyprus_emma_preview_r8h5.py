"""R8-H5 offline synthetic/non-publishing tests for a one-way CAP warning preview."""
import copy
from datetime import date, datetime, timedelta, timezone
import socket
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from cyprus_cap_r8g1_contract import ATOM, CAP, FEED_URL, inspect_captured_pair
from cyprus_emma_preview_r8h5 import (
    OfflinePreview, preview_warning_capture, insert_preview_after_format_v2,
)
from test_cyprus_cap_r8g1_contract import NOW, attrs, make_pair, mutate_xml

CT = f'{{{CAP}}}'
TARGET = date(2026,10,28)


def pair(*, count=1, msg_type='Alert', references=None, event='Thunderstorm',
         area='Cyprus', severity='Moderate'):
    atom, docs = make_pair(count=count, msg_type=msg_type, references=references)
    changed = {}
    for url, xml in docs.items():
        root = ET.fromstring(xml)
        root.find(f'{CT}info/{CT}event').text = event
        root.find(f'{CT}info/{CT}area/{CT}areaDesc').text = area
        root.find(f'{CT}info/{CT}severity').text = severity
        changed[url] = ET.tostring(root,encoding='utf-8')
    return atom,changed


def preview(atom=None, docs=None, clock=NOW, date_=TARGET, **source_changes):
    if atom is None:
        atom, docs = pair()
    return preview_warning_capture(atom=atom,cap_by_url=docs,source=attrs(atom,docs,**source_changes),
                                   at_utc=clock,forecast_date=date_)


class PreviewTests(unittest.TestCase):
    def guard(self,p, classification='UNKNOWN'):
        self.assertIsInstance(p,OfflinePreview)
        self.assertEqual('NO_POST',p.publication_action)
        self.assertEqual(classification,p.classification)
        for attr in ('official_warnings_checked','clean_verified','city_cleared',
                     'positive_water_guidance_allowed','national_inventory_complete'):
            self.assertIs(getattr(p,attr),False,attr)
        if classification=='UNKNOWN': self.assertFalse(p.text)

    def test_single_future_cap_alert_is_candidate_only(self):
        atom, docs=pair()
        p=preview(atom,docs)
        self.guard(p,'OFFLINE_OBSERVED_ALERT_CANDIDATE')
        self.assertIn('По данным MeteoAlarm',p.text)
        self.assertIn('грозах',p.text)
        self.assertIn('Территория по CAP: Cyprus',p.text)
        self.assertIn('28.10 02:00',p.text)
        self.assertIn('28.10 17:59',p.text)
        self.assertIn('ожидается',p.text)
        self.assertIn('Источник: '+FEED_URL,p.text)
        self.assertNotIn('официальные предупреждения проверены',p.text)
        self.assertNotIn('SUP',p.text)

    def test_existing_r8g1_stays_unknown_no_post(self):
        atom, docs=pair()
        v=inspect_captured_pair(atom=atom,cap_by_url=docs,source=attrs(atom,docs),at_utc=NOW)
        self.assertEqual('UNKNOWN',v.state)
        self.assertEqual('NO_POST',v.publication_action)
        self.assertFalse(v.official_warnings_checked)
        self.guard(preview(atom,docs),'OFFLINE_OBSERVED_ALERT_CANDIDATE')

    def test_invalid_source_freshness_status_and_hash_are_no_preview(self):
        atom,docs=pair()
        for override in ({'http_status':401},{'url':'https://bad.invalid/'},
                         {'sha256':'0'*64},{'credentials_sent':True},
                         {'redirect_followed':True},
                         {'received_utc':'2026-10-27T16:00:00Z','http_date_utc':'2026-10-27T16:00:00Z'}):
            with self.subTest(override=override): self.guard(preview(atom,docs,**override))

    def test_empty_feed_never_means_clear(self):
        atom,docs=make_pair(count=0)
        self.guard(preview(atom,docs))

    def test_cancel_update_not_displayed_even_with_history_unavailable(self):
        for t in ('Update','Cancel'):
            atom,docs=pair(msg_type=t)
            with self.subTest(type=t): self.guard(preview(atom,docs))

    def test_no_city_mapping_or_positive_marine_statements(self):
        atom,docs=pair(area='Coastal Sector West')
        p=preview(atom,docs)
        self.guard(p,'OFFLINE_OBSERVED_ALERT_CANDIDATE')
        self.assertIn('Coastal Sector West',p.text)
        self.assertNotIn('Лимассол',p.text)
        for token in ('город безопасен','предупреждений нет','кайт разрешён','SUP безопасен'):
            self.assertNotIn(token,p.text)

    def test_html_escape_and_unknown_severity(self):
        atom,docs=pair(area='A <b> & B')
        p=preview(atom,docs)
        self.guard(p,'OFFLINE_OBSERVED_ALERT_CANDIDATE')
        self.assertIn('A &lt;b&gt; &amp; B',p.text)
        atom,docs=pair(severity='Unknown')
        self.guard(preview(atom,docs))
        for malicious in ('Cyprus\nSUP safe', 'Cyprus\rCANCEL', 'Cyprus\u202ealert'):
            atom,docs=pair(area=malicious)
            with self.subTest(malicious=repr(malicious)):
                self.guard(preview(atom,docs))

    def test_synthetic_fixture_and_unknown_hazard_not_publishable(self):
        atom,docs=make_pair()  # real R8-G1 synthetic sample explicitly says DO-NOT-PUBLISH
        self.guard(preview(atom,docs))
        atom,docs=pair(event='UNCLASSIFIED HAZARD')
        self.guard(preview(atom,docs))

    def test_forecast_date_and_expiry_fail_closed(self):
        atom,docs=pair()
        self.guard(preview(atom,docs,date_=date(2026,10,29)))
        self.guard(preview(atom,docs,clock=NOW+timedelta(days=2)))
        self.guard(preview(atom,docs,date_=None))
        self.guard(preview(atom,docs,date_='2026-10-28'))

    def test_warns_active_if_clock_in_onset_interval_with_fresh_capture(self):
        atom,docs=pair()
        active=datetime(2026,10,28,8,0,tzinfo=timezone.utc)
        root=ET.fromstring(atom)
        root.find(f'{{{ATOM}}}updated').text=active.isoformat()
        atom=ET.tostring(root, encoding='utf-8')
        source=attrs(atom,docs,received_utc=active.isoformat(),http_date_utc=active.isoformat())
        for c in source['cap_evidence'].values():
            c['received_utc']=active.isoformat()
            c['http_date_utc']=active.isoformat()
        p=preview_warning_capture(atom=atom,cap_by_url=docs,source=source,
            at_utc=active,forecast_date=TARGET)
        self.guard(p,'OFFLINE_OBSERVED_ALERT_CANDIDATE')
        self.assertIn('действует',p.text)

    def test_partial_invalid_pair_blocks_all(self):
        atom,docs=pair(count=2)
        changed=copy.deepcopy(docs)
        u=list(changed)[1]
        changed[u]=mutate_xml(changed[u],'Unknown',tag=CT+'info/'+CT+'severity')
        self.guard(preview(atom,changed))

    def test_injection_after_final_format_does_not_erase_prior_guards(self):
        atom,docs=pair()
        p=preview(atom,docs)
        text=('<b>🌅 Кипр завтра (28.10.2026)</b>\n'
              '⚠️ По прогнозу: сильные порывы ветра\n'
              '🏄 SUP / кайт / сёрф: небезопасно\n'
              '✨ VayboMeter: н/д — официальные предупреждения не проверены.\n'
              '#Кипр #погода')
        out=insert_preview_after_format_v2(text,p)
        self.assertTrue(out.startswith('<b>🌅 Кипр'))
        self.assertIn('⚠️ По прогнозу: сильные порывы ветра',out)
        self.assertIn('SUP / кайт / сёрф: небезопасно',out)
        self.assertIn('VayboMeter: н/д',out)
        self.assertTrue(out.rstrip().endswith('#Кипр #погода'))
        self.assertEqual(out.count('По данным MeteoAlarm'),1)
        self.assertEqual(out, insert_preview_after_format_v2(out,p))  # idempotent
        self.assertEqual(text,insert_preview_after_format_v2(text,OfflinePreview()))

    def test_no_network_in_preview(self):
        atom,docs=pair()
        with patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK')):
            self.guard(preview(atom,docs),'OFFLINE_OBSERVED_ALERT_CANDIDATE')

    def test_malformed_inputs_are_unknown(self):
        atom,docs=pair()
        for damaged in (None,False,'bad',0,b'',b'<bad>'):
            with self.subTest(damaged=str(damaged)[:30]):
                result=preview_warning_capture(atom=damaged,cap_by_url=docs,
                    source=attrs(atom,docs),at_utc=NOW,forecast_date=TARGET)
                self.guard(result)


if __name__=='__main__': unittest.main(verbosity=2)
