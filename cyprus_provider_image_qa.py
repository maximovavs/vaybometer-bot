#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Strict provider-only semantic QA for Cyprus generated images."""

from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import mimetypes
import os
from pathlib import Path
from typing import Any


QA_CHECK_KEYS = (
    "scene_family_present_and_dominant",
    "composition_present_and_dominant",
    "cyprus_mediterranean_character",
    "tropical_or_jungle_mismatch",
    "major_landscape_or_architecture_malformed",
    "weather_compatible",
    "visible_text_logo_or_watermark",
    "screenshot_or_ui",
    "photographic_editorial_realism",
)


DEFAULT_QA_MODEL = "gemini-3.7-flash"
DEFAULT_QA_AVAILABILITY_FALLBACK_MODEL = "gemini-2.5-flash"
_RETRYABLE_QA_AVAILABILITY_STATUS_CODES = frozenset({502, 503, 504})


@dataclass(frozen=True)
class ProviderImageQAVerdict:
    status: str
    accepted: bool
    reason: str
    checks: dict[str, bool]
    model: str = ""
    error_type: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "accepted": self.accepted,
            "reason": self.reason,
            "checks": dict(self.checks),
            "model": self.model,
            "error_type": self.error_type,
        }


def _context_value(context: object, key: str) -> Any:
    if isinstance(context, Mapping):
        return context.get(key)
    return getattr(context, key, None)


def build_provider_image_qa_request(
    *,
    selected_scene: str,
    composition: str,
    visual_context: object,
) -> dict[str, Any]:
    """Build a narrow factual request without secrets or image bytes."""
    hazards = _context_value(visual_context, "hazards")
    if not isinstance(hazards, (list, tuple)):
        hazards = []
    return {
        "requested_scene_family": str(selected_scene or "").strip(),
        "required_dominant_composition": str(composition or "").strip(),
        "weather_context": {
            "post_type": _context_value(visual_context, "post_type"),
            "weather_main": _context_value(visual_context, "weather_main"),
            "hazards": [str(item) for item in hazards],
            "visibility_condition": _context_value(visual_context, "visibility_condition"),
            "visibility_haze": bool(_context_value(visual_context, "visibility_haze")),
            "actual_precipitation": bool(_context_value(visual_context, "actual_precipitation")),
            "strong_wind": bool(_context_value(visual_context, "strong_wind")),
            "severe_wind": bool(_context_value(visual_context, "severe_wind")),
            "explicit_storm": bool(_context_value(visual_context, "explicit_storm")),
            "sea_state_hint": _context_value(visual_context, "sea_state_hint"),
        },
    }


def _unavailable(reason: str, *, model: str = "", error_type: str = "") -> ProviderImageQAVerdict:
    return ProviderImageQAVerdict(
        status="unavailable",
        accepted=False,
        reason=reason,
        checks={},
        model=model,
        error_type=error_type,
    )


def _structured_verdict(payload: object, *, model: str = "") -> ProviderImageQAVerdict:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return _unavailable("invalid_structured_response", model=model, error_type="JSONDecodeError")
    if not isinstance(payload, Mapping):
        return _unavailable("invalid_structured_response", model=model, error_type="TypeError")
    checks = payload.get("checks")
    if not isinstance(checks, Mapping) or set(checks) != set(QA_CHECK_KEYS):
        return _unavailable("invalid_structured_response", model=model, error_type="SchemaError")
    normalized: dict[str, bool] = {}
    for key in QA_CHECK_KEYS:
        value = checks.get(key)
        if type(value) is not bool:
            return _unavailable("invalid_structured_response", model=model, error_type="SchemaError")
        normalized[key] = value

    failures: list[str] = []
    if not normalized["scene_family_present_and_dominant"]:
        failures.append("requested_scene_not_dominant")
    if not normalized["composition_present_and_dominant"]:
        failures.append("required_composition_not_dominant")
    if not normalized["cyprus_mediterranean_character"]:
        failures.append("cyprus_mediterranean_character_missing")
    if normalized["tropical_or_jungle_mismatch"]:
        failures.append("tropical_jungle_mismatch")
    if normalized["major_landscape_or_architecture_malformed"]:
        failures.append("malformed_major_structure")
    if not normalized["weather_compatible"]:
        failures.append("weather_mismatch")
    if normalized["visible_text_logo_or_watermark"]:
        failures.append("visible_text_logo_watermark")
    if normalized["screenshot_or_ui"]:
        failures.append("screenshot_ui")
    if not normalized["photographic_editorial_realism"]:
        failures.append("non_photographic_or_fantasy")

    if failures:
        return ProviderImageQAVerdict(
            status="rejected",
            accepted=False,
            reason=failures[0],
            checks=normalized,
            model=model,
        )
    return ProviderImageQAVerdict(
        status="accepted",
        accepted=True,
        reason="accepted",
        checks=normalized,
        model=model,
    )



def _provider_image_qa_primary_model() -> str:
    return (
        (os.getenv("CY_PROVIDER_IMAGE_QA_MODEL") or "").strip()
        or (os.getenv("GEMINI_MODEL") or "").strip()
        or DEFAULT_QA_MODEL
    )


def _provider_image_qa_availability_fallback_model(primary_model: str) -> str:
    """Fallback only for the canonical 3.7 primary; explicit non-default models stay single-model."""
    return (
        DEFAULT_QA_AVAILABILITY_FALLBACK_MODEL
        if str(primary_model or "").strip() == DEFAULT_QA_MODEL
        else ""
    )


def _exception_status_code(exc: Exception) -> int | None:
    raw = getattr(exc, "status_code", None)
    if raw is None:
        raw = getattr(getattr(exc, "response", None), "status_code", None)
    try:
        return int(raw) if raw is not None else None
    except (TypeError, ValueError):
        return None


def _is_retryable_model_availability_failure(exc: Exception) -> bool:
    status_code = _exception_status_code(exc)
    if status_code in _RETRYABLE_QA_AVAILABILITY_STATUS_CODES:
        return True
    message = str(exc).casefold()
    return any(
        token in message
        for token in (
            "503",
            "service unavailable",
            "temporarily unavailable",
            "high demand",
            "overloaded",
        )
    )


def _gemini_structured_evaluator(
    image_path: Path,
    request: Mapping[str, Any],
    *,
    model: str | None = None,
) -> Mapping[str, Any]:
    api_key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("GEMINI_API_KEY is not configured")
    try:
        from openai import OpenAI  # type: ignore
    except Exception as exc:
        raise RuntimeError("OpenAI SDK is unavailable") from exc

    model = str(model or _provider_image_qa_primary_model()).strip()
    mime_type = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    encoded = base64.b64encode(image_path.read_bytes()).decode("ascii")
    data_url = f"data:{mime_type};base64,{encoded}"

    schema_example = {
        "checks": {
            "scene_family_present_and_dominant": True,
            "composition_present_and_dominant": True,
            "cyprus_mediterranean_character": True,
            "tropical_or_jungle_mismatch": False,
            "major_landscape_or_architecture_malformed": False,
            "weather_compatible": True,
            "visible_text_logo_or_watermark": False,
            "screenshot_or_ui": False,
            "photographic_editorial_realism": True,
        }
    }
    instructions = (
        "Inspect this weather editorial image only for the requested semantic contract. "
        "Do not score beauty or aesthetics. Return JSON only, with exactly the schema shown. "
        "Every value in checks must be a JSON boolean. Scene family and composition must be "
        "present and visually dominant, not merely possible. Cyprus/Mediterranean character "
        "must be geographically plausible. Reject incompatible tropical/jungle character, "
        "major malformed landscape/architecture, weather contradiction, visible text/logo/"
        "watermark, screenshot/UI, illustration/fantasy, or failure of photographic editorial realism.\n"
        f"REQUEST={json.dumps(dict(request), ensure_ascii=False, sort_keys=True)}\n"
        f"SCHEMA={json.dumps(schema_example, ensure_ascii=False, sort_keys=True)}"
    )
    client = OpenAI(
        api_key=api_key,
        base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
        timeout=25.0,
        max_retries=0,
    )
    call: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": instructions},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            }
        ],
        "max_tokens": 500,
    }
    if not model.startswith("gemini-3"):
        call["temperature"] = 0
    response = client.chat.completions.create(**call)
    content = response.choices[0].message.content
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("Gemini semantic QA returned empty content")
    parsed = json.loads(content)
    if not isinstance(parsed, Mapping):
        raise RuntimeError("Gemini semantic QA returned non-object JSON")
    return parsed



def _evaluate_default_gemini_with_availability_fallback(
    image_path: Path,
    request: Mapping[str, Any],
    *,
    evaluator: Callable[..., object] | None = None,
) -> ProviderImageQAVerdict:
    """Evaluate with one 3.7→2.5 retry only for temporary model availability failures."""
    call_evaluator = evaluator or _gemini_structured_evaluator
    primary_model = _provider_image_qa_primary_model()
    try:
        payload = call_evaluator(image_path, request, model=primary_model)
    except Exception as exc:
        fallback_model = _provider_image_qa_availability_fallback_model(primary_model)
        if not fallback_model or not _is_retryable_model_availability_failure(exc):
            return _unavailable(
                "qa_unavailable",
                model=primary_model,
                error_type=exc.__class__.__name__,
            )
        try:
            fallback_payload = call_evaluator(image_path, request, model=fallback_model)
        except Exception as fallback_exc:
            return _unavailable(
                "qa_unavailable",
                model=fallback_model,
                error_type=fallback_exc.__class__.__name__,
            )
        return _structured_verdict(fallback_payload, model=fallback_model)
    return _structured_verdict(payload, model=primary_model)


def evaluate_cyprus_provider_image_qa(
    image_path: str | Path,
    *,
    selected_scene: str,
    composition: str,
    visual_context: object,
    structured_evaluator: Callable[[Path, Mapping[str, Any]], object] | None = None,
) -> ProviderImageQAVerdict:
    """Return a strict deterministic verdict; QA failure never accepts an image."""
    path = Path(image_path)
    request = build_provider_image_qa_request(
        selected_scene=selected_scene,
        composition=composition,
        visual_context=visual_context,
    )
    if structured_evaluator is None:
        return _evaluate_default_gemini_with_availability_fallback(path, request)

    try:
        payload = structured_evaluator(path, request)
    except Exception as exc:
        return _unavailable(
            "qa_unavailable",
            model="injected",
            error_type=exc.__class__.__name__,
        )
    return _structured_verdict(payload, model="injected")


__all__ = [
    "ProviderImageQAVerdict",
    "QA_CHECK_KEYS",
    "build_provider_image_qa_request",
    "evaluate_cyprus_provider_image_qa",
]
