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
from post_safety import sanitize_post_text  # noqa: E402
from safe_test_post import _apply_astro_cleanup, _apply_format_v2_test_polish, _insert_main_nuance  # noqa: E402


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
    assert "✨ VayboMeter завтра: 7.4/10 — с оговорками; жара и порывы у моря." in text
    assert "7.4/10" in text
    assert "хорошо; сильная жара" not in text
    assert "☀️ <b>Солнце, Луна и ритм завтра</b>" in text
    assert "🌇 Закат завтра: 20:05" in text
    assert [line for line in text.splitlines() if line.strip()][-1] == "#Кипр #погода #здоровье #Никосия #Тродос"


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
    assert "🌊 Море: средняя вода 27°C; у берега жарко, лучше утром или ближе к закату." in text
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


def cy_evening_recent_safecast_adds_compact_private_sensor_line() -> None:
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
    assert "🧪 Частный датчик: фон в норме; смотрим динамику." in text
    assert "PM₂.₅ 28" not in text


def cy_workflow_morning_schedule_is_earlier() -> None:
    workflow = (ROOT / ".github" / "workflows" / "daily_post.yml").read_text(encoding="utf-8")
    assert "cron: '0 1 * * *'" in workflow
    assert "github.event.schedule == '0 1 * * *'" in workflow
    assert "01:00 UTC ≈ 04:00 на Кипре летом / 03:00 зимой" in workflow
    assert "cron: '0 13 * * *'" in workflow
    assert "cron: '0 7 * * *'" in workflow
    assert "github.event.schedule == '30 2 * * *'" not in workflow


def main() -> None:
    checks = (
        cy_morning_preserves_quake_line,
        cy_evening_polish_does_not_duplicate_nuance,
        cy_evening_recent_safecast_adds_compact_private_sensor_line,
        cy_evening_normal_no_generic_confidence,
        cy_evening_normal_no_island_correction,
        cy_evening_no_old_conclusion_or_recommendations,
        cy_evening_has_one_final_plan,
        cy_evening_preserves_weather_blocks,
        cy_evening_preserves_compact_astro,
        cy_evening_caution_score_softens_good_wording,
        cy_evening_preserves_moon_illumination_and_advice,
        cy_evening_normalizes_zodiac_symbol_suffix,
        cy_evening_preserves_new_moon_and_voc,
        cy_morning_preserves_moon_and_illumination,
        cy_morning_sea_summary_uses_coastal_rows_not_sunset,
        cy_evening_safe_pipeline_preserves_moon_illumination_and_plus,
        cy_evening_safe_pipeline_preserves_new_moon_and_voc,
        cy_evening_uncertain_has_short_confidence_line,
        cy_evening_title_is_compact,
        cy_workflow_morning_schedule_is_earlier,
    )
    for check in checks:
        check()
        print(f"PASS {check.__name__}")
    print(f"OK: {len(checks)} Cyprus evening FORMAT_V2 checks passed")


if __name__ == "__main__":
    main()
