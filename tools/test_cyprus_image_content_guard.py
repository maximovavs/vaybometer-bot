#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for the Cyprus provider-image content guard."""

from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image, ImageDraw  # type: ignore  # noqa: E402
import image_prompt_cy_scene as scene_prompt  # noqa: E402
import world_en.imagegen as imagegen  # noqa: E402
from world_en import image_content_guard as guard  # noqa: E402


class FakeResponse:
    def __init__(self, content: bytes, content_type: str = "image/png") -> None:
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.status_code = 200


def _image_bytes(builder) -> bytes:
    image = Image.new("RGB", (512, 512), (135, 175, 190))
    builder(image)
    buffer = BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def _screen_like_bytes() -> bytes:
    def build(image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 511, 40), fill=(70, 115, 145))
        x = 8
        for index in range(28):
            width = 3 + index % 5
            draw.rectangle(
                (x, 9 + index % 3, x + width, 17 + index % 4),
                fill=(25, 55, 75),
            )
            x += 16 + index % 4
            if x > 500:
                break
        draw.line((0, 39, 511, 39), fill=(210, 225, 230), width=2)
        draw.ellipse((50, 60, 430, 490), fill=(218, 232, 224))
        for index in range(8):
            draw.ellipse(
                (300 + index * 10, 90 + index * 40, 335 + index * 12, 125 + index * 40),
                fill=(70, 120, 140),
            )
        draw.line((80, 350, 350, 260), fill=(240, 120, 150), width=2)

    return _image_bytes(build)


def _landscape_bytes() -> bytes:
    def build(image: Image.Image) -> None:
        draw = ImageDraw.Draw(image)
        for y in range(280):
            draw.line((0, y, 511, y), fill=(135 + y // 5, 185 + y // 8, 220 + y // 15))
        draw.rectangle((0, 280, 511, 400), fill=(45, 135, 180))
        draw.polygon([(0, 390), (512, 360), (512, 512), (0, 512)], fill=(225, 195, 145))
        for x in range(0, 512, 30):
            draw.line((x, 320, x + 20, 315), fill=(210, 230, 235), width=2)
        draw.ellipse((80, 80, 250, 135), fill=(225, 235, 240))

    return _image_bytes(build)


def _set_env(name: str, value: str | None):
    previous = os.environ.get(name)
    if value is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = value
    return previous


def _restore_env(name: str, previous: str | None) -> None:
    if previous is None:
        os.environ.pop(name, None)
    else:
        os.environ[name] = previous


def guard_is_installed_on_imagegen_validator() -> None:
    assert getattr(imagegen._validate_generated_image, "_cyprus_content_guard_installed", False)


def screen_like_provider_image_is_rejected_and_removed() -> None:
    previous_guard = _set_env("CY_IMAGE_CONTENT_GUARD", "1")
    previous_min = _set_env("IMAGEGEN_MIN_VALID_BYTES", "128")
    try:
        with tempfile.TemporaryDirectory() as tmp_name:
            out_path = Path(tmp_name) / "screen.png"
            result = imagegen._validate_generated_image(
                backend="pollinations",
                out_path=out_path,
                payload=_screen_like_bytes(),
                status_code=200,
                content_type="image/png",
            )
            assert result is None
            assert not out_path.exists()
            imagegen._set_backend_diagnostics("pollinations", {"error_category": "invalid_image"})
            diagnostics = imagegen._take_backend_diagnostics("pollinations")
            assert diagnostics["error_category"] == "semantic_mismatch"
            assert diagnostics["content_guard"]["reason"] == "screen_or_ui_chrome"
            assert diagnostics["content_guard"]["valid"] is False
    finally:
        _restore_env("CY_IMAGE_CONTENT_GUARD", previous_guard)
        _restore_env("IMAGEGEN_MIN_VALID_BYTES", previous_min)


def five_dense_top_rows_are_rejected() -> None:
    original_edge_metrics = guard._edge_metrics
    try:
        guard._edge_metrics = lambda _image: (0.13, 0.04, 3.25, 5)
        with tempfile.TemporaryDirectory() as tmp_name:
            out_path = Path(tmp_name) / "five-dense-rows.png"
            out_path.write_bytes(_landscape_bytes())
            verdict = guard.inspect_provider_image(out_path)
            assert verdict.valid is False
            assert verdict.reason == "screen_or_ui_chrome"
            assert verdict.dense_top_rows == 5
    finally:
        guard._edge_metrics = original_edge_metrics


def ordinary_landscape_provider_image_is_accepted() -> None:
    previous_guard = _set_env("CY_IMAGE_CONTENT_GUARD", "1")
    previous_min = _set_env("IMAGEGEN_MIN_VALID_BYTES", "128")
    try:
        with tempfile.TemporaryDirectory() as tmp_name:
            out_path = Path(tmp_name) / "landscape.png"
            result = imagegen._validate_generated_image(
                backend="pollinations",
                out_path=out_path,
                payload=_landscape_bytes(),
                status_code=200,
                content_type="image/png",
            )
            assert result is not None
            assert out_path.exists()
            imagegen._set_backend_diagnostics("pollinations", {})
            diagnostics = imagegen._take_backend_diagnostics("pollinations")
            assert diagnostics["content_guard"]["valid"] is True
            assert diagnostics["content_guard"]["reason"] == "accepted"
    finally:
        _restore_env("CY_IMAGE_CONTENT_GUARD", previous_guard)
        _restore_env("IMAGEGEN_MIN_VALID_BYTES", previous_min)


def explicit_kill_switch_preserves_technical_validation() -> None:
    previous_guard = _set_env("CY_IMAGE_CONTENT_GUARD", "0")
    previous_min = _set_env("IMAGEGEN_MIN_VALID_BYTES", "128")
    try:
        with tempfile.TemporaryDirectory() as tmp_name:
            out_path = Path(tmp_name) / "screen-disabled.png"
            result = imagegen._validate_generated_image(
                backend="pollinations",
                out_path=out_path,
                payload=_screen_like_bytes(),
                status_code=200,
                content_type="image/png",
            )
            assert result is not None
            assert out_path.exists()
    finally:
        _restore_env("CY_IMAGE_CONTENT_GUARD", previous_guard)
        _restore_env("IMAGEGEN_MIN_VALID_BYTES", previous_min)


def rejected_pollinations_image_falls_back_to_horde() -> None:
    previous_guard = _set_env("CY_IMAGE_CONTENT_GUARD", "1")
    previous_min = _set_env("IMAGEGEN_MIN_VALID_BYTES", "128")
    old_get = imagegen.requests.get
    old_horde = imagegen._fetch_from_horde
    old_attempts = imagegen.MAX_ATTEMPTS
    old_custom_url = imagegen.CUSTOM_IMAGE_BASE_URL
    horde_calls: list[str] = []

    def fake_get(*_args, **_kwargs):
        return FakeResponse(_screen_like_bytes())

    def fake_horde(_prompt: str, out_path: Path, **_kwargs):
        horde_calls.append(str(out_path))
        payload = _landscape_bytes()
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(payload)
        return imagegen.ImageGenerationResult(
            path=str(out_path),
            backend="stable_horde",
            byte_count=len(payload),
            content_type="image/png",
        )

    try:
        imagegen.requests.get = fake_get
        imagegen._fetch_from_horde = fake_horde
        imagegen.MAX_ATTEMPTS = 1
        imagegen.CUSTOM_IMAGE_BASE_URL = ""
        with tempfile.TemporaryDirectory() as tmp_name:
            out_path = Path(tmp_name) / "candidate.png"
            outcome = imagegen.generate_astro_image_outcome(
                "Cyprus coast and forecast sky",
                str(out_path),
                max_backend_calls=2,
            )
            assert outcome.result is not None
            assert outcome.result.backend == "stable_horde"
            assert len(horde_calls) == 1
            assert len(outcome.backend_attempts) == 2
            rejected = outcome.backend_attempts[0]
            assert rejected["backend"] == "pollinations"
            assert rejected["result"] == "failed"
            assert rejected["error_category"] == "semantic_mismatch"
            assert rejected["content_guard"]["reason"] == "screen_or_ui_chrome"
    finally:
        imagegen.requests.get = old_get
        imagegen._fetch_from_horde = old_horde
        imagegen.MAX_ATTEMPTS = old_attempts
        imagegen.CUSTOM_IMAGE_BASE_URL = old_custom_url
        _restore_env("CY_IMAGE_CONTENT_GUARD", previous_guard)
        _restore_env("IMAGEGEN_MIN_VALID_BYTES", previous_min)


def prompt_rejects_map_and_screen_outputs_and_bumps_cache_version() -> None:
    assert scene_prompt.CYPRUS_VISUAL_PROMPT_VERSION == "cyprus_visual_v10"
    ctx = SimpleNamespace(
        actual_precipitation=False,
        explicit_storm=False,
        visibility_condition="clear",
        visual_forecast_period="representative_daytime",
    )
    scene = SimpleNamespace(diagnostics={})
    items = scene_prompt._negative_items(
        "evening",
        {},
        {"selected_scene": "long_sandy_beach"},
        scene,
        ctx,
    )
    negative = " ; ".join(items).lower()
    for phrase in (
        "no map",
        "no satellite imagery",
        "no cartographic view",
        "no aerial map",
        "no screenshot",
        "no browser or app interface",
        "no ui chrome",
        "no screen capture",
    ):
        assert phrase in negative


def incident_fingerprints_are_pinned() -> None:
    assert guard._INCIDENT_DHASH == "0f1f560b0f150b03"
    assert guard._INCIDENT_PHASH == "d0692ba536263f36"
    assert guard._hamming_hex(guard._INCIDENT_DHASH, "0f1f560b0f150b03") == 0
    assert guard._hamming_hex(guard._INCIDENT_PHASH, "d0692ba536263f36") == 0


def main() -> None:
    checks = (
        guard_is_installed_on_imagegen_validator,
        screen_like_provider_image_is_rejected_and_removed,
        five_dense_top_rows_are_rejected,
        ordinary_landscape_provider_image_is_accepted,
        explicit_kill_switch_preserves_technical_validation,
        rejected_pollinations_image_falls_back_to_horde,
        prompt_rejects_map_and_screen_outputs_and_bumps_cache_version,
        incident_fingerprints_are_pinned,
    )
    for check in checks:
        check()
        print(f"PASS: {check.__name__}")
    print(f"OK: {len(checks)} Cyprus image content guard checks passed")


if __name__ == "__main__":
    main()
