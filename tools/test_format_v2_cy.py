#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for compact Cyprus FORMAT_V2 evening posts."""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

imghdr_stub = types.ModuleType("imghdr")
imghdr_stub.what = lambda file, h=None: None
sys.modules.setdefault("imghdr", imghdr_stub)

from format_v2 import build_evening_format_v2, build_morning_format_v2  # noqa: E402
from safe_test_post import _insert_main_nuance  # noqa: E402


MORNING_WITH_QUAKE = """<b>🌅 Кипр: погода на сегодня (27.06.2026)</b>
Доброе утро. Теплее всего — Никосия (32°), прохладнее — Тродос (24°).
☀️ <b>УФ-индекс 7 (High)</b>: SPF, вода и тень.
🏭 Воздух: 🟢 чисто.
🌍 Сейсмика 24ч: спокойно — заметных землетрясений рядом с Кипром не было.
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
    start = lines.index("☀️ <b>Солнце и ритм дня</b>")
    block = [line for line in lines[start:start + 5] if line.strip()]
    assert "🌅 Рассвет завтра: 05:35" in block
    assert "🌙 Растущая Луна, ♏ (86%)" in block
    assert "💚 В плюсе: порядок, прогулки, мягкий режим." in block
    assert len(block) <= 5


def cy_evening_uncertain_has_short_confidence_line() -> None:
    text = build_evening_format_v2("Кипр", RAIN_EVENING)
    assert "🎯 Уверенность: температура высокая; ветер/осадки лучше проверить утром." in text
    assert "🎯 <b>Уверенность прогноза</b>" not in text


def cy_evening_title_is_compact() -> None:
    text = build_evening_format_v2("Кипр", NORMAL_EVENING)
    assert text.splitlines()[0] == "<b>🌅 Кипр завтра (27.06.2026)</b>"


def cy_morning_preserves_quake_line() -> None:
    text = build_morning_format_v2("Кипр", MORNING_WITH_QUAKE)
    assert "🌍 Сейсмика 24ч: спокойно — заметных землетрясений рядом с Кипром не было." in text
    assert text.index("🏭 Воздух:") < text.index("🌍 Сейсмика 24ч:") < text.index("🧲 Космопогода:")


def cy_evening_polish_does_not_duplicate_nuance() -> None:
    text = build_evening_format_v2("Кипр", HEAT_WIND_EVENING)
    assert "⚠️ Нюанс:" in text
    polished = _insert_main_nuance(text)
    nuance_lines = [line for line in polished.splitlines() if line.startswith(("⚠️ Нюанс:", "⚠️ Главный нюанс:"))]
    assert len(nuance_lines) == 1
    assert "⚠️ Главный нюанс:" not in polished
    assert polished.count("✅ План завтра:") == 1


def main() -> None:
    checks = (
        cy_morning_preserves_quake_line,
        cy_evening_polish_does_not_duplicate_nuance,
        cy_evening_normal_no_generic_confidence,
        cy_evening_normal_no_island_correction,
        cy_evening_no_old_conclusion_or_recommendations,
        cy_evening_has_one_final_plan,
        cy_evening_preserves_weather_blocks,
        cy_evening_preserves_compact_astro,
        cy_evening_uncertain_has_short_confidence_line,
        cy_evening_title_is_compact,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} Cyprus evening FORMAT_V2 checks passed")


if __name__ == "__main__":
    main()
