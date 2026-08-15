"""Local semantic guard for Cyprus provider-generated images.

The upstream providers occasionally return a technically valid but unrelated
image. This module adds a conservative, offline gate before such an image can
be accepted by ``world_en.imagegen``. It deliberately targets only high
confidence failures:

* the known 2026-07-31 incident image (and very close perceptual variants);
* screenshots / photographs of screens with a dense UI chrome band.

The local informative cover does not pass through ``world_en.imagegen`` and is
therefore unaffected.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from functools import wraps
import logging
import math
import os
from pathlib import Path
from typing import Any

try:
    from PIL import Image, ImageFilter, ImageOps
except Exception:  # pragma: no cover - imagegen already rejects without Pillow
    Image = None  # type: ignore
    ImageFilter = None  # type: ignore
    ImageOps = None  # type: ignore


LOG = logging.getLogger("imagegen.content_guard")

# Production incident: 2026-07-31, unrelated screen / medical-scan-like image.
# We keep only perceptual fingerprints, never the user image itself.
_INCIDENT_DHASH = "0f1f560b0f150b03"
_INCIDENT_PHASH = "d0692ba536263f36"
_INCIDENT_DHASH_MAX_DISTANCE = 6
_INCIDENT_PHASH_MAX_DISTANCE = 10

_SAMPLE_SIZE = 256
_EDGE_THRESHOLD = 45
_TOP_START_ROW = 2
_TOP_END_ROW = 32
_BODY_START_ROW = 48
_BODY_END_ROW = 254


@dataclass(frozen=True)
class ImageContentVerdict:
    valid: bool
    reason: str
    dhash: str
    phash: str
    incident_dhash_distance: int | None
    incident_phash_distance: int | None
    top_edge_density: float
    body_edge_density: float
    top_to_body_edge_ratio: float
    dense_top_rows: int

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _env_truthy(name: str, default: str = "") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "on"}


def content_guard_enabled() -> bool:
    """Enable explicitly, or implicitly for the Cyprus image workflow."""
    explicit = os.getenv("CY_IMAGE_CONTENT_GUARD")
    if explicit is not None:
        return _env_truthy("CY_IMAGE_CONTENT_GUARD")
    return _env_truthy("CY_IMG_ENABLED")


def _hamming_hex(left: str, right: str) -> int | None:
    try:
        return (int(left, 16) ^ int(right, 16)).bit_count()
    except Exception:
        return None


def _dhash(image: "Image.Image", *, hash_size: int = 8) -> str:
    gray = ImageOps.grayscale(image).resize(
        (hash_size + 1, hash_size),
        Image.Resampling.LANCZOS,
    )
    values = list(gray.getdata())
    bits: list[str] = []
    for y in range(hash_size):
        row = y * (hash_size + 1)
        for x in range(hash_size):
            bits.append("1" if values[row + x] > values[row + x + 1] else "0")
    return f"{int(''.join(bits), 2):0{hash_size * hash_size // 4}x}"


def _phash(image: "Image.Image", *, size: int = 32, low: int = 8) -> str:
    gray = ImageOps.grayscale(image).resize((size, size), Image.Resampling.LANCZOS)
    values = [float(value) for value in gray.getdata()]
    cos_x = [
        [math.cos(((2 * x + 1) * u * math.pi) / (2 * size)) for x in range(size)]
        for u in range(low)
    ]
    cos_y = [
        [math.cos(((2 * y + 1) * v * math.pi) / (2 * size)) for y in range(size)]
        for v in range(low)
    ]
    coeffs: list[float] = []
    for v in range(low):
        for u in range(low):
            total = 0.0
            for y in range(size):
                row = y * size
                cy = cos_y[v][y]
                total += sum(values[row + x] * cos_x[u][x] * cy for x in range(size))
            coeffs.append(total)
    comparable = coeffs[1:]
    median = sorted(comparable)[len(comparable) // 2] if comparable else 0.0
    bits = ["1" if value > median else "0" for value in coeffs]
    return f"{int(''.join(bits), 2):0{low * low // 4}x}"


def _edge_metrics(image: "Image.Image") -> tuple[float, float, float, int]:
    sample = ImageOps.grayscale(
        image.convert("RGB").resize(
            (_SAMPLE_SIZE, _SAMPLE_SIZE),
            Image.Resampling.LANCZOS,
        )
    )
    edges = sample.filter(ImageFilter.FIND_EDGES)
    values = list(edges.getdata())
    rows: list[float] = []
    for y in range(_SAMPLE_SIZE):
        start = y * _SAMPLE_SIZE
        row = values[start : start + _SAMPLE_SIZE]
        rows.append(sum(value >= _EDGE_THRESHOLD for value in row) / _SAMPLE_SIZE)

    top_rows = rows[_TOP_START_ROW:_TOP_END_ROW]
    body_rows = rows[_BODY_START_ROW:_BODY_END_ROW]
    top = sum(top_rows) / len(top_rows)
    body = sum(body_rows) / len(body_rows)
    ratio = top / max(body, 1e-6)
    dense_top_rows = sum(value >= 0.20 for value in top_rows)
    return top, body, ratio, dense_top_rows


def inspect_provider_image(path: str | Path) -> ImageContentVerdict:
    if Image is None:
        return ImageContentVerdict(
            valid=False,
            reason="pillow_unavailable",
            dhash="",
            phash="",
            incident_dhash_distance=None,
            incident_phash_distance=None,
            top_edge_density=0.0,
            body_edge_density=0.0,
            top_to_body_edge_ratio=0.0,
            dense_top_rows=0,
        )

    with Image.open(path) as opened:
        image = opened.convert("RGB")
        dhash = _dhash(image)
        incident_dhash_distance = _hamming_hex(dhash, _INCIDENT_DHASH)
        phash = _phash(image)
        incident_phash_distance = _hamming_hex(phash, _INCIDENT_PHASH)
        top, body, ratio, dense_top_rows = _edge_metrics(image)

    known_incident = (
        incident_dhash_distance is not None
        and incident_phash_distance is not None
        and incident_dhash_distance <= _INCIDENT_DHASH_MAX_DISTANCE
        and incident_phash_distance <= _INCIDENT_PHASH_MAX_DISTANCE
    )
    screenshot_chrome = (
        top >= 0.10
        and body <= 0.08
        and ratio >= 3.0
        and dense_top_rows >= 5
    )

    if known_incident:
        reason = "known_unrelated_incident"
    elif screenshot_chrome:
        reason = "screen_or_ui_chrome"
    else:
        reason = "accepted"

    return ImageContentVerdict(
        valid=reason == "accepted",
        reason=reason,
        dhash=dhash,
        phash=phash,
        incident_dhash_distance=incident_dhash_distance,
        incident_phash_distance=incident_phash_distance,
        top_edge_density=round(top, 6),
        body_edge_density=round(body, 6),
        top_to_body_edge_ratio=round(ratio, 6),
        dense_top_rows=dense_top_rows,
    )


def install_imagegen_guard(imagegen_module: Any) -> None:
    """Patch the technical validator once, keeping provider retry semantics."""
    original_validate = getattr(imagegen_module, "_validate_generated_image", None)
    original_set_diagnostics = getattr(imagegen_module, "_set_backend_diagnostics", None)
    if not callable(original_validate) or not callable(original_set_diagnostics):
        raise RuntimeError("world_en.imagegen validation hooks are unavailable")
    if getattr(original_validate, "_cyprus_content_guard_installed", False):
        return

    pending: dict[str, dict[str, Any]] = {}

    @wraps(original_set_diagnostics)
    def guarded_set_diagnostics(backend: str, payload: dict[str, Any]) -> None:
        merged = dict(payload)
        guard_payload = pending.pop(str(backend), None)
        if guard_payload:
            merged["content_guard"] = guard_payload
            if not guard_payload.get("valid"):
                merged["error_category"] = "semantic_mismatch"
                merged["error_message"] = (
                    "provider image rejected by Cyprus content guard: "
                    + str(guard_payload.get("reason") or "unknown")
                )
        original_set_diagnostics(backend, merged)

    @wraps(original_validate)
    def guarded_validate(
        *,
        backend: str,
        out_path: Path,
        payload: bytes,
        status_code: int | None = None,
        content_type: str | None = None,
    ):
        result = original_validate(
            backend=backend,
            out_path=out_path,
            payload=payload,
            status_code=status_code,
            content_type=content_type,
        )
        if result is None or not content_guard_enabled():
            return result

        try:
            verdict = inspect_provider_image(out_path)
        except Exception as exc:
            # Fail closed for Cyprus production: a broken relevance gate must
            # trigger the normal provider/fallback ladder, never a blind send.
            verdict = ImageContentVerdict(
                valid=False,
                reason=f"content_guard_error:{exc.__class__.__name__}",
                dhash="",
                phash="",
                incident_dhash_distance=None,
                incident_phash_distance=None,
                top_edge_density=0.0,
                body_edge_density=0.0,
                top_to_body_edge_ratio=0.0,
                dense_top_rows=0,
            )

        pending[str(backend)] = verdict.to_dict()
        if verdict.valid:
            return result

        try:
            Path(out_path).unlink(missing_ok=True)
        except Exception:
            pass
        LOG.warning(
            "%s provider image rejected before publication: reason=%s "
            "dhash=%s phash=%s top_edge=%.4f body_edge=%.4f ratio=%.2f",
            backend,
            verdict.reason,
            verdict.dhash,
            verdict.phash,
            verdict.top_edge_density,
            verdict.body_edge_density,
            verdict.top_to_body_edge_ratio,
        )
        return None

    guarded_validate._cyprus_content_guard_installed = True  # type: ignore[attr-defined]
    imagegen_module._set_backend_diagnostics = guarded_set_diagnostics
    imagegen_module._validate_generated_image = guarded_validate
