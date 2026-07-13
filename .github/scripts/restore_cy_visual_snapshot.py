#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Restore the latest valid Cyprus production visual snapshot artifact."""

from __future__ import annotations

from datetime import date, timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


def _load_history(path: Path) -> list[dict] | None:
    try:
        data = json.loads(path.read_text("utf-8"))
    except Exception:
        return None
    if not isinstance(data, list) or not data:
        return None
    entries = [entry for entry in data if isinstance(entry, dict)]
    return entries or None


def _latest_entry_date(entries: list[dict]) -> date | None:
    dates: list[date] = []
    for entry in entries:
        try:
            dates.append(date.fromisoformat(str(entry.get("date") or "")[:10]))
        except ValueError:
            pass
    return max(dates) if dates else None


def _current_history_ok(path: Path) -> bool:
    entries = _load_history(path)
    if not entries:
        print("Cyprus visual history prod missing, empty, or malformed; trying snapshot artifact.", file=sys.stderr)
        return False
    latest = _latest_entry_date(entries)
    if latest and latest < date.today() - timedelta(days=21):
        print("Cyprus visual history prod looks stale; trying snapshot artifact.", file=sys.stderr)
        return False
    print(f"Cyprus visual history snapshot restore not needed; entries={len(entries)} latest={latest}.")
    return True


def _gh_json(args: list[str]) -> dict:
    raw = subprocess.check_output(["gh", "api", *args], text=True)
    return json.loads(raw)


def _download_artifact(repo: str, artifact_id: int, target: Path) -> None:
    with target.open("wb") as fh:
        subprocess.check_call(
            ["gh", "api", f"repos/{repo}/actions/artifacts/{artifact_id}/zip"],
            stdout=fh,
        )


def _copy_if_exists(source_root: Path, relative: str, destination_root: Path) -> None:
    source = source_root / relative
    if not source.exists():
        source = source_root / ".cache" / relative
    if not source.exists():
        return
    destination = destination_root / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.is_dir():
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(source, destination)
    else:
        shutil.copy2(source, destination)


def main() -> int:
    history_path = Path(os.getenv("CYPRUS_VISUAL_HISTORY_PROD_PATH", ".cache/cyprus_visual_history_prod.json"))
    if _current_history_ok(history_path):
        return 0

    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY", "")
    if not token or not repo:
        print("No GitHub token/repository available for Cyprus visual snapshot restore.", file=sys.stderr)
        return 0

    artifacts = _gh_json([f"repos/{repo}/actions/artifacts?per_page=100"]).get("artifacts", [])
    candidates = [
        item for item in artifacts
        if isinstance(item, dict)
        and not item.get("expired")
        and str(item.get("name") or "").startswith("cyprus-visual-history-prod-snapshot")
    ]
    candidates.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)

    workspace = Path.cwd()
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
                snapshot_history = extract_dir / "cyprus_visual_history_prod.json"
                if not snapshot_history.exists():
                    snapshot_history = extract_dir / ".cache" / "cyprus_visual_history_prod.json"
                entries = _load_history(snapshot_history)
                if not entries:
                    print(f"Skipping invalid Cyprus visual snapshot artifact id={artifact_id}.", file=sys.stderr)
                    continue
                latest = _latest_entry_date(entries)
                _copy_if_exists(extract_dir, "cyprus_visual_history_prod.json", history_path.parent)
                _copy_if_exists(extract_dir, "cyprus_visual_history_test.json", history_path.parent)
                _copy_if_exists(extract_dir, "cy_image_delivery", workspace / ".cache")
                _copy_if_exists(extract_dir, "cy_text_delivery", workspace / ".cache")
                print(
                    f"Restored Cyprus visual snapshot artifact id={artifact_id}; "
                    f"entries={len(entries)} latest={latest}."
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
