"""R8-G1 offline acceptance; deterministic, synthetic input, no network/API calls.

If executed in the full repository, also exercises the *original* R8-C2A,
R8-C3B and R8-E4A JSON fixtures. The local standalone fallback explicitly
replays their recorded invariants, not their raw evidence bytes.
"""
import copy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import socket
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

from cyprus_cap_r8g1_contract import (
    ATOM, CAP, FEED_URL, FEED_ID, ContractDecision, authority_sender_status,
    classify_golden_contract_case, emma_city_status, geography_status,
    inspect_captured_pair, lifecycle_status, warning_phase,
)

HERE = Path(__file__).resolve().parent
FX = HERE / 'fixtures'
AT = f'{{{ATOM}}}'
CT = f'{{{CAP}}}'
CAP_URL = 'https://feeds.meteoalarm.org/api/v1/warnings/feeds-cyprus/ffb0ff82-5a6d-4c33-9e8c-2aefb5e43cf6'
NOW = datetime(2026, 10, 27, 17, 9, 10, tzinfo=timezone.utc)


def dt(t):
    return t.astimezone(timezone.utc).isoformat().replace('+00:00', 'Z')


def set_text(node, tag, text):
    return ET.SubElement(node, tag).__setattr__('text', text)


def make_pair(*, count=1, msg_type='Alert', references=None):
    feed = ET.Element(AT + 'feed')
    for key, val in ((AT + 'id', FEED_ID), (AT + 'updated', '2026-10-27T17:05:16Z')):
        ET.SubElement(feed, key).text = val
    ET.SubElement(feed, AT + 'title').text = 'SYNTHETIC CY test-only'
    ET.SubElement(feed, AT + 'generator').text = 'MeteoAlarm Producer Server'
    author = ET.SubElement(feed, AT + 'author')
    ET.SubElement(author, AT + 'name').text = 'meteoalarm.org'
    ET.SubElement(author, AT + 'uri').text = 'https://meteoalarm.org'
    ET.SubElement(feed, AT + 'link', {'rel': 'self', 'href': FEED_URL})
    docs = {}
    for i in range(count):
        url = CAP_URL if i == 0 else CAP_URL[:-1] + str(i)
        ident = f'SYNTHETIC-CY-{i}-DO-NOT-PUBLISH'
        ent = ET.SubElement(feed, AT + 'entry')
        ET.SubElement(ent, AT + 'id').text = f'urn:synthetic:warning:{i}'
        ET.SubElement(ent, CT + 'identifier').text = ident
        ET.SubElement(ent, CT + 'status').text = 'Actual'
        ET.SubElement(ent, CT + 'message_type').text = msg_type
        ET.SubElement(ent, AT + 'link', {'href': url, 'type': 'application/cap+xml'})
        emma = ET.SubElement(ent, CT + 'geocode')
        ET.SubElement(emma, AT + 'valueName').text = 'EMMA_ID'
        ET.SubElement(emma, AT + 'value').text = 'CY001'
        cap = ET.Element(CT + 'alert')
        for key, val in [('identifier', ident), ('sender', 'synthetic-test@example.invalid'),
                         ('sent', '2026-10-27T15:46:00+02:00'), ('status', 'Actual'),
                         ('msgType', msg_type), ('scope', 'Public')]:
            ET.SubElement(cap, CT + key).text = val
        if references is not None:
            ET.SubElement(cap, CT + 'references').text = references
        info = ET.SubElement(cap, CT + 'info')
        for key, val in [('event', 'SYNTHETIC TEST-ONLY thunderstorm'), ('severity', 'Moderate'),
                         ('onset', '2026-10-28T02:00:00+02:00'), ('expires', '2026-10-28T17:59:59+02:00')]:
            ET.SubElement(info, CT + key).text = val
        area = ET.SubElement(info, CT + 'area')
        ET.SubElement(area, CT + 'areaDesc').text = 'Cyprus'
        code = ET.SubElement(area, CT + 'geocode')
        ET.SubElement(code, CT + 'valueName').text = 'EMMA_ID'
        ET.SubElement(code, CT + 'value').text = 'CY001'
        docs[url] = ET.tostring(cap, encoding='utf-8')
    return ET.tostring(feed, encoding='utf-8'), docs


def attrs(atom, cap_by_url=None, **overrides):
    if cap_by_url is None:
        cap_by_url = make_pair()[1]
    d = {'url': FEED_URL, 'http_status': 200, 'redirect_followed': False,
         'credentials_sent': False, 'sha256': hashlib.sha256(atom).hexdigest(),
         'received_utc': dt(NOW), 'http_date_utc': dt(NOW),
         'content_type': 'application/atom+xml; charset=utf-8',
         'cap_evidence': {url:{'http_status':200,'redirect_followed':False,
            'credentials_sent':False,'content_type':'application/cap+xml',
            'sha256':hashlib.sha256(data).hexdigest(),
            'received_utc':dt(NOW),'http_date_utc':dt(NOW)} for url,data in cap_by_url.items()}}
    d.update(overrides)
    return d


def mutate_xml(xml, change, *, remove=False, tag=None):
    root = ET.fromstring(xml)
    node = root.find(tag)
    assert node is not None
    if remove:
        node_parent = None
        for parent in root.iter():
            if node in parent:
                node_parent = parent
                break
        assert node_parent is not None
        node_parent.remove(node)
    else:
        node.text = change
    return ET.tostring(root, encoding='utf-8')


class ContractTests(unittest.TestCase):
    def check_failclosed(self, verdict, reason=None):
        self.assertIsInstance(verdict, ContractDecision)
        self.assertEqual('UNKNOWN', verdict.state)
        self.assertEqual('NO_POST', verdict.publication_action)
        for k in ('official_warnings_checked', 'clean_verified', 'city_cleared',
                  'positive_water_guidance_allowed', 'national_inventory_complete'):
            self.assertIs(getattr(verdict, k), False, k)
        if reason:
            self.assertEqual(reason, verdict.reason)

    def test_r8e4a_synthetic_matching_pair_never_authenticates_sender(self):
        atom, docs = make_pair()
        self.check_failclosed(inspect_captured_pair(atom=atom, cap_by_url=docs,
                    source=attrs(atom, docs), at_utc=NOW),
                    'CAP_MATCH_SENDER_UNVERIFIED_INVENTORY_UNKNOWN')

    def test_r8c3b_empty_feed_not_clean_even_with_http_200(self):
        atom, docs = make_pair(count=0)
        for sample_time in (NOW, NOW + timedelta(minutes=10)):
            self.check_failclosed(inspect_captured_pair(atom=atom, cap_by_url=docs,
                source=attrs(atom, docs, received_utc=dt(sample_time), http_date_utc=dt(sample_time)),
                at_utc=sample_time), 'EMPTY_UNVERIFIED')
        # HTTP 401 of metadata/geocodes cannot unlock EMMA area mapping.
        self.assertEqual('UNKNOWN_EMMA_CITY_MAPPING', emma_city_status('CY001', 'Limassol'))
        self.check_failclosed(inspect_captured_pair(atom=atom, cap_by_url=docs,
            source=attrs(atom, docs, http_status=401), at_utc=NOW), 'UNKNOWN_SOURCE_TRANSPORT')

    def test_r8e4a_local_timezone_phase_and_half_open_boundaries(self):
        warning = ('2026-10-27T15:46:00+02:00', '2026-10-28T02:00:00+02:00',
                   '2026-10-28T18:00:00+02:00')
        for when, expected in [
            ('2026-10-27T15:45:59+02:00', 'NOT_YET_ISSUED'),
            ('2026-10-27T15:46:00+02:00', 'UPCOMING'),
            ('2026-10-28T01:59:59+02:00', 'UPCOMING'),
            ('2026-10-28T02:00:00+02:00', 'ACTIVE'),
            ('2026-10-28T00:00:00Z', 'ACTIVE'),
            ('2026-10-28T17:59:59+02:00', 'ACTIVE'),
            ('2026-10-28T18:00:00+02:00', 'EXPIRED_HISTORY'),
            ('2026-10-28T12:00:00', 'UNKNOWN')]:
            with self.subTest(when=when):
                self.assertEqual(warning_phase(*warning, when), expected)

    def test_r8c2a_golden_cases_full_when_available(self):
        f = FX / 'cyprus_r8c2a_contract_golden_set.json'
        if f.exists():
            data = json.loads(f.read_text('utf-8'))
            self.assertEqual('cyprus-r8-c2a-fail-closed-contract-v1', data['schema'])
            self.assertEqual(32, len(data['contract_cases']))
            for case in data['contract_cases']:
                with self.subTest(id=case['id']):
                    self.assertEqual(case['expected'], classify_golden_contract_case(case))
            for prohibited in data['prohibited_positive_states']:
                self.assertNotEqual(prohibited, ContractDecision().state)
        else:
            # R8-C2A case-by-case projection from read-only GitHub fixture.
            cases = [
                ({'kind':'authority','oid':'2.49.0.0.196.0'},'AUTHORITY_REGISTERED_SENDER_UNPROVEN'),
                ({'kind':'authority','oid':'SYNTHETIC-OTHER-OID'},'UNKNOWN_AUTHORITY'),
                *[({'kind':'authority','oid':'2.49.0.0.196.0','claimed_cap_sender':v},
                   'CAP_SENDER_UNVERIFIED') for v in ('2.49.0.0.196.0','LHMS','Meteorological Service')],
                ({'kind':'endpoint','url':FEED_URL},'REGISTERED_ENDPOINT_ONLY'),
                *[({'kind':'endpoint','url':v},'UNKNOWN_ENDPOINT') for v in
                   ('https://feeds.meteoalarm.org/feeds/meteoalarm-legacy-atom-lithuania',
                    'https://feeds.meteoalarm.org.evil.invalid/feeds/meteoalarm-legacy-atom-cyprus')],
                *[({'kind':'geocode','emma_id':id_,'city':city},'UNKNOWN_EMMA_CITY_MAPPING') for id_,city in
                  [('SYNTHETIC_UNKNOWN_CY_EMMA_NOT_REAL','Limassol'),('LT801','Paphos'),
                   (None,'Larnaca'),('SYNTHETIC_UNKNOWN_CY_EMMA_NOT_REAL','Paralimni'),
                   (['SYNTHETIC_ZONE_A','SYNTHETIC_ZONE_B'],'Limassol')]],
                *[({'kind':'geography','product':p,'distance_km':d,'direction':di,'city':c},ex) for p,d,di,c,ex in [
                   ('land_weather_warnings',10,'inland',None,'GEOGRAPHY_TAXONOMY_ONLY'),
                   ('coastal_waters_forecast',8,'seaward',None,'GEOGRAPHY_TAXONOMY_ONLY'),
                   ('land_weather_warnings',8,'seaward','Limassol','UNKNOWN_PRODUCT_CONFLATION'),
                   ('coastal_waters_forecast',10,'inland','Limassol','UNKNOWN_PRODUCT_CONFLATION'),
                   ('land_weather_warnings',10,'inland','Paphos','UNKNOWN_LOCAL_ATTRIBUTION'),
                   ('coastal_waters_forecast',8,'seaward','SUP_SYNTHETIC_SPOT','UNKNOWN_LOCAL_ATTRIBUTION')]],
                ({'kind':'national_html'},'CAP_LIFECYCLE_UNVERIFIED'),
                *[({'kind':'cap_metadata',**fields},ex) for fields,ex in [
                  ({'message_type':'Alert','references':[],'document_complete':False},'UNKNOWN_CAP_DOCUMENT'),
                  ({'message_type':'Update','references':[],'document_complete':True},'UNKNOWN_UNRESOLVED_REFERENCES'),
                  ({'message_type':'Cancel','references':[],'document_complete':True},'UNKNOWN_UNRESOLVED_REFERENCES'),
                  ({'message_type':'Update','references':['SYNTHETIC_REFERENCE_NOT_REAL'],'document_complete':True},'UNKNOWN_UNRESOLVED_REFERENCES'),
                  ({'message_type':'Cancel','references':['SYNTHETIC_REFERENCE_NOT_REAL'],'document_complete':True},'UNKNOWN_UNRESOLVED_REFERENCES'),
                  ({'message_type':'Cancel','references':['SYNTHETIC_REFERENCE_NOT_REAL'],'history_resolved':True,'document_complete':True},'UNKNOWN_CAP_AUTHENTICITY'),
                  ({'message_type':'Alert','references':[],'document_complete':True,'expires':None},'UNKNOWN_CAP_AUTHENTICITY'),
                  ({'message_type':'Alert','references':[],'document_complete':False},'UNKNOWN_CAP_DOCUMENT'),
                  ({'message_type':'Alert','references':[],'document_complete':True,'duplicate_identifier':True},'UNKNOWN_DUPLICATE_IDENTIFIER')]],
                *[({'kind':'coverage','feed_state':f,'marine_state':'NIL'},'UNKNOWN_NATIONAL_COMPLETENESS')
                   for f in ('EMPTY_UNVERIFIED','UNKNOWN','ENTRIES_UNVERIFIED')]
            ]
            self.assertEqual(32, len(cases))
            for case, expected in cases:
                self.assertEqual(expected, classify_golden_contract_case(case), case)

    def test_r8c3b_original_metadata_invariants_when_available(self):
        f = FX / 'cyprus_r8c3b_empty_metadata_golden_set.json'
        if f.exists():
            d = json.loads(f.read_text('utf-8'))
            self.assertEqual(2, len(d['snapshots']))
            self.assertEqual(0, d['c3a_capture_extract']['atom_entry_count'])
            self.assertFalse(d['c3a_capture_extract']['clean_verified'])
            self.assertFalse(d['c3a_capture_extract']['official_warnings_checked'])
            self.assertEqual(401, d['metadata_401']['responses'][0]['http_status'])
            for city in d['cities']:
                self.assertEqual('UNKNOWN_EMMA_CITY_MAPPING', emma_city_status(None, city))
        else:
            for city in ('Limassol','Paphos','Larnaca','Paralimni','Nicosia'):
                self.assertEqual('UNKNOWN_EMMA_CITY_MAPPING', emma_city_status(None, city))

    def test_r8e4a_original_derived_metadata_when_available(self):
        f = FX / 'cyprus_r8e4a_emma68_derived.json'
        if f.exists():
            d = json.loads(f.read_text('utf-8'))
            self.assertEqual('CY001', d['warning']['cap_emma_id'])
            self.assertFalse(d['provenance']['cap']['sender_independently_authenticated'])
            self.assertFalse(d['provenance']['cap']['cap_signature_observed'])
            self.assertFalse(d['acceptance']['full_national_inventory_verified'])
            self.assertEqual('NO_POST', d['acceptance']['unknown_or_missing_warning_draft'])
        else:
            self.assertEqual('UNKNOWN_EMMA_CITY_MAPPING', emma_city_status('CY001','Limassol'))

    def test_negative_mutations_source_atom_and_cap(self):
        atom, docs = make_pair()
        good = attrs(atom, docs)
        source_mutations = [
            ({'url':'https://feeds.meteoalarm.org.evil.invalid/feeds/meteoalarm-legacy-atom-cyprus'},'UNKNOWN_SOURCE_URL'),
            ({'http_status':401},'UNKNOWN_SOURCE_TRANSPORT'),
            ({'redirect_followed':True},'UNKNOWN_SOURCE_TRANSPORT'),
            ({'credentials_sent':True},'UNKNOWN_SOURCE_TRANSPORT'),
            ({'sha256':'0'*64},'UNKNOWN_SOURCE_INTEGRITY'),
            ({'sha256':'NOT_SHA'},'UNKNOWN_SOURCE_INTEGRITY'),
            ({'content_type':'application/json'},'UNKNOWN_SOURCE_CONTENT_TYPE'),
            ({'http_date_utc':dt(NOW - timedelta(minutes=6))},'UNKNOWN_SOURCE_SERVER_CLOCK'),
            ({'received_utc':dt(NOW - timedelta(minutes=31)), 'http_date_utc':dt(NOW - timedelta(minutes=31))},'UNKNOWN_SOURCE_FRESHNESS'),
            ({'received_utc':'NOT_DATE'},'UNKNOWN_SOURCE_SERVER_CLOCK'),
        ]
        for change,reason in source_mutations:
            with self.subTest(source=change):
                v = inspect_captured_pair(atom=atom, cap_by_url=docs, source={**good,**change},at_utc=NOW)
                self.check_failclosed(v, reason)
        atom_mutations = [
            (b'<!DOCTYPE feed>\n' + atom, 'UNKNOWN_SOURCE_INTEGRITY'),
            (mutate_xml(atom,'tag:meteoalarm.org,2021-02-19:LT',tag=AT+'id'),'UNKNOWN_ATOM_COUNTRY'),
            (mutate_xml(atom,'2026-10-27T16:00:00Z',tag=AT+'updated'),'UNKNOWN_ATOM_UPDATED'),
            (mutate_xml(atom,'2026-10-27T17:20:00Z',tag=AT+'updated'),'UNKNOWN_ATOM_UPDATED'),
        ]
        for altered,reason in atom_mutations:
            with self.subTest(atom=reason):
                self.check_failclosed(inspect_captured_pair(atom=altered, cap_by_url=docs,
                    source=attrs(altered, docs),at_utc=NOW), 'UNKNOWN_ATOM_DOCUMENT' if reason == 'UNKNOWN_SOURCE_INTEGRITY' else reason)
        self.check_failclosed(inspect_captured_pair(atom=atom, cap_by_url={},
            source=attrs(atom,{}),at_utc=NOW),'UNKNOWN_INCOMPLETE_CAP_SET')
        self.check_failclosed(inspect_captured_pair(atom=atom, cap_by_url={**docs,'https://example.invalid/x':b'x'},
            source=attrs(atom,{**docs,'https://example.invalid/x':b'x'}),at_utc=NOW),'UNKNOWN_CAP_SET_EXTRANEOUS')
        url = next(iter(docs))
        cap = docs[url]
        cap_mutations = [
            (mutate_xml(cap, 'ALTERED',tag=CT+'identifier'),'UNKNOWN_ATOM_CAP_MISMATCH'),
            (mutate_xml(cap, 'Cancel',tag=CT+'msgType'),'UNKNOWN_ATOM_CAP_MISMATCH'),
            (mutate_xml(cap, '2026-10-27T20:00:00Z',tag=CT+'sent'),'UNKNOWN_CAP_FUTURE_SENT'),
            (mutate_xml(cap, '2026-10-27T16:00:00+02:00',tag=CT+'info/'+CT+'expires'),'UNKNOWN_CAP_TIME_ORDER'),
            (mutate_xml(cap, None,remove=True,tag=CT+'info/'+CT+'expires'),'UNKNOWN_CAP_TIME_WINDOW'),
            (mutate_xml(cap, None,remove=True,tag=CT+'info/'+CT+'area'),'UNKNOWN_CAP_AREA'),
            (mutate_xml(cap,'LT801',tag=CT+'info/'+CT+'area/'+CT+'geocode/'+CT+'value'),'UNKNOWN_ATOM_CAP_GEOCODE_MISMATCH'),
        ]
        for altered,reason in cap_mutations:
            with self.subTest(cap=reason):
                self.check_failclosed(inspect_captured_pair(atom=atom, cap_by_url={url:altered},
                    source=attrs(atom,{url:altered}),at_utc=NOW),reason)

    def test_lifecycle_update_cancel_references_cannot_clear(self):
        for msg_type in ('Update','Cancel'):
            for refs, history in ((None,None),
                      ('synthetic-test@example.invalid,OLD,2026-10-27T13:00:00Z',None),
                      ('synthetic-test@example.invalid,OLD,2026-10-27T13:00:00Z',
                       {('synthetic-test@example.invalid','OLD','2026-10-27T13:00:00+00:00'):{'verified':False}})):
                with self.subTest(typ=msg_type, refs=refs, history=history):
                    atom,docs=make_pair(msg_type=msg_type,references=refs)
                    self.check_failclosed(inspect_captured_pair(atom=atom,cap_by_url=docs,
                        source=attrs(atom, docs),at_utc=NOW,history=history),
                        'UNKNOWN_UNRESOLVED_REFERENCES' if history is None else
                        'CAP_MATCH_SENDER_UNVERIFIED_INVENTORY_UNKNOWN')


    def test_hostile_inputs_never_raise_or_clear(self):
        atom, docs = make_pair()
        for bad in (None, [], False, 0, 'hostile'):
            for item in (
                {'atom':bad, 'cap_by_url':docs, 'source':attrs(atom), 'at_utc':NOW},
                {'atom':atom, 'cap_by_url':bad, 'source':attrs(atom), 'at_utc':NOW},
                {'atom':atom, 'cap_by_url':docs, 'source':bad, 'at_utc':NOW},
                {'atom':atom, 'cap_by_url':docs, 'source':attrs(atom,docs), 'at_utc':bad},
            ):
                with self.subTest(bad=type(bad).__name__, field=next((k for k,v in item.items() if v is bad),'')):
                    self.check_failclosed(inspect_captured_pair(**item))

    def test_cap_capture_integrity_and_untrusted_sender(self):
        atom, docs = make_pair()
        url = next(iter(docs))
        good = attrs(atom, docs)
        for change,reason in [
            ({'sha256':'0'*64},'UNKNOWN_CAP_SOURCE_PROVENANCE'),
            ({'http_status':404},'UNKNOWN_CAP_SOURCE_PROVENANCE'),
            ({'redirect_followed':True},'UNKNOWN_CAP_SOURCE_PROVENANCE'),
            ({'credentials_sent':True},'UNKNOWN_CAP_SOURCE_PROVENANCE'),
            ({'content_type':'text/html'},'UNKNOWN_CAP_SOURCE_PROVENANCE'),
            ({'received_utc':dt(NOW-timedelta(minutes=31))},'UNKNOWN_CAP_SOURCE_FRESHNESS'),
        ]:
            source=copy.deepcopy(good)
            source['cap_evidence'][url].update(change)
            with self.subTest(cap_source=change):
                self.check_failclosed(inspect_captured_pair(atom=atom,cap_by_url=docs,
                                      source=source,at_utc=NOW), reason)
        altered=mutate_xml(docs[url], 'OTHER-SENDER',tag=CT+'sender')
        self.check_failclosed(inspect_captured_pair(atom=atom,cap_by_url={url:altered},
                      source=attrs(atom,{url:altered}),at_utc=NOW),
                      'CAP_MATCH_SENDER_UNVERIFIED_INVENTORY_UNKNOWN')
        # Sender content alone is never a verified authority assertion.
        self.assertEqual('CAP_SENDER_UNVERIFIED',
                         authority_sender_status('2.49.0.0.196.0', '2.49.0.0.196.0'))

    def test_no_network_no_publication_path(self):
        atom,docs=make_pair()
        with patch.object(socket.socket,'connect',side_effect=AssertionError('NETWORK')):
            self.check_failclosed(inspect_captured_pair(atom=atom,cap_by_url=docs,
                source=attrs(atom, docs),at_utc=NOW))
        c=Path(__file__).with_name('cyprus_cap_r8g1_contract.py').read_text('utf-8')
        for prohibited in ('import requests','import httpx','import telegram','send_message(', 'send_photo('):
            self.assertNotIn(prohibited,c)


if __name__=='__main__':
    unittest.main(verbosity=2)
