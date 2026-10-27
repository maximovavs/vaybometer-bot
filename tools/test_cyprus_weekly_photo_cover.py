#!/usr/bin/env python3
"""Offline Cyprus weekly photo R2 acceptance; no provider/Telegram calls."""
from __future__ import annotations
import ast
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import date, timedelta
import hashlib
import importlib
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType, SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import weekly_photo_cover_cy as photo
import weekly_cover as wrapper

WEEK = date(2026, 10, 26)


def valid_weekly_wmo(code: int = 1) -> dict:
    from settings_cy import MARINE_CITIES, INLAND_CITIES
    dates = [(WEEK + timedelta(days=i)).isoformat() for i in range(7)]
    return {city: {
        "daily": {"time": dates[:], "weather_code": [code] * 7,
                  "precipitation_probability_max": [0] * 7,
                  "precipitation_sum": [0.0] * 7,
                  "wind_gusts_10m_max": [7.0] * 7,
                  "uv_index_max": [3.0] * 7,
                  "temperature_2m_max": [24.0] * 7},
        "_weekly_meta": {
            "provider": "open-meteo", "requested_start": dates[0],
            "requested_end": dates[-1], "returned_dates": dates[:],
            "coverage_days": 7, "coverage_complete": True,
            "weather_code_system": "wmo",
            "normalized_units": {"wind_speed": "m/s", "precipitation": "mm"},
        },
    } for city in (*MARINE_CITIES, *INLAND_CITIES)}


def test_trusted_weather_provenance_golden():
    from settings_cy import MARINE_CITIES
    baseline = valid_weekly_wmo()
    assert photo._trusted_variant('спокойная неделя', 'Температура 20–26°C', baseline, WEEK) == 'neutral'
    for code,sky in ((0, 'sunny'), (1, 'neutral'), (2, 'neutral'), (3, 'overcast')):
        actual = photo._trusted_wmo_variant(valid_weekly_wmo(code), WEEK)
        assert actual == sky, (code, actual)

    def fail(case, payload, start=WEEK, main='ровная неделя', weather='Температура 20–26°C'):
        try: photo._trusted_variant(main, weather, payload, start)
        except ValueError: return
        raise AssertionError('Unverified WMO scenario accepted: ' + case)

    fail('missing payload', None)
    fail('empty payload', {})
    fail('fabricated sunny text / missing WMO', None, main='солнечная неделя', weather='Температура 20–26°C; ясно')
    fail('fabricated sunny text / WMO neutral', baseline, weather='Температура 20–26°C; ясно')
    fail('fake weather text / WMO sunny contradiction', valid_weekly_wmo(0), weather='Температура 20–26°C; пасмурно')
    fail('wrong week', baseline, start=WEEK + timedelta(days=7))
    fail('missing city', {k:v for i,(k,v) in enumerate(baseline.items()) if i})
    fail('extra city', {**baseline,'FakeTown':next(iter(baseline.values()))})

    # Trusted weekly evidence cannot be manufactured by modifying prose.
    sample = next(iter(baseline))
    cases = []
    changed=deepcopy(baseline);changed[sample]['daily']['time'] = changed[sample]['daily']['time'][:-1];cases.append(('missing day',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['time'][3] = changed[sample]['daily']['time'][2];cases.append(('duplicate day',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['time'] = changed[sample]['daily']['time'][::-1];cases.append(('reversed dates',changed))
    changed=deepcopy(baseline);changed[sample]['_weekly_meta']['returned_dates'][2] = '2020-01-02';cases.append(('forged metadata dates',changed))
    changed=deepcopy(baseline);changed[sample]['_weekly_meta']['requested_start'] = '2026-10-25';cases.append(('wrong meta start',changed))
    changed=deepcopy(baseline);changed[sample]['_weekly_meta']['provider'] = 'not-open-meteo';cases.append(('untrusted provider',changed))
    changed=deepcopy(baseline);changed[sample]['_weekly_meta']['weather_code_system'] = 'unknown';cases.append(('unknown code system',changed))
    changed=deepcopy(baseline);changed[sample]['_weekly_meta']['coverage_complete']=False;cases.append(('source marks partial',changed))
    changed=deepcopy(baseline);changed[sample]['_weekly_meta'].pop('weather_code_system');cases.append(('missing provenance',changed))
    changed=deepcopy(baseline);changed[sample]['_weekly_meta']['normalized_units']['wind_speed']='km/h';cases.append(('wrong wind units',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['weather_code'][3] = None;cases.append(('missing WMO code',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['weather_code'][3] = True;cases.append(('boolean WMO code',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['weather_code'][3] = float('nan');cases.append(('NaN WMO code',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['weather_code'][3] = 95;cases.append(('thunder',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['weather_code'][3] = 61;cases.append(('rain',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['weather_code'][3] = 45;cases.append(('fog',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['weather_code'][3] = 99;cases.append(('unknown WMO',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['weather_code'][3] = 0;cases.append(('mixed week sunny-neutral',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['weather_code'][3] = 3;cases.append(('mixed week overcast-neutral',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['weather_code'] = [1]*6;cases.append(('incomplete WMO array',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['weathercode']=[0]*7;cases.append(('conflicting WMO aliases',changed))
    changed=deepcopy(baseline);changed[sample]['daily']['date']=['2020-01-01']*7;cases.append(('conflicting date aliases',changed))
    for field, val in (("precipitation_probability_max", 45), ("precipitation_sum", 0.1),
                       ("wind_gusts_10m_max", 16), ("uv_index_max", 9),
                       ("temperature_2m_max", 38)):
        changed=deepcopy(baseline);changed[sample]['daily'][field][3]=val;cases.append(('danger '+field, changed))
        changed=deepcopy(baseline);changed[sample]['daily'].pop(field);cases.append(('unknown '+field, changed))
    for name,payload in cases: fail(name,payload)
    return {'verified_positive':4,'negative_scenarios':len(cases)+8}

SPORT = "\n".join(photo._ROUTINE_SPORT)

def text_fixture(*, main="ровная тёплая неделя для обычных дел, моря и спокойного ритма.",
                 weather="Температура держится в диапазоне 20–26°C; переменная облачность.",
                 sea="Средняя вода 24–25°C; лучше утром или ближе к закату.",
                 sports=SPORT, extra=""):
    return ("🗓 Вайб недели: 26 октября – 1 ноября\n\n"
            f"✨ Главный фон недели\n{main}\n\n"
            f"🌿 Смысл недели\nДела и отдых в балансе.\n\n"
            f"🌦 Погода\n{weather}\n\n"
            f"🌊 Море\n{sea}\n\n"
            f"🏄 Вода и спорт\n{sports}\n\n"
            "🏭 Воздух сейчас\nAQI 32 (низкий).\n\n"
            "🧲 Космопогода сейчас\nСейчас Kp 2.0: фон спокойный.\n\n"
            f"✅ Как прожить неделю\nПроверять обновления прогноза.\n{extra}\n\n"
            "#Кипр #вайбнедели")


def expect_reject(text, *, term):
    try:
        photo._source_guard(text)
    except ValueError as exc:
        assert term in str(exc),(term,str(exc))
    else:
        raise AssertionError(f"Missing rejection for {term}")


def test_provenance_and_approval_fail_closed():
    m=json.loads(photo.MANIFEST.read_text('utf-8'))
    assert m['approved_for_production'] is True
    assert m['size']==[1080,1350]
    assert set(m['variants']) == {'neutral', 'overcast', 'sunny'}
    assert all(x['approved'] is True for x in m['variants'].values())
    from PIL import Image
    for name,desc in m['variants'].items():
        image=photo.ASSET_ROOT/desc['file']
        assert hashlib.sha256(image.read_bytes()).hexdigest()==desc['sha256'],name
        with Image.open(image) as j:
            assert j.size==photo.SIZE and j.format=='JPEG'
            assert j.getexif().get(306) is None # no embedded dates

    # Approval is a per-asset authorization gate, not merely a label.
    # Reject both revoked and missing approval even with complete, safe WMO.
    cases = (
        ('global', 'neutral', 1, text_fixture()),
        ('neutral', 'neutral', 1, text_fixture()),
        ('sunny', 'sunny', 0, text_fixture(weather='Температура держится в диапазоне 20–26°C; ясно.')),
        ('overcast', 'overcast', 3, text_fixture(weather='Температура держится в диапазоне 20–26°C; пасмурно.')),
    )
    with tempfile.TemporaryDirectory() as td:
        candidate_manifest = Path(td) / 'manifest.json'
        for approval_field, variant, wmo_code, source in cases:
            for revoked_value in (False, None):
                revoked = deepcopy(m)
                if approval_field == 'global':
                    if revoked_value is None: revoked.pop('approved_for_production')
                    else: revoked['approved_for_production'] = revoked_value
                else:
                    if revoked_value is None: revoked['variants'][variant].pop('approved')
                    else: revoked['variants'][variant]['approved'] = revoked_value
                candidate_manifest.write_text(json.dumps(revoked), encoding='utf-8')
                output = Path(td) / f'blocked_{approval_field}_{revoked_value}.png'
                with patch.object(photo, 'MANIFEST', candidate_manifest):
                    try:
                        photo.render_photo_cover(source, start=WEEK, output_path=output,
                                                trusted_weather=valid_weekly_wmo(wmo_code))
                    except ValueError as exc:
                        assert ('approval' in str(exc) if approval_field == 'global'
                                else 'unapproved' in str(exc)), str(exc)
                    else:
                        raise AssertionError(f'Unapproved {approval_field} scene rendered')
                assert not output.exists(), 'Unapproved image was written to disk'


def test_golden_warning_matrix():
    assert photo._source_guard(text_fixture())[1:3]==('20–26°C','24–25°C')
    try:
        photo._variant("ровная неделя", "просто тепло")
    except ValueError:
        pass
    else:
        raise AssertionError("Unsourced sky state was allowed")
    checks = (
        ("empty weather",dict(weather=''), 'source facts'),
        ("missing SST",dict(sea='Данные по воде обновятся ближе к неделе.'), 'temperature'),
        ("partial seven days",dict(weather='Полный прогноз на все 7 дней пока не собран.'), 'seven-day'),
        ("partial headline",dict(main='Полный прогноз на все 7 дней пока не подтверждён.'), 'seven-day'),
        ("thunder",dict(weather='Температура держится в диапазоне 20–26°C; грозы.'), 'warning'),
        ("gust",dict(weather='Температура держится в диапазоне 20–26°C; порывы 16 м/с.'), 'warning'),
        ("rain",dict(weather='Температура держится в диапазоне 20–26°C; дождь.'), 'warning'),
        ("storm",dict(main='штормовое предупреждение.'), 'warning'),
        ("marine hazard",dict(sea='Средняя вода 24°C; высокие волны.'), 'warning'),
        ("marine second line",dict(sea='Средняя вода 24°C; комфортно.\nНе выходить из-за волн.'), 'source facts'),
        ("weather second line",dict(weather='Температура держится в диапазоне 20–26°C; спокойно.\nШторм.'), 'source facts'),
        ("UV",dict(weather='Температура держится в диапазоне 20–26°C; высокий УФ.'), 'warning'),
        ("heat",dict(main='жаркая неделя с высоким УФ.'), 'warning'),
        ("haze",dict(weather='Температура держится в диапазоне 20–26°C; пыль.'), 'warning'),
        ("fog",dict(main='Туман утром.'), 'warning'),
        ("poor air",dict(extra='Уличную активность ограничить: воздух неидеален.'), 'warning'),
        ("plan storm",dict(extra='Шторм: не выходить на воду.'), 'warning'),
        ("stronger kite warning",dict(sports=SPORT.replace(photo._ROUTINE_SPORT[1],"Кайт/винг: только опытным; порывы могут быть резкими.")), 'water safety'),
        ("positive SUP",dict(sports=SPORT.replace(photo._ROUTINE_SPORT[0],"SUP: прекрасное окно в бухте.")), 'water safety'),
        ("missing sports",dict(sports=''), 'water safety'),
        ("extra sport warning",dict(sports=SPORT+'\nПри сильном ветре не выходить.'), 'water safety'),
        ("no temperature",dict(weather='Погода ровная; спокойно.'), 'temperature'),
        ("no SST unit",dict(sea='Средняя вода тёплая.'), 'temperature'),
        ("no seven day header",dict(), 'none'),
    )
    for name,kw,term in checks:
        if name=='no seven day header':
            expect_reject(text_fixture().replace('🗓 Вайб недели:','Без заголовка:'),term='source text')
        else:
            expect_reject(text_fixture(**kw),term=term)
    return len(checks)


def test_r5a_adversarial_weather_truth_and_full_text_warnings():
    """R5 four confirmed failures; new strict negative/mixed-sky cases."""
    ordinary = 'Температура держится в диапазоне 20–26°C; '
    # A previously ignored bulletin BEFORE the first section must be visible
    # to the guard. Unknown content at that position also refuses to render.
    prefix = '🗓 Вайб недели: 26 октября – 1 ноября\n'
    for preamble in (
        'Штормовое предупреждение у побережья!',
        '⚠️ Важная информация: не выходить на воду.',
        'Особое сообщение, которое не входит ни в один раздел.',
    ):
        source = text_fixture().replace(prefix, prefix+preamble+'\n', 1)
        expect_reject(source, term='warning before weekly sections')
    # Scan warning-like headings or notices even if they are placed in the
    # headline/date line or appended after the final known section.
    expect_reject(text_fixture().replace('26 октября – 1 ноября',
        '26 октября – 1 ноября: шторм', 1), term='warning')
    expect_reject(text_fixture(extra='⚠️ Особое предупреждение'), term='warning')

    # Each case was accepted as a misleading sunny/neutral photograph in R5.
    unsafe = (
        ('sunny headline / overcast weather', 'ясная спокойная неделя', ordinary+'пасмурно.'),
        ('overcast headline / sunny weather', 'пасмурная спокойная неделя', ordinary+'ясно.'),
        ('negated sunny', 'не солнечная неделя', ordinary+'переменная облачность.'),
        ('negative sunny prediction', 'не будет солнечной погоды', ordinary+'ясно.'),
        ('negated overcast', 'пасмурной погоды не ожидается', ordinary+'пасмурно.'),
        ('negated clear', 'не ясно', ordinary+'ясно.'),
        ('mixed within weather', 'спокойная неделя', ordinary+'ясно, но пасмурно.'),
        ('mixed sunny/neutral', 'спокойная неделя', ordinary+'солнечно, переменная облачность.'),
        ('sunny with ambiguous clouds', 'спокойная неделя', ordinary+'ясно, облачно.'),
        ('uncertain weather', 'ясная неделя', ordinary+'возможно солнечно.'),
        ('unsourced sky', 'ясная неделя', ordinary+'тихо.'),
        ('unsourced headline', 'спокойная неделя', ordinary+'тепло.'),
    )
    for name, main, weather in unsafe:
        w, _temp, _sea, h = photo._source_guard(text_fixture(main=main, weather=weather))
        try:
            photo._variant(h, w)
        except ValueError:
            pass
        else:
            raise AssertionError(f'R5a allowed unsupported weather photo: {name}')
    # Continue allowing genuinely supported SINGLE sky states, not eliminating
    # photo mode merely to get safety regressions green.
    supported = (
        ('спокойная неделя', ordinary+'переменная облачность.', 'neutral'),
        ('ясная спокойная неделя', ordinary+'ясно.', 'sunny'),
        ('пасмурная спокойная неделя', ordinary+'пасмурно.', 'overcast'),
        ('спокойная неделя', ordinary+'пасмурно.', 'overcast'),
        ('спокойная неделя', ordinary+'солнечно.', 'sunny'),
    )
    for main,weather,expected in supported:
        w,_temp,_sea,h=photo._source_guard(text_fixture(main=main,weather=weather))
        assert photo._variant(h,w)==expected
    return {'r5_reproduced_regressions':4, 'extra_adversarial':len(unsafe)-3+3,
            'valid_photo_classifications':len(supported)}


def test_fallback_and_off_switch():
    with tempfile.TemporaryDirectory() as td:
        target=Path(td)/'card.png'
        original=os.environ.get('WEEKLY_PHOTO_COVER_ENABLED')
        try:
            os.environ.pop('WEEKLY_PHOTO_COVER_ENABLED',None)
            off=wrapper.render_weekly_cover(text_fixture(),start=WEEK,output_path=target)
            assert off['renderer_version']==wrapper.RENDERER_VERSION
            baseline_hash=hashlib.sha256(target.read_bytes()).hexdigest()
            os.environ['WEEKLY_PHOTO_COVER_ENABLED']='0'
            off0=wrapper.render_weekly_cover(text_fixture(),start=WEEK,output_path=target)
            assert off0['renderer_version']==wrapper.RENDERER_VERSION
            assert hashlib.sha256(target.read_bytes()).hexdigest()==baseline_hash
            os.environ['WEEKLY_PHOTO_COVER_ENABLED']='1'
            gated=wrapper.render_weekly_cover(text_fixture(),start=WEEK,output_path=target)
            assert gated['renderer_version']==wrapper.RENDERER_VERSION
        finally:
            if original is None: os.environ.pop('WEEKLY_PHOTO_COVER_ENABLED',None)
            else: os.environ['WEEKLY_PHOTO_COVER_ENABLED']=original


def test_three_photo_geometry_only_with_temporary_inmemory_approval():
    from PIL import Image, ImageChops
    manifest=json.loads(photo.MANIFEST.read_text('utf-8'))
    # In-memory TEST ONLY: permanent manifest never touched.
    manifest['approved_for_production']=True
    for v in manifest['variants'].values(): v['approved']=True
    with tempfile.TemporaryDirectory() as td:
        temporary=Path(td)/'manifest.json'
        temporary.write_text(json.dumps(manifest))
        with patch.object(photo,'MANIFEST',temporary):
            sources=(
                ('neutral',text_fixture()),
                ('sunny',text_fixture(main='ясная спокойная неделя для дел.', weather='Температура держится в диапазоне 20–26°C; солнечно.')),
                ('overcast',text_fixture(main='пасмурная спокойная неделя для дел.', weather='Температура держится в диапазоне 20–26°C; пасмурно.')),
            )
            for variant,text in sources:
                source_wmo = valid_weekly_wmo({"neutral": 1, "sunny": 0, "overcast": 3}[variant])
                dest=Path(td)/(variant+'.png')
                meta=photo.render_photo_cover(text,start=WEEK,output_path=dest,trusted_weather=source_wmo)
                assert meta['variant']==variant and meta['region']=='cyprus'
                assert meta['source_text_sha256']==hashlib.sha256(text.encode()).hexdigest()
                assert meta['temperature']=='20–26°C' and meta['marine_observation']=='24–25°C'
                assert meta['safety_footer']=='spot/time windows not confirmed'
                with Image.open(dest) as out:
                    assert out.size==(1080,1350) and out.format=='PNG'
                    with Image.open(photo.ASSET_ROOT/manifest['variants'][variant]['file']) as source:
                        rgb=source.convert('RGB')
                        assert ImageChops.difference(rgb.crop((0,0,1080,135)),out.crop((0,0,1080,135))).getbbox() is None
                        assert ImageChops.difference(rgb.crop((0,1215,1080,1350)),out.crop((0,1215,1080,1350))).getbbox() is None
                    assert out.info['source_text_sha256']==meta['source_text_sha256']
                # Independent guard: a bad manifest hash is always refused.
                bad=json.loads(json.dumps(manifest))
                bad['variants'][variant]['sha256']='0'*64
                temporary.write_text(json.dumps(bad))
                try: photo.render_photo_cover(text,start=WEEK,output_path=dest,trusted_weather=source_wmo)
                except ValueError as exc: assert 'provenance' in str(exc)
                else: raise AssertionError('tampered asset accepted')
                temporary.write_text(json.dumps(manifest))


def test_source_bound_real_wrapper_and_forged_text():
    """Exercise the actual render wrapper; NEVER approve the tracked manifest."""
    manifest = json.loads(photo.MANIFEST.read_text(encoding='utf-8'))
    manifest['approved_for_production'] = True
    for item in manifest['variants'].values(): item['approved'] = True
    with tempfile.TemporaryDirectory() as td:
        mpath = Path(td)/'approved_in_memory.json'
        mpath.write_text(json.dumps(manifest), encoding='utf-8')
        with patch.object(photo, 'MANIFEST', mpath), patch.dict(os.environ, {'WEEKLY_PHOTO_COVER_ENABLED':'1'}):
            for code, variant, expected_text in (
                (0, 'sunny', text_fixture(weather='Температура держится в диапазоне 20–26°C; ясно.')),
                (1, 'neutral', text_fixture()),
                (3, 'overcast', text_fixture(weather='Температура держится в диапазоне 20–26°C; пасмурно.')),
            ):
                payload = valid_weekly_wmo(code)
                got = wrapper.render_weekly_cover(expected_text, start=WEEK,
                              output_path=Path(td)/f'{variant}.png', trusted_weather=payload)
                assert got['renderer_version'] == photo.RENDERER_VERSION
                assert got['variant'] == variant
                assert got['weather_provenance'] == 'seven_day_island_wmo_all_cities'
            # Textual `ясно` must never override a complete neutral WMO week.
            forged = text_fixture(weather='Температура держится в диапазоне 20–26°C; ясно.')
            fallback = wrapper.render_weekly_cover(forged,start=WEEK,
                       output_path=Path(td)/'forged.png',trusted_weather=valid_weekly_wmo(1))
            assert fallback['renderer_version'] == wrapper.RENDERER_VERSION
            missing = wrapper.render_weekly_cover(forged,start=WEEK,
                       output_path=Path(td)/'missing.png')
            assert missing['renderer_version'] == wrapper.RENDERER_VERSION
            storm = valid_weekly_wmo(0)
            next(iter(storm.values()))['daily']['weather_code'][2] = 95
            blocked = wrapper.render_weekly_cover(forged,start=WEEK,
                       output_path=Path(td)/'storm.png',trusted_weather=storm)
            assert blocked['renderer_version'] == wrapper.RENDERER_VERSION


def _isolated_send():
    """Compile only REAL production _send AST without imports that require network deps."""
    source=(ROOT/'send_weekly_forecast.py').read_text()
    tree=ast.parse(source)
    fn=next(n for n in tree.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='_send')
    module=ast.Module(body=[fn],type_ignores=[])
    ast.fix_missing_locations(module)
    env={'__name__':'weekly_send_offline_contract','os':os,'Path':Path,'sys':sys,
         'constants':SimpleNamespace(ParseMode=SimpleNamespace(HTML='HTML')),
         '_fmt_week_range':lambda start:'26 октября – 1 ноября',
         'write_snapshot_atomic':lambda *a: None,'finalize_snapshot':lambda data,messageid:data}
    exec(compile(module,str(ROOT/'send_weekly_forecast.py'),'exec'),env)
    return env['_send'],env


def test_delivery_order_and_fail_closed():
    send,env=_isolated_send()
    events=[]
    sent_image=Path(__file__)
    class FakeBot:
        mode='ok'
        def __init__(self,token): assert token=='OFFLINE_PLACEHOLDER'
        async def send_message(self,**kwargs):
            events.append('text')
            if self.mode=='raise_text': raise RuntimeError('synthetic ambiguous send')
            return SimpleNamespace(message_id=(None if self.mode=='missing_receipt' else 22))
        async def send_photo(self,**kwargs):
            events.append('photo')
            if self.mode=='raise_photo': raise RuntimeError('synthetic send photo failure')
            return SimpleNamespace(message_id=23)
    fake=ModuleType('telegram');fake.Bot=FakeBot;fake.constants=env['constants']
    received_sources=[]
    env['render_weekly_cover']=lambda *args,**kwargs:(received_sources.append(kwargs.get('trusted_weather')) or {'path':str(sent_image)})
    previous=dict(os.environ)
    try:
        os.environ['TELEGRAM_TOKEN']='OFFLINE_PLACEHOLDER'
        os.environ['WEEKLY_PHOTO_COVER_ENABLED']='0'
        with patch.dict(sys.modules,{'telegram':fake}):
            result=asyncio.run(send('source complete','@fake',WEEK))
        assert events==['photo','text'] and result==22,events
        events.clear();os.environ['WEEKLY_PHOTO_COVER_ENABLED']='1'
        proof=valid_weekly_wmo(1)
        with patch.dict(sys.modules,{'telegram':fake}):
            result=asyncio.run(send('source complete','@fake',WEEK,trusted_weather=proof))
        assert events==['text','photo'] and result==22,events
        assert received_sources[-1] is proof, 'source weather not forwarded to renderer'
        events.clear();FakeBot.mode='raise_text'
        try:
            with patch.dict(sys.modules,{'telegram':fake}):asyncio.run(send('source complete','@fake',WEEK))
        except RuntimeError:pass
        else:raise AssertionError('ambiguous send did not stop')
        assert events==['text'],events
        events.clear();FakeBot.mode='missing_receipt'
        with patch.dict(sys.modules,{'telegram':fake}):result=asyncio.run(send('source complete','@fake',WEEK))
        assert events==['text'] and result is None,events
        events.clear();FakeBot.mode='raise_photo'
        with patch.dict(sys.modules,{'telegram':fake}):result=asyncio.run(send('source complete','@fake',WEEK))
        assert events==['text','photo'] and result==22,events
    finally:
        os.environ.clear();os.environ.update(previous)


def test_workflow_and_scope_static():
    workflow=(ROOT/'.github/workflows/weekly_forecast.yml').read_text()
    assert "WEEKLY_PHOTO_COVER_ENABLED: ${{ vars.WEEKLY_PHOTO_COVER_ENABLED == '1' && '1' || '0' }}" in workflow
    assert workflow.count('WEEKLY_PHOTO_COVER_ENABLED:')==1
    checks=(ROOT/'.github/workflows/pr_checks.yml').read_text()
    assert 'python tools/test_cyprus_weekly_photo_cover.py' in checks
    text=(ROOT/'send_weekly_forecast.py').read_text()
    assert 'def _water_sport_lines(metrics:' in text
    assert 'SUP: безопасность по спотам и времени не подтверждена' in text
    assert 'Кайт/винг: подходящие окна по спотам не подтверждены' in text


def main():
    methods=[test_provenance_and_approval_fail_closed,test_golden_warning_matrix,
             test_trusted_weather_provenance_golden,
             test_r5a_adversarial_weather_truth_and_full_text_warnings,
             test_fallback_and_off_switch,test_three_photo_geometry_only_with_temporary_inmemory_approval,
             test_source_bound_real_wrapper_and_forged_text, test_delivery_order_and_fail_closed,test_workflow_and_scope_static]
    for method in methods:
        ret=method()
        print('PASS',method.__name__,ret or '')
    print('PASS Cyprus Weekly Photo R5c offline contract 9/9 groups, trusted WMO provenance + R5a adversarial cases')

if __name__=='__main__': main()
