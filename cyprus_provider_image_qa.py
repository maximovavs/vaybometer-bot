#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider-only semantic QA for Cyprus AI weather images."""
from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Callable, Mapping

_REQUIRED_TRUE = (
    "scene_dominant",
    "composition_present",
    "mediterranean_plausible",
    "weather_compatible",
    "no_text_logo_watermark",
    "no_ui_screenshot",
    "photographic_realism",
)
_REQUIRED_FALSE = ("tropical_mismatch", "malformed_major_structure")
_ALLOWED_CONFIDENCE = {"high", "medium", "low"}


def _payload_dict(value: object) -> dict[str, Any] | None:
    if isinstance(value, Mapping):
        return dict(value)
    text = str(value or "").strip()
    if not text:
        return None
    fence = chr(96) * 3
    if text.startswith(fence):
        lines = text.splitlines()
        if lines and lines[0].startswith(fence):
            lines = lines[1:]
        if lines and lines[-1].strip() == fence:
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
    except Exception:
        return None
    return dict(parsed) if isinstance(parsed, Mapping) else None


def evaluate_structured_verdict(
    payload: Mapping[str, Any] | object,
    *,
    model: str = "",
) -> dict[str, Any]:
    data = _payload_dict(payload)
    if data is None:
        return {
            "available": False,
            "accepted": False,
            "status": "invalid_response",
            "reason": "invalid_json",
            "confidence": "none",
            "checks": {},
            "model": model,
        }

    confidence = str(data.get("confidence") or "").strip().lower()
    checks: dict[str, bool | None] = {}
    malformed = False
    for key in (*_REQUIRED_TRUE, *_REQUIRED_FALSE):
        value = data.get(key)
        checks[key] = value if isinstance(value, bool) else None
        malformed = malformed or not isinstance(value, bool)
    if confidence not in _ALLOWED_CONFIDENCE:
        malformed = True
    if malformed:
        return {
            "available": False,
            "accepted": False,
            "status": "invalid_response",
            "reason": "missing_or_invalid_fields",
            "confidence": confidence or "none",
            "checks": checks,
            "model": model,
        }

    raw_reasons = data.get("reason_codes")
    reason_codes = (
        [str(item).strip() for item in raw_reasons if str(item).strip()]
        if isinstance(raw_reasons, list)
        else []
    )
    failures = [key for key in _REQUIRED_TRUE if checks[key] is not True]
    failures.extend(key for key in _REQUIRED_FALSE if checks[key] is not False)
    if failures:
        return {
            "available": True,
            "accepted": False,
            "status": "rejected" if confidence == "high" else "indeterminate",
            "reason": reason_codes[0] if reason_codes else failures[0],
            "confidence": confidence,
            "checks": checks,
            "model": model,
        }
    if confidence != "high":
        return {
            "available": True,
            "accepted": False,
            "status": "indeterminate",
            "reason": "confidence_not_high",
            "confidence": confidence,
            "checks": checks,
            "model": model,
        }
    return {
        "available": True,
        "accepted": True,
        "status": "accepted",
        "reason": "all_required_checks_passed",
        "confidence": confidence,
        "checks": checks,
        "model": model,
    }


def _default_request(
    image_path: Path,
    *,
    metadata: Mapping[str, Any],
) -> tuple[object, str]:
    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key:
        raise RuntimeError("GEMINI_API_KEY is unavailable")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:
        raise RuntimeError("OpenAI SDK unavailable for Gemini vision QA") from exc

    model = (os.getenv("GEMINI_MODEL") or "gemini-3.7-flash").strip() or "gemini-3.7-flash"
    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    contract = {
        "requested_scene": str(metadata.get("selected_scene") or ""),
        "requested_composition": str(metadata.get("composition") or ""),
        "primary_weather": str(metadata.get("primary_weather") or metadata.get("weather_scenario") or ""),
        "visibility_condition": str(metadata.get("visibility_condition") or ""),
        "actual_precipitation": str(metadata.get("actual_precipitation") or ""),
        "explicit_storm": str(metadata.get("explicit_storm") or ""),
    }
    instruction = (
        "Audit this generated Cyprus weather image only against the supplied visual contract. "
        "Return one JSON object and nothing else. Do not score beauty. "
        "Required booleans: scene_dominant, composition_present, mediterranean_plausible, "
        "tropical_mismatch, malformed_major_structure, weather_compatible, "
        "no_text_logo_watermark, no_ui_screenshot, photographic_realism. "
        "confidence must be high, medium, or low; reason_codes must be a JSON array. "
        "A requested Cyprus promenade must be dominated by a plausible Mediterranean seafront, "
        "not a tropical or jungle path. Reject major deformation or factual weather contradiction. "
        + "CONTRACT=" + json.dumps(contract, ensure_ascii=False, sort_keys=True)
    )
    client = OpenAI(
        api_key=key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        timeout=25.0,
        max_retries=0,
    )
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:" + mime + ";base64," + encoded},
                    },
                ],
            }
        ],
        max_tokens=450,
    )
    return (response.choices[0].message.content or ""), model


def inspect_cyprus_provider_image(
    image_path: str | Path,
    *,
    metadata: Mapping[str, Any],
    visual_context: object | None = None,
    request_fn: Callable[..., object] | None = None,
) -> dict[str, Any]:
    path = Path(image_path)
    if not path.is_file():
        return {
            "available": False,
            "accepted": False,
            "status": "unavailable",
            "reason": "image_missing",
            "confidence": "none",
            "checks": {},
            "model": "",
        }
    try:
        if request_fn is not None:
            raw = request_fn(
                image_path=path,
                metadata=dict(metadata),
                visual_context=visual_context,
            )
            model = "injected"
        else:
            raw, model = _default_request(path, metadata=metadata)
    except Exception as exc:
        return {
            "available": False,
            "accepted": False,
            "status": "unavailable",
            "reason": "qa_request_failed",
            "confidence": "none",
            "checks": {},
            "model": "",
            "error_type": exc.__class__.__name__,
        }
    return evaluate_structured_verdict(raw, model=model)


__all__ = ["evaluate_structured_verdict", "inspect_cyprus_provider_image"]
