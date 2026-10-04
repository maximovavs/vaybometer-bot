#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Narrow semantic QA for Cyprus provider images.

Runs only after the existing provider technical/content guard and before
dedup/presentation. Local curated fallback images never use this module.
"""
from __future__ import annotations

import base64
from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
import mimetypes
import os
from pathlib import Path
import re
from typing import Any

try:
    from openai import OpenAI  # type: ignore
except Exception:  # pragma: no cover
    OpenAI = None  # type: ignore

REQUIRED_CHECKS = (
    "scene_family_present",
    "scene_family_dominant",
    "composition_present",
    "composition_dominant",
    "cyprus_mediterranean_character",
    "no_incompatible_tropical_jungle",
    "major_landscape_architecture_well_formed",
    "weather_compatible",
    "no_visible_text_logo_watermark",
    "no_screenshot_ui",
    "photographic_editorial_realism",
)

@dataclass(frozen=True)
class ProviderImageQAVerdict:
    accepted: bool
    status: str
    reason: str
    checks: dict[str, bool]
    failed_checks: tuple[str, ...] = ()
    confidence: str = ""
    model: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "status": self.status,
            "reason": self.reason,
            "checks": dict(self.checks),
            "failed_checks": list(self.failed_checks),
            "confidence": self.confidence,
            "model": self.model,
        }

def unavailable_verdict(reason: str = "qa_unavailable") -> ProviderImageQAVerdict:
    safe_reason = re.sub(r"[^a-zA-Z0-9_.:-]+", "_", str(reason or "qa_unavailable"))[:120]
    return ProviderImageQAVerdict(
        accepted=False,
        status="unavailable",
        reason=safe_reason or "qa_unavailable",
        checks={},
    )

def verdict_from_structured(payload: object, *, model: str = "") -> ProviderImageQAVerdict:
    if not isinstance(payload, Mapping):
        return ProviderImageQAVerdict(False, "invalid_response", "structured_verdict_not_object", {}, model=model)
    checks_obj = payload.get("checks")
    if not isinstance(checks_obj, Mapping):
        return ProviderImageQAVerdict(False, "invalid_response", "structured_verdict_missing_checks", {}, model=model)
    checks: dict[str, bool] = {}
    for key in REQUIRED_CHECKS:
        value = checks_obj.get(key)
        if type(value) is not bool:
            return ProviderImageQAVerdict(
                False, "invalid_response", f"structured_verdict_invalid_check:{key}", checks, model=model
            )
        checks[key] = value
    failed = tuple(key for key in REQUIRED_CHECKS if not checks[key])
    accepted = not failed
    return ProviderImageQAVerdict(
        accepted=accepted,
        status="accepted" if accepted else "rejected",
        reason="accepted" if accepted else "semantic_mismatch:" + ",".join(failed),
        checks=checks,
        failed_checks=failed,
        confidence=str(payload.get("confidence") or "").strip()[:32],
        model=model,
    )

def _visual_context_payload(visual_context: object) -> dict[str, Any]:
    fields = (
        "primary_weather", "weather_main", "visibility_condition", "visibility_haze",
        "dust_hint", "actual_precipitation", "coastal_precipitation",
        "inland_precipitation", "strong_wind", "severe_wind", "explicit_storm",
        "scene_focus", "visual_forecast_period",
    )
    out: dict[str, Any] = {}
    for field in fields:
        value = getattr(visual_context, field, None)
        if value is None and isinstance(visual_context, Mapping):
            value = visual_context.get(field)
        if value is not None:
            out[field] = value
    return out

def _contract(*, requested_scene_family: str, requested_composition: str, visual_context: object) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "requested_scene_family": str(requested_scene_family or ""),
        "requested_composition": str(requested_composition or ""),
        "visual_context": _visual_context_payload(visual_context),
        "required_checks": list(REQUIRED_CHECKS),
    }

def _parse_json_text(text: str) -> object:
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json)?\s*", "", value, flags=re.I)
        value = re.sub(r"\s*```$", "", value)
    try:
        return json.loads(value)
    except Exception:
        start, end = value.find("{"), value.rfind("}")
        if start >= 0 and end > start:
            return json.loads(value[start:end + 1])
        raise

def evaluate_provider_image_semantics(
    image_path: str | Path,
    *,
    requested_scene_family: str,
    requested_composition: str,
    visual_context: object,
    structured_response_provider: Callable[[dict[str, Any]], object] | None = None,
) -> ProviderImageQAVerdict:
    """Return a strict structured verdict; every failure is fail-closed."""
    contract = _contract(
        requested_scene_family=requested_scene_family,
        requested_composition=requested_composition,
        visual_context=visual_context,
    )
    if structured_response_provider is not None:
        try:
            return verdict_from_structured(structured_response_provider(contract), model="injected")
        except Exception as exc:
            return unavailable_verdict(f"injected_qa_error:{exc.__class__.__name__}")

    key = (os.getenv("GEMINI_API_KEY") or "").strip()
    if not key or OpenAI is None:
        return unavailable_verdict("gemini_qa_unavailable")

    model = (os.getenv("CY_PROVIDER_IMAGE_QA_MODEL") or os.getenv("GEMINI_MODEL") or "gemini-2.5-flash").strip()
    path = Path(image_path)
    try:
        payload = path.read_bytes()
    except Exception as exc:
        return unavailable_verdict(f"image_read_error:{exc.__class__.__name__}")
    if not payload:
        return unavailable_verdict("image_read_error:empty")

    mime = mimetypes.guess_type(path.name)[0] or "image/jpeg"
    image_url = f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}"
    system = (
        "You are a strict semantic QA gate for a Cyprus weather editorial photograph. "
        "Judge only requested scene/composition, geographic plausibility, factual weather appearance, "
        "malformed major landscape/architecture, visible text/logo/watermark/UI, and photographic realism. "
        "Do not score beauty or aesthetics. Return one JSON object only with confidence and checks; "
        "every required check must be boolean."
    )
    prompt = (
        "Validate this provider image against the following contract. "
        "A check is true only when the image positively satisfies it. "
        "For no_incompatible_tropical_jungle, true means there is no incompatible tropical/jungle mismatch. "
        "For major_landscape_architecture_well_formed, true means no obvious major deformation.\n"
        + json.dumps(contract, ensure_ascii=False, sort_keys=True)
        + "\nReturn exactly: {\"confidence\":\"high|medium|low\",\"checks\":{...all required keys...}}"
    )
    try:
        client = OpenAI(
            api_key=key,
            base_url="https://generativelanguage.googleapis.com/v1beta/openai/",
            timeout=25.0,
            max_retries=0,
        )
        request: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": [
                    {"type": "text", "text": prompt},
                    {"type": "image_url", "image_url": {"url": image_url}},
                ]},
            ],
            "max_tokens": 700,
        }
        if not model.startswith("gemini-3"):
            request["temperature"] = 0
        response = client.chat.completions.create(**request)
        raw = (response.choices[0].message.content or "").strip()
        structured = _parse_json_text(raw)
    except Exception as exc:
        return unavailable_verdict(f"gemini_qa_error:{exc.__class__.__name__}")
    return verdict_from_structured(structured, model=model)

__all__ = [
    "ProviderImageQAVerdict", "REQUIRED_CHECKS", "evaluate_provider_image_semantics",
    "unavailable_verdict", "verdict_from_structured",
]
