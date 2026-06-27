#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synthetic, offline checks for the Cyprus visual context/rules pipeline."""

from __future__ import annotations

from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visual_context_cy import parse_visual_context_cy
from visual_rules_cy import apply_visual_rules_cy
from image_prompt_cy_scene import build_cyprus_scene_prompt


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
    assert "warm mediterranean evening" in scene.light_cue.lower()
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
    assert "sea breeze" in _all_cues(scene)


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
    assert "pale blue sky" in low
    assert "fresh early sun" in low
    assert "natural shadows" in low
    for forbidden in (
        "text", "logo", "poster", "card", "moon", "night", "sunset",
        "baltic", "kaliningrad",
    ):
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
    for forbidden in ("beach leisure", "party", "vacation", "poster"):
        assert forbidden not in low


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
    assert "late-day" in evening.lower() or "dusk" in evening.lower()
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
    for forbidden in ("moon", "lunar", "crescent", "night", "evening", "sunset"):
        assert not re.search(rf"\b{forbidden}\b", low)


TESTS = [
    cy_morning_clear_high_uv,
    cy_morning_dust_haze,
    cy_evening_hot_coast,
    cy_evening_rain,
    cy_inland_heat_nicosia,
    cy_coastal_wind,
    cy_no_baltic_leak,
    cy_prompt_morning_sanitized,
    cy_prompt_evening_dust_heat,
    cy_prompt_rain_not_leisure,
    cy_prompt_no_raw_source_hints,
    cy_prompt_coastal_priority_over_nicosia,
    cy_prompt_inland_only_when_no_coast,
    cy_prompt_controlled_variety_is_stable,
    cy_prompt_morning_evening_same_date_differ,
    cy_prompt_adjacent_dates_change_macro_viewpoint,
    cy_prompt_controlled_variety_changes_by_date,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} Cyprus synthetic visual checks passed")


if __name__ == "__main__":
    main()
