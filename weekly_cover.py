#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic high-contrast weekly cover for Cyprus VayboMeter."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any


RENDERER_VERSION = "cy_weekly_cover_v1"
BRANDING = "VAYBOMETER · CYPRUS"
TITLE = "ВАЙБ НЕДЕЛИ"


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
        last = lines[-1]
        while last and draw.textbbox((0, 0), last + "…", font=font)[2] > max_width:
            last = last[:-1].rstrip()
        lines[-1] = last + "…"
    return lines[:max_lines]


def render_weekly_cover(text: str, *, start: date, output_path: str | Path) -> dict[str, Any]:
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
    width = height = 1080
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

    for offset in range(4):
        y = 875 + offset * 30
        draw.arc((70, y - 40, 1010, y + 60), 195, 345, fill=(235, 246, 243), width=5)

    draw.rounded_rectangle((64, 58, 1016, 300), radius=42, fill=(8, 30, 45), outline=(235, 243, 240), width=3)
    draw.text((105, 94), BRANDING, font=_font(27, bold=True), fill=(185, 220, 229))
    draw.text((105, 145), TITLE, font=_font(66, bold=True), fill=(250, 251, 247))
    draw.text((108, 234), week_label, font=_font(34), fill=(219, 231, 231))

    cards = (
        ("ГЛАВНОЕ", main_fact),
        ("ПОГОДА", weather_fact),
        ("МОРЕ", sea_fact),
    )
    y = 350
    body_font = _font(34, bold=True)
    label_font = _font(24, bold=True)
    for label, fact in cards:
        draw.rounded_rectangle((88, y, 992, y + 150), radius=26, fill=(249, 250, 247), outline=(225, 234, 232), width=2)
        draw.text((125, y + 20), label, font=label_font, fill=(31, 92, 111))
        lines = _wrap(draw, fact, body_font, 815, 2)
        line_y = y + 58
        for line in lines:
            draw.text((125, line_y), line, font=body_font, fill=(17, 42, 58))
            line_y += 42
        y += 172

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
    for key, value in metadata.items():
        info.add_text(key, str(value))
    image.save(output, format="PNG", optimize=True, pnginfo=info)
    metadata.update(path=str(output), width=width, height=height)
    return metadata


__all__ = ["RENDERER_VERSION", "render_weekly_cover"]
