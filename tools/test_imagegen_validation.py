#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Regression checks for backend image validation."""

from __future__ import annotations

import base64
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
    def __init__(
        self,
        *,
        content: bytes,
        content_type: str,
        status_code: int = 200,
        json_data: object | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.content = content
        self.headers = {"Content-Type": content_type, **(headers or {})}
        self.status_code = status_code
        self.text = ""
        self._json_data = json_data
        self.closed = False

    def json(self):
        if self._json_data is None:
            raise ValueError("fixture has no JSON payload")
        return self._json_data

    def iter_content(self, chunk_size: int = 64 * 1024):
        for index in range(0, len(self.content), chunk_size):
            yield self.content[index:index + chunk_size]

    def close(self) -> None:
        self.closed = True


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


def failed_pollinations_and_horde_count_as_two_backend_calls() -> None:
    old_pollinations = imagegen._fetch_from_pollinations
    old_horde = imagegen._fetch_from_horde
    old_custom_url = imagegen.CUSTOM_IMAGE_BASE_URL
    old_attempts = imagegen.MAX_ATTEMPTS
    calls: list[str] = []

    def fail_pollinations(*_args, **_kwargs):
        calls.append("pollinations")
        return None

    def fail_horde(*_args, **_kwargs):
        calls.append("stable_horde")
        return None

    try:
        imagegen._fetch_from_pollinations = fail_pollinations
        imagegen._fetch_from_horde = fail_horde
        imagegen.CUSTOM_IMAGE_BASE_URL = ""
        imagegen.MAX_ATTEMPTS = 1
        outcome = imagegen.generate_astro_image_outcome("prompt", "unused.jpg", max_backend_calls=10)
    finally:
        imagegen._fetch_from_pollinations = old_pollinations
        imagegen._fetch_from_horde = old_horde
        imagegen.CUSTOM_IMAGE_BASE_URL = old_custom_url
        imagegen.MAX_ATTEMPTS = old_attempts

    assert outcome.result is None
    assert calls == ["pollinations", "stable_horde"]
    assert outcome.actual_backend_call_count == 2
    assert [item["result"] for item in outcome.backend_attempts] == ["failed", "failed"]


def repeated_provider_none_results_stop_at_shared_limit() -> None:
    old_pollinations = imagegen._fetch_from_pollinations
    old_horde = imagegen._fetch_from_horde
    old_custom_url = imagegen.CUSTOM_IMAGE_BASE_URL
    old_attempts = imagegen.MAX_ATTEMPTS
    calls: list[str] = []

    def fail(name: str):
        def _inner(*_args, **_kwargs):
            calls.append(name)
            return None

        return _inner

    try:
        imagegen._fetch_from_pollinations = fail("pollinations")
        imagegen._fetch_from_horde = fail("stable_horde")
        imagegen.CUSTOM_IMAGE_BASE_URL = ""
        imagegen.MAX_ATTEMPTS = 5
        outcome = imagegen.generate_astro_image_outcome("prompt", "unused.jpg", max_backend_calls=10)
    finally:
        imagegen._fetch_from_pollinations = old_pollinations
        imagegen._fetch_from_horde = old_horde
        imagegen.CUSTOM_IMAGE_BASE_URL = old_custom_url
        imagegen.MAX_ATTEMPTS = old_attempts

    assert outcome.result is None
    assert outcome.exhausted
    assert outcome.actual_backend_call_count == 10
    assert len(calls) == 10


def per_provider_budgets_are_fair_and_bounded() -> None:
    old_pollinations = imagegen._fetch_from_pollinations
    old_horde = imagegen._fetch_from_horde
    old_custom_url = imagegen.CUSTOM_IMAGE_BASE_URL
    old_attempts = imagegen.MAX_ATTEMPTS
    calls: list[str] = []

    def fail(name: str):
        def _inner(*_args, **_kwargs):
            calls.append(name)
            return None

        return _inner

    try:
        imagegen._fetch_from_pollinations = fail("pollinations")
        imagegen._fetch_from_horde = fail("stable_horde")
        imagegen.CUSTOM_IMAGE_BASE_URL = ""
        imagegen.MAX_ATTEMPTS = 5
        outcome = imagegen.generate_astro_image_outcome(
            "prompt",
            "unused.jpg",
            max_backend_calls=10,
            backend_call_limits={"pollinations": 2, "stable_horde": 3, "custom": 0},
        )
    finally:
        imagegen._fetch_from_pollinations = old_pollinations
        imagegen._fetch_from_horde = old_horde
        imagegen.CUSTOM_IMAGE_BASE_URL = old_custom_url
        imagegen.MAX_ATTEMPTS = old_attempts

    assert outcome.result is None
    assert calls.count("pollinations") == 2
    assert calls.count("stable_horde") == 3
    assert outcome.actual_backend_call_count == 5
    assert outcome.configured_backends == ["pollinations", "stable_horde"]
    assert outcome.unconfigured_backends == ["custom"]


def stable_horde_failure_exposes_detailed_safe_diagnostics() -> None:
    old_post = imagegen.requests.post
    old_get = imagegen.requests.get
    old_sleep = imagegen.time.sleep
    old_attempts = imagegen.MAX_ATTEMPTS

    def fake_post(*_args, **_kwargs):
        return FakeResponse(
            content=b'{"id":"fixture-job-1"}',
            content_type="application/json",
            status_code=202,
            json_data={"id": "fixture-job-1"},
        )

    def fake_get(url: str, *_args, **_kwargs):
        if "/generate/check/" in url:
            return FakeResponse(
                content=b'{"done":true}',
                content_type="application/json",
                json_data={"done": True, "queue_position": 0, "waiting": 0, "processing": 0},
            )
        return FakeResponse(
            content=b'{"generations":[]}',
            content_type="application/json",
            json_data={"generations": [], "faulted": False, "cancelled": False},
        )

    try:
        imagegen.requests.post = fake_post
        imagegen.requests.get = fake_get
        imagegen.time.sleep = lambda _seconds: None
        imagegen.MAX_ATTEMPTS = 1
        outcome = imagegen.generate_astro_image_outcome_with_exclusions(
            "prompt",
            "unused.jpg",
            excluded_backends={"pollinations", "custom"},
            max_backend_calls=1,
            backend_call_limits={"stable_horde": 1},
        )
    finally:
        imagegen.requests.post = old_post
        imagegen.requests.get = old_get
        imagegen.time.sleep = old_sleep
        imagegen.MAX_ATTEMPTS = old_attempts

    assert outcome.result is None
    assert len(outcome.backend_attempts) == 1
    diag = outcome.backend_attempts[0]
    required = {
        "http_status",
        "submission_result",
        "request_id",
        "queue_status",
        "timeout",
        "faulted",
        "cancelled",
        "generations_count",
        "payload_byte_count",
        "content_type",
        "image_validation_failure",
        "exception_type",
        "error_category",
        "error_message",
        "elapsed_seconds",
    }
    assert required <= set(diag)
    assert diag["submission_result"] == "accepted"
    assert diag["request_id"] == "fixture-job-1"
    assert diag["generations_count"] == 0
    assert diag["error_category"] == "no_generations"
    assert "apikey" not in str(diag).lower()


def all_backends_excluded_fail_immediately() -> None:
    old_custom_url = imagegen.CUSTOM_IMAGE_BASE_URL
    try:
        imagegen.CUSTOM_IMAGE_BASE_URL = ""
        outcome = imagegen.generate_astro_image_outcome_with_exclusions(
            "prompt",
            "unused.jpg",
            excluded_backends={"pollinations", "stable_horde", "custom"},
            max_backend_calls=10,
        )
    finally:
        imagegen.CUSTOM_IMAGE_BASE_URL = old_custom_url

    assert outcome.result is None
    assert outcome.exhausted
    assert outcome.error_type == "NoBackendsAvailable"
    assert outcome.actual_backend_call_count == 0
    assert outcome.backend_attempts == []


def pillow_unavailable_rejects_otherwise_valid_image() -> None:
    old_image = imagegen.Image
    old_min = os.environ.get("IMAGEGEN_MIN_VALID_BYTES")
    os.environ["IMAGEGEN_MIN_VALID_BYTES"] = "128"
    with tempfile.TemporaryDirectory() as tmp_name:
        out_path = Path(tmp_name) / "candidate.png"
        payload = _png_bytes((20, 80, 140))
        out_path.write_bytes(payload)
        try:
            imagegen.Image = None
            result = imagegen._validate_generated_image(
                backend="pollinations",
                out_path=out_path,
                payload=payload,
                status_code=200,
                content_type="image/png",
            )
        finally:
            imagegen.Image = old_image
            if old_min is None:
                os.environ.pop("IMAGEGEN_MIN_VALID_BYTES", None)
            else:
                os.environ["IMAGEGEN_MIN_VALID_BYTES"] = old_min

        assert result is None
        assert not out_path.exists()


def horde_https_url_is_bounded_validated_and_accepted() -> None:
    old_get = imagegen.requests.get
    old_getaddrinfo = imagegen.socket.getaddrinfo
    old_min = os.environ.get("IMAGEGEN_MIN_VALID_BYTES")
    payload = _png_bytes((34, 91, 148))
    responses: list[FakeResponse] = []

    def fake_getaddrinfo(*_args, **_kwargs):
        return [
            (
                imagegen.socket.AF_INET,
                imagegen.socket.SOCK_STREAM,
                6,
                "",
                ("93.184.216.34", 443),
            )
        ]

    def fake_get(url: str, *_args, **kwargs):
        assert url == "https://images.example.test/horde/result.png"
        assert kwargs["allow_redirects"] is False
        assert kwargs["stream"] is True
        response = FakeResponse(
            content=payload,
            content_type="image/png",
            headers={"Content-Length": str(len(payload))},
        )
        responses.append(response)
        return response

    os.environ["IMAGEGEN_MIN_VALID_BYTES"] = "128"
    with tempfile.TemporaryDirectory() as tmp_name:
        out_path = Path(tmp_name) / "horde-url.png"
        try:
            imagegen.requests.get = fake_get
            imagegen.socket.getaddrinfo = fake_getaddrinfo
            decoded, diagnostics = imagegen.decode_or_fetch_horde_image(
                "https://images.example.test/horde/result.png",
                max_bytes=1024 * 1024,
            )
            assert decoded == payload
            assert diagnostics["horde_img_payload_kind"] == "https_url"
            assert diagnostics["horde_img_url_hostname"] == "images.example.test"
            assert diagnostics["horde_img_download_http_status"] == 200
            assert diagnostics["horde_img_download_content_type"] == "image/png"
            assert diagnostics["horde_img_downloaded_byte_count"] == len(payload)
            assert "https://" not in str(diagnostics)
            result = imagegen._validate_generated_image(
                backend="stable_horde",
                out_path=out_path,
                payload=decoded,
                status_code=200,
                content_type=diagnostics["horde_img_effective_content_type"],
            )
            assert result is not None
            assert out_path.read_bytes() == payload
            assert responses and responses[0].closed
        finally:
            imagegen.requests.get = old_get
            imagegen.socket.getaddrinfo = old_getaddrinfo
            if old_min is None:
                os.environ.pop("IMAGEGEN_MIN_VALID_BYTES", None)
            else:
                os.environ["IMAGEGEN_MIN_VALID_BYTES"] = old_min


def horde_data_url_and_plain_base64_are_strict_and_validated() -> None:
    old_min = os.environ.get("IMAGEGEN_MIN_VALID_BYTES")
    os.environ["IMAGEGEN_MIN_VALID_BYTES"] = "128"
    payload = _png_bytes((84, 122, 166))
    encoded = base64.b64encode(payload).decode("ascii")
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        try:
            for source, expected_kind, name in (
                (f"data:image/png;base64,{encoded}", "data_url", "data-url.png"),
                (encoded, "base64", "base64.png"),
            ):
                decoded, diagnostics = imagegen.decode_or_fetch_horde_image(source)
                assert decoded == payload
                assert diagnostics["horde_img_payload_kind"] == expected_kind
                assert diagnostics["horde_img_validation_result"] == "decoded"
                out_path = tmp / name
                result = imagegen._validate_generated_image(
                    backend="stable_horde",
                    out_path=out_path,
                    payload=decoded,
                    status_code=200,
                    content_type=diagnostics["horde_img_effective_content_type"] or "image/png",
                )
                assert result is not None
                assert out_path.read_bytes() == payload
        finally:
            if old_min is None:
                os.environ.pop("IMAGEGEN_MIN_VALID_BYTES", None)
            else:
                os.environ["IMAGEGEN_MIN_VALID_BYTES"] = old_min


def horde_invalid_base64_and_92_byte_payload_never_write_output() -> None:
    old_min = os.environ.get("IMAGEGEN_MIN_VALID_BYTES")
    os.environ["IMAGEGEN_MIN_VALID_BYTES"] = "128"
    with tempfile.TemporaryDirectory() as tmp_name:
        tmp = Path(tmp_name)
        try:
            invalid, invalid_diag = imagegen.decode_or_fetch_horde_image("%%%not-base64%%%")
            assert invalid is None
            assert invalid_diag["horde_img_payload_kind"] == "invalid"
            assert invalid_diag["horde_img_validation_result"] == "rejected_invalid_base64"

            decoded, diagnostics = imagegen.decode_or_fetch_horde_image(
                base64.b64encode(b"x" * 92).decode("ascii")
            )
            assert decoded == b"x" * 92
            assert diagnostics["horde_img_downloaded_byte_count"] == 92
            out_path = tmp / "invalid-92.png"
            out_path.write_bytes(b"stale-output")
            result = imagegen._validate_generated_image(
                backend="stable_horde",
                out_path=out_path,
                payload=decoded,
                status_code=200,
                content_type="image/png",
            )
            assert result is None
            assert not out_path.exists()
        finally:
            if old_min is None:
                os.environ.pop("IMAGEGEN_MIN_VALID_BYTES", None)
            else:
                os.environ["IMAGEGEN_MIN_VALID_BYTES"] = old_min


def horde_image_url_blocks_private_and_local_addresses() -> None:
    old_get = imagegen.requests.get
    old_getaddrinfo = imagegen.socket.getaddrinfo
    network_calls: list[str] = []

    def forbidden_get(url: str, *_args, **_kwargs):
        network_calls.append(url)
        raise AssertionError("unsafe Horde image URL reached requests.get")

    try:
        imagegen.requests.get = forbidden_get
        for source in (
            "http://169.254.169.254/latest/meta-data/",
            "http://127.0.0.1/horde.png",
            "http://localhost/horde.png",
        ):
            payload, diagnostics = imagegen.decode_or_fetch_horde_image(source)
            assert payload is None
            assert diagnostics["horde_img_payload_kind"] == "invalid"
            assert diagnostics["horde_img_validation_result"] == "rejected_unsafe_url"
            assert diagnostics["error_category"] == "unsafe_url"
        assert network_calls == []

        def fake_getaddrinfo(*_args, **_kwargs):
            return [
                (
                    imagegen.socket.AF_INET,
                    imagegen.socket.SOCK_STREAM,
                    6,
                    "",
                    ("93.184.216.34", 443),
                )
            ]

        def redirect_get(url: str, *_args, **_kwargs):
            network_calls.append(url)
            return FakeResponse(
                content=b"",
                content_type="application/octet-stream",
                status_code=302,
                headers={"Location": "http://169.254.169.254/latest/meta-data/"},
            )

        imagegen.socket.getaddrinfo = fake_getaddrinfo
        imagegen.requests.get = redirect_get
        payload, diagnostics = imagegen.decode_or_fetch_horde_image(
            "https://images.example.test/redirect"
        )
        assert payload is None
        assert diagnostics["horde_img_payload_kind"] == "invalid"
        assert diagnostics["horde_img_validation_result"] == "rejected_unsafe_url"
        assert network_calls == ["https://images.example.test/redirect"]
    finally:
        imagegen.requests.get = old_get
        imagegen.socket.getaddrinfo = old_getaddrinfo


def horde_configured_key_401_switches_once_to_anonymous() -> None:
    old_once = imagegen._fetch_from_horde_once
    old_key = imagegen.HORDE_API_KEY
    old_retry = imagegen.HORDE_TRY_ANON_ON_401
    keys: list[str] = []

    def fake_once(_prompt, _out_path, _size, _timeout, api_key):
        keys.append(api_key)
        if api_key == "fixture-configured-key":
            return None, 401, "AsyncNon2xx", {"http_status": 401, "submission_result": "rejected"}
        return None, 200, "NoGenerations", {"http_status": 200, "submission_result": "accepted"}

    state: dict[str, object] = {}
    try:
        imagegen._fetch_from_horde_once = fake_once
        imagegen.HORDE_API_KEY = "fixture-configured-key"
        imagegen.HORDE_TRY_ANON_ON_401 = True
        imagegen._fetch_from_horde("prompt", Path("unused-1.png"), credential_state=state)
        first_diag = imagegen._take_backend_diagnostics("stable_horde")
        imagegen._fetch_from_horde("prompt", Path("unused-2.png"), credential_state=state)
        second_diag = imagegen._take_backend_diagnostics("stable_horde")
        imagegen._fetch_from_horde("prompt", Path("unused-3.png"), credential_state=state)
        third_diag = imagegen._take_backend_diagnostics("stable_horde")
    finally:
        imagegen._fetch_from_horde_once = old_once
        imagegen.HORDE_API_KEY = old_key
        imagegen.HORDE_TRY_ANON_ON_401 = old_retry

    assert keys == ["fixture-configured-key", "0000000000", "0000000000", "0000000000"]
    assert state == {"configured_key_rejected": True, "initial_http_status": 401}
    assert first_diag["configured_key_rejected"] is True
    assert first_diag["anonymous_retry_used"] is True
    assert first_diag["initial_http_status"] == 401
    assert first_diag["initial_attempt"]["http_status"] == 401
    for diagnostics in (second_diag, third_diag):
        assert diagnostics["configured_key_rejected"] is True
        assert diagnostics["anonymous_retry_used"] is True
        assert diagnostics["initial_http_status"] == 401
    assert "fixture-configured-key" not in str((first_diag, second_diag, third_diag))


def main() -> None:
    checks = (
        pollinations_json_92_bytes_falls_back_to_horde,
        pollinations_text_plain_falls_back_to_horde,
        pollinations_invalid_jpeg_falls_back_to_horde,
        failed_pollinations_and_horde_count_as_two_backend_calls,
        repeated_provider_none_results_stop_at_shared_limit,
        per_provider_budgets_are_fair_and_bounded,
        stable_horde_failure_exposes_detailed_safe_diagnostics,
        all_backends_excluded_fail_immediately,
        pillow_unavailable_rejects_otherwise_valid_image,
        horde_https_url_is_bounded_validated_and_accepted,
        horde_data_url_and_plain_base64_are_strict_and_validated,
        horde_invalid_base64_and_92_byte_payload_never_write_output,
        horde_image_url_blocks_private_and_local_addresses,
        horde_configured_key_401_switches_once_to_anonymous,
    )
    for check in checks:
        check()
        print(f"PASS: {check.__name__}")
    print(f"OK: {len(checks)} imagegen validation checks passed")


if __name__ == "__main__":
    main()
