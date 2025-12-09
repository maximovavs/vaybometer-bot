#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
image_prompt_cy.py

Построение промтов для вечернего кипрского сообщения
с несколькими сценариями картинок и «взвешенным» случайным выбором стиля.

Стили:
1) "sea_mountains"  — приоритетный: море + горы + Луна, кинематографичный вечер.
2) "map_mood"       — стилизованная карта-настроение Кипра.
3) "mini_dashboard" — утилитарный «дашборд»-метафора без текста/цифр.
4) "moon_goddess"   — мифологичная Луна-богиня, играющая со знаком зодиака.

Функция build_cyprus_evening_prompt НЕ делает HTTP-запросы, она только
возвращает текст промта и имя выбранного стиля. Генерация картинки делается
через world_en.imagegen.generate_astro_image или аналогичный бэкенд.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import random
import logging
from typing import Tuple, Optional


@dataclasses.dataclass(frozen=True)
class CyprusImageContext:
    date: dt.date
    # Краткий смысл для моря/берега — собирается из текста «морских городов»
    marine_mood: str
    # Краткий смысл для суши/гор — по Никосии/Тродосу и т.п.
    inland_mood: str
    # Краткий астротекст (строка/фраза из блока "Астрособытия", сжатая и/или переведённая на EN)
    astro_mood_en: str = ""


logger = logging.getLogger(__name__)

# Ключевые слова для определения «ветрено/дождливо» по тексту настроений
WIND_KEYWORDS = (
    "ветер", "ветрен", "шквал", "порыв", "бриз",
    "wind", "windy", "gust", "gusty", "breeze", "storm wind",
)

RAIN_KEYWORDS = (
    "дожд", "ливн", "гроза", "грoз",
    "rain", "rainy", "shower", "showers", "thunderstorm", "storm",
)

# Соответствия знаков: RU → EN
ZODIAC_RU_EN = {
    "овен": "Aries",
    "телец": "Taurus",
    "близнец": "Gemini",
    "рак": "Cancer",
    "лев": "Leo",
    "дева": "Virgo",
    "весы": "Libra",
    "скорпион": "Scorpio",
    "стрелец": "Sagittarius",
    "козерог": "Capricorn",
    "водолей": "Aquarius",
    "рыб": "Pisces",  # «рыбы», «рыб» и т.п.
}

ZODIAC_EN = [
    "Aries", "Taurus", "Gemini", "Cancer", "Leo", "Virgo",
    "Libra", "Scorpio", "Sagittarius", "Capricorn", "Aquarius", "Pisces",
]


def _weather_flavour(marine_mood: str, inland_mood: str) -> str:
    """
    Вытащить «подтон» — ветрено / дождливо / спокойно — из текстовых mood'ов.
    Если явных ключевых слов нет, считаем погоду спокойной.
    """
    text = f"{marine_mood} {inland_mood}".lower()
    is_windy = any(k in text for k in WIND_KEYWORDS)
    is_rainy = any(k in text for k in RAIN_KEYWORDS)

    if is_windy and is_rainy:
        return (
            "Windy, rainy evening: strong gusts from the sea, "
            "wet reflections on the ground, dynamic clouds in the sky."
        )
    if is_windy:
        return (
            "Windy evening: noticeable gusts, moving waves and tree crowns, "
            "hair and clothes slightly lifted by the wind."
        )
    if is_rainy:
        return (
            "Rainy evening: wet pavement, soft raindrops visible in street lights, "
            "misty air above the sea."
        )
    return (
        "Calm weather: light breeze, soft waves and clear visibility, "
        "no heavy rain or storm."
    )


def _parse_moon_phase_and_sign(text: str) -> Tuple[Optional[str], Optional[str]]:
    """
    Из строки астропрогноза вытащить фазу Луны и знак (RU/EN).
    Возвращает (phase, sign_en), где phase ∈ {'full','new','first_quarter','last_quarter'}.
    """
    if not text:
        return None, None

    s = text.lower()
    phase: Optional[str] = None

    if "полнолуние" in s or "full moon" in s:
        phase = "full"
    elif "новолуние" in s or "new moon" in s:
        phase = "new"
    elif "первая четверть" in s or "first quarter" in s:
        phase = "first_quarter"
    elif "последняя четверть" in s or "last quarter" in s:
        phase = "last_quarter"

    sign: Optional[str] = None

    # 1) русские названия
    for ru, en in ZODIAC_RU_EN.items():
        if ru in s:
            sign = en
            break

    # 2) английские
    if sign is None:
        for en in ZODIAC_EN:
            if en.lower() in s:
                sign = en
                break

    logger.debug("Astro parse: phase=%s sign=%s from %r", phase, sign, text)
    return phase, sign


def _astro_visual_sky(text: str) -> str:
    """
    Вариант «рисуем небо по астропрогнозу»: полнолуние / новолуние и т.п.
    Возвращает короткое EN-описание неба или пустую строку.
    """
    phase, sign = _parse_moon_phase_and_sign(text)
    if not phase and not sign:
        return ""

    parts: list[str] = []

    if phase == "full":
        parts.append("a bright full Moon hanging low over the sea")
    elif phase == "new":
        parts.append("a very dark night sky with only a thin lunar crescent")
    elif phase in ("first_quarter", "last_quarter"):
        parts.append("a strong crescent Moon with clear contrast in the sky")

    if not parts:
        parts.append("a calm night sky with a clearly visible Moon")

    if sign:
        parts.append(f"the atmosphere subtly reflects the energy of {sign}")

    return " ".join(parts)


def _astro_visual_goddess(text: str) -> str:
    """
    Мифологический вариант: Луна как богиня, играющая со знаком.
    Возвращает короткое EN-описание сцены или пустую строку.
    """
    phase, sign = _parse_moon_phase_and_sign(text)
    if not phase and not sign:
        return ""

    phase_desc = {
        "full": "full-moon",
        "new": "new-moon",
        "first_quarter": "first-quarter",
        "last_quarter": "last-quarter",
    }.get(phase or "", "lunar")

    sign_phrase = sign or "the zodiac"

    return (
        f"a luminous Moon goddess in the {phase_desc} phase, "
        f"playing with the symbol of {sign_phrase}, "
        "her light spilling over the sea and the coastline"
    )


# ───────────────────── стили ─────────────────────


def _style_prompt_map_mood(ctx: CyprusImageContext) -> Tuple[str, str]:
    """
    Стиль 1: «карта-настроение Кипра».
    Акцент на острове, море и вечернем небе + Луна.
    """
    style_name = "map_mood"

    weather_text = _weather_flavour(ctx.marine_mood, ctx.inland_mood)
    astro_sky = _astro_visual_sky(ctx.astro_mood_en)

    prompt = (
        "Dreamy stylized flat map of Cyprus floating above a calm Mediterranean sea at golden hour. "
        "Turquoise and deep teal water in the lower half, very calm surface with soft reflections. "
        "Soft coral and peach sunset sky in the upper half, fading smoothly into deep blue twilight. "
        "The coastline mood feels like this: "
        f"{ctx.marine_mood or 'warm, welcoming and slightly breezy by the sea'}. "
        "Inland areas carry a different, more grounded vibe: "
        f"{ctx.inland_mood or 'cooler, quieter hills and towns in the background'}. "
        f"{weather_text} "
        "Simple clean shapes, subtle texture, cinematic lighting, soft gradients, high quality digital illustration. "
        "No text, no captions, no labels, no logos, no UI, no country names, absolutely no letters or numbers anywhere. "
        "Square aspect ratio, suitable as Telegram and Facebook post thumbnail."
    )

    if ctx.astro_mood_en:
        prompt += f" The overall astro energy feels like: {ctx.astro_mood_en}."
    if astro_sky:
        prompt += f" The sky area subtly shows this: {astro_sky}."

    return style_name, prompt


def _style_prompt_sea_mountains(ctx: CyprusImageContext) -> Tuple[str, str]:
    """
    Стиль 2 (приоритетный): «море + горы + Луна».
    Кинематографичный вечер у моря с силуэтом Тродоса.
    """
    style_name = "sea_mountains"

    weather_text = _weather_flavour(ctx.marine_mood, ctx.inland_mood)
    astro_sky = _astro_visual_sky(ctx.astro_mood_en)

    prompt = (
        "Cozy Cyprus coastal evening scene. "
        "Calm Mediterranean sea in the foreground with gentle ripples and soft reflections, "
        "turquoise and deep teal tones suggesting a warm, breezy seaside night — "
        f"{ctx.marine_mood or 'perfect for a relaxed seaside walk or quiet SUP session'}. "
        "In the distance, soft layered silhouettes of mountains symbolizing Troodos and inland areas, "
        "painted in cooler bluish and violet tones to show fresher, quieter air — "
        f"{ctx.inland_mood or 'cool, peaceful and grounding'}. "
        f"{weather_text} "
        "Above everything, a large almost–full golden Moon with a subtle halo and a few tiny stars "
        "shining through a clear twilight sky; the sky shifts from soft coral and peach near the horizon "
        "into deep indigo and midnight blue higher up. "
        "Atmospheric, cinematic lighting, soft gradients, high quality digital painting, no people. "
        "No text, no captions, no labels, no logos, absolutely no letters or numbers anywhere. "
        "Square format composition, suitable as a weather thumbnail for social media."
    )

    if ctx.astro_mood_en:
        prompt += f" The Moon and sky subtly reflect this astro mood: {ctx.astro_mood_en}."
    if astro_sky:
        prompt += f" Visually the sky looks like: {astro_sky}."

    return style_name, prompt


def _style_prompt_mini_dashboard(ctx: CyprusImageContext) -> Tuple[str, str]:
    """
    Стиль 3: более утилитарный «мини-дэшборд», но БЕЗ текста/цифр.
    Визуальная метафора прогноза по морю/суше и Луне.
    """
    style_name = "mini_dashboard"

    weather_text = _weather_flavour(ctx.marine_mood, ctx.inland_mood)
    astro_sky = _astro_visual_sky(ctx.astro_mood_en)

    prompt = (
        "Modern minimalist weather dashboard–style illustration for Cyprus, but purely pictorial. "
        "Flat silhouette of the island in the center, floating over calm turquoise sea, "
        "with a few small glowing icon-like circles along the coastline to represent seaside towns "
        "and one circle in the center to represent inland areas. "
        f"Coast markers feel warm and breezy, suggesting a pleasant seaside evening: {ctx.marine_mood or 'mild, slightly cloudy and comfortable'}; "
        f"the inland marker feels cooler and calmer: {ctx.inland_mood or 'fresh, stable and quiet'}. "
        f"{weather_text} "
        "Above the island, an almost–full golden Moon and a few soft clouds and tiny stars hint at the astro energy. "
    )

    if ctx.astro_mood_en:
        prompt += f" The astro mood for tomorrow is: {ctx.astro_mood_en}. "
    if astro_sky:
        prompt += f"The sky zone of the dashboard visually reflects this: {astro_sky}. "

    prompt += (
        "Clean flat design, smooth gradients, subtle depth, no data tables. "
        "No text, no numbers, no UI widgets with labels, no country names, absolutely no typography or letters of any kind. "
        "Square layout, high quality digital illustration, optimized as a neutral weather thumbnail."
    )

    return style_name, prompt


def _style_prompt_moon_goddess(ctx: CyprusImageContext) -> Tuple[str, str]:
    """
    Стиль 4: мифологичная сцена с богиней Луной,
    опираемся на астропрогноз (фаза+знак).
    """
    goddess = _astro_visual_goddess(ctx.astro_mood_en)
    weather_text = _weather_flavour(ctx.marine_mood, ctx.inland_mood)

    # Если распарсить фазу/знак не удалось — откатываемся к основному стилю.
    if not goddess:
        return _style_prompt_sea_mountains(ctx)

    style_name = "moon_goddess"

    prompt = (
        "Mythic evening scene above the Cyprus coast. "
        f"{weather_text} "
        f"Below, the coastline reflects this mood: {ctx.marine_mood or 'warm Mediterranean shoreline with gentle waves and salty air'}. "
        f"Inland you can feel: {ctx.inland_mood or 'quieter, cooler hills and villages with grounded energy'}. "
        f"In the sky, {goddess}. "
        "The sea and the land are softly lit by her light, with subtle reflections on the water and the coastline. "
        "Rich colours, cinematic fantasy illustration, high detail, soft glow. "
        "No text, no captions, no labels, no logos, absolutely no letters or numbers anywhere. "
        "Square composition, suitable as a mystical weather thumbnail for social media."
    )

    return style_name, prompt


# Базовый список функций стилей (на всякий случай, если понадобится где-то ещё)
_STYLES = [
    _style_prompt_sea_mountains,
    _style_prompt_map_mood,
    _style_prompt_mini_dashboard,
    _style_prompt_moon_goddess,
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
    - стиль «sea_mountains» имеет повышенный вес, карта/дашборд/богиня — реже;
    - mood'ы моря/суши и астротекст влияют на атмосферу и погоду в сцене,
      включая ветреные и дождливые варианты.
    """
    ctx = CyprusImageContext(
        date=date,
        marine_mood=(marine_mood or "").strip(),
        inland_mood=(inland_mood or "").strip(),
        astro_mood_en=(astro_mood_en or "").strip(),
    )

    rnd = random.Random()
    # Соль по дате, чтобы стиль для конкретного дня был стабильным
    rnd.seed(date.toordinal() * 9973 + 42)

    # Весовой выбор:
    #   sea_mountains  — основной (3 «билета»),
    #   map_mood       — чуть реже (2),
    #   mini_dashboard — изредка (1),
    #   moon_goddess   — иногда (1), когда есть осмысленный астротекст.
    weighted_style_fns = (
        [_style_prompt_sea_mountains] * 1
        + [_style_prompt_map_mood] * 1
        + [_style_prompt_mini_dashboard] * 1
        + [_style_prompt_moon_goddess] * 1
    )

    style_fn = rnd.choice(weighted_style_fns)
    style_name, prompt = style_fn(ctx)

    logger.info(
        "CY_IMG_PROMPT: date=%s style=%s marine=%r inland=%r astro=%r",
        date.isoformat(),
        style_name,
        ctx.marine_mood,
        ctx.inland_mood,
        ctx.astro_mood_en,
    )

    return prompt, style_name
