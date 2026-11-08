#!/usr/bin/env python3
"""R12-H2 isolated, deterministic offline tests. No provider or Telegram calls."""
from __future__ import annotations

from datetime import date
from hashlib import sha256
import json
import os
from pathlib import Path
import sys
import tempfile
import types
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import weekly_cover as cover
import weekly_legacy_architecture_cy as art

DATE = date(2026, 11, 9)
SOURCE = (ROOT / 'tools/weekly_2026_11_09_from_run_42425806877.txt').read_text(encoding='utf-8')
BASE_MANIFEST = json.loads(art.MANIFEST.read_text(encoding='utf-8'))
HAZARDS = {
    'real': SOURCE,
    'thunder': SOURCE.replace('тёплая неделя, но у моря порывы могут менять ощущение дня.',
        'гроза и порывистый ветер; морская активность небезопасна.'),
    'storm_gusts': SOURCE.replace('тёплая неделя, но у моря порывы могут менять ощущение дня.',
        'штормовой ветер: порывы до 20 м/с, у моря опасно.'),
    'dust': SOURCE.replace('тёплая неделя, но у моря порывы могут менять ощущение дня.',
        'пыльная буря: выход на открытый воздух ограничить.'),
    'rain': SOURCE.replace('тёплая неделя, но у моря порывы могут менять ощущение дня.',
        'ливни и локальные дождевые окна: условия меняются.'),
    'dangerous_surf': SOURCE.replace('тёплая неделя, но у моря порывы могут менять ощущение дня.',
        'опасные волны и сильный ветер у моря; выход не планировать.'),
    'missing': SOURCE.replace('тёплая неделя, но у моря порывы могут менять ощущение дня.',
        'Полный прогноз пока не собран, безопасность спотов не подтверждена.'),
    'unknown': SOURCE.replace('тёплая неделя, но у моря порывы могут менять ощущение дня.',
        'Данных о погоде на неделю пока недостаточно.'),
}


def modules(photo_error='R10: weather/safety warning not represented by photo', landmark_error='R12: refused'):
    p = types.ModuleType('weekly_photo_cover_cy')
    l = types.ModuleType('weekly_architecture_cover_cy')
    def fail_photo(*_a, **_k):
        raise ValueError(photo_error)
    def fail_landmark(*_a, **_k):
        raise ValueError(landmark_error)
    p.render_photo_cover = fail_photo
    l.render_landmark_cover = fail_landmark
    return {'weekly_photo_cover_cy':p, 'weekly_architecture_cover_cy':l}


def write_manifest(dest: Path, **kw) -> Path:
    doc = {**BASE_MANIFEST, **kw}
    m = dest / ('manifest_' + '_'.join(f'{k}={v}' for k,v in sorted(kw.items())).replace('/','_')[:115] + '.json')
    m.write_text(json.dumps(doc, ensure_ascii=False), encoding='utf-8')
    return m


def render(dest: Path, text: str, *, photo_on: bool = True, manifest: Path):
    target = dest/'cover.png'
    with patch.dict(os.environ, {'WEEKLY_PHOTO_COVER_ENABLED':'1' if photo_on else '0'}), \
         patch.object(art,'MANIFEST',manifest), patch.dict(sys.modules,modules()):
        meta=cover.render_weekly_cover(text,start=DATE,output_path=target, trusted_weather=None)
    return meta,target


def run():
    checks=0
    def check(condition, msg):
        nonlocal checks
        assert condition, msg
        checks+=1
    check(cover._ARCHITECTURE_BODY_FONT_SIZE == 29, 'architectural typography baseline')
    check(BASE_MANIFEST['approved'] is False, 'asset must ship unapproved')
    check(BASE_MANIFEST['size']==[1080,1350], 'manifest geometry')
    asset=art.ASSET_ROOT/art.ASSET_FILENAME
    check(asset.exists() and asset.is_file(), 'asset exists')
    check(sha256(asset.read_bytes()).hexdigest()==BASE_MANIFEST['sha256'], 'asset hash')
    with Image.open(asset) as photo:
        check(photo.format=='JPEG' and photo.size==(1080,1350), 'asset JPEG geometry')

    with tempfile.TemporaryDirectory() as td:
        root=Path(td)
        approved=write_manifest(root,approved=True)
        unapproved=write_manifest(root,approved=False)
        with patch.object(art,'MANIFEST',unapproved):
            check(art.approved_architecture_background() is None,'negative approval guard')
        with patch.object(art,'MANIFEST',approved):
            image,digest=art.approved_architecture_background()
            check(image.size==(1080,1350) and digest==BASE_MANIFEST['sha256'],'positive approval path')

        # Photo OFF never imports/uses art; even a locally approved manifest changes nothing.
        with patch.dict(os.environ,{'WEEKLY_PHOTO_COVER_ENABLED':'0'}), \
             patch.object(art,'approved_architecture_background',side_effect=AssertionError('OFF called decorative')):
            m=cover.render_weekly_cover(SOURCE,start=DATE,output_path=root/'off.png')
            check('background_role' not in m,'OFF metadata')
        check(Image.open(root/'off.png').size==(1080,1350),'OFF PNG geometry')
        off_meta,off_approved_path=render(root/'off_approved',SOURCE,photo_on=False,manifest=approved)
        check('background_role' not in off_meta, 'Photo OFF cannot use approved decorative art')
        check(sha256((root/'off.png').read_bytes()).hexdigest()==sha256(off_approved_path.read_bytes()).hexdigest(),
              'Photo OFF output must remain invariant with approval state')

        # Unapproved image and missing/invalid digest do not change existing legacy.
        m1,p1=render(root/'unapproved',SOURCE,manifest=unapproved)
        check('background_role' not in m1, 'unapproved must remain gradient')
        p1hash=sha256(p1.read_bytes()).hexdigest()
        sha_bad=write_manifest(root,approved=True,sha256='0'*64)
        m2,p2=render(root/'sha_bad',SOURCE,manifest=sha_bad)
        check('background_role' not in m2 and sha256(p2.read_bytes()).hexdigest()==p1hash,
              'digest tampering must return identical gradient')
        path_bad=write_manifest(root,approved=True,file='../bad.jpg')
        m3,p3=render(root/'badpath',SOURCE,manifest=path_bad)
        check('background_role' not in m3 and sha256(p3.read_bytes()).hexdigest()==p1hash,
              'traversal must return identical gradient')
        schema_bad=write_manifest(root,approved=True,schema='invalid')
        m4,p4=render(root/'badschema',SOURCE,manifest=schema_bad)
        check('background_role' not in m4 and sha256(p4.read_bytes()).hexdigest()==p1hash,
              'unknown manifest schema must return identical gradient')

        # Real week, thunder, rain, dust, storm, wave hazard and unknown weather.
        for name,text in HAZARDS.items():
            m,p=render(root/name,text,manifest=approved)
            check(m.get('background_role')=='editorial_architecture_not_weather', f'{name} decorative path')
            check(m.get('background_asset_sha256')==BASE_MANIFEST['sha256'],f'{name} bound asset')
            for heading,key in (('✨ Главный фон недели','main_fact'),('🌦 Погода','weather_fact'),('🌊 Море','sea_fact')):
                check(m[key]==cover._section_value(text,heading),f'{name} exact {heading}')
            with Image.open(p) as im:
                check(im.size==(1080,1350) and im.info.get('background_role')=='editorial_architecture_not_weather',f'{name} PNG metadata')
            # All three warning/weather panels must be fully displayable (never summary placeholder).
            body_font=cover._font(29,bold=True)
            draw=ImageDraw.Draw(Image.new('RGB',(1080,1350)))
            for key in ('main_fact','weather_fact','sea_fact'):
                lines=cover._wrap(draw,m[key],body_font,815,3)
                check(' '.join(lines)==' '.join(m[key].split()),f'{name} hidden or truncated {key}')
            print('PASS source+hazards',name,flush=True)

        # Directly inspect actual drawn text; metadata alone is insufficient
        # evidence that weather and safety warnings appear on the raster.
        original_draw_text = ImageDraw.ImageDraw.text
        for scenario in ('real', 'storm_gusts', 'thunder', 'dust'):
            seen = []
            def record_text(self, xy, text, *args, **kwargs):
                seen.append((xy, text, kwargs.get('font')))
                return original_draw_text(self, xy, text, *args, **kwargs)
            with patch.object(ImageDraw.ImageDraw, 'text', record_text):
                observed_meta, observed_png=render(root/('drawn_'+scenario),HAZARDS[scenario],manifest=approved)
            strings=[item for _xy,item,_font_obj in seen]
            for heading,meta_key in (('ГЛАВНОЕ','main_fact'),('ПОГОДА','weather_fact'),('МОРЕ','sea_fact')):
                start_idx=strings.index(heading)
                following_headings=[i for i in range(start_idx+1,len(strings)) if strings[i] in ('ГЛАВНОЕ','ПОГОДА','МОРЕ')]
                stop_idx=following_headings[0] if following_headings else len(strings)
                visible=' '.join(strings[start_idx+1:stop_idx])
                check(visible==' '.join(observed_meta[meta_key].split()),
                      f'{scenario} actual raster drawing lost {heading} text')
            sample_draw=ImageDraw.Draw(Image.new('RGB',(1080,1350)))
            for xy,value,font in seen:
                if not value:
                    continue
                bounds=sample_draw.textbbox(xy,value,font=font)
                check(55<=bounds[0]<=bounds[2]<=1025 and 135<=bounds[1]<=bounds[3]<=1215,
                      f'{scenario} glyph bbox outside central Telegram safe zone: {value} {bounds}')
            check(Image.open(observed_png).size==(1080,1350),f'{scenario} rendered image geometry')

        # R12-H4 X7: 29pt source text fits THREE lines, 34pt truncates its
        # critical final words. This is a realistic wind/wave safety boundary,
        # not a generic long-string smoke test. Capture what is ACTUALLY drawn.
        x7_warning = (
            'Штормовой ветер у моря: порывы до 20 м/с; опасные волны. '
            'Выход на воду не рекомендуется, местами сильные порывы,'
        )
        x7_source = SOURCE.replace(
            'тёплая неделя, но у моря порывы могут менять ощущение дня.',
            x7_warning,
        )
        check(x7_warning in x7_source, 'X7 fixture must replace the real headline')
        draw_for_fit = ImageDraw.Draw(Image.new('RGB', (1080, 1350)))
        fit_at_29 = cover._wrap(draw_for_fit, x7_warning, cover._font(29, bold=True), 815, 3)
        fit_at_34 = cover._wrap(draw_for_fit, x7_warning, cover._font(34, bold=True), 815, 3)
        check(' '.join(fit_at_29) == ' '.join(x7_warning.split()), 'X7 29pt boundary must fit')
        check(' '.join(fit_at_34) != ' '.join(x7_warning.split()), 'X7 34pt must lose safety text')
        x7_drawn = []
        def capture_x7_text(self, xy, value, *args, **kwargs):
            x7_drawn.append((value, kwargs.get('font')))
            return original_draw_text(self, xy, value, *args, **kwargs)
        with patch.object(ImageDraw.ImageDraw, 'text', capture_x7_text):
            x7_meta, x7_png = render(root/'x7_boundary', x7_source, manifest=approved)
        check(x7_meta.get('background_role') == 'editorial_architecture_not_weather',
              'X7 warning fits and should use verified architecture')
        x7_strings = [value for value, _font_obj in x7_drawn]
        main_idx = x7_strings.index('ГЛАВНОЕ')
        weather_idx = x7_strings.index('ПОГОДА')
        main_drawn = x7_drawn[main_idx+1:weather_idx]
        check(' '.join(value for value, _font_obj in main_drawn) == ' '.join(x7_warning.split()),
              'X7 actual drawn main warning truncated or changed')
        check(len(main_drawn) == 3, 'X7 must exercise the three-line layout')
        check(all(getattr(font, 'size', None) == cover._ARCHITECTURE_BODY_FONT_SIZE
                  for _value, font in main_drawn),
              'X7 actual font must match preflight font')
        check(x7_png.is_file(), 'X7 preview must be saved')
        print('PASS R12-H4 X7 actual glyphs and preflight font match', flush=True)

        # A warning too long to display must refuse decorative background.
        overly_long=SOURCE.replace('тёплая неделя, но у моря порывы могут менять ощущение дня.',
                   'ОПАСНО: ' + ('очень сильный штормовой ветер и опасная гроза у побережья; '*15))
        bad,p=render(root/'overwide',overly_long,manifest=approved)
        check('background_role' not in bad,'long hidden warning must not get scenic cover')
        check(bad['main_fact'].startswith('ОПАСНО:'),'source text preserved in metadata')

        # Runtime errors are unchanged by H2: do not escalate to R12.
        runtime = types.ModuleType('weekly_photo_cover_cy')
        def boom(*_a,**_k): raise RuntimeError('simulated R10 exception')
        runtime.render_photo_cover=boom
        with patch.dict(os.environ,{'WEEKLY_PHOTO_COVER_ENABLED':'1'}),patch.object(art,'MANIFEST',approved),patch.dict(sys.modules,{'weekly_photo_cover_cy':runtime}):
            res=cover.render_weekly_cover(SOURCE,start=DATE,output_path=root/'runtime.png')
        check(res['renderer_version']==cover.RENDERER_VERSION,'runtime still legacy')
        check('background_role' in res,'runtime legacy decorative only')

        # Photo success remains authoritative; never render legacy when R10 succeeds.
        success=types.ModuleType('weekly_photo_cover_cy')
        success.render_photo_cover=lambda *_a,**_k: {'renderer_version':'r10_success','path':'noop'}
        with patch.dict(os.environ,{'WEEKLY_PHOTO_COVER_ENABLED':'1'}),patch.dict(sys.modules,{'weekly_photo_cover_cy':success}):
            result=cover.render_weekly_cover(SOURCE,start=DATE,output_path=root/'photo_success.png')
        check(result['renderer_version']=='r10_success','R10 cannot be overridden')

        # R12 success on permitted asset-only refusal is authoritative.
        p=types.ModuleType('weekly_photo_cover_cy')
        l=types.ModuleType('weekly_architecture_cover_cy')
        def denied(*_a,**_k): raise ValueError('weekly scene is unapproved')
        p.render_photo_cover=denied
        l.render_landmark_cover=lambda *_a,**_k: {'renderer_version':'r12_success','path':'noop'}
        with patch.dict(os.environ,{'WEEKLY_PHOTO_COVER_ENABLED':'1'}),patch.dict(sys.modules,{'weekly_photo_cover_cy':p,'weekly_architecture_cover_cy':l}):
            result=cover.render_weekly_cover(SOURCE,start=DATE,output_path=root/'landmark_success.png')
        check(result['renderer_version']=='r12_success','R12 cannot be overridden')

    print(f'R12-H2 OFFLINE PASS ({checks} assertions), no network or Telegram',flush=True)

if __name__=='__main__':
    run()
