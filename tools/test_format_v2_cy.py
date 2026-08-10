#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for compact Cyprus FORMAT_V2 evening posts."""
from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import types
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

imghdr_stub = types.ModuleType("imghdr")
imghdr_stub.what = lambda file, h=None: None
sys.modules.setdefault("imghdr", imghdr_stub)

pendulum_stub = types.ModuleType("pendulum")
pendulum_stub.DateTime = object
sys.modules.setdefault("pendulum", pendulum_stub)

telegram_stub = types.ModuleType("telegram")
telegram_stub.Bot = object
telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
sys.modules.setdefault("telegram", telegram_stub)

from format_v2 import build_evening_format_v2, build_format_v2, build_morning_format_v2  # noqa: E402
from post_safety import sanitize_post_text, split_telegram_text  # noqa: E402
from safe_test_post import (  # noqa: E402
    _apply_astro_cleanup,
    _apply_cyprus_sensor_cleanup,
    _apply_format_v2_test_polish,
    _cyprus_evening_score_line,
    _cy_image_caption,
    _insert_main_nuance,
    finalize_hashtags_at_end,
)


MORNING_WITH_QUAKE = """<b>🌅 Кипр: погода на сегодня (27.06.2026)</b>
Доброе утро. Теплее всего — Никосия (32°), прохладнее — Тродос (24°).
☀️ <b>УФ-индекс 7 (High)</b>: SPF, вода и тень.
🏭 Воздух: 🟢 чисто.
🌍 Сейсмика 24ч: 1 микрособытие M0.9–1.9; заметных событий M2.0+ не найдено.
🧲 Космопогода: Kp 2.0 (спокойно)
🌇 Закат сегодня: 20:05
✅ Сегодня: прогулка до полудня.
#Кипр #погода #здоровье #Никосия #Тродос
"""


NORMAL_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 8.6/10 — комфортно для обычных дел и прогулок.
🏖 <b>Морские города</b>
Лимассол: 29/22 °C • ясно • 💨 4 м/с
Ларнака: 30/22 °C • ясно • 💨 4 м/с
Айя-Напа: 29/23 °C • ясно • 💨 5 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 32/21 °C • ясно
Тродос: 24/15 °C • ясно
———
🌅 Рассвет завтра: 05:35
🌇 Закат завтра: 20:05
🌙 Растущая Луна, ♏ (86%)
💚 В плюсе: порядок, прогулки, мягкий режим.
#Кипр #погода #здоровье #Никосия #Тродос
"""


RAIN_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 6.7/10 — рабочий день; локальные осадки, порывы у моря.
⚠️ <b>Штормовое предупреждение</b>: местами гроза и порывы до 15 м/с.
🏖 <b>Морские города</b>
Пафос: 25/19 °C • 🌧 дождь • 💨 7 м/с • порывы до 15 м/с
Лимассол: 27/20 °C • 🌦 местами дождь • 💨 6 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 29/18 °C • 🌦 местами дождь
Тродос: 18/10 °C • 🌧 дождь
———
🌅 Рассвет завтра: 05:35
🌙 Убывающая Луна, ♐ (56%)
💚 В плюсе: спокойные дела, восстановление.
#Кипр #погода #здоровье #Никосия #Тродос
"""


HEAT_WIND_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 6.9/10 — рабочий день; жара, порывы у моря.
🏖 <b>Морские города</b>
Лимассол: 33/24 °C • ясно • 💨 7 м/с • порывы до 14 м/с
Ларнака: 34/24 °C • ясно • 💨 6 м/с • порывы до 13 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 37/23 °C • ясно
Тродос: 29/20 °C • ясно
———
🌅 Рассвет завтра: 05:35
🌙 Растущая Луна, ♏ (86%)
💚 В плюсе: порядок, прогулки, мягкий режим.
#Кипр #погода #здоровье #Никосия #Тродос
"""


CAUTION_SCORE_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 7.4/10 — хорошо; сильная жара, порывы у моря.
🏖 <b>Морские города</b>
Лимассол: 35/25 °C • ясно • 💨 7 м/с • порывы до 15 м/с
Ларнака: 36/25 °C • ясно • 💨 8 м/с • порывы до 14 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 39/27 °C • ясно
———
🌅 Рассвет завтра: 05:35
🌇 Закат завтра: 20:05
🌕 Полнолуние в ♑ — пик эмоций и результатов.
✨ 100% освещённости — эмоции ярче обычного.
⚠️ Общий фон: не перегружать день.
💚 В плюсе: завершение, договорённости.
#Кипр #погода #здоровье #Никосия #Тродос
"""


RICH_ASTRO_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 8.2/10 — спокойный день.
🏖 <b>Морские города</b>
Лимассол: 29/22 °C • ясно • 💨 4 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 32/21 °C • ясно
———
🌅 Рассвет завтра: 05:37
🌕 Полнолуние в ♐ — подходит для укрепления планов и постепенного роста.
✨ 92% освещённости — эмоции ярче обычного, выбирай спокойный темп.
✅ Общий фон: спокойнее решать дела по одному.
💚 В плюсе: 🧭 планы, ✈️ дороги, 📚 обучение.
#Кипр #погода #здоровье #Никосия #Тродос
"""


MALFORMED_ZODIAC_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 8.2/10 — спокойный день.
🏖 <b>Морские города</b>
Лимассол: 29/22 °C • ясно • 💨 4 м/с
———
🌅 Рассвет завтра: 05:37
🌕 Полнолуние в ♑е — пик эмоций и результатов — лучше завершать, чем начинать.
✨ 100% освещённости — эмоции ярче обычного.
💚 В плюсе: завершение, договорённости.
#Кипр #погода #здоровье #Никосия #Тродос
"""


NEW_MOON_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 7.1/10 — обычный день.
🏖 <b>Морские города</b>
Ларнака: 30/22 °C • ясно
———
🌅 Рассвет завтра: 05:37
🌑 Новолуние в ♋ — лучше начинать мягко и без рывков.
⚠️ Общий фон: не перегружай вечер решениями.
⚫️ VoC: короткая пауза для рутины.
#Кипр #погода #здоровье #Никосия #Тродос
"""


SAFE_ASTRO_PIPELINE_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 8.2/10 — спокойный день.
🏖 <b>Морские города</b>
Лимассол: 29/22 °C • ясно • 💨 4 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 32/21 °C • ясно
———
🌅 Рассвет завтра: 05:37
🌕 Полнолуние в ♐ — 96% освещённости.
✨ 96% освещённости — эмоции ярче обычного.
⚠️ Общий фон: не перегружать день.
💚 В плюсе: 🧭 планы, ✈️ дороги, 📚 обучение.
⚫️ VoC 12:00–13:20 — без новых стартов.
#Кипр #погода #здоровье #Никосия #Тродос
"""


SAFE_ASTRO_PIPELINE_NEW_MOON = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 7.1/10 — обычный день.
🏖 <b>Морские города</b>
Ларнака: 30/22 °C • ясно
———
🌅 Рассвет завтра: 05:37
🌑 Новолуние в ♋ — лучше начинать мягко и без рывков.
⚫️ VoC 12:00–13:20 — без новых стартов.
#Кипр #погода #здоровье #Никосия #Тродос
"""


AIR_SENSOR_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 7.0/10 — обычный день.
🏖 <b>Морские города</b>
Лимассол: 29/22 °C • ясно
———
🏞 <b>Континентальные города</b>
Никосия: 32/21 °C • ясно
———
🏭 Воздух: AQI 125 (высокий) • PM₂.₅ 20 / PM₁₀ 69
🏭 Воздух по городам: Никосия 🟠 PM₁₀ · Лимассол 🟡 · Ларнака 🟡 · Пафос 🟢
🧪 Частный датчик: выше обычной точки; смотрим динамику.
🧪 Safecast CY: 0.18 μSv/h — выше обычного, но без тревоги.
🌅 Рассвет завтра: 05:37
🌕 Полнолуние в ♐ — 96% освещённости.
✨ 96% освещённости — эмоции ярче обычного.
✅ Общий фон: спокойнее решать дела по одному.
💚 В плюсе: 🧭 планы, ✈️ дороги, 📚 обучение.
#Кипр #погода #здоровье #Никосия #Тродос
"""

SCORE_DUP_REASONS_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 6.4/10 — с оговорками; сильная жара, порывы у моря, ветер у моря.
🏖 <b>Морские города</b>
Лимассол: 33/25 °C • ясно • 💨 6 м/с • порывы до 14 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 36/25 °C • ясно
———
🌅 Рассвет завтра: 05:37
🌕 Полнолуние в ♐ — 96% освещённости.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""

LOW_AQI_FOG_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 7.1/10 — обычный день.
🏖 <b>Морские города</b>
Ларнака: 29/23 °C • 🌫 локальная утренняя дымка/туман • 💨 3 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 31/22 °C • ясно
———
🌫 Видимость: завтра утром в Ларнаке (прогноз на 06:00) влажная дымка, местами около 2200 м; на дорогах и у моря видимость снижена.
🏭 Воздух: AQI 48 (низкий) • PM₂.₅ 12 / PM₁₀ 19
🌅 Рассвет завтра: 05:37
🌕 Полнолуние в ♐ — 96% освещённости.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""

DUST_HAZE_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 6.9/10 — обычный день.
🏖 <b>Морские города</b>
Ларнака: 29/23 °C • пылевая дымка • 💨 3 м/с
———
🏭 Воздух: AQI 125 (высокий) • PM₂.₅ 20 / PM₁₀ 69
🌅 Рассвет завтра: 05:37
🌕 Полнолуние в ♐ — 96% освещённости.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""

SURF_NO_WAVE_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 7.1/10 — обычный день.
🏖 <b>Морские города</b>
Лимассол: 29/23 °C • ясно • 💨 5 м/с (W/cross) • 🌊 27
🧜‍♂️ Отлично: Серф (западный ветер, вдоль берега)
———
🌅 Рассвет завтра: 05:37
🌕 Полнолуние в ♐ — 96% освещённости.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""

SURF_WITH_WAVE_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 7.1/10 — обычный день.
🏖 <b>Морские города</b>
Лимассол: 29/23 °C • ясно • 💨 5 м/с (W/cross) • 🌊 27 • 1.2 м
🧜‍♂️ Отлично: Серф (западный ветер, вдоль берега)
———
🌅 Рассвет завтра: 05:37
🌕 Полнолуние в ♐ — 96% освещённости.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""


INTEGRATED_LOCAL_RAIN_GUSTS_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 8.1/10 — хорошо для обычных дел.
🏖 <b>Морские города</b>
Ларнака: 30/25 °C • переменная облачность • 💨 7 м/с • порывы до 12 м/с
Лимассол: 30/23 °C • переменная облачность • 💨 8 м/с • порывы до 11 м/с
Айя-Напа: 29/25 °C • облачно с прояснениями • 💨 6 м/с • порывы до 10 м/с
Пафос: 28/24 °C • переменная облачность • 💨 6 м/с • порывы до 9 м/с
🧜‍♂️ Отлично: Серф (западный ветер, вдоль берега)
———
🏞 <b>Континентальные города</b>
Никосия: 33/24 °C • жарко
Тродос: 25/18 °C • возможна гроза в горах
———
🏭 Воздух: AQI 48 (низкий) • PM₂.₅ 12 / PM₁₀ 19
🌅 Рассвет завтра: 05:37
🌇 Закат завтра: 20:05
🌖 Убывающая Луна в ♐ — мягкий вечерний ритм.
✨ 92% освещённости — Луна яркая.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""

CITY_AIR_BROKEN_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 7.0/10 — обычный день.
🏖 <b>Морские города</b>
Ларнака: 29/23 °C • ясно
———
🏭 Воздух: AQI 48 (низкий) • PM₂.₅ 12 / PM₁₀ 19
🏭 Воздух по городам: Никосия 🟢 · Лимассол 🟢 · Ларнака 🟡 PM₂.₅ 18 Пафос 🟠 PM₂.₅ 28 · Айя-Напа 🟢
🌅 Рассвет завтра: 05:37
🌕 Полнолуние в ♐ — 96% освещённости.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""


GENERIC_UV_WARNING_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 8.1/10 — хорошо для обычных дел.
⚠️ Предупреждение: высокий УФ.
🏖 <b>Морские города</b>
Лимассол: 30/23 °C • ясно • 💨 5 м/с • порывы до 10 м/с
———
🌅 Рассвет завтра: 05:37
🌕 Полнолуние в ♐ — 96% освещённости.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""


RAIN_WARNING_NO_STORM_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 7.0/10 — рабочий день.
⚠️ Предупреждение: местами дождь.
🏖 <b>Морские города</b>
Пафос: 27/21 °C • местами дождь • 💨 5 м/с • порывы до 9 м/с
———
🌅 Рассвет завтра: 05:37
🌙 Растущая Луна в ♐ — спокойный ритм.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""


GUST_STORM_NO_WORD_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 6.8/10 — рабочий день.
🏖 <b>Морские города</b>
Лимассол: 29/22 °C • ясно • 💨 7 м/с • порывы до 17 м/с
———
🌅 Рассвет завтра: 05:37
🌙 Растущая Луна в ♐ — спокойный ритм.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""


NEGATED_STORM_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 7.8/10 — хорошо для обычных дел.
⚠️ Предупреждение о плохой видимости: штормовых предупреждений нет.
🏖 <b>Морские города</b>
Ларнака: 29/23 °C • ясно • 💨 5 м/с • порывы до 8 м/с
———
🌅 Рассвет завтра: 05:37
🌙 Растущая Луна в ♐ — спокойный ритм.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""


CRITICAL_SAFECAST_EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 6.8/10 — обычный день.
🏖 <b>Морские города</b>
Ларнака: 30/22 °C • ясно
———
🧪 Safecast CY: 🔴 alert 0.42 μSv/h — проверить официальные сообщения.
🌅 Рассвет завтра: 05:37
🌑 Новолуние в ♋ — лучше начинать мягко и без рывков.
💚 В плюсе: дом, забота.
#Кипр #погода #здоровье #Никосия #Тродос
"""


MORNING_ASTRO = """<b>🌅 Кипр: погода на сегодня (27.06.2026)</b>
Доброе утро. Теплее всего — Никосия (32°), прохладнее — Тродос (24°).
☀️ <b>УФ-индекс 7 (High)</b>: SPF, вода и тень.
🏭 Воздух: 🟢 чисто.
🌕 Почти полная Луна в ♐ — подходит для укрепления планов.
✨ 99% освещённости — эмоции ярче обычного, выбирай спокойный темп.
✅ Общий фон: держи спокойный ритм.
🌇 Закат сегодня: 20:05
✅ Сегодня: прогулка до полудня.
#Кипр #погода #здоровье #Никосия #Тродос
"""


MORNING_SEA_ROWS = """<b>🌅 Кипр: погода на сегодня (27.06.2026)</b>
Доброе утро. Теплее всего — Никосия (37°), прохладнее — Пафос (28°).
☀️ <b>УФ-индекс 9 (Very High)</b>: SPF 50, тень 11–16.
🏭 Воздух: 🟢 чисто.
Ларнака: 31/24 °C • ☁️ обл • 💨 3.4 м/с • 🌊 27
Лимассол: 31/23 °C • ☁️ обл • 💨 2.3 м/с • 🌊 27 • 0.2 м
Айя-Напа: 30/23 °C • 🌫 туман • 💨 2.5 м/с • 🌊 27
🌇 Закат сегодня: 20:05
✅ Сегодня: прогулка до полудня.
#Кипр #погода #здоровье #Никосия #Тродос
"""


def cy_evening_normal_no_generic_confidence() -> None:
    text = build_evening_format_v2("Кипр", NORMAL_EVENING)
    assert "🎯 <b>Уверенность прогноза</b>" not in text
    assert "🎯 Уверенность:" not in text


def cy_evening_normal_no_island_correction() -> None:
    text = build_evening_format_v2("Кипр", NORMAL_EVENING)
    assert "🌊 <b>Островная поправка</b>" not in text


def cy_evening_no_old_conclusion_or_recommendations() -> None:
    text = build_evening_format_v2("Кипр", NORMAL_EVENING)
    assert "📌 <b>Вывод</b>" not in text
    assert "✅ <b>Рекомендации</b>" not in text


def cy_evening_has_one_final_plan() -> None:
    text = build_evening_format_v2("Кипр", NORMAL_EVENING)
    assert text.count("✅ План завтра:") == 1


def cy_evening_preserves_weather_blocks() -> None:
    text = build_evening_format_v2("Кипр", NORMAL_EVENING)
    assert "🌊 <b>Побережье</b>" in text
    assert "Лимассол: 29/22 °C" in text
    assert "🏙 <b>Центр и горы</b>" in text
    assert "Никосия: 32/21 °C" in text
    assert "Тродос: 24/15 °C" in text


def cy_evening_preserves_compact_astro() -> None:
    text = build_evening_format_v2("Кипр", NORMAL_EVENING)
    lines = text.splitlines()
    start = lines.index("☀️ <b>Солнце, Луна и ритм завтра</b>")
    block = [line for line in lines[start:start + 7] if line.strip()]
    assert "🌅 Рассвет завтра: 05:35" in block
    assert "🌇 Закат завтра: 20:05" in block
    assert "🌙 Растущая Луна, ♏ (86%)" in block
    assert "💚 В плюсе: порядок, прогулки, мягкий режим." in block
    assert len(block) <= 7


def cy_evening_caution_score_softens_good_wording() -> None:
    text = build_evening_format_v2("Кипр", CAUTION_SCORE_EVENING)
    assert "✨ VayboMeter завтра: 5.9/10 — с оговорками; жара и порывы у моря." in text
    assert "7.4/10" not in text
    assert "хорошо; сильная жара" not in text
    assert "☀️ <b>Солнце, Луна и ритм завтра</b>" in text
    assert "🌇 Закат завтра: 20:05" in text
    assert [line for line in text.splitlines() if line.strip()][-1] == "#Кипр #погода #здоровье #Никосия #Тродос"


def cy_evening_score_reasons_are_semantically_deduped() -> None:
    text = build_evening_format_v2("Кипр", SCORE_DUP_REASONS_EVENING)
    assert "✨ VayboMeter завтра: 6.4/10 — с оговорками; сильная жара и порывы у моря." in text
    score_line = next(line for line in text.splitlines() if line.startswith("✨ VayboMeter"))
    assert score_line.count("порывы у моря") == 1
    assert "ветер у моря" not in score_line
    assert "6.4/10" in score_line


def cy_evening_preserves_moon_illumination_and_advice() -> None:
    text = build_evening_format_v2("Кипр", RICH_ASTRO_EVENING)
    assert "🌅 Рассвет завтра: 05:37" in text
    assert "🌕 Полнолуние в ♐ — подходит для укрепления планов и постепенного роста." in text
    assert "✨ 92% освещённости — эмоции ярче обычного, выбирай спокойный темп." in text
    assert "✅ Общий фон: спокойнее решать дела по одному." in text
    assert "💚 В плюсе: 🧭 планы, ✈️ дороги, 📚 обучение." in text
    assert text.count("🌕 Полнолуние") == 1


def cy_evening_normalizes_zodiac_symbol_suffix() -> None:
    text = build_evening_format_v2("Кипр", MALFORMED_ZODIAC_EVENING)
    assert "🌕 Полнолуние в ♑ — пик эмоций и результатов — лучше завершать, чем начинать." in text
    assert "в ♑е" not in text
    assert not re.search(r"в\s+[♈♉♊♋♌♍♎♏♐♑♒♓][а-яё]+", text, flags=re.I)


def cy_evening_preserves_new_moon_and_voc() -> None:
    text = build_evening_format_v2("Кипр", NEW_MOON_EVENING)
    assert "🌑 Новолуние в ♋ — лучше начинать мягко и без рывков." in text
    assert "⚠️ Общий фон: не перегружай вечер решениями." in text
    assert "⚫️ VoC: короткая пауза для рутины." in text
    assert text.count("🌑 Новолуние") == 1


def cy_morning_preserves_moon_and_illumination() -> None:
    text = build_morning_format_v2("Кипр", MORNING_ASTRO)
    assert "🌕 Почти полная Луна в ♐ — 99% освещённости." in text
    assert "✨ 99% освещённости" not in text
    assert "✅ Общий фон: держи спокойный ритм." in text
    assert text.count("🌕 Почти полная Луна") == 1


def cy_morning_sea_summary_uses_coastal_rows_not_sunset() -> None:
    text = build_morning_format_v2("Кипр", MORNING_SEA_ROWS)
    assert "🌊 Море: средняя вода 27°C." in text
    assert "🌊 Море: вода 20°C" not in text
    assert "20:05" in text


def _safe_test_evening_pipeline(source: str) -> str:
    env_names = ("FORMAT_V2", "FORMAT_V2_ASTRO_CLEANUP", "FORMAT_V2_TEST_POLISH", "FORMAT_V2_MAIN_NUANCE")
    old_env = {name: os.environ.get(name) for name in env_names}
    try:
        for name in env_names:
            os.environ[name] = "1"
        text = build_format_v2("Кипр", "evening", source)
        text = _apply_format_v2_test_polish(text)
        text = _insert_main_nuance(text)
        text = _apply_astro_cleanup(text)
        text = _apply_cyprus_sensor_cleanup(text)
        return sanitize_post_text(text).text
    finally:
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def cy_evening_safe_pipeline_preserves_moon_illumination_and_plus() -> None:
    text = _safe_test_evening_pipeline(SAFE_ASTRO_PIPELINE_EVENING)
    assert "🌕 Полнолуние" in text
    assert "✨ 96% освещённости" in text
    assert "💚 В плюсе: 🧭 планы, ✈️ дороги, 📚 обучение." in text
    astro_lines = text.split("☀️ <b>Солнце, Луна и ритм завтра</b>", 1)[1].split("✅ План завтра:", 1)[0].splitlines()
    astro_lines = [line for line in astro_lines if line.strip()]
    assert len(astro_lines) >= 4
    assert len(astro_lines) <= 6
    assert "🌅 Рассвет завтра: 05:37" in astro_lines
    assert "⚫️ VoC 12:00–13:20 — без новых стартов." in astro_lines


def cy_evening_safe_pipeline_preserves_new_moon_and_voc() -> None:
    text = _safe_test_evening_pipeline(SAFE_ASTRO_PIPELINE_NEW_MOON)
    assert "🌑 Новолуние в ♋ — лучше начинать мягко и без рывков." in text
    assert "⚫️ VoC 12:00–13:20 — без новых стартов." in text


def cy_evening_current_air_replaces_sensor_without_tomorrow_advice() -> None:
    text = _safe_test_evening_pipeline(AIR_SENSOR_EVENING)
    assert "🏭 Воздух: AQI 125 (высокий) • PM₂.₅ 20 / PM₁₀ 69" in text
    assert "🏭 Воздух по городам: Никосия 🟠 (PM₁₀) · Лимассол 🟡 · Ларнака 🟡 · Пафос 🟢" in text
    assert "🧭 Главное завтра: пыль/дымка влияют" not in text
    assert "😷 Воздух неидеален:" not in text
    assert "окна лучше держать закрытыми" not in text
    assert "Частный датчик" not in text
    assert "Safecast CY: 0.18" not in text
    assert "🧪" not in text
    assert "🌕 Полнолуние" in text
    assert "✨ 96% освещённости" in text
    assert "✅ Общий фон:" in text
    assert "💚 В плюсе:" in text


def cy_evening_low_aqi_haze_is_visibility_not_poor_air() -> None:
    text = build_evening_format_v2("Кипр", LOW_AQI_FOG_EVENING)
    assert "🧭 Главное завтра: утром местами дымка/туман; на дороге и у побережья лучше проверить видимость." in text
    assert "⚠️ Нюанс: воздух по текущим данным чистый, но локальная дымка может ухудшать видимость." in text
    assert "при дымке/пыли чувствительным людям" not in text
    assert "сократить активность на улице" not in text
    assert "😷 Воздух неидеален" not in text
    assert text.splitlines()[-1] == "#Кипр #погода #здоровье #Никосия #Тродос"


def cy_evening_unstructured_dust_text_does_not_create_air_warning() -> None:
    text = build_evening_format_v2("Кипр", DUST_HAZE_EVENING)
    assert "🧭 Главное завтра: пыль/дымка влияют" not in text
    assert "⚠️ Нюанс: при пыли/дыме" not in text
    assert "😷 Воздух неидеален:" not in text
    assert "окна лучше держать закрытыми" not in text


def cy_evening_surf_without_wave_is_not_excellent() -> None:
    text = _safe_test_evening_pipeline(SURF_NO_WAVE_EVENING)
    assert "Отлично: Серф" not in text
    assert "Отлично: Сёрф" not in text
    assert "🏄 Серф: данных для уверенной оценки недостаточно; проверить спот перед выездом." in text
    assert text.splitlines()[-1] == "#Кипр #погода #здоровье #Никосия #Тродос"


def cy_evening_surf_with_valid_wave_is_cautiously_positive() -> None:
    text = _safe_test_evening_pipeline(SURF_WITH_WAVE_EVENING)
    assert "Отлично: Серф" not in text
    assert "🏄 Серф: есть рабочие окна по волне; проверить конкретный спот." in text


def cy_evening_city_air_line_is_compact_and_parenthesized() -> None:
    text = _safe_test_evening_pipeline(CITY_AIR_BROKEN_EVENING)
    expected = "🏭 Воздух по городам: Никосия 🟢 · Лимассол 🟢 · Ларнака 🟡 (PM₂.₅ 18) · Пафос 🟠 (PM₂.₅ 28) · Айя-Напа 🟢"
    assert expected in text
    assert "Ларнака 🟡 PM₂.₅ 18 Пафос" not in text
    assert text.count("🏭 Воздух по городам:") == 1


def cy_evening_integrated_guidance_is_not_repetitive() -> None:
    text = _safe_test_evening_pipeline(INTEGRATED_LOCAL_RAIN_GUSTS_EVENING)
    lines = text.splitlines()
    score_line = next(line for line in lines if line.startswith("✨ VayboMeter"))
    assert score_line.count(";") == 1
    assert "8.1/10" not in score_line
    assert "6." in score_line or "7." in score_line
    assert text.count("⚠️ Главный нюанс:") + text.count("⚠️ Нюанс:") == 1
    assert text.count("🎯 Уверенность:") == 1
    assert text.count("✅ План завтра:") == 1
    assert text.count("осадки возможны локально") == 1
    assert text.count("проверить") == 1
    assert "ветер/осадки лучше проверить утром" not in text
    assert "сверить осадки" not in text
    assert "Лучшее окно" not in text
    assert text.count("🧭 Главное завтра:") == 1
    assert "🧭 Главное завтра: день неоднородный по острову." in text
    assert "💬 Настрой на завтра:" not in text
    assert "💬 По ощущениям:" not in text
    assert "🏄 Серф: данных для уверенной оценки недостаточно; проверить спот перед выездом." in text
    assert "Отлично: Серф" not in text
    assert text.splitlines()[-1] == "#Кипр #погода #здоровье #Никосия #Тродос"


def cy_evening_generic_warning_does_not_trigger_storm() -> None:
    text = build_evening_format_v2("Кипр", GENERIC_UV_WARNING_EVENING)
    score_line = _cyprus_evening_score_line(GENERIC_UV_WARNING_EVENING)
    assert "главный фактор — предупреждение" not in text
    assert "гибкий маршрут, проверка ветра утром" not in text
    assert "⚠️ <b>Предупреждение</b>" not in text
    assert "предупреждение" not in score_line.lower()
    assert "шторм" not in text.lower()


def cy_evening_rain_warning_is_rain_not_storm() -> None:
    text = build_evening_format_v2("Кипр", RAIN_WARNING_NO_STORM_EVENING)
    assert "🧭 Главное завтра: день неоднородный по острову." in text
    assert "⚠️ Главный нюанс: осадки возможны локально" in text
    assert "✅ План завтра: запасной indoor-вариант; радар — перед выездом." in text
    assert "главный фактор — предупреждение" not in text
    assert "гибкий маршрут, проверка ветра утром" not in text
    assert "⚠️ <b>Предупреждение</b>" not in text


def cy_evening_gust_17_triggers_storm_without_word() -> None:
    text = build_evening_format_v2("Кипр", GUST_STORM_NO_WORD_EVENING)
    assert "🧭 Главное завтра: сильные порывы у моря задают режим дня." in text
    assert "✅ План завтра: защищённый берег, короткие перемещения и без лишнего риска у открытого моря." in text
    assert "⚠️ <b>Предупреждение</b>" in text


def cy_evening_negated_storm_phrase_is_nonstorm() -> None:
    text = build_evening_format_v2("Кипр", NEGATED_STORM_EVENING)
    assert "главный фактор — предупреждение" not in text
    assert "гибкий маршрут, проверка ветра утром" not in text
    assert "⚠️ <b>Предупреждение</b>" not in text


def cy_evening_critical_safecast_is_explicitly_labeled() -> None:
    text = _safe_test_evening_pipeline(CRITICAL_SAFECAST_EVENING)
    assert "🧪 Safecast CY: 🔴 alert 0.42 μSv/h — проверить официальные сообщения." in text
    assert "Частный датчик" not in text


def cy_evening_uncertain_has_short_confidence_line() -> None:
    text = build_evening_format_v2("Кипр", RAIN_EVENING)
    assert "🎯 Уверенность: температура надёжна; по горам и порывам возможны уточнения утром." in text
    assert "🎯 <b>Уверенность прогноза</b>" not in text


def cy_evening_title_is_compact() -> None:
    text = build_evening_format_v2("Кипр", NORMAL_EVENING)
    assert text.splitlines()[0] == "<b>🌅 Кипр завтра (27.06.2026)</b>"


def _evening_visibility_score_fixture(visibility_line: str = "", air_line: str = "") -> str:
    return "\n".join(
        line
        for line in (
            "<b>🌅 Кипр завтра (17.07.2026)</b>",
            "🏙 Лимассол — 28/22 °C • ясно",
            air_line,
            visibility_line,
            "✅ План завтра: обычные дела и прогулки.",
            "#Кипр #погода",
        )
        if line
    )


def _evening_score_value(text: str) -> float:
    line = _cyprus_evening_score_line(text)
    match = re.search(r"VayboMeter завтра:\s*(\d+(?:[\.,]\d+)?)", line)
    assert match, line
    return float(match.group(1).replace(",", "."))


def cy_evening_current_aqi_does_not_change_tomorrow_score() -> None:
    without_air = _evening_visibility_score_fixture()
    with_current_air = _evening_visibility_score_fixture(
        air_line="🏭 Воздух сейчас: AQI 150 • PM₂.₅ 42 • PM₁₀ 91"
    )
    assert _evening_score_value(with_current_air) == _evening_score_value(without_air)
    formatted = build_evening_format_v2("Кипр", with_current_air)
    assert "🏭 Воздух сейчас: AQI 150" in formatted


def cy_evening_current_aqi_plus_fog_uses_only_fog_penalty() -> None:
    clear = _evening_visibility_score_fixture(
        air_line="🏭 Воздух сейчас: AQI 150 • PM₂.₅ 42 • PM₁₀ 91"
    )
    fog = _evening_visibility_score_fixture(
        visibility_line="🌫 Видимость: завтра утром туман, местами около 900 м; дальние объекты плохо различимы.",
        air_line="🏭 Воздух сейчас: AQI 150 • PM₂.₅ 42 • PM₁₀ 91",
    )
    assert round(_evening_score_value(clear) - _evening_score_value(fog), 1) == 0.5


def cy_evening_current_aqi_plus_reduced_visibility_uses_point_two() -> None:
    clear = _evening_visibility_score_fixture(
        air_line="🏭 Воздух сейчас: AQI 150 • PM₂.₅ 42 • PM₁₀ 91"
    )
    reduced = _evening_visibility_score_fixture(
        visibility_line="🌫 Видимость: завтра утром местами снижена, местами около 4000 м; нужна дополнительная дистанция.",
        air_line="🏭 Воздух сейчас: AQI 150 • PM₂.₅ 42 • PM₁₀ 91",
    )
    assert round(_evening_score_value(clear) - _evening_score_value(reduced), 1) == 0.2


def cy_evening_explicit_forecast_aqi_can_affect_tomorrow_score() -> None:
    clear = _evening_visibility_score_fixture()
    forecast_air = _evening_visibility_score_fixture(
        air_line="🏭 Воздух завтра утром: AQI 150 • PM₂.₅ 42 • PM₁₀ 91"
    )
    assert round(_evening_score_value(clear) - _evening_score_value(forecast_air), 1) == 0.8
    formatted = build_evening_format_v2("Кипр", forecast_air)
    assert "🏭 Воздух завтра утром: AQI 150" in formatted


def cy_morning_preserves_quake_line() -> None:
    text = build_morning_format_v2("Кипр", MORNING_WITH_QUAKE)
    assert "🌍 Сейсмика 24ч: 1 микрособытие M0.9–1.9; заметных событий M2.0+ не найдено." in text
    assert text.index("🏭 Воздух:") < text.index("🌍 Сейсмика 24ч:") < text.index("🧲 Космопогода:")


def cy_evening_polish_does_not_duplicate_nuance() -> None:
    text = build_evening_format_v2("Кипр", HEAT_WIND_EVENING)
    assert "⚠️ Нюанс:" in text
    polished = _insert_main_nuance(text)
    nuance_lines = [line for line in polished.splitlines() if line.startswith(("⚠️ Нюанс:", "⚠️ Главный нюанс:"))]
    assert len(nuance_lines) == 1
    assert "⚠️ Главный нюанс:" not in polished
    assert polished.count("✅ План завтра:") == 1


def cy_evening_recent_safecast_normal_is_omitted() -> None:
    old_file = os.environ.get("CY_SAFECAST_FILE")
    old_age = os.environ.get("CY_SAFECAST_MAX_AGE_HOURS")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "safecast_cy.json"
        path.write_text(json.dumps({"radiation_usvh": 0.08, "pm25": 28, "pm10": 63}), encoding="utf-8")
        try:
            os.environ["CY_SAFECAST_FILE"] = str(path)
            os.environ["CY_SAFECAST_MAX_AGE_HOURS"] = "24"
            text = build_evening_format_v2("Кипр", NORMAL_EVENING)
        finally:
            if old_file is None:
                os.environ.pop("CY_SAFECAST_FILE", None)
            else:
                os.environ["CY_SAFECAST_FILE"] = old_file
            if old_age is None:
                os.environ.pop("CY_SAFECAST_MAX_AGE_HOURS", None)
            else:
                os.environ["CY_SAFECAST_MAX_AGE_HOURS"] = old_age
    assert "🧪" not in text
    assert "Частный датчик" not in text
    assert "Safecast CY" not in text
    assert "PM₂.₅ 28" not in text


def cy_evening_hashtag_finalizer_and_caption_use_target_date() -> None:
    dirty = "\n".join(
        (
            "<b>🌅 Кипр завтра (15.07.2026)</b>",
            "#Кипр #погода #здоровье #Никосия #Тродос",
            "✅ План завтра: проверить ветер.",
            "💬 Настрой на завтра: лишняя строка.",
            "#Кипр #погода #здоровье #Никосия #Тродос",
            "",
        )
    )
    without_editorial = "\n".join(
        line for line in dirty.splitlines() if not line.startswith("💬 Настрой")
    )
    text = finalize_hashtags_at_end(
        without_editorial,
        canonical_hashtags="#Кипр #погода #здоровье #Никосия #Тродос",
    )
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[-1] == "#Кипр #погода #здоровье #Никосия #Тродос"
    assert text.count("#Кипр #погода #здоровье #Никосия #Тродос") == 1
    assert "💬 Настрой" not in text
    chunks = split_telegram_text(text)
    assert [line for line in chunks[-1].splitlines() if line.strip()][-1] == lines[-1]
    assert _cy_image_caption(
        "evening",
        "2026-07-15",
        test_label=True,
        current_date=date(2026, 7, 14),
    ) == "🧪 Визуальный вайб погоды на Кипре завтра 🌊"
    assert "сегодняшнего вечера" not in _cy_image_caption(
        "evening",
        "2026-07-15",
        test_label=False,
        current_date=date(2026, 7, 14),
    )


def cy_workflow_morning_schedule_is_earlier() -> None:
    workflow = (ROOT / ".github" / "workflows" / "daily_post.yml").read_text(encoding="utf-8")
    assert "cron: '0 1 * * *'" in workflow
    assert "cron: '15 3 * * *'" in workflow
    assert "github.event.schedule == '0 1 * * *'" in workflow
    assert "github.event.schedule == '15 3 * * *'" in workflow
    assert "CY_MORNING_DELIVERY_SKIP" in workflow
    assert "Upload Cyprus morning diagnostics" in workflow
    assert "01:00 UTC ≈ 04:00 на Кипре летом / 03:00 зимой" in workflow
    assert "cron: '0 13 * * *'" in workflow
    assert "cron: '0 7 * * *'" in workflow
    assert "github.event.schedule == '30 2 * * *'" not in workflow


def cy_h2_evening_score_is_not_published_twice() -> None:
    """H.2: the recomputed evening score replaces the factual one, never doubles it."""
    import safe_test_post as safe_module

    old = os.environ.get("EVENING_VAYBOMETER_SCORE")
    try:
        os.environ["EVENING_VAYBOMETER_SCORE"] = "1"
        v2 = build_evening_format_v2("Кипр", HEAT_WIND_EVENING)
        before_count = sum(
            1 for line in v2.splitlines() if line.strip().startswith("✨ VayboMeter")
        )
        after = safe_module._inject_evening_score(v2, "evening")
    finally:
        if old is None:
            os.environ.pop("EVENING_VAYBOMETER_SCORE", None)
        else:
            os.environ["EVENING_VAYBOMETER_SCORE"] = old

    assert before_count == 1, "fixture should already carry one factual score line"
    after_count = sum(
        1 for line in after.splitlines() if line.strip().startswith("✨ VayboMeter")
    )
    assert after_count == 1, f"evening score published {after_count} times"
    # The factual city rows are untouched.
    assert "Лимассол" in after and "Никосия" in after


def cy_h2_evening_redundant_nuance_is_suppressed_independent_kept() -> None:
    """A nuance rephrasing the score is dropped; an independent signal is kept."""
    import safe_test_post as safe_module

    # Score already names heat; a heat-only nuance adds nothing.
    redundant = safe_module._cyprus_main_nuance(
        "✨ VayboMeter завтра: 6.0/10 — с оговорками; сильная жара.\nНикосия: 38/24 °C • ясно\n"
    )
    assert "жара во внутренних районах" not in redundant

    # Fog is an independent signal and must survive.
    fog = safe_module._cyprus_main_nuance(
        "✨ VayboMeter завтра: 6.0/10 — с оговорками; сильная жара.\n"
        "🌫 Видимость: завтра утром местами около 250 м, вероятен туман.\n"
    )
    assert "туман" in fog.lower(), fog

    # Local rain is an independent signal too.
    rain = safe_module._cyprus_main_nuance(
        "✨ VayboMeter завтра: 6.0/10 — с оговорками; сильная жара.\n"
        "Тродос: 22/14 °C • местами дождь возможен\n"
    )
    assert "осадки" in rain.lower(), rain


def cy_evening_final_publication_path_applies_editorial_voice_once() -> None:
    """The final FORMAT_V2 path must publish exactly one evening editorial line."""
    from html.parser import HTMLParser

    import safe_test_post as safe_module

    old_values = {
        key: os.environ.get(key)
        for key in ("EVENING_VAYBOMETER_SCORE", "FORMAT_V2_MAIN_NUANCE")
    }
    try:
        os.environ.update({key: "1" for key in old_values})
        v2 = build_evening_format_v2("Кипр", NORMAL_EVENING)
        v2 = safe_module._inject_evening_score(v2, "evening")
        v2 = _apply_format_v2_test_polish(v2)
        v2 = _insert_main_nuance(v2)
        v2 = _apply_astro_cleanup(v2)
        v2 = _apply_cyprus_sensor_cleanup(v2)
        v2 = safe_module._apply_editorial_voice(v2, "evening")
        final_text = finalize_hashtags_at_end(
            v2,
            canonical_hashtags="#Кипр #погода #здоровье #Никосия #Тродос",
        )
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert final_text.count("💬 Настрой на завтра:") == 1
    assert "💬 По ощущениям дня:" not in final_text
    # Evening context is tomorrow, and the facts are untouched.
    assert "Кипр завтра" in final_text or "завтра" in final_text
    assert "Лимассол" in final_text
    lines = [line for line in final_text.splitlines() if line.strip()]
    assert lines[-1].startswith("#")
    HTMLParser().feed(final_text)

    # Re-applying the helper stays idempotent on the evening path too.
    again = safe_module._apply_editorial_voice(final_text, "evening")
    assert again.count("💬 Настрой на завтра:") == 1


def cy_evening_factual_formatter_still_omits_editorial_voice() -> None:
    """format_v2 stays a factual-only layer."""
    text = build_evening_format_v2("Кипр", NORMAL_EVENING)
    assert "💬 Настрой на завтра:" not in text
    assert "💬 По ощущениям дня:" not in text


def main() -> None:
    checks = (
        cy_morning_preserves_quake_line,
        cy_evening_polish_does_not_duplicate_nuance,
        cy_evening_recent_safecast_normal_is_omitted,
        cy_evening_hashtag_finalizer_and_caption_use_target_date,
        cy_evening_normal_no_generic_confidence,
        cy_evening_normal_no_island_correction,
        cy_evening_no_old_conclusion_or_recommendations,
        cy_evening_has_one_final_plan,
        cy_evening_preserves_weather_blocks,
        cy_evening_preserves_compact_astro,
        cy_evening_caution_score_softens_good_wording,
        cy_evening_score_reasons_are_semantically_deduped,
        cy_evening_preserves_moon_illumination_and_advice,
        cy_evening_normalizes_zodiac_symbol_suffix,
        cy_evening_preserves_new_moon_and_voc,
        cy_morning_preserves_moon_and_illumination,
        cy_morning_sea_summary_uses_coastal_rows_not_sunset,
        cy_evening_safe_pipeline_preserves_moon_illumination_and_plus,
        cy_evening_safe_pipeline_preserves_new_moon_and_voc,
        cy_evening_current_air_replaces_sensor_without_tomorrow_advice,
        cy_evening_low_aqi_haze_is_visibility_not_poor_air,
        cy_evening_unstructured_dust_text_does_not_create_air_warning,
        cy_evening_surf_without_wave_is_not_excellent,
        cy_evening_surf_with_valid_wave_is_cautiously_positive,
        cy_evening_city_air_line_is_compact_and_parenthesized,
        cy_evening_integrated_guidance_is_not_repetitive,
        cy_evening_generic_warning_does_not_trigger_storm,
        cy_evening_rain_warning_is_rain_not_storm,
        cy_evening_gust_17_triggers_storm_without_word,
        cy_evening_negated_storm_phrase_is_nonstorm,
        cy_evening_critical_safecast_is_explicitly_labeled,
        cy_evening_uncertain_has_short_confidence_line,
        cy_evening_title_is_compact,
        cy_evening_current_aqi_does_not_change_tomorrow_score,
        cy_evening_current_aqi_plus_fog_uses_only_fog_penalty,
        cy_evening_current_aqi_plus_reduced_visibility_uses_point_two,
        cy_evening_explicit_forecast_aqi_can_affect_tomorrow_score,
        cy_workflow_morning_schedule_is_earlier,
        cy_h2_evening_score_is_not_published_twice,
        cy_h2_evening_redundant_nuance_is_suppressed_independent_kept,
        cy_evening_final_publication_path_applies_editorial_voice_once,
        cy_evening_factual_formatter_still_omits_editorial_voice,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} Cyprus evening FORMAT_V2 checks passed")


if __name__ == "__main__":
    main()
