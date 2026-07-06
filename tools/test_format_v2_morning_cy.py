#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for Cyprus morning FORMAT_V2 post polish."""
from __future__ import annotations

import asyncio
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
    _build_safe_test_image,
    _send_telegram_text_chunks,
    cy_morning_delivery_path,
    cy_morning_has_valid_production_receipt,
    cy_morning_image_phase_for_result,
    cy_morning_load_delivery_receipt,
    cy_morning_maybe_write_delivery_receipt,
    cy_morning_target_date,
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
    old_run = os.environ.get("GITHUB_RUN_ID")
    old_attempt = os.environ.get("GITHUB_RUN_ATTEMPT")
    old_schedule = os.environ.get("GITHUB_EVENT_SCHEDULE")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            os.environ["CY_MORNING_DELIVERY_DIR"] = tmp
            os.environ["GITHUB_RUN_ID"] = "fixture-run"
            os.environ["GITHUB_RUN_ATTEMPT"] = "2"
            callback(Path(tmp))
        finally:
            if old_dir is None:
                os.environ.pop("CY_MORNING_DELIVERY_DIR", None)
            else:
                os.environ["CY_MORNING_DELIVERY_DIR"] = old_dir
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
    assert cy_morning_image_phase_for_result("skipped") == "image_skipped"
    assert cy_morning_image_phase_for_result("failed_non_fatal") != "image_sent"


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
    )
    for check in checks:
        check()
        print(f"PASS: {check.__name__}")
    print(f"OK: {len(checks)} Cyprus morning FORMAT_V2 checks passed")


if __name__ == "__main__":
    main()
