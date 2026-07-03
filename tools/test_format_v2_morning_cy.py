#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for Cyprus morning FORMAT_V2 post polish."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

telegram_stub = types.ModuleType("telegram")
telegram_stub.Bot = object
telegram_stub.constants = types.SimpleNamespace(ParseMode=types.SimpleNamespace(HTML="HTML"))
sys.modules.setdefault("telegram", telegram_stub)

pendulum_stub = types.ModuleType("pendulum")
pendulum_stub.DateTime = object
sys.modules.setdefault("pendulum", pendulum_stub)

imghdr_stub = types.ModuleType("imghdr")
imghdr_stub.what = lambda *args, **kwargs: None
sys.modules.setdefault("imghdr", imghdr_stub)

from format_v2 import build_morning_format_v2  # noqa: E402
from post_safety import sanitize_post_text  # noqa: E402
from safe_test_post import (  # noqa: E402
    _apply_astro_cleanup,
    _apply_cyprus_morning_raw_context,
    _apply_cyprus_sensor_cleanup,
)


MORNING_WITH_SEA = """<b>Кипр: погода, море, бури, Луна (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31 • 📟 0.08 μSv/h • 🌿 пыльца: низко
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
Море у Ларнаки: вода 28°C, волна спокойная.
🧲 Космопогода: Kp 2.0 (спокойно) • 🌬️ v 420 км/с
🌇 Закат сегодня: 20:05
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""


MORNING_NO_SEA = """<b>Кипр: погода на сегодня (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
🏭 AQI 42 (низкий) • PM₂.₅ 9 / PM₁₀ 18
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
✅ Сегодня: вода, SPF.
#Кипр #погода #здоровье
"""

MORNING_WITH_COASTAL_ROWS = """<b>Кипр: погода, море, бури, Луна (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
Ларнака: 34/25 °C • ☀️ ясно • 🌊 28
Лимассол: 35/26 °C • ☀️ ясно • 🌊 26
Айя-Напа: 35/26 °C • ☀️ ясно • 🌊 28
Пафос: 31/23 °C • ☀️ ясно • 🌊 26
🌇 Закат сегодня: 20:05
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""

MORNING_NON_MARINE_NUMBERS = """<b>Кипр: погода, жара и море (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31
💨 Ветер 6 м/с, давление 1009 гПа.
🌇 Закат сегодня: 20:05
✨ 96% освещённости — Луна яркая.
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""

MORNING_WINTER_WITH_SEA = """<b>Кипр: погода и море (15.01.2026)</b>
👋 Доброе утро! Теплее всего — Ларнака (19°), прохладнее — Тродос (8°).
🏭 AQI 42 (низкий) • PM₂.₅ 9 / PM₁₀ 18
💨 Ветер: 3.0 м/с • 🔹 1014 гПа →
Море у Ларнаки: вода 19°C, волна спокойная.
🌇 Закат сегодня: 17:02
✅ Сегодня: прогулка у моря, слой от ветра.
#Кипр #погода #здоровье
"""

MORNING_WINTER_NON_MARINE_NUMBERS = """<b>Кипр: погода и море (15.01.2026)</b>
👋 Доброе утро! Теплее всего — Ларнака (19°), прохладнее — Тродос (8°).
🏭 AQI 42 (низкий) • PM₂.₅ 9 / PM₁₀ 18
💨 Ветер: 3.0 м/с • 🔹 1014 гПа →
🌇 Закат сегодня: 19:05
✅ Сегодня: прогулка у моря, слой от ветра.
#Кипр #погода #здоровье
"""

REAL_RAW_MORNING_WITH_SEA_ASTRO = """<b>Кипр: погода, жара и море (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
Ларнака: 34/25 °C • ☀️ ясно • 🌊 28
Лимассол: 35/26 °C • ☀️ ясно • 🌊 26
Айя-Напа: 35/26 °C • ☀️ ясно • 🌊 28
Пафос: 31/23 °C • ☀️ ясно • 🌊 26
🌇 Закат сегодня: 20:05
🌕 Почти полная Луна в ♒ — 96% освещённости.
💚 В плюсе: планы, восстановление.
⚫️ VoC: 08:20–10:10.
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""

REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO = """<b>Кипр: погода, жара и море (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
🌇 Закат сегодня: 20:05
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""

MORNING_POOR_AIR = """<b>Кипр: погода на сегодня (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
🏭 AQI 112 (умеренный) • PM₂.₅ 24 / PM₁₀ 63
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
✅ Сегодня: вода, SPF.
#Кипр #погода #здоровье
"""


MORNING_FULL_MOON = """<b>Кипр: погода на сегодня (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (32°), прохладнее — Тродос (24°).
🏭 AQI 42 (низкий) • PM₂.₅ 9 / PM₁₀ 18
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
🌇 Закат сегодня: 20:05
Полнолуние в ♑ — пик эмоций и результатов.
✨ 100% освещённости — Луна яркая.
✅ Общий фон: благоприятный, но без перегруза.
💚 В плюсе: завершение, восстановление.
⚫️ VoC: 08:20–10:10.
✅ Сегодня: вода, SPF.
#Кипр #погода #здоровье
"""


def cy_morning_adds_concise_sea_block_when_available() -> None:
    text = build_morning_format_v2("Кипр", MORNING_WITH_SEA)
    assert "🌊 Море: вода 28°C; волна спокойная; лучше до 11:00 или после 18:30." in text
    assert "🏭 Воздух: AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31 • 🌿 пыльца: низкая" in text
    assert "📟" not in text
    assert "🌿 пыльца" in text


def cy_morning_averages_coastal_sea_rows() -> None:
    text = build_morning_format_v2("Кипр", MORNING_WITH_COASTAL_ROWS)
    assert "🌊 Море: средняя вода 27°C; лучше до 11:00 или после 18:30." in text
    assert "🌊 Море: вода 20°C" not in text


def cy_morning_adds_sea_fallback_when_unavailable() -> None:
    text = build_morning_format_v2("Кипр", MORNING_NO_SEA)
    assert "🌊 Море: данные о температуре воды обновляются; лучше до 11:00 или после 18:30." in text


def cy_morning_rejects_non_marine_numbers_for_sea() -> None:
    text = build_morning_format_v2("Кипр", MORNING_NON_MARINE_NUMBERS)
    assert "🌊 Море: данные о температуре воды обновляются; лучше до 11:00 или после 18:30." in text
    assert "🌊 Море: вода 20°C" not in text
    assert "🌊 Море: вода 31°C" not in text


def cy_morning_accepts_winter_explicit_sea_temperature() -> None:
    text = build_morning_format_v2("Кипр", MORNING_WINTER_WITH_SEA)
    assert "🌊 Море: вода 19°C; волна спокойная; лучше до 11:00 или после 18:30." in text


def cy_morning_winter_sunset_time_is_not_sea_temperature() -> None:
    text = build_morning_format_v2("Кипр", MORNING_WINTER_NON_MARINE_NUMBERS)
    assert "🌊 Море: данные о температуре воды обновляются; лучше до 11:00 или после 18:30." in text
    assert "🌊 Море: вода 19°C" not in text


def cy_morning_preserves_full_moon_line_without_illumination_duplicate() -> None:
    text = build_morning_format_v2("Кипр", MORNING_FULL_MOON)
    assert "🌕 Полнолуние в ♑ — 100% освещённости." in text
    assert "✨ 100% освещённости" not in text
    assert "✅ Общий фон: благоприятный, но без перегруза." in text
    assert "💚 В плюсе: завершение, восстановление." in text
    assert "⚫️ VoC: 08:20–10:10." in text
    assert text.index("🌇 Закат сегодня: 20:05") < text.index("🌕 Полнолуние")
    assert text.index("🌕 Полнолуние") < text.index("✅ План:")


def cy_morning_poor_air_adds_health_recommendation() -> None:
    text = build_morning_format_v2("Кипр", MORNING_POOR_AIR)
    assert "😷 Воздух неидеален: активность на улице короче" in text
    assert "окна лучше держать закрытыми" in text


def cy_morning_recent_safecast_elevated_is_omitted() -> None:
    old_file = os.environ.get("CY_SAFECAST_FILE")
    old_age = os.environ.get("CY_SAFECAST_MAX_AGE_HOURS")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "safecast_cy.json"
        path.write_text(json.dumps({"radiation_usvh": 0.23, "pm25": 9, "pm10": 18}), encoding="utf-8")
        try:
            os.environ["CY_SAFECAST_FILE"] = str(path)
            os.environ["CY_SAFECAST_MAX_AGE_HOURS"] = "24"
            text = build_morning_format_v2("Кипр", MORNING_NO_SEA)
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
    assert "PM₂.₅" in text


def cy_morning_real_safe_path_restores_sea_and_astro_from_raw() -> None:
    legacy_result = sanitize_post_text(REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO)
    text = build_morning_format_v2("Кипр", legacy_result.text)
    text = _apply_astro_cleanup(text)
    text = _apply_cyprus_morning_raw_context(text, REAL_RAW_MORNING_WITH_SEA_ASTRO, legacy_result.text, "morning")
    text = _apply_cyprus_sensor_cleanup(text)
    text = sanitize_post_text(text).text

    assert "🌊 Море: средняя вода 27°C; лучше до 11:00 или после 18:30." in text
    assert "🌊 Море: вода 20°C" not in text
    assert "☀️ <b>Солнце, Луна и ритм дня</b>" in text
    assert "🌇 Закат сегодня: 20:05" in text
    assert "Почти полная Луна" in text
    assert "96% освещённости" in text
    assert "💚 В плюсе: планы, восстановление." in text
    assert text.splitlines()[-1] == "#Кипр #погода #здоровье"


def main() -> None:
    checks = (
        cy_morning_adds_concise_sea_block_when_available,
        cy_morning_averages_coastal_sea_rows,
        cy_morning_adds_sea_fallback_when_unavailable,
        cy_morning_rejects_non_marine_numbers_for_sea,
        cy_morning_accepts_winter_explicit_sea_temperature,
        cy_morning_winter_sunset_time_is_not_sea_temperature,
        cy_morning_preserves_full_moon_line_without_illumination_duplicate,
        cy_morning_poor_air_adds_health_recommendation,
        cy_morning_recent_safecast_elevated_is_omitted,
        cy_morning_real_safe_path_restores_sea_and_astro_from_raw,
    )
    for check in checks:
        check()
        print(f"PASS: {check.__name__}")
    print(f"OK: {len(checks)} Cyprus morning FORMAT_V2 checks passed")


if __name__ == "__main__":
    main()
