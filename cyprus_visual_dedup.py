#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Cyprus visual history and duplicate detection."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
import hashlib
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, Iterable

from cyprus_visual_policy import (
    CYPRUS_MACRO_COOLDOWN_REASON,
    cyprus_macro_family_from_entry,
    cyprus_scene_macro_family,
    macro_family_is_saturated,
    recent_real_visual_entries,
)


CYPRUS_VISUAL_HISTORY_PATH = Path(
    os.getenv("CYPRUS_VISUAL_HISTORY_PATH", ".cache/cyprus_visual_history_prod.json")
)
CYPRUS_VISUAL_HISTORY_PROD_PATH = Path(
    os.getenv("CYPRUS_VISUAL_HISTORY_PROD_PATH", ".cache/cyprus_visual_history_prod.json")
)
CYPRUS_VISUAL_HISTORY_TEST_PATH = Path(
    os.getenv("CYPRUS_VISUAL_HISTORY_TEST_PATH", ".cache/cyprus_visual_history_test.json")
)
CYPRUS_VISUAL_EXACT_DAYS = 30
CYPRUS_VISUAL_NEAR_DAYS = 14
CYPRUS_VISUAL_DHASH_THRESHOLD = 6
CYPRUS_VISUAL_PHASH_THRESHOLD = 10
CYPRUS_VISUAL_SCENE_RECENT_COUNT = 3
CYPRUS_VISUAL_COMPOSITION_RECENT_COUNT = 5
CYPRUS_VISUAL_BAY_ARCHETYPE_RECENT_COUNT = 10
CYPRUS_VISUAL_ELEVATED_ARCHETYPE_RECENT_COUNT = 6


@dataclass(frozen=True)
class CyprusVisualDuplicateResult:
    accepted: bool
    reason: str
    sha256: str
    perceptual_hash: str | None
    phash: str | None = None
    min_distance: int | None = None
    min_phash_distance: int | None = None
    matched_entry: dict[str, Any] | None = None


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _parse_date(value: object) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def _today() -> date:
    return datetime.utcnow().date()


def _within_days(entry: dict[str, Any], current: date, days: int) -> bool:
    entry_date = _parse_date(entry.get("date"))
    if entry_date is None:
        return True
    return current - timedelta(days=days) <= entry_date <= current


def cyprus_visual_history_path(namespace: str = "prod") -> Path:
    value = str(namespace or "prod").strip().lower()
    if value in {"prod", "production"}:
        return CYPRUS_VISUAL_HISTORY_PROD_PATH
    if value in {"test", "safe_test"}:
        return CYPRUS_VISUAL_HISTORY_TEST_PATH
    if value in {"dry", "dry_run", "none"}:
        return CYPRUS_VISUAL_HISTORY_TEST_PATH
    raise ValueError("namespace must be 'prod' or 'test'")


def _backup_malformed_history(path: Path) -> None:
    if not path.exists():
        return
    stamp = datetime.utcnow().strftime("%Y%m%d%H%M%S")
    backup = path.with_name(f"{path.name}.malformed.{stamp}.bak")
    try:
        backup.write_bytes(path.read_bytes())
        logging.warning("Cyprus visual malformed history backed up to %s", backup)
    except Exception as exc:
        logging.warning("Cyprus visual malformed history backup failed: %s", exc)


def load_cyprus_visual_history(
    path: str | Path = CYPRUS_VISUAL_HISTORY_PATH,
) -> list[dict[str, Any]]:
    history_path = Path(path)
    if not history_path.exists():
        return []
    try:
        data = json.loads(history_path.read_text("utf-8"))
    except Exception as exc:
        logging.warning("Cyprus visual history read failed: %s", exc)
        _backup_malformed_history(history_path)
        return []
    if not isinstance(data, list):
        logging.warning("Cyprus visual history is not a list: %s", history_path)
        _backup_malformed_history(history_path)
        return []
    return [entry for entry in data if isinstance(entry, dict)]


def cyprus_visual_archetype_from_entry(entry: dict[str, Any]) -> str:
    explicit = str(entry.get("visual_archetype") or "").strip()
    if explicit:
        return explicit
    scene = str(entry.get("selected_scene") or "").strip().lower()
    composition = str(entry.get("composition") or "").strip().lower()
    if any(token in scene for token in ("bay", "cove", "lagoon")):
        return "bay_panorama"
    if scene in {"open_sea_cliffs", "mountain_coast_view", "rocky_cove_overlook"} and any(
        token in composition for token in ("aerial", "raised", "wide panorama", "cliff")
    ):
        return "elevated_cliff_panorama"
    if scene in {"long_sandy_beach", "open_beach_horizon"}:
        return "beach_eye_level"
    if scene in {"coastal_promenade", "beach_cafe_terrace"}:
        return "promenade_eye_level"
    if scene == "marina_walkway":
        return "marina_closeup"
    if scene in {"small_harbour", "harbour_pier_waterlevel", "breakwater_coast"}:
        return "harbour_pier"
    if scene in {"coastal_urban_rooftop", "inland_urban_rooftop"}:
        return "urban_rooftop"
    if scene in {"troodos_landscape", "inland_village", "dry_inland_landscape", "salt_lake_landscape"}:
        return scene
    return "open_sea_shore" if scene else ""


def load_cyprus_visual_reference_history(
    paths: Iterable[str | Path],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, str]] = set()
    for path in paths:
        for source_entry in load_cyprus_visual_history(path):
            entry = dict(source_entry)
            archetype = cyprus_visual_archetype_from_entry(entry)
            if archetype:
                entry["visual_archetype"] = archetype
            key = (
                str(entry.get("date") or ""),
                str(entry.get("post_type") or ""),
                str(entry.get("sha256") or ""),
                str(entry.get("cache_key") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            merged.append(entry)
    merged.sort(key=lambda entry: str(entry.get("date") or ""))
    return merged


def save_cyprus_visual_history(
    entries: list[dict[str, Any]],
    path: str | Path = CYPRUS_VISUAL_HISTORY_PATH,
) -> None:
    history_path = Path(path)
    history_path.parent.mkdir(parents=True, exist_ok=True)
    tmp = history_path.with_suffix(history_path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2, sort_keys=True),
        "utf-8",
    )
    tmp.replace(history_path)


def _hamming_hex(left: str, right: str) -> int:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except Exception:
        return 10**9


def hamming_distance_hex(left: str | None, right: str | None) -> int | None:
    if not left or not right:
        return None
    return _hamming_hex(left, right)


def pillow_available() -> bool:
    try:
        import PIL  # noqa: F401

        return True
    except Exception:
        return False


def ensure_pillow_for_visual_dedup() -> bool:
    available = pillow_available()
    if not available:
        logging.error("Cyprus visual near-duplicate detection unavailable: Pillow missing.")
    return available


def _dhash_from_pixels(
    pixels: list[int],
    width: int,
    height: int,
    *,
    hash_size: int = 8,
) -> str:
    if width <= 0 or height <= 0 or len(pixels) < width * height:
        raise ValueError("invalid pixel buffer")
    target_w = hash_size + 1
    target_h = hash_size
    sample: list[int] = []
    for y in range(target_h):
        src_y = min(height - 1, int((y + 0.5) * height / target_h))
        for x in range(target_w):
            src_x = min(width - 1, int((x + 0.5) * width / target_w))
            sample.append(pixels[src_y * width + src_x])

    bits: list[str] = []
    for y in range(target_h):
        row = y * target_w
        for x in range(hash_size):
            bits.append("1" if sample[row + x] > sample[row + x + 1] else "0")
    return f"{int(''.join(bits), 2):0{hash_size * hash_size // 4}x}"


def _read_ppm_or_pgm(path: Path) -> tuple[list[int], int, int] | None:
    data = path.read_bytes()
    index = 0

    def token() -> bytes:
        nonlocal index
        while index < len(data):
            byte = data[index]
            if byte == 35:
                while index < len(data) and data[index] not in b"\r\n":
                    index += 1
            elif chr(byte).isspace():
                index += 1
            else:
                break
        start = index
        while index < len(data) and not chr(data[index]).isspace():
            index += 1
        return data[start:index]

    magic = token()
    if magic not in {b"P5", b"P6"}:
        return None
    try:
        width = int(token())
        height = int(token())
        max_value = int(token())
    except ValueError:
        return None
    if width <= 0 or height <= 0 or max_value <= 0 or max_value > 255:
        return None
    while index < len(data) and chr(data[index]).isspace():
        index += 1
        break
    raw = data[index:]
    expected = width * height * (3 if magic == b"P6" else 1)
    if len(raw) < expected:
        return None
    pixels: list[int] = []
    if magic == b"P5":
        pixels = [int(value) for value in raw[: width * height]]
    else:
        for offset in range(0, expected, 3):
            r, g, b = raw[offset], raw[offset + 1], raw[offset + 2]
            pixels.append((299 * r + 587 * g + 114 * b) // 1000)
    return pixels, width, height


def dhash_file(path: str | Path, *, hash_size: int = 8) -> str | None:
    image_path = Path(path)
    try:
        from PIL import Image, ImageOps  # type: ignore

        with Image.open(image_path) as image:
            image = ImageOps.grayscale(image)
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:  # pragma: no cover - old Pillow fallback
                resample = Image.LANCZOS
            image = image.resize((hash_size + 1, hash_size), resample)
            values = list(image.getdata())
        return _dhash_from_pixels(values, hash_size + 1, hash_size, hash_size=hash_size)
    except Exception:
        ppm = _read_ppm_or_pgm(image_path)
        if ppm is None:
            logging.error("Cyprus visual near-duplicate detection unavailable: Pillow missing.")
            return None
        pixels, width, height = ppm
        return _dhash_from_pixels(pixels, width, height, hash_size=hash_size)


def _sample_grayscale(
    pixels: list[int],
    width: int,
    height: int,
    *,
    target: int = 32,
) -> list[float]:
    if width <= 0 or height <= 0 or len(pixels) < width * height:
        raise ValueError("invalid pixel buffer")
    sample: list[float] = []
    for y in range(target):
        src_y = min(height - 1, int((y + 0.5) * height / target))
        for x in range(target):
            src_x = min(width - 1, int((x + 0.5) * width / target))
            sample.append(float(pixels[src_y * width + src_x]))
    return sample


def _phash_from_sample(values: list[float], *, size: int = 32, low: int = 8) -> str:
    if len(values) < size * size:
        raise ValueError("invalid DCT sample")
    coeffs: list[float] = []
    for v in range(low):
        for u in range(low):
            total = 0.0
            for y in range(size):
                cy = math.cos(((2 * y + 1) * v * math.pi) / (2 * size))
                row = y * size
                for x in range(size):
                    cx = math.cos(((2 * x + 1) * u * math.pi) / (2 * size))
                    total += values[row + x] * cx * cy
            coeffs.append(total)
    comparable = coeffs[1:]
    median = sorted(comparable)[len(comparable) // 2] if comparable else 0.0
    bits = ["1" if value > median else "0" for value in coeffs]
    return f"{int(''.join(bits), 2):0{low * low // 4}x}"


def phash_file(path: str | Path) -> str | None:
    image_path = Path(path)
    try:
        from PIL import Image, ImageOps  # type: ignore

        with Image.open(image_path) as image:
            image = ImageOps.grayscale(image)
            try:
                resample = Image.Resampling.LANCZOS
            except AttributeError:  # pragma: no cover - old Pillow fallback
                resample = Image.LANCZOS
            image = image.resize((32, 32), resample)
            values = [float(value) for value in image.getdata()]
        return _phash_from_sample(values)
    except Exception:
        ppm = _read_ppm_or_pgm(image_path)
        if ppm is None:
            logging.error("Cyprus visual pHash detection unavailable: Pillow missing.")
            return None
        pixels, width, height = ppm
        return _phash_from_sample(_sample_grayscale(pixels, width, height))


def _recent_entries(history: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    return [entry for entry in history if isinstance(entry, dict)][-limit:]


def evaluate_cyprus_visual_candidate(
    image_path: str | Path,
    *,
    date_value: str,
    post_type: str,
    selected_scene: str,
    prompt_version: str,
    composition: str | None = None,
    visual_archetype: str | None = None,
    history_path: str | Path = CYPRUS_VISUAL_HISTORY_PATH,
    reference_history_paths: Iterable[str | Path] | None = None,
    current_date: date | None = None,
    threshold: int = CYPRUS_VISUAL_DHASH_THRESHOLD,
    phash_threshold: int = CYPRUS_VISUAL_PHASH_THRESHOLD,
) -> CyprusVisualDuplicateResult:
    current = current_date or _parse_date(date_value) or _today()
    history = (
        load_cyprus_visual_reference_history(reference_history_paths)
        if reference_history_paths is not None
        else load_cyprus_visual_history(history_path)
    )
    digest = sha256_file(image_path)
    perceptual = dhash_file(image_path)
    phash = phash_file(image_path)

    for entry in history:
        if not _within_days(entry, current, CYPRUS_VISUAL_EXACT_DAYS):
            continue
        if str(entry.get("sha256") or "") == digest:
            return CyprusVisualDuplicateResult(
                accepted=False,
                reason="exact_duplicate",
                sha256=digest,
                perceptual_hash=perceptual,
                phash=phash,
                matched_entry=entry,
            )

    min_distance: int | None = None
    nearest_entry: dict[str, Any] | None = None
    if perceptual:
        for entry in history:
            if not _within_days(entry, current, CYPRUS_VISUAL_NEAR_DAYS):
                continue
            previous_hash = str(entry.get("perceptual_hash") or "")
            if not previous_hash:
                continue
            distance = _hamming_hex(perceptual, previous_hash)
            if min_distance is None or distance < min_distance:
                min_distance = distance
                nearest_entry = entry
        if min_distance is not None and min_distance <= threshold:
            return CyprusVisualDuplicateResult(
                accepted=False,
                reason="near_duplicate",
                sha256=digest,
                perceptual_hash=perceptual,
                phash=phash,
                min_distance=min_distance,
                matched_entry=nearest_entry,
            )

    min_phash_distance: int | None = None
    nearest_phash_entry: dict[str, Any] | None = None
    if phash:
        for entry in history:
            if not _within_days(entry, current, CYPRUS_VISUAL_NEAR_DAYS):
                continue
            previous_hash = str(entry.get("phash") or "")
            if not previous_hash:
                continue
            distance = _hamming_hex(phash, previous_hash)
            if min_phash_distance is None or distance < min_phash_distance:
                min_phash_distance = distance
                nearest_phash_entry = entry
        if min_phash_distance is not None and min_phash_distance <= phash_threshold:
            return CyprusVisualDuplicateResult(
                accepted=False,
                reason="near_duplicate_phash",
                sha256=digest,
                perceptual_hash=perceptual,
                phash=phash,
                min_distance=min_distance,
                min_phash_distance=min_phash_distance,
                matched_entry=nearest_phash_entry,
            )

    archetype_value = str(visual_archetype or "").strip()
    if archetype_value:
        recent = _recent_entries(history, 1)
        if recent and cyprus_visual_archetype_from_entry(recent[-1]) == archetype_value:
            return CyprusVisualDuplicateResult(
                accepted=False,
                reason="immediate_visual_archetype_repeat",
                sha256=digest,
                perceptual_hash=perceptual,
                phash=phash,
                min_distance=min_distance,
                min_phash_distance=min_phash_distance,
                matched_entry=recent[-1],
            )
        cooldowns = {
            "bay_panorama": CYPRUS_VISUAL_BAY_ARCHETYPE_RECENT_COUNT,
            "elevated_cliff_panorama": CYPRUS_VISUAL_ELEVATED_ARCHETYPE_RECENT_COUNT,
        }
        cooldown = cooldowns.get(archetype_value)
        if cooldown:
            for entry in _recent_entries(history, cooldown):
                if cyprus_visual_archetype_from_entry(entry) == archetype_value:
                    return CyprusVisualDuplicateResult(
                        accepted=False,
                        reason=f"recent_{archetype_value}",
                        sha256=digest,
                        perceptual_hash=perceptual,
                        phash=phash,
                        min_distance=min_distance,
                        min_phash_distance=min_phash_distance,
                        matched_entry=entry,
                    )

    scene_value = str(selected_scene or "").strip()
    if scene_value:
        for entry in _recent_entries(history, CYPRUS_VISUAL_SCENE_RECENT_COUNT):
            if str(entry.get("selected_scene") or "").strip() == scene_value:
                return CyprusVisualDuplicateResult(
                    accepted=False,
                    reason="recent_scene_family",
                    sha256=digest,
                    perceptual_hash=perceptual,
                    phash=phash,
                    min_distance=min_distance,
                    min_phash_distance=min_phash_distance,
                    matched_entry=entry,
                )

    composition_value = str(composition or "").strip()
    if composition_value:
        for entry in _recent_entries(history, CYPRUS_VISUAL_COMPOSITION_RECENT_COUNT):
            if str(entry.get("composition") or "").strip() == composition_value:
                return CyprusVisualDuplicateResult(
                    accepted=False,
                    reason="recent_composition",
                    sha256=digest,
                    perceptual_hash=perceptual,
                    phash=phash,
                    min_distance=min_distance,
                    min_phash_distance=min_phash_distance,
                    matched_entry=entry,
                )

    # Final macro diversity gate. It runs last so the existing exact/perceptual/
    # archetype/scene/composition priorities are untouched, and it is a hard gate:
    # the caller's LRU bypass covers only recent_scene_family and recent_composition.
    macro_candidate = cyprus_scene_macro_family(scene_value)
    if macro_family_is_saturated(history, macro_candidate):
        saturating_entry = next(
            (
                entry
                for entry in reversed(recent_real_visual_entries(history))
                if cyprus_macro_family_from_entry(entry) == macro_candidate
            ),
            None,
        )
        return CyprusVisualDuplicateResult(
            accepted=False,
            reason=CYPRUS_MACRO_COOLDOWN_REASON,
            sha256=digest,
            perceptual_hash=perceptual,
            phash=phash,
            min_distance=min_distance,
            min_phash_distance=min_phash_distance,
            matched_entry=saturating_entry,
        )

    return CyprusVisualDuplicateResult(
        accepted=True,
        reason="accepted",
        sha256=digest,
        perceptual_hash=perceptual,
        phash=phash,
        min_distance=min_distance,
        min_phash_distance=min_phash_distance,
        matched_entry=nearest_entry,
    )


def record_cyprus_visual_publication(
    *,
    date_value: str,
    post_type: str,
    image_path: str | Path,
    selected_scene: str,
    prompt_version: str,
    cache_key: str,
    style_name: str,
    composition: str | None = None,
    visual_archetype: str | None = None,
    history_path: str | Path = CYPRUS_VISUAL_HISTORY_PATH,
) -> dict[str, Any]:
    current = _parse_date(date_value) or _today()
    # Reload immediately before recording so concurrent morning/evening runs keep
    # whichever history the cache restored in this runner.
    entries = [
        entry
        for entry in load_cyprus_visual_history(history_path)
        if _within_days(entry, current, 45)
    ]
    entry = {
        "date": date_value,
        "post_type": post_type,
        "sha256": sha256_file(image_path),
        "perceptual_hash": dhash_file(image_path),
        "phash": phash_file(image_path),
        "selected_scene": selected_scene,
        "composition": composition or "",
        "visual_archetype": visual_archetype or "",
        # Additive field; legacy entries without it derive their macro from the scene.
        "scene_macro_family": cyprus_scene_macro_family(selected_scene),
        "prompt_version": prompt_version,
        "cache_key": cache_key,
        "style_name": style_name,
        "path": str(Path(image_path)),
    }
    dedup_key = (entry["date"], entry["post_type"], entry["sha256"])
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for existing in entries:
        key = (
            str(existing.get("date") or ""),
            str(existing.get("post_type") or ""),
            str(existing.get("sha256") or ""),
        )
        if key == dedup_key:
            continue
        if key in seen:
            continue
        seen.add(key)
        merged.append(existing)
    merged.append(entry)
    entries = merged
    save_cyprus_visual_history(entries, history_path)
    return entry


__all__ = [
    "CYPRUS_VISUAL_DHASH_THRESHOLD",
    "CYPRUS_VISUAL_BAY_ARCHETYPE_RECENT_COUNT",
    "CYPRUS_VISUAL_ELEVATED_ARCHETYPE_RECENT_COUNT",
    "CYPRUS_VISUAL_EXACT_DAYS",
    "CYPRUS_VISUAL_HISTORY_PATH",
    "CYPRUS_VISUAL_HISTORY_PROD_PATH",
    "CYPRUS_VISUAL_HISTORY_TEST_PATH",
    "CYPRUS_VISUAL_NEAR_DAYS",
    "CYPRUS_VISUAL_PHASH_THRESHOLD",
    "CyprusVisualDuplicateResult",
    "cyprus_visual_history_path",
    "cyprus_visual_archetype_from_entry",
    "dhash_file",
    "ensure_pillow_for_visual_dedup",
    "evaluate_cyprus_visual_candidate",
    "hamming_distance_hex",
    "load_cyprus_visual_history",
    "load_cyprus_visual_reference_history",
    "pillow_available",
    "phash_file",
    "record_cyprus_visual_publication",
    "save_cyprus_visual_history",
    "sha256_file",
]
