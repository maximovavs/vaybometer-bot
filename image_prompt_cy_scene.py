#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a sanitized Cyprus landscape prompt from finalized FORMAT_V2 text."""

from __future__ import annotations

import re

from visual_context_cy import VisualContextCY, parse_visual_context_cy
from visual_rules_cy import SceneCuesCY, apply_visual_rules_cy


_GENERAL_TRIGGER_PATTERNS = (
    r"\bweather\s+card\b",
    r"\btext\b",
    r"\bcaption\b",
    r"\blabel\b",
    r"\blogo\b",
    r"\bwatermark\b",
    r"\bnumbers?\b",
    r"\bui\b",
    r"\bposter\b",
    r"\blayout\b",
    r"\bpanel\b",
    r"\binfographic\b",
    r"\bcard\b",
    r"\bbaltic\b",
    r"\bkaliningrad\b",
    r"\bkld\b",
)
_MORNING_TRIGGER_PATTERNS = (
    r"\bmoon\b",
    r"\blunar\b",
    r"\bcrescent\b",
    r"\bnight\b",
    r"\bevening\b",
    r"\bsunset\b",
)
_FOCAL_OBJECT_PATTERNS = (
    r"\bboats?\b",
    r"\bsails?\b",
    r"\byachts?\b",
    r"\bmasts?\b",
)

_CITY_PATTERNS = (
    ("Paphos", (r"\bpaphos\b", r"\bpafos\b", r"пафос")),
    ("Nicosia", (r"\bnicosia\b", r"никос")),
    ("Limassol", (r"\blimassol\b", r"лимассол")),
    ("Larnaca", (r"\blarnaca\b", r"ларнак")),
    ("Ayia Napa", (r"\bayia[\s-]+napa\b", r"айя[\s-]+напа")),
)

_SAFE_FOUNDATION = (
    "pure full-frame Mediterranean landscape",
    "natural open sky",
    "uninterrupted sea and coast",
    "clean scenic composition",
    "human-made objects only distant and non-focal",
    "practical weather mood",
)


def build_visual_context_cy(
    final_format_v2_message: str,
    *,
    post_type: str = "evening",
) -> VisualContextCY:
    """Compatibility-named deterministic context step for the scene pipeline."""
    return parse_visual_context_cy(final_format_v2_message, post_type=post_type)


def sanitize_cyprus_scene_prompt(prompt: str, *, post_type: str) -> str:
    """Remove generator trigger vocabulary without adding negative instructions."""
    mode = post_type.strip().lower()
    if mode not in {"morning", "evening"}:
        raise ValueError("post_type must be 'morning' or 'evening'")

    cleaned = str(prompt)
    patterns = list(_GENERAL_TRIGGER_PATTERNS) + list(_FOCAL_OBJECT_PATTERNS)
    if mode == "morning":
        patterns.extend(_MORNING_TRIGGER_PATTERNS)

    for pattern in patterns:
        cleaned = re.sub(pattern, " ", cleaned, flags=re.I)

    cleaned = re.sub(r"\s*;\s*", "; ", cleaned)
    cleaned = re.sub(r"\s*,\s*", ", ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned)
    cleaned = re.sub(r"(?:;\s*){2,}", "; ", cleaned)
    cleaned = re.sub(r"\s+([,.;:])", r"\1", cleaned)
    cleaned = re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", cleaned)
    return cleaned.strip(" ,;:.")


def _location_cue(message: str, ctx: VisualContextCY) -> str:
    low = message.lower()
    found = [
        name
        for name, patterns in _CITY_PATTERNS
        if any(re.search(pattern, low, flags=re.I) for pattern in patterns)
    ]

    if "Nicosia" in found and ctx.inland_heat_focus:
        return "Nicosia inland Cyprus with sun-baked stone streets and shaded urban depth"
    if "Paphos" in found:
        return "Paphos rocky Mediterranean coast as the geographic setting"
    if "Larnaca" in found:
        return "Larnaca seafront promenade with palms and distant marina silhouettes"
    if "Limassol" in found:
        return "Limassol Mediterranean promenade with palms and distant marina silhouettes"
    if "Ayia Napa" in found:
        return "Ayia Napa eastern Cyprus coast with clear Mediterranean shoreline"
    if ctx.inland_heat_focus:
        return "dry inland Cyprus urban setting with Nicosia character"
    return "Cyprus Mediterranean coast with palms and local stone architecture"


def _weather_cues(ctx: VisualContextCY, scene: SceneCuesCY) -> list[str]:
    cues = [
        scene.sky_cue,
        scene.light_cue,
        scene.sea_cue,
        scene.air_cue,
    ]

    if scene.diagnostics.get("wet_rule"):
        cues.extend(
            [
                "wet promenade surfaces",
                "dramatic rain clouds",
                "sheltered pedestrians moving with practical rain awareness",
                "practical rain mood",
            ]
        )
    else:
        cues.append(scene.mood_cue)
        if scene.diagnostics.get("wind_rule"):
            cues.append("visible sea breeze moving palm fronds and lightly rippling the water")

    if scene.diagnostics.get("dust_rule"):
        cues.append("dust haze with muted beige-gold atmospheric depth")
    if scene.diagnostics.get("hot_rule"):
        cues.append("visible heat shimmer above sun-warmed stone and dry air")
    if ctx.uv_level in {"high", "extreme"}:
        cues.append("strong direct sunlight with crisp daylight contrast")
    if ctx.humidity_hint in {"high", "present"}:
        cues.append("soft humid sea haze along the coast")

    return cues


def build_cyprus_scene_prompt(
    final_format_v2_message: str,
    *,
    post_type: str = "evening",
) -> tuple[str, str]:
    """Return a sanitized positive Cyprus landscape prompt and stable style name."""
    mode = post_type.strip().lower()
    if mode not in {"morning", "evening"}:
        raise ValueError("post_type must be 'morning' or 'evening'")

    ctx = build_visual_context_cy(final_format_v2_message, post_type=mode)
    scene = apply_visual_rules_cy(ctx)

    time_cue = (
        "daylight Mediterranean morning with practical weather clarity"
        if mode == "morning"
        else "warm Mediterranean late-day atmosphere with soft golden dusk light"
    )
    prompt_parts = [
        *_SAFE_FOUNDATION,
        _location_cue(final_format_v2_message, ctx),
        time_cue,
        *_weather_cues(ctx, scene),
        "palms and promenade integrated as environmental background",
        "distant marina silhouettes as subtle non-focal background detail",
    ]
    prompt = sanitize_cyprus_scene_prompt(
        "; ".join(part for part in prompt_parts if part),
        post_type=mode,
    )
    style_name = f"cyprus_{mode}_mediterranean_landscape"
    return prompt, style_name


__all__ = [
    "build_visual_context_cy",
    "sanitize_cyprus_scene_prompt",
    "build_cyprus_scene_prompt",
]
