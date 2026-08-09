#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build a sanitized Cyprus landscape prompt from finalized FORMAT_V2 text."""

from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Mapping
from urllib.parse import quote_plus

from cyprus_visual_policy import cyprus_scene_macro_family
from visual_context_cy import VisualContextCY, parse_visual_context_cy
from visual_rules_cy import SceneCuesCY, apply_visual_rules_cy


CYPRUS_VISUAL_PROMPT_VERSION = "cyprus_visual_v10"

_PROMPT_TARGET_MIN_CHARS = 450
_PROMPT_TARGET_MAX_CHARS = 1600
_PROMPT_HARD_MAX_CHARS = 2200
_POLLINATIONS_URL_HARD_MAX_CHARS = 3500

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
_CYPRUS_SCENE_FAMILIES = (
    "rocky_cove_overlook",
    "long_sandy_beach",
    "open_beach_horizon",
    "coastal_promenade",
    "marina_walkway",
    "small_harbour",
    "harbour_pier_waterlevel",
    "open_sea_cliffs",
    "mountain_coast_view",
    "breakwater_coast",
    "protected_bay",
    "windy_exposed_coast",
    "quiet_blue_lagoon",
    "coastal_urban_rooftop",
    "salt_lake_landscape",
    "beach_cafe_terrace",
)
_CY_COASTAL_SCENE_TEMPLATES = {
    "rocky_cove_overlook": {
        "morning": "Dominant rocky Paphos coast overlook with limestone shelves and rugged open shoreline, human-made elements distant and non-focal",
        "evening": "Dominant rocky Paphos coast overlook with limestone shelves and rugged open shoreline, human-made elements distant and non-focal",
    },
    "long_sandy_beach": {
        "morning": "Dominant long sandy Cyprus beach with pale sand, low dunes and an open sea horizon, human-made elements distant and non-focal",
        "evening": "Dominant long sandy Cyprus beach with pale sand, low dunes and an open sea horizon, human-made elements distant and non-focal",
    },
    "open_beach_horizon": {
        "morning": "Dominant eye-level open Cyprus beach with pale sand, a straight sea horizon and no enclosing headlands, human-made elements distant and non-focal",
        "evening": "Dominant eye-level open Cyprus beach with pale sand, a straight sea horizon and no enclosing headlands, human-made elements distant and non-focal",
    },
    "coastal_promenade": {
        "morning": "Dominant Larnaca seafront promenade with broad paving and a low seawall beside open water",
        "evening": "Dominant Larnaca seafront promenade with broad paving and a low seawall beside open water",
    },
    "marina_walkway": {
        "morning": "Dominant Limassol marina walkway with stone paving, railings and mooring details beside open water",
        "evening": "Dominant Limassol marina walkway with stone paving, railings and mooring details beside open water",
    },
    "small_harbour": {
        "morning": "Dominant small Cyprus harbour with a linear stone harbour basin and quay, mooring posts and low waterfront buildings",
        "evening": "Dominant small Cyprus harbour with a linear stone harbour basin and quay, mooring posts and low waterfront buildings",
    },
    "harbour_pier_waterlevel": {
        "morning": "Dominant Cyprus harbour pier at water level with a linear stone edge, mooring posts and an open horizon",
        "evening": "Dominant Cyprus harbour pier at water level with a linear stone edge, mooring posts and an open horizon",
    },
    "open_sea_cliffs": {
        "morning": "Dominant Ayia Napa sea-cave coast with sculpted pale rock arches and open Mediterranean water, human-made elements distant and non-focal",
        "evening": "Dominant Ayia Napa sea-cave coast with sculpted pale rock arches and open Mediterranean water, human-made elements distant and non-focal",
    },
    "mountain_coast_view": {
        "morning": "Dominant Cyprus hillside sea view with terraced stone, layered mountain-to-coast depth and wide open water, human-made elements distant and non-focal",
        "evening": "Dominant Cyprus hillside sea view with terraced stone, layered mountain-to-coast depth and wide open water, human-made elements distant and non-focal",
    },
    "breakwater_coast": {
        "morning": "Dominant coastal road viewpoint with angular breakwater stone, a dry road edge and sea beyond",
        "evening": "Dominant coastal road viewpoint with angular breakwater stone, a dry road edge and sea beyond",
    },
    "protected_bay": {
        "morning": "Dominant protected Cyprus bay with a sheltered natural shoreline and shallow water, human-made elements distant and non-focal",
        "evening": "Dominant protected Cyprus bay with a sheltered natural shoreline and shallow water, human-made elements distant and non-focal",
    },
    "windy_exposed_coast": {
        "morning": "Dominant exposed Cyprus coast with an open horizon, dry rocks and wind-shaped coastal grass, human-made elements distant and non-focal",
        "evening": "Dominant exposed Cyprus coast with an open horizon, dry rocks and wind-shaped coastal grass, human-made elements distant and non-focal",
    },
    "quiet_blue_lagoon": {
        "morning": "Dominant quiet Cyprus blue lagoon with an open sea horizon and pale local stone in the foreground",
        "evening": "Dominant quiet Cyprus blue lagoon with an open sea horizon and pale local stone in the foreground",
    },
    "coastal_urban_rooftop": {
        "morning": "Dominant Cyprus coastal rooftop with low pale buildings, practical urban depth and a distant strip of open sea",
        "evening": "Dominant Cyprus coastal rooftop with low pale buildings, practical urban depth and a distant strip of open sea",
    },
    "salt_lake_landscape": {
        "morning": "Dominant flat Cyprus salt-lake landscape with low reeds, a pale mineral shore and wide sky, human-made elements distant and non-focal",
        "evening": "Dominant flat Cyprus salt-lake landscape with low reeds, a pale mineral shore and wide sky, human-made elements distant and non-focal",
    },
    "beach_cafe_terrace": {
        "morning": "Dominant street-level Cyprus beach cafe terrace with simple shaded tables, a low railing and a straight open-sea horizon",
        "evening": "Dominant street-level Cyprus beach cafe terrace with simple tables, a low railing and a straight open-sea horizon",
    },
}
_CY_INLAND_SCENES = (
    "Nicosia urban rooftop under the forecast sky with low pale buildings and practical city depth",
    "Troodos mountain landscape with dry pine slopes, layered ridges, and a wide forecast sky",
    "traditional inland Cyprus village landscape with stone houses and narrow shaded lanes",
    "dry inland Cyprus landscape with ochre fields, sparse scrub, and low rolling terrain",
)
_CY_INLAND_SCENE_PROMPTS = {
    "inland_urban_rooftop": "Dominant Nicosia urban rooftop with low pale buildings and practical inland city depth",
    "troodos_landscape": "Dominant Troodos mountain landscape with dry pine slopes and layered ridges, human-made elements distant and non-focal",
    "inland_village": "Dominant traditional inland Cyprus village with stone houses and narrow shaded lanes",
    "dry_inland_landscape": "Dominant dry inland Cyprus landscape with ochre fields, sparse scrub and low rolling terrain, human-made elements distant and non-focal",
}
_CY_SCENE_COMPOSITIONS = {
    "rocky_cove_overlook": (
        "Raised natural overlook with limestone foreground and open-shore depth",
        "Diagonal rocky-coast composition led by limestone shelves",
    ),
    "long_sandy_beach": (
        "Eye-level shoreline composition along the open beach",
        "Low beach composition led by sand, dunes and a straight horizon",
    ),
    "open_beach_horizon": (
        "Eye-level composition facing the straight open-sea horizon",
        "Low shoreline composition with uninterrupted beach depth",
    ),
    "coastal_promenade": (
        "Street-level promenade composition led by paving and seawall",
        "Linear seafront composition with the promenade as the main structure",
    ),
    "marina_walkway": (
        "Close street-level marina-walkway composition led by quay details",
        "Linear marina composition along paving, railings and water",
    ),
    "small_harbour": (
        "Water-level linear harbour composition led by the stone basin and quay",
        "Quayside composition following the harbour edge and low buildings",
    ),
    "harbour_pier_waterlevel": (
        "Water-level pier composition along the linear stone edge",
        "Low harbour-pier viewpoint facing the open horizon",
    ),
    "open_sea_cliffs": (
        "Cliff-edge composition with pale rock arches framing open water",
        "Low sea-cave viewpoint led by sculpted rock and open water",
    ),
    "mountain_coast_view": (
        "Hillside overlook composition with terraced foreground and coast below",
        "Layered highland-to-sea composition led by terraced stone",
    ),
    "breakwater_coast": (
        "Road-edge viewpoint led by angular breakwater stone",
        "Linear coastal-road composition with dry stone and sea beyond",
    ),
    "protected_bay": (
        "Shore-level composition following the sheltered natural shoreline",
        "Low sheltered-water composition led by rocks and shallow water",
    ),
    "windy_exposed_coast": (
        "Eye-level exposed-shore composition with an open horizon",
        "Low rocky-coast composition shaped by wind and open water",
    ),
    "quiet_blue_lagoon": (
        "Shore-level open-horizon composition with pale stone foreground",
        "Low lagoon-edge composition facing the open sea horizon",
    ),
    "coastal_urban_rooftop": (
        "Rooftop composition led by low buildings and wide sky",
        "Urban-depth composition with pale roofs and distant open sea",
    ),
    "salt_lake_landscape": (
        "Low flat-landscape composition with reeds in the foreground",
        "Wide salt-lake composition led by mineral shore and open sky",
    ),
    "beach_cafe_terrace": (
        "Street-level terrace composition led by shade and open horizon",
        "Linear cafe-edge composition with tables, railing and sea beyond",
    ),
}
_CY_COASTAL_COMPOSITIONS = tuple(
    composition
    for scene_family in _CYPRUS_SCENE_FAMILIES
    for composition in _CY_SCENE_COMPOSITIONS[scene_family]
)
_CY_INLAND_SCENE_COMPOSITIONS = {
    "inland_urban_rooftop": (
        "Layered Nicosia rooftop-and-sky composition",
        "Framed inland urban view led by pale buildings",
    ),
    "troodos_landscape": (
        "Layered Troodos ridge composition with dry pines",
        "Wide mountain composition led by receding ridges",
    ),
    "inland_village": (
        "Street-level village composition along a shaded stone lane",
        "Framed inland village view led by local stone houses",
    ),
    "dry_inland_landscape": (
        "Low inland composition across ochre fields and scrub",
        "Layered dry-land composition with low rolling terrain",
    ),
}
def _bay_visuals_disabled() -> bool:
    return str(os.getenv("CY_DISABLE_BAY_VISUALS", "0")).strip().lower() in {"1", "true", "yes", "on"}


def _scene_is_bay_or_cove(scene_family: str) -> bool:
    value = str(scene_family or "").lower()
    return any(token in value for token in ("bay", "cove", "lagoon"))


def _available_scene_families(options: tuple[str, ...]) -> tuple[str, ...]:
    if not _bay_visuals_disabled():
        return options
    filtered = tuple(value for value in options if not _scene_is_bay_or_cove(value))
    return filtered or ("open_beach_horizon", "coastal_promenade", "marina_walkway")


def _available_compositions(scene_family: str = "") -> tuple[str, ...]:
    options = _CY_SCENE_COMPOSITIONS.get(scene_family, _CY_COASTAL_COMPOSITIONS)
    if not _bay_visuals_disabled():
        return options
    blocked_terms = ("aerial", "raised", "bay", "cove", "lagoon", "sheltered")
    filtered = tuple(
        value for value in options
        if not any(term in value.lower() for term in blocked_terms)
    )
    return filtered or ("Scene-specific eye-level composition",)


def _inland_scene_family(scene_text: str) -> str:
    low = str(scene_text or "").lower()
    if "troodos" in low:
        return "troodos_landscape"
    if "village" in low:
        return "inland_village"
    if "dry inland" in low:
        return "dry_inland_landscape"
    return "inland_urban_rooftop"


def _visual_archetype(scene_family: str, composition: str) -> str:
    scene = str(scene_family or "").lower()
    comp = str(composition or "").lower()
    if _scene_is_bay_or_cove(scene):
        return "bay_panorama"
    if scene in {"open_sea_cliffs", "mountain_coast_view", "rocky_cove_overlook"} and any(
        token in comp for token in ("aerial", "raised", "wide panorama")
    ):
        return "elevated_cliff_panorama"
    if scene in {"long_sandy_beach", "open_beach_horizon"}:
        return "beach_eye_level"
    if scene in {"coastal_promenade", "beach_cafe_terrace"}:
        return "promenade_eye_level"
    if scene == "marina_walkway":
        return "marina_closeup"
    if scene in {"small_harbour", "harbour_pier_waterlevel", "breakwater_coast"}:
        return "harbour_pier"
    if scene == "coastal_urban_rooftop" or scene == "inland_urban_rooftop":
        return "urban_rooftop"
    if scene == "troodos_landscape":
        return "troodos_landscape"
    if scene in {"inland_village", "dry_inland_landscape"}:
        return scene
    if scene == "salt_lake_landscape":
        return "salt_lake_landscape"
    return "open_sea_shore"


def build_visual_context_cy(
    final_format_v2_message: str,
    *,
    post_type: str = "evening",
    visibility_metadata: Mapping[str, Any] | None = None,
) -> VisualContextCY:
    """Compatibility-named deterministic context step for the scene pipeline."""
    return parse_visual_context_cy(
        final_format_v2_message,
        post_type=post_type,
        visibility_metadata=visibility_metadata,
    )


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


def _select_composition_with_mode(
    date_key: str,
    post_type: str,
    weather_main: str,
    scene_family: str,
    *,
    variation_attempt: int = 0,
    blocked_compositions: tuple[str, ...] = (),
) -> tuple[str, str]:
    options = _available_compositions(scene_family)
    count = len(options)
    seed = "|".join([post_type, weather_main, "cyprus_composition"])
    offset = _stable_index(seed, "composition_offset", count)
    try:
        ordinal = date.fromisoformat(date_key).toordinal()
    except ValueError:
        ordinal = _stable_index(date_key, "undated_composition", count)
    idx = (ordinal + offset + variation_attempt) % count
    return _select_recent_aware_option_with_mode(
        options,
        idx,
        variation_attempt=variation_attempt,
        recent_values=blocked_compositions,
    )


def _select_recent_aware_option(
    options: tuple[str, ...],
    preferred_index: int,
    *,
    variation_attempt: int,
    recent_values: tuple[str, ...] = (),
) -> str:
    """Select from non-recent options first; if all are recent, use least-recently-used."""
    value, _mode = _select_recent_aware_option_with_mode(
        options,
        preferred_index,
        variation_attempt=variation_attempt,
        recent_values=recent_values,
    )
    return value


def _select_recent_aware_option_with_mode(
    options: tuple[str, ...],
    preferred_index: int,
    *,
    variation_attempt: int,
    recent_values: tuple[str, ...] = (),
) -> tuple[str, str]:
    if not options:
        raise ValueError("options must not be empty")
    ordered = tuple(options[(preferred_index + shift) % len(options)] for shift in range(len(options)))
    recent = tuple(value for value in recent_values if value in options)
    blocked = set(recent)
    eligible = tuple(value for value in ordered if value not in blocked)
    if eligible:
        return eligible[variation_attempt % len(eligible)], "eligible"
    if recent:
        return recent[0], "least_recently_used"
    return ordered[0], "eligible"


def _weather_constrained_scene_family(
    scene_family: str,
    date_key: str,
    post_type: str,
    ctx: VisualContextCY,
    *,
    variation_attempt: int,
    blocked_scenes: tuple[str, ...] = (),
) -> str:
    scene, _mode = _weather_constrained_scene_family_with_mode(
        scene_family,
        date_key,
        post_type,
        ctx,
        variation_attempt=variation_attempt,
        blocked_scenes=blocked_scenes,
    )
    return scene


def _weather_constrained_scene_family_with_mode(
    scene_family: str,
    date_key: str,
    post_type: str,
    ctx: VisualContextCY,
    *,
    variation_attempt: int,
    blocked_scenes: tuple[str, ...] = (),
) -> tuple[str, str]:
    wind_reference = max(
        [value for value in (ctx.wind_max, ctx.gust_max) if isinstance(value, (int, float))],
        default=None,
    )
    if wind_reference is not None and wind_reference >= 12:
        options = (
            "windy_exposed_coast",
            "breakwater_coast",
            "open_sea_cliffs",
            "long_sandy_beach",
            "coastal_promenade",
            "mountain_coast_view",
        )
    elif (
        (
            ctx.visibility_forecast_window in {"current_morning", "tomorrow_morning"}
            and ctx.visibility_condition != "clear"
        )
        or (ctx.visibility_haze and not ctx.dust_hint)
    ):
        options = ("coastal_promenade", "small_harbour")
    elif getattr(ctx, "inland_precipitation", False) or getattr(ctx, "inland_thunder_risk", False):
        options = ("mountain_coast_view", "open_sea_cliffs", "breakwater_coast")
    else:
        options = _CYPRUS_SCENE_FAMILIES
    options = _available_scene_families(options)
    if scene_family in options and scene_family not in set(blocked_scenes):
        return scene_family, "eligible"
    seed = "|".join([date_key, post_type, str(ctx.weather_main), "weather_scene_constraint"])
    base_index = _stable_index(seed, "scene", len(options))
    return _select_recent_aware_option_with_mode(
        options,
        base_index,
        variation_attempt=variation_attempt,
        recent_values=blocked_scenes,
    )


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
    if ctx.visibility_condition in {
        "dense_fog",
        "fog",
        "mist",
        "reduced_visibility",
        "dust_haze",
        "mixed_visibility",
    }:
        return ctx.visibility_condition
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
    if ctx.explicit_storm:
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
    blocked_scenes: tuple[str, ...] = (),
    blocked_compositions: tuple[str, ...] = (),
    blocked_archetypes: tuple[str, ...] = (),
    blocked_macro_families: tuple[str, ...] = (),
) -> dict[str, str]:
    date_key = _extract_date_key(message)
    blocked_archetype_set = {value for value in blocked_archetypes if value}
    blocked_macro_set = {value for value in blocked_macro_families if value}
    selected: tuple[str, str, str, str, str] | None = None
    attempts = max(1, len(_available_scene_families(_CYPRUS_SCENE_FAMILIES)) * 2)
    for offset in range(attempts):
        candidate_attempt = variation_attempt + offset
        scene_family = _select_scene_family(
            date_key,
            post_type,
            str(ctx.weather_main),
            variation_attempt=candidate_attempt,
        )
        scene_family, scene_selection_mode = _weather_constrained_scene_family_with_mode(
            scene_family,
            date_key,
            post_type,
            ctx,
            variation_attempt=candidate_attempt,
            blocked_scenes=blocked_scenes,
        )
        composition, composition_selection_mode = _select_composition_with_mode(
            date_key,
            post_type,
            str(ctx.weather_main),
            scene_family,
            variation_attempt=candidate_attempt,
            blocked_compositions=blocked_compositions,
        )
        visual_archetype = _visual_archetype(scene_family, composition)
        selected = (
            scene_family,
            composition,
            visual_archetype,
            scene_selection_mode,
            composition_selection_mode,
        )
        # An over-used macro family keeps the existing candidate search going; the
        # weather-aware selection above stays authoritative for what is offered.
        if (
            visual_archetype not in blocked_archetype_set
            and cyprus_scene_macro_family(scene_family) not in blocked_macro_set
        ):
            break
    assert selected is not None
    (
        scene_family,
        composition,
        visual_archetype,
        scene_selection_mode,
        composition_selection_mode,
    ) = selected
    scene_text = _CY_COASTAL_SCENE_TEMPLATES[scene_family][post_type]
    return {
        "scene_family": scene_family,
        "scene_text": scene_text,
        "composition": composition,
        "visual_archetype": visual_archetype,
        "scene_selection_mode": scene_selection_mode,
        "composition_selection_mode": composition_selection_mode,
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


def _semantic_keys(value: str) -> set[str]:
    low = re.sub(r"[^a-z0-9]+", " ", str(value).lower()).strip()
    groups = (
        ("visible_text", ("no text", "visible text", "letters", "pseudo caption")),
        ("logo", ("no logo", "brand marks")),
        ("watermark_signature", ("watermark", "artist signature", "no signature")),
        ("moon_scale", ("natural moon scale", "small to medium", "oversized moon", "fantasy planet", "fantasy supermoon")),
        ("wind_water", ("textured water", "wind ruffled", "uneven water", "mirror flat water")),
        ("tourist_calm", ("tourist calm", "perfect calm")),
        ("background_palms", ("palms optional", "palms distant", "foreground palms", "background accents")),
        ("curved_bay", ("scenic curved bay", "scenic curved tourist bay")),
    )
    keys = {
        key
        for key, phrases in groups
        if any(phrase in low for phrase in phrases)
    }
    return keys or {low}


def _dedupe_semantic_items(items: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        cleaned = re.sub(r"\s+", " ", str(item)).strip(" ,;:.")
        if not cleaned:
            continue
        keys = _semantic_keys(cleaned)
        if seen.intersection(keys):
            continue
        seen.update(keys)
        result.append(cleaned)
    return result


def _selected_scene_clause(metadata: dict[str, str], post_type: str) -> str:
    scene_family = metadata["selected_scene"]
    if scene_family in _CY_INLAND_SCENE_PROMPTS:
        return _CY_INLAND_SCENE_PROMPTS[scene_family]
    return _CY_COASTAL_SCENE_TEMPLATES[scene_family][post_type]


def _format_illumination(value: object) -> str:
    if not isinstance(value, (int, float)):
        return ""
    return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"


def _compact_time_cue(
    post_type: str,
    moon_context: dict[str, object],
    ctx: VisualContextCY,
) -> str:
    visibility_window = ctx.visibility_forecast_window
    if visibility_window in {"current_morning", "tomorrow_morning"}:
        prefix = "Next-day early-morning forecast window only: " if visibility_window == "tomorrow_morning" else ""
        if ctx.visibility_condition == "dense_fog":
            return prefix + "Early morning daylight filtered through humid coastal fog, soft diffused neutral light, muted contrast and moist atmospheric depth"
        if ctx.visibility_condition == "fog":
            return prefix + "Early morning daylight diffused through humid coastal fog with a softly obscured horizon"
        if ctx.visibility_condition == "mist":
            return prefix + "Fresh early morning daylight through light humid mist with gentle atmospheric depth"
        if ctx.visibility_condition == "reduced_visibility":
            return prefix + "Neutral early morning daylight with softened distant clarity and restrained contrast"
        if ctx.visibility_condition == "dust_haze":
            return prefix + "Neutral early morning daylight filtered by dry suspended particles"
        if ctx.visibility_condition == "mixed_visibility":
            return prefix + "Soft neutral early morning daylight through a muted grey mixed atmosphere"
    if ctx.visual_forecast_period == "representative_daytime":
        cue = "Bright representative Cyprus daytime, warm or neutral Mediterranean daylight and natural shadows"
        if ctx.uv_level in {"high", "extreme"}:
            cue += ", with crisp direct sunlight"
        return cue
    if ctx.visual_forecast_period in {"current_morning", "tomorrow_morning"}:
        cue = "Fresh neutral morning daylight, pale blue sky, natural shadows and light from the left"
        if ctx.uv_level in {"high", "extreme"}:
            cue += ", with crisp direct sunlight"
        return cue
    kind = str(moon_context.get("kind") or "")
    illumination = _format_illumination(moon_context.get("illumination"))
    if kind == "near_full":
        direction = str(moon_context.get("direction") or "waxing")
        amount = f", {illumination}% illuminated" if illumination else ""
        return f"Blue-hour late twilight with a realistic {direction} gibbous Moon{amount} and residual right-side horizon glow"
    if kind == "full":
        amount = f", {illumination}% illuminated" if illumination else ""
        return f"Blue-hour twilight with a realistic full Moon{amount} and residual right-side horizon glow"
    return "Restrained Cyprus late twilight with soft residual horizon glow and natural shadows"


def _compact_weather_cue(ctx: VisualContextCY, scene: SceneCuesCY) -> str:
    diagnostics = scene.diagnostics
    visibility_cues = {
        "dense_fog": "Dense humid fog, heavily reduced distant visibility, partially obscured horizon, soft diffused light, muted contrast, moist atmospheric depth",
        "fog": "Humid coastal fog, reduced distant visibility, softened horizon, diffused light",
        "mist": "Humid morning mist, softened distant clarity, gentle atmospheric depth",
        "reduced_visibility": "Reduced distant clarity, softened horizon, restrained contrast",
        "dust_haze": "Muted beige-grey dry atmospheric haze, dry suspended particles, reduced clarity, no humid fog cues",
        "mixed_visibility": "Muted grey atmospheric haze, restrained mixed grey haze, reduced distant clarity, restrained humid softness, restrained polluted-air haze",
    }
    if diagnostics.get("visibility_visual_rule") and ctx.visibility_condition in visibility_cues:
        cue = visibility_cues[ctx.visibility_condition]
        if diagnostics.get("wet_rule"):
            cue += ", with factual rain and wet coastal surfaces"
        return cue
    if diagnostics.get("wet_rule"):
        return "Layered Mediterranean rain clouds with factual rain and wet coastal surfaces"
    if diagnostics.get("dust_rule"):
        return "Muted Mediterranean sky with explicit beige-gold dust haze"
    if diagnostics.get("visibility_haze_rule"):
        return "Soft humid haze with reduced distant visibility"
    if diagnostics.get("inland_unsettled_rule"):
        return "Dry coast under clearer sky with distant convective cloud towers over Troodos"
    if diagnostics.get("explicit_storm_rule"):
        return "Strong wind-alert cloud structure over a dry Cyprus landscape, without invented precipitation"
    if ctx.weather_main == "cloudy":
        return "Layered Mediterranean cloud cover"
    if diagnostics.get("severe_wind_rule"):
        return "Layered wind-driven Mediterranean clouds over dry coastal surfaces"
    if ctx.weather_main == "mixed":
        return "Passing Mediterranean clouds with clear intervals"
    return "Clear Mediterranean sky"


def _compact_wind_sea_cue(ctx: VisualContextCY, scene: SceneCuesCY) -> str:
    diagnostics = scene.diagnostics
    wet = bool(diagnostics.get("wet_rule"))
    windy = bool(diagnostics.get("wind_rule"))
    severe = bool(diagnostics.get("severe_wind_rule"))
    inland_only = ctx.scene_focus == "inland"
    if inland_only:
        if severe:
            return "Strong wind visibly moving dry inland vegetation"
        if windy:
            return "Breezy inland air with visible movement in dry vegetation"
        return "Light natural movement in dry inland vegetation"
    if severe:
        if wet:
            return "Strong coastal wind in textured water and leaning coastal grass, with frequent small whitecaps"
        return "Strong dry coastal wind in textured water and leaning coastal grass, frequent small whitecaps, dry promenade and rocks"
    if windy:
        cue = "Gusty wind visible in textured water and leaning coastal grass"
        if ctx.gust_max is not None and ctx.gust_max >= 12:
            cue += ", with occasional small whitecaps"
        return cue
    if wet:
        return "Rain-rippled Mediterranean water with an active natural surface"
    if ctx.sea_state_hint == "rough":
        return "Active Mediterranean water with irregular natural wave texture"
    if ctx.sea_state_hint == "calm":
        return "Calm Mediterranean water with light natural ripples"
    return "Natural Mediterranean water with restrained surface movement"


def _compact_finish_cue(ctx: VisualContextCY) -> str:
    cue = "Natural Mediterranean colors, realistic atmospheric perspective and restrained editorial weather photography, with palms optional as background accents"
    suppress_heat_shimmer = (
        ctx.visibility_forecast_window in {"current_morning", "tomorrow_morning"}
        and ctx.visibility_condition in {"dense_fog", "fog", "mist", "mixed_visibility"}
    )
    if ctx.temp_max is not None and ctx.temp_max >= 33 and not suppress_heat_shimmer:
        cue += " and subtle heat shimmer over sun-warmed stone"
    return cue


def _negative_items(
    post_type: str,
    moon_context: dict[str, object],
    metadata: dict[str, str],
    scene: SceneCuesCY,
    ctx: VisualContextCY,
) -> list[str]:
    items = [
        "no text or logo",
        "no watermark or signature",
        "no illustration or fantasy",
        "no map, no satellite imagery, no cartographic view, no aerial map, no screenshot, no browser or app interface, no UI chrome, no screen capture",
    ]
    if not ctx.actual_precipitation:
        items.extend(["no rain", "no wet roads or wet rocks"])
    if not ctx.explicit_storm:
        items.extend(["no storm", "no lightning or rough storm sea"])
    if scene.diagnostics.get("wind_rule"):
        items.append("no mirror-flat water or still vegetation")
    if ctx.visibility_condition == "clear":
        items.append("no fog")
    if _bay_visuals_disabled():
        items.append(
            "no scenic curved bay, no natural cove, no enclosed tourist lagoon, no elevated postcard coastline"
        )
    elif metadata["selected_scene"] == "small_harbour":
        items.append("no scenic curved tourist bay")
    if ctx.visual_forecast_period == "representative_daytime":
        items.extend(["no sunset", "no night", "no moon-led scene", "no gloomy twilight", "no cold winter palette"])
    visibility_window = str(scene.diagnostics.get("visibility_forecast_window") or "none")
    visibility_condition = str(scene.diagnostics.get("visibility_condition") or "clear")
    if visibility_window in {"current_morning", "tomorrow_morning"}:
        if visibility_window == "current_morning":
            items.extend(
                [
                    "no sunset and no orange golden-hour sky",
                    "no moon and no night",
                    "no bright light source on the right",
                ]
            )
        else:
            items.extend(
                [
                    "no evening twilight or moon-led scene",
                    "no all-day fog implication",
                ]
            )
        if visibility_condition in {"dense_fog", "fog", "mist"}:
            items.append(
                "no crisp distant horizon, no perfectly clear horizon, no sharp postcard visibility, no completely transparent air"
            )
            items.append("no dry dust-colored sky unless dust evidence exists")
        elif visibility_condition == "reduced_visibility":
            items.append("no invented humid fog or wet atmosphere")
        elif visibility_condition == "dust_haze":
            items.append("no humid coastal fog or moist fog depth")
        elif visibility_condition == "mixed_visibility":
            items.append("no dense wall of fog and no exaggerated Sahara palette")
    elif ctx.visual_forecast_period in {"representative_daytime", "current_morning", "tomorrow_morning"}:
        items.extend(
            [
                "no night",
                "no moon-led scene",
                "no bright light source on the right",
            ]
        )
    elif moon_context.get("kind"):
        items.append("natural moon scale, no oversized moon and no fantasy planet")
        if moon_context.get("kind") == "near_full":
            items.append("no perfect full moon")
    return _dedupe_semantic_items(items)[:15]


def _fmt_fact_number(value: object) -> str:
    if not isinstance(value, (int, float)):
        return "unknown"
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


def _weather_truth_block(ctx: VisualContextCY) -> str:
    period_labels = {
        "current_morning": "current morning",
        "representative_daytime": "representative daytime tomorrow" if ctx.post_type == "evening" else "representative daytime today",
        "tomorrow_morning": "tomorrow morning",
        "evening": "explicitly forecast evening period",
        "overnight": "explicitly forecast overnight period",
    }
    dry_suffix = " and mostly dry" if not ctx.actual_precipitation else " with confirmed precipitation"
    facts = [
        f"forecast period: {period_labels.get(ctx.visual_forecast_period, ctx.visual_forecast_period)}",
        f"primary weather: {ctx.primary_weather}{dry_suffix}",
    ]
    if ctx.inland_max_temp is not None:
        facts.append(f"inland maximum: {_fmt_fact_number(ctx.inland_max_temp)} C")
    elif ctx.temp_max is not None:
        facts.append(f"island maximum: {_fmt_fact_number(ctx.temp_max)} C")
    if ctx.coastal_temp_min is not None and ctx.coastal_temp_max is not None:
        facts.append(
            "coastal temperatures: "
            f"{_fmt_fact_number(ctx.coastal_temp_min)}-{_fmt_fact_number(ctx.coastal_temp_max)} C"
        )
    if ctx.gust_max is not None:
        facts.append(f"coastal gusts: up to {_fmt_fact_number(ctx.gust_max)} m/s")
    facts.extend(
        [
            "precipitation: confirmed" if ctx.actual_precipitation else "precipitation: none confirmed",
            "storm: explicitly confirmed" if ctx.explicit_storm else "storm: not confirmed",
            f"visibility: {ctx.visibility_condition}",
        ]
    )

    priorities: list[str] = []
    must_show: list[str] = []
    if ctx.inland_heat_focus:
        priorities.append("strong inland heat")
        must_show.extend(["hot Cyprus daytime", "inland heat haze"])
    if ctx.coastal_focus:
        priorities.append("breezy Mediterranean coast" if ctx.strong_wind else "Mediterranean coast")
        must_show.append("breezy Mediterranean coast" if ctx.strong_wind else "Mediterranean coastal context")
    if ctx.primary_weather in {"mixed", "cloudy", "hot"} and not ctx.actual_precipitation:
        priorities.append("partly cloudy or mostly dry summer sky")
        must_show.append("partly cloudy or mostly dry summer sky")
    if ctx.actual_precipitation:
        priorities.append("factual precipitation")
        must_show.append("factual rain only where forecast")
    if ctx.explicit_storm:
        priorities.append("explicit storm warning")
        must_show.append("storm wind structure")
    if ctx.strong_wind:
        must_show.append("visible wind on sea surface or vegetation")
    if ctx.visibility_condition in {"dense_fog", "fog", "mist"}:
        priorities.append("forecast visibility restriction")
        must_show.append("factual fog or mist depth")
    if not priorities:
        priorities.append("factual Cyprus weather")
    if not must_show:
        must_show.append("dry Cyprus landscape in natural forecast-period light")
    return (
        "WEATHER TRUTH:\n- "
        + ";\n- ".join(facts)
        + ".\nVISUAL PRIORITY:\n"
        + "\n".join(f"{index}. {value};" for index, value in enumerate(priorities[:4], start=1))
        + "\nMUST SHOW:\n- "
        + ";\n- ".join(must_show[:5])
    )


def _compose_prompt(positive: list[str], negative: list[str]) -> str:
    return "; ".join(positive).rstrip(" .;") + ". Avoid: " + "; ".join(negative) + "."


def _fit_prompt_budget(positive: list[str], negative: list[str]) -> tuple[str, list[str]]:
    positive = _dedupe_semantic_items(positive)
    negative = _dedupe_semantic_items(negative)[:15]
    prompt = _compose_prompt(positive, negative)
    if len(prompt) < _PROMPT_TARGET_MIN_CHARS:
        positive[-1] += ", natural depth and balanced local detail without postcard exaggeration"
        prompt = _compose_prompt(positive, negative)
    if len(prompt) > _PROMPT_TARGET_MAX_CHARS:
        if len(positive) > 1:
            positive[1] = "Photorealistic Cyprus landscape photography"
        positive[-1] = "Natural colors, realistic detail and palms only as optional background accents"
        prompt = _compose_prompt(positive, negative)
    if len(prompt) > _PROMPT_TARGET_MAX_CHARS:
        if len(positive) > 6:
            positive[6] = "Coherent composition"
        positive[-1] = "Natural colors and detail"
        prompt = _compose_prompt(positive, negative)
    if len(prompt) > _PROMPT_HARD_MAX_CHARS:
        raise ValueError("Cyprus visual prompt exceeds the hard length limit")
    return prompt, positive


def _pollinations_encoded_url_length(prompt: str) -> int:
    base = os.getenv("POLLINATIONS_BASE_URL", "https://image.pollinations.ai/prompt/").rstrip("/")
    referrer = os.getenv("POLLINATIONS_REFERRER", "worldvibemeter").strip()
    encoded = quote_plus(f"{prompt} :: {'0' * 32}")
    return len(f"{base}/{encoded}?width=1024&height=1024&referrer={quote_plus(referrer)}")


def _visual_cache_metadata(
    message: str,
    ctx: VisualContextCY,
    post_type: str,
    *,
    variation_attempt: int,
    blocked_scenes: tuple[str, ...] = (),
    blocked_compositions: tuple[str, ...] = (),
    blocked_archetypes: tuple[str, ...] = (),
    blocked_macro_families: tuple[str, ...] = (),
) -> dict[str, object]:
    forecast_date = _extract_date_key(message)
    if ctx.scene_focus == "inland":
        seed = _variant_seed(message, ctx, post_type)
        scene_text = _stable_variant(seed, "scene", _CY_INLAND_SCENES)
        selected_scene = _inland_scene_family(scene_text)
        composition = _stable_variant(
            seed,
            "composition",
            _CY_INLAND_SCENE_COMPOSITIONS[selected_scene],
        )
        visual_archetype = _visual_archetype(selected_scene, composition)
        scene_selection_mode = "eligible"
        composition_selection_mode = "eligible"
    else:
        variants = _coastal_visual_variants(
            message,
            ctx,
            post_type,
            variation_attempt=variation_attempt,
            blocked_scenes=blocked_scenes,
            blocked_compositions=blocked_compositions,
            blocked_archetypes=blocked_archetypes,
            blocked_macro_families=blocked_macro_families,
        )
        selected_scene = variants["scene_family"]
        composition = variants["composition"]
        visual_archetype = variants["visual_archetype"]
        scene_selection_mode = variants["scene_selection_mode"]
        composition_selection_mode = variants["composition_selection_mode"]
    lunar_mode = post_type if ctx.visual_forecast_period in {"evening", "overnight"} else "morning"
    lunar_phase, lunar_illumination = _moon_cache_fields(message, lunar_mode)
    metadata = {
        "forecast_date": forecast_date,
        "post_type": post_type,
        "target_date": "today" if post_type == "morning" else "tomorrow",
        "prompt_version": CYPRUS_VISUAL_PROMPT_VERSION,
        "selected_scene": selected_scene,
        "composition": composition,
        "visual_archetype": visual_archetype,
        "scene_selection_mode": scene_selection_mode,
        "composition_selection_mode": composition_selection_mode,
        "weather_scenario": str(ctx.weather_main),
        "primary_weather": str(ctx.primary_weather),
        "hazards": ",".join(ctx.hazards),
        "visual_forecast_period": ctx.visual_forecast_period,
        "scene_focus": ctx.scene_focus,
        "actual_precipitation": str(bool(ctx.actual_precipitation)).lower(),
        "explicit_storm": str(bool(ctx.explicit_storm)).lower(),
        "severe_wind": str(bool(ctx.severe_wind)).lower(),
        "wind_gust_category": _wind_category(ctx),
        "cloud_haze_category": _cloud_haze_category(ctx),
        "current_visibility_m": ctx.current_visibility_m,
        "morning_min_visibility_m": ctx.morning_min_visibility_m,
        "humidity_pct": ctx.humidity_pct,
        "temperature_c": ctx.temperature_c,
        "dew_point_c": ctx.dew_point_c,
        "dew_point_spread_c": ctx.dew_point_spread_c,
        "weather_code": ctx.weather_code,
        "weather_code_source": ctx.weather_code_source,
        "observation_time": ctx.observation_time,
        "confidence": ctx.confidence,
        "visibility_condition": str(ctx.visibility_condition),
        "visibility_forecast_window": ctx.visibility_forecast_window,
        "visibility_evidence": ctx.visibility_evidence,
        "classification_reason": ctx.classification_reason,
        "location_label": ctx.location_label,
        "fog_text_added": str(bool(ctx.visibility_evidence)).lower(),
        "fog_visual_rule": str(
            ctx.visibility_forecast_window in {"current_morning", "tomorrow_morning"}
            and ctx.visibility_condition in {"dense_fog", "fog", "mist"}
        ).lower(),
        "dust_vs_fog_classification": str(ctx.dust_vs_fog_classification),
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
        "visual_archetype",
        "scene_selection_mode",
        "composition_selection_mode",
        "weather_scenario",
        "primary_weather",
        "hazards",
        "visual_forecast_period",
        "scene_focus",
        "actual_precipitation",
        "explicit_storm",
        "severe_wind",
        "wind_gust_category",
        "cloud_haze_category",
        "visibility_condition",
        "visibility_forecast_window",
        "dust_vs_fog_classification",
        "lunar_phase",
        "lunar_illumination",
        "variation_attempt",
    )
    metadata["cache_key"] = "|".join(f"{key}={metadata[key]}" for key in ordered)
    metadata["cache_digest"] = hashlib.sha256(metadata["cache_key"].encode("utf-8")).hexdigest()[:12]
    # Macro identity is an additive diagnostics/history field only. It is assigned
    # after the ordered cache key above, and it is deliberately absent from `ordered`,
    # so the existing cache identity stays byte-for-byte unchanged.
    metadata["scene_macro_family"] = cyprus_scene_macro_family(selected_scene)
    return metadata


def build_cyprus_visual_cache_key(
    final_format_v2_message: str,
    *,
    post_type: str = "evening",
    variation_attempt: int = 0,
    blocked_scenes: tuple[str, ...] = (),
    blocked_compositions: tuple[str, ...] = (),
    blocked_archetypes: tuple[str, ...] = (),
    blocked_macro_families: tuple[str, ...] = (),
    visibility_metadata: Mapping[str, Any] | None = None,
) -> str:
    mode = post_type.strip().lower()
    if mode not in {"morning", "evening"}:
        raise ValueError("post_type must be 'morning' or 'evening'")
    ctx = build_visual_context_cy(
        final_format_v2_message,
        post_type=mode,
        visibility_metadata=visibility_metadata,
    )
    return _visual_cache_metadata(
        final_format_v2_message,
        ctx,
        mode,
        variation_attempt=variation_attempt,
        blocked_scenes=blocked_scenes,
        blocked_compositions=blocked_compositions,
        blocked_archetypes=blocked_archetypes,
        blocked_macro_families=blocked_macro_families,
    )["cache_key"]


@dataclass(frozen=True)
class CyprusVisualDecision:
    """One canonical Cyprus visual decision, reused across the whole candidate lifecycle.

    The decision is built exactly once per visual candidate and then flows unchanged
    through routing, prompt, provider, validation, dedup, diagnostics, history and
    receipt, so every stage reports the same identity without re-parsing or reselecting.
    """

    context: VisualContextCY
    prompt: str
    style_name: str
    metadata: dict[str, object]
    visibility_metadata: Mapping[str, Any] | None = field(default=None)

    @property
    def decision_id(self) -> str:
        return str(self.metadata.get("decision_id", ""))

    @property
    def cache_key(self) -> str:
        return str(self.metadata.get("cache_key", ""))

    @property
    def selected_scene(self) -> str:
        return str(self.metadata.get("selected_scene", ""))

    @property
    def composition(self) -> str:
        return str(self.metadata.get("composition", ""))

    @property
    def visual_archetype(self) -> str:
        return str(self.metadata.get("visual_archetype", ""))

    @property
    def forecast_date(self) -> str:
        return str(self.metadata.get("forecast_date", ""))

    @property
    def post_type(self) -> str:
        return str(self.metadata.get("post_type", ""))

    def identity(self) -> dict[str, str]:
        """Identity fields that must match across provider, dedup, history and receipt."""
        return {
            "decision_id": self.decision_id,
            "selected_scene": self.selected_scene,
            "composition": self.composition,
            "visual_archetype": self.visual_archetype,
            "style_name": self.style_name,
            "cache_key": self.cache_key,
        }


def build_cyprus_visual_decision(
    final_format_v2_message: str,
    *,
    post_type: str = "evening",
    variation_attempt: int = 0,
    blocked_scenes: tuple[str, ...] = (),
    blocked_compositions: tuple[str, ...] = (),
    blocked_archetypes: tuple[str, ...] = (),
    blocked_macro_families: tuple[str, ...] = (),
    visibility_metadata: Mapping[str, Any] | None = None,
    visual_context: VisualContextCY | None = None,
) -> CyprusVisualDecision:
    """Build the canonical decision for one Cyprus visual candidate.

    When ``visual_context`` is supplied it is used as-is: the parser is not run a
    second time, so context provenance stays identical across the lifecycle. Callers
    that do not have a precomputed context keep the previous parse-on-demand behaviour.
    """
    mode = post_type.strip().lower()
    if mode not in {"morning", "evening"}:
        raise ValueError("post_type must be 'morning' or 'evening'")

    ctx = (
        visual_context
        if visual_context is not None
        else build_visual_context_cy(
            final_format_v2_message,
            post_type=mode,
            visibility_metadata=visibility_metadata,
        )
    )
    scene = apply_visual_rules_cy(ctx)
    moon_context = (
        _evening_moon_visual_context(final_format_v2_message)
        if ctx.visual_forecast_period in {"evening", "overnight"}
        else {}
    )
    metadata = _visual_cache_metadata(
        final_format_v2_message,
        ctx,
        mode,
        variation_attempt=variation_attempt,
        blocked_scenes=blocked_scenes,
        blocked_compositions=blocked_compositions,
        blocked_archetypes=blocked_archetypes,
        blocked_macro_families=blocked_macro_families,
    )
    positive = [
        "Photorealistic natural Cyprus landscape photography",
        _selected_scene_clause(metadata, mode),
        _compact_time_cue(mode, moon_context, ctx),
        _compact_weather_cue(ctx, scene),
        _compact_wind_sea_cue(ctx, scene),
        metadata["composition"],
        _compact_finish_cue(ctx),
    ]
    sanitized_positive = [
        sanitize_cyprus_scene_prompt(part, post_type=mode)
        for part in positive
    ]
    # The time cue is controlled text; restore its factual lunar percentage after
    # the general sanitizer removes raw percentages from source-derived content.
    sanitized_positive[2] = _compact_time_cue(mode, moon_context, ctx)
    sanitized_positive.insert(0, _weather_truth_block(ctx))
    negative = _negative_items(mode, moon_context, metadata, scene, ctx)
    prompt, final_positive = _fit_prompt_budget(sanitized_positive, negative)
    final_negative = _dedupe_semantic_items(negative)[:15]
    encoded_url_length = _pollinations_encoded_url_length(prompt)
    if encoded_url_length > _POLLINATIONS_URL_HARD_MAX_CHARS:
        raise ValueError("Cyprus visual prompt exceeds the Pollinations URL length limit")
    style_digest = hashlib.sha256(
        f"{metadata['cache_key']}|{prompt}".encode("utf-8")
    ).hexdigest()[:8]
    style_name = f"cyprus_{mode}_mediterranean_landscape_{style_digest}"
    metadata["style_name"] = style_name
    metadata["prompt_length_chars"] = len(prompt)
    metadata["positive_clause_count"] = len(final_positive)
    metadata["negative_item_count"] = len(final_negative)
    metadata["pollinations_encoded_url_length"] = encoded_url_length

    # Diagnostics-only provenance. Everything below is appended AFTER the ordered
    # cache key, the prompt and the style digest are already final, so it can never
    # change visual selection, the cache identity or the style name.
    metadata["routing_inputs"] = {
        "primary_weather": metadata["primary_weather"],
        "hazards": metadata["hazards"],
        "scene_focus": metadata["scene_focus"],
        "visual_forecast_period": metadata["visual_forecast_period"],
        "visibility_condition": metadata["visibility_condition"],
        "weather_scenario": metadata["weather_scenario"],
        "variation_attempt": metadata["variation_attempt"],
    }
    metadata["cooldown_inputs"] = {
        "blocked_scenes": list(blocked_scenes),
        "blocked_compositions": list(blocked_compositions),
        "blocked_archetypes": list(blocked_archetypes),
        "blocked_macro_families": list(blocked_macro_families),
    }
    metadata["decision_id"] = hashlib.sha256(
        "|".join(
            (
                str(metadata["cache_key"]),
                str(metadata["style_name"]),
                prompt,
            )
        ).encode("utf-8")
    ).hexdigest()[:16]

    return CyprusVisualDecision(
        context=ctx,
        prompt=prompt,
        style_name=style_name,
        metadata=metadata,
        visibility_metadata=visibility_metadata,
    )


def build_cyprus_scene_prompt_with_metadata(
    final_format_v2_message: str,
    *,
    post_type: str = "evening",
    variation_attempt: int = 0,
    blocked_scenes: tuple[str, ...] = (),
    blocked_compositions: tuple[str, ...] = (),
    blocked_archetypes: tuple[str, ...] = (),
    blocked_macro_families: tuple[str, ...] = (),
    visibility_metadata: Mapping[str, Any] | None = None,
) -> tuple[str, str, dict[str, object]]:
    """Return a sanitized Cyprus prompt, stable style name, and visual cache metadata."""
    decision = build_cyprus_visual_decision(
        final_format_v2_message,
        post_type=post_type,
        variation_attempt=variation_attempt,
        blocked_scenes=blocked_scenes,
        blocked_compositions=blocked_compositions,
        blocked_archetypes=blocked_archetypes,
        blocked_macro_families=blocked_macro_families,
        visibility_metadata=visibility_metadata,
    )
    return decision.prompt, decision.style_name, decision.metadata


def build_cyprus_scene_prompt(
    final_format_v2_message: str,
    *,
    post_type: str = "evening",
    variation_attempt: int = 0,
    blocked_scenes: tuple[str, ...] = (),
    blocked_compositions: tuple[str, ...] = (),
    blocked_archetypes: tuple[str, ...] = (),
    blocked_macro_families: tuple[str, ...] = (),
    visibility_metadata: Mapping[str, Any] | None = None,
) -> tuple[str, str]:
    """Return a sanitized positive Cyprus landscape prompt and stable style name."""
    prompt, style_name, _metadata = build_cyprus_scene_prompt_with_metadata(
        final_format_v2_message,
        post_type=post_type,
        variation_attempt=variation_attempt,
        blocked_scenes=blocked_scenes,
        blocked_compositions=blocked_compositions,
        blocked_archetypes=blocked_archetypes,
        blocked_macro_families=blocked_macro_families,
        visibility_metadata=visibility_metadata,
    )
    return prompt, style_name


__all__ = [
    "build_visual_context_cy",
    "sanitize_cyprus_scene_prompt",
    "build_cyprus_visual_cache_key",
    "build_cyprus_scene_prompt",
    "build_cyprus_scene_prompt_with_metadata",
    "build_cyprus_visual_decision",
    "CyprusVisualDecision",
    "CYPRUS_VISUAL_PROMPT_VERSION",
]
