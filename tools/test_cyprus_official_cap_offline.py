#!/usr/bin/env python3
"""R8-B2 zero-network Golden Set: three real Lithuanian CAP + synthetic negatives.

The tests NEVER treat foreign data or specification-derived Update/Cancel as
proof of Cyprus source completeness, or as a valid alert for publication.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET
import hashlib
import importlib.util
import json
import sys
import unittest

BASE = Path(__file__).resolve().parent
F = BASE / "fixtures"
P = BASE / "cyprus_official_cap_offline.py"
spec = importlib.util.spec_from_file_location("cyprus_official_cap_offline", P)
mod = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = mod
spec.loader.exec_module(mod)
RAW_ATOM = (F / "lithuania_atom.raw.xml").read_bytes()
RAW_CAPS = [(F / f"lithuania_cap_{i}.raw.xml").read_bytes() for i in (1, 2, 3)]
MANIFEST = json.loads((F / "lithuania_capture_manifest.json").read_text())
GOLDEN = json.loads((F / "lithuania_r8b2_golden_set.json").read_text())
URLS = MANIFEST["cap_candidate_urls"]
RECEIVED = datetime.fromisoformat(MANIFEST["observed_calls"][0]["finished_utc"])
SERVER = datetime.strptime(MANIFEST["observed_calls"][0]["response_headers"]["date"], "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=timezone.utc)


def _run(atom=RAW_ATOM, caps=None, recv=RECEIVED, server=SERVER, source=None):
    if caps is None:
        caps = dict(zip(URLS, RAW_CAPS))
    return mod.inspect_lt_atom_cap_samples(atom, caps,
        source_url=mod.LT_FEED if source is None else source,
        received_at=recv, http_date_utc=server)


def _xml_change(raw: bytes, tag: str, value: str, *, occurrence: int = 0) -> bytes:
    tree = ET.fromstring(raw)
    nodes = tree.findall('.//' + tag)
    if not nodes or len(nodes) <= occurrence:
        raise AssertionError(f"mutation anchor absent: {tag} occurrence={occurrence}")
    nodes[occurrence].text = value
    return ET.tostring(tree, encoding='utf-8', xml_declaration=True)


def _xml_duplicate(raw: bytes, tag: str) -> bytes:
    tree = ET.fromstring(raw)
    node = tree.find('.//' + tag)
    if node is None: raise AssertionError(tag)
    tree.append(ET.fromstring(ET.tostring(node)))
    return ET.tostring(tree, encoding='utf-8')


def _run_mutation(kind: str):
    caps = dict(zip(URLS, RAW_CAPS))
    atom, recv, server, source = RAW_ATOM, RECEIVED, SERVER, None
    a = mod.ATOM_TAG
    c = mod.CAP_TAG
    if kind == 'real_capture': pass
    elif kind == 'fresh_synthetic_receive': recv = server = datetime(2026,10,22,11,30,0,tzinfo=timezone.utc)
    elif kind == 'missing_cap': caps.pop(URLS[2])
    elif kind == 'extra_cap': caps['https://feeds.meteoalarm.org/api/v1/warnings/feeds-lithuania/00000000-0000-0000-0000-000000000000'] = RAW_CAPS[0]
    elif kind == 'empty_feed':
        node = ET.fromstring(atom)
        for ent in node.findall(a+'entry'): node.remove(ent)
        atom = ET.tostring(node)
    elif kind == 'wrong_source': source = 'https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-cyprus'
    elif kind == 'wrong_country': atom = _xml_change(atom, a+'id', 'tag:meteoalarm.org,2021-02-19:CY')
    elif kind == 'stale_feed': atom = _xml_change(atom,a+'updated','2026-10-21T11:05:02Z')
    elif kind == 'future_feed': atom = _xml_change(atom,a+'updated','2026-10-23T11:05:02Z')
    elif kind == 'wrong_generator': atom = _xml_change(atom,a+'generator','other')
    elif kind == 'bad_self': atom = atom.replace(mod.LT_FEED.encode(),b'https://other.test/feed',1)
    elif kind == 'invalid_atom': atom = b'<broken'
    elif kind == 'entity_atom': atom = b'<!ENTITY x "aaa">'+atom
    elif kind == 'doctype_cap': caps[URLS[0]] = b'<!DOCTYPE alert>'+caps[URLS[0]]
    elif kind == 'oversize_cap': caps[URLS[0]] = caps[URLS[0]] + b' '*(mod.MAX_CAP_BYTES+1)
    elif kind == 'bad_cap_root':
        tree = ET.fromstring(caps[URLS[0]]);tree.tag = c+'wrong';caps[URLS[0]]=ET.tostring(tree)
    elif kind == 'id_mismatch': caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'identifier','FAKE')
    elif kind == 'sender_mismatch': caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'sender','FAKE')
    elif kind == 'type_mismatch': caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'msgType','Cancel')
    elif kind == 'actual_cancel':
        caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'msgType','Cancel')
        atom = _xml_change(atom,c+'message_type','Cancel')
    elif kind == 'actual_update':
        caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'msgType','Update')
        atom = _xml_change(atom,c+'message_type','Update')
    elif kind == 'wrong_status': caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'status','Test')
    elif kind == 'wrong_scope': caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'scope','Private')
    elif kind == 'matched_test_status':
        # Synthetic B5 control: Atom and CAP agree, so header mismatch cannot mask the policy guard.
        atom = _xml_change(atom,c+'status','Test')
        caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'status','Test')
    elif kind == 'matched_private_scope':
        # Synthetic B5 control for scope, independent of the status case.
        atom = _xml_change(atom,c+'scope','Private')
        caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'scope','Private')
    elif kind == 'duplicate_identifier_distinct_links':
        # Synthetic B7 control: different CAP URLs with the SAME message identifier.
        first_identifier = ET.fromstring(RAW_CAPS[0]).findtext(c+'identifier')
        assert first_identifier
        atom = _xml_change(atom,c+'identifier',first_identifier,occurrence=1)
        caps[URLS[1]] = _xml_change(caps[URLS[1]],c+'identifier',first_identifier)
    elif kind == 'wrong_sent': caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'sent','2026-10-20T10:35:14+03:00')
    elif kind == 'bad_onset': caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'onset','2026-11-01T10:35:14+03:00')
    elif kind == 'bad_expiry': caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'expires','2026-10-20T10:35:14+03:00')
    elif kind == 'wrong_severity': caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'severity','Extreme')
    elif kind == 'wrong_urgency': caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'urgency','Unknown')
    elif kind == 'wrong_certainty': caps[URLS[0]] = _xml_change(caps[URLS[0]],c+'certainty','Unknown')
    elif kind == 'wrong_geocode':
        tree=ET.fromstring(caps[URLS[0]]);tree.find('.//'+c+'geocode/'+c+'value').text='LT999';caps[URLS[0]]=ET.tostring(tree)
    elif kind == 'wrong_awareness':
        tree=ET.fromstring(caps[URLS[0]])
        for p in tree.findall('.//'+c+'parameter'):
            if p.findtext(c+'valueName')=='awareness_level':p.find(c+'value').text='4; red; Extreme'
        caps[URLS[0]]=ET.tostring(tree)
    elif kind == 'missing_english':
        tree=ET.fromstring(caps[URLS[0]])
        for inf in tree.findall(c+'info'):
            if inf.findtext(c+'language')=='en-GB':tree.remove(inf)
        caps[URLS[0]]=ET.tostring(tree)
    elif kind == 'duplicate_language':
        tree=ET.fromstring(caps[URLS[0]]);inf=tree.find(c+'info');tree.append(ET.fromstring(ET.tostring(inf)));caps[URLS[0]]=ET.tostring(tree)
    elif kind == 'cap_link_cross_host': atom = atom.replace(URLS[0].encode(),b'https://evil.example/api/v1/warnings/feeds-lithuania/18c05a22-9e43-4de4-bb33-e03b42f900c8')
    elif kind == 'cap_link_query': atom = atom.replace((URLS[0]+'"').encode(),(URLS[0]+'?evil=1"').encode())
    elif kind == 'wrong_atom_identifier': atom = _xml_change(atom,c+'identifier','OTHER')
    elif kind == 'wrong_atom_msgtype': atom = _xml_change(atom,c+'message_type','Cancel')
    elif kind == 'wrong_atom_geography': atom = _xml_change(atom,a+'value','LT999')
    elif kind == 'wrong_capture_clock': recv = server + timedelta(minutes=7)
    elif kind == 'naive_capture_time': recv = recv.replace(tzinfo=None)
    elif kind == 'duplicate_cap_link':
        tree=ET.fromstring(atom);ent=tree.find(a+'entry');link=next(x for x in ent.findall(a+'link') if x.get('type')=='application/cap+xml');ent.append(ET.fromstring(ET.tostring(link)));atom=ET.tostring(tree)
    else: raise AssertionError(f'Unsupported Golden mutation {kind}')
    return _run(atom,caps,recv,server,source)


class LithuaniaCAPGoldenSet(unittest.TestCase):
    def test_provenance_and_raw_hashes(self):
        known = {
            'lithuania_atom.raw.xml':'725034bbadae9945284d494df44d1e8b90e2010af21f4906d7113be1c57a43ef',
            'lithuania_cap_1.raw.xml':'ac867d342f6c2003480c3471cdd018e41a5cd71b0261531c2ceebcae1993ca19',
            'lithuania_cap_2.raw.xml':'79c9c3aba801512b9b64fd6489c1ddc8129835b17fd91e96685c9d4c4eb2ea8a',
            'lithuania_cap_3.raw.xml':'cf2eb9921370375fdca34fa1d9973c3b0391e01baf9d4365acd3f11210da291b',
        }
        for name,sha in known.items():
            with self.subTest(name=name):self.assertEqual(sha,hashlib.sha256((F/name).read_bytes()).hexdigest())
        self.assertEqual(4, len(MANIFEST['observed_calls']))
        self.assertEqual([200]*4,[r['http_status'] for r in MANIFEST['observed_calls']])
        self.assertIs(MANIFEST['official_warnings_checked'], False)
        self.assertIs(MANIFEST['clean_verified'], False)

    def test_real_structure_stale_guard(self):
        r = _run()
        self.assertEqual(mod.SampleState.STALE_UNVERIFIED,r.state)
        self.assertEqual(3,r.sample_count)
        self.assertEqual(3,r.entry_count)
        self.assertEqual('FEED_UPDATED_OLDER_THAN_30M',r.reason)
        self.assertEqual(['2.49.0.0.440.0.LT.261022133514__001',
                          '2.49.0.0.440.0.LT.261022133514__002',
                          '2.49.0.0.440.0.LT.261022133514__000'],[s.identifier for s in r.samples])
        self.assertEqual(['LT801']*3,[s.emma_id for s in r.samples])
        self.assertEqual(['LHMS']*3,[s.sender for s in r.samples])
        self.assertEqual(['Moderate']*3,[s.severity for s in r.samples])
        self.assertEqual([2]*3,[s.language_count for s in r.samples])
        self.assertIs(r.official_warnings_checked,False)
        self.assertIs(r.clean_verified,False)

    def test_all_golden_scenarios(self):
        cases=GOLDEN['cases']
        self.assertGreaterEqual(len(cases),35)
        self.assertEqual(len(cases),len({v['id'] for v in cases}))
        for case in cases:
            with self.subTest(case=case['id']):
                r=_run_mutation(case['mutation'])
                self.assertEqual(case['state'],r.state.value,r.reason)
                if case.get('reason'):self.assertEqual(case['reason'],r.reason)
                self.assertIs(r.official_warnings_checked,False)
                self.assertIs(r.clean_verified,False)

    def test_temporal_segments_not_collapsed(self):
        r = _run()
        a = [(x.onset_utc, x.expires_utc) for x in r.samples]
        self.assertEqual(3,len(set(a)))
        self.assertEqual([a[2],a[0],a[1]],sorted(a))  # feed is not chronological
        self.assertLess(a[2][0],RECEIVED)
        self.assertGreater(a[0][0],RECEIVED)
        self.assertGreater(a[1][0],RECEIVED)

    def test_all_results_immutable_and_nonverified(self):
        from dataclasses import FrozenInstanceError
        for kind in [c['mutation'] for c in GOLDEN['cases']]:
            r=_run_mutation(kind)
            with self.assertRaises((AttributeError,FrozenInstanceError)):
                r.clean_verified = True
            with self.assertRaises((AttributeError,FrozenInstanceError)):
                r.official_warnings_checked = True

    def test_no_network_or_publication_imports(self):
        source=P.read_text()
        for forbidden in ('import requests','import urllib.request','import socket','urlopen(',
                          'import post_common','import safe_test_post','import telegram','CLEAN_VERIFIED'):
            self.assertNotIn(forbidden, source)
        self.assertIn('CAP_STRUCTURE_VALID_UNTRUSTED_FOR_CYPRUS',source)

if __name__ == '__main__':unittest.main(verbosity=2)
