#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
image_prompt_cy_morning.py — prompts/templates for Cyprus *morning* images (VayboMeter).

Goal:
- Keep ALL prompt tuning in one place.
- Support 5 rotating styles (1..5), with deterministic AUTO choice per date.
- Generate *background-first* prompts (no text) so you can overlay exact metrics in code later.

ENV (recommended; handled by caller, not required here):
  CY_MORNING_STYLE=auto|1|2|3|4|5
  CY_MORNING_STYLE_SEED_OFFSET=0   (integer)
  CY_MORNING_IMG_SIZE=1024
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Union

import pendulum


# ────────────────────────── styles ──────────────────────────

STYLE_NAMES = {
    1: "data_card",
    2: "mood_scene",
    3: "mascot",
    4: "landscape_badges",
    5: "split_sea_mountains",
}

STYLE_TITLES = {
    1: "Data Card (clean background for metrics overlay)",
    2: "Mood Scene (cinematic morning atmosphere)",
    3: "Mascot (recurring character)",
    4: "Landscape + 3 Badges (empty placeholders)",
    5: "Split Sea / Mountains (coast vs Troodos)",
}


@dataclass(frozen=True)
class MorningMetrics:
    # These are OPTIONAL (can be None) — prompts should still work.
    warm_city: Optional[str] = None
    warm_temp_c: Optional[float] = None
    cool_city: Optional[str] = None
    cool_temp_c: Optional[float] = None

    sunset_hhmm: Optional[str] = None

    aqi_value: Optional[float] = None
    aqi_bucket: Optional[str] = None  # e.g. "низкий", "умеренный"

    kp_value: Optional[float] = None
    kp_bucket: Optional[str] = None   # e.g. "спокойно", "умеренно", "буря"

    storm_warning: bool = False


def _safe_str(x: object, default: str = "") -> str:
    s = (str(x).strip() if x is not None else "")
    return s if s else default


def _fmt_temp(t: Optional[float]) -> str:
    if not isinstance(t, (int, float)):
        return ""
    return f"{float(t):.0f}°C"


def normalize_style_id(style: Union[str, int, None]) -> Union[int, str]:
    """Returns 1..5, or 'auto'."""
    if style is None:
        return "auto"
    if isinstance(style, int):
        return style if style in STYLE_NAMES else "auto"
    s = str(style).strip().lower()
    if s in ("auto", "a", "random", "rotate"):
        return "auto"
    try:
        i = int(s)
        return i if i in STYLE_NAMES else "auto"
    except Exception:
        return "auto"


def choose_morning_style_id(
    date_local: pendulum.Date,
    style: Union[str, int, None] = "auto",
    seed_offset: int = 0,
) -> int:
    """
    Deterministic style selection:
      style_id = ((date.toordinal() + seed_offset) % 5) + 1

    - Ensures daily rotation without "jumping" on retries.
    - seed_offset allows shifting the rotation without code edits.
    """
    st = normalize_style_id(style)
    if st != "auto" and isinstance(st, int):
        return st
    return ((int(date_local.toordinal()) + int(seed_offset)) % 5) + 1


# ────────────────────────── prompt building blocks ──────────────────────────

def _base_negative_prompt(no_text: bool = True) -> str:
    neg = [
        "no watermark",
        "no logos",
        "no brand names",
        "no QR codes",
        "no UI screenshots",
        "no low-resolution",
        "no artifacts",
        "no distorted faces",
        "no extra limbs",
        "no gore",
    ]
    if no_text:
        neg += [
            "no text",
            "no letters",
            "no numbers",
            "no captions",
            "no subtitles",
            "no typographic elements",
        ]
    return ", ".join(neg)


def _weather_mood_snippet(m: MorningMetrics) -> str:
    if m.storm_warning:
        return "dramatic windy morning, fast-moving clouds, dynamic light"
    # If no storm, keep it calm & optimistic.
    return "calm pleasant morning, soft sunlight, gentle breeze"


def _palette_snippet(m: MorningMetrics) -> str:
    # Light heuristics by temperature spread; keep safe if temps missing.
    warm = m.warm_temp_c if isinstance(m.warm_temp_c, (int, float)) else None
    cool = m.cool_temp_c if isinstance(m.cool_temp_c, (int, float)) else None

    if warm is not None and warm >= 28:
        return "warm golden palette, sunlit highlights, slightly tropical vibe"
    if cool is not None and cool <= 10:
        return "cool crisp palette, clear air, soft pastel sky"
    return "soft sunrise palette, pastel sky, clean airy tones"


def _composition_snippet(style_id: int) -> str:
    if style_id == 1:
        return (
            "minimal composition with large clean negative space for an overlay card; "
            "subtle gradient background; gentle bokeh; no clutter"
        )
    if style_id == 2:
        return "cinematic wide shot, depth of field, natural perspective, pleasing composition"
    if style_id == 3:
        return (
            "single friendly recurring character (gender-neutral, cute but not childish), "
            "clear silhouette, simple shapes, expressive but subtle"
        )
    if style_id == 4:
        return (
            "landscape background with three empty circular badge placeholders at the bottom; "
            "placeholders are blank shapes (no icons, no text)"
        )
    if style_id == 5:
        return (
            "split composition: left side Cyprus coast/sea, right side Troodos mountains; "
            "balanced lighting, consistent style, clean division line"
        )
    return "clean composition"


def _context_snippet(region_name: str, m: MorningMetrics) -> str:
    warm_city = _safe_str(m.warm_city)
    cool_city = _safe_str(m.cool_city)

    bits = [f"{region_name} morning"]
    if warm_city or cool_city:
        # Mention locations as *scene context*, not as text to render.
        if warm_city and cool_city:
            bits.append(f"coast vs inland contrast: {warm_city} feels warmer, {cool_city} cooler")
        else:
            bits.append(f"local atmosphere inspired by {warm_city or cool_city}")
    return "; ".join(bits)


# ────────────────────────── full prompt by style ──────────────────────────

def build_cyprus_morning_prompt(
    date_local: pendulum.Date,
    metrics: MorningMetrics,
    region_name: str = "Cyprus",
    style: Union[str, int, None] = "auto",
    seed_offset: int = 0,
    aspect: str = "1:1",
    no_text: bool = True,
) -> Tuple[str, str, int]:
    """
    Returns (prompt, style_name, style_id).

    Notes:
    - Prompt is in English on purpose: image models usually follow EN more reliably.
    - Designed as *background first*; overlay exact numbers in code if needed.
    """
    style_id = choose_morning_style_id(date_local, style=style, seed_offset=seed_offset)
    style_name = STYLE_NAMES.get(style_id, "auto")
    style_title = STYLE_TITLES.get(style_id, style_name)

    weather = _weather_mood_snippet(metrics)
    palette = _palette_snippet(metrics)
    comp = _composition_snippet(style_id)
    ctx = _context_snippet(region_name, metrics)

    # small “data hints” as mood only (NOT for text rendering)
    warm_temp = _fmt_temp(metrics.warm_temp_c)
    cool_temp = _fmt_temp(metrics.cool_temp_c)
    aqi_hint = _safe_str(metrics.aqi_bucket) or ("good air" if metrics.aqi_value and metrics.aqi_value <= 50 else "")
    kp_hint = _safe_str(metrics.kp_bucket)

    data_mood = []
    if warm_temp or cool_temp:
        data_mood.append(f"temperature contrast: warm about {warm_temp or '—'} and cool about {cool_temp or '—'}")
    if aqi_hint:
        data_mood.append(f"air quality mood: {aqi_hint}")
    if kp_hint:
        data_mood.append(f"geomagnetic mood: {kp_hint}")
    data_mood_snippet = "; ".join(data_mood) if data_mood else ""

    neg = _base_negative_prompt(no_text=no_text)

    # Style-specific flavor
    if style_id == 1:
        flavor = (
            "modern minimalist background for a weather card; "
            "soft gradient sunrise sky over Cyprus; subtle abstract shapes; "
            "leave a clean translucent panel area (empty) for later overlay; "
            "high clarity, elegant, calm"
        )
    elif style_id == 2:
        flavor = (
            "photorealistic cinematic sunrise in Cyprus, peaceful street or seafront, "
            "natural light, gentle lens bloom, realistic textures"
        )
    elif style_id == 3:
        outfit = "light jacket"  # default
        if isinstance(metrics.cool_temp_c, (int, float)) and metrics.cool_temp_c <= 10:
            outfit = "warm hoodie"
        elif isinstance(metrics.warm_temp_c, (int, float)) and metrics.warm_temp_c >= 25:
            outfit = "light summer outfit"
        flavor = (
            f"a friendly recurring mascot character holding morning coffee, wearing {outfit}; "
            "simple clean illustration; Cyprus morning background; warm approachable vibe"
        )
    elif style_id == 4:
        flavor = (
            "beautiful Cyprus morning landscape, clean sky; "
            "three blank circular badge placeholders at the bottom (empty shapes only); "
            "high readability on mobile, strong silhouette, no clutter"
        )
    elif style_id == 5:
        flavor = (
            "split-screen composition: left side Mediterranean coast, right side Troodos mountains; "
            "consistent art style, soft sunrise lighting, gentle contrast"
        )
    else:
        flavor = "Cyprus morning scenery"

    prompt_parts = [
        f"STYLE: {style_title}",
        f"ASPECT: {aspect}",
        ctx,
        weather,
        palette,
        comp,
        flavor,
    ]
    if data_mood_snippet:
        prompt_parts.append(data_mood_snippet)

    prompt_parts += [
        "ultra clean, high quality, high detail, professional composition",
        f"NEGATIVE: {neg}",
    ]

    prompt = ". ".join([p for p in prompt_parts if p]).strip()
    return prompt, style_name, style_id


__all__ = [
    "MorningMetrics",
    "choose_morning_style_id",
    "normalize_style_id",
    "build_cyprus_morning_prompt",
    "STYLE_NAMES",
    "STYLE_TITLES",
]
