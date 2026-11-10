#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic high-contrast weekly cover for Cyprus VayboMeter."""

from __future__ import annotations

import json
import logging
import os
from datetime import date
from pathlib import Path
from typing import Any, Mapping


RENDERER_VERSION = "cy_weekly_cover_v2_portrait"
BRANDING = "VAYBOMETER · CYPRUS"
TITLE = "ВАЙБ НЕДЕЛИ"
# One font size for BOTH source-text preflight and actual legacy image drawing.
_ARCHITECTURE_BODY_FONT_SIZE = 29


def _font(size: int, *, bold: bool = False):
    from PIL import ImageFont

    names = (
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/liberation2/LiberationSans-Regular.ttf",
    )
    for name in names:
        if Path(name).exists():
            return ImageFont.truetype(name, size=size)
    return ImageFont.load_default()


def _section_value(text: str, heading: str) -> str:
    lines = [line.strip() for line in str(text or "").splitlines()]
    for index, line in enumerate(lines):
        if line == heading:
            for candidate in lines[index + 1:]:
                if candidate:
                    return candidate
    return ""


def _week_label(text: str, start: date) -> str:
    for raw in str(text or "").splitlines():
        line = raw.strip()
        if line.startswith("🗓 Вайб недели:"):
            return line.split(":", 1)[1].strip()
    return start.isoformat()


def _wrap(draw: Any, text: str, font: Any, max_width: int, max_lines: int = 2) -> list[str]:
    words = str(text or "").split()
    if not words:
        return ["ДАННЫЕ ОБНОВЛЯЮТСЯ"]
    lines: list[str] = []
    current = ""
    for word in words:
        if (os.getenv("WEEKLY_PHOTO_COVER_ENABLED", "0").strip() == "1"
                and draw.textbbox((0,0),word,font=font)[2] > max_width):
            return ["ПОДРОБНОСТИ В ПОСТЕ"]
        probe = f"{current} {word}".strip()
        box = draw.textbbox((0, 0), probe, font=font)
        if box[2] - box[0] <= max_width:
            current = probe
            continue
        if current:
            lines.append(current)
        current = word
        if len(lines) >= max_lines:
            break
    if current and len(lines) < max_lines:
        lines.append(current)
    if len(lines) == max_lines and len(" ".join(lines).split()) < len(words):
        if os.getenv("WEEKLY_PHOTO_COVER_ENABLED", "0").strip() == "1":
            # In the R10 photo-on fallback, don't visually clip safety/forecast
            # statements. The complete authoritative warning remains in text.
            return ["ПОДРОБНОСТИ В ПОСТЕ"]
        last = lines[-1]
        while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines[:max_lines]



# Only the exact R10 photo *asset* failures below permit an R12 attempt.
# In particular, no R10 safety/refusal message or arbitrary runtime exception
# can ever promote a comforting architectural scene.
_R12_PHOTO_ASSET_FAILURES = {
    "Cyprus weekly photo assets require independent visual approval": "photo_global_unapproved",
    "weekly scene is unapproved": "photo_scene_unapproved",
    "missing/bad weekly asset path": "photo_asset_missing",
    "weekly asset provenance mismatch": "photo_asset_sha_mismatch",
    "weekly candidate must be full-resolution verified JPEG": "photo_asset_geometry",
}


def _photo_refusal_reason(exc: Exception) -> tuple[str, bool]:
    if type(exc) is not ValueError:
        return "photo_runtime_error", False
    message = str(exc)
    if message in _R12_PHOTO_ASSET_FAILURES:
        return _R12_PHOTO_ASSET_FAILURES[message], True
    if message.startswith("R10: explicit warning"):
        return "r10_explicit_warning", False
    if message.startswith("R10: weather/safety warning"):
        return "r10_weather_safety_warning", False
    if "weather/safety warning" in message:
        return "source_weather_safety_warning", False
    if message.startswith("R10:"):
        return "r10_refused", False
    return "photo_source_or_visual_refused", False


def _landmark_refusal_reason(exc: Exception) -> tuple[str, bool]:
    if type(exc) is not ValueError:
        return "landmark_runtime_error", False
    message = str(exc)
    if message == "R12: no independently approved landmark assets":
        return "landmark_unapproved", False
    if message == "R12: asset SHA mismatch":
        return "landmark_asset_sha_mismatch", False
    if message == "R12: bad asset path":
        return "landmark_asset_missing", False
    if message.startswith("R12:"):
        return "landmark_safety_or_source_refused", False
    return "landmark_r10_validation_refused", False


def render_weekly_cover(text: str, *, start: date, output_path: str | Path,
                        trusted_weather: Mapping[str, Any] | None = None) -> dict[str, Any]:
    # Strictly OFF unless set exactly to 1; never mutate the historical renderer.
    if os.getenv("WEEKLY_PHOTO_COVER_ENABLED", "0").strip() == "1":
        try:
            from weekly_photo_cover_cy import render_photo_cover
            return render_photo_cover(text, start=start, output_path=output_path, trusted_weather=trusted_weather)
        except Exception as exc:
            reason, asset_only = _photo_refusal_reason(exc)
            # Stable reason labels avoid leaking source strings into CI logs.
            logging.getLogger(__name__).warning(
                "Cyprus weekly strict photo rejected reason=%s", reason)
            # R12 cannot override any R10 source, weather, semantic or safety
            # refusal. Only an independently approved/verified photo asset
            # failure is eligible, and the landmark rechecks R10 source/WMO.
            if asset_only:
                try:
                    from weekly_architecture_cover_cy import render_landmark_cover
                    return render_landmark_cover(
                        text, start=start, output_path=output_path,
                        trusted_weather=trusted_weather)
                except Exception as landmark_error:
                    landmark_reason, _ = _landmark_refusal_reason(landmark_error)
                    logging.getLogger(__name__).warning(
                        "Cyprus weekly landmark refused reason=%s; using legacy",
                        landmark_reason)
    from PIL import Image, ImageDraw, PngImagePlugin

    week_label = _week_label(text, start)
    main_fact = _section_value(text, "✨ Главный фон недели")
    weather_fact = _section_value(text, "🌦 Погода")
    sea_fact = _section_value(text, "🌊 Море")
    variant = ("sea_glass", "warm_stone", "deep_blue")[start.isocalendar().week % 3]

    palettes = {
        "sea_glass": ((24, 79, 104), (110, 185, 193), (243, 221, 158)),
        "warm_stone": ((74, 82, 93), (199, 162, 112), (245, 230, 195)),
        "deep_blue": ((17, 46, 78), (62, 133, 157), (228, 214, 171)),
    }
    top, middle, bottom = palettes[variant]
    width, height = 1080, 1350
    image = Image.new("RGB", (width, height), top)
    draw = ImageDraw.Draw(image)
    for y in range(height):
        ratio = y / (height - 1)
        if ratio < 0.62:
            local = ratio / 0.62
            color = tuple(round(top[i] * (1 - local) + middle[i] * local) for i in range(3))
        else:
            local = (ratio - 0.62) / 0.38
            color = tuple(round(middle[i] * (1 - local) + bottom[i] * local) for i in range(3))
        draw.line((0, y, width, y), fill=color)

    # R12-H2 affects only the legacy *background* after the unmodified
    # R10/R12 routing. Never reinterpret safety refusals or change card text.
    # OFF/unapproved/tampered assets retain the byte-identical gradient.
    architecture_sha = None
    if os.getenv("WEEKLY_PHOTO_COVER_ENABLED", "0").strip() == "1":
        try:
            from weekly_legacy_architecture_cy import approved_architecture_background
            decorative = approved_architecture_background()
            if decorative is not None:
                candidate_image, candidate_sha = decorative
                candidate_draw = ImageDraw.Draw(candidate_image)
                candidate_font = _font(_ARCHITECTURE_BODY_FONT_SIZE, bold=True)
                # A scenic background is NOT allowed to obscure a sourced
                # warning, temperature or sea fact behind a truncation label.
                readable = all(
                    not fact or " ".join(_wrap(candidate_draw, fact, candidate_font, 815, 3))
                    == " ".join(fact.split())
                    for fact in (main_fact, weather_fact, sea_fact)
                )
                if readable:
                    image, architecture_sha = candidate_image, candidate_sha
                    draw = ImageDraw.Draw(image)
        except Exception as exc:
            logging.getLogger(__name__).warning(
                "Cyprus weekly legacy architecture unavailable error_type=%s; using gradient",
                type(exc).__name__)

    if architecture_sha is None:
        for offset in range(4):
            y = 1150 + offset * 38
            draw.arc((70, y - 42, 1010, y + 62), 195, 345, fill=(235, 246, 243), width=5)

    # R12-H10: visual-only glass overlay for an independently approved art
    # background. All source facts and the R10/R12 routing are unchanged.
    architecture_visible = architecture_sha is not None
    if architecture_visible:
        from PIL import ImageFilter
        glass_header = (64, 165, 1016, 336)
        glass_cards = ((88, 552, 992, 731), (88, 750, 992, 929),
                       (88, 948, 992, 1127))
        mask = Image.new("L", (width, height), 0)
        glass_mask = ImageDraw.Draw(mask)
        glass_mask.rounded_rectangle(glass_header, radius=34, fill=255)
        for rect in glass_cards:
            glass_mask.rounded_rectangle(rect, radius=26, fill=255)
        image.paste(image.filter(ImageFilter.GaussianBlur(radius=11)), (0, 0), mask)
        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        glass = ImageDraw.Draw(overlay)
        glass.rounded_rectangle(glass_header, radius=34,
                                fill=(7, 29, 45, 213), outline=(232, 244, 242, 170), width=2)
        accents = ((239, 192, 126), (134, 204, 220), (120, 209, 201))
        for rect, accent in zip(glass_cards, accents):
            glass.rounded_rectangle(rect, radius=26,
                                    fill=(7, 29, 45, 205), outline=(*accent, 163), width=2)
            glass.rounded_rectangle((112, rect[1] + 26, 117, rect[1] + 59),
                                    radius=2, fill=(*accent, 255))
        image = Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB")
        draw = ImageDraw.Draw(image)
        brand_y, title_y, date_y = 185, 219, 292
    else:
        header_box, brand_y, title_y, date_y = (64, 58, 1016, 300), 94, 145, 234
        draw.rounded_rectangle(header_box, radius=42, fill=(8, 30, 45),
                               outline=(235, 243, 240), width=3)
    draw.text((105, brand_y), BRANDING,
              font=_font(23 if architecture_visible else 27, bold=True),
              fill=(196, 228, 233) if architecture_visible else (185, 220, 229))
    draw.text((105, title_y), TITLE,
              font=_font(53 if architecture_visible else 66, bold=True),
              fill=(250, 251, 247))
    draw.text((108, date_y), week_label,
              font=_font(29 if architecture_visible else 34),
              fill=(221, 237, 236) if architecture_visible else (219, 231, 231))

    cards = (
        ("ГЛАВНОЕ", main_fact),
        ("ПОГОДА", weather_fact),
        ("МОРЕ", sea_fact),
    )
    y = 370
    body_font = _font(_ARCHITECTURE_BODY_FONT_SIZE if architecture_visible else 34, bold=True)
    label_font = _font(23 if architecture_visible else 24, bold=True)
    for index, (label, fact) in enumerate(cards):
        if architecture_visible:
            y = glass_cards[index][1]
        else:
            draw.rounded_rectangle((88, y, 992, y + 190), radius=26,
                                   fill=(249, 250, 247), outline=(225, 234, 232), width=2)
        draw.text((128 if architecture_visible else 125, y + (18 if architecture_visible else 24)),
                  label, font=label_font,
                  fill=accents[index] if architecture_visible else (31, 92, 111))
        lines = _wrap(draw, fact, body_font, 815, 3 if architecture_visible else 2)
        line_y = y + (59 if architecture_visible else 72)
        for line in lines:
            draw.text((128 if architecture_visible else 125, line_y), line,
                      font=body_font,
                      fill=(250, 251, 247) if architecture_visible else (17, 42, 58))
            line_y += 37 if architecture_visible else 46
        if not architecture_visible:
            y += 220

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    info = PngImagePlugin.PngInfo()
    metadata = {
        "renderer_version": RENDERER_VERSION,
        "region": "cyprus",
        "week_start": start.isoformat(),
        "week_label": week_label,
        "variant": variant,
        "main_fact": main_fact,
        "weather_fact": weather_fact,
        "sea_fact": sea_fact,
    }
    if architecture_sha is not None:
        metadata["background_role"] = "editorial_architecture_not_weather"
        metadata["background_asset_sha256"] = architecture_sha
    for key, value in metadata.items():
        info.add_text(key, str(value))
    image.save(output, format="PNG", optimize=True, pnginfo=info)
    metadata.update(path=str(output), width=width, height=height)
    return metadata


__all__ = ["RENDERER_VERSION", "render_weekly_cover"]
