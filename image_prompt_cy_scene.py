#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a sanitized Cyprus landscape prompt from finalized FORMAT_V2 text."""

from __future__ import annotations

import hashlib
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

_COASTAL_FOUNDATION = (
    "pure full-frame Mediterranean landscape",
    "natural open sky",
    "distinct Cyprus coastal geography filling the frame",
    "clean scenic composition",
    "human-made objects only distant and non-focal",
    "practical weather mood",
    "Mediterranean coastal weather mood",
    "local stone, sea, cliffs, marina edges, or coastal roads as the main structure",
    "palms only optional and distant as background accents",
)
_INLAND_FOUNDATION = (
    "pure full-frame inland Cyprus landscape",
    "natural open sky",
    "sun-baked Nicosia urban depth",
    "clean scenic composition",
    "human-made objects only distant and non-focal",
    "practical hot-weather mood",
)

_CY_COASTAL_MORNING_SCENES = (
    "dominant rocky Paphos coast with limestone shelves, clear blue morning water, and rugged shoreline geometry",
    "dominant Larnaca seafront promenade in clean daylight, broad paved edge, low sea wall, and open calm water",
    "dominant Limassol marina edge in crisp daylight, stone quay lines, reflective basin, and distant waterfront shapes",
    "dominant Ayia Napa sea caves in daylight, sculpted pale rock arches, turquoise water, and cliff-shadow detail",
    "dominant sea-view from a Cyprus hillside in clear morning air, terraced stone foreground and wide coastal drop",
    "dominant coastal road viewpoint in daylight, curving asphalt edge, guardrail, rocky slope, and sea beyond",
    "dominant open sea horizon with local stone architecture in the foreground, pale walls and clean morning sky",
)
_CY_COASTAL_EVENING_SCENES = (
    "dominant rocky Paphos coast at dusk with limestone shelves, long shadows, and darkening Mediterranean water",
    "dominant Larnaca seafront promenade in late-day light, broad paved edge, low sea wall, and warm reflections",
    "dominant Limassol marina edge at dusk, stone quay lines, moored silhouettes kept distant, and warm water glow",
    "dominant Ayia Napa sea caves in evening light, sculpted pale rock arches, turquoise water, and long cliff shadows",
    "dominant sea-view from a Cyprus hillside in warm late-day air, terraced stone foreground and layered coast below",
    "dominant coastal road viewpoint near dusk, curving asphalt edge, guardrail, rocky slope, and glowing sea beyond",
    "dominant open sea horizon with local stone architecture in the foreground, pale walls and warm late-day sky",
)
_CY_INLAND_SCENES = (
    "inland urban heat view with shaded stone streets",
    "sun-baked inland urban depth",
    "dry Nicosia street perspective with sparse shade",
)
_CY_COASTAL_FOREGROUNDS = (
    "rough limestone foreground",
    "warm stone foreground",
    "low seawall and paved edge in the foreground",
    "marina stone quay in the foreground",
    "coastal road shoulder and rock cut in the foreground",
    "terraced hillside stone in the foreground",
    "sea surface close texture in the lower frame",
)
_CY_INLAND_FOREGROUNDS = (
    "warm stone foreground",
    "shaded pavement edge in the foreground",
    "dry urban planting in the foreground",
)
_CY_COASTAL_COMPOSITIONS = (
    "open horizon composition",
    "diagonal shoreline composition",
    "cliff-led coastal view",
    "marina-edge perspective",
    "hillside overlook composition",
    "roadside viewpoint composition",
    "layered coast-and-sky composition",
)
_CY_INLAND_COMPOSITIONS = (
    "layered street-and-sky composition",
    "framed inland urban view",
    "diagonal shaded-street composition",
)


def build_visual_context_cy(
    final_format_v2_message: str,
    *,
    post_type: str = "evening",
) -> VisualContextCY:
    """Compatibility-named deterministic context step for the scene pipeline."""
    return parse_visual_context_cy(final_format_v2_message, post_type=post_type)


def _extract_date_key(text: str) -> str:
    value = str(text or "")
    match = re.search(r"\b(\d{2})[./-](\d{2})[./-](\d{4})\b", value)
    if match:
        return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
    match = re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", value)
    return match.group(0) if match else "undated"


def _stable_variant(seed: str, dimension: str, options: tuple[str, ...]) -> str:
    digest = hashlib.sha256(f"{seed}|{dimension}".encode("utf-8")).digest()
    return options[int.from_bytes(digest[:8], "big") % len(options)]


def _variant_seed(message: str, ctx: VisualContextCY, post_type: str) -> str:
    return "|".join(
        [
            _extract_date_key(message),
            post_type,
            str(ctx.weather_main),
            "none",
            "none",
            "cyprus",
        ]
    )


def _controlled_variety(message: str, ctx: VisualContextCY, post_type: str) -> list[str]:
    seed = _variant_seed(message, ctx, post_type)
    inland_only = ctx.inland_heat_focus and not ctx.coastal_focus
    if inland_only:
        scenes = _CY_INLAND_SCENES
        foregrounds = _CY_INLAND_FOREGROUNDS
        compositions = _CY_INLAND_COMPOSITIONS
    else:
        scenes = (
            _CY_COASTAL_MORNING_SCENES
            if post_type == "morning"
            else _CY_COASTAL_EVENING_SCENES
        )
        foregrounds = _CY_COASTAL_FOREGROUNDS
        compositions = _CY_COASTAL_COMPOSITIONS
    return [
        "dominant macro scene variant: " + _stable_variant(seed, "scene", scenes),
        "controlled foreground variant: " + _stable_variant(seed, "foreground", foregrounds),
        "controlled composition variant: " + _stable_variant(seed, "composition", compositions),
    ]


def sanitize_cyprus_scene_prompt(prompt: str, *, post_type: str) -> str:
    """Remove generator trigger vocabulary without adding negative instructions."""
    mode = post_type.strip().lower()
    if mode not in {"morning", "evening"}:
        raise ValueError("post_type must be 'morning' or 'evening'")

    cleaned = re.sub(r"<[^>]*>", " ", str(prompt))
    cleaned = re.sub(r"\bsource\b[^;]*", " ", cleaned, flags=re.I)
    cleaned = re.sub(
        r"[-+]?\d+(?:[.,]\d+)?\s*°\s*[cCfFСс]?",
        " ",
        cleaned,
    )
    cleaned = re.sub(
        r"\b(?:UV|AQI)\s*[-:=]?\s*\d+(?:[.,]\d+)?\b",
        " ",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(
        r"[-+]?\d+(?:[.,]\d+)?\s*(?:m/s|km/h|м/с|км/ч|%)",
        " ",
        cleaned,
        flags=re.I,
    )
    cleaned = re.sub(r"[\u0400-\u04FF]+", " ", cleaned)
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

    if ctx.coastal_focus:
        if "Paphos" in found:
            return "Paphos rocky Mediterranean coast as the geographic setting"
        if "Larnaca" in found:
            return "Larnaca seafront promenade with low seawall, broad paving, and open water"
        if "Limassol" in found:
            return "Limassol marina edge with stone quay, waterfront depth, and open sea nearby"
        if "Ayia Napa" in found:
            return "Ayia Napa sea caves and eastern Cyprus rocky shoreline"
        return "Cyprus Mediterranean coast with local stone architecture and varied shoreline"
    if "Nicosia" in found and ctx.inland_heat_focus:
        return "Nicosia inland Cyprus with sun-baked stone streets and shaded urban depth"
    if ctx.inland_heat_focus:
        return "dry inland Cyprus urban setting with Nicosia character"
    return "Cyprus Mediterranean coast with local stone architecture and varied shoreline"


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
        if ctx.coastal_focus and ctx.post_type == "evening":
            cues.append("clear hot Cyprus evening air")
    if ctx.uv_level in {"high", "extreme"}:
        cues.append("strong direct sunlight with crisp daylight contrast")
    if ctx.humidity_hint in {"high", "present"}:
        cues.append("soft humid sea haze along the coast")
    if ctx.coastal_focus and ctx.sea_state_hint == "calm":
        cues.append("calm warm sea surface")

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
    foundation = _COASTAL_FOUNDATION if ctx.coastal_focus or not ctx.inland_heat_focus else _INLAND_FOUNDATION
    prompt_parts = [
        *foundation,
        _location_cue(final_format_v2_message, ctx),
        time_cue,
        *_weather_cues(ctx, scene),
        *_controlled_variety(final_format_v2_message, ctx, mode),
    ]
    if ctx.coastal_focus or not ctx.inland_heat_focus:
        prompt_parts.extend(
            [
                "palms may appear only as small background accents",
            ]
        )
    prompt = sanitize_cyprus_scene_prompt(
        "; ".join(part for part in prompt_parts if part),
        post_type=mode,
    )
    style_digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:8]
    style_name = f"cyprus_{mode}_mediterranean_landscape_{style_digest}"
    return prompt, style_name


__all__ = [
    "build_visual_context_cy",
    "sanitize_cyprus_scene_prompt",
    "build_cyprus_scene_prompt",
]
