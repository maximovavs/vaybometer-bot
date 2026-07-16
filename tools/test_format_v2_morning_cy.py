#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for Cyprus morning FORMAT_V2 post polish."""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
import types
from datetime import date
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

from format_v2 import build_format_v2, build_morning_format_v2  # noqa: E402
import cyprus_visual_dedup  # noqa: E402
import image_prompt_cy_scene as cy_scene_prompt  # noqa: E402
import safe_test_post as safe_module  # noqa: E402
import weather as weather_module  # noqa: E402
from image_prompt_cy_scene import build_cyprus_scene_prompt_with_metadata  # noqa: E402
from post_safety import sanitize_post_text  # noqa: E402
from safe_test_post import (  # noqa: E402
    _apply_astro_cleanup,
    _apply_cyprus_morning_raw_context,
    _apply_cyprus_sensor_cleanup,
    _build_safe_test_image,
    _cy_image_receipt_path,
    _cy_image_caption,
    _cy_text_receipt_path,
    _cy_write_image_diagnostics,
    _send_telegram_text_chunks,
    cy_morning_delivery_path,
    cy_morning_has_valid_production_receipt,
    cy_morning_image_phase_for_result,
    cy_morning_load_delivery_receipt,
    cy_morning_maybe_write_delivery_receipt,
    cy_morning_target_date,
    has_valid_cy_text_delivery,
    is_valid_cy_image_receipt,
    is_valid_cy_text_receipt,
    finalize_hashtags_at_end,
    _cyprus_main_nuance,
    _cyprus_score_line,
    _cyprus_smart_plan_line,
    _inject_morning_score,
    _inject_morning_smart_plan,
    _insert_main_nuance,
)
from weather import (  # noqa: E402
    build_cyprus_visibility_line,
    get_cyprus_visibility_context,
    save_cyprus_visibility_diagnostics,
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


def _with_temp_delivery_dir(callback) -> None:
    old_dir = os.environ.get("CY_MORNING_DELIVERY_DIR")
    old_text_dir = os.environ.get("CY_TEXT_DELIVERY_DIR")
    old_image_dir = os.environ.get("CY_IMAGE_DELIVERY_DIR")
    old_diag_dir = os.environ.get("CY_IMAGE_DIAGNOSTICS_DIR")
    old_health_dir = os.environ.get("CY_IMAGE_PROVIDER_HEALTH_DIR")
    old_run = os.environ.get("GITHUB_RUN_ID")
    old_attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
    old_schedule = os.environ.get("GITHUB_EVENT_SCHEDULE")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["CY_MORNING_DELIVERY_DIR"] = tmp
            os.environ["CY_TEXT_DELIVERY_DIR"] = str(Path(tmp) / "cy_text_delivery")
            os.environ["CY_IMAGE_DELIVERY_DIR"] = str(Path(tmp) / "cy_image_delivery")
            os.environ["CY_IMAGE_DIAGNOSTICS_DIR"] = str(Path(tmp) / "cy_image_diagnostics")
            os.environ["CY_IMAGE_PROVIDER_HEALTH_DIR"] = str(Path(tmp) / "cy_image_provider_health")
            os.environ["GITHUB_RUN_ID"] = "fixture-run"
            os.environ["GITHUB_RUN_ATTEMPT"] = "2"
            callback(Path(tmp))
        finally:
            if old_dir is None:
                os.environ.pop("CY_MORNING_DELIVERY_DIR", None)
            else:
                os.environ["CY_MORNING_DELIVERY_DIR"] = old_dir
            if old_text_dir is None:
                os.environ.pop("CY_TEXT_DELIVERY_DIR", None)
            else:
                os.environ["CY_TEXT_DELIVERY_DIR"] = old_text_dir
            if old_image_dir is None:
                os.environ.pop("CY_IMAGE_DELIVERY_DIR", None)
            else:
                os.environ["CY_IMAGE_DELIVERY_DIR"] = old_image_dir
            if old_diag_dir is None:
                os.environ.pop("CY_IMAGE_DIAGNOSTICS_DIR", None)
            else:
                os.environ["CY_IMAGE_DIAGNOSTICS_DIR"] = old_diag_dir
            if old_health_dir is None:
                os.environ.pop("CY_IMAGE_PROVIDER_HEALTH_DIR", None)
            else:
                os.environ["CY_IMAGE_PROVIDER_HEALTH_DIR"] = old_health_dir
            if old_run is None:
                os.environ.pop("GITHUB_RUN_ID", None)
            else:
                os.environ["GITHUB_RUN_ID"] = old_run
            if old_attempt is None:
                os.environ.pop("GITHUB_RUN_ATTEMPT", None)
            else:
                os.environ["GITHUB_RUN_ATTEMPT"] = old_attempt
            if old_schedule is None:
                os.environ.pop("GITHUB_EVENT_SCHEDULE", None)
            else:
                os.environ["GITHUB_EVENT_SCHEDULE"] = old_schedule


def _write_fixture_receipt(target_date: str, event_schedule: str) -> Path:
    os.environ["GITHUB_EVENT_SCHEDULE"] = event_schedule
    receipt = cy_morning_maybe_write_delivery_receipt(
        target_date=target_date,
        chat_type="production",
        telegram_message_ids=[12345],
        text_chunk_count=1,
        sent=True,
        event_schedule=event_schedule,
    )
    assert receipt is not None
    return receipt


def _write_canonical_text_receipt(target_date: str, post_type: str = "morning", ids: list[int] | None = None) -> Path:
    payload = {
        "target_date": target_date,
        "post_type": post_type,
        "chat_type": "production",
        "telegram_message_ids": ids or [12345],
        "text_chunk_count": 1,
        "run_id": "fixture-run",
        "run_attempt": "2",
        "sent_at_utc": "2026-07-06T01:05:00Z",
    }
    path = _cy_text_receipt_path(target_date, post_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


def _write_image_receipt(target_date: str, post_type: str = "morning", message_id: int = 777) -> Path:
    payload = {
        "target_date": target_date,
        "post_type": post_type,
        "chat_type": "production",
        "telegram_message_id": message_id,
        "sha256": "a" * 64,
        "perceptual_hash": "b" * 16,
        "selected_scene": "coastal_promenade",
        "style_name": "fixture",
        "cache_key": "fixture",
        "sent_at_utc": "2026-07-06T01:06:00Z",
    }
    path = _cy_image_receipt_path(target_date, post_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return path


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


def cy_morning_hashtags_are_final_without_editorial_tail() -> None:
    built = build_morning_format_v2("Кипр", MORNING_WITH_SEA)
    assert "💬 Настрой" not in built
    assert "💬 По ощущениям" not in built
    dirty = built + "\n#Кипр #погода #здоровье #Никосия #Тродос\n\n"
    text = finalize_hashtags_at_end(
        dirty,
        canonical_hashtags="#Кипр #погода #здоровье #Никосия #Тродос",
    )
    lines = [line for line in text.splitlines() if line.strip()]
    assert lines[-1] == "#Кипр #погода #здоровье #Никосия #Тродос"
    assert text.count("#Кипр #погода #здоровье #Никосия #Тродос") == 1
    assert _cy_image_caption(
        "morning",
        "2026-07-14",
        test_label=True,
        current_date=date(2026, 7, 14),
    ) == "🧪 Визуальный вайб сегодняшнего дня на Кипре 🌊"
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


def cy_morning_image_failure_still_sends_text_chunks() -> None:
    async def _run() -> tuple[dict[str, object], list[dict[str, object]], list[int]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")

        def _fail_image(*_args, **_kwargs):
            raise RuntimeError("fixture image backend failure")

        imagegen_stub.generate_astro_image = _fail_image
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        try:
            image_result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=False,
                image_chat_id=None,
            )
        finally:
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old

        class FakeBot:
            def __init__(self) -> None:
                self.messages: list[dict[str, object]] = []

            async def send_message(self, **kwargs):
                self.messages.append(kwargs)
                return types.SimpleNamespace(message_id=9000 + len(self.messages))

        bot = FakeBot()
        message_ids = await _send_telegram_text_chunks(
            bot,
            chat_id=123,
            chunks=["<b>Кипр сегодня</b>\n✅ План: текст отправлен."],
            add_test_label=False,
        )
        return image_result, bot.messages, message_ids

    image_result, messages, message_ids = asyncio.run(_run())
    assert image_result["result"] == "failed_non_fatal"
    assert image_result["error_type"] == "RuntimeError"
    assert len(messages) == 1
    assert messages[0]["chat_id"] == 123
    assert message_ids == [9001]


def cy_morning_recovery_publishes_then_delayed_primary_skips_by_receipt() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        assert not cy_morning_has_valid_production_receipt(target_date)
        receipt = _write_fixture_receipt(target_date, "15 3 * * *")
        assert receipt == cy_morning_delivery_path(target_date)
        assert cy_morning_has_valid_production_receipt(target_date)
        data = cy_morning_load_delivery_receipt(target_date)
        assert data is not None
        assert data["event_schedule"] == "15 3 * * *"
        assert data["chat_type"] == "production"

    _with_temp_delivery_dir(_case)


def cy_morning_primary_publishes_then_recovery_skips_by_receipt() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        _write_fixture_receipt(target_date, "0 1 * * *")
        assert cy_morning_has_valid_production_receipt(target_date)
        data = cy_morning_load_delivery_receipt(target_date)
        assert data is not None
        assert data["event_schedule"] == "0 1 * * *"
        assert data["telegram_message_ids"] == [12345]
        assert data["text_chunk_count"] == 1

    _with_temp_delivery_dir(_case)


def cy_morning_failed_primary_without_text_receipt_allows_recovery() -> None:
    def _case(tmp: Path) -> None:
        target_date = "2026-07-06"
        failed_attempt_path = tmp / f"{target_date}.partial.json"
        failed_attempt_path.write_text(
            json.dumps({"target_date": target_date, "chat_type": "production"}),
            encoding="utf-8",
        )
        assert not cy_morning_has_valid_production_receipt(target_date)
        _write_fixture_receipt(target_date, "15 3 * * *")
        assert cy_morning_has_valid_production_receipt(target_date)

    _with_temp_delivery_dir(_case)


def cy_morning_dry_run_does_not_create_delivery_receipt() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        receipt = cy_morning_maybe_write_delivery_receipt(
            target_date=target_date,
            chat_type="production",
            telegram_message_ids=[1],
            text_chunk_count=1,
            sent=False,
            event_schedule="0 1 * * *",
        )
        assert receipt is None
        assert not cy_morning_delivery_path(target_date).exists()
        assert not cy_morning_has_valid_production_receipt(target_date)

    _with_temp_delivery_dir(_case)


def cy_morning_test_channel_send_does_not_create_production_receipt() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        receipt = cy_morning_maybe_write_delivery_receipt(
            target_date=target_date,
            chat_type="test",
            telegram_message_ids=[1],
            text_chunk_count=1,
            sent=True,
            event_schedule="0 1 * * *",
        )
        assert receipt is None
        assert not cy_morning_delivery_path(target_date).exists()
        assert not cy_morning_has_valid_production_receipt(target_date)

    _with_temp_delivery_dir(_case)


def cy_morning_image_failure_plus_successful_text_creates_receipt() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        image_result = {"result": "failed_non_fatal", "error_type": "RuntimeError"}
        assert image_result["result"] == "failed_non_fatal"
        receipt = cy_morning_maybe_write_delivery_receipt(
            target_date=target_date,
            chat_type="production",
            telegram_message_ids=[5001],
            text_chunk_count=1,
            sent=True,
            event_schedule="0 1 * * *",
        )
        assert receipt is not None
        assert cy_morning_has_valid_production_receipt(target_date)

    _with_temp_delivery_dir(_case)


def cy_morning_successful_github_job_without_receipt_does_not_suppress_recovery() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        successful_job_seen = True
        assert successful_job_seen
        assert not cy_morning_has_valid_production_receipt(target_date)

    _with_temp_delivery_dir(_case)


def cy_morning_receipt_uses_asia_nicosia_target_date() -> None:
    def _case(_tmp: Path) -> None:
        assert cy_morning_target_date("2026-07-06", "Asia/Nicosia") == "2026-07-06"
        target_date = cy_morning_target_date("2026-07-06", "Asia/Nicosia")
        _write_fixture_receipt(target_date, "0 1 * * *")
        assert cy_morning_delivery_path(target_date).name == "2026-07-06.json"
        data = cy_morning_load_delivery_receipt(target_date)
        assert data is not None
        assert data["target_date"] == "2026-07-06"

    _with_temp_delivery_dir(_case)


def cy_morning_partial_text_failure_has_no_completed_receipt() -> None:
    async def _run() -> tuple[list[int], str]:
        class FailingBot:
            def __init__(self) -> None:
                self.calls = 0

            async def send_message(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    return types.SimpleNamespace(message_id=7001)
                raise RuntimeError("fixture second chunk failed")

        partial: list[int] = []
        try:
            await _send_telegram_text_chunks(
                FailingBot(),
                chat_id=123,
                chunks=["chunk one", "chunk two"],
                add_test_label=False,
                partial_message_ids=partial,
            )
        except RuntimeError as exc:
            return partial, exc.__class__.__name__
        raise AssertionError("second chunk failure was expected")

    def _case(_tmp: Path) -> None:
        partial, error_type = asyncio.run(_run())
        assert partial == [7001]
        assert error_type == "RuntimeError"
        receipt = cy_morning_maybe_write_delivery_receipt(
            target_date="2026-07-06",
            chat_type="production",
            telegram_message_ids=partial,
            text_chunk_count=2,
            sent=True,
            event_schedule="0 1 * * *",
        )
        assert receipt is None
        assert not cy_morning_has_valid_production_receipt("2026-07-06")

    _with_temp_delivery_dir(_case)


def cy_morning_image_phase_names_match_delivery_state() -> None:
    assert cy_morning_image_phase_for_result("sent") == "image_sent"
    assert cy_morning_image_phase_for_result("generated") == "image_generated"
    assert cy_morning_image_phase_for_result("failed_non_fatal") == "image_failed_non_fatal"
    assert cy_morning_image_phase_for_result("failed_after_duplicates") == "image_failed_non_fatal"
    assert cy_morning_image_phase_for_result("skipped") == "image_skipped"
    assert cy_morning_image_phase_for_result("failed_non_fatal") != "image_sent"


def cy_text_and_image_receipt_validators_are_strict() -> None:
    def _case(tmp: Path) -> None:
        target_date = "2026-07-06"
        assert not is_valid_cy_text_receipt(target_date, "morning")
        assert not is_valid_cy_image_receipt(target_date, "morning")
        _write_canonical_text_receipt(target_date)
        _write_image_receipt(target_date)
        assert is_valid_cy_text_receipt(target_date, "morning")
        assert has_valid_cy_text_delivery(target_date, "morning")
        assert is_valid_cy_image_receipt(target_date, "morning")

        bad_text = _cy_text_receipt_path("2026-07-07", "morning")
        bad_text.parent.mkdir(parents=True, exist_ok=True)
        bad_text.write_text("{not json", encoding="utf-8")
        assert not is_valid_cy_text_receipt("2026-07-07", "morning")

        bad_image = _cy_image_receipt_path("2026-07-07", "morning")
        bad_image.parent.mkdir(parents=True, exist_ok=True)
        bad_image.write_text(json.dumps({"target_date": "2026-07-07", "post_type": "morning"}), encoding="utf-8")
        assert not is_valid_cy_image_receipt("2026-07-07", "morning")

    _with_temp_delivery_dir(_case)


def cy_morning_delayed_0315_decision_sends_image_only_when_text_exists() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"

        def decision() -> str:
            text_delivered = has_valid_cy_text_delivery(target_date, "morning")
            image_delivered = is_valid_cy_image_receipt(target_date, "morning")
            if text_delivered and image_delivered:
                return "skip_all"
            if text_delivered and not image_delivered:
                return "image_only"
            return "full_publish"

        assert decision() == "full_publish"
        _write_canonical_text_receipt(target_date)
        assert decision() == "image_only"
        _write_image_receipt(target_date)
        assert decision() == "skip_all"

    _with_temp_delivery_dir(_case)


def cy_evening_late_recovery_decision_sends_image_only_after_delayed_text() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-07"
        assert not has_valid_cy_text_delivery(target_date, "evening", allow_legacy_morning=False)
        _write_canonical_text_receipt(target_date, "evening")
        assert has_valid_cy_text_delivery(target_date, "evening", allow_legacy_morning=False)
        assert not is_valid_cy_image_receipt(target_date, "evening")
        _write_image_receipt(target_date, "evening")
        assert is_valid_cy_image_receipt(target_date, "evening")

    _with_temp_delivery_dir(_case)


def cy_manual_prod_text_receipt_suppresses_scheduled_recovery() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        _write_canonical_text_receipt(target_date, "morning", ids=[9001])
        assert has_valid_cy_text_delivery(target_date, "morning")
        assert not cy_morning_has_valid_production_receipt(target_date)

    _with_temp_delivery_dir(_case)


def cy_legacy_morning_receipt_remains_compatibility_fallback() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        assert not is_valid_cy_text_receipt(target_date, "morning")
        _write_fixture_receipt(target_date, "0 1 * * *")
        assert has_valid_cy_text_delivery(target_date, "morning")

    _with_temp_delivery_dir(_case)


def cy_missing_image_message_id_does_not_validate_receipt() -> None:
    def _case(_tmp: Path) -> None:
        target_date = "2026-07-06"
        _write_image_receipt(target_date, message_id=0)
        assert not is_valid_cy_image_receipt(target_date, "morning")

    _with_temp_delivery_dir(_case)


def cy_image_diagnostics_redacts_secrets() -> None:
    def _case(tmp: Path) -> None:
        old_token = os.environ.get("POLLINATIONS_TOKEN")
        old_diag = os.environ.get("CY_IMAGE_DIAGNOSTICS_DIR")
        secret = "fixture-secret-token"
        try:
            os.environ["POLLINATIONS_TOKEN"] = secret
            # The implementation writes to the standard diagnostics dir; isolate via cwd-like env is not needed here.
            path = _cy_write_image_diagnostics(
                mode="morning",
                target_date="2026-07-06",
                result="failed",
                error=RuntimeError(f"backend failed with {secret} and https://x.test/?token={secret}"),
            )
            data = json.loads(path.read_text(encoding="utf-8"))
            message = data["error"]["message"]
            assert secret not in message
            assert "[redacted]" in message or "[redacted-url]" in message
        finally:
            if old_token is None:
                os.environ.pop("POLLINATIONS_TOKEN", None)
            else:
                os.environ["POLLINATIONS_TOKEN"] = old_token
            if old_diag is None:
                os.environ.pop("CY_IMAGE_DIAGNOSTICS_DIR", None)
            else:
                os.environ["CY_IMAGE_DIAGNOSTICS_DIR"] = old_diag

    _with_temp_delivery_dir(_case)


def cy_image_recovery_force_regenerates_instead_of_reusing_cache() -> None:
    async def _run(tmp: Path) -> tuple[int, list[str]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        old_min = os.environ.get("CY_IMG_MIN_BYTES")
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")
        calls: list[str] = []

        def _generate(_prompt: str, requested_path: str) -> str:
            calls.append(requested_path)
            path = Path(requested_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"fresh image bytes " + str(len(calls)).encode("ascii"))
            return str(path)

        imagegen_stub.generate_astro_image = _generate
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        os.environ["CY_IMG_MIN_BYTES"] = "1"
        try:
            for _ in range(2):
                result = await _build_safe_test_image(
                    REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                    "morning",
                    generate_image=True,
                    send_image_to_test=False,
                    send_image_to_chat=False,
                    image_chat_id=None,
                    image_only_recovery=True,
                )
                assert result["result"] == "generated"
        finally:
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            if old_img_dir is None:
                os.environ.pop("CY_SAFE_IMAGE_DIR", None)
            else:
                os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
            if old_min is None:
                os.environ.pop("CY_IMG_MIN_BYTES", None)
            else:
                os.environ["CY_IMG_MIN_BYTES"] = old_min
        return len(calls), calls

    def _case(tmp: Path) -> None:
        count, paths = asyncio.run(_run(tmp))
        assert count == 2
        assert len(set(paths)) == 2

    _with_temp_delivery_dir(_case)


def cy_test_image_checks_prod_history_and_writes_only_test_history() -> None:
    async def _run(tmp: Path) -> tuple[dict, bytes, list[dict[str, object]]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_prod = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH
        old_test = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH
        old_bot = safe_module.Bot
        old_token = safe_module.TOKEN
        old_env = {name: os.environ.get(name) for name in (
            "CHANNEL_ID_TEST",
            "CY_SAFE_IMAGE_DIR",
            "CY_IMG_MIN_BYTES",
            "CY_DISABLE_BAY_VISUALS",
        )}
        prod = tmp / "cyprus_visual_history_prod.json"
        test = tmp / "cyprus_visual_history_test.json"
        duplicate_seed = tmp / "production.ppm"
        _write_ppm(duplicate_seed, color=(70, 90, 120), comment="production")
        cyprus_visual_dedup.record_cyprus_visual_publication(
            date_value="2026-06-20",
            post_type="evening",
            image_path=duplicate_seed,
            selected_scene="protected_bay",
            prompt_version="cyprus_visual_v5",
            cache_key="fixture-prod",
            style_name="fixture-prod",
            composition="wide panorama composition",
            visual_archetype="bay_panorama",
            history_path=prod,
        )
        prod_before = prod.read_bytes()
        calls: list[str] = []
        photo_calls: list[dict[str, object]] = []
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")

        def _generate(_prompt: str, requested_path: str, **_kwargs):
            calls.append(requested_path)
            path = Path(requested_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            if len(calls) == 1:
                shutil.copy2(duplicate_seed, path)
            else:
                _write_gradient_ppm(path, comment="distinct-test")
            return types.SimpleNamespace(
                path=str(path),
                backend="custom",
                byte_count=path.stat().st_size,
                backend_attempts=[{"backend": "custom"}],
            )

        class FakeBot:
            def __init__(self, token: str) -> None:
                self.token = token

            async def send_photo(self, **kwargs):
                photo_calls.append(kwargs)
                return types.SimpleNamespace(message_id=8080)

        imagegen_stub.generate_astro_image_result_with_exclusions = _generate
        imagegen_stub.generate_astro_image = lambda prompt, path: str(_generate(prompt, path).path)
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = prod
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = test
        safe_module.Bot = FakeBot
        safe_module.TOKEN = "fixture-token"
        os.environ["CHANNEL_ID_TEST"] = "9090"
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        os.environ["CY_IMG_MIN_BYTES"] = "12000"
        os.environ["CY_DISABLE_BAY_VISUALS"] = "1"
        try:
            result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=True,
                send_image_to_chat=False,
                image_chat_id=None,
            )
        finally:
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = old_prod
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = old_test
            safe_module.Bot = old_bot
            safe_module.TOKEN = old_token
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value
        assert prod.read_bytes() == prod_before
        assert len(cyprus_visual_dedup.load_cyprus_visual_history(test)) == 1
        return result, prod_before, photo_calls

    def _case(tmp: Path) -> None:
        result, _prod_before, photo_calls = asyncio.run(_run(tmp))
        assert result["result"] == "sent"
        attempts = result.get("attempts") or []
        assert attempts[0]["dedup_reason"] == "exact_duplicate"
        assert attempts[-1]["dedup_reason"] == "accepted"
        assert len(photo_calls) == 1

    _with_temp_delivery_dir(_case)


async def _run_image_liveness_fixture(tmp: Path, text: str, history: list[dict]) -> tuple[int, dict]:
    world_old = sys.modules.get("world_en")
    imagegen_old = sys.modules.get("world_en.imagegen")
    old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
    old_min = os.environ.get("CY_IMG_MIN_BYTES")
    old_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH
    world_stub = types.ModuleType("world_en")
    imagegen_stub = types.ModuleType("world_en.imagegen")
    calls: list[str] = []
    history_path = tmp / "history.json"
    history_path.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")

    def _generate(_prompt: str, requested_path: str) -> str:
        calls.append(requested_path)
        path = Path(requested_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"liveness image bytes " + str(len(calls)).encode("ascii"))
        return str(path)

    imagegen_stub.generate_astro_image = _generate
    world_stub.imagegen = imagegen_stub
    sys.modules["world_en"] = world_stub
    sys.modules["world_en.imagegen"] = imagegen_stub
    cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = history_path
    os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
    os.environ["CY_IMG_MIN_BYTES"] = "1"
    try:
        result = await _build_safe_test_image(
            text,
            "morning",
            generate_image=True,
            send_image_to_test=False,
            send_image_to_chat=False,
            image_chat_id=None,
            image_only_recovery=False,
        )
    finally:
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = old_history
        if world_old is None:
            sys.modules.pop("world_en", None)
        else:
            sys.modules["world_en"] = world_old
        if imagegen_old is None:
            sys.modules.pop("world_en.imagegen", None)
        else:
            sys.modules["world_en.imagegen"] = imagegen_old
        if old_img_dir is None:
            os.environ.pop("CY_SAFE_IMAGE_DIR", None)
        else:
            os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
        if old_min is None:
            os.environ.pop("CY_IMG_MIN_BYTES", None)
        else:
            os.environ["CY_IMG_MIN_BYTES"] = old_min
    return len(calls), result


def _history_entry_for_liveness(
    index: int,
    *,
    scene: str = "",
    composition: str = "",
    post_type: str = "morning",
) -> dict:
    return {
        "date": f"2026-07-{index + 1:02d}",
        "post_type": post_type,
        "sha256": f"{index:x}" * 64,
        "selected_scene": scene,
        "composition": composition,
        "prompt_version": "cyprus_visual_v5",
        "cache_key": f"fixture-{index}",
        "style_name": "fixture",
    }


def _write_ppm(path: Path, *, color: tuple[int, int, int], comment: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = height = 300
    header = f"P6\n# {comment}\n{width} {height}\n255\n".encode("ascii")
    path.write_bytes(header + bytes(color) * width * height)


def _write_gradient_ppm(path: Path, *, comment: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    width = height = 300
    header = f"P6\n# {comment}\n{width} {height}\n255\n".encode("ascii")
    pixels = bytearray()
    for y in range(height):
        for x in range(width):
            pixels.extend((255 - (x * 255) // width, (y * 255) // height, ((x + y) * 255) // (width + height)))
    path.write_bytes(header + bytes(pixels))


def cy_image_candidate_errors_continue_to_later_candidate() -> None:
    async def _run(tmp: Path) -> tuple[dict, list[str]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        old_min = os.environ.get("CY_IMG_MIN_BYTES")
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")
        calls: list[str] = []

        def _generate(_prompt: str, requested_path: str, **_kwargs):
            calls.append(requested_path)
            path = Path(requested_path)
            if len(calls) == 1:
                raise RuntimeError("fixture candidate backend error")
            if len(calls) == 2:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"x" * 92)
                return types.SimpleNamespace(
                    path=str(path),
                    backend="pollinations",
                    byte_count=92,
                    backend_attempts=[{"backend": "pollinations"}],
                )
            _write_ppm(path, color=(40, 120, 180), comment=f"candidate-{len(calls)}")
            return types.SimpleNamespace(
                path=str(path),
                backend="stable_horde",
                byte_count=path.stat().st_size,
                backend_attempts=[{"backend": "stable_horde"}],
            )

        imagegen_stub.generate_astro_image_result_with_exclusions = _generate
        imagegen_stub.generate_astro_image = lambda prompt, path: str(_generate(prompt, path).path)
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        os.environ["CY_IMG_MIN_BYTES"] = "12000"
        try:
            result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=False,
                image_chat_id=None,
            )
        finally:
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            if old_img_dir is None:
                os.environ.pop("CY_SAFE_IMAGE_DIR", None)
            else:
                os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
            if old_min is None:
                os.environ.pop("CY_IMG_MIN_BYTES", None)
            else:
                os.environ["CY_IMG_MIN_BYTES"] = old_min
        return result, calls

    def _case(tmp: Path) -> None:
        result, calls = asyncio.run(_run(tmp))
        assert result["result"] == "generated"
        assert len(calls) == 3
        attempts = result.get("attempts") or []
        assert attempts[0]["error_type"] == "RuntimeError"
        assert attempts[1]["image_bytes"] == 92
        assert attempts[-1]["backend"] == "stable_horde"

    _with_temp_delivery_dir(_case)


def cy_image_repeated_pollinations_switches_backend_and_sends_receipt() -> None:
    async def _run(tmp: Path) -> tuple[dict, list[set[str]], list[dict[str, object]]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        old_min = os.environ.get("CY_IMG_MIN_BYTES")
        old_channel = os.environ.get("CHANNEL_ID")
        old_token_attr = safe_module.TOKEN
        old_bot = safe_module.Bot
        old_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH
        history_path = tmp / "history.json"
        duplicate_seed = tmp / "duplicate_seed.ppm"
        _write_ppm(duplicate_seed, color=(80, 80, 80), comment="history")
        history = [
            {
                **_history_entry_for_liveness(0),
                "date": "2026-06-27",
                "perceptual_hash": cyprus_visual_dedup.dhash_file(duplicate_seed),
                "phash": cyprus_visual_dedup.phash_file(duplicate_seed),
            }
        ]
        history_path.write_text(json.dumps(history, ensure_ascii=False), encoding="utf-8")

        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")
        excluded_seen: list[set[str]] = []
        photo_calls: list[dict[str, object]] = []

        def _generate(_prompt: str, requested_path: str, *, excluded_backends=None, **_kwargs):
            excluded = set(excluded_backends or set())
            excluded_seen.append(excluded)
            path = Path(requested_path)
            if "pollinations" not in excluded:
                _write_ppm(path, color=(80, 80, 80), comment=f"pollinations-{len(excluded_seen)}")
                return types.SimpleNamespace(
                    path=str(path),
                    backend="pollinations",
                    byte_count=path.stat().st_size,
                    backend_attempts=[{"backend": "pollinations"}],
                )
            _write_gradient_ppm(path, comment="stable-horde-distinct")
            return types.SimpleNamespace(
                path=str(path),
                backend="stable_horde",
                byte_count=path.stat().st_size,
                backend_attempts=[{"backend": "stable_horde"}],
            )

        class FakeBot:
            def __init__(self, token: str) -> None:
                self.token = token

            async def send_photo(self, **kwargs):
                photo_calls.append(kwargs)
                return types.SimpleNamespace(message_id=7001)

        imagegen_stub.generate_astro_image_result_with_exclusions = _generate
        imagegen_stub.generate_astro_image = lambda prompt, path: str(_generate(prompt, path).path)
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = history_path
        safe_module.TOKEN = "fixture-token"
        safe_module.Bot = FakeBot
        os.environ["CHANNEL_ID"] = "777"
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        os.environ["CY_IMG_MIN_BYTES"] = "12000"
        try:
            result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=True,
                image_chat_id=777,
            )
        finally:
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = old_history
            safe_module.TOKEN = old_token_attr
            safe_module.Bot = old_bot
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            if old_img_dir is None:
                os.environ.pop("CY_SAFE_IMAGE_DIR", None)
            else:
                os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
            if old_min is None:
                os.environ.pop("CY_IMG_MIN_BYTES", None)
            else:
                os.environ["CY_IMG_MIN_BYTES"] = old_min
            if old_channel is None:
                os.environ.pop("CHANNEL_ID", None)
            else:
                os.environ["CHANNEL_ID"] = old_channel
        return result, excluded_seen, photo_calls

    def _case(tmp: Path) -> None:
        result, excluded_seen, photo_calls = asyncio.run(_run(tmp))
        assert result["result"] == "sent"
        assert result["backend"] == "stable_horde"
        assert any("pollinations" in value for value in excluded_seen)
        assert len(photo_calls) == 1
        receipt = _cy_image_receipt_path("2026-06-27", "morning")
        assert is_valid_cy_image_receipt("2026-06-27", "morning")
        receipt_data = json.loads(receipt.read_text("utf-8"))
        assert receipt_data["telegram_message_id"] == 7001
        assert receipt_data["backend"] == "stable_horde"
        attempts = result.get("attempts") or []
        assert any(item.get("dedup_reason") == "provider_repeated_output" for item in attempts)
        assert attempts[-1]["backend"] == "stable_horde"

    _with_temp_delivery_dir(_case)


def cy_image_raising_backend_is_bounded_to_ten_calls() -> None:
    async def _run(tmp: Path) -> tuple[dict, list[int]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")
        remaining_values: list[int] = []

        def _raise(_prompt: str, _requested_path: str, *, max_backend_calls: int, **_kwargs):
            remaining_values.append(max_backend_calls)
            raise RuntimeError("fixture provider outage")

        imagegen_stub.generate_astro_image_outcome_with_exclusions = _raise
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        try:
            result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=False,
                image_chat_id=None,
            )
        finally:
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            if old_img_dir is None:
                os.environ.pop("CY_SAFE_IMAGE_DIR", None)
            else:
                os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
        return result, remaining_values

    def _case(tmp: Path) -> None:
        result, remaining_values = asyncio.run(_run(tmp))
        assert result["result"] == "failed_non_fatal"
        assert len(remaining_values) == 10
        assert remaining_values == list(range(10, 0, -1))
        assert result["backend_call_count"] == 10
        assert result["backend_call_limit"] == 10
        assert result["provider_failure_count"] == 10
        diag = tmp / "cy_image_diagnostics" / "2026-06-27-morning" / "image_result.json"
        payload = json.loads(diag.read_text("utf-8"))
        assert payload["backend_call_count"] == 10
        assert payload["final_reason"] == "failed_non_fatal"

    _with_temp_delivery_dir(_case)


def cy_image_duplicates_then_outage_is_failed_after_duplicates() -> None:
    async def _run(tmp: Path) -> dict:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        old_min = os.environ.get("CY_IMG_MIN_BYTES")
        old_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH
        history_path = tmp / "history.json"
        seed = tmp / "seed.ppm"
        _write_ppm(seed, color=(90, 90, 90), comment="history")
        history_path.write_text(
            json.dumps(
                [
                    {
                        **_history_entry_for_liveness(0),
                        "date": "2026-06-26",
                        "perceptual_hash": cyprus_visual_dedup.dhash_file(seed),
                        "phash": cyprus_visual_dedup.phash_file(seed),
                    }
                ],
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")
        calls = 0

        def _outcome(_prompt: str, requested_path: str, *, max_backend_calls: int, **_kwargs):
            nonlocal calls
            calls += 1
            if calls <= 2:
                path = Path(requested_path)
                _write_ppm(path, color=(90, 90, 90), comment=f"duplicate-{calls}")
                result = types.SimpleNamespace(
                    path=str(path),
                    backend="pollinations",
                    byte_count=path.stat().st_size,
                    backend_attempts=[{"backend": "pollinations", "result": "success"}],
                )
                return types.SimpleNamespace(
                    result=result,
                    backend_attempts=result.backend_attempts,
                    error_type="",
                    error_message="",
                    exhausted=False,
                    actual_backend_call_count=1,
                )
            attempts = [
                {"backend": "stable_horde", "result": "failed"}
                for _ in range(max_backend_calls)
            ]
            return types.SimpleNamespace(
                result=None,
                backend_attempts=attempts,
                error_type="ProviderOutage",
                error_message="fixture outage after duplicates",
                exhausted=True,
                actual_backend_call_count=max_backend_calls,
            )

        imagegen_stub.generate_astro_image_outcome_with_exclusions = _outcome
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = history_path
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        os.environ["CY_IMG_MIN_BYTES"] = "12000"
        try:
            return await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=False,
                image_chat_id=None,
            )
        finally:
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = old_history
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            if old_img_dir is None:
                os.environ.pop("CY_SAFE_IMAGE_DIR", None)
            else:
                os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
            if old_min is None:
                os.environ.pop("CY_IMG_MIN_BYTES", None)
            else:
                os.environ["CY_IMG_MIN_BYTES"] = old_min

    def _case(tmp: Path) -> None:
        result = asyncio.run(_run(tmp))
        assert result["result"] == "failed_after_duplicates"
        assert result["result"] != "skipped_duplicate"
        assert result["valid_candidate_count"] == 2
        assert result["duplicate_candidate_count"] == 2
        assert result["provider_failure_count"] == 8
        assert result["backend_call_count"] == 10
        assert not _cy_image_receipt_path("2026-06-27", "morning").exists()

    _with_temp_delivery_dir(_case)


def cy_image_no_available_backends_stops_without_variation_spin() -> None:
    async def _run(tmp: Path) -> tuple[dict, int]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")
        calls = 0

        def _outcome(*_args, **_kwargs):
            nonlocal calls
            calls += 1
            return types.SimpleNamespace(
                result=None,
                backend_attempts=[],
                error_type="NoBackendsAvailable",
                error_message="all backends excluded",
                exhausted=True,
                actual_backend_call_count=0,
            )

        imagegen_stub.generate_astro_image_outcome_with_exclusions = _outcome
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        try:
            result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=False,
                image_chat_id=None,
            )
        finally:
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            if old_img_dir is None:
                os.environ.pop("CY_SAFE_IMAGE_DIR", None)
            else:
                os.environ["CY_SAFE_IMAGE_DIR"] = old_img_dir
        return result, calls

    def _case(tmp: Path) -> None:
        result, calls = asyncio.run(_run(tmp))
        assert result["result"] == "failed_non_fatal"
        assert calls == 1
        assert result["backend_call_count"] == 0
        assert result["provider_failure_count"] == 0

    _with_temp_delivery_dir(_case)


def cy_image_pollinations_receipt_uses_actual_backend() -> None:
    async def _run(tmp: Path) -> tuple[dict, list[dict[str, object]]]:
        world_old = sys.modules.get("world_en")
        imagegen_old = sys.modules.get("world_en.imagegen")
        old_img_dir = os.environ.get("CY_SAFE_IMAGE_DIR")
        old_min = os.environ.get("CY_IMG_MIN_BYTES")
        old_channel = os.environ.get("CHANNEL_ID")
        old_token_attr = safe_module.TOKEN
        old_bot = safe_module.Bot
        old_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH
        history_path = tmp / "history.json"
        history_path.write_text("[]", encoding="utf-8")
        photo_calls: list[dict[str, object]] = []
        world_stub = types.ModuleType("world_en")
        imagegen_stub = types.ModuleType("world_en.imagegen")

        def _outcome(_prompt: str, requested_path: str, **_kwargs):
            path = Path(requested_path)
            _write_gradient_ppm(path, comment="pollinations-distinct")
            attempts = [{"backend": "pollinations", "result": "success"}]
            result = types.SimpleNamespace(
                path=str(path),
                backend="pollinations",
                byte_count=path.stat().st_size,
                backend_attempts=attempts,
            )
            return types.SimpleNamespace(
                result=result,
                backend_attempts=attempts,
                error_type="",
                error_message="",
                exhausted=False,
                actual_backend_call_count=1,
            )

        class FakeBot:
            def __init__(self, token: str) -> None:
                self.token = token

            async def send_photo(self, **kwargs):
                photo_calls.append(kwargs)
                return types.SimpleNamespace(message_id=7002)

        imagegen_stub.generate_astro_image_outcome_with_exclusions = _outcome
        world_stub.imagegen = imagegen_stub
        sys.modules["world_en"] = world_stub
        sys.modules["world_en.imagegen"] = imagegen_stub
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = history_path
        safe_module.TOKEN = "fixture-token"
        safe_module.Bot = FakeBot
        os.environ["CHANNEL_ID"] = "777"
        os.environ["CY_SAFE_IMAGE_DIR"] = str(tmp / "images")
        os.environ["CY_IMG_MIN_BYTES"] = "12000"
        try:
            result = await _build_safe_test_image(
                REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO,
                "morning",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=True,
                image_chat_id=777,
            )
        finally:
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = old_history
            safe_module.TOKEN = old_token_attr
            safe_module.Bot = old_bot
            if world_old is None:
                sys.modules.pop("world_en", None)
            else:
                sys.modules["world_en"] = world_old
            if imagegen_old is None:
                sys.modules.pop("world_en.imagegen", None)
            else:
                sys.modules["world_en.imagegen"] = imagegen_old
            for key, value in (
                ("CY_SAFE_IMAGE_DIR", old_img_dir),
                ("CY_IMG_MIN_BYTES", old_min),
                ("CHANNEL_ID", old_channel),
            ):
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value
        return result, photo_calls

    def _case(tmp: Path) -> None:
        result, photo_calls = asyncio.run(_run(tmp))
        assert result["result"] == "sent"
        assert result["backend"] == "pollinations"
        assert len(photo_calls) == 1
        receipt = json.loads(_cy_image_receipt_path("2026-06-27", "morning").read_text("utf-8"))
        assert receipt["backend"] == "pollinations"

    _with_temp_delivery_dir(_case)


def cy_image_liveness_skips_recent_compositions_before_backend_without_consuming_attempts() -> None:
    text = REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO
    first_five_metadata = [
        build_cyprus_scene_prompt_with_metadata(text, post_type="morning", variation_attempt=attempt)[2]
        for attempt in range(5)
    ]

    history = [
        _history_entry_for_liveness(
            index,
            scene=metadata["selected_scene"],
            composition=metadata["composition"],
        )
        for index, metadata in enumerate(first_five_metadata)
    ]

    def _case(tmp: Path) -> None:
        count, result = asyncio.run(_run_image_liveness_fixture(tmp, text, history))
        assert count == 1
        assert result["result"] == "generated"
        attempts = result.get("attempts") or []
        assert attempts
        assert attempts[0]["dedup_reason"] in {"accepted", "recent_scene_family_lru_allowed", "recent_composition_lru_allowed"}
        assert attempts[0]["composition"] not in {meta["composition"] for meta in first_five_metadata}

    _with_temp_delivery_dir(_case)


def cy_image_liveness_visibility_haze_lru_scene_reaches_backend() -> None:
    text = """
    <b>Кипр: погода на сегодня (27.06.2026)</b>
    Лимассол: +31°, ветер 3 м/с.
    Утром местами локальная дымка/туман, AQI 35, PM₂.₅ 8, PM₁₀ 14.
    🌊 Море: волна спокойная.
    #Кипр #погода
    """
    history = [
        _history_entry_for_liveness(0, scene="coastal_promenade"),
        _history_entry_for_liveness(1, scene="small_harbour"),
    ]

    def _case(tmp: Path) -> None:
        count, result = asyncio.run(_run_image_liveness_fixture(tmp, text, history))
        assert count == 1
        attempts = result.get("attempts") or []
        assert attempts and attempts[0]["dedup_reason"] == "recent_scene_family_lru_allowed"
        assert attempts[0]["selected_scene"] in {"coastal_promenade", "small_harbour"}
        assert attempts[0]["cache_status"] != "not_generated"
        assert result["metadata"]["scene_selection_mode"] == "least_recently_used"

    _with_temp_delivery_dir(_case)


def cy_image_liveness_inland_thunder_lru_scene_reaches_backend() -> None:
    text = """
    <b>Кипр: погода на сегодня (27.06.2026)</b>
    Ларнака: 30/25 °C • переменная облачность • 💨 5 м/с
    Лимассол: 30/23 °C • переменная облачность • 💨 5 м/с
    Тродос: 25/18 °C • возможна гроза в горах
    Лимассол: +32°, ветер 4 м/с.
    #Кипр #погода
    """
    history = [
        _history_entry_for_liveness(0, scene="mountain_coast_view"),
        _history_entry_for_liveness(1, scene="open_sea_cliffs"),
        _history_entry_for_liveness(2, scene="breakwater_coast"),
    ]

    def _case(tmp: Path) -> None:
        count, result = asyncio.run(_run_image_liveness_fixture(tmp, text, history))
        assert count == 1
        attempts = result.get("attempts") or []
        assert attempts and attempts[0]["dedup_reason"] == "recent_scene_family_lru_allowed"
        assert attempts[0]["selected_scene"] in {"mountain_coast_view", "open_sea_cliffs", "breakwater_coast"}
        assert result["metadata"]["scene_selection_mode"] == "least_recently_used"

    _with_temp_delivery_dir(_case)


def cy_image_liveness_prefers_eligible_scene_when_available() -> None:
    text = """
    <b>Кипр: погода на сегодня (27.06.2026)</b>
    Лимассол: +31°, ветер 3 м/с.
    Утром местами локальная дымка/туман, AQI 35, PM₂.₅ 8, PM₁₀ 14.
    🌊 Море: волна спокойная.
    #Кипр #погода
    """
    history = [_history_entry_for_liveness(0, scene="coastal_promenade")]

    def _case(tmp: Path) -> None:
        count, result = asyncio.run(_run_image_liveness_fixture(tmp, text, history))
        assert count == 1
        attempts = result.get("attempts") or []
        assert attempts and attempts[0]["selected_scene"] == "small_harbour"
        assert attempts[0]["dedup_reason"] == "accepted"
        assert result["metadata"]["scene_selection_mode"] == "eligible"

    _with_temp_delivery_dir(_case)


def cy_image_liveness_all_recent_compositions_lru_reaches_backend() -> None:
    text = REAL_LEGACY_MORNING_WITHOUT_SEA_ASTRO
    probe = build_cyprus_scene_prompt_with_metadata(text, post_type="morning", variation_attempt=0)[2]
    selected_scene = str(probe["selected_scene"])
    old_scene_compositions = cy_scene_prompt._CY_SCENE_COMPOSITIONS
    test_compositions = old_scene_compositions[selected_scene]
    history = [
        _history_entry_for_liveness(index, composition=composition)
        for index, composition in enumerate(test_compositions)
    ]

    def _case(tmp: Path) -> None:
        try:
            cy_scene_prompt._CY_SCENE_COMPOSITIONS = {
                **old_scene_compositions,
                selected_scene: test_compositions,
            }
            count, result = asyncio.run(_run_image_liveness_fixture(tmp, text, history))
            assert count == 1
            attempts = result.get("attempts") or []
            assert attempts and attempts[0]["dedup_reason"] == "recent_composition_lru_allowed"
            assert attempts[0]["composition"] == test_compositions[0]
            assert result["metadata"]["composition_selection_mode"] == "least_recently_used"
        finally:
            cy_scene_prompt._CY_SCENE_COMPOSITIONS = old_scene_compositions

    _with_temp_delivery_dir(_case)


def _visibility_payload(
    visibility_m: float,
    *,
    humidity: float = 96,
    temperature: float = 24,
    dew_point: float = 23.5,
    weather_code: int = 45,
    current_visibility: float | None = None,
) -> dict:
    times = [f"2026-07-16T{hour:02d}:00" for hour in range(4, 11)]
    visibilities = [8000.0] * len(times)
    visibilities[2] = visibility_m
    return {
        "current": {
            "time": "2026-07-16T03:20",
            "visibility": current_visibility if current_visibility is not None else max(visibility_m, 7000),
            "relative_humidity_2m": humidity,
            "temperature_2m": temperature,
            "dew_point_2m": dew_point,
            "weather_code": weather_code,
        },
        "hourly": {
            "time_local": times,
            "visibility": visibilities,
            "relative_humidity_2m": [humidity] * len(times),
            "temperature_2m": [temperature] * len(times),
            "dew_point_2m": [dew_point] * len(times),
            "weather_code": [weather_code] * len(times),
        },
    }


def cy_visibility_thresholds_and_dust_classification_are_stable() -> None:
    dense = get_cyprus_visibility_context(_visibility_payload(320), target_date="2026-07-16")
    fog = get_cyprus_visibility_context(_visibility_payload(900, weather_code=3), target_date="2026-07-16")
    mist = get_cyprus_visibility_context(_visibility_payload(2200, weather_code=3), target_date="2026-07-16")
    clear = get_cyprus_visibility_context(_visibility_payload(8000, humidity=60, dew_point=16, weather_code=0), target_date="2026-07-16")
    mixed = get_cyprus_visibility_context(
        _visibility_payload(600),
        target_date="2026-07-16",
        air_data={"aqi": 130, "pm10": 65},
    )
    dust = get_cyprus_visibility_context(
        _visibility_payload(2500, humidity=55, dew_point=14, weather_code=3),
        target_date="2026-07-16",
        air_data={"aqi": 125, "pm10": 90},
    )
    assert dense.condition == "dense_fog"
    assert fog.condition == "fog"
    assert mist.condition == "mist"
    assert clear.condition == "clear"
    assert build_cyprus_visibility_line(clear) is None
    assert mixed.dust_vs_fog_classification == "mixed_humid_haze_and_pollution"
    assert "смесь влажной дымки и загрязнения воздуха" in (build_cyprus_visibility_line(mixed) or "")
    assert dust.condition == "dust_haze"
    assert dust.dust_vs_fog_classification == "dust_haze"


def cy_visibility_wmo_fog_and_weather_request_fallback_are_safe() -> None:
    payload = _visibility_payload(8000, current_visibility=None)
    payload["current"].pop("visibility", None)
    payload["current"]["weather_code"] = 45
    payload["hourly"]["visibility"] = []
    context = get_cyprus_visibility_context(payload, target_date="2026-07-16")
    assert context.condition == "dense_fog"
    unavailable = get_cyprus_visibility_context({}, target_date="2026-07-16")
    assert unavailable.condition == "clear"
    assert build_cyprus_visibility_line(unavailable) is None
    rich_url = weather_module._build_url(34.707, 33.022, "Asia/Nicosia", weather_module.ATTEMPTS[0])
    minimal_url = weather_module._build_url(34.707, 33.022, "Asia/Nicosia", weather_module.ATTEMPTS[2])
    assert "visibility" in rich_url and "dew_point_2m" in rich_url
    assert "visibility" not in minimal_url and "dew_point_2m" not in minimal_url

    old_dir = os.environ.get("CY_VISIBILITY_DIAGNOSTICS_DIR")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["CY_VISIBILITY_DIAGNOSTICS_DIR"] = tmp
            save_cyprus_visibility_diagnostics(context, post_type="morning", fog_text_added=True)
            diagnostic = json.loads((Path(tmp) / "2026-07-16-morning.json").read_text("utf-8"))
        finally:
            if old_dir is None:
                os.environ.pop("CY_VISIBILITY_DIAGNOSTICS_DIR", None)
            else:
                os.environ["CY_VISIBILITY_DIAGNOSTICS_DIR"] = old_dir
    required = {
        "current_visibility_m",
        "morning_min_visibility_m",
        "humidity_pct",
        "dew_point_c",
        "dew_point_spread_c",
        "weather_code",
        "visibility_condition",
        "visibility_evidence",
        "fog_text_added",
        "fog_visual_rule",
        "dust_vs_fog_classification",
    }
    assert required <= set(diagnostic)


def cy_morning_fog_survives_format_and_changes_score_nuance_plan() -> None:
    fog_line = build_cyprus_visibility_line(
        get_cyprus_visibility_context(_visibility_payload(320), target_date="2026-07-16")
    )
    assert fog_line
    legacy = MORNING_WITH_SEA.replace("☀️ <b>УФ-индекс", fog_line + "\n☀️ <b>УФ-индекс")
    formatted = build_morning_format_v2("Кипр", legacy)
    assert formatted.index("💨 Ветер") < formatted.index("🌫 Видимость:") < formatted.index("☀️ УФ")
    clear_score = _cyprus_score_line(build_morning_format_v2("Кипр", MORNING_WITH_SEA))
    fog_score = _cyprus_score_line(formatted)
    assert float(fog_score.split(":", 1)[1].split("/", 1)[0].strip()) < float(clear_score.split(":", 1)[1].split("/", 1)[0].strip())
    assert _cyprus_main_nuance(formatted) == "⚠️ Главный нюанс: до рассеивания тумана осторожнее на дорогах и развязках."
    assert _cyprus_smart_plan_line(formatted) == "✅ План: утром снизить скорость и увеличить дистанцию; после прояснения — вода, SPF и тень."

    mixed_context = get_cyprus_visibility_context(
        _visibility_payload(600),
        target_date="2026-07-16",
        air_data={"aqi": 130, "pm10": 65},
    )
    mixed_line = build_cyprus_visibility_line(mixed_context)
    high_air_legacy = MORNING_WITH_SEA.replace("AQI 58", "AQI 130")
    high_air = build_morning_format_v2("Кипр", high_air_legacy)
    mixed_air = build_morning_format_v2(
        "Кипр",
        high_air_legacy.replace("☀️ <b>УФ-индекс", (mixed_line or "") + "\n☀️ <b>УФ-индекс"),
    )
    assert _cyprus_score_line(mixed_air).split("/10", 1)[0] == _cyprus_score_line(high_air).split("/10", 1)[0]


def cy_evening_preserves_only_tomorrow_morning_visibility() -> None:
    context = get_cyprus_visibility_context(
        _visibility_payload(900),
        post_type="evening",
        target_date="2026-07-16",
    )
    line = build_cyprus_visibility_line(context, post_type="evening")
    assert line and "завтра утром" in line
    legacy = """<b>Кипр: погода на завтра (16.07.2026)</b>
✨ VayboMeter завтра: 8.0/10 — хорошо.
🏖 <b>Морские города</b>
Лимассол: 31/24 °C • ясно • 💨 4 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 35/23 °C • ясно
———
{line}
#Кипр #погода
""".format(line=line)
    formatted = build_format_v2("Кипр", "evening", legacy)
    assert line in formatted
    assert "туман весь день" not in formatted.lower()


def cy_morning_safe_production_polish_keeps_fog_actions() -> None:
    fog_line = build_cyprus_visibility_line(
        get_cyprus_visibility_context(_visibility_payload(320), target_date="2026-07-16")
    )
    legacy = MORNING_WITH_SEA.replace("☀️ <b>УФ-индекс", (fog_line or "") + "\n☀️ <b>УФ-индекс")
    old_values = {key: os.environ.get(key) for key in ("MORNING_VAYBOMETER_SCORE", "FORMAT_V2_MAIN_NUANCE", "MORNING_SMART_PLAN")}
    try:
        os.environ.update({key: "1" for key in old_values})
        sanitized_legacy = sanitize_post_text(legacy).text
        final = build_morning_format_v2("Кипр", sanitized_legacy)
        final = _inject_morning_score(final, "morning")
        final = _insert_main_nuance(final)
        final = _inject_morning_smart_plan(final, "morning")
        final = sanitize_post_text(final).text
    finally:
        for key, value in old_values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
    assert fog_line in final
    assert "✨ VayboMeter:" in final and "утренний туман" in final
    assert "⚠️ Главный нюанс: до рассеивания тумана осторожнее на дорогах и развязках." in final
    assert "✅ План: утром снизить скорость и увеличить дистанцию; после прояснения — вода, SPF и тень." in final


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
        cy_morning_hashtags_are_final_without_editorial_tail,
        cy_morning_real_safe_path_restores_sea_and_astro_from_raw,
        cy_morning_image_failure_still_sends_text_chunks,
        cy_morning_recovery_publishes_then_delayed_primary_skips_by_receipt,
        cy_morning_primary_publishes_then_recovery_skips_by_receipt,
        cy_morning_failed_primary_without_text_receipt_allows_recovery,
        cy_morning_dry_run_does_not_create_delivery_receipt,
        cy_morning_test_channel_send_does_not_create_production_receipt,
        cy_morning_image_failure_plus_successful_text_creates_receipt,
        cy_morning_successful_github_job_without_receipt_does_not_suppress_recovery,
        cy_morning_receipt_uses_asia_nicosia_target_date,
        cy_morning_partial_text_failure_has_no_completed_receipt,
        cy_morning_image_phase_names_match_delivery_state,
        cy_text_and_image_receipt_validators_are_strict,
        cy_morning_delayed_0315_decision_sends_image_only_when_text_exists,
        cy_evening_late_recovery_decision_sends_image_only_after_delayed_text,
        cy_manual_prod_text_receipt_suppresses_scheduled_recovery,
        cy_legacy_morning_receipt_remains_compatibility_fallback,
        cy_missing_image_message_id_does_not_validate_receipt,
        cy_image_diagnostics_redacts_secrets,
        cy_image_recovery_force_regenerates_instead_of_reusing_cache,
        cy_test_image_checks_prod_history_and_writes_only_test_history,
        cy_image_candidate_errors_continue_to_later_candidate,
        cy_image_repeated_pollinations_switches_backend_and_sends_receipt,
        cy_image_raising_backend_is_bounded_to_ten_calls,
        cy_image_duplicates_then_outage_is_failed_after_duplicates,
        cy_image_no_available_backends_stops_without_variation_spin,
        cy_image_pollinations_receipt_uses_actual_backend,
        cy_image_liveness_skips_recent_compositions_before_backend_without_consuming_attempts,
        cy_image_liveness_visibility_haze_lru_scene_reaches_backend,
        cy_image_liveness_inland_thunder_lru_scene_reaches_backend,
        cy_image_liveness_prefers_eligible_scene_when_available,
        cy_image_liveness_all_recent_compositions_lru_reaches_backend,
        cy_visibility_thresholds_and_dust_classification_are_stable,
        cy_visibility_wmo_fog_and_weather_request_fallback_are_safe,
        cy_morning_fog_survives_format_and_changes_score_nuance_plan,
        cy_evening_preserves_only_tomorrow_morning_visibility,
        cy_morning_safe_production_polish_keeps_fog_actions,
    )
    for check in checks:
        check()
        print(f"PASS: {check.__name__}")
    print(f"OK: {len(checks)} Cyprus morning FORMAT_V2 checks passed")


if __name__ == "__main__":
    main()
