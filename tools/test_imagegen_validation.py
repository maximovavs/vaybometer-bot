#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for backend image validation."""

from __future__ import annotations

from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from PIL import Image  # type: ignore  # noqa: E402
import world_en.imagegen as imagegen  # noqa: E402


class FakeResponse:
    def __init__(self, *, content: bytes, content_type: str, status_code: int = 200) -> None:
        self.content = content
        self.headers = {"Content-Type": content_type}
        self.status_code = status_code
        self.text = ""


def _png_bytes(color: tuple[int, int, int]) -> bytes:
    buffer = BytesIO()
    Image.new("RGB", (300, 300), color).save(buffer, format="PNG")
    return buffer.getvalue()


def _run_invalid_pollinations_case(content: bytes, content_type: str) -> tuple[object, list[str], bool]:
    old_get = imagegen.requests.get
    old_horde = imagegen._fetch_from_horde
    old_min = os.environ.get("IMAGEGEN_MIN_VALID_BYTES")
    old_attempts = imagegen.MAX_ATTEMPTS
    horde_calls: list[str] = []
    os.environ["IMAGEGEN_MIN_VALID_BYTES"] = "128"
    imagegen.MAX_ATTEMPTS = 1

    def fake_get(*_args, **_kwargs):
        return FakeResponse(content=content, content_type=content_type)

    def fake_horde(_prompt: str, out_path: Path, **_kwargs):
        horde_calls.append(str(out_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        payload = _png_bytes((40, 120, 190))
        out_path.write_bytes(payload)
        return imagegen.ImageGenerationResult(
            path=str(out_path),
            backend="stable_horde",
            byte_count=len(payload),
            content_type="image/png",
            backend_attempts=[{"backend": "stable_horde"}],
        )

    with tempfile.TemporaryDirectory() as tmp_name:
        out_path = Path(tmp_name) / "candidate.jpg"
        try:
            imagegen.requests.get = fake_get
            imagegen._fetch_from_horde = fake_horde
            result = imagegen.generate_astro_image_result("prompt", str(out_path))
            exists_after = out_path.exists()
        finally:
            imagegen.requests.get = old_get
            imagegen._fetch_from_horde = old_horde
            imagegen.MAX_ATTEMPTS = old_attempts
            if old_min is None:
                os.environ.pop("IMAGEGEN_MIN_VALID_BYTES", None)
            else:
                os.environ["IMAGEGEN_MIN_VALID_BYTES"] = old_min
        return result, horde_calls, exists_after


def pollinations_json_92_bytes_falls_back_to_horde() -> None:
    body = (b'{"error":"temporary backend text"}' + b" " * 92)[:92]
    result, horde_calls, out_exists = _run_invalid_pollinations_case(body, "application/json")
    assert result is not None
    assert result.backend == "stable_horde"
    assert len(horde_calls) == 1
    assert out_exists


def pollinations_text_plain_falls_back_to_horde() -> None:
    result, horde_calls, _out_path = _run_invalid_pollinations_case(b"plain backend response" * 8, "text/plain")
    assert result is not None
    assert result.backend == "stable_horde"
    assert len(horde_calls) == 1


def pollinations_invalid_jpeg_falls_back_to_horde() -> None:
    result, horde_calls, _out_path = _run_invalid_pollinations_case(b"\xff\xd8\xff" + b"x" * 900, "image/jpeg")
    assert result is not None
    assert result.backend == "stable_horde"
    assert len(horde_calls) == 1


def main() -> None:
    checks = (
        pollinations_json_92_bytes_falls_back_to_horde,
        pollinations_text_plain_falls_back_to_horde,
        pollinations_invalid_jpeg_falls_back_to_horde,
    )
    for check in checks:
        check()
        print(f"PASS: {check.__name__}")
    print(f"OK: {len(checks)} imagegen validation checks passed")


if __name__ == "__main__":
    main()
