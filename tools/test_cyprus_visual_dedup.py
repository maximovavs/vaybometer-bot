#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline checks for Cyprus visual duplicate detection."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cyprus_visual_dedup import (
    CYPRUS_VISUAL_DHASH_THRESHOLD,
    dhash_file,
    evaluate_cyprus_visual_candidate,
    hamming_distance_hex,
    load_cyprus_visual_history,
    record_cyprus_visual_publication,
)


def _write_ppm(path: Path, *, mode: str, tint: int = 0) -> None:
    width = 64
    height = 64
    payload = bytearray()
    for y in range(height):
        for x in range(width):
            if mode == "coast_a":
                value = int(255 * x / (width - 1))
                if 20 <= x <= 44 and 18 <= y <= 48:
                    value = max(0, value - 18)
            elif mode == "coast_a_cropped":
                source_x = min(width - 1, max(0, x + 2))
                value = int(255 * source_x / (width - 1))
                if 18 <= x <= 42 and 16 <= y <= 46:
                    value = max(0, value - 18)
            elif mode == "coast_b":
                value = int(255 * (width - 1 - x) / (width - 1))
            else:
                value = (x * 7 + y * 13) % 256
            r = max(0, min(255, value + tint))
            g = max(0, min(255, value + tint // 2))
            b = max(0, min(255, value - tint // 2))
            payload.extend((r, g, b))
    path.write_bytes(f"P6\n{width} {height}\n255\n".encode("ascii") + bytes(payload))


def _tmpdir() -> Path:
    return Path(tempfile.mkdtemp(prefix="cy_visual_dedup_"))


def _evaluate(path: Path, history: Path, date_value: str = "2026-07-05"):
    return evaluate_cyprus_visual_candidate(
        path,
        date_value=date_value,
        post_type="morning",
        selected_scene="rocky_cove_overlook",
        prompt_version="cyprus_visual_v_test",
        history_path=history,
    )


def cy_dedup_exact_sha_is_rejected() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        image = root / "image.ppm"
        _write_ppm(image, mode="coast_a")
        record_cyprus_visual_publication(
            date_value="2026-07-01",
            post_type="morning",
            image_path=image,
            selected_scene="rocky_cove_overlook",
            prompt_version="cyprus_visual_v_test",
            cache_key="cache=one",
            style_name="style_one",
            history_path=history,
        )
        result = _evaluate(image, history)
        assert result.accepted is False
        assert result.reason == "exact_duplicate"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cy_dedup_near_duplicate_recolor_crop_is_rejected() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        original = root / "original.ppm"
        similar = root / "similar.ppm"
        _write_ppm(original, mode="coast_a")
        _write_ppm(similar, mode="coast_a_cropped", tint=8)
        record_cyprus_visual_publication(
            date_value="2026-07-01",
            post_type="evening",
            image_path=original,
            selected_scene="protected_bay",
            prompt_version="cyprus_visual_v_test",
            cache_key="cache=two",
            style_name="style_two",
            history_path=history,
        )
        distance = hamming_distance_hex(dhash_file(original), dhash_file(similar))
        assert distance is not None and distance <= CYPRUS_VISUAL_DHASH_THRESHOLD
        result = evaluate_cyprus_visual_candidate(
            similar,
            date_value="2026-07-02",
            post_type="morning",
            selected_scene="protected_bay",
            prompt_version="cyprus_visual_v_test",
            history_path=history,
        )
        assert result.accepted is False
        assert result.reason == "near_duplicate"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cy_dedup_genuinely_different_image_is_accepted() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        original = root / "original.ppm"
        different = root / "different.ppm"
        _write_ppm(original, mode="coast_a")
        _write_ppm(different, mode="coast_b")
        record_cyprus_visual_publication(
            date_value="2026-07-01",
            post_type="morning",
            image_path=original,
            selected_scene="long_sandy_beach",
            prompt_version="cyprus_visual_v_test",
            cache_key="cache=three",
            style_name="style_three",
            history_path=history,
        )
        result = _evaluate(different, history)
        assert result.accepted is True
        assert result.reason == "accepted"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cy_dedup_history_is_persisted_and_read() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        image = root / "image.ppm"
        _write_ppm(image, mode="coast_a")
        entry = record_cyprus_visual_publication(
            date_value="2026-07-05",
            post_type="evening",
            image_path=image,
            selected_scene="small_harbour",
            prompt_version="cyprus_visual_v_test",
            cache_key="cache=four",
            style_name="style_four",
            history_path=history,
        )
        loaded = load_cyprus_visual_history(history)
        assert len(loaded) == 1
        assert loaded[0]["sha256"] == entry["sha256"]
        assert loaded[0]["perceptual_hash"] == entry["perceptual_hash"]
        assert loaded[0]["selected_scene"] == "small_harbour"
    finally:
        shutil.rmtree(root, ignore_errors=True)


TESTS = [
    cy_dedup_exact_sha_is_rejected,
    cy_dedup_near_duplicate_recolor_crop_is_rejected,
    cy_dedup_genuinely_different_image_is_accepted,
    cy_dedup_history_is_persisted_and_read,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} Cyprus visual dedup checks passed")


if __name__ == "__main__":
    main()
