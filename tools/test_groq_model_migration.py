#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Groq migration and Cyprus factual fallback regression checks."""
from __future__ import annotations

import os
import subprocess
import sys
import types
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PRIMARY_MODEL = "openai/gpt-oss-120b"
FALLBACK_MODEL = "openai/gpt-oss-20b"
GEMINI_PRIMARY_MODEL = "gemini-3.7-flash"
GEMINI_FALLBACK_MODEL = "gemini-2.5-flash"

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
from safe_test_post import _apply_cyprus_morning_raw_context, _apply_cyprus_sensor_cleanup  # noqa: E402


def _deprecated_model_ids() -> tuple[str, str]:
    return ("llama-" + "3.3-70b-versatile", "llama-" + "3.1-8b-instant")


def _tracked_runtime_files() -> list[Path]:
    proc = subprocess.run(
        ["git", "ls-files"],
        cwd=ROOT,
        check=True,
        text=True,
        capture_output=True,
    )
    exts = {".py", ".yml", ".yaml", ".md"}
    names = {".env.example", ".env"}
    out: list[Path] = []
    for rel in proc.stdout.splitlines():
        path = ROOT / rel
        if path.suffix in exts or path.name in names:
            out.append(path)
    return out


def test_no_deprecated_model_ids_in_runtime_files() -> None:
    offenders: list[str] = []
    deprecated = _deprecated_model_ids()
    for path in _tracked_runtime_files():
        text = path.read_text(encoding="utf-8", errors="ignore")
        for model_id in deprecated:
            if model_id in text:
                offenders.append(str(path.relative_to(ROOT)))
                break
    assert not offenders, "deprecated Groq model IDs remain in: " + ", ".join(offenders)


def _import_gpt_fresh():
    for name in (
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "GEMINI_MODEL",
        "GEMINI_FALLBACK_MODEL",
        "GEMINI_MODELS",
        "GROQ_API_KEY",
        "GROQ_MODEL",
        "GROQ_FALLBACK_MODEL",
    ):
        os.environ.pop(name, None)
    sys.modules.pop("gpt", None)
    import gpt  # type: ignore

    return gpt


class _FakeGroqClient:
    def __init__(self, failures: set[str]) -> None:
        self.failures = failures
        self.calls: list[str] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, *, model: str, messages, temperature: float, max_tokens: int):
        self.calls.append(model)
        if model in self.failures:
            raise RuntimeError("429 rate limit")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(
                        content="Если завтра что-то пойдёт не так, вините ветер!\nПейте воду\nДышите ровно\nЛожитесь раньше"
                    )
                )
            ]
        )


class _FakeGeminiClient:
    def __init__(self, failures: set[str]) -> None:
        self.failures = failures
        self.calls: list[dict] = []
        self.models = SimpleNamespace(
            list=lambda: SimpleNamespace(
                data=[
                    SimpleNamespace(id=GEMINI_PRIMARY_MODEL),
                    SimpleNamespace(id=GEMINI_FALLBACK_MODEL),
                ]
            )
        )
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **request):
        self.calls.append(request)
        if request["model"] in self.failures:
            raise RuntimeError("model not found")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="Gemini fallback text"))]
        )


def _force_groq_only(gpt, client: _FakeGroqClient) -> None:
    gpt.OPENAI_KEY = ""
    gpt.GEMINI_KEY = ""
    gpt.GROQ_KEY = "test"
    gpt.GROQ_MODEL = PRIMARY_MODEL
    gpt.GROQ_FALLBACK_MODEL = FALLBACK_MODEL
    gpt.GROQ_MODELS = [PRIMARY_MODEL, FALLBACK_MODEL]
    gpt._OPENAI_DISABLED_FOR_RUN = False
    gpt._GEMINI_DISABLED_FOR_RUN = False
    gpt._groq_client = lambda: client


def test_default_groq_model_config() -> None:
    gpt = _import_gpt_fresh()
    assert gpt.GROQ_MODEL == PRIMARY_MODEL
    assert gpt.GROQ_FALLBACK_MODEL == FALLBACK_MODEL
    assert gpt.GROQ_MODELS == [PRIMARY_MODEL, FALLBACK_MODEL]


def test_default_gemini_model_config() -> None:
    gpt = _import_gpt_fresh()
    assert gpt.GEMINI_MODEL == GEMINI_PRIMARY_MODEL
    assert gpt.GEMINI_FALLBACK_MODEL == GEMINI_FALLBACK_MODEL
    assert gpt.GEMINI_MODELS == [GEMINI_PRIMARY_MODEL, GEMINI_FALLBACK_MODEL]


def test_gemini_37_falls_back_without_legacy_sampling_controls() -> None:
    gpt = _import_gpt_fresh()
    client = _FakeGeminiClient(failures={GEMINI_PRIMARY_MODEL})
    gpt.OPENAI_KEY = ""
    gpt.GEMINI_KEY = "test"
    gpt.GROQ_KEY = ""
    gpt._OPENAI_DISABLED_FOR_RUN = False
    gpt._GEMINI_DISABLED_FOR_RUN = False
    gpt._GEMINI_MODEL_SET = None
    gpt._gemini_openai_compat_client = lambda: client

    text = gpt.gpt_complete("test prompt", temperature=0.7, max_tokens=80)

    assert text == "Gemini fallback text"
    assert [call["model"] for call in client.calls] == [GEMINI_PRIMARY_MODEL, GEMINI_FALLBACK_MODEL]
    primary_request, fallback_request = client.calls
    assert "temperature" not in primary_request
    assert "top_p" not in primary_request
    assert "top_k" not in primary_request
    assert fallback_request["temperature"] == 0.7


def test_removed_gemini_preview_ids_are_not_runtime_candidates() -> None:
    runtime = (ROOT / "gpt.py").read_text(encoding="utf-8")
    removed = ("gemini-" + "3-flash", "gemini-" + "3-pro", "gemini-" + "3-flash-preview")
    assert not [model for model in removed if model in runtime]


def test_primary_failure_attempts_fallback_model() -> None:
    gpt = _import_gpt_fresh()
    client = _FakeGroqClient(failures={PRIMARY_MODEL})
    _force_groq_only(gpt, client)

    text = gpt.gpt_complete("test prompt", temperature=0.7, max_tokens=80)

    assert client.calls == [PRIMARY_MODEL, FALLBACK_MODEL]
    assert isinstance(text, str)
    assert text.startswith("Если завтра")


def test_total_groq_failure_uses_local_blurb_fallback() -> None:
    gpt = _import_gpt_fresh()
    client = _FakeGroqClient(failures={PRIMARY_MODEL, FALLBACK_MODEL})
    _force_groq_only(gpt, client)

    summary, tips = gpt.gpt_blurb("жара")

    assert client.calls == [PRIMARY_MODEL, FALLBACK_MODEL]
    assert isinstance(summary, str)
    assert 0 < len(summary) < 180
    assert isinstance(tips, list)
    assert len(tips) == 3
    assert all(isinstance(tip, str) and tip for tip in tips)


RAW_WITH_COASTAL_SEA = """<b>Кипр: погода, жара и море (27.06.2026)</b>
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

LEGACY_WITHOUT_SEA = """<b>Кипр: погода, жара и море (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31
💨 Ветер: 3.0 м/с • 🔹 1009 гПа →
🌇 Закат сегодня: 20:05
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""

NO_MARINE_DATA = """<b>Кипр: погода, жара и море (27.06.2026)</b>
👋 Доброе утро! Теплее всего — Никосия (37°), прохладнее — Пафос (30°).
☀️ <b>УФ-индекс 9 (Very High)</b>: тень 11–16.
🏭 AQI 58 (умеренный) • PM₂.₅ 14 / PM₁₀ 31
💨 Ветер 6 м/с, давление 1009 гПа.
🌇 Закат сегодня: 20:05
✨ 96% освещённости — Луна яркая.
✅ Сегодня: вода, SPF, тень.
#Кипр #погода #здоровье
"""

RAW_WINTER_WITH_SEA = """<b>Кипр: погода и море (15.01.2026)</b>
👋 Доброе утро! Теплее всего — Ларнака (19°), прохладнее — Тродос (8°).
🏭 AQI 42 (низкий) • PM₂.₅ 9 / PM₁₀ 18
💨 Ветер: 3.0 м/с • 🔹 1014 гПа →
Море у Ларнаки: вода 19°C, волна спокойная.
🌇 Закат сегодня: 17:02
✅ Сегодня: прогулка у моря, слой от ветра.
#Кипр #погода #здоровье
"""

LEGACY_WINTER_WITHOUT_SEA = """<b>Кипр: погода и море (15.01.2026)</b>
👋 Доброе утро! Теплее всего — Ларнака (19°), прохладнее — Тродос (8°).
🏭 AQI 42 (низкий) • PM₂.₅ 9 / PM₁₀ 18
💨 Ветер: 3.0 м/с • 🔹 1014 гПа →
🌇 Закат сегодня: 19:05
✅ Сегодня: прогулка у моря, слой от ветра.
#Кипр #погода #здоровье
"""


def cy_real_path_uses_only_marine_numbers_for_sea() -> None:
    legacy = sanitize_post_text(LEGACY_WITHOUT_SEA)
    text = build_morning_format_v2("Кипр", legacy.text)
    text = _apply_cyprus_morning_raw_context(text, RAW_WITH_COASTAL_SEA, legacy.text, "morning")
    text = _apply_cyprus_sensor_cleanup(text)
    text = sanitize_post_text(text).text

    assert "🌊 Море: средняя вода 27°C; лучше до 11:00 или после 18:30." in text
    assert "🌊 Море: вода 20°C" not in text
    assert "🌇 Закат сегодня: 20:05" in text
    assert text.splitlines()[-1] == "#Кипр #погода #здоровье"


def cy_missing_marine_data_is_transparent() -> None:
    text = build_morning_format_v2("Кипр", NO_MARINE_DATA)
    assert "🌊 Море: данные о температуре воды обновляются; лучше до 11:00 или после 18:30." in text
    assert "🌊 Море: вода 20°C" not in text
    assert "🌊 Море: вода 31°C" not in text


def cy_winter_raw_path_accepts_explicit_marine_temperature() -> None:
    legacy = sanitize_post_text(LEGACY_WINTER_WITHOUT_SEA)
    text = build_morning_format_v2("Кипр", legacy.text)
    text = _apply_cyprus_morning_raw_context(text, RAW_WINTER_WITH_SEA, legacy.text, "morning")
    text = sanitize_post_text(text).text

    assert "🌊 Море: вода 19°C; волна спокойная; лучше до 11:00 или после 18:30." in text


def cy_winter_raw_path_does_not_use_sunset_time_as_sea() -> None:
    legacy = sanitize_post_text(LEGACY_WINTER_WITHOUT_SEA)
    text = build_morning_format_v2("Кипр", legacy.text)
    text = _apply_cyprus_morning_raw_context(text, LEGACY_WINTER_WITHOUT_SEA, legacy.text, "morning")
    text = sanitize_post_text(text).text

    assert "🌊 Море: данные о температуре воды обновляются; лучше до 11:00 или после 18:30." in text
    assert "🌊 Море: вода 19°C" not in text


def main() -> None:
    tests = [
        test_no_deprecated_model_ids_in_runtime_files,
        test_default_groq_model_config,
        test_default_gemini_model_config,
        test_gemini_37_falls_back_without_legacy_sampling_controls,
        test_removed_gemini_preview_ids_are_not_runtime_candidates,
        test_primary_failure_attempts_fallback_model,
        test_total_groq_failure_uses_local_blurb_fallback,
        cy_real_path_uses_only_marine_numbers_for_sea,
        cy_missing_marine_data_is_transparent,
        cy_winter_raw_path_accepts_explicit_marine_temperature,
        cy_winter_raw_path_does_not_use_sunset_time_as_sea,
    ]
    for test in tests:
        test()
    print("PASS groq_model_migration")


if __name__ == "__main__":
    main()
