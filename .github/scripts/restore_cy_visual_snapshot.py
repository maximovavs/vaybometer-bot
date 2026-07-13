#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Merge Cyprus visual history and delivery receipts from the newest valid snapshot."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from typing import Any
import zipfile


SNAPSHOT_PREFIX = "cyprus-visual-history-prod-snapshot"
HISTORY_NAME = "cyprus_visual_history_prod.json"
TEST_HISTORY_NAME = "cyprus_visual_history_test.json"


def _parse_date(value: object) -> date | None:
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _parse_time(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text("utf-8"))


def _write_json_atomic(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _history_source_path(source_root: Path, filename: str) -> Path:
    root_path = source_root / filename
    if root_path.exists():
        return root_path
    return source_root / ".cache" / filename


def _valid_history_entry(entry: Any) -> bool:
    return (
        isinstance(entry, dict)
        and _parse_date(entry.get("date")) is not None
        and str(entry.get("post_type") or "").strip() in {"morning", "evening"}
        and bool(str(entry.get("sha256") or "").strip())
    )


def _load_valid_history(path: Path) -> list[dict[str, Any]]:
    try:
        data = _load_json(path)
    except Exception:
        return []
    if not isinstance(data, list):
        return []
    return [entry for entry in data if _valid_history_entry(entry)]


def _latest_entry_date(entries: list[dict[str, Any]]) -> date | None:
    dates = [_parse_date(entry.get("date")) for entry in entries]
    known = [value for value in dates if value is not None]
    return max(known) if known else None


def _history_sort_key(indexed: tuple[int, dict[str, Any]]) -> tuple[str, int, str, str, int]:
    index, entry = indexed
    post_order = {"morning": 0, "evening": 1}.get(str(entry.get("post_type") or ""), 9)
    sent_at = str(entry.get("sent_at_utc") or entry.get("created_at") or "")
    return (str(entry.get("date") or ""), post_order, sent_at, str(entry.get("sha256") or ""), index)


def _merge_history(local_entries: list[dict[str, Any]], snapshot_entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    today = datetime.now(timezone.utc).date()
    cutoff = today - timedelta(days=45)
    merged_by_key: dict[tuple[str, str, str], tuple[int, dict[str, Any]]] = {}

    for source_offset, entry in enumerate(local_entries):
        entry_date = _parse_date(entry.get("date"))
        if entry_date is not None and not (cutoff <= entry_date <= today + timedelta(days=7)):
            continue
        key = (str(entry.get("date") or ""), str(entry.get("post_type") or ""), str(entry.get("sha256") or ""))
        merged_by_key[key] = (source_offset, dict(entry))

    base = len(local_entries) + 1000
    for source_offset, entry in enumerate(snapshot_entries):
        entry_date = _parse_date(entry.get("date"))
        if entry_date is not None and not (cutoff <= entry_date <= today + timedelta(days=7)):
            continue
        key = (str(entry.get("date") or ""), str(entry.get("post_type") or ""), str(entry.get("sha256") or ""))
        if key in merged_by_key:
            continue
        merged_by_key[key] = (base + source_offset, dict(entry))

    ordered = sorted(merged_by_key.values(), key=_history_sort_key)
    return [entry for _index, entry in ordered]


def _positive_int_list(value: Any) -> list[int]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, int) and item > 0]


def _valid_text_receipt(data: Any, *, target_date: str | None = None, post_type: str | None = None) -> bool:
    if not isinstance(data, dict):
        return False
    if target_date and data.get("target_date") != target_date:
        return False
    if post_type and data.get("post_type") != post_type:
        return False
    if data.get("chat_type") != "production":
        return False
    chunk_count = data.get("text_chunk_count")
    if not isinstance(chunk_count, int) or chunk_count < 1:
        return False
    if len(_positive_int_list(data.get("telegram_message_ids"))) < chunk_count:
        return False
    return isinstance(data.get("sent_at_utc"), str) and bool(str(data.get("sent_at_utc")).strip())


def _valid_image_receipt(data: Any, *, target_date: str | None = None, post_type: str | None = None) -> bool:
    if not isinstance(data, dict):
        return False
    if target_date and data.get("target_date") != target_date:
        return False
    if post_type and data.get("post_type") != post_type:
        return False
    if data.get("chat_type") != "production":
        return False
    if not isinstance(data.get("telegram_message_id"), int) or data.get("telegram_message_id") <= 0:
        return False
    if not str(data.get("sha256") or "").strip():
        return False
    if not str(data.get("selected_scene") or "").strip():
        return False
    return isinstance(data.get("sent_at_utc"), str) and bool(str(data.get("sent_at_utc")).strip())


def _receipt_is_newer_or_equal(local_data: dict[str, Any], snapshot_data: dict[str, Any]) -> bool:
    local_time = _parse_time(local_data.get("sent_at_utc"))
    snapshot_time = _parse_time(snapshot_data.get("sent_at_utc"))
    if local_time and snapshot_time:
        return local_time >= snapshot_time
    return True


def _receipt_source_dir(source_root: Path, name: str) -> Path:
    direct = source_root / name
    if direct.exists():
        return direct
    return source_root / ".cache" / name


def _restore_receipts(
    source_root: Path,
    destination_cache: Path,
    *,
    dir_name: str,
    validator,
    target_date: str | None,
    post_type: str | None,
) -> int:
    source_dir = _receipt_source_dir(source_root, dir_name)
    if not source_dir.is_dir():
        return 0

    if target_date and post_type:
        candidates = [source_dir / f"{target_date}-{post_type}.json"]
    else:
        candidates = sorted(source_dir.glob("*.json"))

    restored = 0
    destination_dir = destination_cache / dir_name
    for source in candidates:
        if not source.is_file():
            continue
        try:
            snapshot_data = _load_json(source)
        except Exception:
            continue
        if not validator(snapshot_data, target_date=target_date, post_type=post_type):
            continue
        destination = destination_dir / source.name
        local_data = None
        try:
            local_data = _load_json(destination)
        except Exception:
            local_data = None
        if validator(local_data, target_date=target_date, post_type=post_type) and _receipt_is_newer_or_equal(local_data, snapshot_data):
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        restored += 1
    return restored


def _gh_json(args: list[str]) -> dict[str, Any]:
    raw = subprocess.check_output(["gh", "api", *args], text=True)
    return json.loads(raw)


def _download_artifact(repo: str, artifact_id: int, target: Path) -> None:
    with target.open("wb") as fh:
        subprocess.check_call(
            ["gh", "api", f"repos/{repo}/actions/artifacts/{artifact_id}/zip"],
            stdout=fh,
        )


def _artifact_candidates() -> list[dict[str, Any]]:
    repo = os.getenv("GITHUB_REPOSITORY", "")
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    if not token or not repo:
        print("No GitHub token/repository available for Cyprus visual snapshot restore.", file=sys.stderr)
        return []
    artifacts = _gh_json([f"repos/{repo}/actions/artifacts?per_page=100"]).get("artifacts", [])
    candidates = [
        item for item in artifacts
        if isinstance(item, dict)
        and not item.get("expired")
        and str(item.get("name") or "").startswith(SNAPSHOT_PREFIX)
    ]
    candidates.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
    return candidates


def main() -> int:
    history_path = Path(os.getenv("CYPRUS_VISUAL_HISTORY_PROD_PATH", ".cache/cyprus_visual_history_prod.json"))
    destination_cache = Path.cwd() / ".cache"
    target_date = (os.getenv("CY_RECOVERY_TARGET_DATE") or "").strip() or None
    post_type = (os.getenv("CY_RECOVERY_POST_TYPE") or "").strip() or None
    if post_type and post_type not in {"morning", "evening"}:
        print(f"Unsupported CY_RECOVERY_POST_TYPE={post_type}; ignoring targeted receipt restore.", file=sys.stderr)
        post_type = None

    local_entries = _load_valid_history(history_path)
    local_latest = _latest_entry_date(local_entries)
    if local_entries:
        print(f"Cyprus local visual history count={len(local_entries)} latest={local_latest}.")
    else:
        print("Cyprus visual history prod missing, empty, or malformed; trying snapshot artifact.", file=sys.stderr)

    repo = os.getenv("GITHUB_REPOSITORY", "")
    candidates = _artifact_candidates()
    if not candidates:
        return 0

    with tempfile.TemporaryDirectory(prefix="cy_visual_snapshot_") as tmp_name:
        tmp = Path(tmp_name)
        for artifact in candidates:
            artifact_id = int(artifact["id"])
            zip_path = tmp / f"{artifact_id}.zip"
            extract_dir = tmp / str(artifact_id)
            try:
                _download_artifact(repo, artifact_id, zip_path)
                extract_dir.mkdir()
                with zipfile.ZipFile(zip_path) as archive:
                    archive.extractall(extract_dir)

                snapshot_history_path = _history_source_path(extract_dir, HISTORY_NAME)
                snapshot_entries = _load_valid_history(snapshot_history_path)
                if not snapshot_entries:
                    print(f"Skipping invalid Cyprus visual snapshot artifact id={artifact_id}.", file=sys.stderr)
                    continue

                snapshot_latest = _latest_entry_date(snapshot_entries)
                merged = _merge_history(local_entries, snapshot_entries)
                if merged != local_entries:
                    _write_json_atomic(history_path, merged)
                test_history_path = _history_source_path(extract_dir, TEST_HISTORY_NAME)
                test_entries = _load_valid_history(test_history_path)
                if test_entries:
                    test_destination = destination_cache / TEST_HISTORY_NAME
                    local_test_entries = _load_valid_history(test_destination)
                    merged_test = _merge_history(local_test_entries, test_entries)
                    if merged_test != local_test_entries:
                        _write_json_atomic(test_destination, merged_test)

                text_restored = _restore_receipts(
                    extract_dir,
                    destination_cache,
                    dir_name="cy_text_delivery",
                    validator=_valid_text_receipt,
                    target_date=target_date,
                    post_type=post_type,
                )
                image_restored = _restore_receipts(
                    extract_dir,
                    destination_cache,
                    dir_name="cy_image_delivery",
                    validator=_valid_image_receipt,
                    target_date=target_date,
                    post_type=post_type,
                )
                print(
                    "Cyprus visual snapshot merged: "
                    f"artifact_id={artifact_id}; local_count={len(local_entries)}; "
                    f"snapshot_count={len(snapshot_entries)}; merged_count={len(merged)}; "
                    f"local_latest={local_latest}; snapshot_latest={snapshot_latest}; "
                    f"text_receipts_restored={text_restored}; image_receipts_restored={image_restored}."
                )
                return 0
            except Exception as exc:
                print(
                    f"Skipping Cyprus visual snapshot artifact id={artifact_id}: {exc.__class__.__name__}: {exc}",
                    file=sys.stderr,
                )
    print("No valid Cyprus visual history snapshot artifact found.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
