"""R8-H6 offline-only seam and regression checks. No network or Telegram."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from pathlib import Path
import runpy
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch

# The real FORMAT_V2 builder, sanitizer and splitter live in the repository root.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))

from cyprus_emma_preview_r8h5 import OfflinePreview, preview_warning_capture
from cyprus_emma_pipeline_r8h6 import (
    apply_emma_preview_offline, guarded_preview_warning_capture)
from test_cyprus_emma_preview_r8h5 import pair, TARGET
from test_cyprus_cap_r8g1_contract import NOW, attrs

BASE = ('<b>🌅 Кипр завтра (28.10.2026)</b>\n'
        '⚠️ По прогнозу: грозы и сильный ветер\n'
        '🏄 SUP / кайт / сёрф: безопасность не подтверждена\n'
        '✨ VayboMeter: н/д — официальные предупреждения не проверены.\n'
        '#Кипр #погода')


def identity_sanitize(text):
    return SimpleNamespace(text=text, issues=[])


def warning():
    atom, docs = pair()
    return guarded_preview_warning_capture(atom=atom, cap_by_url=docs,
        source=attrs(atom, docs), at_utc=NOW, forecast_date=TARGET)


class PipelineTests(unittest.TestCase):
    def test_negated_or_ambiguous_cap_event_refused_despite_h5_substring_match(self):
        from test_cyprus_emma_preview_r8h5 import pair as make_test_pair
        for event in ("No thunderstorm warning", "Thunderstorm not expected",
                      "Thunderstorm warning CANCELLED", "Strong wind warning not active",
                      "High temperatures not expected", "No fog expected",
                      "Thunderstorm forecast"):
            atom, docs = make_test_pair(event=event)
            # This is the known H5 semantic gap, not a real published warning.
            h5 = preview_warning_capture(atom=atom, cap_by_url=docs,
                source=attrs(atom, docs), at_utc=NOW, forecast_date=TARGET)
            h6 = guarded_preview_warning_capture(atom=atom, cap_by_url=docs,
                source=attrs(atom, docs), at_utc=NOW, forecast_date=TARGET)
            with self.subTest(event=event):
                self.assertEqual('UNKNOWN', h6.classification)
                self.assertEqual('NO_POST', h6.publication_action)
                self.assertFalse(h6.text)

    def test_strict_known_event_taxonomy_accepts_only_previews_not_posts(self):
        for event in ("Thunderstorm", "Heavy Rain", "Strong Wind", "High Temperature", "Snowfall", "Fog"):
            atom,docs=pair(event=event)
            p=guarded_preview_warning_capture(atom=atom,cap_by_url=docs,
                source=attrs(atom,docs),at_utc=NOW,forecast_date=TARGET)
            with self.subTest(event=event):
                self.assertEqual('OFFLINE_OBSERVED_ALERT_CANDIDATE',p.classification)
                self.assertEqual('NO_POST',p.publication_action)
                self.assertEqual('R8H6_EVENT_TAXONOMY_OK_OFFLINE',p.reason)
                self.assertIs(p.official_warnings_checked,False)

    def test_default_noop_without_activation(self):
        p = warning()
        self.assertEqual(BASE, apply_emma_preview_offline(BASE))
        self.assertEqual(BASE, apply_emma_preview_offline(BASE, p))
        self.assertEqual(BASE, apply_emma_preview_offline(BASE, p, offline_preview=False))

    def test_unfiltered_h5_preview_cannot_bypass_h6_gate(self):
        atom,docs=pair(event="No thunderstorm warning")
        h5=preview_warning_capture(atom=atom,cap_by_url=docs,
            source=attrs(atom,docs),at_utc=NOW,forecast_date=TARGET)
        self.assertEqual('OFFLINE_OBSERVED_ALERT_CANDIDATE',h5.classification)
        self.assertEqual(BASE,apply_emma_preview_offline(BASE,h5,
            offline_preview=True,sanitizer=identity_sanitize))

    def test_offline_injection_preserves_original_weather_and_water_text(self):
        p = warning()
        output = apply_emma_preview_offline(BASE, p, offline_preview=True, sanitizer=identity_sanitize)
        self.assertIn('По данным MeteoAlarm', output)
        self.assertIn('⚠️ По прогнозу: грозы и сильный ветер', output)
        self.assertIn('SUP / кайт / сёрф: безопасность не подтверждена', output)
        self.assertIn('VayboMeter: н/д — официальные предупреждения не проверены', output)
        self.assertTrue(output.endswith('#Кипр #погода'))
        self.assertEqual(output.count('По данным MeteoAlarm'), 1)
        self.assertEqual(output, apply_emma_preview_offline(output, p, offline_preview=True, sanitizer=identity_sanitize))
        self.assertEqual('NO_POST', p.publication_action)
        self.assertIs(p.official_warnings_checked, False)

    def test_preview_false_or_forged_positive_status_rejected(self):
        p = warning()
        for variant in (OfflinePreview(), replace(p, publication_action='POST'),
                        replace(p, clean_verified=True), replace(p, city_cleared=True),
                        replace(p, positive_water_guidance_allowed=True),
                        replace(p, national_inventory_complete=True),
                        replace(p, official_warnings_checked=True), object()):
            with self.subTest(type=str(type(variant))):
                self.assertEqual(BASE, apply_emma_preview_offline(BASE, variant,
                    offline_preview=True, sanitizer=identity_sanitize))

    def test_sanitizer_cannot_delete_forecast_warning_or_marine_guard(self):
        p = warning()
        def remove_forecast(s):
            return SimpleNamespace(text='\n'.join(line for line in s.splitlines() if not line.startswith('⚠️ По прогнозу')))
        def remove_water(s):
            return SimpleNamespace(text='\n'.join(line for line in s.splitlines() if not line.startswith('🏄')))
        def remove_rating(s):
            return SimpleNamespace(text='\n'.join(line for line in s.splitlines() if not line.startswith('✨')))
        for fn in (remove_forecast, remove_water, remove_rating):
            self.assertEqual(BASE, apply_emma_preview_offline(BASE, p,
                offline_preview=True, sanitizer=fn))

    def test_sanitizer_cannot_silently_remove_or_modify_warning(self):
        p = warning()
        for fn in (lambda s: SimpleNamespace(text=s.replace('По данным MeteoAlarm', 'По прогнозу')),
                   lambda s: SimpleNamespace(text=s.replace('Источник:', 'Ресурс:')),
                   lambda s: SimpleNamespace(text=s+'\nПо данным MeteoAlarm'),
                   lambda s: SimpleNamespace(text='')):
            self.assertEqual(BASE, apply_emma_preview_offline(BASE, p,
                offline_preview=True, sanitizer=fn))

    def test_sanitizer_reordering_is_fail_closed(self):
        p=warning()
        def reorder(s):
            lines=s.splitlines()
            lines[1],lines[2]=lines[2],lines[1]
            return SimpleNamespace(text='\n'.join(lines))
        self.assertEqual(BASE, apply_emma_preview_offline(BASE, p, offline_preview=True, sanitizer=reorder))

    def test_empty_and_invalid_r8h5_inputs_no_preview(self):
        atom, docs = pair(count=0)
        p = preview_warning_capture(atom=atom,cap_by_url=docs,source=attrs(atom,docs),at_utc=NOW,forecast_date=TARGET)
        self.assertFalse(p.text)
        self.assertEqual(BASE, apply_emma_preview_offline(BASE,p,offline_preview=True,sanitizer=identity_sanitize))

    def test_real_sanitizer_when_available(self):
        from post_safety import sanitize_post_text, split_telegram_text
        p=warning()
        original=sanitize_post_text(BASE).text
        out=apply_emma_preview_offline(original,p,offline_preview=True,sanitizer=sanitize_post_text)
        self.assertNotEqual(original,out,'real sanitizer must keep a valid offline preview')
        self.assertEqual(out.count('По данным MeteoAlarm'),1)
        self.assertIn('SUP / кайт / сёрф:',out)
        chunks=split_telegram_text(out)
        self.assertEqual(1,sum(ch.count('По данным MeteoAlarm') for ch in chunks))
        self.assertEqual('NO_POST', p.publication_action)


def _real_format_v2_cases():
    """Evening/morning sources from the existing FORMAT_V2 regression suite."""
    fx = runpy.run_path(str(ROOT / 'tools' / 'test_format_v2_cy.py'), run_name='r8h8_fixtures')
    return [(name, 'morning' if name.startswith('MORNING') else 'evening', value)
            for name, value in sorted(fx.items())
            if name.isupper() and isinstance(value, str) and len(value) > 200
            and ('MORNING' in name or 'EVENING' in name)]


class RealFormatV2PipelineTests(unittest.TestCase):
    """Real FORMAT_V2 -> sanitizer -> seam -> hashtags -> Telegram split."""

    @classmethod
    def setUpClass(cls):
        # Some morning fixtures have no wind line, so FORMAT_V2 would ask
        # weather.get_weather(); keep the cases offline and deterministic.
        import weather
        weather_stub = patch.object(weather, 'get_weather', return_value={})
        weather_stub.start()
        cls.addClassCleanup(weather_stub.stop)
        from format_v2 import build_format_v2
        from post_safety import sanitize_post_text, split_telegram_text, _SAFE_CHUNK_LIMIT
        from safe_test_post import finalize_hashtags_at_end, _CY_CANONICAL_DAILY_HASHTAGS
        cls.build, cls.sanitize, cls.split = map(staticmethod, (
            build_format_v2, sanitize_post_text, split_telegram_text))
        cls.limit = _SAFE_CHUNK_LIMIT
        cls.finalize = staticmethod(lambda text: finalize_hashtags_at_end(
            text, canonical_hashtags=_CY_CANONICAL_DAILY_HASHTAGS))
        cls.cases = _real_format_v2_cases()
        cls.preview = warning()

    def publish_path(self, base):
        out = apply_emma_preview_offline(base, self.preview, offline_preview=True,
                                         sanitizer=self.sanitize)
        return out, self.split(self.finalize(out))

    def assert_lossless(self, base, out, chunks):
        self.assertNotEqual(base, out, 'valid offline preview was not inserted')
        self.assertEqual(1, out.count(self.preview.text))
        self.assertEqual(1, sum(ch.count('По данным MeteoAlarm') for ch in chunks))
        base_lines = [l for l in base.splitlines() if l.strip()]
        out_lines = [l for l in out.splitlines() if l.strip()]
        added = [l for l in self.preview.text.splitlines() if l.strip()]
        # Exactly one contiguous preview block added; every original line kept once, in order.
        at = out_lines.index(added[0])
        self.assertEqual(added, out_lines[at:at + len(added)])
        self.assertEqual(base_lines, out_lines[:at] + out_lines[at + len(added):])
        joined = [l.strip() for ch in chunks for l in ch.splitlines() if l.strip()]
        for line in base_lines:
            if not line.strip().startswith('#'):
                self.assertEqual(base_lines.count(line), joined.count(line.strip()), line)
        for ch in chunks:
            self.assertLessEqual(len(ch), self.limit)

    def test_all_real_fixtures_require_insertion_without_loss(self):
        self.assertGreaterEqual(len(self.cases), 20)
        for name, mode, source in self.cases:
            with self.subTest(case=name):
                base = self.sanitize(self.build('Кипр', mode, source)).text
                # Production default stays an exact no-op.
                self.assertEqual(base, apply_emma_preview_offline(base))
                out, chunks = self.publish_path(base)
                self.assert_lossless(base, out, chunks)
                self.assertEqual('NO_POST', self.preview.publication_action)
                self.assertFalse(any((self.preview.official_warnings_checked, self.preview.clean_verified,
                                      self.preview.city_cleared, self.preview.positive_water_guidance_allowed,
                                      self.preview.national_inventory_complete)))

    def test_deterministic_multi_chunk_telegram_split(self):
        name, mode, source = next(c for c in self.cases if c[0] == 'NORMAL_EVENING')
        lines = self.sanitize(self.build('Кипр', mode, source)).text.split('\n')
        tags = next(i for i, l in enumerate(lines) if l.strip().startswith('#'))
        filler = [f'Справка {i:02d}: нейтральная тестовая строка для детерминированного разбиения поста.'
                  for i in range(80)]
        paragraphs = ['\n'.join(filler[i:i + 5]) + '\n' for i in range(0, 80, 5)]
        base = self.sanitize('\n'.join(lines[:tags] + paragraphs + lines[tags:])).text
        self.assertGreater(len(base), self.limit)
        out, chunks = self.publish_path(base)
        self.assertGreaterEqual(len(chunks), 2)
        self.assert_lossless(base, out, chunks)
        self.assertEqual(1, sum(1 for ch in chunks if self.preview.text in ch),
                         'preview must stay whole inside one chunk')


if __name__ == '__main__':
    unittest.main(verbosity=2)
