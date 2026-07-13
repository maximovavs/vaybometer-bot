#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Static workflow checks for persistent Cyprus visual dedup history."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DAILY = ROOT / ".github" / "workflows" / "daily_post.yml"
SAFE_TEST = ROOT / ".github" / "workflows" / "safe_test_post.yml"
SNAPSHOT_HELPER = ROOT / ".github" / "scripts" / "restore_cy_visual_snapshot.py"


def _read(path: Path) -> str:
    return path.read_text("utf-8")


def _assert(name: str, condition: bool, detail: str = "") -> None:
    if not condition:
        raise AssertionError(f"{name}: {detail or 'assertion failed'}")


def _load_snapshot_helper():
    spec = importlib.util.spec_from_file_location("restore_cy_visual_snapshot_test", SNAPSHOT_HELPER)
    if spec is None or spec.loader is None:
        raise AssertionError("cannot load restore_cy_visual_snapshot helper")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _history_entry(day: str, post_type: str, sha: str, **extra) -> dict:
    payload = {
        "date": day,
        "post_type": post_type,
        "sha256": sha,
        "selected_scene": "coastal_promenade",
        "composition": "wide panorama composition",
        "prompt_version": "cyprus_visual_v5",
        "cache_key": f"{day}-{post_type}-{sha}",
        "style_name": "fixture",
    }
    payload.update(extra)
    return payload


def _text_receipt(day: str, post_type: str = "morning", sent_at: str = "2026-07-10T01:00:00Z") -> dict:
    return {
        "target_date": day,
        "post_type": post_type,
        "chat_type": "production",
        "telegram_message_ids": [111],
        "text_chunk_count": 1,
        "sent_at_utc": sent_at,
    }


def _image_receipt(day: str, post_type: str = "morning", sent_at: str = "2026-07-10T01:01:00Z") -> dict:
    return {
        "target_date": day,
        "post_type": post_type,
        "chat_type": "production",
        "telegram_message_id": 222,
        "sha256": "b" * 64,
        "selected_scene": "coastal_promenade",
        "sent_at_utc": sent_at,
    }


def _write_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _make_snapshot_zip(base: Path, artifact_id: int, *, history, text_receipts=(), image_receipts=()) -> Path:
    source = base / f"artifact_{artifact_id}_src"
    cache = source / ".cache"
    _write_json(cache / "cyprus_visual_history_prod.json", history)
    for receipt in text_receipts:
        name = f"{receipt['target_date']}-{receipt['post_type']}.json"
        _write_json(cache / "cy_text_delivery" / name, receipt)
    for receipt in image_receipts:
        name = f"{receipt['target_date']}-{receipt['post_type']}.json"
        _write_json(cache / "cy_image_delivery" / name, receipt)
    zip_path = base / f"artifact_{artifact_id}.zip"
    with zipfile.ZipFile(zip_path, "w") as archive:
        for item in source.rglob("*"):
            if item.is_file():
                archive.write(item, item.relative_to(source).as_posix())
    return zip_path


def _run_snapshot_helper(tmp: Path, artifacts: list[tuple[int, str, Path]], *, target_date: str | None = None, post_type: str | None = None):
    module = _load_snapshot_helper()
    old_env = {name: os.environ.get(name) for name in (
        "GH_TOKEN",
        "GITHUB_REPOSITORY",
        "CYPRUS_VISUAL_HISTORY_PROD_PATH",
        "CY_RECOVERY_TARGET_DATE",
        "CY_RECOVERY_POST_TYPE",
    )}
    old_cwd = Path.cwd()
    old_gh_json = module._gh_json
    old_download = module._download_artifact
    artifact_map = {artifact_id: zip_path for artifact_id, _created, zip_path in artifacts}

    def fake_gh_json(_args):
        return {
            "artifacts": [
                {
                    "id": artifact_id,
                    "name": "cyprus-visual-history-prod-snapshot-fixture",
                    "created_at": created,
                    "expired": False,
                }
                for artifact_id, created, _zip_path in artifacts
            ]
        }

    def fake_download(_repo: str, artifact_id: int, target: Path) -> None:
        shutil.copy2(artifact_map[artifact_id], target)

    try:
        os.chdir(tmp)
        os.environ["GH_TOKEN"] = "fixture-token"
        os.environ["GITHUB_REPOSITORY"] = "maximovavs/vaybometer-bot"
        os.environ["CYPRUS_VISUAL_HISTORY_PROD_PATH"] = str(tmp / ".cache" / "cyprus_visual_history_prod.json")
        if target_date:
            os.environ["CY_RECOVERY_TARGET_DATE"] = target_date
        else:
            os.environ.pop("CY_RECOVERY_TARGET_DATE", None)
        if post_type:
            os.environ["CY_RECOVERY_POST_TYPE"] = post_type
        else:
            os.environ.pop("CY_RECOVERY_POST_TYPE", None)
        module._gh_json = fake_gh_json
        module._download_artifact = fake_download
        result = module.main()
    finally:
        module._gh_json = old_gh_json
        module._download_artifact = old_download
        os.chdir(old_cwd)
        for name, value in old_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
    return result


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
    _assert("daily_generic_cache_excludes_prod_history", "!.cache/cyprus_visual_history_prod.json" in text)
    _assert("daily_generic_cache_excludes_test_history", "!.cache/cyprus_visual_history_test.json" in text)
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
            f"daily_generic_cache_{idx}_excludes_safe_images",
            "!.cache/cy_safe_images" in cache_block,
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
    _assert("daily_schedule_morning_image_recovery", "cron: '45 1 * * *'" in text)
    _assert("daily_schedule_evening_image_recovery", "cron: '45 13 * * *'" in text)
    _assert("daily_schedule_evening_late_image_recovery", "cron: '15 15 * * *'" in text)
    _assert("daily_morning_0315_image_only_branch", "CY_MORNING_IMAGE_ONLY_RECOVERY" in text)
    _assert("daily_morning_uses_unified_text_delivery", "has_valid_cy_text_delivery(target_date, \"morning\")" in text)
    _assert("daily_morning_uses_image_delivery_validation", "is_valid_cy_image_receipt(target_date, \"morning\")" in text)
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


def test_image_recovery_jobs_are_production_only() -> None:
    text = _read(DAILY)
    morning = _block(text, "  morning_image_recovery:", "  evening_image_recovery:")
    evening = _block(text, "  evening_image_recovery:", "  noon_fx:")
    for name, block, cron in (
        ("morning", morning, "45 1 * * *"),
        ("evening", evening, "45 13 * * *"),
    ):
        _assert(f"{name}_recovery_schedule_guard", f"github.event.schedule == '{cron}'" in block)
        _assert(f"{name}_recovery_image_only", "--image-only-recovery" in block)
        _assert(f"{name}_recovery_send_image_to_chat", "--send-image-to-chat" in block)
        _assert(f"{name}_recovery_uses_prod_channel", '--chat-id "$CHANNEL_ID"' in block)
        _assert(f"{name}_recovery_not_test", "--send-image-to-test" not in block)
        _assert(f"{name}_recovery_no_text_send", "--send " not in block and "--send\n" not in block)
        _assert(f"{name}_recovery_restores_snapshot", "restore_cy_visual_snapshot.py" in block)
        _assert(f"{name}_recovery_passes_target_date", "CY_RECOVERY_TARGET_DATE" in block)
        _assert(f"{name}_recovery_passes_post_type", f'CY_RECOVERY_POST_TYPE="{name}"' in block)
    _assert("evening_late_recovery_schedule_guard", "github.event.schedule == '15 15 * * *'" in evening)
    print("PASS image_recovery_jobs_are_production_only")


def test_delivery_receipts_diagnostics_and_snapshots() -> None:
    text = _read(DAILY)
    _assert("permissions_actions_read", "actions: read" in text)
    _assert("image_delivery_artifact_path", ".cache/cy_image_delivery" in text)
    _assert("text_delivery_artifact_path", ".cache/cy_text_delivery" in text)
    _assert("diagnostics_artifact", "cyprus-image-diagnostics-${{ github.job }}" in text)
    _assert("diagnostics_path", "path: .cache/cy_image_diagnostics" in text)
    _assert("history_snapshot_artifact", "cyprus-visual-history-prod-snapshot-${{ github.job }}" in text)
    _assert("snapshot_restore_step", "Restore Cyprus visual history snapshot artifact if needed" in text)
    _assert("snapshot_restore_helper", "python .github/scripts/restore_cy_visual_snapshot.py" in text)
    _assert("snapshot_restore_not_inline_control_file", "cy_history_needs_snapshot" not in text)
    helper = (ROOT / ".github" / "scripts" / "restore_cy_visual_snapshot.py").read_text("utf-8")
    _assert("snapshot_restore_uses_gh_api", "actions/artifacts?per_page=100" in helper)
    _assert("snapshot_restore_merges_history", "_merge_history" in helper and "merged_count" in helper)
    _assert("snapshot_restore_validates_receipts", "_valid_text_receipt" in helper and "_valid_image_receipt" in helper)
    _assert("snapshot_restore_rejects_invalid_newest", "Skipping invalid Cyprus visual snapshot artifact" in helper)
    _assert("snapshot_restore_restores_receipts", "cy_image_delivery" in helper and "cy_text_delivery" in helper)
    _assert("snapshot_restore_used_in_four_jobs", text.count("restore_cy_visual_snapshot.py") >= 4)
    print("PASS delivery_receipts_diagnostics_and_snapshots")


def test_snapshot_restores_receipts_even_when_history_current() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        local_history = [
            _history_entry("2026-07-12", "morning", "a" * 64),
        ]
        _write_json(tmp / ".cache" / "cyprus_visual_history_prod.json", local_history)
        snapshot = _make_snapshot_zip(
            tmp,
            101,
            history=local_history,
            text_receipts=[_text_receipt("2026-07-13", "morning")],
            image_receipts=[_image_receipt("2026-07-13", "morning")],
        )
        result = _run_snapshot_helper(
            tmp,
            [(101, "2026-07-13T05:00:00Z", snapshot)],
            target_date="2026-07-13",
            post_type="morning",
        )
        _assert("snapshot_receipt_restore_result", result == 0)
        _assert("snapshot_text_receipt_restored", (tmp / ".cache" / "cy_text_delivery" / "2026-07-13-morning.json").exists())
        _assert("snapshot_image_receipt_restored", (tmp / ".cache" / "cy_image_delivery" / "2026-07-13-morning.json").exists())
    print("PASS snapshot_restores_receipts_even_when_history_current")


def test_snapshot_merges_recent_history_without_21_day_gap() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        local_entry = _history_entry("2026-07-10", "morning", "1" * 64)
        snapshot_entry = _history_entry("2026-07-12", "evening", "2" * 64)
        _write_json(tmp / ".cache" / "cyprus_visual_history_prod.json", [local_entry])
        snapshot = _make_snapshot_zip(tmp, 102, history=[snapshot_entry])
        _run_snapshot_helper(tmp, [(102, "2026-07-13T05:00:00Z", snapshot)])
        merged = json.loads((tmp / ".cache" / "cyprus_visual_history_prod.json").read_text("utf-8"))
        keys = {(entry["date"], entry["post_type"], entry["sha256"]) for entry in merged}
        _assert("snapshot_preserves_local_entry", ("2026-07-10", "morning", "1" * 64) in keys)
        _assert("snapshot_adds_yesterday_entry", ("2026-07-12", "evening", "2" * 64) in keys)
    print("PASS snapshot_merges_recent_history_without_21_day_gap")


def test_snapshot_skips_malformed_newest_artifact() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        bad = _make_snapshot_zip(tmp, 201, history=[{"date": "2026-07-12", "post_type": "morning"}])
        valid_entry = _history_entry("2026-07-12", "morning", "3" * 64)
        good = _make_snapshot_zip(tmp, 202, history=[valid_entry])
        _run_snapshot_helper(
            tmp,
            [
                (201, "2026-07-13T06:00:00Z", bad),
                (202, "2026-07-13T05:00:00Z", good),
            ],
        )
        merged = json.loads((tmp / ".cache" / "cyprus_visual_history_prod.json").read_text("utf-8"))
        _assert("snapshot_second_newest_used", merged and merged[0]["sha256"] == "3" * 64, merged)
    print("PASS snapshot_skips_malformed_newest_artifact")


def test_snapshot_receipt_validation_and_newer_local_protection() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        _write_json(tmp / ".cache" / "cyprus_visual_history_prod.json", [_history_entry("2026-07-12", "morning", "4" * 64)])
        local_newer = _text_receipt("2026-07-13", "morning", "2026-07-13T06:00:00Z")
        _write_json(tmp / ".cache" / "cy_text_delivery" / "2026-07-13-morning.json", local_newer)
        invalid_image = _image_receipt("2026-07-13", "morning")
        invalid_image.pop("telegram_message_id")
        snapshot = _make_snapshot_zip(
            tmp,
            301,
            history=[_history_entry("2026-07-13", "morning", "5" * 64)],
            text_receipts=[_text_receipt("2026-07-13", "morning", "2026-07-13T05:00:00Z")],
            image_receipts=[invalid_image],
        )
        _run_snapshot_helper(
            tmp,
            [(301, "2026-07-13T07:00:00Z", snapshot)],
            target_date="2026-07-13",
            post_type="morning",
        )
        final_text = json.loads((tmp / ".cache" / "cy_text_delivery" / "2026-07-13-morning.json").read_text("utf-8"))
        _assert("snapshot_keeps_newer_local_text_receipt", final_text["sent_at_utc"] == "2026-07-13T06:00:00Z")
        _assert("snapshot_does_not_restore_invalid_image_receipt", not (tmp / ".cache" / "cy_image_delivery" / "2026-07-13-morning.json").exists())
    print("PASS snapshot_receipt_validation_and_newer_local_protection")


def test_snapshot_targeted_restore_continues_to_older_artifact_for_receipts() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        newest = _make_snapshot_zip(
            tmp,
            401,
            history=[_history_entry("2026-07-13", "morning", "6" * 64)],
        )
        older = _make_snapshot_zip(
            tmp,
            402,
            history=[_history_entry("2026-07-12", "morning", "7" * 64)],
            text_receipts=[_text_receipt("2026-07-13", "morning")],
            image_receipts=[_image_receipt("2026-07-13", "morning")],
        )
        _run_snapshot_helper(
            tmp,
            [
                (401, "2026-07-13T08:00:00Z", newest),
                (402, "2026-07-13T07:00:00Z", older),
            ],
            target_date="2026-07-13",
            post_type="morning",
        )
        _assert("snapshot_older_text_receipt_restored", (tmp / ".cache" / "cy_text_delivery" / "2026-07-13-morning.json").exists())
        _assert("snapshot_older_image_receipt_restored", (tmp / ".cache" / "cy_image_delivery" / "2026-07-13-morning.json").exists())
        merged = json.loads((tmp / ".cache" / "cyprus_visual_history_prod.json").read_text("utf-8"))
        keys = {entry["sha256"] for entry in merged}
        _assert("snapshot_merges_both_checked_artifacts", {"6" * 64, "7" * 64}.issubset(keys), keys)
    print("PASS snapshot_targeted_restore_continues_to_older_artifact_for_receipts")


def test_snapshot_targeted_restore_stops_when_newest_has_receipts() -> None:
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        newest = _make_snapshot_zip(
            tmp,
            501,
            history=[_history_entry("2026-07-13", "morning", "8" * 64)],
            text_receipts=[_text_receipt("2026-07-13", "morning", "2026-07-13T08:00:00Z")],
            image_receipts=[_image_receipt("2026-07-13", "morning", "2026-07-13T08:01:00Z")],
        )
        older = _make_snapshot_zip(
            tmp,
            502,
            history=[_history_entry("2026-07-12", "morning", "9" * 64)],
            text_receipts=[_text_receipt("2026-07-13", "morning", "2026-07-13T07:00:00Z")],
            image_receipts=[_image_receipt("2026-07-13", "morning", "2026-07-13T07:01:00Z")],
        )
        _run_snapshot_helper(
            tmp,
            [
                (501, "2026-07-13T08:00:00Z", newest),
                (502, "2026-07-13T07:00:00Z", older),
            ],
            target_date="2026-07-13",
            post_type="morning",
        )
        merged = json.loads((tmp / ".cache" / "cyprus_visual_history_prod.json").read_text("utf-8"))
        keys = {entry["sha256"] for entry in merged}
        _assert("snapshot_newest_receipts_stop_before_older", "8" * 64 in keys and "9" * 64 not in keys, keys)
        final_text = json.loads((tmp / ".cache" / "cy_text_delivery" / "2026-07-13-morning.json").read_text("utf-8"))
        _assert("snapshot_newest_text_receipt_kept", final_text["sent_at_utc"] == "2026-07-13T08:00:00Z")
    print("PASS snapshot_targeted_restore_stops_when_newest_has_receipts")


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
    test_image_recovery_jobs_are_production_only,
    test_delivery_receipts_diagnostics_and_snapshots,
    test_snapshot_restores_receipts_even_when_history_current,
    test_snapshot_merges_recent_history_without_21_day_gap,
    test_snapshot_skips_malformed_newest_artifact,
    test_snapshot_receipt_validation_and_newer_local_protection,
    test_snapshot_targeted_restore_continues_to_older_artifact_for_receipts,
    test_snapshot_targeted_restore_stops_when_newest_has_receipts,
    test_simulated_manual_morning_evening_history_chain,
    test_pillow_is_bounded_dependency,
]


def main() -> None:
    for test in TESTS:
        test()
    print(f"OK: {len(TESTS)} Cyprus visual workflow checks passed")


if __name__ == "__main__":
    main()
