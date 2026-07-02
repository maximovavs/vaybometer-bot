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


def apply_visual_rules_cy(ctx: VisualContextCY) -> SceneCuesCY:
    """Map parsed facts to Cyprus-specific scene cues."""
    if ctx.post_type not in {"morning", "evening"}:
        raise ValueError("ctx.post_type must be 'morning' or 'evening'")

    hot = _is_hot(ctx)
    rain = bool(ctx.actual_precipitation)
    severe_wind = bool(ctx.severe_wind)
    wet = rain
    dusty = ctx.weather_main == "dusty" or bool(ctx.dust_hint)
    visibility_haze = bool(ctx.visibility_haze) and not dusty
    windy = _is_windy(ctx)

    if ctx.coastal_focus:
        base_scene = "Cyprus Mediterranean coast with a Limassol or Larnaca promenade"
    elif ctx.inland_heat_focus:
        base_scene = "Cyprus dry urban inland scene in Nicosia"
    else:
        base_scene = "Cyprus Mediterranean coast with palms and local stone architecture"

    if wet:
        sky_cue = "dramatic rain clouds over Cyprus"
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
    else:
        sky_cue = "clear bright Mediterranean sky"

    if ctx.post_type == "morning":
        light_cue = "daylight Mediterranean morning, bright practical light for the day ahead"
    else:
        light_cue = "warm Mediterranean evening with golden or soft dusk light"

    if ctx.post_type == "morning" and ctx.uv_level in {"high", "extreme"}:
        light_cue += "; strong sun cue with crisp sunlit surfaces"
    if dusty:
        light_cue += "; muted filtered sun"
    if hot:
        light_cue += "; visible heat shimmer"

    if wet:
        sea_cue = "rain-darkened coast and a wet promenade; active unsettled sea"
    elif severe_wind:
        sea_cue = (
            "strongly textured Mediterranean water surface with frequent small whitecaps; "
            "visibly bent palm fronds and coastal grass; dry promenade and dry coastal rocks"
        )
    elif windy:
        sea_cue = (
            "textured Mediterranean water surface with small wind-driven ripples; "
            "visible wind response in palm fronds and coastal grass"
        )
        if ctx.gust_max is not None and ctx.gust_max >= 12:
            sea_cue += "; occasional small whitecaps"
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
    if dusty:
        air_parts.append("hazy beige-gold air with suspended dust")
    elif visibility_haze:
        air_parts.append("soft humid atmospheric depth with reduced distant visibility")
    elif ctx.humidity_hint in {"high", "present"}:
        air_parts.append("soft sea haze from humid coastal air")
    if hot:
        air_parts.append("dry hot air and heat shimmer")
    if ctx.aqi_level in {"poor", "very_poor"}:
        air_parts.append("reduced atmospheric clarity")
    air_cue = "; ".join(air_parts) if air_parts else "clear Mediterranean air"

    if wet:
        activity_cue = "sheltered pedestrians on a wet promenade; no beach leisure mood"
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

    if ctx.post_type == "morning":
        mood_cue = "bright practical weather-for-the-day mood"
    else:
        mood_cue = "warm Mediterranean evening mood"
    if wet:
        mood_cue = "weather-alert, dramatic and practical; not a leisure beach scene"
    elif severe_wind:
        mood_cue = "wind-alert but dry Mediterranean evening mood"
    elif dusty:
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
                "textured Mediterranean water surface",
                "small wind-driven ripples",
            ]
        )
        if ctx.gust_max is not None and ctx.gust_max >= 12:
            must_show.append("occasional small whitecaps")
    if dusty:
        must_show.append("hazy muted beige-gold atmosphere")
    elif visibility_haze:
        must_show.append("soft humid haze and reduced distant visibility")
    if hot:
        must_show.append("heat shimmer")
    if ctx.post_type == "morning" and ctx.uv_level in {"high", "extreme"}:
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
    if wet:
        must_avoid.extend(["beach leisure mood", "sunbathing", "carefree swimming scene"])
    elif severe_wind:
        must_avoid.extend(
            [
                "mirror-flat water",
                "completely still vegetation",
            ]
        )
    elif windy:
        must_avoid.extend(["mirror-flat water", "completely still vegetation"])
    if not ctx.inland_heat_focus:
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
            "severe_wind_rule": severe_wind,
            "dust_rule": dusty,
            "visibility_haze_rule": visibility_haze,
            "wind_rule": windy,
            "coastal_focus": ctx.coastal_focus,
            "inland_heat_focus": ctx.inland_heat_focus,
            "sea_state_hint": ctx.sea_state_hint,
            "uv_level": ctx.uv_level,
        },
    )


__all__ = ["SceneCuesCY", "apply_visual_rules_cy"]
