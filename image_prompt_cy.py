#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
image_prompt_cy.py

Построение промтов для вечернего кипрского сообщения
с тремя сценариями картинок и «взвешенным» случайным выбором стиля.

Стили:
1) "sea_mountains"  — приоритетный: море + горы + Луна, кинематографичный вечер.
2) "map_mood"       — стилизованная карта-настроение Кипра.
3) "mini_dashboard" — утилитарный «дашборд» без текста/цифр.

Функция build_cyprus_evening_prompt НЕ делает HTTP-запросы, она только
возвращает текст промта и имя выбранного стиля. Генерация картинки делается
через world_en.imagegen.generate_astro_image или аналогичный бэкенд.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import random
from typing import Tuple


@dataclasses.dataclass(frozen=True)
class CyprusImageContext:
    date: dt.date
    # Краткий смысл для моря/берега — можно собрать из текста "Морские города"
    marine_mood: str
    # Краткий смысл для суши/гор — по Никосии/Тродосу
    inland_mood: str
    # Краткий астротекст (например, строка из блока "Астрособытия", сжатая и/или переведённая на EN)
    astro_mood_en: str = ""


# ───────────────────── стили ─────────────────────


def _style_prompt_map_mood(ctx: CyprusImageContext) -> Tuple[str, str]:
    """
    Стиль 1: «карта-настроение Кипра».
    Акцент на острове, море и вечернем небе + Луна.
    """
    style_name = "map_mood"

    prompt = (
        "Dreamy stylized flat map of Cyprus floating above a calm Mediterranean sea at golden hour. "
        "Turquoise and deep teal water in the lower half, very calm surface with soft reflections. "
        "Soft coral and peach sunset sky in the upper half, fading smoothly into deep blue twilight. "
        "A big almost–full golden Moon with a gentle halo hangs above the island, a few tiny stars just appearing. "
        "Simple clean shapes, subtle texture, cinematic lighting, soft gradients, high quality digital illustration. "
        "No text, no captions, no labels, no logos, no UI, no country names, absolutely no letters or numbers anywhere. "
        "Square aspect ratio, suitable as Telegram and Facebook post thumbnail."
    )

    if ctx.astro_mood_en:
        prompt += f" The overall astro energy feels like: {ctx.astro_mood_en}."

    return style_name, prompt


def _style_prompt_sea_mountains(ctx: CyprusImageContext) -> Tuple[str, str]:
    """
    Стиль 2 (приоритетный): «море + горы + Луна».
    Кинематографичный вечер у моря с силуэтом Тродоса.
    """
    style_name = "sea_mountains"

    prompt = (
        "Cozy Cyprus coastal evening scene. "
        "Calm Mediterranean sea in the foreground with gentle ripples and soft reflections, "
        "turquoise and deep teal tones suggesting a warm, breezy seaside night — "
        f"{ctx.marine_mood or 'perfect for a relaxed seaside walk or quiet SUP session'}. "
        "In the distance, soft layered silhouettes of mountains symbolizing Troodos and inland areas, "
        "painted in cooler bluish and violet tones to show fresher, quieter air — "
        f"{ctx.inland_mood or 'cool, peaceful and grounding'}. "
        "Above everything, a large almost–full golden Moon with a subtle halo and a few tiny stars "
        "shining through a clear twilight sky; the sky shifts from soft coral and peach near the horizon "
        "into deep indigo and midnight blue higher up. "
        "Atmospheric, cinematic lighting, soft gradients, high quality digital painting, no people. "
        "No text, no captions, no labels, no logos, absolutely no letters or numbers anywhere. "
        "Square format composition, suitable as a weather thumbnail for social media."
    )

    if ctx.astro_mood_en:
        prompt += f" The Moon and sky subtly reflect this astro mood: {ctx.astro_mood_en}."

    return style_name, prompt


def _style_prompt_mini_dashboard(ctx: CyprusImageContext) -> Tuple[str, str]:
    """
    Стиль 3: более утилитарный «мини-дэшборд», но БЕЗ текста/цифр.
    Визуальная метафора прогноза по морю/суше и Луне.
    """
    style_name = "mini_dashboard"

    prompt = (
        "Modern minimalist weather dashboard–style illustration for Cyprus, but purely pictorial. "
        "Flat silhouette of the island in the center, floating over calm turquoise sea, "
        "with a few small glowing icon-like circles along the coastline to represent seaside towns "
        "and one circle in the center to represent inland areas. "
        f"Coast markers feel warm and breezy, suggesting a pleasant seaside evening: {ctx.marine_mood or 'mild, slightly cloudy and comfortable'}; "
        f"the inland marker feels cooler and calmer: {ctx.inland_mood or 'fresh, stable and quiet'}. "
        "Above the island, an almost–full golden Moon and a few soft clouds and tiny stars hint at the astro energy: "
        f"{ctx.astro_mood_en or 'stable, grounded and reflective'}. "
        "Clean flat design, smooth gradients, subtle depth, no data tables. "
        "No text, no numbers, no UI widgets with labels, no country names, absolutely no typography or letters of any kind. "
        "Square layout, high quality digital illustration, optimized as a neutral weather thumbnail."
    )

    return style_name, prompt


# ───────────────────── выбор стиля ─────────────────────

# Базовый список функций стилей (для ссылок, если понадобится)
_STYLES = [
    _style_prompt_sea_mountains,
    _style_prompt_map_mood,
    _style_prompt_mini_dashboard,
]


def build_cyprus_evening_prompt(
    date: dt.date,
    marine_mood: str,
    inland_mood: str,
    astro_mood_en: str = "",
) -> Tuple[str, str]:
    """
    Собирает промт и возвращает (prompt_text, style_name).

    Особенности:
    - randomness детерминируется датой (по ordinal), чтобы в течение дня
      стиль не скакал между перезапусками;
    - стиль «sea_mountains» имеет повышенный вес, карта и дэшборд — более редкие.
    """
    ctx = CyprusImageContext(
        date=date,
        marine_mood=(marine_mood or "").strip(),
        inland_mood=(inland_mood or "").strip(),
        astro_mood_en=(astro_mood_en or "").strip(),
    )

    rnd = random.Random()
    # Можно добавить «соль», чтобы seed не пересекался с другими местами
    rnd.seed(date.toordinal() * 9973 + 42)

    # Весовой выбор:
    #   sea_mountains — приоритет (5 «билетов»),
    #   map_mood      — реже,
    #   mini_dashboard— реже.
    weighted_style_fns = (
        [_style_prompt_sea_mountains] * 5
        + [_style_prompt_map_mood] * 1
        + [_style_prompt_mini_dashboard] * 1
    )

    style_fn = rnd.choice(weighted_style_fns)
    style_name, prompt = style_fn(ctx)
    return prompt, style_name
