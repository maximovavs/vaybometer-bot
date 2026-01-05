#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
image_prompt_cy.py

Построение промтов для вечернего кипрского сообщения
с несколькими сценариями картинок и «взвешенным» детерминированным выбором.

Стили:
1) sea_mountains  — море + горы + Луна, кинематографичный вечер.
2) map_mood       — стилизованная карта-настроение Кипра.
3) mini_dashboard — утилитарный «дашборд»-метафора без текста/цифр.
4) moon_goddess   — мифологичная Луна-богиня, играющая со знаком зодиака.

Особенности:
- Стиль выбирается детерминированно от даты (чтобы при ретраях не «скакало»),
  но с хорошей вариативностью между днями.
- Фаза Луны и знак берутся из lunar_calendar.json (на ЗАВТРА),
  чтобы визуально совпадать с «погодой на завтра».
- storm_warning (из post_common.py) влияет на выбор стиля и на описание сцены:
  при шторме богиня по умолчанию отключается (можно разрешить env-переменной).
- Много «свободы» для генератора: избегаем чрезмерно жёстких деталей,
  оставляя ключевые якоря (Кипр, море, горы/контур острова, Луна, настроение).
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import random
import logging
import json
import os
from pathlib import Path
from typing import Tuple, Optional, List


@dataclasses.dataclass(frozen=True)
class CyprusImageContext:
    date: dt.date
    marine_mood: str
    inland_mood: str
    astro_mood_en: str = ""
    storm_warning: bool = False


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


# ───────────────────── утилиты детерминированной «случайности» ─────────────────────


def _choice_by_date(ctx: CyprusImageContext, salt: str, options: List[str]) -> str:
    """Детерминированный выбор варианта от даты + «соли»."""
    seed = ctx.date.toordinal() * 10007 + sum(ord(c) for c in salt)
    rnd = random.Random(seed)
    return rnd.choice(options)


def _rand_by_date(date: dt.date, salt: str) -> random.Random:
    seed = date.toordinal() * 9973 + sum(ord(c) for c in salt) + 42
    return random.Random(seed)


# ───────────────────── лунный календарь ─────────────────────


def _load_calendar(path: str = "lunar_calendar.json") -> dict:
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("days"), dict):
        return data["days"]
    return data if isinstance(data, dict) else {}


def _astro_phrase_from_calendar(date_for_astro: dt.date) -> str:
    """Собираем короткую EN-фразу вроде 'Full Moon in Taurus' из lunar_calendar.json."""
    cal = _load_calendar()
    rec = cal.get(date_for_astro.isoformat(), {})
    if not isinstance(rec, dict):
        return ""

    phase_raw = (rec.get("phase_name") or rec.get("phase") or "").lower()
    sign_raw = (rec.get("sign") or rec.get("zodiac") or "").lower()

    phase_en: Optional[str] = None
    if "полнолуние" in phase_raw or "full" in phase_raw:
        phase_en = "Full Moon"
    elif "новолуние" in phase_raw or "new" in phase_raw:
        phase_en = "New Moon"
    elif (
        "первая четверть" in phase_raw
        or "first quarter" in phase_raw
        or "растущ" in phase_raw
        or "waxing" in phase_raw
    ):
        phase_en = "First Quarter Moon"
    elif (
        "последняя четверть" in phase_raw
        or "last quarter" in phase_raw
        or "убывающ" in phase_raw
        or "waning" in phase_raw
    ):
        phase_en = "Last Quarter Moon"

    sign_en: Optional[str] = None
    if sign_raw:
        for ru, en in ZODIAC_RU_EN.items():
            if ru in sign_raw:
                sign_en = en
                break
        if sign_en is None:
            for en in ZODIAC_EN:
                if en.lower() in sign_raw:
                    sign_en = en
                    break

    parts: List[str] = []
    if phase_en:
        parts.append(phase_en)
    if sign_en:
        parts.append(f"in {sign_en}")

    return " ".join(parts)


def _astro_is_full_or_new(text: str) -> Tuple[bool, bool]:
    s = (text or "").lower()
    return ("full moon" in s), ("new moon" in s)


# ───────────────────── анализ погоды ─────────────────────


def _weather_flavour(marine_mood: str, inland_mood: str, storm_warning: bool) -> str:
    """
    Вытащить «подтон» — ветрено / дождливо / спокойно — из mood'ов.
    Если storm_warning=True, считаем, что драматичный шторм — в приоритете,
    даже если в текстах mood'ов ключевых слов нет.
    """
    if storm_warning:
        return (
            "Stormy evening: strong coastal wind and gusts, bigger waves, "
            "dramatic fast-moving clouds, wet reflections and high-energy sea mood."
        )

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


# ───────────────────── астрокартинка ─────────────────────


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
    elif "первая четверть" in s or "first quarter" in s or "waxing" in s or "растущ" in s:
        phase = "first_quarter"
    elif "последняя четверть" in s or "last quarter" in s or "waning" in s or "убывающ" in s:
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


def _astro_visual_sky(text: str, storm_warning: bool) -> str:
    """
    Вариант «рисуем небо по астропрогнозу»: полнолуние / новолуние и т.п.
    Если storm_warning=True, добавляем драматичность, но Луна остаётся якорем.
    """
    phase, sign = _parse_moon_phase_and_sign(text)
    if not phase and not sign and not storm_warning:
        return ""

    parts: list[str] = []

    if storm_warning:
        parts.append("a dramatic stormy sky with fast layered clouds, but the Moon is still visible")

    if phase == "full":
        parts.append("a bright full Moon hanging low over the sea")
    elif phase == "new":
        parts.append("a very dark night sky with only a thin lunar crescent")
    elif phase in ("first_quarter", "last_quarter"):
        parts.append("a strong crescent Moon with clear contrast in the sky")
    elif not storm_warning:
        parts.append("a calm night sky with a clearly visible Moon")

    if sign:
        parts.append(f"the atmosphere subtly reflects the energy of {sign}")

    return " ".join([p for p in parts if p])


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


# ───────────────────── палитры ─────────────────────


def _sea_palette(ctx: CyprusImageContext) -> str:
    if ctx.storm_warning:
        return _choice_by_date(
            ctx,
            "sea_palette_storm",
            [
                "stormy teal and steel-blue sky with heavy clouds and electric moonlit highlights",
                "deep navy and graphite clouds with cold silver moonlight over rough sea",
                "dark indigo storm sky with sharp white highlights and churning turquoise water",
            ],
        )
    return _choice_by_date(
        ctx,
        "sea_palette",
        [
            "soft coral and peach near the horizon, fading into deep indigo and midnight blue above",
            "magenta and violet twilight sky with electric blue highlights near the horizon",
            "cold silver-blue moonlit sky with subtle greenish aurora-like tints over the sea",
            "stormy teal and steel-blue sky with heavy clouds and small gaps of warm light",
        ],
    )


def _map_palette(ctx: CyprusImageContext) -> str:
    return _choice_by_date(
        ctx,
        "map_palette",
        [
            "turquoise and deep teal water with golden-orange sky glow",
            "soft cyan sea against a lavender and pink twilight sky",
            "deep navy-blue sea with warm copper and amber sky tones",
        ],
    )


def _dashboard_palette(ctx: CyprusImageContext) -> str:
    return _choice_by_date(
        ctx,
        "dashboard_palette",
        [
            "cool teal and navy background with soft neon-like accents",
            "warm gradient from peach to violet with calm turquoise sea area",
            "deep blue background with subtle cyan and magenta glows",
        ],
    )


# ───────────────────── стили ─────────────────────


def _style_prompt_map_mood(ctx: CyprusImageContext) -> Tuple[str, str]:
    style_name = "map_mood"

    weather_text = _weather_flavour(ctx.marine_mood, ctx.inland_mood, ctx.storm_warning)
    astro_sky = _astro_visual_sky(ctx.astro_mood_en, ctx.storm_warning)
    palette = _map_palette(ctx)

    prompt = (
        "Dreamy stylized flat map of Cyprus floating above the Mediterranean sea at twilight. "
        "The island shape is clean and easily recognizable, but without any labels. "
        f"Water is rendered with {palette}, with soft reflections and gentle gradients. "
        "Sky fades from warm dusk near the horizon into deeper night tones. "
        "Coastline mood: "
        f"{ctx.marine_mood or 'warm, welcoming and slightly breezy by the sea'}. "
        "Inland mood: "
        f"{ctx.inland_mood or 'cooler, quieter hills and towns in the background'}. "
        f"{weather_text} "
        "Simple clean shapes, subtle texture, cinematic lighting, high quality digital illustration. "
        "No text, no captions, no labels, no logos, no UI, no country names, absolutely no letters or numbers anywhere. "
        "Square aspect ratio, suitable as Telegram and Facebook post thumbnail."
    )

    if ctx.astro_mood_en:
        prompt += f" The overall astro energy feels like: {ctx.astro_mood_en}."
    if astro_sky:
        prompt += f" The sky subtly shows: {astro_sky}."

    return style_name, prompt


def _style_prompt_sea_mountains(ctx: CyprusImageContext) -> Tuple[str, str]:
    style_name = "sea_mountains"

    weather_text = _weather_flavour(ctx.marine_mood, ctx.inland_mood, ctx.storm_warning)
    astro_sky = _astro_visual_sky(ctx.astro_mood_en, ctx.storm_warning)
    palette = _sea_palette(ctx)

    prompt = (
        "Cozy Cyprus coastal evening scene. "
        "Mediterranean sea in the foreground with visible texture and reflections; "
        f"the seaside mood feels like: {ctx.marine_mood or 'perfect for a relaxed seaside walk or quiet SUP session'}. "
        "In the distance, soft layered silhouettes of mountains symbolizing Troodos and inland areas, "
        f"matching this inland mood: {ctx.inland_mood or 'cool, peaceful and grounding'}. "
        f"{weather_text} "
        "Above everything, the night sky follows this palette: "
        f"{palette}. "
        "A clearly visible Moon is a key anchor of the composition, with a shimmering reflection path on the water. "
        "Atmospheric, cinematic lighting, high quality digital painting, no people. "
        "No text, no captions, no labels, no logos, absolutely no letters or numbers anywhere. "
        "Square format composition, suitable as a weather thumbnail for social media."
    )

    if ctx.astro_mood_en:
        prompt += f" The Moon and sky subtly reflect this astro mood: {ctx.astro_mood_en}."
    if astro_sky:
        prompt += f" Visually the sky looks like: {astro_sky}."

    return style_name, prompt


def _style_prompt_mini_dashboard(ctx: CyprusImageContext) -> Tuple[str, str]:
    style_name = "mini_dashboard"

    weather_text = _weather_flavour(ctx.marine_mood, ctx.inland_mood, ctx.storm_warning)
    astro_sky = _astro_visual_sky(ctx.astro_mood_en, ctx.storm_warning)
    palette = _dashboard_palette(ctx)

    prompt = (
        "Modern minimalist weather dashboard–style illustration for Cyprus, but purely pictorial. "
        "Flat silhouette of the island in the center, floating over the sea; "
        "a few small glowing abstract markers along the coastline and one in the inland area. "
        f"Coast feels like: {ctx.marine_mood or 'mild, slightly cloudy and comfortable'}; "
        f"inland feels like: {ctx.inland_mood or 'fresh, stable and quiet'}. "
        f"{weather_text} "
        "Above the island, the Moon and a few clouds hint at the astro mood. "
        f"Background uses this palette: {palette}. "
    )

    if ctx.astro_mood_en:
        prompt += f" The astro mood for tomorrow is: {ctx.astro_mood_en}. "
    if astro_sky:
        prompt += f"Sky area visually reflects: {astro_sky}. "

    prompt += (
        "Clean flat design, smooth gradients, subtle depth, no data tables. "
        "No text, no numbers, no UI widgets with labels, no country names, absolutely no typography or letters of any kind. "
        "Square layout, high quality digital illustration, optimized as a neutral weather thumbnail."
    )

    return style_name, prompt


def _style_prompt_moon_goddess(ctx: CyprusImageContext) -> Tuple[str, str]:
    allow_in_storm = os.getenv("CY_IMG_ALLOW_GODDESS_IN_STORM", "0").strip().lower() in ("1", "true", "yes", "on")
    if ctx.storm_warning and not allow_in_storm:
        return _style_prompt_sea_mountains(ctx)

    goddess = _astro_visual_goddess(ctx.astro_mood_en)
    weather_text = _weather_flavour(ctx.marine_mood, ctx.inland_mood, ctx.storm_warning)

    # Если распарсить фазу/знак не удалось — откат к основному стилю.
    if not goddess:
        return _style_prompt_sea_mountains(ctx)

    style_name = "moon_goddess"
    palette = _sea_palette(ctx)

    prompt = (
        "Mythic evening scene above the Cyprus coast. "
        f"{weather_text} "
        f"Below, the coastline reflects this mood: {ctx.marine_mood or 'warm Mediterranean shoreline with gentle waves and salty air'}. "
        f"Inland you can feel: {ctx.inland_mood or 'quieter, cooler hills and villages with grounded energy'}. "
        f"In the sky, {goddess}. "
        f"The sky and sea follow this color palette: {palette}. "
        "The sea and the land are softly lit by her light, with subtle reflections on the water and the coastline. "
        "Rich colours, cinematic fantasy illustration, high detail, soft glow. "
        "No text, no captions, no labels, no logos, absolutely no letters or numbers anywhere. "
        "Square composition, suitable as a mystical weather thumbnail for social media."
    )

    return style_name, prompt


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
    storm_warning: bool = False,
) -> Tuple[str, str]:
    """
    Собирает промт и возвращает (prompt_text, style_name).

    Правила выбора стиля:
    - при storm_warning=True: усиливаем вероятность «погодных» стилей,
      moon_goddess по умолчанию отключаем (но можно включить env-переменной
      CY_IMG_ALLOW_GODDESS_IN_STORM=1);
    - при Full/New Moon (и без шторма): moon_goddess получает повышенный вес,
      но всё равно остаётся вероятностной, чтобы посты не были одинаковыми.
    """
    # Астроданные берём на ЗАВТРА, чтобы картинка была синхронна с прогнозом
    cal_phrase = _astro_phrase_from_calendar(date + dt.timedelta(days=1))
    if cal_phrase and astro_mood_en:
        astro_combined = f"{cal_phrase}. {astro_mood_en}"
    elif cal_phrase:
        astro_combined = cal_phrase
    else:
        astro_combined = astro_mood_en or ""

    ctx = CyprusImageContext(
        date=date,
        marine_mood=(marine_mood or "").strip(),
        inland_mood=(inland_mood or "").strip(),
        astro_mood_en=astro_combined.strip(),
        storm_warning=bool(storm_warning),
    )

    is_full, is_new = _astro_is_full_or_new(ctx.astro_mood_en)

    # (Опционально) Можно принудительно включать богиню на Full/New Moon (если нет шторма)
    force_goddess = os.getenv("CY_IMG_FORCE_GODDESS_ON_FULL_NEW", "0").strip().lower() in ("1","true","yes","on")
    if force_goddess and (is_full or is_new) and not ctx.storm_warning:
        style_name, prompt = _style_prompt_moon_goddess(ctx)
        logger.info(
            "CY_IMG_PROMPT: date=%s style=%s storm=%s marine=%r inland=%r astro=%r",
            date.isoformat(),
            style_name,
            ctx.storm_warning,
            ctx.marine_mood,
            ctx.inland_mood,
            ctx.astro_mood_en,
        )
        return prompt, style_name

    # Детерминированный RNG по дате (чтобы ретраи не меняли стиль)
    rnd = _rand_by_date(date, "cy_img_style")

    # Базовые веса (чуть более разнообразно, чем раньше)
    weights = {
        "sea_mountains": 3,
        "map_mood": 2,
        "mini_dashboard": 2,
        "moon_goddess": 1,
    }

    # Особые дни: Full/New Moon (если нет шторма)
    if (is_full or is_new) and not ctx.storm_warning:
        weights["moon_goddess"] += 3  # заметный приоритет, но не 100%
        weights["sea_mountains"] += 1

    # Шторм: приоритет погодных сцен
    if ctx.storm_warning:
        weights["sea_mountains"] += 3
        weights["map_mood"] += 1
        weights["mini_dashboard"] += 1
        # goddess — по умолчанию выключаем
        weights["moon_goddess"] = 0

    # Если все веса случайно стали нулями — страхуемся
    if sum(weights.values()) <= 0:
        weights["sea_mountains"] = 1

    # Собираем «лотерею» функций
    pool = []
    for fn in _STYLES:
        name = fn.__name__.replace("_style_prompt_", "")
        w = int(weights.get(name, 0))
        if w > 0:
            pool += [fn] * w

    style_fn = rnd.choice(pool)
    style_name, prompt = style_fn(ctx)

    logger.info(
        "CY_IMG_PROMPT: date=%s style=%s storm=%s marine=%r inland=%r astro=%r",
        date.isoformat(),
        style_name,
        ctx.storm_warning,
        ctx.marine_mood,
        ctx.inland_mood,
        ctx.astro_mood_en,
    )

    return prompt, style_name


__all__ = ["build_cyprus_evening_prompt"]
