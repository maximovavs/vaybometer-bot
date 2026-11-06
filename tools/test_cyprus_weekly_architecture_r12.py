#!/usr/bin/env python3
"""R12-F: safe and unsafe weekly landmark fallback, offline only.

Use real R10 source/WMO validation, mock only its *asset rendering* failure.
Never approve a repository asset or perform network/Telegram operations.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import date, timedelta
import hashlib
from io import StringIO
import json
import logging
import os
from pathlib import Path
import sys
import tempfile
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import weekly_cover
import weekly_photo_cover_cy as photo
import weekly_architecture_cover_cy as art

START = date(2026, 11, 2)
SAFE = '''🗓 Вайб недели: 02–08 ноября

✨ Главный фон недели
Ровная неделя для прогулок по острову.

🌦 Погода
Температура держится в диапазоне 20–26°C; переменная облачность.

🌊 Море
Средняя вода 23–24°C; лучше утром или ближе к закату.

🏄 Вода и спорт
''' + '\n'.join(photo._ROUTINE_SPORT) + '''

🏭 Воздух сейчас
AQI 32 (низкий).

🌙 Луна
Данные в основном посте.
'''
PARTIAL = SAFE.replace('Температура держится в диапазоне 20–26°C; переменная облачность.',
                       'Полный прогноз пока не собран; температуру лучше уточнять ближе к датам.')
ASSET_REJECTED = 'Cyprus weekly photo assets require independent visual approval'


def verified_weather(code: int = 1) -> dict:
    from settings_cy import MARINE_CITIES, INLAND_CITIES
    dates = [(START + timedelta(days=i)).isoformat() for i in range(7)]
    return {city: {
        'daily': {
            'time': dates[:], 'weather_code': [code]*7,
            'precipitation_probability_max': [0]*7,
            'precipitation_sum': [0.0]*7,
            'wind_gusts_10m_max': [7.0]*7,
            'uv_index_max': [3.0]*7,
            'temperature_2m_max': [24.0]*7,
        },
        '_weekly_meta': {
            'provider': 'open-meteo', 'requested_start': dates[0],
            'requested_end': dates[-1], 'returned_dates': dates[:],
            'coverage_days': 7, 'coverage_complete': True,
            'weather_code_system': 'wmo',
            'normalized_units': {'wind_speed': 'm/s', 'precipitation': 'mm'},
        },
    } for city in (*MARINE_CITIES, *INLAND_CITIES)}


def approved_manifest(temp: Path, scene: str | None) -> Path:
    manifest = json.loads(art.MANIFEST.read_text(encoding='utf-8'))
    for entry in manifest['assets']:
        entry['approved'] = entry['id'] == scene if scene is not None else False
    path = temp / f'manifest_{scene or "none"}.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False), encoding='utf-8')
    return path


def render(photo_error: Exception, source: str, weather: dict | None,
           path: Path, manifest: Path):
    with patch.dict(os.environ, {'WEEKLY_PHOTO_COVER_ENABLED':'1'}), \
         patch.object(photo, 'render_photo_cover', side_effect=photo_error), \
         patch.object(art, 'MANIFEST', manifest):
        return weekly_cover.render_weekly_cover(
            source, start=START, output_path=path, trusted_weather=weather)


def main() -> None:
    items = json.loads(art.MANIFEST.read_text(encoding='utf-8'))['assets']
    assert len(items) == 3 and all(x['approved'] is False for x in items)
    for entry in items:
        file = art.ASSET_ROOT / entry['file']
        with Image.open(file) as im:
            assert im.size == (1080,1350) and im.format == 'JPEG'
        assert hashlib.sha256(file.read_bytes()).hexdigest() == entry['sha256']
    # Safety checks run with the real R10 validators, independently of photo asset gates.
    assert photo._source_guard(SAFE)[1:3] == ('20–26°C','23–24°C')
    assert photo._trusted_variant('Ровная неделя', 'Температура 20–26°C',
                                  verified_weather(), START) == 'neutral'

    with tempfile.TemporaryDirectory() as folder:
        d = Path(folder)
        manifest = approved_manifest(d, 'eleftheria_nicosia')
        # If photo rejected for a SAFETY reason, not even an approved artwork is tried.
        for category, source, wx, error in (
            ('r10_thunder', SAFE, verified_weather(), ValueError('R10: weather/safety warning not represented by photo')),
            ('r10_warning', SAFE, verified_weather(), ValueError('R10: explicit warning requires an appropriate photo')),
            ('untrusted_runtime', SAFE, verified_weather(), RuntimeError('network or rendering error')),
            ('partial', PARTIAL, verified_weather(), ValueError(ASSET_REJECTED)),
            ('dust_haze', SAFE.replace('AQI 32 (низкий).', 'AQI 145 (высокий): пыль и дымка.'), verified_weather(), ValueError(ASSET_REJECTED)),
            ('explicit_warning', SAFE + '\n⚠️ На острове ожидается гроза.', verified_weather(), ValueError(ASSET_REJECTED)),
            ('marine_hazard', SAFE.replace('Средняя вода 23–24°C', 'Опасные волны; средняя вода 23–24°C'), verified_weather(), ValueError(ASSET_REJECTED)),
            ('missing_weather', SAFE, None, ValueError(ASSET_REJECTED)),
            ('all_overcast', SAFE, verified_weather(3), ValueError(ASSET_REJECTED)),
        ):
            out = render(error, source, wx, d / (category+'.png'), manifest)
            assert out['renderer_version'] == weekly_cover.RENDERER_VERSION, category
            print('PASS FAIL-CLOSED',category,flush=True)

        for code in (95,96,99,61,45):
            wx = verified_weather(); next(iter(wx.values()))['daily']['weather_code'][2] = code
            result = render(ValueError(ASSET_REJECTED), SAFE, wx, d/f'wmo{code}.png', manifest)
            assert result['renderer_version'] == weekly_cover.RENDERER_VERSION, code
            print('PASS HAZARD WMO',code,flush=True)

        for key,value in (('wind_gusts_10m_max', 16.0),
                          ('precipitation_probability_max', 80),
                          ('precipitation_sum', 3.0),
                          ('uv_index_max', 10.0)):
            wx = verified_weather();next(iter(wx.values()))['daily'][key][3] = value
            result=render(ValueError(ASSET_REJECTED), SAFE, wx,d/f'{key}.png',manifest)
            assert result['renderer_version'] == weekly_cover.RENDERER_VERSION, key
            print('PASS HAZARD FIELD',key,flush=True)

        # No manifest approvals in Git; positive visual checks use a LOCAL in-memory copy.
        unapproved = approved_manifest(d,None)
        result=render(ValueError(ASSET_REJECTED),SAFE,verified_weather(),d/'unapproved.png',unapproved)
        assert result['renderer_version'] == weekly_cover.RENDERER_VERSION

        # Exact SHA tampering must not promote the artwork even with safe WMO.
        tampered=json.loads(manifest.read_text());tampered['assets'][0]['sha256']='0'*64
        manifest.write_text(json.dumps(tampered),encoding='utf-8')
        log_output=StringIO();handler=logging.StreamHandler(log_output)
        logger=logging.getLogger(weekly_cover.__name__);logger.addHandler(handler)
        try:
            result=render(ValueError(ASSET_REJECTED),SAFE,verified_weather(),d/'tampered.png',manifest)
        finally:
            logger.removeHandler(handler)
        assert result['renderer_version'] == weekly_cover.RENDERER_VERSION
        assert 'landmark_asset_sha_mismatch' in log_output.getvalue(),log_output.getvalue()
        print('PASS ASSET NOT-APPROVED / SHA TAMPER',flush=True)

        # One exact SAFE, 7-day verified weather source for all three cities.
        export=Path(os.getenv('R12F_PREVIEW_DIR',d));export.mkdir(parents=True,exist_ok=True)
        outputs=[]
        for scene in ('eleftheria_nicosia','kamares_larnaca','limassol_skyline'):
            scene_manifest=approved_manifest(d,scene)
            outfile=export/f'{scene}_safe_week.png'
            r=render(ValueError(ASSET_REJECTED),SAFE,verified_weather(),outfile,scene_manifest)
            assert r['renderer_version'] == art.RENDERER_VERSION and r['landmark'] == scene
            assert r['source_text_sha256'] == hashlib.sha256(SAFE.encode()).hexdigest()
            assert r['temperature'] == '20–26°C' and r['marine_observation'] == '23–24°C'
            with Image.open(outfile) as im:
                assert im.size==(1080,1350) and im.info['visual_weather_assertion']=='none'
                assert im.info['warning_text_changed']=='False'
                assert im.info['renderer_version']==art.RENDERER_VERSION
                assert im.info['verified_sky_class']=='neutral'
                assert im.info['r12_safety_preflight']=='r10_full_source_and_verified_wmo'
            outputs.append(r)
            print('PASS SAFE LANDMARK',scene,flush=True)
        assert len(set(x['source_text_sha256'] for x in outputs)) == 1
        # Preview and sent cover use the same deterministic renderer output.
        test=export/'repeat_safe_week.png'
        r2=render(ValueError(ASSET_REJECTED),SAFE,verified_weather(),test,approved_manifest(d,'limassol_skyline'))
        assert hashlib.sha256(test.read_bytes()).digest()==hashlib.sha256((export/'limassol_skyline_safe_week.png').read_bytes()).digest()
        assert r2['renderer_version']==art.RENDERER_VERSION

        # Photo OFF must never execute landmark, regardless of approvals/hazards.
        with patch.dict(os.environ,{'WEEKLY_PHOTO_COVER_ENABLED':'0'}):
            a=weekly_cover.render_weekly_cover(SAFE,start=START,output_path=d/'off.png')
            assert a['renderer_version']==weekly_cover.RENDERER_VERSION
        print('PASS OFF + DETERMINISTIC PREVIEW',flush=True)

    assert weekly_cover._photo_refusal_reason(ValueError('R10: weather/safety warning not represented by photo')) == ('r10_weather_safety_warning',False)
    assert weekly_cover._photo_refusal_reason(ValueError('weekly scene is unapproved')) == ('photo_scene_unapproved',True)
    assert weekly_cover._photo_refusal_reason(RuntimeError('unsafe!'))[1] is False
    print('PASS NORMALIZED LOG REASONS',flush=True)

if __name__=='__main__':main()
