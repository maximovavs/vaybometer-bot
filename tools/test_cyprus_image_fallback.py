#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline recovery checks for Cyprus provider health and local weather cards."""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import inspect
import json
import os
from pathlib import Path
import tempfile
import types

ROOT = Path(__file__).resolve().parents[1]
import sys

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

from PIL import Image, ImageDraw  # type: ignore  # noqa: E402

import cyprus_visual_dedup  # noqa: E402
import cyprus_image_recovery  # noqa: E402
from cyprus_image_recovery import (  # noqa: E402
    LOCAL_INFORMATIVE_COVER_BRANDING,
    LOCAL_INFORMATIVE_COVER_VERSION,
    load_provider_health,
    mark_provider_duplicate,
    provider_health_exclusions,
    provider_health_path,
    record_provider_attempts,
    render_local_informative_cover,
    write_provider_health,
)
import safe_test_post as safe_module  # noqa: E402
import world_en.imagegen as imagegen  # noqa: E402


MESSAGE = """🌅 Кипр сегодня (15.07.2026)
✨ VayboMeter: 6.8/10 — с оговорками; жара и порывы у моря.
🏙 Лимассол — 34/26 °C • переменная облачность • ветер 8 м/с, порывы 14 м/с • 🌊 27°C
🏙 Ларнака — 35/25 °C • локальная облачность • ветер 7 м/с, порывы 13 м/с • 🌊 27°C
⚠️ Нюанс: локальные осадки возможны ближе к внутренним районам.
✅ План: раннее море, днём тень и вода.
#Кипр #погода #здоровье
"""


EVENING_MESSAGE = """🌅 Кипр завтра (16.07.2026)
✨ VayboMeter завтра: 6.8/10 — с оговорками; жара и порывы у моря.
🏙 Лимассол — 34/26 °C • переменная облачность • ветер 8 м/с, порывы 14 м/с • 🌊 27°C
🏙 Ларнака — 35/25 °C • локальная облачность • ветер 7 м/с, порывы 13 м/с • 🌊 27°C
⚠️ Нюанс: локальные осадки возможны ближе к внутренним районам.
🌕 Полнолуние — 100% освещённости.
✅ План завтра: море до усиления ветра, затем тень и вода.
#Кипр #погода #здоровье
"""


PROBLEM_EVENING_MESSAGE = """🌅 Кипр завтра (20.07.2026)
🏙 Никосия — 38/24 °C • жарко • переменная облачность
🏙 Лимассол — 33/24 °C • переменная облачность • ветер 8 м/с, порывы до 15 м/с • 🌊 28°C
🏙 Ларнака — 32/24 °C • облачно • ветер 7 м/с • 🌊 29°C
✅ План завтра: тень в центре, у моря проверить порывы.
#Кипр #погода
"""


AIR_ONLY_MESSAGE = """🌅 Кипр завтра (22.07.2026)
🏭 Воздух сейчас: AQI 25 · хороший.
✅ План завтра: обычные дела и прогулка.
#Кипр #погода
"""


ISLAND_GUST_MESSAGE = """🌅 Кипр завтра (23.07.2026)
🏙 Никосия — 36/24 °C • переменная облачность • порывы до 17.5 м/с.
✅ План завтра: проверить порывы перед выездом.
#Кипр #погода
"""


LONGEST_HOTTEST_CITY_MESSAGE = """🌅 Кипр завтра (24.07.2026)
🏙 Айя-Напа — 39.5/27 °C • жарко • переменная облачность.
🏙 Никосия — 38/25 °C • жарко.
✅ План завтра: тень и вода в жаркие часы.
#Кипр #погода
"""


def _write_dhash_fixture(path: Path, *, flipped_rows: int) -> None:
    rows: list[int] = []
    for row in range(8):
        if row < flipped_rows:
            rows.extend((220, 20, 40, 60, 80, 100, 120, 140, 160))
        else:
            rows.extend((0, 20, 40, 60, 80, 100, 120, 140, 160))
    image = Image.new("L", (9, 8))
    image.putdata(rows)
    image = image.resize((288, 256), Image.Resampling.NEAREST).convert("RGB")
    path.parent.mkdir(parents=True, exist_ok=True)
    image.save(path, format="PPM")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_valid_image_receipt(
    target_date: str,
    post_type: str,
    *,
    message_id: int = 7001,
) -> Path:
    path = safe_module._cy_image_receipt_path(target_date, post_type)
    safe_module._cy_write_json_atomic(
        path,
        {
            "target_date": target_date,
            "post_type": post_type,
            "chat_type": "production",
            "telegram_message_id": message_id,
            "sha256": "a" * 64,
            "selected_scene": "fixture_scene",
            "sent_at_utc": "2026-07-15T18:00:00Z",
        },
    )
    return path


def _write_valid_text_receipt(target_date: str, post_type: str) -> Path:
    path = safe_module._cy_text_receipt_path(target_date, post_type)
    safe_module._cy_write_json_atomic(
        path,
        {
            "target_date": target_date,
            "post_type": post_type,
            "chat_type": "production",
            "telegram_message_ids": [6001],
            "text_chunk_count": 1,
            "sent_at_utc": "2026-07-15T17:59:00Z",
        },
    )
    return path


def local_informative_cover_is_valid_deterministic_and_factual() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        first = render_local_informative_cover(
            MESSAGE,
            target_date="2026-07-15",
            post_type="morning",
            output_path=tmp / "first.png",
            minimum_bytes=12000,
        )
        second = render_local_informative_cover(
            MESSAGE,
            target_date="2026-07-15",
            post_type="morning",
            output_path=tmp / "second.png",
            minimum_bytes=12000,
        )
        evening = render_local_informative_cover(
            PROBLEM_EVENING_MESSAGE,
            target_date="2026-07-20",
            post_type="evening",
            output_path=tmp / "evening.png",
            minimum_bytes=12000,
        )
        first_path = Path(first["path"])
        second_path = Path(second["path"])
        evening_path = Path(evening["path"])
        assert first_path.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
        assert first_path.stat().st_size > 12000
        assert _sha256(first_path) == _sha256(second_path)
        assert _sha256(first_path) != _sha256(evening_path)
        required_metadata = {
            "renderer_version",
            "branding",
            "branding_bbox",
            "visual_forecast_period",
            "primary_weather",
            "hazards",
            "scene_focus",
            "headline",
            "primary_fact",
            "secondary_fact",
            "tertiary_fact",
            "actual_precipitation",
            "explicit_storm",
            "severe_wind",
            "rendered_text",
            "palette",
            "cache_key",
        }
        assert required_metadata <= set(first["metadata"])
        assert required_metadata <= set(evening["metadata"])
        assert first["metadata"]["renderer_version"] == LOCAL_INFORMATIVE_COVER_VERSION
        assert evening["metadata"]["renderer_version"] == LOCAL_INFORMATIVE_COVER_VERSION
        assert first["metadata"]["branding"] == LOCAL_INFORMATIVE_COVER_BRANDING
        assert first["metadata"]["rendered_text"].splitlines()[0] == LOCAL_INFORMATIVE_COVER_BRANDING
        brand_left, brand_top, brand_right, brand_bottom = json.loads(
            first["metadata"]["branding_bbox"]
        )
        assert 92 <= brand_left < brand_right <= 988
        assert 80 <= brand_top < brand_bottom <= 300
        assert evening["metadata"]["headline"] == "КИПР ЗАВТРА"
        assert evening["metadata"]["primary_fact"] == "🔥 ДО 38° В НИКОСИИ"
        assert evening["metadata"]["secondary_fact"] == "💨 ПОРЫВЫ ДО 15 М/С У МОРЯ"
        assert evening["metadata"]["actual_precipitation"] == "false"
        assert evening["metadata"]["explicit_storm"] == "false"
        assert evening["metadata"]["severe_wind"] == "true"
        assert evening["metadata"]["visual_forecast_period"] == "representative_daytime"
        assert evening["metadata"]["scene_focus"] == "coast_inland_contrast"
        assert "🌧" not in evening["metadata"]["rendered_text"]
        assert safe_module._cy_image_caption(
            "morning",
            "2026-07-16",
            test_label=False,
            current_date=dt.date(2026, 7, 16),
        ) == "Визуальный вайб сегодняшнего дня на Кипре 🌊"
        assert safe_module._cy_image_caption(
            "evening",
            "2026-07-16",
            test_label=False,
            current_date=dt.date(2026, 7, 15),
        ) == "Визуальный вайб погоды на Кипре завтра 🌊"
        with Image.open(first_path) as image:
            assert image.size == (1080, 1080)
            assert image.format == "PNG"
            assert image.info["backend"] == "local_informative_cover"
            assert image.info["target_date"] == "2026-07-15"
            assert image.info["post_type"] == "morning"
            assert image.info["headline"] == "КИПР СЕГОДНЯ"
            assert image.info["branding"] == LOCAL_INFORMATIVE_COVER_BRANDING
            assert image.info["branding_bbox"] == first["metadata"]["branding_bbox"]
            assert image.info["rendered_text"]
            image.verify()
        with Image.open(evening_path) as image:
            assert image.size == (1080, 1080)
            assert image.info["post_type"] == "evening"
            assert image.info["visual_forecast_period"] == "representative_daytime"
            assert image.info["palette"] == "hot"
            image.verify()


def informative_cover_long_facts_fit_pixel_bounds() -> None:
    fixtures = (
        ("air", AIR_ONLY_MESSAGE, "🏭 ВОЗДУХ СЕЙЧАС: AQI 25 · ХОРОШИЙ"),
        ("gust", ISLAND_GUST_MESSAGE, "💨 ПОРЫВЫ ДО 17.5 М/С НА ОСТРОВЕ"),
        ("hottest", LONGEST_HOTTEST_CITY_MESSAGE, "🔥 ДО 39.5° · АЙЯ-НАПА"),
        ("three", PROBLEM_EVENING_MESSAGE, "🔥 ДО 38° В НИКОСИИ"),
    )
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        rendered: dict[str, dict[str, object]] = {}
        for name, message, expected_fact in fixtures:
            result = render_local_informative_cover(
                message,
                target_date=f"2026-07-{22 + len(rendered):02d}",
                post_type="evening",
                output_path=tmp / f"{name}.png",
                minimum_bytes=12000,
            )
            rendered[name] = result
            metadata = result["metadata"]
            full_facts = {
                metadata["primary_fact"],
                metadata["secondary_fact"],
                metadata["tertiary_fact"],
            }
            assert expected_fact in full_facts
            layout = json.loads(metadata["fact_layout"])
            assert 1 <= len(layout) <= 3
            assert {item["source_fact"] for item in layout} == {fact for fact in full_facts if fact}

            previous_bottom = 0
            with Image.open(result["path"]) as image:
                assert image.size == (1080, 1080)
                assert image.format == "PNG"
                assert image.info["fact_layout"] == metadata["fact_layout"]
                draw = ImageDraw.Draw(image)
                for item in layout:
                    card_left, card_top, card_right, card_bottom = item["card_bbox"]
                    assert card_left == 92 and card_right == 988
                    assert 0 <= card_top < card_bottom <= 988
                    assert card_top >= previous_bottom
                    previous_bottom = card_bottom
                    assert 34 <= item["font_size"] <= 52
                    assert 1 <= len(item["lines"]) <= 2
                    assert " ".join(line["text"] for line in item["lines"]) == item["display_text"]
                    font = cyprus_image_recovery._cover_font(item["font_size"], bold=True)
                    for line in item["lines"]:
                        bbox = line["bbox"]
                        assert bbox[0] >= 128
                        assert bbox[2] <= 952
                        assert bbox[1] >= card_top
                        assert bbox[3] <= card_bottom
                        measured = list(draw.textbbox(tuple(line["origin"]), line["text"], font=font))
                        assert measured == bbox

        assert len(json.loads(rendered["three"]["metadata"]["fact_layout"])) == 3
        assert any(
            item["font_size"] < 52 or len(item["lines"]) == 2
            for result in rendered.values()
            for item in json.loads(result["metadata"]["fact_layout"])
        )

        repeat = render_local_informative_cover(
            PROBLEM_EVENING_MESSAGE,
            target_date="2026-07-25",
            post_type="evening",
            output_path=tmp / "three-repeat.png",
            minimum_bytes=12000,
        )
        assert _sha256(Path(rendered["three"]["path"])) == _sha256(Path(repeat["path"]))
        assert rendered["three"]["metadata"]["rain_graphics"] == "false"
        assert rendered["three"]["metadata"]["storm_graphics"] == "false"

        probe_image = Image.new("RGBA", (1080, 1080), (255, 255, 255, 255))
        probe_draw = ImageDraw.Draw(probe_image, "RGBA")
        wrap_probe = cyprus_image_recovery._draw_cover_fact_cards(
            probe_draw,
            ["💨 ПОРЫВЫ ДО 17.5 М/С НА ОСТРОВЕ — ПРОВЕРИТЬ УТРОМ"] * 3,
            accent=(25, 76, 107),
        )
        assert len(wrap_probe) == 3
        assert all(len(item["lines"]) == 2 for item in wrap_probe)
        for previous, current in zip(wrap_probe, wrap_probe[1:]):
            assert current["card_bbox"][1] >= previous["card_bbox"][3] + 24
        assert wrap_probe[-1]["card_bbox"][3] <= 988


def local_cover_graphics_and_cache_follow_confirmed_facts() -> None:
    dry_storm = """🌅 Кипр завтра (21.07.2026)
⚠️ Официальное предупреждение: штормовой ветер у побережья, без осадков.
🏙 Пафос — 29/23 °C • облачно • порывы до 16 м/с.
"""
    rain = """🌅 Кипр завтра (21.07.2026)
🏙 Пафос — 29/23 °C • дождь • порывы до 16 м/с.
"""
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        dry = render_local_informative_cover(
            dry_storm,
            target_date="2026-07-21",
            post_type="evening",
            output_path=tmp / "dry.png",
            minimum_bytes=12000,
        )
        wet = render_local_informative_cover(
            rain,
            target_date="2026-07-21",
            post_type="evening",
            output_path=tmp / "wet.png",
            minimum_bytes=12000,
        )
        changed = render_local_informative_cover(
            PROBLEM_EVENING_MESSAGE.replace("38/24", "37/24"),
            target_date="2026-07-20",
            post_type="evening",
            output_path=tmp / "changed.png",
            minimum_bytes=12000,
        )
        baseline = render_local_informative_cover(
            PROBLEM_EVENING_MESSAGE,
            target_date="2026-07-20",
            post_type="evening",
            output_path=tmp / "baseline.png",
            minimum_bytes=12000,
        )
        assert dry["metadata"]["storm_graphics"] == "true"
        assert dry["metadata"]["rain_graphics"] == "false"
        assert "🌧" not in dry["metadata"]["rendered_text"]
        assert wet["metadata"]["actual_precipitation"] == "true"
        assert wet["metadata"]["rain_graphics"] == "true"
        assert "🌧 ДОЖДЬ МЕСТАМИ" in wet["metadata"]["rendered_text"]
        assert changed["metadata"]["cache_key"] != baseline["metadata"]["cache_key"]


def provider_health_is_date_and_namespace_scoped() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        old_root = os.environ.get("CY_IMAGE_PROVIDER_HEALTH_DIR")
        os.environ["CY_IMAGE_PROVIDER_HEALTH_DIR"] = tmp_name
        try:
            health = load_provider_health("2099-07-15", "morning", "test")
            record_provider_attempts(
                health,
                [
                    {
                        "backend": "stable_horde",
                        "result": "failed",
                        "error_category": "invalid_image",
                    }
                ],
                run_id="fixture-run",
            )
            mark_provider_duplicate(
                health,
                "pollinations",
                dhash="0000393f47c6b62e",
                phash="d2a4af9a406f70bc",
                stuck=True,
                run_id="fixture-run",
            )
            test_path = write_provider_health(health)
            assert test_path == provider_health_path("2099-07-15", "morning", "test")
            assert provider_health_exclusions(load_provider_health("2099-07-15", "morning", "test")) == {
                "pollinations"
            }
            stored = load_provider_health("2099-07-15", "morning", "test")
            assert stored["providers"]["stable_horde"]["invalid_response_count"] == 1
            assert stored["providers"]["stable_horde"]["consecutive_failures"] == 1
            assert not provider_health_path("2099-07-15", "morning", "prod").exists()
            next_day = load_provider_health("2099-07-16", "morning", "test")
            assert provider_health_exclusions(next_day) == set()
        finally:
            if old_root is None:
                os.environ.pop("CY_IMAGE_PROVIDER_HEALTH_DIR", None)
            else:
                os.environ["CY_IMAGE_PROVIDER_HEALTH_DIR"] = old_root


def primary_evening_incident_sends_local_visual_before_text() -> None:
    async def run_case(tmp: Path) -> None:
        old_env = {name: os.environ.get(name) for name in (
            "CHANNEL_ID",
            "CY_SAFE_IMAGE_DIR",
            "CY_IMG_MIN_BYTES",
            "CY_IMAGE_DELIVERY_DIR",
            "CY_TEXT_DELIVERY_DIR",
            "CY_IMAGE_DIAGNOSTICS_DIR",
            "CY_IMAGE_PROVIDER_HEALTH_DIR",
            "GITHUB_RUN_ID",
            "GITHUB_RUN_ATTEMPT",
        )}
        old_token = safe_module.TOKEN
        old_bot = safe_module.Bot
        old_outcome = imagegen.generate_astro_image_outcome_with_exclusions
        old_availability = imagegen.configured_image_backends
        old_prod_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH
        old_test_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH

        history_path = tmp / "cyprus_visual_history_prod.json"
        test_history_path = tmp / "cyprus_visual_history_test.json"
        seed_path = tmp / "seed.ppm"
        _write_dhash_fixture(seed_path, flipped_rows=0)
        history_path.write_text(
            json.dumps(
                [
                    {
                        "date": "2026-07-15",
                        "post_type": "evening",
                        "sha256": cyprus_visual_dedup.sha256_file(seed_path),
                        "perceptual_hash": cyprus_visual_dedup.dhash_file(seed_path),
                        "phash": cyprus_visual_dedup.phash_file(seed_path),
                        "selected_scene": "open_beach_horizon",
                        "composition": "open horizon",
                        "visual_archetype": "beach_eye_level",
                        "prompt_version": "fixture",
                        "cache_key": "fixture-seed",
                        "style_name": "fixture",
                    }
                ],
                ensure_ascii=False,
            ),
            "utf-8",
        )
        test_history_path.write_text("[]", "utf-8")

        calls = {"pollinations": 0, "stable_horde": 0}
        photo_calls: list[dict] = []

        def fake_outcome(
            _prompt: str,
            requested_path: str,
            *,
            excluded_backends=None,
            backend_call_limits=None,
            **_kwargs,
        ):
            limits = dict(backend_call_limits or {})
            path = Path(requested_path)
            if calls == {"pollinations": 0, "stable_horde": 0}:
                assert limits["pollinations"] == 2
                assert limits["stable_horde"] == 3
                calls["pollinations"] += 2
                calls["stable_horde"] += 1
                _write_dhash_fixture(path, flipped_rows=3)
                attempts = [
                    {
                        "backend": "pollinations",
                        "result": "failed",
                        "http_status": 500,
                        "content_type": "application/json",
                        "payload_byte_count": 58,
                        "error_category": "server_error",
                    },
                    {
                        "backend": "stable_horde",
                        "result": "failed",
                        "http_status": 200,
                        "submission_result": "accepted",
                        "request_id": "fixture-horde-invalid-1",
                        "configured_key_rejected": True,
                        "anonymous_retry_used": True,
                        "initial_http_status": 401,
                        "horde_img_payload_kind": "base64",
                        "horde_img_source_length": 124,
                        "horde_img_downloaded_byte_count": 92,
                        "horde_img_validation_result": "rejected_image_validation",
                        "image_validation_failure": "Pillow/signature/content validation failed",
                        "error_category": "invalid_image",
                    },
                    {
                        "backend": "pollinations",
                        "result": "success",
                        "http_status": 200,
                        "content_type": "image/jpeg",
                        "payload_byte_count": path.stat().st_size,
                    },
                ]
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
                    actual_backend_call_count=3,
                )
            horde_limit = int(limits.get("stable_horde", 0) or 0)
            if horde_limit > 0:
                assert limits.get("pollinations", 0) == 0
                assert horde_limit == 2
                calls["stable_horde"] += 2
                attempts = [
                    {
                        "backend": "stable_horde",
                        "result": "failed",
                        "http_status": 200,
                        "submission_result": "accepted",
                        "request_id": f"fixture-horde-invalid-{index + 2}",
                        "configured_key_rejected": True,
                        "anonymous_retry_used": True,
                        "initial_http_status": 401,
                        "horde_img_payload_kind": "base64",
                        "horde_img_source_length": 124,
                        "horde_img_downloaded_byte_count": 92,
                        "horde_img_validation_result": "rejected_image_validation",
                        "image_validation_failure": "Pillow/signature/content validation failed",
                        "error_category": "invalid_image",
                    }
                    for index in range(2)
                ]
                return types.SimpleNamespace(
                    result=None,
                    backend_attempts=attempts,
                    error_type="InvalidImage",
                    error_message="Stable Horde decoded a 92-byte invalid image payload",
                    exhausted=True,
                    actual_backend_call_count=2,
                )
            return types.SimpleNamespace(
                result=None,
                backend_attempts=[],
                error_type="NoBackendsAvailable",
                error_message="all configured image backends are excluded or exhausted",
                exhausted=True,
                actual_backend_call_count=0,
            )

        class FakeBot:
            def __init__(self, token: str) -> None:
                assert token == "fixture-token"

            async def send_photo(self, **kwargs):
                photo_calls.append(
                    {
                        "chat_id": kwargs["chat_id"],
                        "caption": kwargs["caption"],
                        "photo_bytes": kwargs["photo"].read(),
                    }
                )
                return types.SimpleNamespace(message_id=81602)

        try:
            os.environ.update(
                {
                    "CHANNEL_ID": "777",
                    "CY_SAFE_IMAGE_DIR": str(tmp / "images"),
                    "CY_IMG_MIN_BYTES": "12000",
                    "CY_IMAGE_DELIVERY_DIR": str(tmp / "cy_image_delivery"),
                    "CY_TEXT_DELIVERY_DIR": str(tmp / "cy_text_delivery"),
                    "CY_IMAGE_DIAGNOSTICS_DIR": str(tmp / "cy_image_diagnostics"),
                    "CY_IMAGE_PROVIDER_HEALTH_DIR": str(tmp / "cy_image_provider_health"),
                    "GITHUB_RUN_ID": "29424885298",
                    "GITHUB_RUN_ATTEMPT": "1",
                }
            )
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = history_path
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = test_history_path
            safe_module.TOKEN = "fixture-token"
            safe_module.Bot = FakeBot
            imagegen.generate_astro_image_outcome_with_exclusions = fake_outcome
            imagegen.configured_image_backends = lambda **_kwargs: {
                "configured_backends": ["pollinations", "stable_horde"],
                "available_backends": ["pollinations", "stable_horde"],
                "unconfigured_backends": ["custom"],
            }

            text_path = safe_module._cy_text_receipt_path("2026-07-16", "evening")
            assert not text_path.exists()

            result = await safe_module._build_safe_test_image(
                EVENING_MESSAGE,
                "evening",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=True,
                image_chat_id=777,
                image_only_recovery=False,
            )
            assert result["result"] == "sent"
            assert result["backend"] == "local_informative_cover"
            assert calls == {"pollinations": 2, "stable_horde": 3}
            assert len(photo_calls) == 1
            assert photo_calls[0]["chat_id"] == 777
            assert photo_calls[0]["caption"] == safe_module._cy_image_caption(
                "evening",
                "2026-07-16",
                test_label=False,
            )
            assert photo_calls[0]["photo_bytes"].startswith(b"\x89PNG\r\n\x1a\n")
            assert not text_path.exists()

            receipt_path = safe_module._cy_image_receipt_path("2026-07-16", "evening")
            receipt = json.loads(receipt_path.read_text("utf-8"))
            assert receipt["backend"] == "local_informative_cover"
            assert receipt["selected_scene"] == "local_informative_cover"
            assert receipt["composition"] == "informative_cover"
            assert receipt["visual_archetype"] == "factual_weather_cover"
            assert receipt["telegram_message_id"] == 81602
            assert receipt["sha256"] == _sha256(Path(result["path"]))

            health_path = provider_health_path("2026-07-16", "evening", "prod")
            health = json.loads(health_path.read_text("utf-8"))
            pollinations = health["providers"]["pollinations"]
            assert pollinations["repeated_dhash"]
            assert pollinations["repeated_phash"]
            assert pollinations["duplicate_count"] >= 1
            assert health["providers"]["stable_horde"]["invalid_response_count"] >= 3
            assert not provider_health_path("2026-07-16", "evening", "test").exists()

            diagnostics = json.loads(
                (tmp / "cy_image_diagnostics" / "2026-07-16-evening" / "image_result.json").read_text("utf-8")
            )
            assert diagnostics["image_result"] == "sent"
            assert diagnostics["selected_backend"] == "local_informative_cover"
            assert diagnostics["provider_call_counts"] == {
                "pollinations": 2,
                "stable_horde": 3,
                "custom": 0,
            }
            assert diagnostics["valid_candidate_count"] == 1
            assert diagnostics["duplicate_candidate_count"] == 1
            assert diagnostics["provider_failure_count"] == 4
            assert diagnostics["local_fallback_generated"] is True
            assert diagnostics["configured_backends"] == ["pollinations", "stable_horde"]
            assert diagnostics["unconfigured_backends"] == ["custom"]
            horde_attempts = [
                backend_attempt
                for selected in diagnostics["selected_scene_attempts"]
                for backend_attempt in selected.get("backend_attempts", [])
                if backend_attempt.get("backend") == "stable_horde"
            ]
            assert len(horde_attempts) == 3
            assert horde_attempts[0]["configured_key_rejected"] is True
            assert horde_attempts[0]["anonymous_retry_used"] is True
            assert horde_attempts[0]["initial_http_status"] == 401
            assert all(item["horde_img_downloaded_byte_count"] == 92 for item in horde_attempts)
            local_attempt = diagnostics["selected_scene_attempts"][-1]
            assert local_attempt["primary_fallback_allowed"] is True
            assert local_attempt["recovery_fallback_allowed"] is False
            assert local_attempt["local_metadata"]["renderer_version"] == LOCAL_INFORMATIVE_COVER_VERSION
            assert local_attempt["local_metadata"]["rendered_text"]

            amain_source = inspect.getsource(safe_module.main)
            image_index = amain_source.index("image_result = await _build_safe_test_image(")
            recovery_return_index = amain_source.index("if args.image_only_recovery:", image_index)
            text_send_index = amain_source.index("await _send_telegram_text_chunks(", recovery_return_index)
            assert image_index < recovery_return_index < text_send_index

            calls_after_send = dict(calls)
            second = await safe_module._build_safe_test_image(
                EVENING_MESSAGE,
                "evening",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=True,
                image_chat_id=777,
                image_only_recovery=True,
            )
            assert second["result"] == "skipped_receipt_exists"
            assert calls == calls_after_send
            assert len(photo_calls) == 1

            # Recovery still requires a successful text receipt; primary image-first does not.
            receipt_path.unlink()
            third = await safe_module._build_safe_test_image(
                EVENING_MESSAGE,
                "evening",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=True,
                image_chat_id=777,
                image_only_recovery=True,
            )
            assert third["result"] == "skipped_no_text_receipt"
            assert calls == calls_after_send
            assert len(photo_calls) == 1
        finally:
            safe_module.TOKEN = old_token
            safe_module.Bot = old_bot
            imagegen.generate_astro_image_outcome_with_exclusions = old_outcome
            imagegen.configured_image_backends = old_availability
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = old_prod_history
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = old_test_history
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    with tempfile.TemporaryDirectory() as tmp_name:
        asyncio.run(run_case(Path(tmp_name)))


def image_receipt_recheck_closes_primary_and_recovery_send_race() -> None:
    async def run_case(
        case_dir: Path,
        *,
        receipt_during_generation: bool = False,
        existing_receipt: str = "",
        send_to_test: bool = False,
        image_only_recovery: bool = False,
    ) -> tuple[dict[str, object], list[dict[str, object]], dict[str, object]]:
        history_prod = case_dir / "history-prod.json"
        history_test = case_dir / "history-test.json"
        history_prod.parent.mkdir(parents=True, exist_ok=True)
        history_prod.write_text("[]", encoding="utf-8")
        history_test.write_text("[]", encoding="utf-8")
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = history_prod
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = history_test

        os.environ.update(
            {
                "CHANNEL_ID": "777",
                "CHANNEL_ID_TEST": "778",
                "CY_SAFE_IMAGE_DIR": str(case_dir / "images"),
                "CY_IMG_MIN_BYTES": "12000",
                "CY_IMAGE_DELIVERY_DIR": str(case_dir / "image-receipts"),
                "CY_TEXT_DELIVERY_DIR": str(case_dir / "text-receipts"),
                "CY_IMAGE_DIAGNOSTICS_DIR": str(case_dir / "diagnostics"),
                "CY_IMAGE_PROVIDER_HEALTH_DIR": str(case_dir / "provider-health"),
            }
        )

        if existing_receipt == "valid":
            _write_valid_image_receipt("2026-07-16", "evening")
        elif existing_receipt == "invalid":
            safe_module._cy_write_json_atomic(
                safe_module._cy_image_receipt_path("2026-07-16", "evening"),
                {
                    "target_date": "2026-07-15",
                    "post_type": "evening",
                    "chat_type": "production",
                    "telegram_message_id": 0,
                    "sent_at_utc": "",
                },
            )
        if image_only_recovery:
            _write_valid_text_receipt("2026-07-16", "evening")

        photo_calls: list[dict[str, object]] = []

        def successful_outcome(_prompt: str, requested_path: str, **_kwargs):
            if receipt_during_generation:
                _write_valid_image_receipt("2026-07-16", "evening", message_id=7002)
            path = Path(requested_path)
            _write_dhash_fixture(path, flipped_rows=2)
            backend_attempts = [
                {
                    "backend": "pollinations",
                    "result": "success",
                    "http_status": 200,
                    "content_type": "image/jpeg",
                    "payload_byte_count": path.stat().st_size,
                }
            ]
            generated = types.SimpleNamespace(
                path=str(path),
                backend="pollinations",
                byte_count=path.stat().st_size,
                backend_attempts=backend_attempts,
            )
            return types.SimpleNamespace(
                result=generated,
                backend_attempts=backend_attempts,
                error_type="",
                error_message="",
                exhausted=False,
                actual_backend_call_count=1,
            )

        class FakeBot:
            def __init__(self, token: str) -> None:
                assert token == "fixture-token"

            async def send_photo(self, **kwargs):
                photo_calls.append(
                    {
                        "chat_id": kwargs["chat_id"],
                        "caption": kwargs["caption"],
                        "photo_bytes": kwargs["photo"].read(),
                    }
                )
                return types.SimpleNamespace(message_id=9101)

        safe_module.Bot = FakeBot
        imagegen.generate_astro_image_outcome_with_exclusions = successful_outcome
        imagegen.configured_image_backends = lambda **_kwargs: {
            "configured_backends": ["pollinations"],
            "available_backends": ["pollinations"],
            "unconfigured_backends": ["stable_horde", "custom"],
        }

        result = await safe_module._build_safe_test_image(
            EVENING_MESSAGE,
            "evening",
            generate_image=True,
            send_image_to_test=send_to_test,
            send_image_to_chat=not send_to_test,
            image_chat_id=None if send_to_test else 777,
            image_only_recovery=image_only_recovery,
        )
        diagnostics = json.loads(
            (
                case_dir
                / "diagnostics"
                / "2026-07-16-evening"
                / "image_result.json"
            ).read_text(encoding="utf-8")
        )
        return result, photo_calls, diagnostics

    env_names = (
        "CHANNEL_ID",
        "CHANNEL_ID_TEST",
        "CY_SAFE_IMAGE_DIR",
        "CY_IMG_MIN_BYTES",
        "CY_IMAGE_DELIVERY_DIR",
        "CY_TEXT_DELIVERY_DIR",
        "CY_IMAGE_DIAGNOSTICS_DIR",
        "CY_IMAGE_PROVIDER_HEALTH_DIR",
    )
    old_env = {name: os.environ.get(name) for name in env_names}
    old_token = safe_module.TOKEN
    old_bot = safe_module.Bot
    old_outcome = imagegen.generate_astro_image_outcome_with_exclusions
    old_availability = imagegen.configured_image_backends
    old_prod_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH
    old_test_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH
    safe_module.TOKEN = "fixture-token"
    try:
        with tempfile.TemporaryDirectory() as tmp_name:
            root = Path(tmp_name)

            primary_race, primary_photos, primary_diag = asyncio.run(
                run_case(root / "primary-race", receipt_during_generation=True)
            )
            assert primary_race["result"] == "skipped_receipt_appeared_during_generation"
            assert primary_photos == []
            assert primary_diag["image_result"] == "skipped_receipt_appeared_during_generation"
            assert primary_diag["selected_backend"] == "pollinations"

            ordinary, ordinary_photos, ordinary_diag = asyncio.run(
                run_case(root / "ordinary")
            )
            assert ordinary["result"] == "sent"
            assert len(ordinary_photos) == 1
            assert ordinary_diag["image_result"] == "sent"
            assert safe_module.is_valid_cy_image_receipt("2026-07-16", "evening")

            test_send, test_photos, test_diag = asyncio.run(
                run_case(root / "test-send", existing_receipt="valid", send_to_test=True)
            )
            assert test_send["result"] == "sent"
            assert len(test_photos) == 1 and test_photos[0]["chat_id"] == 778
            assert test_diag["image_result"] == "sent"

            stale, stale_photos, stale_diag = asyncio.run(
                run_case(root / "stale-receipt", existing_receipt="invalid")
            )
            assert stale["result"] == "sent"
            assert len(stale_photos) == 1
            assert stale_diag["image_result"] == "sent"
            assert safe_module.is_valid_cy_image_receipt("2026-07-16", "evening")

            recovery_race, recovery_photos, recovery_diag = asyncio.run(
                run_case(
                    root / "recovery-race",
                    receipt_during_generation=True,
                    image_only_recovery=True,
                )
            )
            assert recovery_race["result"] == "skipped_receipt_appeared_during_generation"
            assert recovery_photos == []
            assert recovery_diag["image_result"] == "skipped_receipt_appeared_during_generation"
            assert safe_module.cy_morning_image_phase_for_result(
                recovery_race["result"]
            ) == "image_skipped"
    finally:
        safe_module.TOKEN = old_token
        safe_module.Bot = old_bot
        imagegen.generate_astro_image_outcome_with_exclusions = old_outcome
        imagegen.configured_image_backends = old_availability
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = old_prod_history
        cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_TEST_PATH = old_test_history
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def informative_cover_failure_falls_through_to_text_without_stale_image() -> None:
    async def run_case(tmp: Path) -> None:
        env_names = (
            "CHANNEL_ID",
            "CY_SAFE_IMAGE_DIR",
            "CY_IMG_MIN_BYTES",
            "CY_IMAGE_DELIVERY_DIR",
            "CY_TEXT_DELIVERY_DIR",
            "CY_IMAGE_DIAGNOSTICS_DIR",
            "CY_IMAGE_PROVIDER_HEALTH_DIR",
        )
        old_env = {name: os.environ.get(name) for name in env_names}
        old_token = safe_module.TOKEN
        old_bot = safe_module.Bot
        old_outcome = imagegen.generate_astro_image_outcome_with_exclusions
        old_availability = imagegen.configured_image_backends
        old_renderer = cyprus_image_recovery.render_local_informative_cover
        old_history = cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH
        photo_calls: list[dict[str, object]] = []
        text_calls: list[dict[str, object]] = []

        def exhausted_outcome(_prompt: str, _path: str, *, backend_call_limits=None, **_kwargs):
            attempts: list[dict[str, object]] = []
            for backend in ("pollinations", "stable_horde"):
                for _ in range(int((backend_call_limits or {}).get(backend, 0) or 0)):
                    attempts.append(
                        {
                            "backend": backend,
                            "result": "failed",
                            "error_category": "server_error",
                            "error_type": "FixtureProviderExhausted",
                        }
                    )
            return types.SimpleNamespace(
                result=None,
                backend_attempts=attempts,
                error_type="FixtureProviderExhausted",
                error_message="all fixture providers exhausted",
                exhausted=True,
                actual_backend_call_count=len(attempts),
            )

        def fail_renderer(*_args, **_kwargs):
            raise RuntimeError("fixture informative cover renderer failed")

        class FakeBot:
            def __init__(self, token: str) -> None:
                self.token = token

            async def send_photo(self, **kwargs):
                photo_calls.append(kwargs)
                return types.SimpleNamespace(message_id=81001)

            async def send_message(self, **kwargs):
                text_calls.append(kwargs)
                return types.SimpleNamespace(message_id=82000 + len(text_calls))

        try:
            os.environ.update(
                {
                    "CHANNEL_ID": "777",
                    "CY_SAFE_IMAGE_DIR": str(tmp / "images"),
                    "CY_IMG_MIN_BYTES": "12000",
                    "CY_IMAGE_DELIVERY_DIR": str(tmp / "cy_image_delivery"),
                    "CY_TEXT_DELIVERY_DIR": str(tmp / "cy_text_delivery"),
                    "CY_IMAGE_DIAGNOSTICS_DIR": str(tmp / "cy_image_diagnostics"),
                    "CY_IMAGE_PROVIDER_HEALTH_DIR": str(tmp / "cy_image_provider_health"),
                }
            )
            history = tmp / "history.json"
            history.write_text("[]", "utf-8")
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = history
            stale_path = tmp / "images" / "local_informative_cover_2026-07-16_evening.png"
            stale_path.parent.mkdir(parents=True, exist_ok=True)
            stale_path.write_bytes(b"stale-image-must-not-send")
            safe_module.TOKEN = "fixture-token"
            safe_module.Bot = FakeBot
            imagegen.generate_astro_image_outcome_with_exclusions = exhausted_outcome
            imagegen.configured_image_backends = lambda **_kwargs: {
                "configured_backends": ["pollinations", "stable_horde"],
                "available_backends": ["pollinations", "stable_horde"],
                "unconfigured_backends": ["custom"],
            }
            cyprus_image_recovery.render_local_informative_cover = fail_renderer

            result = await safe_module._build_safe_test_image(
                EVENING_MESSAGE,
                "evening",
                generate_image=True,
                send_image_to_test=False,
                send_image_to_chat=True,
                image_chat_id=777,
                image_only_recovery=False,
            )
            assert result["result"] == "failed_non_fatal"
            assert photo_calls == []
            assert not safe_module._cy_image_receipt_path("2026-07-16", "evening").exists()
            assert stale_path.read_bytes() == b"stale-image-must-not-send"

            bot = FakeBot("fixture-token")
            message_ids = await safe_module._send_telegram_text_chunks(
                bot,
                chat_id=777,
                chunks=["<b>Кипр завтра</b>\n✅ План завтра: текст опубликован."],
                add_test_label=False,
            )
            assert message_ids == [82001]
            assert len(text_calls) == 1
        finally:
            safe_module.TOKEN = old_token
            safe_module.Bot = old_bot
            imagegen.generate_astro_image_outcome_with_exclusions = old_outcome
            imagegen.configured_image_backends = old_availability
            cyprus_image_recovery.render_local_informative_cover = old_renderer
            cyprus_visual_dedup.CYPRUS_VISUAL_HISTORY_PROD_PATH = old_history
            for name, value in old_env.items():
                if value is None:
                    os.environ.pop(name, None)
                else:
                    os.environ[name] = value

    with tempfile.TemporaryDirectory() as tmp_name:
        asyncio.run(run_case(Path(tmp_name)))


def main() -> None:
    checks = (
        local_informative_cover_is_valid_deterministic_and_factual,
        informative_cover_long_facts_fit_pixel_bounds,
        local_cover_graphics_and_cache_follow_confirmed_facts,
        provider_health_is_date_and_namespace_scoped,
        primary_evening_incident_sends_local_visual_before_text,
        image_receipt_recheck_closes_primary_and_recovery_send_race,
        informative_cover_failure_falls_through_to_text_without_stale_image,
    )
    for check in checks:
        check()
        print(f"PASS: {check.__name__}")
    print(f"OK: {len(checks)} Cyprus local image fallback checks passed")


if __name__ == "__main__":
    main()
