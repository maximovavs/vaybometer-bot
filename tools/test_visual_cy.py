#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synthetic, offline checks for the Cyprus visual context/rules pipeline."""

from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visual_context_cy import parse_visual_context_cy
from visual_rules_cy import apply_visual_rules_cy
from image_prompt_cy_scene import (
    CYPRUS_VISUAL_PROMPT_VERSION,
    build_cyprus_scene_prompt,
    build_cyprus_scene_prompt_with_metadata,
    build_cyprus_visual_cache_key,
)
from format_v2 import build_evening_format_v2


def _macro_scene_cue(prompt: str) -> str:
    match = re.search(r"dominant macro scene variant: ([^;]+)", prompt, flags=re.I)
    assert match is not None
    return match.group(1).lower()


def _all_cues(scene) -> str:
    values = [
        scene.base_scene,
        scene.sky_cue,
        scene.light_cue,
        scene.sea_cue,
        scene.air_cue,
        scene.activity_cue,
        scene.mood_cue,
        *scene.must_show,
        *scene.must_avoid,
    ]
    return " ".join(values).lower()


def cy_morning_clear_high_uv() -> None:
    text = """
    <b>Кипр: погода на сегодня</b>
    👋 Доброе утро!
    ☀️ Ясно и жарко, Лимассол +34°, Никосия +37°.
    УФ-индекс 9 — высокий, солнце очень активное.
    Море у Ларнаки 27°, на побережье спокойно.
    """
    ctx = parse_visual_context_cy(text)
    scene = apply_visual_rules_cy(ctx)
    assert ctx.weather_main in {"clear", "hot"}
    assert ctx.uv_level == "high"
    assert "daylight mediterranean morning" in scene.light_cue.lower()
    assert "strong sun cue" in scene.light_cue.lower()


def cy_morning_dust_haze() -> None:
    text = """
    Доброе утро, Кипр. Сегодня сухо.
    Пыль из Сахары и заметная дымка, AQI 112.
    Лимассол: +30°, у воды влажность 72%.
    """
    ctx = parse_visual_context_cy(text)
    scene = apply_visual_rules_cy(ctx)
    assert ctx.weather_main == "dusty"
    assert ctx.dust_hint
    assert "hazy muted sky" in scene.sky_cue.lower()
    assert "muted filtered sun" in scene.light_cue.lower()


def cy_evening_hot_coast() -> None:
    text = """
    <b>Кипр: погода на завтра</b>
    Вечером готовимся к жаркому дню: Лимассол +35°, Ларнака +36°.
    На побережье и у моря слабый бриз, вода +28°.
    """
    ctx = parse_visual_context_cy(text)
    scene = apply_visual_rules_cy(ctx)
    assert ctx.post_type == "evening"
    assert ctx.coastal_focus is True
    assert "restrained twilight light" in scene.light_cue.lower()
    assert "heat shimmer" in _all_cues(scene)


def cy_evening_rain() -> None:
    text = """
    Кипр: прогноз на завтра.
    Вечером: дождь в Пафосе и Лимассоле, местами гроза и сильные порывы 14 м/с.
    На побережье мокро, море неспокойное.
    """
    ctx = parse_visual_context_cy(text)
    scene = apply_visual_rules_cy(ctx)
    assert ctx.weather_main in {"rain", "storm"}
    assert "wet promenade" in _all_cues(scene)
    assert "no beach leisure mood" in scene.activity_cue.lower()


def cy_inland_heat_nicosia() -> None:
    text = """
    Кипр: погода на завтра.
    Никосия: жара до +39°, сухо и без ветра.
    """
    ctx = parse_visual_context_cy(text)
    scene = apply_visual_rules_cy(ctx)
    assert ctx.inland_heat_focus is True
    assert "dry urban inland" in scene.base_scene.lower()
    assert "nicosia" in _all_cues(scene)


def cy_coastal_wind() -> None:
    text = """
    Доброе утро. Ларнака и Айя-Напа: +27°.
    На побережье ветер 8 м/с, порывы до 12 м/с, у воды свежо.
    Море с умеренной волной.
    """
    ctx = parse_visual_context_cy(text)
    scene = apply_visual_rules_cy(ctx)
    assert ctx.coastal_focus is True
    assert ctx.wind_max is not None and ctx.wind_max >= 8
    cues = _all_cues(scene)
    assert "textured mediterranean water surface" in cues
    assert "visible wind response in palm fronds and coastal grass" in cues
    assert "occasional small whitecaps" in cues


def cy_visual_negated_storm_phrase_is_not_storm() -> None:
    text = """
    Кипр: прогноз на завтра.
    Штормовых предупреждений нет, риск шторма низкий.
    Ларнака: ясно, ветер 5 м/с, порывы до 8 м/с.
    """
    ctx = parse_visual_context_cy(text, post_type="evening")
    assert ctx.weather_main != "storm"


def cy_visual_gust_17_without_storm_word_is_storm() -> None:
    text = """
    Кипр: прогноз на завтра.
    Лимассол: ясно, ветер 7 м/с, порывы до 17 м/с.
    """
    ctx = parse_visual_context_cy(text, post_type="evening")
    scene = apply_visual_rules_cy(ctx)
    assert ctx.weather_main == "storm"
    assert ctx.severe_wind is True
    assert ctx.actual_precipitation is False
    assert scene.diagnostics["severe_wind_rule"] is True
    assert scene.diagnostics["wet_rule"] is False


def cy_visual_gust_17_with_rain_is_wet_and_severe() -> None:
    text = """
    Кипр: прогноз на завтра.
    Лимассол: местами дождь, ветер 7 м/с, порывы до 17 м/с.
    На побережье мокро.
    """
    ctx = parse_visual_context_cy(text, post_type="evening")
    scene = apply_visual_rules_cy(ctx)
    assert ctx.severe_wind is True
    assert ctx.actual_precipitation is True
    assert scene.diagnostics["severe_wind_rule"] is True
    assert scene.diagnostics["wet_rule"] is True


def cy_visual_precipitation_uncertainty_is_not_rain() -> None:
    false_cases = (
        "осадки не ожидаются",
        "без осадков",
        "дождя не будет",
        "вероятность осадков низкая",
        "ветер/осадки лучше проверить утром",
        "осадки уточнить утром",
        "проверить осадки перед поездкой",
        "осадки и порывы требуют гибкого плана",
        "✨ VayboMeter завтра: 6.4/10 — с оговорками; осадки и порывы требуют гибкого плана.",
        "🎯 Уверенность: температура высокая; ветер/осадки лучше проверить утром.",
    )
    for phrase in false_cases:
        ctx = parse_visual_context_cy("Кипр завтра.\n" + phrase, post_type="evening")
        assert ctx.actual_precipitation is False, phrase

    factual = parse_visual_context_cy("Кипр завтра.\nЛимассол: местами дождь, ветер 4 м/с.", post_type="evening")
    assert factual.actual_precipitation is True


def cy_visual_ordinary_haze_is_not_dust() -> None:
    text = """
    Кипр: прогноз на завтра.
    Ларнака: локальная утренняя дымка/туман, AQI 40, море у побережья.
    """
    ctx = parse_visual_context_cy(text, post_type="evening")
    scene = apply_visual_rules_cy(ctx)
    cues = _all_cues(scene)
    assert ctx.visibility_haze is True
    assert not ctx.dust_hint
    assert scene.diagnostics["dust_rule"] is False
    assert "soft humid" in cues
    assert "beige-gold" not in cues
    assert "suspended dust" not in cues


def cy_visual_dust_haze_is_dust() -> None:
    text = """
    Кипр: прогноз на завтра.
    Ларнака: пылевая дымка, AQI 110, море у побережья.
    """
    ctx = parse_visual_context_cy(text, post_type="evening")
    scene = apply_visual_rules_cy(ctx)
    cues = _all_cues(scene)
    assert ctx.dust_hint
    assert scene.diagnostics["dust_rule"] is True
    assert "beige-gold" in cues or "suspended dust" in cues


def cy_no_baltic_leak() -> None:
    text = """
    Доброе утро, Кипр. Лимассол +29°, ясно.
    Море спокойное, лёгкий ветер 3 м/с.
    """
    scene = apply_visual_rules_cy(parse_visual_context_cy(text))
    positive_cues = " ".join(
        [
            scene.base_scene,
            scene.sky_cue,
            scene.light_cue,
            scene.sea_cue,
            scene.air_cue,
            scene.activity_cue,
            scene.mood_cue,
            *scene.must_show,
        ]
    ).lower()
    forbidden = ("baltic", "kaliningrad", "kld")
    assert not any(word in positive_cues for word in forbidden)


def cy_prompt_morning_sanitized() -> None:
    message = """
    <b>Кипр: погода на сегодня</b>
    Доброе утро. Лимассол +32°, ясно, УФ-индекс 9.
    Море спокойное, на побережье лёгкая дымка.
    Moon poster weather card with logo, text and Baltic sunset.
    """
    prompt, style = build_cyprus_scene_prompt(message, post_type="morning")
    low = prompt.lower()
    assert "mediterranean" in low
    assert "daylight" in low
    assert "fresh morning daylight" in low
    assert "neutral daylight" in low
    assert "pale blue sky" in low
    assert "cool fresh morning atmosphere" in low
    assert "soft neutral sunlight" in low
    assert "sun from left" in low
    assert "light direction from left" in low
    assert "left side of frame" in low
    assert "no visible sun disk" in low
    assert "no bright illumination from the right side of frame" in low
    assert "no warm low-angle glow" in low
    assert "crisp daytime visibility" in low
    assert "natural daytime shadows" in low
    assert "clear early morning daylight" in low
    assert "weather card" not in low
    assert "baltic sunset" not in low
    assert "no text" in low
    assert "no watermark" in low
    assert "no logo" in low
    assert "no poster" in low
    assert "no painting" in low
    assert "no illustration" in low
    assert "no digital art" in low
    assert "no watercolor" in low
    assert "no fantasy landscape" in low
    assert "no sunset" in low
    assert "no golden hour" in low
    assert "no orange horizon" in low
    assert "no low sun on the right" in low
    assert "no evening glow" in low
    assert "no dusk" in low
    for forbidden in ("sunrise", "baltic", "kaliningrad"):
        assert not re.search(rf"\b{forbidden}\b", low)
    assert style.startswith("cyprus_morning_mediterranean_landscape_")
    assert re.search(r"_[0-9a-f]{8}$", style)


def cy_prompt_evening_dust_heat() -> None:
    message = """
    Кипр: прогноз на завтра.
    Никосия: жара до +39°, сухой воздух.
    Ларнака и Лимассол: пыль, дымка, AQI 118, у моря +35°.
    """
    prompt, style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "dust" in low or "haze" in low
    assert "heat shimmer" in low
    assert "baltic" not in low
    assert "kaliningrad" not in low
    assert style.startswith("cyprus_evening_mediterranean_landscape_")
    assert re.search(r"_[0-9a-f]{8}$", style)


def cy_prompt_rain_not_leisure() -> None:
    message = """
    Кипр: прогноз на завтра.
    Пафос и Лимассол: дождь, местами гроза, порывы 13 м/с.
    На побережье мокро, море неспокойное.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "wet promenade" in low
    assert "dramatic rain clouds" in low
    assert "practical rain mood" in low
    for forbidden in ("beach leisure", "party", "vacation", "carefree swimming"):
        assert forbidden not in low
    assert "no poster" in low


def cy_prompt_generic_warning_gust_10_is_windy_not_storm() -> None:
    message = """
    27.06.2026
    Кипр завтра.
    ⚠️ Предупреждение: высокий УФ.
    Ларнака: ясно, ветер 6 м/с, порывы до 10 м/с, море у побережья.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "visible wind response in palm fronds and coastal grass" in low
    assert "textured mediterranean water surface" in low
    assert "small wind-driven ripples" in low
    assert "dramatic rain clouds" not in low
    assert "storm" not in low


def cy_prompt_gust_13_has_whitecaps_without_flat_water() -> None:
    message = """
    27.06.2026
    Кипр завтра.
    Лимассол: ясно, ветер 7 м/с, порывы до 13 м/с, море у побережья.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "textured mediterranean water surface" in low
    assert "visible wind response in palm fronds and coastal grass" in low
    assert "occasional small whitecaps" in low
    assert "no mirror-flat water" in low
    assert "no perfect tourist calm" in low
    assert "no completely still vegetation" in low


def cy_prompt_dry_gust_17_does_not_create_rain() -> None:
    message = """
    27.06.2026
    Кипр завтра.
    Лимассол: ясно, ветер 7 м/с, порывы до 17 м/с, море у побережья.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "strongly textured mediterranean water surface" in low
    assert "frequent small whitecaps" in low
    assert "visibly bent palm fronds and coastal grass" in low
    assert "dry promenade" in low
    assert "dry coastal" in low
    assert "dramatic rain clouds" not in low
    assert "wet promenade" not in low
    assert "rain-darkened coast" not in low


def cy_prompt_evening_high_uv_has_no_direct_sun_cue() -> None:
    message = """
    27.06.2026
    Кипр завтра.
    Ларнака: тепло, УФ-индекс 9, море спокойное.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "strong direct sunlight" not in low
    assert "strong sun cue" not in low
    assert "strong sunlight cue" not in low


def cy_prompt_morning_high_uv_keeps_direct_sun_cue() -> None:
    message = """
    27.06.2026
    Кипр сегодня.
    Ларнака: ясно, УФ-индекс 9, море спокойное.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="morning")
    low = prompt.lower()
    assert "strong direct sunlight with crisp daylight contrast" in low


def cy_prompt_real_evening_wind_moon_haze_has_no_rain_or_dust_contradictions() -> None:
    message = """
    27.06.2026
    Кипр завтра.
    Никосия: жара до 37°, УФ-индекс 9.
    Ларнака: локальная утренняя дымка/туман, AQI 40, PM₂.₅ 8 / PM₁₀ 14.
    Лимассол: ясно, ветер 7 м/с, порывы до 17 м/с, море у побережья.
    🌖 Убывающая Луна в ♐ — мягкий вечерний ритм.
    ✨ 92% освещённости — Луна яркая.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "realistic waning gibbous moon, 92% illuminated" in low
    assert "blue-hour or late twilight" in low
    assert "visible wind response in palm fronds and coastal grass" in low
    assert "textured mediterranean water surface" in low
    assert "frequent small whitecaps" in low
    assert "dry promenade" in low
    assert "dry coastal" in low
    assert "dramatic rain clouds" not in low
    assert "wet promenade" not in low
    assert "rain-darkened coast" not in low
    assert "strong direct sunlight" not in low
    assert "beige-gold dust" not in low
    assert "suspended dust" not in low
    assert "no perfect full moon" in low
    assert "no oversized moon" in low
    assert "no fantasy supermoon" in low
    assert "natural-scale moon only" in low


DRY_SEVERE_WIND_SOURCE = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 7.2/10 — хорошо; сильная жара, порывы у моря.
🏖 <b>Морские города</b>
Лимассол: 34/25 °C • ясно • 💨 7 м/с • порывы до 17 м/с
Ларнака: 33/24 °C • локальная утренняя дымка/туман • 💨 6 м/с • порывы до 12 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 37/25 °C • ясно • УФ-индекс 9
———
🏭 Воздух: AQI 40 (низкий) • PM₂.₅ 8 / PM₁₀ 14
🌅 Рассвет завтра: 05:37
🌖 Убывающая Луна в ♐ — мягкий вечерний ритм.
✨ 92% освещённости — Луна яркая.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""


WET_SEVERE_WIND_SOURCE = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 6.8/10 — рабочий день; порывы у моря.
🏖 <b>Морские города</b>
Лимассол: 30/24 °C • местами дождь • 💨 7 м/с • порывы до 17 м/с
Ларнака: 31/24 °C • ясно • 💨 6 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 34/24 °C • ясно
———
🌅 Рассвет завтра: 05:37
🌖 Убывающая Луна в ♐ — мягкий вечерний ритм.
✨ 92% освещённости — Луна яркая.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""


def cy_prompt_format_v2_dry_severe_wind_advice_does_not_create_rain() -> None:
    final_text = build_evening_format_v2("Кипр", DRY_SEVERE_WIND_SOURCE)
    assert "порывы лучше перепроверить утром" in final_text
    prompt, _style = build_cyprus_scene_prompt(final_text, post_type="evening")
    low = prompt.lower()
    assert "strong dry coastal wind response" in low
    assert "frequent small whitecaps" in low
    assert "visibly bent palm fronds and coastal grass" in low
    assert "dry promenade" in low
    assert "realistic waning gibbous moon, 92% illuminated" in low
    assert "dramatic rain clouds" not in low
    assert "wet promenade" not in low
    assert "rain-darkened coast" not in low


LOCAL_MOUNTAIN_THUNDER_SOURCE = """<b>🌅 Кипр: погода на завтра (27.06.2026)</b>
✨ VayboMeter завтра: 8.1/10 — хорошо для обычных дел.
🏖 <b>Морские города</b>
Ларнака: 30/25 °C • переменная облачность • 💨 7 м/с • порывы до 12 м/с
Лимассол: 30/23 °C • переменная облачность • 💨 8 м/с • порывы до 11 м/с
Айя-Напа: 29/25 °C • облачно с прояснениями • 💨 6 м/с • порывы до 10 м/с
Пафос: 28/24 °C • переменная облачность • 💨 6 м/с • порывы до 9 м/с
———
🏞 <b>Континентальные города</b>
Никосия: 33/24 °C • жарко
Тродос: 25/18 °C • возможна гроза в горах
———
🏭 Воздух: AQI 48 (низкий) • PM₂.₅ 12 / PM₁₀ 19
🌅 Рассвет завтра: 05:37
🌇 Закат завтра: 20:05
🌖 Убывающая Луна в ♐ — мягкий вечерний ритм.
✨ 92% освещённости — Луна яркая.
💚 В плюсе: планы.
#Кипр #погода #здоровье #Никосия #Тродос
"""


def cy_prompt_local_mountain_thunder_keeps_coast_dry_and_windy() -> None:
    final_text = build_evening_format_v2("Кипр", LOCAL_MOUNTAIN_THUNDER_SOURCE)
    prompt, _style, meta = build_cyprus_scene_prompt_with_metadata(final_text, post_type="evening")
    low = prompt.lower()
    assert meta["cloud_haze_category"] == "inland_cloud_development"
    assert "distant inland cloud development" in low
    assert "convective cloud build-up toward troodos/inland" in low
    assert "towering cumulus over inland hills" in low
    assert "cloud towers over inland hills" in low
    assert "wind-ruffled sea with uneven texture" in low
    assert "textured mediterranean water surface" in low
    assert "visible wind response in palm fronds and coastal grass" in low
    assert "occasional small whitecaps" in low
    assert "dry coastal surfaces" in low
    assert "no perfect tourist calm" in low
    assert "no ideal postcard sunset scene" in low
    assert "dramatic rain clouds" not in low
    assert "wet promenade" not in low
    assert "rain-darkened coast" not in low
    assert "whole-coast storm scene" in low


def cy_prompt_small_harbour_scene_has_harbour_logic() -> None:
    scenario = """
    2026-07-09
    Кипр завтра.
    Лимассол и Ларнака: тёплый вечер у моря, ветер 6 м/с, порывы до 10 м/с.
    🌙 Растущая Луна в ♐.
    """
    selected: tuple[str, str, dict[str, str]] | None = None
    for attempt in range(20):
        prompt, style, meta = build_cyprus_scene_prompt_with_metadata(
            scenario,
            post_type="evening",
            variation_attempt=attempt,
        )
        if meta["selected_scene"] == "small_harbour":
            selected = (prompt, style, meta)
            break
    assert selected is not None
    prompt, _style, meta = selected
    low = prompt.lower()
    assert meta["selected_scene"] == "small_harbour"
    assert "protected harbour basin" in low
    assert "harbour edge as main motif" in low
    assert "mooring posts" in low
    assert "low coastal human structure" in low
    assert "not a generic cliff bay" in low


def cy_prompt_format_v2_wet_severe_wind_keeps_rain_visual() -> None:
    final_text = build_evening_format_v2("Кипр", WET_SEVERE_WIND_SOURCE)
    prompt, _style = build_cyprus_scene_prompt(final_text, post_type="evening")
    low = prompt.lower()
    assert "dramatic rain clouds" in low
    assert "wet promenade" in low
    assert "rain-darkened coast" in low


def cy_prompt_no_raw_source_hints() -> None:
    message = """
    <b>Кипр: погода на завтра</b>
    Ларнака +34°, Никосия +38°.
    Море у Ларнаки +28°, вода спокойная, на побережье солнечно.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="evening")
    assert "source" not in prompt.lower()
    assert "°" not in prompt
    assert not re.search(r"[\u0400-\u04FF]", prompt)
    assert "<b>" not in prompt.lower()


def cy_prompt_coastal_priority_over_nicosia() -> None:
    message = """
    Кипр: прогноз на завтра.
    Лимассол +34°, Ларнака +35°, Никосия +39°.
    Море у Ларнаки +28°, вода спокойная, на побережье жарко.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "mediterranean coast" in low or "coastal" in low
    assert "nicosia inland" not in low


def cy_prompt_inland_only_when_no_coast() -> None:
    message = """
    Кипр: прогноз на завтра.
    Никосия: жара до +40°, сухой воздух, УФ-индекс 10.
    Ветер 3 м/с, порывы до 6 м/с.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "nicosia" in low
    assert "inland" in low
    assert "uninterrupted sea and coast" not in low
    assert "mediterranean coast" not in low
    assert "coastal promenade" not in low


def cy_prompt_controlled_variety_is_stable() -> None:
    message = """
    19.06.2026
    Кипр: прогноз на завтра.
    Лимассол +34°, Ларнака +35°.
    Море спокойное, на побережье солнечно и жарко.
    """
    prompt_a, _ = build_cyprus_scene_prompt(message, post_type="evening")
    prompt_b, _ = build_cyprus_scene_prompt(message, post_type="evening")
    assert prompt_a == prompt_b
    assert "dominant macro scene variant" in prompt_a.lower()
    assert "controlled foreground variant" in prompt_a.lower()
    assert "controlled composition variant" in prompt_a.lower()
    assert "heat shimmer" in prompt_a.lower()
    assert "baltic" not in prompt_a.lower()


def cy_prompt_morning_evening_same_date_differ() -> None:
    message = """
    20.06.2026
    Кипр: прогноз.
    Лимассол +34°, Ларнака +35°.
    Море спокойное, на побережье солнечно и жарко.
    """
    morning, morning_style = build_cyprus_scene_prompt(message, post_type="morning")
    evening, evening_style = build_cyprus_scene_prompt(message, post_type="evening")
    assert morning != evening
    assert morning_style != evening_style
    assert "daylight" in morning.lower()
    assert "pale blue sky" in morning.lower()
    assert "late-day" in evening.lower() or "twilight" in evening.lower()
    assert "restrained twilight color" in evening.lower()
    assert "no mandatory visible sun disk" in evening.lower()
    assert "default postcard golden sunset" in evening.lower()
    assert _macro_scene_cue(morning) != _macro_scene_cue(evening)


def cy_prompt_adjacent_dates_change_macro_viewpoint() -> None:
    scenario = """
    Кипр: прогноз на завтра.
    Лимассол +34°, Ларнака +35°.
    Море спокойное, на побережье солнечно и жарко.
    """
    prompt_a, _ = build_cyprus_scene_prompt("20.06.2026\n" + scenario, post_type="morning")
    prompt_b, _ = build_cyprus_scene_prompt("21.06.2026\n" + scenario, post_type="morning")
    assert _macro_scene_cue(prompt_a) != _macro_scene_cue(prompt_b)
    assert prompt_a != prompt_b


def cy_prompt_controlled_variety_changes_by_date() -> None:
    scenario = """
    Кипр: прогноз на завтра.
    Пафос и Лимассол: дождь, местами гроза, порывы 13 м/с.
    На побережье мокро, море неспокойное.
    """
    prompt_a, _ = build_cyprus_scene_prompt("19.06.2026\n" + scenario, post_type="evening")
    prompt_b, _ = build_cyprus_scene_prompt("20.06.2026\n" + scenario, post_type="evening")
    assert prompt_a != prompt_b
    for prompt in (prompt_a, prompt_b):
        low = prompt.lower()
        assert "wet promenade" in low
        assert "dramatic rain clouds" in low
        assert "practical rain mood" in low
        assert "baltic" not in low

    morning, _ = build_cyprus_scene_prompt("20.06.2026\n" + scenario, post_type="morning")
    low = morning.lower()
    for forbidden in ("lunar", "crescent", "night"):
        assert not re.search(rf"\b{forbidden}\b", low)
    assert "no sunset" in low
    assert "no evening glow" in low


def cy_prompt_full_moon_evening_uses_blue_hour_moonlight() -> None:
    message = """
    27.06.2026
    Кипр завтра.
    Лимассол и Ларнака: тепло, море спокойное.
    🌕 Полнолуние в ♑ — пик эмоций и результатов.
    ✨ 100% освещённости — Луна яркая.
    """
    prompt, style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "visible realistic full moon" in low
    assert "soft moonlit water" in low
    assert "blue-hour" in low
    assert "residual warm horizon glow" in low
    assert "right side of frame" in low
    assert "soft golden dusk light" not in low
    assert "not a sun-dominant scene" in low
    assert "no bright golden sunset" in low
    assert "no oversized moon" in low
    assert "no fantasy planet" in low
    assert "no fantasy supermoon" in low
    assert "no visible text anywhere" in low
    assert "no pseudo-caption" in low
    assert "no watermark" in low
    assert "no artist signature" in low
    assert "baltic" not in low
    assert style.startswith("cyprus_evening_mediterranean_landscape_")


def cy_prompt_waning_92_uses_near_full_moon_context() -> None:
    message = """
    27.06.2026
    Кипр завтра.
    Лимассол и Ларнака: тепло, море спокойное.
    🌖 Убывающая Луна в ♐ — мягкий вечерний ритм.
    ✨ 92% освещённости — Луна яркая.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "realistic waning gibbous moon, 92% illuminated" in low
    assert "blue-hour or late twilight" in low
    assert "residual right-side horizon glow" in low
    assert "no perfect full moon" in low
    assert "no oversized moon" in low
    assert "no fantasy supermoon" in low
    assert "natural-scale moon only" in low


def cy_visual_cache_key_contains_identity_fields() -> None:
    scenario = """
    Кипр: прогноз.
    Лимассол +34°, Ларнака +35°.
    Море спокойно, ветер 6 м/с, порывы до 10 м/с.
    🌖 Убывающая Луна в ♐.
    ✨ 92% освещённости.
    """
    key_a = build_cyprus_visual_cache_key(
        "2026-07-04\n" + scenario,
        post_type="evening",
    )
    key_b = build_cyprus_visual_cache_key(
        "2026-07-05\n" + scenario,
        post_type="evening",
    )
    assert key_a != key_b
    assert "forecast_date=2026-07-04" in key_a
    assert "forecast_date=2026-07-05" in key_b
    assert "post_type=evening" in key_a
    assert "target_date=tomorrow" in key_a
    assert f"prompt_version={CYPRUS_VISUAL_PROMPT_VERSION}" in key_a
    assert "selected_scene=" in key_a
    assert "weather_scenario=" in key_a
    assert "wind_gust_category=" in key_a
    assert "cloud_haze_category=" in key_a
    assert "lunar_phase=waning_near_full" in key_a
    assert "lunar_illumination=92" in key_a


def cy_scene_rotation_week_has_no_obvious_repeats() -> None:
    scenario = """
    Кипр: прогноз.
    Лимассол +34°, Ларнака +35°.
    Море спокойно, на побережье солнечно и жарко.
    """
    families: list[str] = []
    morning_compositions: list[str] = []
    previous_morning = ""
    previous_evening = ""
    for offset in range(7):
        day = (date(2026, 7, 1) + timedelta(days=offset)).isoformat()
        morning_prompt, _ms, morning_meta = build_cyprus_scene_prompt_with_metadata(
            day + "\n" + scenario,
            post_type="morning",
        )
        _ep, _es, evening_meta = build_cyprus_scene_prompt_with_metadata(
            day + "\n" + scenario,
            post_type="evening",
        )
        morning_scene = morning_meta["selected_scene"]
        evening_scene = evening_meta["selected_scene"]
        assert morning_scene != evening_scene
        if previous_morning:
            assert morning_scene != previous_morning
        if previous_evening:
            assert evening_scene != previous_evening
        families.extend([morning_scene, evening_scene])
        composition = re.search(
            r"controlled composition variant: ([^;]+)",
            morning_prompt,
            flags=re.I,
        )
        assert composition is not None
        morning_compositions.append(composition.group(1).lower())
        previous_morning = morning_scene
        previous_evening = evening_scene
    assert len(set(families)) >= 5
    for index, composition in enumerate(morning_compositions):
        assert composition not in morning_compositions[max(0, index - 5):index]


def cy_scene_retry_rotates_scene_family() -> None:
    scenario = """
    2026-07-05
    Кипр: прогноз.
    Лимассол +34°, Ларнака +35°.
    Море спокойно, на побережье солнечно и жарко.
    """
    _p0, _s0, meta0 = build_cyprus_scene_prompt_with_metadata(
        scenario,
        post_type="evening",
        variation_attempt=0,
    )
    _p1, _s1, meta1 = build_cyprus_scene_prompt_with_metadata(
        scenario,
        post_type="evening",
        variation_attempt=1,
    )
    assert meta0["selected_scene"] != meta1["selected_scene"]
    assert meta0["cache_key"] != meta1["cache_key"]


def cy_prompt_cloudy_uses_cloud_cover_without_blazing_sun() -> None:
    message = """
    27.06.2026
    Кипр сегодня.
    Ларнака: облачно, ветер 4 м/с, море у побережья.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="morning")
    low = prompt.lower()
    assert "layered mediterranean cloud cover" in low
    assert "blazing" not in low
    assert "strong direct sunlight" not in low


def cy_scene_strong_gusts_use_exposed_coast_family() -> None:
    text = """
    Добрый вечер, Кипр. 2026-07-21
    Лимассол: ясно, ветер 7 м/с, порывы до 13 м/с.
    🌊 Море: волна умеренная.
    """
    allowed = {"windy_exposed_coast", "breakwater_coast", "open_sea_cliffs"}
    for attempt in range(5):
        _prompt, _style, meta = build_cyprus_scene_prompt_with_metadata(
            text,
            post_type="evening",
            variation_attempt=attempt,
        )
        assert meta["selected_scene"] in allowed


def cy_scene_plain_haze_prefers_visibility_friendly_coast() -> None:
    text = """
    Добрый вечер, Кипр. 2026-07-22
    Ларнака: утром местами дымка/туман, AQI 40, PM₂.₅ 8, PM₁₀ 14.
    🌊 Море: спокойно.
    """
    allowed = {"coastal_promenade", "small_harbour"}
    for attempt in range(4):
        prompt, _style, meta = build_cyprus_scene_prompt_with_metadata(
            text,
            post_type="evening",
            variation_attempt=attempt,
        )
        assert meta["selected_scene"] in allowed
        assert "dust haze with muted beige-gold" not in prompt


TESTS = [
    cy_morning_clear_high_uv,
    cy_morning_dust_haze,
    cy_evening_hot_coast,
    cy_evening_rain,
    cy_inland_heat_nicosia,
    cy_coastal_wind,
    cy_visual_negated_storm_phrase_is_not_storm,
    cy_visual_gust_17_without_storm_word_is_storm,
    cy_visual_gust_17_with_rain_is_wet_and_severe,
    cy_visual_precipitation_uncertainty_is_not_rain,
    cy_visual_ordinary_haze_is_not_dust,
    cy_visual_dust_haze_is_dust,
    cy_no_baltic_leak,
    cy_prompt_morning_sanitized,
    cy_prompt_evening_dust_heat,
    cy_prompt_rain_not_leisure,
    cy_prompt_generic_warning_gust_10_is_windy_not_storm,
    cy_prompt_gust_13_has_whitecaps_without_flat_water,
    cy_prompt_dry_gust_17_does_not_create_rain,
    cy_prompt_evening_high_uv_has_no_direct_sun_cue,
    cy_prompt_morning_high_uv_keeps_direct_sun_cue,
    cy_prompt_real_evening_wind_moon_haze_has_no_rain_or_dust_contradictions,
    cy_prompt_format_v2_dry_severe_wind_advice_does_not_create_rain,
    cy_prompt_local_mountain_thunder_keeps_coast_dry_and_windy,
    cy_prompt_small_harbour_scene_has_harbour_logic,
    cy_prompt_format_v2_wet_severe_wind_keeps_rain_visual,
    cy_prompt_no_raw_source_hints,
    cy_prompt_coastal_priority_over_nicosia,
    cy_prompt_inland_only_when_no_coast,
    cy_prompt_controlled_variety_is_stable,
    cy_prompt_morning_evening_same_date_differ,
    cy_prompt_adjacent_dates_change_macro_viewpoint,
    cy_prompt_controlled_variety_changes_by_date,
    cy_prompt_full_moon_evening_uses_blue_hour_moonlight,
    cy_prompt_waning_92_uses_near_full_moon_context,
    cy_visual_cache_key_contains_identity_fields,
    cy_scene_rotation_week_has_no_obvious_repeats,
    cy_scene_retry_rotates_scene_family,
    cy_prompt_cloudy_uses_cloud_cover_without_blazing_sun,
    cy_scene_strong_gusts_use_exposed_coast_family,
    cy_scene_plain_haze_prefers_visibility_friendly_coast,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} Cyprus synthetic visual checks passed")


if __name__ == "__main__":
    main()
