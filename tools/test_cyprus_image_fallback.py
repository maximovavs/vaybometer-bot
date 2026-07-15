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

from PIL import Image  # type: ignore  # noqa: E402

import cyprus_visual_dedup  # noqa: E402
from cyprus_image_recovery import (  # noqa: E402
    load_provider_health,
    mark_provider_duplicate,
    provider_health_exclusions,
    provider_health_path,
    record_provider_attempts,
    render_local_weather_card,
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


def local_weather_card_is_valid_deterministic_and_metadata_rich() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        first = render_local_weather_card(
            MESSAGE,
            target_date="2026-07-15",
            post_type="morning",
            output_path=tmp / "first.png",
            minimum_bytes=12000,
        )
        second = render_local_weather_card(
            MESSAGE,
            target_date="2026-07-15",
            post_type="morning",
            output_path=tmp / "second.png",
            minimum_bytes=12000,
        )
        evening = render_local_weather_card(
            EVENING_MESSAGE,
            target_date="2026-07-16",
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
            "local_visual_variant",
            "local_palette",
            "local_time_of_day",
            "local_weather_scenario",
            "renderer_version",
        }
        assert required_metadata <= set(first["metadata"])
        assert required_metadata <= set(evening["metadata"])
        assert first["metadata"]["renderer_version"] == "cy_local_atmospheric_visual_v2"
        assert first["metadata"]["rendered_text"] == ""
        assert evening["metadata"]["rendered_text"] == ""
        renderer_source = inspect.getsource(render_local_weather_card)
        assert "rounded_rectangle" not in renderer_source
        assert "draw.text" not in renderer_source
        assert "VAYBOMETER" not in renderer_source.upper()
        assert "м/с" not in first["metadata"]["rendered_text"]
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
            assert image.info["backend"] == "local_weather_card"
            assert image.info["target_date"] == "2026-07-15"
            assert image.info["post_type"] == "morning"
            assert image.info["focus"] == "coastal"
            assert image.info["rendered_text"] == ""
            image.verify()
        with Image.open(evening_path) as image:
            assert image.size == (1080, 1080)
            assert image.info["post_type"] == "evening"
            assert image.info["local_time_of_day"] in {
                "blue_hour",
                "twilight",
                "moonlit_evening",
                "late_twilight",
            }
            image.verify()


def provider_health_is_date_and_namespace_scoped() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        old_root = os.environ.get("CY_IMAGE_PROVIDER_HEALTH_DIR")
        os.environ["CY_IMAGE_PROVIDER_HEALTH_DIR"] = tmp_name
        try:
            health = load_provider_health("2026-07-15", "morning", "test")
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
            assert test_path == provider_health_path("2026-07-15", "morning", "test")
            assert provider_health_exclusions(load_provider_health("2026-07-15", "morning", "test")) == {
                "pollinations"
            }
            stored = load_provider_health("2026-07-15", "morning", "test")
            assert stored["providers"]["stable_horde"]["invalid_response_count"] == 1
            assert stored["providers"]["stable_horde"]["consecutive_failures"] == 1
            assert not provider_health_path("2026-07-15", "morning", "prod").exists()
            next_day = load_provider_health("2026-07-16", "morning", "test")
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
            assert result["backend"] == "local_weather_card"
            assert calls == {"pollinations": 2, "stable_horde": 3}
            assert len(photo_calls) == 1
            assert photo_calls[0]["chat_id"] == 777
            assert photo_calls[0]["caption"] == "Визуальный вайб погоды на Кипре завтра 🌊"
            assert photo_calls[0]["photo_bytes"].startswith(b"\x89PNG\r\n\x1a\n")
            assert not text_path.exists()

            receipt_path = safe_module._cy_image_receipt_path("2026-07-16", "evening")
            receipt = json.loads(receipt_path.read_text("utf-8"))
            assert receipt["backend"] == "local_weather_card"
            assert receipt["selected_scene"] == "local_weather_card"
            assert receipt["composition"] == "atmospheric_full_bleed"
            assert receipt["visual_archetype"] == "local_atmospheric_visual"
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
            assert diagnostics["selected_backend"] == "local_weather_card"
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
            assert local_attempt["local_metadata"]["renderer_version"] == "cy_local_atmospheric_visual_v2"
            assert local_attempt["local_metadata"]["local_visual_variant"]

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


def main() -> None:
    checks = (
        local_weather_card_is_valid_deterministic_and_metadata_rich,
        provider_health_is_date_and_namespace_scoped,
        primary_evening_incident_sends_local_visual_before_text,
    )
    for check in checks:
        check()
        print(f"PASS: {check.__name__}")
    print(f"OK: {len(checks)} Cyprus local image fallback checks passed")


if __name__ == "__main__":
    main()
