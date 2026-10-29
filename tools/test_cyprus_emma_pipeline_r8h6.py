"""R8-H6 offline-only seam and regression checks. No network or Telegram."""
from __future__ import annotations

from dataclasses import replace
from datetime import date
from types import SimpleNamespace
import unittest

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
        try:
            from post_safety import sanitize_post_text, split_telegram_text
        except ImportError:
            self.skipTest('full checkout / actual post_safety not present')
        p=warning()
        original=sanitize_post_text(BASE).text
        out=apply_emma_preview_offline(original,p,offline_preview=True,sanitizer=sanitize_post_text)
        # When sanitizer is unable to preserve exact text, it must refuse to inject.
        if out != original:
            self.assertEqual(out.count('По данным MeteoAlarm'),1)
            self.assertIn('SUP / кайт / сёрф:',out)
            chunks=split_telegram_text(out)
            self.assertTrue(chunks)
            self.assertTrue(any('По данным MeteoAlarm' in ch for ch in chunks))
        self.assertEqual('NO_POST', p.publication_action)


if __name__ == '__main__':
    unittest.main(verbosity=2)
