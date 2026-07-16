#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synthetic, offline checks for the Cyprus visual context/rules pipeline."""

from __future__ import annotations

from pathlib import Path
from datetime import date, timedelta
import os
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visual_context_cy import parse_visual_context_cy
from visual_rules_cy import apply_visual_rules_cy
from image_prompt_cy_scene import (
    CYPRUS_VISUAL_PROMPT_VERSION,
    _CY_COASTAL_COMPOSITIONS,
    _CY_SCENE_COMPOSITIONS,
    _dedupe_semantic_items,
    build_cyprus_scene_prompt,
    build_cyprus_scene_prompt_with_metadata,
    build_cyprus_visual_cache_key,
)
from format_v2 import build_evening_format_v2


def _macro_scene_cue(prompt: str) -> str:
    positive, _negative = _prompt_sections(prompt)
    clauses = [part.strip() for part in positive.split(";") if part.strip()]
    assert len(clauses) >= 2
    assert clauses[1].lower().startswith("dominant ")
    return clauses[1].lower()


def _prompt_sections(prompt: str) -> tuple[str, str]:
    assert prompt.count(". Avoid: ") == 1
    return tuple(prompt.split(". Avoid: ", 1))  # type: ignore[return-value]


def _assert_compact_prompt_contract(prompt: str, metadata: dict[str, object]) -> None:
    positive, negative = _prompt_sections(prompt)
    positive_clauses = [part.strip(" .") for part in positive.split(";") if part.strip()]
    negative_items = [part.strip(" .") for part in negative.split(";") if part.strip(" .")]
    assert 450 <= len(prompt) <= 900
    assert len(prompt) <= 1200
    assert len(positive_clauses) <= 8
    assert len(negative_items) <= 10
    assert positive_clauses == _dedupe_semantic_items(positive_clauses)
    assert negative_items == _dedupe_semantic_items(negative_items)
    assert positive_clauses[0].lower().startswith("photorealistic")
    assert positive_clauses[1].lower().startswith("dominant ")
    assert metadata["prompt_length_chars"] == len(prompt)
    assert metadata["positive_clause_count"] == len(positive_clauses)
    assert metadata["negative_item_count"] == len(negative_items)
    assert int(metadata["pollinations_encoded_url_length"]) <= 3500


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
    prompt, style, metadata = build_cyprus_scene_prompt_with_metadata(message, post_type="morning")
    low = prompt.lower()
    positive, negative = _prompt_sections(prompt)
    assert "mediterranean" in low
    assert "daylight" in low
    assert "fresh neutral morning daylight" in low
    assert "neutral morning daylight" in low
    assert "pale blue sky" in low
    assert "light from the left" in low
    assert "natural shadows" in low
    assert "weather card" not in low
    assert "baltic sunset" not in low
    assert "no text or logo" in negative.lower()
    assert "no watermark or signature" in negative.lower()
    assert "no illustration or fantasy" in negative.lower()
    assert "no sunset and no orange golden-hour sky" in negative.lower()
    assert "no moon and no night" in negative.lower()
    for forbidden in ("sunrise", "sunset", "moon", "night", "baltic", "kaliningrad"):
        assert not re.search(rf"\b{forbidden}\b", positive.lower())
    assert style.startswith("cyprus_morning_mediterranean_landscape_")
    assert re.search(r"_[0-9a-f]{8}$", style)
    _assert_compact_prompt_contract(prompt, metadata)


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
    assert "rain clouds" in low
    assert "factual rain" in low
    assert "wet coastal surfaces" in low
    for forbidden in ("beach leisure", "party", "vacation", "carefree swimming"):
        assert forbidden not in low
    assert "no illustration or fantasy" in low


def cy_prompt_generic_warning_gust_10_is_windy_not_storm() -> None:
    message = """
    27.06.2026
    Кипр завтра.
    ⚠️ Предупреждение: высокий УФ.
    Ларнака: ясно, ветер 6 м/с, порывы до 10 м/с, море у побережья.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "gusty wind visible in textured water and leaning coastal grass" in low
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
    assert "gusty wind visible in textured water and leaning coastal grass" in low
    assert "occasional small whitecaps" in low
    assert "no mirror-flat water" in low
    assert "still vegetation" in low


def cy_prompt_dry_gust_17_does_not_create_rain() -> None:
    message = """
    27.06.2026
    Кипр завтра.
    Лимассол: ясно, ветер 7 м/с, порывы до 17 м/с, море у побережья.
    """
    prompt, _style = build_cyprus_scene_prompt(message, post_type="evening")
    low = prompt.lower()
    assert "strong dry coastal wind in textured water" in low
    assert "frequent small whitecaps" in low
    assert "leaning coastal grass" in low
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
    assert "crisp direct sunlight" in low


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
    assert "blue-hour late twilight" in low
    assert "strong dry coastal wind in textured water and leaning coastal grass" in low
    assert "frequent small whitecaps" in low
    assert "dry promenade" in low
    assert "dry promenade and rocks" in low
    assert "dramatic rain clouds" not in low
    assert "wet promenade" not in low
    assert "rain-darkened coast" not in low
    assert "strong direct sunlight" not in low
    assert "beige-gold dust" not in low
    assert "suspended dust" not in low
    assert "no perfect full moon" in low
    assert "no oversized moon" in low
    assert "no fantasy planet" in low
    assert "natural moon scale" in low


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
    assert "strong dry coastal wind in textured water" in low
    assert "frequent small whitecaps" in low
    assert "leaning coastal grass" in low
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
    assert "distant convective cloud towers over troodos" in low
    assert "gusty wind visible in textured water and leaning coastal grass" in low
    assert "occasional small whitecaps" in low
    assert "dry coast" in low
    assert "dramatic rain clouds" not in low
    assert "wet promenade" not in low
    assert "rain-darkened coast" not in low


def cy_prompt_small_harbour_scene_has_harbour_logic() -> None:
    scenario = """
    2026-07-01
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
    assert "linear stone harbour basin and quay" in low
    assert "mooring posts" in low
    assert "low waterfront buildings" in low
    assert "no scenic curved tourist bay" in low


def cy_prompt_format_v2_wet_severe_wind_keeps_rain_visual() -> None:
    final_text = build_evening_format_v2("Кипр", WET_SEVERE_WIND_SOURCE)
    prompt, _style = build_cyprus_scene_prompt(final_text, post_type="evening")
    low = prompt.lower()
    assert "rain clouds" in low
    assert "factual rain" in low
    assert "wet coastal surfaces" in low


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
    prompt, _style, metadata = build_cyprus_scene_prompt_with_metadata(message, post_type="evening")
    low = prompt.lower()
    assert metadata["selected_scene"] not in {
        "inland_urban_rooftop",
        "troodos_landscape",
        "inland_village",
        "dry_inland_landscape",
    }
    assert any(term in low for term in ("sea", "water", "beach", "harbour", "coastal", "salt-lake"))
    assert "nicosia inland" not in low


def cy_prompt_inland_only_when_no_coast() -> None:
    message = """
    Кипр: прогноз на завтра.
    Никосия: жара до +40°, сухой воздух, УФ-индекс 10.
    Ветер 3 м/с, порывы до 6 м/с.
    """
    prompt, _style, metadata = build_cyprus_scene_prompt_with_metadata(message, post_type="evening")
    low = prompt.lower()
    assert metadata["selected_scene"] in {
        "inland_urban_rooftop",
        "troodos_landscape",
        "inland_village",
        "dry_inland_landscape",
    }
    assert any(term in low for term in ("nicosia", "troodos", "inland"))
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
    prompt_a, _, metadata_a = build_cyprus_scene_prompt_with_metadata(message, post_type="evening")
    prompt_b, _, metadata_b = build_cyprus_scene_prompt_with_metadata(message, post_type="evening")
    assert prompt_a == prompt_b
    assert metadata_a["selected_scene"] == metadata_b["selected_scene"]
    assert metadata_a["visual_archetype"] == metadata_b["visual_archetype"]
    assert metadata_a["cache_key"] == metadata_b["cache_key"]
    assert _macro_scene_cue(prompt_a).startswith("dominant ")
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
    assert "twilight" in evening.lower()
    assert "restrained cyprus late twilight" in evening.lower()
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
        assert "rain clouds" in low
        assert "factual rain" in low
        assert "wet coastal surfaces" in low
        assert "baltic" not in low

    morning, _ = build_cyprus_scene_prompt("20.06.2026\n" + scenario, post_type="morning")
    positive, negative = _prompt_sections(morning)
    low = positive.lower()
    for forbidden in ("lunar", "crescent", "night"):
        assert not re.search(rf"\b{forbidden}\b", low)
    assert "sunset" not in positive.lower()
    assert "no sunset" in negative.lower()


def cy_prompt_full_moon_evening_uses_blue_hour_moonlight() -> None:
    message = """
    27.06.2026
    Кипр завтра.
    Лимассол и Ларнака: тепло, море спокойное.
    🌕 Полнолуние в ♑ — пик эмоций и результатов.
    ✨ 100% освещённости — Луна яркая.
    """
    prompt, style, metadata = build_cyprus_scene_prompt_with_metadata(message, post_type="evening")
    low = prompt.lower()
    assert "realistic full moon, 100% illuminated" in low
    assert "blue-hour" in low
    assert "residual right-side horizon glow" in low
    assert "no oversized moon" in low
    assert "no fantasy planet" in low
    assert "no text or logo" in low
    assert "no watermark" in low
    assert "signature" in low
    assert "baltic" not in low
    assert metadata["visibility_forecast_window"] == "none"
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
    assert "blue-hour late twilight" in low
    assert "residual right-side horizon glow" in low
    assert "no perfect full moon" in low
    assert "no oversized moon" in low
    assert "no fantasy planet" in low
    assert "natural moon scale" in low


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
    assert "visual_archetype=" in key_a
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
        morning_compositions.append(str(morning_meta["composition"]).lower())
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
    allowed = {
        "windy_exposed_coast",
        "breakwater_coast",
        "open_sea_cliffs",
        "long_sandy_beach",
        "coastal_promenade",
        "mountain_coast_view",
    }
    for attempt in range(5):
        _prompt, _style, meta = build_cyprus_scene_prompt_with_metadata(
            text,
            post_type="evening",
            variation_attempt=attempt,
        )
        assert meta["selected_scene"] in allowed
        assert meta["selected_scene"] != "quiet_blue_lagoon"


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


def cy_scene_strong_wind_pool_avoids_three_scene_deadlock() -> None:
    text = """
    Добрый вечер, Кипр. 2026-07-23
    Лимассол: ясно, ветер 8 м/с, порывы до 13 м/с.
    🌊 Море: волна умеренная.
    """
    recently_used = {"windy_exposed_coast", "breakwater_coast", "open_sea_cliffs"}
    scenes: list[str] = []
    for attempt in range(6):
        _prompt, _style, meta = build_cyprus_scene_prompt_with_metadata(
            text,
            post_type="evening",
            variation_attempt=attempt,
        )
        scenes.append(meta["selected_scene"])
    assert any(scene not in recently_used for scene in scenes)
    assert "quiet_blue_lagoon" not in scenes


def cy_composition_selection_uses_eligible_before_backend() -> None:
    text = """
    Доброе утро, Кипр. 2026-07-24
    Лимассол: ясно, ветер 4 м/с.
    🌊 Море: волна спокойная.
    """
    first_five = [
        build_cyprus_scene_prompt_with_metadata(text, post_type="morning", variation_attempt=attempt)[2]["composition"]
        for attempt in range(5)
    ]
    _prompt, _style, meta = build_cyprus_scene_prompt_with_metadata(
        text,
        post_type="morning",
        variation_attempt=0,
        blocked_compositions=tuple(first_five),
    )
    assert meta["composition"] not in set(first_five)
    assert meta["composition"] in set(_CY_COASTAL_COMPOSITIONS)


def cy_composition_selection_uses_lru_when_everything_recent() -> None:
    text = """
    Доброе утро, Кипр. 2026-07-25
    Лимассол: ясно, ветер 4 м/с.
    🌊 Море: волна спокойная.
    """
    recent = tuple(_CY_COASTAL_COMPOSITIONS)
    _prompt, _style, meta = build_cyprus_scene_prompt_with_metadata(
        text,
        post_type="morning",
        variation_attempt=0,
        blocked_compositions=recent,
    )
    scene_options = _CY_SCENE_COMPOSITIONS[str(meta["selected_scene"])]
    assert meta["composition"] == scene_options[0]


def cy_disable_bay_visuals_excludes_bays_and_adds_negative_constraints() -> None:
    text = """
    2026-07-15
    Кипр завтра: ясно, у моря ветер 7 м/с, порывы до 12 м/с.
    🌙 Убывающая Луна, 92% освещённости.
    """
    old_value = os.environ.get("CY_DISABLE_BAY_VISUALS")
    os.environ["CY_DISABLE_BAY_VISUALS"] = "1"
    try:
        for attempt in range(32):
            prompt, _style, meta = build_cyprus_scene_prompt_with_metadata(
                text,
                post_type="evening",
                variation_attempt=attempt,
            )
            scene = meta["selected_scene"].lower()
            composition = meta["composition"].lower()
            assert not any(token in scene for token in ("bay", "cove", "lagoon"))
            assert meta["visual_archetype"] not in {"bay_panorama", "elevated_cliff_panorama"}
            assert not any(token in composition for token in ("aerial", "raised", "wide panorama", "beach curve"))
            low = prompt.lower()
            for clause in (
                "no scenic curved bay",
                "no natural cove",
                "no enclosed tourist lagoon",
                "no elevated postcard coastline",
            ):
                assert clause in low
            _assert_compact_prompt_contract(prompt, meta)
    finally:
        if old_value is None:
            os.environ.pop("CY_DISABLE_BAY_VISUALS", None)
        else:
            os.environ["CY_DISABLE_BAY_VISUALS"] = old_value


def cy_prompt_compact_contract_covers_weather_and_inland_matrix() -> None:
    cases = (
        ("morning", "2026-07-01\nЛарнака: ясно, УФ-индекс 4. Море спокойное."),
        ("morning", "2026-07-02\nЛимассол: жара 36°, УФ-индекс 9. Море спокойное."),
        ("evening", "2026-07-03\nЛарнака: ясно, ветер 7 м/с, порывы до 13 м/с. Море у побережья."),
        ("morning", "2026-07-04\nПафос: облачно, ветер 4 м/с. Море у побережья."),
        ("evening", "2026-07-05\nЛимассол: местами дождь, ветер 6 м/с. На побережье мокро."),
        ("evening", "2026-07-06\nЛарнака: пылевая дымка, AQI 125. Море у побережья."),
        ("evening", "2026-07-07\nНикосия: жара 39°, сухо, ветер 3 м/с."),
    )
    for post_type, text in cases:
        prompt, _style, metadata = build_cyprus_scene_prompt_with_metadata(
            text,
            post_type=post_type,
        )
        _assert_compact_prompt_contract(prompt, metadata)
        assert prompt.count("no text") == 1
        assert prompt.count("logo") == 1
        assert prompt.count("watermark") == 1


def cy_prompt_semantic_dedupe_collapses_equivalent_cues() -> None:
    items = [
        "no text",
        "no visible text",
        "no letters or pseudo-caption",
        "no logo",
        "no brand marks",
        "no watermark",
        "no artist signature",
        "natural moon scale",
        "small-to-medium Moon",
        "no oversized moon",
        "textured water",
        "wind-ruffled sea with uneven water",
        "no perfect tourist calm",
        "no tourist calm",
        "palms distant",
        "no foreground palms",
    ]
    assert len(_dedupe_semantic_items(items)) == 7


def _find_prompt_for_scene(scene_family: str) -> tuple[str, dict[str, object]]:
    for day_offset in range(7):
        forecast_date = (date(2026, 7, 8) + timedelta(days=day_offset)).isoformat()
        text = (
            forecast_date
            + "\nКипр: ясная погода у моря."
            + "\nЛимассол и Ларнака: ветер 4 м/с, море спокойное."
        )
        for attempt in range(32):
            prompt, _style, metadata = build_cyprus_scene_prompt_with_metadata(
                text,
                post_type="evening",
                variation_attempt=attempt,
            )
            if metadata["selected_scene"] == scene_family:
                return prompt, metadata
    raise AssertionError(f"scene was not selected: {scene_family}")


def cy_prompt_scene_foundations_are_specific_and_compatible() -> None:
    old_value = os.environ.pop("CY_DISABLE_BAY_VISUALS", None)
    try:
        infrastructure_scenes = (
            "coastal_promenade",
            "marina_walkway",
            "small_harbour",
            "harbour_pier_waterlevel",
            "breakwater_coast",
            "coastal_urban_rooftop",
            "beach_cafe_terrace",
        )
        for scene_family in infrastructure_scenes:
            prompt, metadata = _find_prompt_for_scene(scene_family)
            assert "human-made elements distant and non-focal" not in prompt.lower()
            _assert_compact_prompt_contract(prompt, metadata)

        beach_prompt, _beach_meta = _find_prompt_for_scene("open_beach_horizon")
        beach_positive, _beach_negative = _prompt_sections(beach_prompt)
        for unrelated in ("marina", "harbour", "coastal road"):
            assert unrelated not in beach_positive.lower()

        marina_prompt, _marina_meta = _find_prompt_for_scene("marina_walkway")
        marina_positive, _marina_negative = _prompt_sections(marina_prompt)
        assert "cliff" not in marina_positive.lower()
        assert "coastal road" not in marina_positive.lower()

        harbour_prompt, _harbour_meta = _find_prompt_for_scene("small_harbour")
        assert "linear stone harbour basin and quay" in harbour_prompt.lower()
        assert "no scenic curved tourist bay" in harbour_prompt.lower()
        assert "no enclosed water" not in harbour_prompt.lower()
    finally:
        if old_value is not None:
            os.environ["CY_DISABLE_BAY_VISUALS"] = old_value


def cy_prompt_no_bay_mode_keeps_small_harbour_basin() -> None:
    old_value = os.environ.get("CY_DISABLE_BAY_VISUALS")
    os.environ["CY_DISABLE_BAY_VISUALS"] = "1"
    try:
        prompt, metadata = _find_prompt_for_scene("small_harbour")
        low = prompt.lower()
        assert "harbour basin" in low
        assert "no scenic curved bay" in low
        assert "no enclosed tourist lagoon" in low
        assert "no enclosed water" not in low
        _assert_compact_prompt_contract(prompt, metadata)
    finally:
        if old_value is None:
            os.environ.pop("CY_DISABLE_BAY_VISUALS", None)
        else:
            os.environ["CY_DISABLE_BAY_VISUALS"] = old_value


def cy_prompt_evening_moon_context_is_not_forced_or_duplicated() -> None:
    ordinary = "2026-07-09\nЛимассол: ясно, море спокойно, ветер 3 м/с."
    prompt, _style, metadata = build_cyprus_scene_prompt_with_metadata(
        ordinary,
        post_type="evening",
    )
    positive, negative = _prompt_sections(prompt)
    assert "moon" not in positive.lower()
    assert "moon" not in negative.lower()
    _assert_compact_prompt_contract(prompt, metadata)

    lunar_cases = (
        (
            "2026-07-10\nЛимассол: ясно, море спокойно. 🌕 Полнолуние. ✨ 100% освещённости.",
            "realistic full moon, 100% illuminated",
        ),
        (
            "2026-07-11\nЛимассол: ясно, море спокойно. 🌖 Убывающая Луна. ✨ 92% освещённости.",
            "realistic waning gibbous moon, 92% illuminated",
        ),
    )
    for text, moon_cue in lunar_cases:
        lunar_prompt, _style, lunar_meta = build_cyprus_scene_prompt_with_metadata(
            text,
            post_type="evening",
        )
        lunar_positive, _lunar_negative = _prompt_sections(lunar_prompt)
        assert lunar_positive.lower().count(moon_cue) == 1
        _assert_compact_prompt_contract(lunar_prompt, lunar_meta)


def cy_morning_dense_fog_overrides_hot_clear_visuals() -> None:
    text = """
    <b>🌅 Кипр сегодня (16.07.2026)</b>
    Никосия: 36/25 °C • ясно
    Лимассол: 33/26 °C • ясно • 🌊 Море 28°C
    ☀️ УФ-индекс 10 — очень высокий.
    🌫 Видимость: сильный утренний туман в Лимассоле — местами около 320 м.
    #Кипр #погода
    """
    ctx = parse_visual_context_cy(text, post_type="morning")
    scene = apply_visual_rules_cy(ctx)
    prompt, _style, metadata = build_cyprus_scene_prompt_with_metadata(text, post_type="morning")
    low = prompt.lower()
    assert ctx.visibility_condition == "dense_fog"
    assert scene.diagnostics["fog_visual_rule"] is True
    for cue in (
        "dense humid fog",
        "heavily reduced distant visibility",
        "partially obscured horizon",
        "soft diffused",
        "muted contrast",
        "moist atmospheric depth",
        "no crisp distant horizon",
        "no perfectly clear horizon",
        "no sharp postcard visibility",
        "no completely transparent air",
    ):
        assert cue in low
    assert "heat shimmer" not in low
    assert "crisp direct sunlight" not in low
    assert metadata["visibility_condition"] == "dense_fog"
    assert metadata["fog_visual_rule"] == "true"
    _assert_compact_prompt_contract(prompt, metadata)


def cy_morning_mixed_haze_does_not_become_dry_dust_scene() -> None:
    text = """
    <b>🌅 Кипр сегодня (16.07.2026)</b>
    Лимассол: 31/25 °C • облачно • 🌊 Море 27°C
    🏭 Воздух: AQI 130 • PM₁₀ 65
    🌫 Видимость: утром снижена, местами около 600 м; возможна смесь влажной дымки и загрязнения воздуха.
    #Кипр #погода
    """
    ctx = parse_visual_context_cy(text, post_type="morning")
    prompt, _style, metadata = build_cyprus_scene_prompt_with_metadata(text, post_type="morning")
    assert ctx.visibility_condition == "mixed_visibility"
    assert ctx.dust_vs_fog_classification == "mixed_visibility"
    assert "muted grey atmospheric haze" in prompt.lower()
    assert "restrained humid softness" in prompt.lower()
    assert "restrained polluted-air haze" in prompt.lower()
    assert "no exaggerated sahara palette" in prompt.lower()
    assert "dense humid coastal fog" not in prompt.lower()
    assert "beige-gold atmospheric dust" not in prompt.lower()
    _assert_compact_prompt_contract(prompt, metadata)


def cy_morning_visibility_states_have_distinct_prompt_cues_and_metadata() -> None:
    text = """
    <b>🌅 Кипр сегодня (16.07.2026)</b>
    Лимассол: 29/24 °C • облачно • 🌊 Море 27°C
    🌫 Видимость: утром местами снижена.
    #Кипр #погода
    """
    cases = {
        "dense_fog": ("dense humid fog", "heavily reduced distant visibility"),
        "fog": ("humid coastal fog", "softened horizon"),
        "mist": ("humid morning mist", "gentle atmospheric depth"),
        "reduced_visibility": ("reduced distant clarity", "restrained contrast"),
        "dust_haze": ("muted beige-grey dry atmospheric haze", "dry suspended particles"),
        "mixed_visibility": ("muted grey atmospheric haze", "restrained polluted-air haze"),
    }
    prompts: dict[str, str] = {}
    for condition, required in cases.items():
        visibility_metadata = {
            "condition": condition,
            "current_visibility_m": 7000,
            "morning_min_visibility_m": 2200,
            "humidity_pct": 91,
            "temperature_c": 24,
            "dew_point_c": 23,
            "dew_point_spread_c": 1,
            "weather_code": 45,
            "weather_code_source": "hourly_morning",
            "evidence_source": "hourly_morning+air_quality",
            "observation_time": "2026-07-16T06:00",
            "confidence": "high",
            "classification_reason": f"offline fixture: {condition}",
            "location_label": "Лимассол",
        }
        prompt, _style, metadata = build_cyprus_scene_prompt_with_metadata(
            text,
            post_type="morning",
            visibility_metadata=visibility_metadata,
        )
        positive, negative = _prompt_sections(prompt)
        positive_low = positive.lower()
        for cue in required:
            assert cue in positive_low, (condition, cue, prompt)
        if condition in {"fog", "mist", "reduced_visibility", "mixed_visibility"}:
            assert "dense humid fog" not in positive_low
        if condition == "mist":
            assert "dense" not in positive_low
        if condition == "reduced_visibility":
            assert "humid fog" not in positive_low
            assert "wet atmosphere" not in positive_low
        if condition == "dust_haze":
            assert "no humid fog cues" in positive_low
        if condition == "mixed_visibility":
            assert "exaggerated sahara palette" not in positive_low
            assert "no exaggerated sahara palette" in negative.lower()
        assert metadata["prompt_version"] == "cyprus_visual_v8"
        assert metadata["current_visibility_m"] == 7000
        assert metadata["morning_min_visibility_m"] == 2200
        assert metadata["humidity_pct"] == 91
        assert metadata["temperature_c"] == 24
        assert metadata["dew_point_c"] == 23
        assert metadata["dew_point_spread_c"] == 1
        assert metadata["weather_code"] == 45
        assert metadata["observation_time"] == "2026-07-16T06:00"
        assert metadata["confidence"] == "high"
        assert metadata["classification_reason"] == f"offline fixture: {condition}"
        assert metadata["location_label"] == "Лимассол"
        prompts[condition] = prompt

    assert len(set(prompts.values())) == len(cases)
    fallback_context = parse_visual_context_cy(
        text.replace("местами снижена", "влажная дымка, местами около 2200 м"),
        post_type="morning",
    )
    assert fallback_context.visibility_condition == "mist"
    assert fallback_context.current_visibility_m is None
    assert fallback_context.morning_min_visibility_m is None


def _evening_visibility_prompt(visibility_line: str) -> tuple[str, str, dict[str, object]]:
    message = f"""
    <b>🌅 Кипр завтра (17.07.2026)</b>
    Лимассол: 29/23 °C • облачно • 🌊 Море 27°C
    {visibility_line}
    🌕 Полнолуние в ♑ — пик эмоций и результатов.
    ✨ 100% освещённости — Луна яркая.
    #Кипр #погода
    """
    prompt, _style, metadata = build_cyprus_scene_prompt_with_metadata(
        message,
        post_type="evening",
    )
    positive, negative = _prompt_sections(prompt)
    assert metadata["visibility_forecast_window"] == "tomorrow_morning"
    assert "next-day early-morning forecast window only" in positive.lower()
    assert "restrained cyprus late twilight" not in positive.lower()
    assert "blue-hour" not in positive.lower()
    assert "realistic full moon" not in positive.lower()
    assert "no evening twilight or moon-led scene" in negative.lower()
    return positive.lower(), negative.lower(), metadata


def cy_evening_tomorrow_morning_fog_overrides_moon_twilight() -> None:
    positive, negative, metadata = _evening_visibility_prompt(
        "🌫 Видимость: завтра утром туман, местами около 900 м; дальние объекты плохо различимы."
    )
    assert "humid coastal fog" in positive
    for cue in (
        "no crisp distant horizon",
        "no perfectly clear horizon",
        "no sharp postcard visibility",
        "no completely transparent air",
    ):
        assert cue in negative
    assert metadata["visibility_condition"] == "fog"


def cy_evening_tomorrow_morning_mist_is_not_dense_fog() -> None:
    positive, _negative, metadata = _evening_visibility_prompt(
        "🌫 Видимость: завтра утром влажная дымка, местами около 2200 м; у моря видимость снижена."
    )
    assert "humid morning mist" in positive
    assert "dense humid fog" not in positive
    assert metadata["visibility_condition"] == "mist"


def cy_evening_tomorrow_morning_reduced_visibility_has_no_fog_claim() -> None:
    positive, negative, metadata = _evening_visibility_prompt(
        "🌫 Видимость: завтра утром местами снижена, местами около 4000 м; нужна дополнительная дистанция."
    )
    assert "reduced distant clarity" in positive
    assert "humid fog" not in positive
    assert "no invented humid fog or wet atmosphere" in negative
    assert metadata["visibility_condition"] == "reduced_visibility"


def cy_evening_tomorrow_morning_dust_haze_is_dry() -> None:
    positive, negative, metadata = _evening_visibility_prompt(
        "🌫 Видимость: завтра утром возможна сухая пылевая дымка, местами около 4000 м; проверить дальность обзора."
    )
    assert "muted beige-grey dry atmospheric haze" in positive
    assert "humid coastal fog" not in positive
    assert "no humid coastal fog" in negative
    assert metadata["visibility_condition"] == "dust_haze"


def cy_evening_tomorrow_morning_mixed_visibility_stays_mixed() -> None:
    positive, negative, metadata = _evening_visibility_prompt(
        "🌫 Видимость: завтра утром снижена, местами около 600 м; возможна смесь влажной дымки и загрязнения воздуха."
    )
    assert "mixed grey haze" in positive
    assert "dense wall of fog" not in positive
    assert "exaggerated sahara palette" not in positive
    assert "no dense wall of fog" in negative
    assert "no exaggerated sahara palette" in negative
    assert metadata["visibility_condition"] == "mixed_visibility"


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
    cy_scene_strong_wind_pool_avoids_three_scene_deadlock,
    cy_composition_selection_uses_eligible_before_backend,
    cy_composition_selection_uses_lru_when_everything_recent,
    cy_disable_bay_visuals_excludes_bays_and_adds_negative_constraints,
    cy_prompt_compact_contract_covers_weather_and_inland_matrix,
    cy_prompt_semantic_dedupe_collapses_equivalent_cues,
    cy_prompt_scene_foundations_are_specific_and_compatible,
    cy_prompt_no_bay_mode_keeps_small_harbour_basin,
    cy_prompt_evening_moon_context_is_not_forced_or_duplicated,
    cy_morning_dense_fog_overrides_hot_clear_visuals,
    cy_morning_mixed_haze_does_not_become_dry_dust_scene,
    cy_morning_visibility_states_have_distinct_prompt_cues_and_metadata,
    cy_evening_tomorrow_morning_fog_overrides_moon_twilight,
    cy_evening_tomorrow_morning_mist_is_not_dense_fog,
    cy_evening_tomorrow_morning_reduced_visibility_has_no_fog_claim,
    cy_evening_tomorrow_morning_dust_haze_is_dry,
    cy_evening_tomorrow_morning_mixed_visibility_stays_mixed,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} Cyprus synthetic visual checks passed")


if __name__ == "__main__":
    main()
