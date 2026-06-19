#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Synthetic, offline checks for the Cyprus visual context/rules pipeline."""

from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from visual_context_cy import parse_visual_context_cy
from visual_rules_cy import apply_visual_rules_cy


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
    Лимассол: +32°.
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


TESTS = [
    cy_morning_clear_high_uv,
    cy_morning_dust_haze,
    cy_evening_hot_coast,
    cy_evening_rain,
    cy_inland_heat_nicosia,
    cy_coastal_wind,
    cy_no_baltic_leak,
]


def main() -> None:
    for test in TESTS:
        test()
        print(f"PASS: {test.__name__}")
    print(f"OK: {len(TESTS)} Cyprus synthetic visual checks passed")


if __name__ == "__main__":
    main()
