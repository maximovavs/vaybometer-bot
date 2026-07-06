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


def _block(text: str, start: str, end: str | None = None) -> str:
    start_idx = text.index(start)
    end_idx = text.index(end, start_idx) if end else len(text)
    return text[start_idx:end_idx]


def _blocks(text: str, start: str) -> list[str]:
    out: list[str] = []
    idx = 0
    while True:
        try:
            start_idx = text.index(start, idx)
        except ValueError:
            return out
        try:
            end_idx = text.index("\n      - name:", start_idx + 1)
        except ValueError:
            end_idx = len(text)
        out.append(text[start_idx:end_idx])
        idx = end_idx


def test_daily_visual_history_cache() -> None:
    text = _read(DAILY)
    _assert("daily_cache_action", "uses: actions/cache@v4" in text)
    _assert("daily_prod_path", "path: .cache/cyprus_visual_history_prod.json" in text)
    _assert("daily_test_path", "path: .cache/cyprus_visual_history_test.json" in text)
    _assert(
        "daily_prod_unique_key",
        "key: cyprus-visual-history-prod-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}" in text,
    )
    _assert(
        "daily_test_unique_key",
        "key: cyprus-visual-history-test-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}" in text,
    )
    _assert("daily_restore_prefix", "cyprus-visual-history-prod-" in text)
    _assert("daily_test_restore_prefix", "cyprus-visual-history-test-" in text)
    _assert("daily_no_image_cache_key", "path: .cache/cy_safe_images" not in text)
    _assert("daily_no_empty_history_seed", "printf '[]\\n' > \"$CYPRUS_VISUAL_HISTORY_PATH\"" not in text)
    _assert(
        "daily_prod_env",
        'CYPRUS_VISUAL_HISTORY_PATH: ".cache/cyprus_visual_history_prod.json"' in text,
    )
    _assert(
        "daily_prod_env_explicit",
        'CYPRUS_VISUAL_HISTORY_PROD_PATH: ".cache/cyprus_visual_history_prod.json"' in text,
    )
    _assert(
        "daily_test_env_explicit",
        'CYPRUS_VISUAL_HISTORY_TEST_PATH: ".cache/cyprus_visual_history_test.json"' in text,
    )
    _assert("daily_exact_hit_log", "Cyprus visual history cache restored:" not in text)
    _assert("daily_prod_exact_hit_log", "Cyprus visual history prod exact-key hit:" in text)
    _assert("daily_test_exact_hit_log", "Cyprus visual history test exact-key hit:" in text)
    generic_cache_blocks = _blocks(text, "Restore .cache (FX + intermarket deltas)")
    _assert("daily_generic_cache_blocks", len(generic_cache_blocks) >= 3, str(len(generic_cache_blocks)))
    for idx, cache_block in enumerate(generic_cache_blocks, start=1):
        _assert(f"daily_generic_cache_{idx}_multiline", "path: |" in cache_block)
        _assert(f"daily_generic_cache_{idx}_cache_dir", "\n            .cache" in cache_block)
        _assert(
            f"daily_generic_cache_{idx}_excludes_prod_history",
            "!.cache/cyprus_visual_history_prod.json" in cache_block,
        )
        _assert(
            f"daily_generic_cache_{idx}_excludes_test_history",
            "!.cache/cyprus_visual_history_test.json" in cache_block,
        )
        _assert(
            f"daily_generic_cache_{idx}_excludes_delivery_receipts",
            "!.cache/cy_morning_delivery" in cache_block,
        )

    morning_restore = text.index("Restore Cyprus visual history (prod)")
    morning_test_restore = text.index("Restore Cyprus visual history (test)")
    morning_delivery_restore = text.index("Restore Cyprus morning delivery receipts")
    morning_post = text.index("Post morning (for today)")
    _assert("daily_morning_restore_before_generation", morning_restore < morning_test_restore < morning_post)
    _assert("daily_morning_delivery_restore_before_post", morning_delivery_restore < morning_post)
    _assert(
        "daily_morning_delivery_cache_key",
        "key: cyprus-morning-delivery-${{ github.run_id }}-${{ github.run_attempt }}-${{ github.job }}" in text,
    )
    _assert("daily_morning_delivery_cache_prefix", "cyprus-morning-delivery-" in text)
    _assert("daily_morning_delivery_path", "path: .cache/cy_morning_delivery" in text)
    _assert("daily_morning_delivery_inspect", "valid production receipt exists" in text)
    _assert("daily_morning_delivery_skip_primary_and_recovery", "CY_MORNING_DELIVERY_SKIP" in text)
    _assert(
        "daily_no_github_success_skip",
        "receipt still required for delivery skip" in text,
    )

    evening_job = text.index("evening:")
    evening_restore = text.index("Restore Cyprus visual history (prod)", evening_job)
    evening_test_restore = text.index("Restore Cyprus visual history (test)", evening_job)
    evening_post = text.index("Post evening (announce tomorrow)", evening_job)
    _assert("daily_evening_restore_before_generation", evening_restore < evening_test_restore < evening_post)

    _assert("daily_schedule_morning_unchanged", "cron: '0 1 * * *'" in text)
    _assert("daily_schedule_morning_recovery", "cron: '15 3 * * *'" in text)
    _assert("daily_recovery_guard", "github.event.schedule == '15 3 * * *'" in text)
    _assert("daily_delivery_skip_log", "CY_MORNING_DELIVERY_SKIP" in text)
    _assert("daily_morning_failure_artifact", "Upload Cyprus morning diagnostics" in text)
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
    daily_prod = _block(daily, "Restore Cyprus visual history (prod)", "Restore Cyprus visual history (test)")
    daily_test = _block(daily, "Restore Cyprus visual history (test)", "Inspect Cyprus visual history")
    _assert("prod_cache_prefix", "cyprus-visual-history-prod-" in daily)
    _assert("test_cache_prefix", "cyprus-visual-history-test-" in safe)
    _assert("daily_test_prefix", "cyprus-visual-history-test-" in daily)
    _assert("daily_test_prefix_matches_safe", "cyprus-visual-history-test-" in daily and "cyprus-visual-history-test-" in safe)
    _assert("daily_prod_action_path", "path: .cache/cyprus_visual_history_prod.json" in daily_prod)
    _assert("daily_prod_action_not_test_path", "path: .cache/cyprus_visual_history_test.json" not in daily_prod)
    _assert("daily_test_action_path", "path: .cache/cyprus_visual_history_test.json" in daily_test)
    _assert("daily_test_action_not_prod_path", "path: .cache/cyprus_visual_history_prod.json" not in daily_test)
    _assert("test_path_not_prod_cache", "path: .cache/cyprus_visual_history_prod.json" not in safe)
    print("PASS prod_and_test_history_are_separated")


def test_evening_waits_for_morning_without_losing_dispatch_paths() -> None:
    text = _read(DAILY)
    evening = _block(text, "  evening:", "  noon_fx:")
    _assert("evening_needs_morning", "needs: morning" in evening)
    _assert("evening_always_condition", "always() &&" in evening)
    _assert("evening_schedule_still_allowed", "github.event.schedule == '0 13 * * *'" in evening)
    _assert("evening_manual_still_allowed", "github.event.inputs.run_evening == 'true'" in evening)
    _assert("evening_no_morning_gate", "github.event.inputs.run_morning" not in evening)
    print("PASS evening_waits_for_morning_without_losing_dispatch_paths")


def test_simulated_manual_morning_evening_history_chain() -> None:
    cache_store: dict[str, list[str]] = {}
    prefix = "cyprus-visual-history-prod-"
    run_id = "12345"
    run_attempt = "1"

    def restore(primary_key: str) -> list[str]:
        if primary_key in cache_store:
            return list(cache_store[primary_key])
        candidates = [(key, value) for key, value in cache_store.items() if key.startswith(prefix)]
        if not candidates:
            return []
        key, value = candidates[-1]
        _assert("sim_restore_prefix_key", key.startswith(prefix), key)
        return list(value)

    def save(primary_key: str, value: list[str]) -> None:
        cache_store[primary_key] = list(value)

    morning_key = f"{prefix}{run_id}-{run_attempt}-morning"
    evening_key = f"{prefix}{run_id}-{run_attempt}-evening"
    morning_history = restore(morning_key)
    morning_history.append("A")
    save(morning_key, morning_history)

    evening_history = restore(evening_key)
    _assert("sim_evening_restores_morning_entry", evening_history == ["A"], evening_history)
    evening_history.append("B")
    save(evening_key, evening_history)

    final_history = restore(evening_key)
    _assert("sim_final_history_has_both_entries", final_history == ["A", "B"], final_history)
    print("PASS simulated_manual_morning_evening_history_chain")


def test_pillow_is_bounded_dependency() -> None:
    requirements = (ROOT / "requirements.txt").read_text("utf-8")
    _assert("pillow_bound", "Pillow>=10,<12" in requirements)
    print("PASS pillow_is_bounded_dependency")


TESTS = [
    test_daily_visual_history_cache,
    test_safe_test_visual_history_cache,
    test_prod_and_test_history_are_separated,
    test_evening_waits_for_morning_without_losing_dispatch_paths,
    test_simulated_manual_morning_evening_history_chain,
    test_pillow_is_bounded_dependency,
]


def main() -> None:
    for test in TESTS:
        test()
    print(f"OK: {len(TESTS)} Cyprus visual workflow checks passed")


if __name__ == "__main__":
    main()
