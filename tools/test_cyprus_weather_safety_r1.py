#!/usr/bin/env python3
"""Offline regression tests for Cyprus weather wording and storm score.

Extracts the named production functions from source without importing network-aware
publication modules. This tests local behavior only: it does not replace the
full repository regression suite or prove weather-provider integration.
"""
from __future__ import annotations

import ast
from pathlib import Path
import re
import unittest
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]


def _extract(path: str, name: str, ns: dict) -> object:
    text = (ROOT / path).read_text(encoding="utf-8")
    match = re.search(rf"^def {re.escape(name)}\(", text, flags=re.M)
    if match is None:
        raise AssertionError(f"missing function {name} in {path}")
    selected = []
    for index, line in enumerate(text[match.start():].splitlines(keepends=True)):
        if index and line.strip() and not line.startswith((' ', '\t')):
            break
        selected.append(line)
    snippet = ''.join(selected).rstrip() + '\n'
    tree = ast.parse(snippet, filename=path)
    exec(compile(tree, path, 'exec'), ns)
    return ns[name]


def _plain(text: str) -> str:
    return re.sub(r"</?b>", "", str(text or "")).strip()


class WeatherSafetyR1(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.format_ns = {
            're': re,
            '_plain': _plain,
            '_MORNING_CITY_PRECIP_RE': re.compile(
                r"^(Никосия|Лимассол|Ларнака|Пафос|Паралимни|Айя-Напа|Тродос)\s*:\s*(.+)$", re.I),
            '_STORM_NEGATION_RE': re.compile(
                r"шторм\w*\s+не\s+ожида|без\s+шторма|штормов\w*\s+предупрежден\w*\s+нет|риск\s+шторма\s+низк", re.I),
            '_STORM_POSITIVE_RE': re.compile(r"\b(?:шторм\w*|шквал\w*)\b", re.I),
        }
        cls.format_ns['_line_has_actual_storm_signal'] = lambda line: (
            not cls.format_ns['_STORM_NEGATION_RE'].search(_plain(line)) and
            bool(cls.format_ns['_STORM_POSITIVE_RE'].search(_plain(line)))
        )
        cls.storm_line = staticmethod(_extract('format_v2.py', '_storm_line', cls.format_ns))
        cls.local_precip = staticmethod(_extract('format_v2.py', '_morning_local_precipitation_line', cls.format_ns))
        cls.score_ns = {
            're': re,
            '_plain': _plain,
            '_CY_STORM_NEGATION_RE': cls.format_ns['_STORM_NEGATION_RE'],
            '_CY_STORM_POSITIVE_RE': cls.format_ns['_STORM_POSITIVE_RE'],
            'visibility_air_penalty': lambda visibility, air: air,
            'visibility_penalty': lambda visibility: 0,
            '_cyprus_visibility_condition': lambda text: 'clear',
            '_cyprus_conditions': lambda text: {
                'warm_t': 26, 'cool_t': 17, 'wind': 2.7,
                'gust': (16 if 'GUST_16' in text else 11), 'uv': None, 'aqi': 29,
            },
            '_cyprus_local_precipitation_kind': lambda text: (
                'storm' if any(l.lower().startswith('🌦 локально:') and 'гроз' in l.lower() for l in text.splitlines())
                else ('precipitation' if any(l.lower().startswith('🌦 локально:') and 'дожд' in l.lower() for l in text.splitlines()) else '')
            ),
            '_score_label': lambda value: ('отлично' if value >= 8.5 else 'хорошо' if value >= 7 else
                                           'с оговорками' if value >= 5.5 else 'бережный режим'),
        }
        cls.has_storm = staticmethod(_extract('safe_test_post.py', '_has_actual_cyprus_storm_signal', cls.score_ns))
        cls.score = staticmethod(_extract('safe_test_post.py', '_cyprus_score_line', cls.score_ns))
        cls.common_ns = {'re': re, 'Optional': Optional, 'Dict': Dict, 'Any': Any}
        cls.city_thunder = staticmethod(_extract('post_common.py', '_cyprus_city_thunder_signal', cls.common_ns))
        cls.weather_status = staticmethod(_extract('post_common.py', '_cyprus_morning_weather_status', cls.common_ns))

    def test_cyprus_oct14_score_not_excellent(self):
        text = '🌦 Локально: Лимассол — гроза (риск осадков до 90%) · Пафос — гроза (риск осадков до 95%)'
        self.assertIn('5.4/10 — бережный режим; локальная гроза', self.score(text))

    def test_structured_forecast_thunder_score_is_capped(self):
        self.assertIn('5.4/10 — бережный режим', self.score('⚠️ <b>По прогнозу</b>: гроза'))

    def test_other_storm_warning_score_is_capped(self):
        self.assertIn('5.4/10 — бережный режим', self.score('⚠️ По прогнозу: сильный дождь'))

    def test_gust_warning_score_is_capped(self):
        self.assertIn('бережный режим', self.score('GUST_16'))

    def test_clear_day_still_has_normal_score(self):
        self.assertIn('10.0/10 — отлично', self.score('Нет локального сигнала непогоды'))

    def test_astro_metaphor_is_not_storm_evidence(self):
        self.assertIn('10.0/10 — отлично', self.score('🌙 Эмоциональный шторм без фактической грозы'))

    def test_rain_only_not_treated_as_thunder(self):
        self.assertIn('9.3/10 — отлично', self.score('🌦 Локально: Ларнака — дождь'))

    def test_storm_negation_not_a_positive_warning(self):
        self.assertEqual('', self.storm_line(['🔎 Итого: воздух 🟢 • без шторма • Kp умеренно']))

    def test_explicit_storm_warning_kept(self):
        self.assertEqual('⚠️ Штормовой ветер', self.storm_line(['⚠️ Штормовой ветер']))

    def test_structured_city_forecast_warning_kept(self):
        self.assertEqual('⚠️ <b>По прогнозу</b>: гроза', self.storm_line(['⚠️ <b>По прогнозу</b>: гроза']))

    def test_city_storm_probability_is_not_thunder_probability(self):
        rows = ['<b>Лимассол</b>: 25/18 °C • ⛈ гроза • риск осадков до 90%',
                '<b>Пафос</b>: 24/19 °C • ⛈ гроза • риск осадков до 95%']
        summary = self.local_precip(rows)
        self.assertIn('Лимассол — гроза (риск осадков до 90%)', summary)
        self.assertIn('Пафос — гроза (риск осадков до 95%)', summary)
        self.assertNotIn('гроза до 90%', summary)

    def test_rain_probability_preserved(self):
        self.assertEqual('🌦 Локально: Ларнака — дождь до 40%',
                         self.local_precip(['Ларнака: 🌧 дождь • риск осадков до 40%']))

    def test_no_alert_data_never_claims_no_storm(self):
        status = self.weather_status({'warning': False}, False)
        self.assertEqual('официальные предупреждения не проверены', status)
        self.assertNotIn('без шторма', status)

    def test_city_thunder_outweighs_region_negative(self):
        self.assertEqual('локальные грозы по прогнозу', self.weather_status({'warning': False}, True))

    def test_region_weather_warning(self):
        self.assertEqual('риск опасной погоды по прогнозу', self.weather_status({'warning': True}, False))

    def test_city_thunder_signal(self):
        self.assertTrue(self.city_thunder('<b>Лимассол</b>: 25/18 °C • ⛈ гроза • риск осадков до 90%'))
        self.assertFalse(self.city_thunder('<b>Лимассол</b>: 25/18 °C • ☀️ ясно'))
        self.assertFalse(self.city_thunder('Лимассол: без грозы'))

    def test_city_signal_wired_into_message(self):
        source = (ROOT / 'post_common.py').read_text('utf-8')
        self.assertIn('if _cyprus_city_thunder_signal(_line):', source)
        self.assertIn('if thunder_cities and not storm_region.get("warning"):', source)
        self.assertIn('storm = _cyprus_morning_weather_status(storm_region, bool(thunder_cities))', source)


if __name__ == '__main__':
    unittest.main(verbosity=2)
