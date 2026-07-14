#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Offline checks for Cyprus visual duplicate detection."""

from __future__ import annotations

from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cyprus_visual_dedup import (
    CYPRUS_VISUAL_DHASH_THRESHOLD,
    cyprus_visual_history_path,
    dhash_file,
    ensure_pillow_for_visual_dedup,
    evaluate_cyprus_visual_candidate,
    hamming_distance_hex,
    load_cyprus_visual_history,
    load_cyprus_visual_reference_history,
    phash_file,
    pillow_available,
    record_cyprus_visual_publication,
    save_cyprus_visual_history,
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
        assert loaded[0]["phash"] == entry["phash"]
        assert loaded[0]["selected_scene"] == "small_harbour"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cy_dedup_history_namespaces_are_separate() -> None:
    assert cyprus_visual_history_path("prod").name == "cyprus_visual_history_prod.json"
    assert cyprus_visual_history_path("test").name == "cyprus_visual_history_test.json"


def cy_dedup_record_is_atomic_and_dedupes_same_publication() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        image = root / "image.ppm"
        _write_ppm(image, mode="coast_a")
        for _ in range(2):
            record_cyprus_visual_publication(
                date_value="2026-07-05",
                post_type="morning",
                image_path=image,
                selected_scene="small_harbour",
                prompt_version="cyprus_visual_v_test",
            cache_key="cache=repeat",
            style_name="style_repeat",
            composition="wide panorama composition",
            history_path=history,
        )
        loaded = load_cyprus_visual_history(history)
        assert len(loaded) == 1

        record_cyprus_visual_publication(
            date_value="2026-07-05",
            post_type="evening",
            image_path=image,
            selected_scene="small_harbour",
            prompt_version="cyprus_visual_v_test",
            cache_key="cache=evening",
            style_name="style_evening",
            composition="eye-level coast view",
            history_path=history,
        )
        loaded = load_cyprus_visual_history(history)
        assert len(loaded) == 2
        assert {entry["post_type"] for entry in loaded} == {"morning", "evening"}
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cy_dedup_malformed_history_keeps_backup() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        history.write_text("{not valid json", "utf-8")
        loaded = load_cyprus_visual_history(history)
        assert loaded == []
        backups = list(root.glob("history.json.malformed.*.bak"))
        assert backups
        assert backups[0].read_text("utf-8") == "{not valid json"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cy_dedup_fresh_run_restore_simulation() -> None:
    root = _tmpdir()
    try:
        run1 = root / "run1"
        run2 = root / "run2"
        run3 = root / "run3"
        run4 = root / "run4"
        for folder in (run1, run2, run3, run4):
            folder.mkdir()

        image_a = run1 / "a.ppm"
        _write_ppm(image_a, mode="coast_a")
        history1 = run1 / "cyprus_visual_history_prod.json"
        record_cyprus_visual_publication(
            date_value="2026-07-01",
            post_type="morning",
            image_path=image_a,
            selected_scene="rocky_cove_overlook",
            prompt_version="cyprus_visual_v_test",
            cache_key="cache=a",
            style_name="style_a",
            history_path=history1,
        )

        history2 = run2 / history1.name
        shutil.copy2(history1, history2)
        image_a2 = run2 / "a.ppm"
        shutil.copy2(image_a, image_a2)
        exact = _evaluate(image_a2, history2, date_value="2026-07-02")
        assert exact.accepted is False
        assert exact.reason == "exact_duplicate"

        history3 = run3 / history1.name
        shutil.copy2(history1, history3)
        near_image = run3 / "near.ppm"
        _write_ppm(near_image, mode="coast_a_cropped", tint=8)
        near = _evaluate(near_image, history3, date_value="2026-07-03")
        assert near.accepted is False
        assert near.reason == "near_duplicate"

        history4 = run4 / history1.name
        shutil.copy2(history1, history4)
        image_b = run4 / "b.ppm"
        _write_ppm(image_b, mode="coast_b")
        different = evaluate_cyprus_visual_candidate(
            image_b,
            date_value="2026-07-04",
            post_type="morning",
            selected_scene="long_sandy_beach",
            prompt_version="cyprus_visual_v_test",
            composition="open horizon composition",
            history_path=history4,
        )
        assert different.accepted is True
        record_cyprus_visual_publication(
            date_value="2026-07-04",
            post_type="morning",
            image_path=image_b,
            selected_scene="long_sandy_beach",
            prompt_version="cyprus_visual_v_test",
            cache_key="cache=b",
            style_name="style_b",
            history_path=history4,
        )
        assert len(load_cyprus_visual_history(history4)) == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cy_dedup_png_jpg_hashes_when_pillow_available() -> None:
    if not pillow_available():
        requirements = (ROOT / "requirements.txt").read_text("utf-8")
        assert "Pillow>=10,<12" in requirements
        assert ensure_pillow_for_visual_dedup() is False
        return

    from PIL import Image

    root = _tmpdir()
    try:
        png = root / "sample.png"
        jpg = root / "sample.jpg"
        image = Image.new("RGB", (32, 32), color=(80, 140, 200))
        image.save(png)
        image.save(jpg)
        for path in (png, jpg):
            digest = dhash_file(path)
            assert digest is not None
            assert len(digest) == 16
            int(digest, 16)
            perceptual = phash_file(path)
            assert perceptual is not None
            assert len(perceptual) == 16
            int(perceptual, 16)
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cy_dedup_recent_scene_family_is_rejected_after_hash_checks() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        old_image = root / "old.ppm"
        new_image = root / "new.ppm"
        _write_ppm(old_image, mode="coast_a")
        _write_ppm(new_image, mode="coast_b")
        record_cyprus_visual_publication(
            date_value="2026-07-05",
            post_type="morning",
            image_path=old_image,
            selected_scene="quiet_blue_lagoon",
            prompt_version="cyprus_visual_v_test",
            cache_key="cache=old",
            style_name="style_old",
            composition="beach curve composition",
            history_path=history,
        )
        result = evaluate_cyprus_visual_candidate(
            new_image,
            date_value="2026-07-06",
            post_type="evening",
            selected_scene="quiet_blue_lagoon",
            prompt_version="cyprus_visual_v_test",
            composition="open horizon composition",
            history_path=history,
        )
        assert result.accepted is False
        assert result.reason == "recent_scene_family"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cy_dedup_recent_composition_is_rejected() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        old_image = root / "old.ppm"
        new_image = root / "new.ppm"
        _write_ppm(old_image, mode="coast_a")
        _write_ppm(new_image, mode="coast_b")
        record_cyprus_visual_publication(
            date_value="2026-07-05",
            post_type="morning",
            image_path=old_image,
            selected_scene="small_harbour",
            prompt_version="cyprus_visual_v_test",
            cache_key="cache=old",
            style_name="style_old",
            composition="wide panorama composition",
            history_path=history,
        )
        result = evaluate_cyprus_visual_candidate(
            new_image,
            date_value="2026-07-06",
            post_type="evening",
            selected_scene="open_sea_cliffs",
            prompt_version="cyprus_visual_v_test",
            composition="wide panorama composition",
            history_path=history,
        )
        assert result.accepted is False
        assert result.reason == "recent_composition"
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cy_test_reference_reads_prod_but_writes_test_only() -> None:
    root = _tmpdir()
    try:
        prod = root / "cyprus_visual_history_prod.json"
        test = root / "cyprus_visual_history_test.json"
        production_image = root / "production.ppm"
        distinct_image = root / "distinct.ppm"
        _write_ppm(production_image, mode="coast_a")
        _write_ppm(distinct_image, mode="coast_b")
        record_cyprus_visual_publication(
            date_value="2026-07-08",
            post_type="evening",
            image_path=production_image,
            selected_scene="protected_bay",
            prompt_version="cyprus_visual_v_test",
            cache_key="cache=prod",
            style_name="style_prod",
            composition="wide panorama composition",
            visual_archetype="bay_panorama",
            history_path=prod,
        )
        prod_before = prod.read_bytes()

        duplicate = evaluate_cyprus_visual_candidate(
            production_image,
            date_value="2026-07-15",
            post_type="evening",
            selected_scene="open_beach_horizon",
            prompt_version="cyprus_visual_v_test",
            composition="eye-level open beach horizon composition",
            visual_archetype="beach_eye_level",
            reference_history_paths=(prod, test),
        )
        assert duplicate.accepted is False
        assert duplicate.reason == "exact_duplicate"

        distinct = evaluate_cyprus_visual_candidate(
            distinct_image,
            date_value="2026-07-15",
            post_type="evening",
            selected_scene="marina_walkway",
            prompt_version="cyprus_visual_v_test",
            composition="marina walkway close-up composition",
            visual_archetype="marina_closeup",
            reference_history_paths=(prod, test),
        )
        assert distinct.accepted is True
        record_cyprus_visual_publication(
            date_value="2026-07-15",
            post_type="evening",
            image_path=distinct_image,
            selected_scene="marina_walkway",
            prompt_version="cyprus_visual_v_test",
            cache_key="cache=test",
            style_name="style_test",
            composition="marina walkway close-up composition",
            visual_archetype="marina_closeup",
            history_path=test,
        )

        assert prod.read_bytes() == prod_before
        assert len(load_cyprus_visual_history(prod)) == 1
        test_entries = load_cyprus_visual_history(test)
        assert len(test_entries) == 1
        assert test_entries[0]["visual_archetype"] == "marina_closeup"
        assert len(load_cyprus_visual_reference_history((prod, test))) == 2
    finally:
        shutil.rmtree(root, ignore_errors=True)


def cy_bay_archetype_cooldown_uses_last_ten_references() -> None:
    root = _tmpdir()
    try:
        history = root / "history.json"
        image = root / "candidate.ppm"
        _write_ppm(image, mode="coast_b")
        entries = []
        for index in range(10):
            entry = {
                "date": f"2026-07-{index + 5:02d}",
                "post_type": "morning",
                "sha256": f"{index + 1:064x}",
                "selected_scene": "protected_bay" if index == 0 else f"fixture_scene_{index}",
                "composition": "wide panorama composition" if index == 0 else f"fixture_composition_{index}",
                "prompt_version": "cyprus_visual_v_test",
                "cache_key": f"cache={index}",
                "style_name": f"style_{index}",
            }
            if index > 0:
                entry["visual_archetype"] = f"fixture_{index}"
            entries.append(entry)
        save_cyprus_visual_history(entries, history)
        result = evaluate_cyprus_visual_candidate(
            image,
            date_value="2026-07-15",
            post_type="evening",
            selected_scene="fresh_scene",
            prompt_version="cyprus_visual_v_test",
            composition="fresh composition",
            visual_archetype="bay_panorama",
            reference_history_paths=(history,),
        )
        assert result.accepted is False
        assert result.reason == "recent_bay_panorama"
    finally:
        shutil.rmtree(root, ignore_errors=True)


TESTS = [
    cy_dedup_exact_sha_is_rejected,
    cy_dedup_near_duplicate_recolor_crop_is_rejected,
    cy_dedup_genuinely_different_image_is_accepted,
    cy_dedup_history_is_persisted_and_read,
    cy_dedup_history_namespaces_are_separate,
    cy_dedup_record_is_atomic_and_dedupes_same_publication,
    cy_dedup_malformed_history_keeps_backup,
    cy_dedup_fresh_run_restore_simulation,
    cy_dedup_png_jpg_hashes_when_pillow_available,
    cy_dedup_recent_scene_family_is_rejected_after_hash_checks,
    cy_dedup_recent_composition_is_rejected,
    cy_test_reference_reads_prod_but_writes_test_only,
    cy_bay_archetype_cooldown_uses_last_ten_references,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} Cyprus visual dedup checks passed")


if __name__ == "__main__":
    main()
