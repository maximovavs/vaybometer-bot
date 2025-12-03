#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
image_prompt_cy.py

Построение промтов для вечернего кипрского сообщения
с тремя сценариями картинок и детерминированным выбором стиля.

Стили:
1) "map_mood"       — стилизованная карта-настроение Кипра.
2) "sea_mountains"  — море + горы + Луна.
3) "mini_dashboard" — более утилитарная картинка-дашборд, но без текста.

Функция build_cyprus_evening_prompt НЕ делает HTTP-запросы, она только
возвращает текст промта и имя выбранного стиля. Генерация картинки делается
через world_en.imagegen.generate_astro_image или иной бэкенд.
"""

from __future__ import annotations

import dataclasses
import datetime as dt
import random
from typing import Tuple


@dataclasses.dataclass(frozen=True)
class CyprusImageContext:
    """Мини-контекст для построения промта по Кипру."""
    date: dt.date
    # Краткий смысл для моря/берега — можно собрать из текста "Морские города".
    marine_mood: str
    # Краткий смысл для суши/гор — по Никосии/Тродосу.
    inland_mood: str
    # Краткий астротекст (например, строка из блока "Астрособытия" на EN).
    astro_mood_en: str = ""


def _style_prompt_map_mood(ctx: CyprusImageContext) -> Tuple[str, str]:
    """
    Стиль 1: "карта-настроение Кипра".
    """
    style_name = "map_mood"
    base_prompt = (
        "Dreamy stylized flat map of Cyprus at dusk, soft gradients. "
        "Warm Mediterranean coast glowing gently, suggesting relaxed seaside evening. "
        "Cooler tones over the mountains in the center, symbolizing inland areas. "
        "Subtle clouds in the sky and an almost full Moon, hinting at the astro mood: "
        f"{ctx.astro_mood_en or 'calm, grounded energy for tomorrow'}. "
        "No text, no labels, minimalist illustration, square format, "
        "suitable as Telegram and Facebook post thumbnail."
    )
    return style_name, base_prompt


def _style_prompt_sea_mountains(ctx: CyprusImageContext) -> Tuple[str, str]:
    """
    Стиль 2: "море + горы + Луна".
    """
    style_name = "sea_mountains"
    base_prompt = (
        "Cozy Cyprus coastal evening scene. "
        "Calm sea in the foreground with a quiet beach, warm air and gentle breeze — "
        f"{ctx.marine_mood or 'ideal for a relaxed seaside walk'}. "
        "In the distance, soft silhouettes of mountains symbolizing Troodos and inland areas — "
        f"{ctx.inland_mood or 'cooler and more quiet but still inviting'}. "
        "Above, an almost full Moon in a slightly hazy sky, reflecting the astro mood: "
        f"{ctx.astro_mood_en or 'grounded, slow and nurturing energy'}. "
        "Soft pastel colors, subtle gradients, no people, no text, "
        "minimalist digital illustration, square format."
    )
    return style_name, base_prompt


def _style_prompt_mini_dashboard(ctx: CyprusImageContext) -> Tuple[str, str]:
    """
    Стиль 3: более утилитарный "мини-дэшборд", но без текста/цифр.
    """
    style_name = "mini_dashboard"
    base_prompt = (
        "Modern minimalist weather dashboard illustration for Cyprus. "
        "Flat silhouette of the island in the center, with small glowing markers "
        "along the coastline for seaside towns and one marker in the center for inland areas. "
        f"Coast markers suggest warm, breezy evening by the sea: {ctx.marine_mood or 'mild and pleasant'}; "
        f"the inland marker suggests cooler, calmer conditions: {ctx.inland_mood or 'fresh and quiet'}. "
        "Above the island, an almost full Moon and a few soft clouds hint at the astro energy: "
        f"{ctx.astro_mood_en or 'stable, grounded and reflective'}. "
        "No text, no numbers, clean flat design, subtle gradients, "
        "square layout, suitable as Telegram/Facebook post thumbnail."
    )
    return style_name, base_prompt


_STYLES = [
    _style_prompt_map_mood,
    _style_prompt_sea_mountains,
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

    randomness детерминируется датой, чтобы в течение дня
    стиль не скакал между перезапусками. Если нужен полностью
    случайный выбор — можно убрать зависимость от date.toordinal().
    """
    ctx = CyprusImageContext(
        date=date,
        marine_mood=(marine_mood or "").strip(),
        inland_mood=(inland_mood or "").strip(),
        astro_mood_en=(astro_mood_en or "").strip(),
    )

    # Детерминированный "случайный" выбор по дате
    rnd = random.Random(ctx.date.toordinal())
    style_fn = rnd.choice(_STYLES)

    style_name, prompt = style_fn(ctx)
    return prompt, style_name


__all__ = ["build_cyprus_evening_prompt", "CyprusImageContext"]
