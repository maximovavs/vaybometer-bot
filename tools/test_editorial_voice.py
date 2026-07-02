#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for Cyprus deterministic editorial voice."""
from __future__ import annotations

import sys
from datetime import date
from html.parser import HTMLParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from editorial_voice import CYPRUS_MORNING_VARIANTS, deterministic_variant  # noqa: E402
from format_v2 import build_evening_format_v2, build_morning_format_v2  # noqa: E402
from send_weekly_forecast import build_weekly_forecast  # noqa: E402


FORBIDDEN = (
    "доверьтесь Вселенной",
    "повысьте вибрации",
    "энергии дня требуют",
    "судьбоносный период",
    "трансформация",
    "проявленность",
    "слушайте знаки",
    "аварии",
    "чрезвычайные ситуации",
    "операции лучше отложить",
    "воздушном пространстве",
)

MORNING = """<b>🌅 Кипр: погода на сегодня (27.06.2026)</b>
Доброе утро. Теплее всего — Никосия (34°), прохладнее — Тродос (24°).
💨 Ветер: 4.0 м/с • порывы до 8 м/с • 🔹 1012 гПа.
☀️ <b>УФ-индекс 8 (High)</b>: SPF, вода и тень.
🏭 Воздух: AQI 125 (высокий) • PM₂.₅ 20 / PM₁₀ 69
Ларнака: ясно • 🌊 28
Лимассол: ясно • 🌊 26
🌇 Закат сегодня: 20:05
🌕 Почти полная Луна в ♒ — 96% освещённости.
💚 В плюсе: планы, дороги, обучение.
✅ Сегодня: основные дела до 11:00; днём — вода, тень и паузы.
#Кипр #погода #здоровье
"""

EVENING = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 7.4/10 — хорошо; жара и порывы у моря.
🏖 <b>Морские города</b>
Лимассол: 31/24 °C • ясно • 💨 5 м/с • порывы до 11 м/с
Ларнака: 30/23 °C • ясно • 💨 4 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 35/24 °C • ясно
Тродос: 26/17 °C • ясно
———
🏭 Воздух: AQI 75 • PM₂.₅ 12 / PM₁₀ 24
🌅 Рассвет завтра: 05:35
🌕 Почти полная Луна в ♒ — 96% освещённости.
💚 В плюсе: планы, дороги, обучение.
#Кипр #погода #здоровье #Никосия #Тродос
"""

WEATHER = {
    "daily": {
        "time": ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04", "2026-07-05", "2026-07-06", "2026-07-07"],
        "temperature_2m_max": [32, 34, 36, 35, 33, 31, 30],
        "temperature_2m_min": [24, 25, 26, 25, 24, 23, 23],
        "wind_speed_10m_max": [5, 6, 7, 6, 5, 5, 4],
        "wind_gusts_10m_max": [8, 9, 10, 8, 7, 7, 6],
        "precipitation_probability_max": [0, 5, 10, 10, 0, 0, 0],
        "weathercode": [0, 1, 1, 2, 1, 0, 0],
        "uv_index_max": [8, 9, 9, 8, 7, 7, 7],
    }
}

LUNAR = {
    "days": {
        "2026-07-01": {"phase_name": "Полнолуние", "percent": 99},
        "2026-07-07": {"phase_name": "Убывающая Луна", "percent": 75},
    }
}


class _Parser(HTMLParser):
    pass


def _assert_clean(text: str) -> None:
    low = text.lower()
    assert not any(phrase.lower() in low for phrase in FORBIDDEN)
    assert text.splitlines()[-1].startswith("#")
    _Parser().feed(text)


def test_deterministic_variant_is_stable_and_rotates() -> None:
    variants = CYPRUS_MORNING_VARIANTS["HOT_UV"]
    first = deterministic_variant("Кипр", "2026-07-01", "HOT_UV", variants)
    second = deterministic_variant("Кипр", "2026-07-01", "HOT_UV", variants)
    assert first == second
    rotated = {
        deterministic_variant("Кипр", f"2026-07-{day:02d}", "HOT_UV", variants)
        for day in range(1, 15)
    }
    assert len(rotated) > 1
    assert "hash(" not in (ROOT / "editorial_voice.py").read_text(encoding="utf-8")


def test_morning_output_has_one_human_line_and_keeps_facts() -> None:
    text = build_morning_format_v2("Кипр", MORNING)
    assert text.count("💬 По ощущениям дня:") == 1
    assert "AQI 125" in text
    assert "PM₂.₅ 20" in text
    assert "PM₁₀ 69" in text
    assert "средняя вода 27°C" in text
    assert "96% освещённости" in text
    assert "Балтика" not in text
    _assert_clean(text)


def test_evening_output_has_one_human_line_and_keeps_facts() -> None:
    text = build_evening_format_v2("Кипр", EVENING)
    assert text.count("💬 Настрой на завтра:") == 1
    assert "Никосия: 35/24 °C" in text
    assert "Лимассол: 31/24 °C" in text
    assert "AQI 75" in text
    assert "96% освещённости" in text
    assert "Балтика" not in text
    _assert_clean(text)


def test_weekly_output_contains_meaning_block_and_keeps_facts() -> None:
    text = build_weekly_forecast(
        date(2026, 7, 1),
        weather_payload=WEATHER,
        air_data={"aqi": 125, "pm25": 20, "pm10": 69},
        sea_temps=[27.2, 28.1, 27.6],
        kp_tuple=(2.3, "спокойно", 123456, "fixture"),
        lunar_data=LUNAR,
        astro_events_paths=[Path("__missing_astro_events.json")],
    )
    assert "🌿 Смысл недели" in text
    assert text.index("🌿 Смысл недели") > text.index("✨ Главный фон недели")
    assert text.index("🌿 Смысл недели") < text.index("🌦 Погода")
    assert "Температура держится в диапазоне 30–36°C" in text
    assert "AQI 125" in text
    assert "Средняя вода 27–28°C" in text
    assert "Kp 2.3" in text
    assert "Полнолуние" in text
    _assert_clean(text)


def main() -> None:
    checks = (
        test_deterministic_variant_is_stable_and_rotates,
        test_morning_output_has_one_human_line_and_keeps_facts,
        test_evening_output_has_one_human_line_and_keeps_facts,
        test_weekly_output_contains_meaning_block_and_keeps_facts,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} Cyprus editorial voice checks passed")


if __name__ == "__main__":
    main()
