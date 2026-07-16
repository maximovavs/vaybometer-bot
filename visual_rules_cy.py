#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic scene rules for Cyprus visual context."""

from __future__ import annotations

from dataclasses import dataclass, field

from visual_context_cy import VisualContextCY


@dataclass
class SceneCuesCY:
    base_scene: str
    sky_cue: str
    light_cue: str
    sea_cue: str
    air_cue: str
    activity_cue: str
    mood_cue: str
    must_show: list[str] = field(default_factory=list)
    must_avoid: list[str] = field(default_factory=list)
    diagnostics: dict[str, object] = field(default_factory=dict)


def _is_hot(ctx: VisualContextCY) -> bool:
    return ctx.weather_main == "hot" or (ctx.temp_max is not None and ctx.temp_max >= 33)


def _is_windy(ctx: VisualContextCY) -> bool:
    return (
        (ctx.wind_max is not None and ctx.wind_max >= 6)
        or (ctx.gust_max is not None and ctx.gust_max >= 9)
    )


_WET_VISIBILITY_CONDITIONS = {"dense_fog", "fog", "mist"}
_VISIBILITY_VISUAL_CONDITIONS = {
    "dense_fog",
    "fog",
    "mist",
    "reduced_visibility",
    "dust_haze",
    "mixed_visibility",
}


def _visibility_visual_cues(condition: str) -> dict[str, object]:
    cues: dict[str, dict[str, object]] = {
        "dense_fog": {
            "sky": "dense humid fog with a heavily obscured Cyprus horizon",
            "light": "soft diffused morning light with muted contrast and moist atmospheric depth",
            "air": "dense humid fog; heavily reduced distant visibility; partially obscured horizon; soft diffused light; muted contrast; moist atmospheric depth",
            "mood": "cautious practical morning mood until the dense coastal fog disperses",
            "show": [
                "dense humid fog",
                "heavily reduced distant visibility",
                "partially obscured horizon",
                "soft diffused light",
                "muted contrast",
                "moist atmospheric depth",
            ],
        },
        "fog": {
            "sky": "humid coastal fog with a softened Cyprus horizon",
            "light": "soft diffused morning light through humid coastal fog",
            "air": "humid coastal fog; reduced distant visibility; softened horizon; diffused light",
            "mood": "soft cautious Cyprus morning while coastal fog limits distance",
            "show": [
                "humid coastal fog",
                "reduced distant visibility",
                "softened horizon",
                "diffused light",
            ],
        },
        "mist": {
            "sky": "light humid morning mist with gently softened distant detail",
            "light": "gentle neutral morning light filtered through light mist",
            "air": "humid morning mist; softened distant clarity; gentle atmospheric depth",
            "mood": "fresh practical Cyprus morning with light local mist",
            "show": [
                "humid morning mist",
                "softened distant clarity",
                "gentle atmospheric depth",
            ],
        },
        "reduced_visibility": {
            "sky": "restrained sky with a softened distant horizon",
            "light": "neutral morning daylight with restrained contrast",
            "air": "reduced distant clarity; softened horizon; restrained contrast",
            "mood": "practical morning mood with locally reduced clarity",
            "show": [
                "reduced distant clarity",
                "softened horizon",
                "restrained contrast",
            ],
        },
        "dust_haze": {
            "sky": "muted beige-grey dry atmospheric haze over Cyprus",
            "light": "morning daylight filtered by dry suspended particles",
            "air": "muted beige-grey dry atmospheric haze; dry suspended particles; reduced clarity; no humid fog cues",
            "mood": "subdued practical morning under dry airborne haze",
            "show": [
                "muted beige-grey dry atmospheric haze",
                "dry suspended particles",
                "reduced clarity",
                "no humid fog cues",
            ],
        },
        "mixed_visibility": {
            "sky": "muted grey atmospheric haze with restrained humid softness",
            "light": "soft neutral morning light through a mixed grey atmosphere",
            "air": "muted grey atmospheric haze; reduced distant clarity; restrained humid softness; restrained polluted-air haze; no exaggerated Sahara palette",
            "mood": "cautious practical morning under mixed humid and polluted-air haze",
            "show": [
                "muted grey atmospheric haze",
                "reduced distant clarity",
                "restrained humid softness",
                "restrained polluted-air haze",
                "no exaggerated Sahara palette",
            ],
        },
    }
    return cues.get(condition, {})


def apply_visual_rules_cy(ctx: VisualContextCY) -> SceneCuesCY:
    """Map parsed facts to Cyprus-specific scene cues."""
    if ctx.post_type not in {"morning", "evening"}:
        raise ValueError("ctx.post_type must be 'morning' or 'evening'")

    hot = _is_hot(ctx)
    rain = bool(ctx.coastal_precipitation or (ctx.actual_precipitation and not ctx.coastal_focus))
    inland_unsettled = bool(ctx.inland_precipitation or ctx.inland_thunder_risk) and ctx.coastal_focus and not rain
    severe_wind = bool(ctx.severe_wind)
    wet = rain
    visibility_visual = (
        ctx.post_type == "morning"
        and ctx.visibility_condition in _VISIBILITY_VISUAL_CONDITIONS
    )
    fog_visual = visibility_visual and ctx.visibility_condition in _WET_VISIBILITY_CONDITIONS
    dense_fog = ctx.visibility_condition == "dense_fog"
    mixed_haze = ctx.visibility_condition == "mixed_visibility"
    dusty = (
        ctx.visibility_condition == "dust_haze"
        or ((ctx.weather_main == "dusty" or bool(ctx.dust_hint)) and not visibility_visual)
    )
    visibility_haze = bool(ctx.visibility_haze) and not visibility_visual and not dusty
    visibility_cues = _visibility_visual_cues(ctx.visibility_condition) if visibility_visual else {}
    windy = _is_windy(ctx)

    if ctx.coastal_focus:
        base_scene = "Cyprus Mediterranean coast with a Limassol or Larnaca promenade"
    elif ctx.inland_heat_focus:
        base_scene = "Cyprus dry urban inland scene in Nicosia"
    else:
        base_scene = "Cyprus Mediterranean coast with palms and local stone architecture"

    if visibility_visual:
        sky_cue = str(visibility_cues["sky"])
    elif wet:
        sky_cue = "dramatic rain clouds over Cyprus"
    elif inland_unsettled:
        sky_cue = "warm Cyprus coast with distant inland cloud development and convective cloud build-up toward the Troodos mountains"
    elif severe_wind:
        sky_cue = "layered wind-driven Mediterranean clouds over a dry Cyprus coast"
    elif dusty:
        sky_cue = "hazy muted sky with beige-gold atmospheric dust"
    elif visibility_haze:
        sky_cue = "soft humid haze with reduced distant visibility"
    elif ctx.weather_main == "cloudy":
        sky_cue = "layered Mediterranean cloud cover"
    elif ctx.weather_main == "mixed":
        sky_cue = "sun and passing Mediterranean clouds"
    elif ctx.weather_main == "rain":
        sky_cue = "mixed Mediterranean cloud depth without making the whole coast stormy"
    else:
        sky_cue = "clear bright Mediterranean sky"

    if visibility_visual:
        light_cue = str(visibility_cues["light"])
    elif ctx.post_type == "morning":
        light_cue = "daylight Mediterranean morning, bright practical light for the day ahead"
    else:
        light_cue = "Mediterranean evening with restrained twilight light and residual horizon glow"

    if ctx.post_type == "morning" and ctx.uv_level in {"high", "extreme"} and not visibility_visual:
        light_cue += "; strong sun cue with crisp sunlit surfaces"
    if dusty and not visibility_visual:
        light_cue += "; muted filtered sun"
    if hot and not visibility_visual:
        light_cue += "; visible heat shimmer"

    if wet:
        sea_cue = "rain-darkened coast and a wet promenade; active unsettled sea"
    elif inland_unsettled and windy:
        sea_cue = (
            "warm dry Cyprus coast with wind-ruffled sea and uneven Mediterranean water texture; "
            "visible wind response in palm fronds and coastal grass; convective cloud build-up toward Troodos/inland"
        )
        if ctx.gust_max is not None and ctx.gust_max >= 12:
            sea_cue += "; occasional small whitecaps, not storm-scale"
    elif inland_unsettled:
        sea_cue = "warm dry Cyprus coast with distant inland cloud towers and convective build-up toward the mountains"
    elif severe_wind:
        sea_cue = (
            "strongly textured Mediterranean water surface with frequent small whitecaps; "
            "visibly bent palm fronds and coastal grass; dry promenade and dry coastal rocks"
        )
    elif windy:
        sea_cue = (
            "wind-ruffled textured Mediterranean water surface with small wind-driven ripples; "
            "visible wind response in palm fronds and coastal grass"
        )
        if ctx.gust_max is not None and ctx.gust_max >= 12:
            sea_cue += "; occasional small whitecaps, not storm-scale"
    elif ctx.coastal_focus:
        if ctx.sea_state_hint == "calm":
            sea_cue = "calm warm sea surface beside a Cyprus coastal promenade"
        elif ctx.sea_state_hint == "rough":
            sea_cue = "active Mediterranean sea beside a weather-exposed Cyprus coast"
        else:
            sea_cue = "Mediterranean water beside a Cyprus promenade or rocky coast"
    elif ctx.inland_heat_focus:
        sea_cue = "dry inland horizon with sun-warmed stone and urban depth"
    else:
        sea_cue = "Mediterranean sea present as quiet geographic context"

    air_parts: list[str] = []
    if visibility_visual:
        air_parts.append(str(visibility_cues["air"]))
    elif dusty:
        air_parts.append("hazy beige-gold air with suspended dust")
    elif visibility_haze:
        air_parts.append("soft humid atmospheric depth with reduced distant visibility")
    elif ctx.humidity_hint in {"high", "present"}:
        air_parts.append("soft sea haze from humid coastal air")
    if hot and not visibility_visual:
        air_parts.append("dry hot air and heat shimmer")
    if ctx.aqi_level in {"poor", "very_poor"}:
        air_parts.append("reduced atmospheric clarity")
    air_cue = "; ".join(air_parts) if air_parts else "clear Mediterranean air"

    if wet:
        activity_cue = "sheltered pedestrians on a wet promenade; no beach leisure mood"
    elif inland_unsettled:
        activity_cue = "dry coastal promenade with weather-aware planning and distant inland cloud development"
    elif severe_wind:
        activity_cue = "dry coastal promenade with wind-aware pedestrians and visibly bent vegetation"
    elif windy:
        activity_cue = "coastal walking scene with visible moving vegetation and wind-aware posture"
    elif ctx.coastal_focus:
        activity_cue = "practical coastal promenade activity, relaxed but weather-aware"
    elif ctx.inland_heat_focus:
        activity_cue = "quiet shaded Nicosia street, sparse midday activity, practical heat avoidance"
    else:
        activity_cue = "subtle everyday Cyprus life, not object-focused"

    if visibility_visual:
        mood_cue = str(visibility_cues["mood"])
    elif ctx.post_type == "morning":
        mood_cue = "bright practical weather-for-the-day mood"
    else:
        mood_cue = "warm Mediterranean evening mood"
    if wet:
        mood_cue = "weather-alert, dramatic and practical; not a leisure beach scene"
    elif inland_unsettled:
        mood_cue = "warm coastal evening with practical awareness of inland weather changes"
    elif severe_wind:
        mood_cue = "wind-alert but dry Mediterranean evening mood"
    elif dusty and not visibility_visual:
        mood_cue += "; subdued by dusty haze"
    elif visibility_haze:
        mood_cue += "; softened by local humid haze"
    elif hot:
        mood_cue += "; sun-baked and heat-aware"

    must_show = ["recognizable Cyprus Mediterranean character"]
    if ctx.coastal_focus:
        must_show.extend(["Mediterranean coast", "Cyprus promenade or rocky shoreline", "palm trees"])
    if ctx.inland_heat_focus and not ctx.coastal_focus:
        must_show.extend(["dry Nicosia urban heat", "shade and sun-baked stone"])
    if wet:
        must_show.extend(["wet promenade surfaces", "dramatic rain clouds"])
    elif inland_unsettled:
        must_show.extend(
            [
                "distant inland cloud development toward the mountains",
                "convective cloud build-up toward Troodos/inland",
                "towering cumulus over inland hills",
                "clearer warm coastal foreground",
                "dry promenade and dry coastal surfaces",
            ]
        )
    elif severe_wind:
        must_show.extend(
            [
                "strongly textured Mediterranean water surface",
                "frequent small whitecaps",
                "visibly bent palm fronds and coastal grass",
                "dry promenade and dry coastal rocks",
            ]
        )
    if windy:
        must_show.extend(
            [
                "visible wind response in palm fronds and coastal grass",
                "coastal vegetation visibly leaning in gusts",
                "wind-ruffled sea with uneven texture",
                "textured Mediterranean water surface",
                "small wind-driven ripples",
            ]
        )
        if ctx.gust_max is not None and ctx.gust_max >= 12:
            must_show.append("occasional small whitecaps, not storm-scale")
    if visibility_visual:
        must_show.extend(list(visibility_cues["show"]))
    elif dusty:
        must_show.append("hazy muted beige-gold atmosphere")
    elif visibility_haze:
        must_show.append("soft humid haze and reduced distant visibility")
    if hot and not visibility_visual:
        must_show.append("heat shimmer")
    if ctx.post_type == "morning" and ctx.uv_level in {"high", "extreme"} and not visibility_visual:
        must_show.append("strong sunlight cue")

    must_avoid = [
        "Baltic Sea cues",
        "Kaliningrad or KLD references",
        "northern sea mood",
        "dunes or pine forest as the default landscape",
        "generic cold-climate coastline",
        "object-focused marina inventory",
    ]
    if ctx.post_type == "morning":
        must_avoid.extend(["sunset", "night", "moon-led scene"])
    if fog_visual:
        must_avoid.extend(
            [
                "crisp distant horizon",
                "perfectly clear horizon",
                "sharp postcard visibility",
                "completely transparent air",
            ]
        )
        must_avoid.append("dry dust-colored sky unless dust evidence exists")
    elif mixed_haze:
        must_avoid.extend(["exaggerated Sahara palette", "dense wall of fog"])
    elif ctx.visibility_condition == "reduced_visibility":
        must_avoid.extend(["invented humid fog", "invented wet atmosphere"])
    elif ctx.visibility_condition == "dust_haze":
        must_avoid.extend(["humid coastal fog", "moist fog depth"])
    if wet:
        must_avoid.extend(["beach leisure mood", "sunbathing", "carefree swimming scene"])
    elif inland_unsettled:
        must_avoid.extend(
            [
                "whole-coast storm scene",
                "coastal surfaces shown as rainy when coastal rain is absent",
                "fully stormy shoreline when only inland clouds are forecast",
                "perfect tourist calm",
                "ideal postcard sunset scene",
                "mirror-flat water",
            ]
        )
    elif severe_wind:
        must_avoid.extend(
            [
                "mirror-flat water",
                "completely still vegetation",
            ]
        )
    elif windy:
        must_avoid.extend(["mirror-flat water", "completely still vegetation", "perfect tourist calm"])
    if not ctx.inland_heat_focus and not inland_unsettled:
        must_avoid.append("Troodos or inland mountains without explicit relevance")

    return SceneCuesCY(
        base_scene=base_scene,
        sky_cue=sky_cue,
        light_cue=light_cue,
        sea_cue=sea_cue,
        air_cue=air_cue,
        activity_cue=activity_cue,
        mood_cue=mood_cue,
        must_show=must_show,
        must_avoid=must_avoid,
        diagnostics={
            "post_type": ctx.post_type,
            "weather_main": ctx.weather_main,
            "hot_rule": hot,
            "rain_rule": rain,
            "wet_rule": wet,
            "inland_unsettled_rule": inland_unsettled,
            "severe_wind_rule": severe_wind,
            "dust_rule": dusty,
            "visibility_haze_rule": visibility_haze,
            "visibility_condition": ctx.visibility_condition,
            "visibility_evidence": ctx.visibility_evidence,
            "visibility_visual_rule": visibility_visual,
            "fog_visual_rule": fog_visual,
            "dense_fog_rule": dense_fog,
            "fog_rule": ctx.visibility_condition == "fog",
            "mist_rule": ctx.visibility_condition == "mist",
            "reduced_visibility_rule": ctx.visibility_condition == "reduced_visibility",
            "dust_haze_rule": ctx.visibility_condition == "dust_haze",
            "mixed_visibility_rule": mixed_haze,
            "dust_vs_fog_classification": ctx.dust_vs_fog_classification,
            "wind_rule": windy,
            "coastal_focus": ctx.coastal_focus,
            "inland_heat_focus": ctx.inland_heat_focus,
            "sea_state_hint": ctx.sea_state_hint,
            "uv_level": ctx.uv_level,
        },
    )


__all__ = ["SceneCuesCY", "apply_visual_rules_cy"]
