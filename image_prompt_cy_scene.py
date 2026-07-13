#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a sanitized Cyprus landscape prompt from finalized FORMAT_V2 text."""

from __future__ import annotations

import hashlib
import re
from datetime import date, timedelta

from visual_context_cy import VisualContextCY, parse_visual_context_cy
from visual_rules_cy import SceneCuesCY, apply_visual_rules_cy


CYPRUS_VISUAL_PROMPT_VERSION = "cyprus_visual_v5"

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
    r"\bsunrise\b",
    r"\bdusk\b",
    r"\bgolden\s+hour\b",
    r"\bgolden\b",
    r"\borange\s+horizon\b",
    r"\borange\s+sky\b",
    r"\borange\b",
    r"\bamber(?:\s+light)?\b",
    r"\bwarm\s+horizon\s+glow\b",
    r"\bsun\s+from\s+right\b",
    r"\bright-side\s+(?:sun|light|horizon\s+glow)\b",
    r"\bsunset-like\s+lighting\b",
    r"\blow\s+sun\b",
    r"\bevening\s+warmth\b",
    r"\bcinematic\s+dusk\b",
    r"\bcinematic\s+evening\s+light\b",
)
_FOCAL_OBJECT_PATTERNS = (
    r"\bboats?\b",
    r"\bsails?\b",
    r"\byachts?\b",
    r"\bmasts?\b",
)
_EVENING_TEXT_GUARD = (
    "No visible text anywhere, no tiny white bottom text, no pseudo-caption, "
    "no watermark, no artist signature, no letters, no logo, no brand marks."
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

_CYPRUS_SCENE_FAMILIES = (
    "rocky_cove_overlook",
    "long_sandy_beach",
    "coastal_promenade",
    "small_harbour",
    "open_sea_cliffs",
    "mountain_coast_view",
    "breakwater_coast",
    "protected_bay",
    "windy_exposed_coast",
    "quiet_blue_lagoon",
)
_CY_COASTAL_SCENE_TEMPLATES = {
    "rocky_cove_overlook": {
        "morning": "dominant rocky Paphos coast overlook with limestone shelves, clear blue morning water, rugged shoreline geometry filling the frame",
        "evening": "dominant rocky Paphos coast overlook in blue-hour twilight with limestone shelves, long shadows, and darkening Mediterranean water",
    },
    "long_sandy_beach": {
        "morning": "dominant long sandy Cyprus beach curve in clear daylight, pale sand, low dunes, open sea horizon, and no foreground palms",
        "evening": "dominant long sandy Cyprus beach curve in late-day light, low dunes, long shoreline shadows, and restrained warm horizon glow",
    },
    "coastal_promenade": {
        "morning": "dominant Larnaca seafront promenade in clean morning daylight, broad paved edge, low sea wall, and open water as the main structure",
        "evening": "dominant Larnaca seafront promenade in late twilight, broad paved edge, low sea wall, and water reflections kept realistic",
    },
    "small_harbour": {
        "morning": "dominant small Cyprus harbour in crisp daylight, protected harbour basin, stone quay edge, mooring posts, low waterfront buildings, and open sea beyond",
        "evening": "dominant small Cyprus harbour in restrained twilight, protected harbour basin, stone quay edge, mooring posts, low waterfront buildings, and textured water inside the harbour",
    },
    "open_sea_cliffs": {
        "morning": "dominant Ayia Napa sea caves and open-sea cliffs in daylight, sculpted pale rock arches, turquoise water, and cliff-shadow detail",
        "evening": "dominant Ayia Napa sea caves and open-sea cliffs in evening blue hour, sculpted pale rock arches, textured water, and long cliff shadows",
    },
    "mountain_coast_view": {
        "morning": "dominant sea-view from a Cyprus hillside in clear early daylight, terraced stone foreground, mountain-to-coast depth, and wide coastal drop",
        "evening": "dominant sea-view from a Cyprus hillside in late twilight, terraced stone foreground, layered coast below, and residual right-side horizon glow",
    },
    "breakwater_coast": {
        "morning": "dominant breakwater and coastal road viewpoint in daylight, angular stone blocks, dry promenade edge, guardrail, rocky slope, and sea beyond",
        "evening": "dominant breakwater and coastal road viewpoint near dusk, angular stone blocks, dry promenade edge, rocky slope, and glowing sea beyond",
    },
    "protected_bay": {
        "morning": "dominant protected Cyprus bay in fresh daylight, curved shoreline, shallow turquoise water, sheltered rocks, and realistic local vegetation",
        "evening": "dominant protected Cyprus bay in blue-hour twilight, curved shoreline, sheltered rocks, small ripples, and quiet residual horizon glow",
    },
    "windy_exposed_coast": {
        "morning": "dominant exposed Cyprus coast in clear daylight, open horizon, dry rocks, wind-shaped coastal grass, and visibly textured sea surface",
        "evening": "dominant exposed Cyprus coast in late twilight, open horizon, dry rocks, wind-shaped coastal grass, and stronger textured water",
    },
    "quiet_blue_lagoon": {
        "morning": "dominant open sea horizon and quiet blue lagoon with local stone architecture in the foreground, pale walls, clean morning sky, and natural depth",
        "evening": "dominant open sea horizon and quiet blue lagoon with local stone architecture in the foreground, pale walls, late twilight, and natural depth",
    },
}
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
    "aerial or raised viewpoint",
    "eye-level coast view",
    "wide panorama composition",
    "closer foreground rocks composition",
    "open horizon composition",
    "promenade or harbour foreground composition",
    "beach curve composition",
    "cliffs without foreground palms composition",
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


def _date_shift(date_key: str, days: int) -> str | None:
    try:
        value = date.fromisoformat(date_key)
    except ValueError:
        return None
    return (value + timedelta(days=days)).isoformat()


def _stable_index(seed: str, dimension: str, modulo: int) -> int:
    digest = hashlib.sha256(f"{seed}|{dimension}".encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % modulo


def _stable_variant(seed: str, dimension: str, options: tuple[str, ...]) -> str:
    return options[_stable_index(seed, dimension, len(options))]


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


def _scene_seed(date_key: str, post_type: str, weather_main: str) -> str:
    return "|".join([date_key, post_type, weather_main, "cyprus_scene_family"])


def _base_scene_index(date_key: str, post_type: str, weather_main: str) -> int:
    return _stable_index(
        _scene_seed(date_key, post_type, weather_main),
        "scene",
        len(_CYPRUS_SCENE_FAMILIES),
    )


def _scene_index_without_previous(
    date_key: str,
    post_type: str,
    weather_main: str,
    *,
    variation_attempt: int = 0,
) -> int:
    count = len(_CYPRUS_SCENE_FAMILIES)
    idx = (_base_scene_index(date_key, post_type, weather_main) + variation_attempt) % count
    if post_type == "evening":
        morning_idx = _base_scene_index(date_key, "morning", weather_main)
        for _ in range(count):
            if idx != morning_idx:
                break
            idx = (idx + 1) % count
    return idx


def _select_scene_family(
    date_key: str,
    post_type: str,
    weather_main: str,
    *,
    variation_attempt: int = 0,
) -> str:
    """Pick a deterministic scene family while avoiding obvious repetitions."""
    count = len(_CYPRUS_SCENE_FAMILIES)
    idx = _scene_index_without_previous(
        date_key,
        post_type,
        weather_main,
        variation_attempt=variation_attempt,
    )
    blocked: set[int] = set()

    if post_type == "evening":
        blocked.add(_scene_index_without_previous(date_key, "morning", weather_main))

    previous_date = _date_shift(date_key, -1)
    if previous_date:
        blocked.add(_scene_index_without_previous(previous_date, post_type, weather_main))

    for _ in range(count):
        if idx not in blocked:
            break
        idx = (idx + 1) % count
    return _CYPRUS_SCENE_FAMILIES[idx]


def _select_composition(
    date_key: str,
    post_type: str,
    weather_main: str,
    scene_family: str,
    *,
    variation_attempt: int = 0,
    _history_depth: int = 5,
) -> str:
    count = len(_CY_COASTAL_COMPOSITIONS)
    seed = "|".join([post_type, weather_main, "cyprus_composition"])
    offset = _stable_index(seed, "composition_offset", count)
    try:
        ordinal = date.fromisoformat(date_key).toordinal()
    except ValueError:
        ordinal = _stable_index(date_key, "undated_composition", count)
    idx = (ordinal + offset + variation_attempt) % count
    return _CY_COASTAL_COMPOSITIONS[idx]


def _weather_constrained_scene_family(
    scene_family: str,
    date_key: str,
    post_type: str,
    ctx: VisualContextCY,
    *,
    variation_attempt: int,
) -> str:
    wind_reference = max(
        [value for value in (ctx.wind_max, ctx.gust_max) if isinstance(value, (int, float))],
        default=None,
    )
    if wind_reference is not None and wind_reference >= 12:
        options = ("windy_exposed_coast", "breakwater_coast", "open_sea_cliffs")
    elif ctx.visibility_haze and not ctx.dust_hint:
        options = ("coastal_promenade", "small_harbour")
    elif getattr(ctx, "inland_precipitation", False) or getattr(ctx, "inland_thunder_risk", False):
        options = ("mountain_coast_view", "open_sea_cliffs", "breakwater_coast")
    else:
        return scene_family
    if scene_family in options:
        return scene_family
    seed = "|".join([date_key, post_type, str(ctx.weather_main), "weather_scene_constraint"])
    return options[(_stable_index(seed, "scene", len(options)) + variation_attempt) % len(options)]


def _wind_category(ctx: VisualContextCY) -> str:
    strongest = max(
        [value for value in (ctx.wind_max, ctx.gust_max) if isinstance(value, (int, float))],
        default=None,
    )
    if strongest is None:
        return "unknown"
    if strongest >= 15:
        return "severe_wind"
    if strongest >= 12:
        return "strong_gusts"
    if strongest >= 9:
        return "gusty"
    if strongest >= 6:
        return "windy"
    if strongest >= 3:
        return "breeze"
    return "calm"


def _cloud_haze_category(ctx: VisualContextCY) -> str:
    if ctx.dust_hint:
        return "dust_haze"
    if ctx.visibility_haze:
        return "visibility_haze"
    if getattr(ctx, "inland_precipitation", False) or getattr(ctx, "inland_thunder_risk", False):
        return "inland_cloud_development"
    if ctx.weather_main == "cloudy":
        return "cloudy"
    if ctx.weather_main == "rain":
        return "rain_clouds"
    if ctx.weather_main == "storm":
        return "wind_alert_clouds" if not ctx.actual_precipitation else "storm_rain_clouds"
    if ctx.weather_main in {"clear", "hot"}:
        return "clear"
    return "mixed_or_unknown"


def _moon_cache_fields(message: str, post_type: str) -> tuple[str, str]:
    if post_type != "evening":
        return ("not_applicable", "not_applicable")
    context = _evening_moon_visual_context(message)
    kind = str(context.get("kind") or "none")
    direction = str(context.get("direction") or "")
    phase = f"{direction}_{kind}".strip("_") if kind == "near_full" else kind
    illumination = context.get("illumination")
    if isinstance(illumination, (int, float)):
        illum = f"{illumination:.1f}".rstrip("0").rstrip(".")
    else:
        illum = "unknown"
    return (phase, illum)


def _coastal_visual_variants(
    message: str,
    ctx: VisualContextCY,
    post_type: str,
    *,
    variation_attempt: int,
) -> dict[str, str]:
    date_key = _extract_date_key(message)
    scene_family = _select_scene_family(
        date_key,
        post_type,
        str(ctx.weather_main),
        variation_attempt=variation_attempt,
    )
    scene_family = _weather_constrained_scene_family(
        scene_family,
        date_key,
        post_type,
        ctx,
        variation_attempt=variation_attempt,
    )
    scene_text = _CY_COASTAL_SCENE_TEMPLATES[scene_family][post_type]
    composition = _select_composition(
        date_key,
        post_type,
        str(ctx.weather_main),
        scene_family,
        variation_attempt=variation_attempt,
    )
    seed = _variant_seed(message, ctx, post_type)
    foreground = _stable_variant(
        f"{seed}|{scene_family}|{variation_attempt}",
        "foreground",
        _CY_COASTAL_FOREGROUNDS,
    )
    return {
        "scene_family": scene_family,
        "scene_text": scene_text,
        "foreground": foreground,
        "composition": composition,
    }


def _lunar_illumination_percent(message: str) -> float | None:
    for line in str(message or "").splitlines():
        low = line.lower()
        if not (
            "освещ" in low
            or "полнолу" in low
            or "луна" in low
            or "moon" in low
            or line.strip().startswith(("🌑", "🌒", "🌓", "🌔", "🌕", "🌖", "🌗", "🌘", "🌙", "✨"))
        ):
            continue
        match = re.search(r"\b(\d{1,3}(?:[.,]\d+)?)\s*%", line)
        if not match:
            continue
        try:
            value = float(match.group(1).replace(",", "."))
        except Exception:
            continue
        if 0 <= value <= 100:
            return value
    return None


def _has_full_moon_evening_context(message: str) -> bool:
    text = str(message or "")
    if re.search(r"полнолу", text, flags=re.I):
        return True
    if re.search(r"\b100\s*%\s*освещ", text, flags=re.I):
        return True
    illumination = _lunar_illumination_percent(text)
    return illumination is not None and illumination >= 95


def _moon_phase_direction(message: str) -> str:
    text = str(message or "").lower()
    if re.search(r"убыва|waning", text, flags=re.I):
        return "waning"
    if re.search(r"растущ|waxing", text, flags=re.I):
        return "waxing"
    return "waxing"


def _evening_moon_visual_context(message: str) -> dict[str, object]:
    text = str(message or "")
    illumination = _lunar_illumination_percent(text)
    has_full_word = bool(re.search(r"полнолу|full moon", text, flags=re.I))
    if illumination is not None:
        if 90 <= illumination < 97:
            return {
                "kind": "near_full",
                "illumination": illumination,
                "direction": _moon_phase_direction(text),
            }
        if illumination >= 97:
            return {"kind": "full", "illumination": illumination}
    if has_full_word or re.search(r"\b100\s*%\s*освещ", text, flags=re.I):
        return {"kind": "full", "illumination": illumination}
    return {"kind": "", "illumination": illumination}


def _controlled_variety(
    message: str,
    ctx: VisualContextCY,
    post_type: str,
    *,
    variation_attempt: int = 0,
) -> list[str]:
    seed = _variant_seed(message, ctx, post_type)
    inland_only = ctx.inland_heat_focus and not ctx.coastal_focus
    if inland_only:
        scenes = _CY_INLAND_SCENES
        foregrounds = _CY_INLAND_FOREGROUNDS
        compositions = _CY_INLAND_COMPOSITIONS
        scene_text = _stable_variant(seed, "scene", scenes)
        foreground = _stable_variant(seed, "foreground", foregrounds)
        composition = _stable_variant(seed, "composition", compositions)
        scene_family = "inland_urban_heat"
    else:
        variants = _coastal_visual_variants(
            message,
            ctx,
            post_type,
            variation_attempt=variation_attempt,
        )
        scene_text = variants["scene_text"]
        foreground = variants["foreground"]
        composition = variants["composition"]
        scene_family = variants["scene_family"]
    parts = [
        "dominant Cyprus scene family: " + scene_family,
        "dominant macro scene variant: " + scene_text,
        "controlled foreground variant: " + foreground,
        "controlled composition variant: " + composition,
        "avoid repeating previous postcard composition, avoid foreground palms as the main subject, avoid identical centered bay curve, avoid cliff walls on both sides",
    ]
    if scene_family == "small_harbour":
        parts.append(
            "small harbour adherence: protected harbour basin, harbour edge as main motif, mooring posts, low coastal human structure, not a generic cliff bay"
        )
    return parts


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
        coastal_found = [name for name in found if name in {"Paphos", "Larnaca", "Limassol", "Ayia Napa"}]
        if len(coastal_found) > 1:
            return "Cyprus Mediterranean coast with local stone architecture and varied shoreline"
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
        if scene.diagnostics.get("inland_unsettled_rule"):
            cues.extend(
                [
                    "distant inland cloud development toward the Troodos mountains",
                    "convective cloud build-up toward Troodos/inland",
                    "towering cumulus over inland hills",
                    "cloud towers over inland hills while the coastal foreground remains dry",
                    "clearer warm Cyprus coast with weather building inland",
                    "no whole-coast storm scene",
                    "no perfect tourist calm",
                    "no ideal postcard sunset scene",
                    "dry coastal surfaces, not a rainy shoreline",
                ]
            )
        if scene.diagnostics.get("wind_rule"):
            cues.extend(
                [
                    "visible wind response in palm fronds and coastal grass",
                    "coastal vegetation visibly leaning in gusts",
                    "wind-ruffled sea with uneven texture",
                    "textured Mediterranean water surface",
                    "small wind-driven ripples",
                    "no mirror-flat water",
                    "no perfect tourist calm",
                    "no completely still vegetation",
                ]
            )
            if ctx.gust_max is not None and ctx.gust_max >= 12:
                cues.append("occasional small whitecaps, not storm-scale")
            if scene.diagnostics.get("severe_wind_rule"):
                cues.extend(
                    [
                        "strong dry coastal wind response",
                        "frequent small whitecaps on textured Mediterranean water",
                        "visibly bent palm fronds and coastal grass",
                        "dry promenade and dry coastal surfaces",
                    ]
                )

    if scene.diagnostics.get("dust_rule"):
        cues.append("dust haze with muted beige-gold atmospheric depth")
    if scene.diagnostics.get("hot_rule"):
        cues.append("visible heat shimmer above sun-warmed stone and dry air")
        if ctx.coastal_focus and ctx.post_type == "evening":
            cues.append("clear hot Cyprus evening air")
    if ctx.post_type == "morning" and ctx.uv_level in {"high", "extreme"}:
        cues.append("strong direct sunlight with crisp daylight contrast")
    if ctx.humidity_hint in {"high", "present"}:
        cues.append("soft humid sea haze along the coast")
    if (
        ctx.coastal_focus
        and ctx.sea_state_hint == "calm"
        and not scene.diagnostics.get("wind_rule")
        and not scene.diagnostics.get("wet_rule")
        and not scene.diagnostics.get("inland_unsettled_rule")
    ):
        cues.append("calm warm sea surface")

    return cues


def _selected_scene_family(
    message: str,
    ctx: VisualContextCY,
    post_type: str,
    *,
    variation_attempt: int,
) -> str:
    if ctx.inland_heat_focus and not ctx.coastal_focus:
        return "inland_urban_heat"
    return _coastal_visual_variants(
        message,
        ctx,
        post_type,
        variation_attempt=variation_attempt,
    )["scene_family"]


def _visual_cache_metadata(
    message: str,
    ctx: VisualContextCY,
    post_type: str,
    *,
    variation_attempt: int,
) -> dict[str, str]:
    forecast_date = _extract_date_key(message)
    selected_scene = _selected_scene_family(
        message,
        ctx,
        post_type,
        variation_attempt=variation_attempt,
    )
    lunar_phase, lunar_illumination = _moon_cache_fields(message, post_type)
    metadata = {
        "forecast_date": forecast_date,
        "post_type": post_type,
        "target_date": "today" if post_type == "morning" else "tomorrow",
        "prompt_version": CYPRUS_VISUAL_PROMPT_VERSION,
        "selected_scene": selected_scene,
        "composition": _coastal_visual_variants(
            message,
            ctx,
            post_type,
            variation_attempt=variation_attempt,
        )["composition"] if not (ctx.inland_heat_focus and not ctx.coastal_focus) else "inland_composition",
        "weather_scenario": str(ctx.weather_main),
        "wind_gust_category": _wind_category(ctx),
        "cloud_haze_category": _cloud_haze_category(ctx),
        "lunar_phase": lunar_phase,
        "lunar_illumination": lunar_illumination,
        "variation_attempt": str(variation_attempt),
        "region": "cyprus",
    }
    ordered = (
        "region",
        "forecast_date",
        "target_date",
        "post_type",
        "prompt_version",
        "selected_scene",
        "composition",
        "weather_scenario",
        "wind_gust_category",
        "cloud_haze_category",
        "lunar_phase",
        "lunar_illumination",
        "variation_attempt",
    )
    metadata["cache_key"] = "|".join(f"{key}={metadata[key]}" for key in ordered)
    metadata["cache_digest"] = hashlib.sha256(metadata["cache_key"].encode("utf-8")).hexdigest()[:12]
    return metadata


def build_cyprus_visual_cache_key(
    final_format_v2_message: str,
    *,
    post_type: str = "evening",
    variation_attempt: int = 0,
) -> str:
    mode = post_type.strip().lower()
    if mode not in {"morning", "evening"}:
        raise ValueError("post_type must be 'morning' or 'evening'")
    ctx = build_visual_context_cy(final_format_v2_message, post_type=mode)
    return _visual_cache_metadata(
        final_format_v2_message,
        ctx,
        mode,
        variation_attempt=variation_attempt,
    )["cache_key"]


_GLOBAL_PHOTOREALISM_GUARD = (
    "Photorealistic natural coastal photography; realistic Mediterranean vegetation; "
    "natural atmospheric perspective; realistic sea texture; no painting; no illustration; "
    "no digital art; no watercolor; no poster; no fantasy landscape; no text; no watermark; no logo."
)
_MORNING_LIGHT_GUARD = (
    "Morning-only constraints: fresh neutral daylight, pale blue sky, primary light from the left, "
    "no visible sun disk by default, no sunset, no golden hour, no orange horizon, no amber wash, "
    "no low sun on the right, no evening glow, no dusk, no heavy cinematic sunset grading."
)


def build_cyprus_scene_prompt_with_metadata(
    final_format_v2_message: str,
    *,
    post_type: str = "evening",
    variation_attempt: int = 0,
) -> tuple[str, str, dict[str, str]]:
    """Return a sanitized Cyprus prompt, stable style name, and visual cache metadata."""
    mode = post_type.strip().lower()
    if mode not in {"morning", "evening"}:
        raise ValueError("post_type must be 'morning' or 'evening'")

    ctx = build_visual_context_cy(final_format_v2_message, post_type=mode)
    scene = apply_visual_rules_cy(ctx)
    moon_context = _evening_moon_visual_context(final_format_v2_message) if mode == "evening" else {}
    full_moon_evening = moon_context.get("kind") == "full"
    near_full_moon_evening = moon_context.get("kind") == "near_full"
    metadata = _visual_cache_metadata(
        final_format_v2_message,
        ctx,
        mode,
        variation_attempt=variation_attempt,
    )

    if mode == "morning":
        time_cue = (
            "fresh morning daylight, clear early morning daylight, pale blue sky, neutral daylight, "
            "cool fresh morning atmosphere, crisp visibility, crisp daytime visibility, "
            "soft natural light from the left side of frame, soft neutral sunlight from the left side of frame, "
            "sun from left, light direction from left, no visible sun disk, natural daytime shadows, "
            "no bright illumination from the right side of frame, no warm low-angle glow"
        )
    elif near_full_moon_evening:
        direction = str(moon_context.get("direction") or "waxing")
        time_cue = (
            f"Mediterranean blue-hour or late twilight, visible realistic {direction} gibbous Moon "
            "above the sea, residual warm horizon glow on the right side of frame, "
            "small-to-medium natural moon scale"
        )
    elif full_moon_evening:
        time_cue = (
            "Mediterranean blue-hour twilight, visible realistic full moon above the sea, "
            "soft moonlit water, residual warm horizon glow on the right side of frame, "
            "natural moonrise balance"
        )
    else:
        time_cue = (
            "Mediterranean late-day atmosphere with restrained twilight color, "
            "subtle residual horizon glow that does not have to sit on the right side, "
            "not a default postcard golden sunset, no mandatory visible sun disk"
        )
    foundation = _COASTAL_FOUNDATION if ctx.coastal_focus or not ctx.inland_heat_focus else _INLAND_FOUNDATION
    prompt_parts = [
        *foundation,
        _location_cue(final_format_v2_message, ctx),
        time_cue,
        *_weather_cues(ctx, scene),
        *_controlled_variety(
            final_format_v2_message,
            ctx,
            mode,
            variation_attempt=variation_attempt,
        ),
    ]
    if ctx.coastal_focus or not ctx.inland_heat_focus:
        prompt_parts.extend(
            [
                "palms may appear only as small background accents",
            ]
        )
    if full_moon_evening:
        prompt_parts.extend(
            [
                "moonrise blue-hour emphasis dominates over late-day warmth",
                "visible realistic full moon if clouds allow",
                "subtle moonlit reflection on Mediterranean water",
                "realistic moon scale and natural position, not oversized moon",
                "not a sun-dominant scene",
                "no bright golden sunset",
                "no oversized moon",
                "no fantasy planet",
                "no fantasy supermoon",
            ]
        )
    elif near_full_moon_evening:
        direction = str(moon_context.get("direction") or "waxing")
        prompt_parts.extend(
            [
                f"realistic {direction} gibbous Moon at small-to-medium natural scale",
                "blue-hour or late twilight moon context",
                "residual right-side horizon glow",
                "not a sun-dominant scene",
                "no bright golden sunset",
                "avoid exact circular full-moon disk",
                "natural-scale moon only",
                "no surreal lunar scale",
                "no perfect full moon",
                "no oversized moon",
                "no fantasy supermoon",
            ]
        )
    prompt = sanitize_cyprus_scene_prompt(
        "; ".join(part for part in prompt_parts if part),
        post_type=mode,
    )
    if near_full_moon_evening:
        direction = str(moon_context.get("direction") or "waxing")
        illumination = moon_context.get("illumination")
        if isinstance(illumination, (int, float)):
            pct = f"{illumination:.0f}" if float(illumination).is_integer() else f"{illumination:.1f}"
            near_full_cue = f"realistic {direction} gibbous Moon, {pct}% illuminated"
        else:
            near_full_cue = f"realistic {direction} gibbous Moon"
        prompt = (
            prompt.rstrip(" .;")
            + ". "
            + "; ".join(
                [
                    near_full_cue,
                    "blue-hour or late twilight",
                    "residual right-side horizon glow",
                    "small-to-medium natural moon scale",
                    "avoid exact circular full-moon disk",
                    "natural-scale moon only",
                    "no surreal lunar scale",
                    "no perfect full moon",
                    "no oversized moon",
                    "no fantasy supermoon",
                ]
            )
        )
    if mode == "evening" and _EVENING_TEXT_GUARD.lower() not in prompt.lower():
        prompt = prompt.rstrip(" .;") + ". " + _EVENING_TEXT_GUARD
    if _GLOBAL_PHOTOREALISM_GUARD.lower() not in prompt.lower():
        prompt = prompt.rstrip(" .;") + ". " + _GLOBAL_PHOTOREALISM_GUARD
    if mode == "morning" and "morning-only constraints" not in prompt.lower():
        prompt = prompt.rstrip(" .;") + " " + _MORNING_LIGHT_GUARD
    style_digest = hashlib.sha256(
        f"{metadata['cache_key']}|{prompt}".encode("utf-8")
    ).hexdigest()[:8]
    style_name = f"cyprus_{mode}_mediterranean_landscape_{style_digest}"
    metadata["style_name"] = style_name
    return prompt, style_name, metadata


def build_cyprus_scene_prompt(
    final_format_v2_message: str,
    *,
    post_type: str = "evening",
    variation_attempt: int = 0,
) -> tuple[str, str]:
    """Return a sanitized positive Cyprus landscape prompt and stable style name."""
    prompt, style_name, _metadata = build_cyprus_scene_prompt_with_metadata(
        final_format_v2_message,
        post_type=post_type,
        variation_attempt=variation_attempt,
    )
    return prompt, style_name


__all__ = [
    "build_visual_context_cy",
    "sanitize_cyprus_scene_prompt",
    "build_cyprus_visual_cache_key",
    "build_cyprus_scene_prompt",
    "build_cyprus_scene_prompt_with_metadata",
    "CYPRUS_VISUAL_PROMPT_VERSION",
]
