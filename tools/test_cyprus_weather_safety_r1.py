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


def _num(pattern: str, text: str) -> Optional[float]:
    m = re.search(pattern, _plain(text), flags=re.I)
    return float(m.group(1).replace(",", ".")) if m else None


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
            '_num': _num,
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

    def test_r3_fails_closed_on_unknown_wmo(self):
        good = dict(region_wmo_known=True, uv_known=True, wind_known=True,
                    gust_known=True, air_known=True, weather_source='live', weather_age_sec=0,
                    official_warnings_checked=True, city_expected_checks=1, city_checks=[{'complete': True}])
        bad = dict(good, region_wmo_known=False)
        self.assertIn('VayboMeter: н/д', self.score('Нет локального сигнала непогоды', bad))
        self.assertIn('10.0/10', self.score('Нет локального сигнала непогоды', good))

    def test_r3_fails_closed_on_stale_or_missing_observations(self):
        good = dict(region_wmo_known=True, uv_known=True, wind_known=True,
                    gust_known=True, air_known=True, weather_source='live', weather_age_sec=0,
                    official_warnings_checked=True, city_expected_checks=1, city_checks=[{'complete': True}])
        for changes in ({'weather_source': 'cache_stale'}, {'air_known': False},
                        {'uv_known': False}, {'wind_known': False}, {'gust_known': False}):
            with self.subTest(changes=changes):
                self.assertIn('VayboMeter: н/д', self.score('Нет локального сигнала непогоды', dict(good, **changes)))

    def test_r3_confirmed_thunder_cap_survives_missing_data(self):
        text = '🌦 Локально: Лимассол — гроза'
        self.assertIn('5.4/10', self.score(text, {}))

    def test_r3_rendered_uv_and_gust_values(self):
        # Real parser, not the existing R1 stub for _cyprus_conditions.
        score_source = (ROOT / 'safe_test_post.py').read_text(encoding='utf-8')
        self.assertIn('УФ(?:-индекс)?', score_source)
        self.assertIn('порывы\\s+(?:до', score_source)
        real_ns = {'re': re, '_plain': _plain, '_num': _num}
        real_conditions = _extract('safe_test_post.py', '_cyprus_conditions', real_ns)
        parsed = real_conditions('☀️ <b>УФ-индекс 8 (Very High)</b>\n💨 Ветер: 13 м/с, порывы 14 м/с')
        self.assertEqual(8.0, parsed['uv'])
        self.assertEqual(13.0, parsed['wind'])
        self.assertEqual(14.0, parsed['gust'])

    def test_r4_official_warnings_unverified_never_perfect(self):
        good = dict(region_wmo_known=True, uv_known=True, wind_known=True,
                    gust_known=True, air_known=True, weather_source='live', weather_age_sec=0,
                    official_warnings_checked=True, city_expected_checks=1, city_checks=[{'complete': True}])
        calm = 'Нет локального сигнала непогоды'
        self.assertIn('10.0/10', self.score(calm, good))
        for unchecked in (False, None, 'true', 0):
            with self.subTest(unchecked=unchecked):
                evidence = dict(good, official_warnings_checked=unchecked)
                result = self.score(calm, evidence)
                self.assertIn('VayboMeter: н/д', result)
                self.assertIn('официальные предупреждения не проверены', result)
                self.assertNotIn('10.0/10', result)
        missing_flag = dict(good)
        del missing_flag['official_warnings_checked']
        self.assertIn('официальные предупреждения не проверены', self.score(calm, missing_flag))

    def test_r4_uncertain_weather_outweighs_official_check(self):
        good = dict(region_wmo_known=True, uv_known=True, wind_known=True,
                    gust_known=True, air_known=True, weather_source='live', weather_age_sec=0,
                    official_warnings_checked=True, city_expected_checks=1, city_checks=[{'complete': True}])
        result = self.score('Нет локального сигнала непогоды', dict(good, air_known=False))
        self.assertIn('погодные данные неполные или устарели', result)
        self.assertNotIn('10.0/10', result)

    def test_r4_thunder_and_gust_cap_even_with_unverified_official(self):
        unverified = dict(official_warnings_checked=False)
        self.assertIn('5.4/10', self.score('🌦 Локально: Лимассол — гроза', unverified))
        self.assertIn('5.4/10', self.score('⚠️ <b>По прогнозу</b>: гроза', unverified))
        self.assertIn('5.4/10', self.score('GUST_16', unverified))

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
        self.assertIn('or _cyprus_city_thunder_signal(_line)', source)
        self.assertIn('if has_thunder and _ru_city(city) not in thunder_cities:', source)
        self.assertIn('if thunder_cities and not storm_region.get("warning"):', source)
        self.assertIn('storm = _cyprus_morning_weather_status(storm_region, bool(thunder_cities))', source)




class WeatherSafetyR6(unittest.TestCase):
    """AST extraction tests run without Open-Meteo, Telegram or cached provider calls."""

    @classmethod
    def setUpClass(cls):
        from datetime import date
        cls.date = date(2026, 10, 19)
        if not hasattr(WeatherSafetyR1, "score"):
            WeatherSafetyR1.setUpClass()
        cls.city_ns = {
            'Dict': Dict, 'Any': Any, 'Optional': Optional,
            'math': __import__('math'),
            '_CY_THUNDER_WMO_CODES': {95, 96, 99},
            '_CY_PRECIP_WMO_CODES': {51, 53, 55, 56, 57, 61, 63, 65, 66, 67, 80, 81, 82, 95, 96, 99},
            'WMO_DESC': {0: 'ясно', 1: 'ясно', 2: 'облачно', 3: 'пасмурно', 45: 'туман',
                         48: 'туман', 51: 'морось', 61: 'дождь', 71: 'снег', 95: 'гроза'},
            '_daily_idx_for_date': lambda wm, tz, d: (
                (wm.get('daily') or {}).get('time', []).index(str(d))
                if str(d) in (wm.get('daily') or {}).get('time', []) else None),
            '_pick': lambda d, *keys, default=None: next((d[k] for k in keys if k in d), default),
            '_number_at': lambda values, idx: (
                float(values[idx]) if isinstance(values, list) and isinstance(idx, int)
                and idx < len(values) and values[idx] is not None else None),
            '_value_at': lambda values, idx: (
                values[idx] if isinstance(values, list) and isinstance(idx, int)
                and idx < len(values) else None),
            '_weather_code_int': lambda value: int(value) if value is not None else None,
            '_source_indices_for_date': lambda times, d, tz: [
                i for i, t in enumerate(times) if str(t).startswith(str(d))
                and 'T' in str(t)],
            '_city_header_metrics_for_date': lambda wm, tz, d: (
                3.0 if any(str(t).startswith(str(d)) for t in wm.get('hourly', {}).get('time', []))
                and any(v is not None for v in wm.get('hourly', {}).get('windspeed_10m', [])) else None,
                None, None, '→',
                6.0 if any(str(t).startswith(str(d)) for t in wm.get('hourly', {}).get('time', []))
                and any(v is not None for v in wm.get('hourly', {}).get('windgusts_10m', [])) else None),
            '_city_daily_metrics_for_date': lambda wm, tz, d: (
                next((wm.get('daily', {}).get(k, [None])[0] for k in ('temperature_2m_max',) if k in wm.get('daily', {})), None),
                wm.get('daily', {}).get('temperature_2m_min', [None])[0],
                wm.get('daily', {}).get('weathercode', [None])[0]),
        }
        cls.evidence = staticmethod(_extract('post_common.py', '_cyprus_city_forecast_evidence', cls.city_ns))

    def _city(self, **overrides):
        import copy
        wm = {
            'daily': {'time': ['2026-10-19'], 'temperature_2m_max': [25],
                      'temperature_2m_min': [18], 'weathercode': [0],
                      'precipitation_sum': [0]},
            'hourly': {'time': ['2026-10-19T12:00'], 'windspeed_10m': [10],
                       'windgusts_10m': [18], 'precipitation_probability': [0]},
            '_vaybometer_weather_meta': {'source': 'live', 'age_sec': 0},
        }
        for section, pairs in overrides.items():
            wm[section].update(pairs)
        return self.evidence('Лимассол', wm, None, self.date)

    def test_r6_complete_city_accepts_true_zero_precipitation(self):
        self.assertTrue(self._city()['complete'])

    def test_r6_missing_city_fields_fail_closed(self):
        cases = (
            {'daily': {'weathercode': [None]}},
            {'daily': {'weathercode': [73]}},
            {'daily': {'temperature_2m_min': [None]}},
            {'hourly': {'windspeed_10m': [None]}},
            {'hourly': {'windgusts_10m': [None]}},
            {'hourly': {'time': ['2026-10-19T06:00', '2026-10-19T12:00'],
                        'windspeed_10m': [10, None], 'windgusts_10m': [None, 18]}},
            {'daily': {'precipitation_sum': [None]}, 'hourly': {'precipitation_probability': [None]}},
            {'hourly': {'time': ['2026-10-20T12:00']}},
            {'_vaybometer_weather_meta': {'source': 'cache_stale'}},
            {'_vaybometer_weather_meta': {'age_sec': 1900}},
        )
        for changed in cases:
            with self.subTest(changed=changed):
                self.assertFalse(self._city(**changed)['complete'])

    def test_r6_city_thunder_is_preserved_even_without_temperature(self):
        result = self._city(daily={'weathercode': [95], 'temperature_2m_max': [None]})
        self.assertTrue(result['thunder'])
        self.assertFalse(result['complete'])

    def test_r6_rating_requires_every_city_and_both_coastal_reads(self):
        good = dict(region_wmo_known=True, uv_known=True, wind_known=True,
                    gust_known=True, air_known=True, weather_source='live',
                    weather_age_sec=0, official_warnings_checked=True)
        dummy = dict(complete=True)
        good['city_expected_checks'] = 3
        good['city_checks'] = [dummy.copy() for _ in range(3)]
        self.assertIn('10.0/10', WeatherSafetyR1.score('Нет локального сигнала непогоды', good))
        for change in ({'city_checks': [dummy.copy(), dict(complete=False), dummy.copy()]},
                       {'city_checks': [dummy.copy(), dummy.copy()]},
                       {'city_checks': []}):
            with self.subTest(change=change):
                bad = dict(good, **change)
                self.assertIn('VayboMeter: н/д', WeatherSafetyR1.score('Нет локального сигнала непогоды', bad))

    def test_r6_thunder_cap_survives_incomplete_city_evidence(self):
        e = dict(city_expected_checks=2, city_checks=[dict(complete=False)])
        self.assertIn('5.4/10', WeatherSafetyR1.score('🌦 Локально: Лимассол — гроза', e))


if __name__ == '__main__':
    unittest.main(verbosity=2)
