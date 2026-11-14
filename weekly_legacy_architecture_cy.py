#!/usr/bin/env python3
"""Verified decorative architecture for the existing Cyprus legacy cover.

No weather interpretation, HTTP, Telegram calls, or change to R10/R12 guards.
Only an independently approved on-disk JPEG may replace the gradient.
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

SIZE = (1080, 1350)
ASSET_ROOT = Path(__file__).resolve().parent / "assets" / "weekly" / "cy" / "legacy_architecture"
MANIFEST = ASSET_ROOT / "manifest.json"
ASSET_FILENAME = "cyprus_stone_architecture_neutral.jpg"
SCHEMA = "cy_weekly_legacy_background_r12h2_v1"


def approved_architecture_background():
    """Return (PIL.Image RGB, sha256), or None when not approved.

    Malformed manifests, missing or modified assets always fail; caller uses
    the historical gradient. The approval flag never changes in this function.
    """
    from PIL import Image

    doc = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if doc.get("schema") != SCHEMA or doc.get("size") != list(SIZE):
        raise ValueError("R12-H2: invalid architecture manifest")
    if doc.get("approved") is not True:
        return None
    if doc.get("file") != ASSET_FILENAME:
        raise ValueError("R12-H2: unexpected architecture asset path")
    declared_sha = doc.get("sha256")
    if (not isinstance(declared_sha, str) or len(declared_sha) != 64
            or any(c not in "0123456789abcdef" for c in declared_sha)):
        raise ValueError("R12-H2: invalid asset digest")
    asset = ASSET_ROOT / ASSET_FILENAME
    if not asset.is_file() or asset.is_symlink() or asset.parent.resolve() != ASSET_ROOT.resolve():
        raise ValueError("R12-H2: unsafe or missing architecture asset")
    if not 0 < asset.stat().st_size < 8_000_000:
        raise ValueError("R12-H2: invalid asset size")
    raw = asset.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    if digest != declared_sha:
        raise ValueError("R12-H2: asset SHA mismatch")
    with Image.open(io.BytesIO(raw)) as im:
        im.load()
        if im.format != "JPEG" or im.size != SIZE:
            raise ValueError("R12-H2: asset must be a 1080x1350 JPEG")
        return im.convert("RGB"), digest
