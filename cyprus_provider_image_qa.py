#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Provider-only semantic QA for generated Cyprus weather images."""

from __future__ import annotations

import base64
import json
import mimetypes
import os
from pathlib import Path
from typing import Any, Mapping

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

_REQUIRED_BOOLEAN_FIELDS = (
    "scene_dominant",
    "composition_present",
    "mediterranean_plausible",
    "tropical_mismatch",
    "malformed_structure",
    "weather_compatible",
    "text_logo_watermark_absent",
    "screen_ui_absent",
    "photographic_realism",
)
_CONFIDENCE_MIN = 0.75


def _unavailable(reason: str, *, error_type: str = "") -> dict[str, Any]:
    return {
        "status": "unavailable",
        "accepted": False,
        "reason_codes": [reason],
        "confidence": 0.0,
        "error_type": error_type,
    }


def normalize_semantic_verdict(payload: Mapping[str, Any] | object) -> dict[str, Any]:
    if not isinstance(payload, Mapping):
        return _unavailable("invalid_response_shape")
    for field in _REQUIRED_BOOLEAN_FIELDS:
        if not isinstance(payload.get(field), bool):
            return _unavailable(f"invalid_field:{field}")
    try:
        confidence = float(payload.get("confidence"))
    except (TypeError, ValueError):
        return _unavailable("invalid_confidence")
    if not 0.0 <= confidence <= 1.0:
        return _unavailable("invalid_confidence")

    reasons: list[str] = []
    if not payload["scene_dominant"]:
        reasons.append("scene_missing_or_non_dominant")
    if not payload["composition_present"]:
        reasons.append("composition_missing")
    if not payload["mediterranean_plausible"]:
        reasons.append("geography_mismatch")
    if payload["tropical_mismatch"]:
        reasons.append("tropical_or_jungle_mismatch")
    if payload["malformed_structure"]:
        reasons.append("malformed_structure")
    if not payload["weather_compatible"]:
        reasons.append("weather_mismatch")
    if not payload["text_logo_watermark_absent"]:
        reasons.append("visible_text_logo_or_watermark")
    if not payload["screen_ui_absent"]:
        reasons.append("screen_or_ui")
    if not payload["photographic_realism"]:
        reasons.append("non_photographic")

    if confidence < _CONFIDENCE_MIN:
        return {
            "status": "unavailable",
            "accepted": False,
            "reason_codes": ["low_confidence", *reasons],
            "confidence": confidence,
            "error_type": "",
        }
    return {
        "status": "reject" if reasons else "accept",
        "accepted": not reasons,
        "reason_codes": reasons or ["accepted"],
        "confidence": confidence,
        "error_type": "",
    }


def _json_object(text: str) -> Mapping[str, Any] | None:
    try:
        parsed = json.loads(str(text or "").strip())
    except json.JSONDecodeError:
        return None
    return parsed if isinstance(parsed, Mapping) else None


def evaluate_provider_image_semantics(
    image_path: str | Path,
    *,
    scene_family: str,
    composition: str,
    visual_context: Mapping[str, Any],
    response_payload: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Evaluate one provider image. Provider/API errors become fail-closed unavailable."""
    if response_payload is not None:
        return normalize_semantic_verdict(response_payload)

    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key or OpenAI is None:
        return _unavailable("qa_unavailable")

    path = Path(image_path)
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return _unavailable("image_read_failed", error_type=type(exc).__name__)
    if not raw:
        return _unavailable("image_empty")

    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    data_url = f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"
    model = (
        os.getenv("CY_IMAGE_QA_GEMINI_MODEL")
        or os.getenv("GEMINI_MODEL")
        or "gemini-2.5-flash"
    ).strip()
    rubric = {
        "requested_scene_family": str(scene_family or ""),
        "required_composition": str(composition or ""),
        "weather_context": dict(visual_context),
        "required_output_fields": list(_REQUIRED_BOOLEAN_FIELDS) + ["confidence"],
    }
    instruction = (
        "Act as a strict QA gate for one generated Cyprus weather photo. "
        "Return one JSON object only. Judge objective scene compliance, not subjective beauty. "
        "scene_dominant means the requested scene is clearly present and dominant. "
        "composition_present means the requested dominant composition is visibly satisfied. "
        "mediterranean_plausible must be false for obviously non-Cyprus geography. "
        "tropical_mismatch must be true for incompatible jungle/tropical-resort imagery. "
        "malformed_structure flags obvious impossible/deformed major landscape or architecture. "
        "weather_compatible must follow the supplied factual weather context. "
        "text_logo_watermark_absent and screen_ui_absent are strict. "
        "photographic_realism rejects illustration, fantasy or broken renders. "
        "confidence is 0..1.\n"
        + json.dumps(rubric, ensure_ascii=False, sort_keys=True)
    )
    try:
        client = OpenAI(
            api_key=key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            timeout=25.0,
            max_retries=0,
        )
        response = client.chat.completions.create(
            model=model,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text", "text": instruction},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }],
            max_tokens=450,
        )
        payload = _json_object(response.choices[0].message.content or "")
        if payload is None:
            return _unavailable("invalid_json_response")
        verdict = normalize_semantic_verdict(payload)
        verdict["model"] = model
        return verdict
    except Exception as exc:
        return _unavailable("qa_request_failed", error_type=type(exc).__name__)


__all__ = ["evaluate_provider_image_semantics", "normalize_semantic_verdict"]
