"""R8-K3/K4 deterministic safety regressions. Synthetic envelopes; original R8-E2
prose only as fixture text, not a live warning. Raw bytes are replayed separately.
K4: decisions use structured CAP fields; free text can only veto (UNKNOWN).
Historical source: MeteoAlarm Cyprus / EMMA 68, 27 Oct 2026, CC BY 4.0.
CAP SHA256 fb9bda2794e5e9509888030ab6023316ebb7d94c121964c98289972635014fcf.
No sockets, providers, Telegram, or production authorization.
"""
from __future__ import annotations
import copy
from datetime import date, datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
import os
import socket
import sys
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from cyprus_cap_r8g1_contract import ATOM, CAP, FEED_URL, FEED_ID
import cyprus_emma_r8i_handoff as H
from post_safety import sanitize_post_text, split_telegram_text

CT, AT = f'{{{CAP}}}', f'{{{ATOM}}}'
URL = 'https://feeds.meteoalarm.org/api/v1/warnings/feeds-cyprus/ffb0ff82-5a6d-4c33-9e8c-2aefb5e43cf6'
NOW = datetime(2026, 10, 27, 17, 9, 10, tzinfo=timezone.utc)
TARGET = date(2026, 10, 28)
BASE = ('<b>Кипр</b>\n⚠️ По прогнозу: сильный ветер\n'
        '🏄 SUP / кайт / сёрф: безопасность не подтверждена\n'
        '✨ VayboMeter: н/д — официальные предупреждения не проверены.\n#Кипр #погода')
MARKER = '⚠️ <b>По данным MeteoAlarm</b>:'
FLAGS = ('official_warnings_checked', 'clean_verified', 'city_cleared',
         'positive_water_guidance_allowed', 'national_inventory_complete')

_REVIEWED_TEXT = {'el-GR': {'headline': 'Καταιγίδα Κίτρινο για την Κύπρο', 'description': 'ΜΕΜΟΝΩΜΕΝΕΣ ΙΣΧΥΡΕΣ ΚΑΤΑΙΓΙΔΕΣ ΑΝΑΜΕΝΕΤΑΙ ΝΑ ΕΠΗΡΕΑΣΟΥΝ ΚΑΤΑ ΔΙΑΣΤΗΜΑΤΑ ΑΡΧΙΚΑ ΤΟ ΔΥΤΙΚΟ ΜΙΣΟ ΤΟΥ ΝΗΣΙΟΥ ΚΑΙ ΑΠΟ ΤΟ ΜΕΣΗΜΕΡΙ ΚΑΙ ΜΕΤΑ ΚΑΙ ΑΛΛΕΣ ΠΕΡΙΟΧΕΣ. Η ΕΝΤΑΣΗ ΤΗΣ ΒΡΟΧΗΣ ΑΝΑΜΕΝΕΤΑΙ ΝΑ ΚΥΜΑΝΘΕΙ ΜΕΤΑΞΥ 35 ΚΑΙ 55 ΧΙΛΙΟΣΤΑ ΑΝΑ ΩΡΑ ΕΝΩ ΠΙΘΑΝΟ ΝΑ ΠΕΣΕΙ ΚΑΙ ΧΑΛΑΖΙ. ΠΑΡΟΔΙΚΑ ΣΤΙΣ ΚΑΤΑΙΓΙΔΕΣ ΟΙ ΡΙΠΕΣ ΤΩΝ ΑΝΕΜΩΝ ΔΥΝΑΤΟ ΝΑ ΦΤΑΝΟΥΝ MEXΡΙ ΚΑΙ ΤΑ 7 ΜΕ 8 ΜΠΟΦΟΡ.', 'instruction': 'Να είσαι ενήμερος για την εκδήλωση Καταιγίδων. Πάρε επιπλέον μέτρα σε εκτεθειμένες περιοχές, όπως βουνά, δάση και πεδιάδες. Είναι δυνατόν να δημιουργηθούν προβλήματα σε εξωτερικές δραστηριότητες.'}, 'en-GB': {'headline': 'Thunderstorm Yellow for Cyprus', 'description': 'ISOLATED HEAVY THUNDERSTORMS ARE EXPECTED TO AFFECT AT TIMES, INITIALLY THE WESTERN HALF OF THE ISLAND AND AFTER MIDDAY OTHER AREAS AS WELL. RAIN RATES ARE EXPECTED TO RANGE BETWEEN 35 AND 55 MILLIMETERS PER HOUR, WHILE HAIL IS ALSO LIKELY. IN THUNDERSTORMS WIND GUST IS LIKELY TO REACH 7 TO 8 BEAUFORT, AT TIMES.', 'instruction': 'BE AWARE that thunderstorms may occur.  Take extra care in exposed areas, like mountains, forest and open terrain. Disruption to outdoor activities is possible.'}}

def add_reviewed_prose(en, el):
    """Attach independently reviewed original text to a synthetic envelope."""
    for info, lang in ((en, 'en-GB'), (el, 'el-GR')):
        for name, value in _REVIEWED_TEXT[lang].items():
            ET.SubElement(info, CT+name).text = value


def _text(node, tag, value):
    ET.SubElement(node, tag).text = value


def _code(node, tag, name, value):
    item = ET.SubElement(node, CT+tag)
    _text(item, CT+'valueName', name)
    _text(item, CT+'value', value)
    return item


def fixture(coloured=True):
    feed = ET.Element(AT+'feed')
    _text(feed, AT+'id', FEED_ID)
    _text(feed, AT+'updated', '2026-10-27T17:05:16Z')
    _text(feed, AT+'title', 'SYNTHETIC R8-K3 TEST ONLY')
    _text(feed, AT+'generator', 'MeteoAlarm Producer Server')
    author = ET.SubElement(feed, AT+'author')
    _text(author, AT+'name', 'meteoalarm.org')
    _text(author, AT+'uri', 'https://meteoalarm.org')
    ET.SubElement(feed, AT+'link', {'rel':'self','href':FEED_URL})
    entry = ET.SubElement(feed, AT+'entry')
    event = 'Thunderstorm Yellow' if coloured else 'Thunderstorm'
    for tag, value in ((AT+'id','urn:synthetic:r8k3'), (CT+'identifier','SYNTHETIC-R8K3'),
                       (CT+'status','Actual'), (CT+'message_type','Alert'), (CT+'event',event),
                       (CT+'severity','Moderate')):
        _text(entry, tag, value)
    ET.SubElement(entry, AT+'link', {'type':'application/cap+xml','href':URL})
    geo = ET.SubElement(entry, CT+'geocode')
    _text(geo, AT+'valueName','EMMA_ID'); _text(geo, AT+'value','CY001')
    cap = ET.Element(CT+'alert')
    for tag,value in (('identifier','SYNTHETIC-R8K3'),('sender','synthetic@example.invalid'),
                      ('sent','2026-10-27T15:46:00+02:00'),('status','Actual'),
                      ('msgType','Alert'),('scope','Public')):
        _text(cap, CT+tag, value)
    for lang, label in (('en-GB',event), ('el-GR','Καταιγίδα Κίτρινο' if coloured else 'Καταιγίδες')):
        info=ET.SubElement(cap,CT+'info')
        for tag,value in (('language',lang),('event',label),('category','Met'),('severity','Moderate'),
                          ('urgency','Future'),('certainty','Likely'),('effective','2026-10-27T15:46:00+02:00'),
                          ('onset','2026-10-28T02:00:00+02:00'),('expires','2026-10-28T17:59:59+02:00')):
            _text(info,CT+tag,value)
        if coloured:
            _code(info,'parameter','awareness_type','3; Thunderstorm')
            _code(info,'parameter','awareness_level','2; yellow; Moderate')
        area=ET.SubElement(info,CT+'area')
        _text(area,CT+'areaDesc','Cyprus'); _code(area,'geocode','EMMA_ID','CY001')
    if coloured:
        add_reviewed_prose(*cap.findall(CT+'info'))
    return feed,cap


def infos(cap):
    return tuple(cap.findall(CT+'info'))


def run(feed, cap, *, base=BASE, enabled=True, outage=False, sanitizer=sanitize_post_text, clock=NOW):
    bodies = {FEED_URL:(ET.tostring(feed, encoding='utf-8'),'application/atom+xml'),
              URL:(ET.tostring(cap, encoding='utf-8'),'text/xml')}
    calls=[]
    def fetch(url):
        calls.append(url)
        if outage:
            raise TimeoutError('SYNTHETIC outage')
        body,mime=bodies[url]
        return dict(url=url,body=body,status=200,content_type=mime,
                    http_date=format_datetime(clock,usegmt=True),received_utc=clock)
    with patch.object(H,'_verified_https_get',fetch):
        decision=H.prepare_registered_emma_warning_for_publisher(
            final_sanitized_text=base,enabled=enabled,at_utc=clock,
            forecast_date=TARGET,sanitizer=sanitizer)
    return decision,calls


class K3Safety(unittest.TestCase):
    def setUp(self):
        for owner,name in ((socket.socket,'connect'),(socket.socket,'connect_ex'),
                           (socket,'getaddrinfo'),(socket,'create_connection')):
            p=patch.object(owner,name,side_effect=AssertionError('NETWORK_FORBIDDEN'))
            p.start();self.addCleanup(p.stop)

    def accept(self, feed, cap):
        d,calls=run(feed,cap)
        self.assertTrue(d.warning_attached,d.reason);self.assertFalse(d.block_publication)
        self.assertEqual(2,len(calls));self.assertEqual(1,d.publisher_text.count(MARKER))
        self.assertIn('Территория по CAP: Cyprus',d.publisher_text)
        self.assertIn('безопасность не подтверждена',d.publisher_text)
        first=H.warning_first_for_delivery(d.publisher_text)
        self.assertTrue(H.warning_overlay_survives_telegram_split(first,split_telegram_text(first)))
        return d

    def reject(self,feed,cap):
        d,_=run(feed,cap)
        self.assertFalse(d.warning_attached,d.reason);self.assertNotIn(MARKER,d.publisher_text)
        self.assertIsNone(d.expires_utc)

    def test_reviewed_pair_and_whitespace_and_order(self):
        f,c=fixture();self.accept(f,c)
        for info in infos(c):
            for field in _REVIEWED_TEXT['en-GB']:
                v=info.find(CT+field);v.text='  \n'+v.text+'  \n'
        en,el=infos(c);c.remove(en);c.append(en);self.accept(f,c)

    def test_eight_false_accepts(self):
        cases=((0,'headline','CANCELLED Thunderstorm Yellow for Cyprus'),
               (0,'description','NO THUNDERSTORMS ARE EXPECTED'),
               (0,'instruction','No action needed.'),
               (1,'headline','ΑΚΥΡΩΣΗ'),(1,'description','ΔΕΝ ΑΝΑΜΕΝΟΝΤΑΙ ΚΑΤΑΙΓΙΔΕΣ'),
               (1,'instruction','Δεν υπάρχει κίνδυνος καταιγίδων.'),
               (0,'area/areaDesc','Paphos only'),(1,'area/areaDesc','Paphos only'))
        for i,path,value in cases:
            with self.subTest(language=i,field=path):
                f,c=fixture();infos(c)[i].find('/'.join(CT+x for x in path.split('/'))).text=value
                self.reject(f,c)

    def test_unseen_missing_duplicate_and_nested_prose(self):
        # K4: unseen or absent prose is not evidence either way -> structured contract decides.
        for mode in ('new','missing_one','missing_all'):
            with self.subTest(mode=mode):
                f,c=fixture();en,el=infos(c)
                if mode=='new':en.find(CT+'description').text+=' Revised.'
                if mode=='missing_one':el.remove(el.find(CT+'instruction'))
                if mode=='missing_all':
                    for i in (en,el):
                        for field in _REVIEWED_TEXT['en-GB']:i.remove(i.find(CT+field))
                self.accept(f,c)
        for mode in ('duplicate','nested','both_paphos','geometry_both'):
            with self.subTest(mode=mode):
                f,c=fixture();en,el=infos(c)
                if mode=='new':en.find(CT+'description').text+=' Revised.'
                if mode=='missing_one':el.remove(el.find(CT+'instruction'))
                if mode=='missing_all':
                    for i in (en,el):
                        for field in _REVIEWED_TEXT['en-GB']:i.remove(i.find(CT+field))
                if mode=='duplicate':el.append(copy.deepcopy(el.find(CT+'headline')))
                if mode=='nested':ET.SubElement(en.find(CT+'instruction'),CT+'other').text='cancel'
                if mode=='both_paphos':
                    for i in (en,el):i.find(CT+'area/'+CT+'areaDesc').text='Paphos only'
                if mode=='geometry_both':
                    for i in (en,el):_text(i.find(CT+'area'),CT+'polygon','35,33 35,34 36,34 35,33')
                self.reject(f,c)

    def test_k01_eventcode(self):
        f,c=fixture();en,el=infos(c)
        _code(en,'eventCode','EMMA_CODE','3; Thunderstorm');_code(el,'eventCode','EMMA_CODE','3; Thunderstorm')
        self.accept(f,c);el.find(CT+'eventCode/'+CT+'value').text='4; Wind';self.reject(f,c)

    def test_k02_awareness_one_side(self):
        f,c=fixture();infos(c)[1].find(CT+'parameter/'+CT+'value').text='1; Wind';self.reject(f,c)

    def test_k03_geography_only_greek_geometry(self):
        f,c=fixture();_text(infos(c)[1].find(CT+'area'),CT+'polygon','35,33 35,34 36,34 35,33');self.reject(f,c)

    def test_k04_time(self):
        f,c=fixture();infos(c)[1].find(CT+'onset').text='2026-10-28T03:00:00+02:00';self.reject(f,c)

    def test_k05_greek_yellow(self):
        f,c=fixture();infos(c)[1].find(CT+'event').text='Καταιγίδα Πορτοκαλί';self.reject(f,c)

    def test_k06_k07_k11_exact_bare_label(self):
        f,c=fixture(False);self.accept(f,c)
        for label in ('Δεν αναμένονται καταιγίδες','Καταιγίδες Κίτρινο','Καταιγίδες και άνεμος','Ισχυρός άνεμος'):
            with self.subTest(label=label):
                f,c=fixture(False);infos(c)[1].find(CT+'event').text=label;self.reject(f,c)

    def test_k08_atom_event(self):
        f,c=fixture();f.find(AT+'entry/'+CT+'event').text='Strong Wind Yellow';self.reject(f,c)

    def test_k09_category_urgency(self):
        f,c=fixture();infos(c)[1].find(CT+'urgency').text='Immediate';self.reject(f,c)

    def test_k10_second_language(self):
        f,c=fixture();infos(c)[1].find(CT+'language').text='tr-TR';self.reject(f,c)

    def test_k12_yellow_awareness(self):
        f,c=fixture()
        for i in infos(c):i.find(CT+'parameter/'+CT+'value').text='1; Wind'
        self.reject(f,c)

    def test_lifecycle_and_single_prose(self):
        for kind in ('Update','Cancel'):
            f,c=fixture();c.find(CT+'msgType').text=kind;f.find(AT+'entry/'+CT+'message_type').text=kind;self.reject(f,c)
        f,c=fixture(False);en,el=infos(c);c.remove(el);self.accept(f,c)
        _text(en,CT+'headline','CANCELLED Thunderstorm');self.reject(f,c)

    def test_off_and_marine(self):
        f,c=fixture();unsafe=BASE.replace('безопасность не подтверждена','✅ можно кататься')
        d,calls=run(f,c,base=unsafe,enabled=False)
        self.assertEqual(unsafe,d.publisher_text);self.assertFalse(d.warning_attached);self.assertFalse(d.block_publication);self.assertEqual([],calls)
        d,_=run(f,c,base=unsafe);self.assertTrue(d.block_publication);self.assertFalse(d.warning_attached)
        infos(c)[1].find(CT+'headline').text='ΑΚΥΡΩΣΗ'
        d,_=run(f,c,base=unsafe);self.assertTrue(d.block_publication);self.assertFalse(d.warning_attached)
        d,_=run(f,c,base=unsafe,outage=True);self.assertFalse(d.block_publication);self.assertIn('безопасность не подтверждена',d.publisher_text);self.assertNotIn('можно кататься',d.publisher_text)

    def test_editorial_projection_real_sanitizer_and_splitter(self):
        text=('<b>🌅 Кипр завтра (28.10.2026)</b>\n'
              '✨ VayboMeter завтра: 8.6/10 — комфортно для обычных дел и прогулок.\n'
              '🧭 Главное завтра: заметен контраст побережья, центра острова и Тродоса.\n'
              '💬 Настрой на завтра: завтра погода держится ровно, без заметных перепадов.\n'
              'Лимассол: 29/22 °C • ясно • 💨 4 м/с\n'
              '⚠️ По прогнозу: сильный ветер\n'
              '💚 В плюсе: порядок, прогулки, мягкий режим.\n'
              '✅ План завтра: обычные дела и прогулки, с короткой проверкой ветра и солнца утром.\n'
              '#Кипр #погода')
        with patch.dict(os.environ,{'FORMAT_V2':'1','FORMAT_V2_MORNING_SPACING':'1'}):
            f,c=fixture();base=sanitize_post_text(text).text
            d,_=run(f,c,base=base);self.assertTrue(d.warning_attached,d.reason)
            self.assertFalse(d.block_publication);self.assertIn('VayboMeter: н/д',d.publisher_text)
            for bad in ('8.6/10','комфортно','держится ровно','обычные дела и прогулки','В плюсе'):
                self.assertNotIn(bad,d.publisher_text)
            for fact in ('Лимассол: 29/22 °C • ясно • 💨 4 м/с','⚠️ По прогнозу: сильный ветер','#Кипр #погода'):
                self.assertIn(fact,d.publisher_text)
            self.assertIn('Показатели прогноза модели не исключают',d.publisher_text)
            first=H.warning_first_for_delivery(d.publisher_text)
            self.assertTrue(H.warning_overlay_survives_telegram_split(first,split_telegram_text(first)))
            self.assertTrue(H.warning_overlay_fresh_for_delivery(d,NOW))
            self.assertFalse(H.warning_overlay_fresh_for_delivery(d,d.expires_utc-timedelta(seconds=30)))
            infos(c)[1].find(CT+'headline').text='ΑΚΥΡΩΣΗ'
            u,_=run(f,c,base=base);self.assertFalse(u.warning_attached);self.assertFalse(u.block_publication)
            self.assertNotIn('8.6/10',u.publisher_text);self.assertNotIn('держится ровно',u.publisher_text)
            self.assertIn('требуют проверки',u.publisher_text)

    def test_unknown_editorial_and_sanitizer_fail_closed(self):
        # K4: reassuring advice is removed instead of blocking the whole post.
        f,c=fixture();base=BASE.replace('#Кипр','✅ План завтра: можно гулять везде, кроме участка с сильным ветром.\n#Кипр')
        d,_=run(f,c,base=base);self.assertFalse(d.block_publication);self.assertTrue(d.warning_attached,d.reason)
        self.assertNotIn('можно гулять',d.publisher_text);self.assertIn('⚠️ По прогнозу: сильный ветер',d.publisher_text)
        high=BASE.replace('н/д — официальные предупреждения не проверены.','8.6/10')
        class Removed:
            text=''
        d,_=run(f,c,base=high,sanitizer=lambda _:Removed());self.assertTrue(d.block_publication);self.assertFalse(d.warning_attached)
        infos(c)[1].find(CT+'headline').text='ΑΚΥΡΩΣΗ'
        d,_=run(f,c,base=base);self.assertFalse(d.block_publication);self.assertFalse(d.warning_attached)
        self.assertNotIn('можно гулять',d.publisher_text);self.assertNotIn(MARKER,d.publisher_text)


_AQI_CAUTION = ('✅ План завтра: утром сверить прогноз воздуха и при повышенных значениях '
                'выбрать спокойную нагрузку.')  # format_v2.py deterministic template


def recolour(f, c, *, en_event, el_event, level, severity, kind='3; Thunderstorm'):
    f.find(AT+'entry/'+CT+'event').text = en_event
    f.find(AT+'entry/'+CT+'severity').text = severity
    for info in infos(c):
        lang = info.find(CT+'language').text
        info.find(CT+'event').text = en_event if lang == 'en-GB' else el_event
        info.find(CT+'severity').text = severity
        for prm in info.findall(CT+'parameter'):
            name = prm.find(CT+'valueName').text
            prm.find(CT+'value').text = kind if name == 'awareness_type' else level
        for field in ('headline', 'description', 'instruction'):  # keep prose colour consistent
            node = info.find(CT+field)
            if node is not None:
                node.text = node.text.replace('Yellow', en_event.rpartition(' ')[2]).replace(
                    'Κίτρινο', el_event.rpartition(' ')[2] if ' ' in el_event else 'Κίτρινο')


class K4StructuredContract(K3Safety):
    """Independent K4 counterexamples: no prose hashes, prose only vetoes."""

    def test_consistent_new_prose_is_accepted(self):
        f, c = fixture()
        for info in infos(c):
            d = info.find(CT+'description'); d.text = d.text.replace('35', '36', 1)
            h = info.find(CT+'headline'); h.text += ' (update 2)'
        self.accept(f, c)

    def test_retraction_markers_in_any_field_and_language_are_unknown(self):
        phrases = {'en-GB': ('Warning cancelled.', 'The warning has been lifted.', 'No longer in effect.',
                             'All clear for Cyprus.', 'Thunderstorms are not expected.',
                             'No thunderstorms expected.', 'NO RISK TODAY.'),
                   'el-GR': ('Άρση προειδοποίησης.', 'Η προειδοποίηση ακυρώνεται.',
                             'Δεν αναμένονται καταιγίδες.', 'Δεν υπάρχει κίνδυνος.', 'Παύει να ισχύει.')}
        for lang, values in phrases.items():
            for field in ('headline', 'description', 'instruction'):
                for value in values:
                    with self.subTest(lang=lang, field=field, value=value):
                        f, c = fixture()
                        info = next(i for i in infos(c) if i.find(CT+'language').text == lang)
                        info.find(CT+field).text = value
                        self.reject(f, c)
        for lang, value in (('en-GB', 'Thunderstorm Yellow cancelled'), ('el-GR', 'Καταιγίδα Κίτρινο ακυρώθηκε')):
            with self.subTest(event=value):
                f, c = fixture()
                next(i for i in infos(c) if i.find(CT+'language').text == lang).find(CT+'event').text = value
                self.reject(f, c)

    def test_ordinary_cautionary_prose_is_not_a_veto(self):
        f, c = fixture(); en, el = infos(c)
        en.find(CT+'instruction').text = 'Do not shelter under trees. Avoid the open sea during thunderstorms.'
        el.find(CT+'instruction').text = 'Μην κολυμπάτε κατά τη διάρκεια καταιγίδας.'
        self.accept(f, c)

    def test_coloured_contract_is_structural_and_self_consistent(self):
        f, c = fixture()
        recolour(f, c, en_event='Thunderstorm Orange', el_event='Καταιγίδα Πορτοκαλί',
                 level='3; orange; Severe', severity='Severe')
        d = self.accept(f, c); self.assertIn('Уровень: серьёзная', d.publisher_text)
        bad = (dict(en_event='Thunderstorm Orange', el_event='Καταιγίδα Πορτοκαλί', level='2; yellow; Moderate', severity='Severe'),
               dict(en_event='Thunderstorm Orange', el_event='Καταιγίδα Πορτοκαλί', level='3; orange; Moderate', severity='Severe'),
               dict(en_event='Thunderstorm Yellow', el_event='Καταιγίδα Κίτρινο', level='2; yellow; Moderate', severity='Moderate', kind='1; Wind'),
               dict(en_event='Thunderstorm Green', el_event='Καταιγίδα Πράσινο', level='1; green; Minor', severity='Minor'),
               dict(en_event='Avalanches Orange', el_event='Χιονοστιβάδες Πορτοκαλί', level='3; orange; Severe', severity='Severe', kind='9; Avalanches'),
               dict(en_event='Thunderstorm Yellow', el_event='Καταιγίδα', level='2; yellow; Moderate', severity='Moderate'),
               dict(en_event='Thunderstorm Yellow', el_event='Καταιγίδα Κόκκινο', level='2; yellow; Moderate', severity='Moderate'))
        for kw in bad:
            with self.subTest(**kw):
                f, c = fixture(); recolour(f, c, **kw); self.reject(f, c)

    def test_prose_colour_must_match_structured_colour(self):
        f, c = fixture()
        recolour(f, c, en_event='Thunderstorm Orange', el_event='Καταιγίδα Πορτοκαλί',
                 level='3; orange; Severe', severity='Severe')
        infos(c)[0].find(CT+'headline').text = 'Thunderstorm Yellow for Cyprus'
        self.reject(f, c)
        f, c = fixture(False); en, el = infos(c); c.remove(el); self.accept(f, c)
        _text(en, CT+'headline', 'Thunderstorm Yellow for Cyprus'); self.reject(f, c)

    def test_sentence_initial_negation_is_a_veto(self):
        for lang, value in (('en-GB', 'NO ISOLATED HEAVY THUNDERSTORMS ARE EXPECTED TODAY.'),
                            ('en-GB', 'Rain is likely. None of the thunderstorms will reach the coast.')):
            with self.subTest(value=value):
                f, c = fixture()
                next(i for i in infos(c) if i.find(CT+'language').text == lang).find(CT+'description').text = value
                self.reject(f, c)

    def test_level_colour_alone_must_match_event_colour(self):
        f, c = fixture()
        recolour(f, c, en_event='Thunderstorm Orange', el_event='Καταιγίδα Πορτοκαλί',
                 level='2; yellow; Severe', severity='Severe')
        self.reject(f, c)

    def test_projection_sanitizer_failure_blocks_instead_of_publishing_unprojected(self):
        drops_caution = lambda text: type('R', (), {'text': '\n'.join(
            l for l in text.splitlines() if not l.startswith('⚠️ Показатели прогноза модели'))})()
        f, c = fixture(); base = self._forecast('💬 Настрой на завтра: завтра погода держится ровно, без заметных перепадов.')
        d, _ = run(f, c, base=base, sanitizer=drops_caution)
        self.assertTrue(d.block_publication); self.assertFalse(d.warning_attached)
        self.assertEqual('WARNING_CONTEXT_SANITIZER_REJECTED', d.reason)

    def test_h5_coloured_taxonomy_equals_h6_taxonomy(self):
        import cyprus_emma_preview_r8h5 as H5, cyprus_emma_pipeline_r8h6 as H6
        self.assertEqual(H5._COLOURED_TAXONOMY, H6._ALLOWED_EVENT_NAMES)

    def _forecast(self, *extra):
        return sanitize_post_text('\n'.join((
            '<b>🌅 Кипр завтра (28.10.2026)</b>',
            '✨ VayboMeter завтра: 7.4/10 — с оговорками; жара и порывы у моря.',
            'Лимассол: 29/22 °C • ясно • 💨 4 м/с', *extra,
            '🏄 SUP / кайт / сёрф: безопасность не подтверждена', '#Кипр #погода'))).text

    def test_cautious_format_v2_advice_never_blocks(self):
        base = self._forecast(_AQI_CAUTION, '⚠️ Нюанс: чувствительным людям лучше сократить интенсивную активность на улице.')
        f, c = fixture(); d, _ = run(f, c, base=base)
        self.assertTrue(d.warning_attached, d.reason); self.assertFalse(d.block_publication)
        for line in (_AQI_CAUTION, 'Лимассол: 29/22 °C • ясно • 💨 4 м/с', 'безопасность не подтверждена'):
            self.assertIn(line, d.publisher_text)
        self.assertNotIn('7.4/10', d.publisher_text)
        empty = copy.deepcopy(f); empty.remove(empty.find(AT+'entry'))
        u, calls = run(empty, c, base=base)
        self.assertFalse(u.block_publication); self.assertFalse(u.warning_attached); self.assertEqual(1, len(calls))
        self.assertIn(_AQI_CAUTION, u.publisher_text)
        o, _ = run(f, c, base=base, outage=True)
        self.assertFalse(o.block_publication); self.assertIn(_AQI_CAUTION, o.publisher_text)
        self.assertIn('не означает отсутствия опасности', o.publisher_text)

    def test_positive_editorial_removed_facts_kept(self):
        base = self._forecast('Сегодня прекрасная погода для отдыха на улице.',
                              '🌊 Море: вода 27°C — идеально для купания.',
                              '💬 Настрой на завтра: завтрашний день выглядит предсказуемым — резких сюрпризов погода не обещает.',
                              '✅ Общий фон: стабильный — день можно планировать заранее.')
        f, c = fixture(); d, _ = run(f, c, base=base)
        self.assertTrue(d.warning_attached, d.reason); self.assertFalse(d.block_publication)
        for bad in ('прекрасная', 'идеально', 'предсказуемым', 'планировать заранее', '7.4/10'):
            self.assertNotIn(bad, d.publisher_text)
        for fact in ('🌊 Море: вода 27°C.', 'Лимассол: 29/22 °C • ясно • 💨 4 м/с', 'VayboMeter: н/д'):
            self.assertIn(fact, d.publisher_text)
        first = H.warning_first_for_delivery(d.publisher_text)
        self.assertTrue(H.warning_overlay_survives_telegram_split(first, split_telegram_text(first)))

    def test_format_v2_marine_cautions_do_not_block_but_positive_marine_does(self):
        cautious = ('🧜\u200d♂️ <b>SUP</b>',
                    '• Пафос — 🧜\u200d♂️ SUP лучше отложить: порывы до 15 м/с (западный ветер, вдоль берега).',
                    '🏄 <b>Серф</b>',
                    '• Пафос — 🏄 Серф: данных для уверенной оценки недостаточно; проверить спот перед выездом.')
        f, c = fixture(); d, _ = run(f, c, base=self._forecast(*cautious))
        self.assertTrue(d.warning_attached, d.reason); self.assertFalse(d.block_publication)
        for line in cautious: self.assertIn(line.split(' — ')[-1][:30], d.publisher_text)
        for positive in ('• Ларнака — 🧜\u200d♂️ SUP: только опытным и короткая сессия • порывы до 12 м/с (южный ветер, к берегу).',
                         '• Пафос — 🧜\u200d♂️ Отлично: Кайт/Винг/Винд (западный ветер, вдоль берега)',
                         '• Лимассол — 🏄 Серф: есть рабочие окна по волне; проверить конкретный спот.'):
            with self.subTest(positive=positive):
                f, c = fixture(); d, _ = run(f, c, base=self._forecast('🏄 <b>Серф</b>', positive))
                self.assertTrue(d.block_publication); self.assertFalse(d.warning_attached)
                self.assertEqual('ACTIVE_WARNING_CONFLICTS_WITH_MARINE_GUIDANCE', d.reason)

    def test_off_path_is_exact_for_editorial_text(self):
        base = self._forecast('Сегодня прекрасная погода для отдыха на улице.', _AQI_CAUTION)
        f, c = fixture(); d, calls = run(f, c, base=base, enabled=False)
        self.assertEqual(base, d.publisher_text); self.assertEqual([], calls)
        self.assertFalse(d.block_publication); self.assertFalse(d.warning_attached)

    def test_no_safety_flag_or_all_clear_on_any_path(self):
        for mode in ('accept', 'retracted', 'outage'):
            with self.subTest(mode=mode):
                f, c = fixture()
                if mode == 'retracted': infos(c)[1].find(CT+'headline').text = 'Άρση προειδοποίησης.'
                d, _ = run(f, c, outage=(mode == 'outage'))
                for bad in ('предупреждений нет', 'нет предупреждений', 'официальные предупреждения проверены'):
                    self.assertNotIn(bad, d.publisher_text)


class K5TargetedRegressions(unittest.TestCase):
    """Contrasts for the nine independent K4 counterexamples (synthetic)."""

    def decision(self, *lines, **kw):
        f, c = fixture()
        base = sanitize_post_text(BASE.replace('#Кипр', '\n'.join(lines) + '\n#Кипр')).text
        return run(f, c, base=base, **kw)[0]

    def test_marine_heading_and_inline_go_contrasts(self):
        bad = (
            ('🏄 <b>Серф</b>', '• Пафос — есть рабочие окна по волне; проверить конкретный спот.'),
            ('🏄 <b>Серф</b>', '• Пафос — катание рекомендуется.'),
            ('🏄 SUP: только опытным; порывы до 12 м/с',),
            ('🏄 SUP: сильный ветер; катание рекомендуется.',),
            ('🏄 <b>Серф</b>', '• Пафос — данных недостаточно.', '', '• Ларнака — катание рекомендуется.'),
            ('🏄 SUP: не рекомендуется; катание рекомендуется.',),
            ('✅ <b>SUP</b>',),
            ('🏄 SUP: безопасность не подтверждена', 'катание рекомендуется.'),
            ('🏄 <b>Серф</b>', 'Пафос: катание рекомендуется.'),
        )
        for lines in bad:
            with self.subTest(lines=lines):
                d = self.decision(*lines)
                self.assertTrue(d.block_publication)
                self.assertFalse(d.warning_attached)
        for lines in (
            ('🏄 <b>Серф</b>', '• Пафос — данных для уверенной оценки недостаточно; проверить конкретный спот.'),
            ('🏄 SUP: лучше отложить; порывы до 12 м/с',),
            ('🏄 SUP: сильный ветер; катание не рекомендуется.',),
            ('🏄 SUP: безопасность не подтверждена.',),
            ('🏄 SUP: небезопасно; не выходить на воду.',),
            ('🏄 SUP: рекомендация — не выходить на воду.',),
            ('🏄 <b>Серф</b>',),
        ):
            with self.subTest(lines=lines):
                d = self.decision(*lines)
                self.assertTrue(d.warning_attached, d.reason)
                self.assertFalse(d.block_publication)

    def test_awareness_code_and_greek_hazard_contrasts(self):
        for kind, level, greek in (
            ('999; Thunderstorm', '2; yellow; Moderate', 'Καταιγίδα Κίτρινο'),
            ('1; Thunderstorm', '2; yellow; Moderate', 'Καταιγίδα Κίτρινο'),
            ('3; Thunderstorm', '999; yellow; Moderate', 'Καταιγίδα Κίτρινο'),
            ('3; Thunderstorm', '3; yellow; Moderate', 'Καταιγίδα Κίτρινο'),
            ('3; Thunderstorm', '2; yellow; Moderate', 'Ισχυρός άνεμος Κίτρινο'),
            ('3; Thunderstorm', '2; yellow; Moderate', 'Καταιγίδα και άνεμος Κίτρινο'),
        ):
            with self.subTest(kind=kind, level=level, greek=greek):
                f, c = fixture()
                recolour(f, c, en_event='Thunderstorm Yellow', el_event=greek,
                         kind=kind, level=level, severity='Moderate')
                d, _ = run(f, c)
                self.assertFalse(d.warning_attached)
                self.assertNotIn(MARKER, d.publisher_text)
        f, c = fixture(); d, _ = run(f, c)
        self.assertTrue(d.warning_attached)
        # Same valid structure with new prose remains supported.
        infos(c)[0].find(CT+'description').text += ' Take extra care.'
        d, _ = run(f, c); self.assertTrue(d.warning_attached)

    def test_editorial_projection_preserves_mixed_forecast(self):
        d = self.decision('Погода располагает к прогулкам на открытом воздухе.',
                          '⚠️ Прекрасная погода утром, но вечером гроза и порывы до 15 м/с.',
                          '✅ План завтра: утром сверить прогноз воздуха и при повышенных значениях выбрать спокойную нагрузку.')
        self.assertTrue(d.warning_attached, d.reason)
        self.assertNotIn('Погода располагает', d.publisher_text)
        self.assertNotIn('Прекрасная погода', d.publisher_text)
        self.assertIn('⚠️ вечером гроза и порывы до 15 м/с.', d.publisher_text)
        self.assertIn('утром сверить прогноз воздуха', d.publisher_text)
        # Non-numeric warning clauses must be preserved too.
        d = self.decision('Прекрасная погода утром, но вечером возможна гроза.')
        self.assertTrue(d.warning_attached, d.reason)
        self.assertIn('вечером возможна гроза', d.publisher_text)
        # Inseparable facts stay in diagnostic text; no publication is allowed.
        line = '⚠️ Прекрасная погода с порывами до 15 м/с.'
        d = self.decision(line)
        self.assertTrue(d.block_publication)
        self.assertIn(line, d.publisher_text)
        self.assertFalse(d.warning_attached)

    def test_score_tail_weather_facts_survive(self):
        d = self.decision('✨ VayboMeter завтра: 8.0/10 — прекрасная погода утром; вечером гроза и порывы до 15 м/с.')
        self.assertTrue(d.warning_attached, d.reason)
        self.assertNotIn('8.0/10', d.publisher_text)
        self.assertNotIn('прекрасная погода', d.publisher_text)
        self.assertIn('вечером гроза и порывы до 15 м/с', d.publisher_text)

    def test_outage_new_marine_conflict_neutralized_before_publish(self):
        d = self.decision('🏄 SUP: только опытным; порывы до 12 м/с', outage=True)
        self.assertFalse(d.block_publication)
        self.assertFalse(d.warning_attached)
        self.assertEqual('SOURCE_UNAVAILABLE', d.reason)
        expected = sanitize_post_text(BASE.replace('#Кипр',
            '🏄 SUP: безопасность не подтверждена; порывы до 12 м/с\n#Кипр')).text
        self.assertEqual(expected, d.publisher_text)
        self.assertNotIn('только опытным', d.publisher_text)


class K6OutageReliability(unittest.TestCase):
    """K6 unified outage contract; confirmed-alert veto remains strict."""

    def decision(self, *lines, outage=False, enabled=True):
        f, c = fixture()
        base = sanitize_post_text(BASE.replace('#Кипр', '\n'.join(lines) + '\n#Кипр')).text
        return run(f, c, base=base, outage=outage, enabled=enabled)[0], base

    def test_outage_cautious_weather_unchanged(self):
        for marine in ('🏄 SUP лучше отложить: порывы до 15 м/с',
                       '🏄 SUP: не рекомендуется',
                       '🏄 SUP: опасно — ветер 12 м/с, хорошая видимость'):
            with self.subTest(marine=marine):
                d, base = self.decision(marine, '🏙 Лимассол: хорошая погода',
                                        '🌡 Вода 24 °C — отлично', outage=True)
                self.assertFalse(d.block_publication)
                self.assertFalse(d.warning_attached)
                self.assertEqual('SOURCE_UNAVAILABLE', d.reason)
                self.assertEqual(base, d.publisher_text)

    def test_outage_ignores_unusable_confirmed_alert_projection(self):
        line = '🏙 Лимассол: хорошая погода при температуре 24 °C.'
        outage, base = self.decision(line, outage=True)
        self.assertFalse(outage.block_publication)
        self.assertFalse(outage.warning_attached)
        self.assertEqual(base, outage.publisher_text)
        active, _ = self.decision(line)
        self.assertTrue(active.block_publication)
        self.assertIn(line, active.publisher_text)

    def test_outage_experience_is_neutralized_without_fact_loss(self):
        for prefix, suffix in (
                ('🏄 SUP: только опытным', '; порывы до 12 м/с'),
                ('• Ларнака — 🧜‍♂️ SUP: только опытным и короткая сессия',
                 ' • порывы до 12 м/с (южный ветер, к берегу).')):
            with self.subTest(prefix=prefix):
                d, base = self.decision(prefix + suffix, outage=True)
                self.assertFalse(d.block_publication)
                self.assertFalse(d.warning_attached)
                self.assertIn(prefix.split(':')[0] + ': безопасность не подтверждена' + suffix,
                              d.publisher_text)
                self.assertNotIn('только опытным', d.publisher_text)
                self.assertNotIn('короткая сессия', d.publisher_text)
                active, _ = self.decision(prefix + suffix)
                self.assertTrue(active.block_publication)
                self.assertFalse(active.warning_attached)
                off, off_base = self.decision(prefix + suffix, enabled=False)
                self.assertEqual(off_base, off.publisher_text)

    def test_outage_unclear_new_go_and_heading_remain_vetoed(self):
        for lines in (
            ('🏄 SUP: только опытным при грозе в 18:00',),
            ('🏄 SUP: сильный ветер; катание рекомендуется.',),
            ('🏄 SUP: опасно, хорошая видимость; катание рекомендуется.',),
            ('🏄 <b>Серф</b>', '• Пафос — катание рекомендуется.'),
            ('🏄 <b>Серф</b>', '• Пафос — данных недостаточно.', '',
             '• Ларнака — есть рабочие окна по волне.'),
            ('✅ <b>SUP</b>', '• Пафос — катание рекомендуется.'),
        ):
            with self.subTest(lines=lines):
                d, _ = self.decision(*lines, outage=True)
                self.assertTrue(d.block_publication)
                self.assertFalse(d.warning_attached)
                for line in lines:
                    self.assertIn(line, d.publisher_text)
        d, _ = self.decision('🏄 <b>Серф</b>', '• Пафос — данных недостаточно.', outage=True)
        self.assertFalse(d.block_publication)

    def test_confirmed_city_prefix_is_not_a_fact_but_location_survives(self):
        d, _ = self.decision('🏙 Лимассол: хорошая погода',
                             '⚠️ Пафос: прекрасная погода, но вечером гроза и порывы до 15 м/с.')
        self.assertTrue(d.warning_attached, d.reason)
        self.assertFalse(d.block_publication)
        self.assertNotIn('хорошая погода', d.publisher_text)
        self.assertIn('⚠️ Пафос: вечером гроза и порывы до 15 м/с.', d.publisher_text)
        for line in ('⚠️ Лимассол: прекрасная погода с порывами до 15 м/с.',
                     'Прекрасная погода в Лимассоле, но вечером гроза.'):
            with self.subTest(line=line):
                d, _ = self.decision(line)
                self.assertTrue(d.block_publication)
                self.assertIn(line, d.publisher_text)

    def test_confirmed_score_tail_removes_ordinary_not_hazard(self):
        for tail in ('обычный день.', 'обычный день, но вечером гроза и порывы до 15 м/с.'):
            with self.subTest(tail=tail):
                d, _ = self.decision('✨ VayboMeter завтра: 6.9/10 — ' + tail)
                self.assertTrue(d.warning_attached, d.reason)
                self.assertNotIn('обычный день', d.publisher_text)
                self.assertNotIn('6.9/10', d.publisher_text)
                if 'гроза' in tail:
                    self.assertIn('вечером гроза и порывы до 15 м/с.', d.publisher_text)


class K7TargetedRepair(unittest.TestCase):
    """Reported FORMAT_V2 output line and isolated structured CAP contrasts.

    These are NOT a substitute for the five actual FORMAT_V2 render fixtures.
    """

    def test_reported_surf_windows_outage_preserves_forecast(self):
        # Exact rendered line quoted in independent K6 report. The enclosing
        # forecast is synthetic; the real renderer is tested at full checkout.
        line = '🏄 Серф: есть рабочие окна по волне; проверить конкретный спот.'
        expected = '🏄 Серф: безопасность не подтверждена; проверить конкретный спот.'
        # Both the ungrouped output and the city-prefixed grouped output
        # are asserted in tools/test_format_v2_cy.py at PR #117 exact head.
        for prefix in ('', '• Пафос — '):
            with self.subTest(prefix=prefix):
                extra = ('Пафос: 28/22 °C • ясно • 🌊 26 • 1.2 м\n' +
                         prefix + line +
                         '\n🏄 SUP лучше отложить: порывы до 15 м/с (западный ветер, вдоль берега).')
                base = sanitize_post_text(BASE.replace('#Кипр', extra + '\n#Кипр')).text
                f, c = fixture()
                d, _ = run(f, c, base=base, outage=True)
                self.assertFalse(d.block_publication, d.reason)
                self.assertFalse(d.warning_attached)
                self.assertEqual('SOURCE_UNAVAILABLE', d.reason)
                self.assertEqual(base.replace(prefix + line, prefix + expected), d.publisher_text)
                self.assertNotIn('рабочие окна', d.publisher_text)
                active, _ = run(f, c, base=base)
                self.assertTrue(active.block_publication)
                self.assertFalse(active.warning_attached)
                off, calls = run(f, c, base=base, enabled=False)
                self.assertFalse(off.block_publication)
                self.assertEqual(base, off.publisher_text)
                self.assertEqual([], calls)

    def test_inseparable_surf_windows_still_vetoed(self):
        for line in (
                '🏄 Серф: есть рабочие окна по волне при грозе в 18:00.',
                '🏄 Серф: есть рабочие окна по волне; проверить конкретный спот. Порывы до 15 м/с.',
                '🏄 Серф: есть рабочие окна по волне; проверить конкретный спот; катание рекомендуется.'):
            with self.subTest(line=line):
                base = sanitize_post_text(BASE.replace('#Кипр', line + '\n#Кипр')).text
                f, c = fixture(); d, _ = run(f, c, base=base, outage=True)
                self.assertTrue(d.block_publication)
                self.assertFalse(d.warning_attached)
                self.assertIn(line, d.publisher_text)

    def test_awareness_level_colour_word_independent(self):
        import cyprus_emma_preview_r8h5 as P
        f, c = fixture()
        self.assertTrue(P._coloured_event_contract(infos(c)[0]))
        recolour(f, c, en_event='Thunderstorm Yellow', el_event='Καταιγίδα Κίτρινο',
                 level='2; orange; Moderate', severity='Moderate')
        self.assertFalse(P._coloured_event_contract(infos(c)[0]))
        d, _ = run(f, c)
        self.assertFalse(d.warning_attached)
        self.assertNotIn(MARKER, d.publisher_text)

    def test_cap_severity_independent_of_level(self):
        import cyprus_emma_preview_r8h5 as P
        f, c = fixture()
        self.assertTrue(P._coloured_event_contract(infos(c)[0]))
        recolour(f, c, en_event='Thunderstorm Yellow', el_event='Καταιγίδα Κίτρινο',
                 level='2; yellow; Moderate', severity='Severe')
        self.assertFalse(P._coloured_event_contract(infos(c)[0]))
        d, _ = run(f, c)
        self.assertFalse(d.warning_attached)
        self.assertNotIn(MARKER, d.publisher_text)

    def test_single_info_with_awareness_not_legacy(self):
        import cyprus_emma_preview_r8h5 as P
        f, c = fixture(False)
        en, el = infos(c); c.remove(el)
        self.assertIs(en, P._select_display_info(c))
        d, _ = run(f, c); self.assertTrue(d.warning_attached, d.reason)
        # Same legacy event; only add codes. This isolates the single-info guard.
        _code(en, 'parameter', 'awareness_type', '3; Thunderstorm')
        _code(en, 'parameter', 'awareness_level', '2; yellow; Moderate')
        self.assertIsNone(P._select_display_info(c))
        d, _ = run(f, c)
        self.assertFalse(d.warning_attached)
        self.assertNotIn(MARKER, d.publisher_text)
        f, c = fixture(); en, el = infos(c); c.remove(el)
        self.assertIsNone(P._select_display_info(c))
        d, _ = run(f, c)
        self.assertFalse(d.warning_attached)
        self.assertNotIn(MARKER, d.publisher_text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
