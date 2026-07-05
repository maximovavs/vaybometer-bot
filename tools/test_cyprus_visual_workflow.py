#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static workflow checks for persistent Cyprus visual dedup history."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / ".github" / "workflows" / "daily_post.yml"
SAFE_TEST = ROOT / ".github" / "workflows" / "safe_test_post.yml"


def _read(path: Path) -> str:
    return path.read_text("utf-8")


def _assert(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail or 'assertion failed'}")


def test_daily_visual_history_cache() -> None:
    text = _read(DAILY)
    _assert("daily_cache_action", "uses: actions/cache@v4" in text)
    _assert("daily_prod_path", "path: .cache/cyprus_visual_history_prod.json" in text)
    _assert(
        "daily_unique_key",
        "key: cyprus-visual-history-prod-${{ github.run_id }}-${{ github.run_attempt }}" in text,
    )
    _assert("daily_restore_prefix", "cyprus-visual-history-prod-" in text)
    _assert("daily_no_image_cache_key", "path: .cache/cy_safe_images" not in text)
    _assert("daily_no_empty_history_seed", "printf '[]\\n' > \"$CYPRUS_VISUAL_HISTORY_PATH\"" not in text)
    _assert(
        "daily_prod_env",
        'CYPRUS_VISUAL_HISTORY_PATH: ".cache/cyprus_visual_history_prod.json"' in text,
    )

    morning_restore = text.index("Restore Cyprus visual history")
    morning_post = text.index("Post morning (for today)")
    _assert("daily_morning_restore_before_generation", morning_restore < morning_post)

    evening_job = text.index("evening:")
    evening_restore = text.index("Restore Cyprus visual history", evening_job)
    evening_post = text.index("Post evening (announce tomorrow)", evening_job)
    _assert("daily_evening_restore_before_generation", evening_restore < evening_post)

    _assert("daily_schedule_morning_unchanged", "cron: '0 1 * * *'" in text)
    _assert("daily_schedule_evening_unchanged", "cron: '0 13 * * *'" in text)
    _assert("daily_schedule_fx_unchanged", "cron: '0 7 * * *'" in text)
    print("PASS daily_visual_history_cache")


def test_safe_test_visual_history_cache() -> None:
    text = _read(SAFE_TEST)
    _assert("safe_cache_action", "uses: actions/cache@v4" in text)
    _assert("safe_test_path", "path: .cache/cyprus_visual_history_test.json" in text)
    _assert(
        "safe_unique_key",
        "key: cyprus-visual-history-test-${{ github.run_id }}-${{ github.run_attempt }}" in text,
    )
    _assert("safe_restore_prefix", "cyprus-visual-history-test-" in text)
    _assert("safe_no_empty_history_seed", "printf '[]\\n' > \"$CYPRUS_VISUAL_HISTORY_PATH\"" not in text)
    _assert(
        "safe_test_env",
        'CYPRUS_VISUAL_HISTORY_PATH: ".cache/cyprus_visual_history_test.json"' in text,
    )
    restore = text.index("Restore Cyprus visual history")
    build = text.index("Build / optionally send safe test post")
    _assert("safe_restore_before_generation", restore < build)
    print("PASS safe_test_visual_history_cache")


def test_prod_and_test_history_are_separated() -> None:
    daily = _read(DAILY)
    safe = _read(SAFE_TEST)
    _assert("prod_cache_prefix", "cyprus-visual-history-prod-" in daily)
    _assert("test_cache_prefix", "cyprus-visual-history-test-" in safe)
    _assert("prod_path_not_test_cache", "path: .cache/cyprus_visual_history_test.json" not in daily)
    _assert("test_path_not_prod_cache", "path: .cache/cyprus_visual_history_prod.json" not in safe)
    print("PASS prod_and_test_history_are_separated")


def test_pillow_is_bounded_dependency() -> None:
    requirements = (ROOT / "requirements.txt").read_text("utf-8")
    _assert("pillow_bound", "Pillow>=10,<12" in requirements)
    print("PASS pillow_is_bounded_dependency")


TESTS = [
    test_daily_visual_history_cache,
    test_safe_test_visual_history_cache,
    test_prod_and_test_history_are_separated,
    test_pillow_is_bounded_dependency,
]


def main() -> None:
    for test in TESTS:
        test()
    print(f"OK: {len(TESTS)} Cyprus visual workflow checks passed")


if __name__ == "__main__":
    main()
