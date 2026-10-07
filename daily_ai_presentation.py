#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Deterministic full-bleed branded presentation for accepted Cyprus AI visuals."""

from __future__ import annotations

from datetime import date
import hashlib
from pathlib import Path
import re
from typing import Iterable

from PIL import Image, ImageDraw, ImageFont, ImageOps, PngImagePlugin


PRESENTATION_VERSION = "cy_ai_primary_branded_v2_full_bleed"
CANVAS_SIZE = (1080, 1350)
_TITLE_PANEL = (42, 84, 710, 230)
_TITLE_SAFE = (70, 100, 682, 158)
_DATE_PANEL = (865, 84, 1034, 240)
_FACT_PANEL = (38, 828, 762, 1232)
_FACT_SAFE = (78, 865, 724, 1198)
_GLASS_FILL = (226, 236, 240, 158)
_GLASS_OUTLINE = (248, 251, 252, 222)
_GLASS_SHADOW = (0, 0, 0, 42)
_TEXT_DARK = (13, 32, 47)
_TEXT_STROKE = (247, 250, 250)


def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    candidates = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
        if bold
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size=size)


def _sha256(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _date_label(value: str) -> str:
    raw = str(value or "").strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", raw):
        return date.fromisoformat(raw).strftime("%d.%m")
    match = re.fullmatch(r"(\d{2})\.(\d{2})\.\d{4}", raw)
    if match:
        return f"{match.group(1)}.{match.group(2)}"
    return raw[:10]


def _display_fact(value: str) -> str:
    """Match curated-cover typography without changing the facts contract."""
    return re.sub(r"^[^\wА-ЯЁ+]+\s*", "", str(value).strip(), flags=re.I)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for word in str(text).split():
        candidate = f"{current} {word}".strip()
        box = draw.textbbox((0, 0), candidate, font=font, stroke_width=1)
        if box[2] - box[0] <= width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _fit_single_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    safe_box: tuple[int, int, int, int],
    *,
    maximum_size: int,
    minimum_size: int,
) -> tuple[ImageFont.FreeTypeFont, int, tuple[int, int], tuple[int, int, int, int]]:
    max_width = safe_box[2] - safe_box[0]
    max_height = safe_box[3] - safe_box[1]
    for size in range(maximum_size, minimum_size - 1, -1):
        font = _font(size, bold=True)
        measured = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        width = measured[2] - measured[0]
        height = measured[3] - measured[1]
        if width > max_width or height > max_height:
            continue
        origin = (
            safe_box[0] - measured[0],
            safe_box[1] + (max_height - height) // 2 - measured[1],
        )
        bbox = draw.textbbox(origin, text, font=font, stroke_width=1)
        return font, size, origin, bbox
    raise RuntimeError(f"Cyprus AI presentation headline does not fit safe-zone: {text!r}")


def _fit_fact_layout(
    draw: ImageDraw.ImageDraw,
    facts: list[str],
    safe_box: tuple[int, int, int, int],
) -> tuple[ImageFont.FreeTypeFont, int, list[list[str]], int, int, int]:
    max_width = safe_box[2] - safe_box[0]
    max_height = safe_box[3] - safe_box[1]
    for size in range(40, 27, -1):
        font = _font(size, bold=True)
        wrapped = [_wrap(draw, fact, font, max_width) for fact in facts]
        if any(not lines or len(lines) > 2 for lines in wrapped):
            continue
        line_gap = max(5, round(size * 0.16))
        fact_gap = max(16, round(size * 0.50))
        total_height = 0
        for lines in wrapped:
            for line_index, line in enumerate(lines):
                measured = draw.textbbox((0, 0), line, font=font, stroke_width=1)
                total_height += measured[3] - measured[1]
                if line_index + 1 < len(lines):
                    total_height += line_gap
        total_height += fact_gap * max(0, len(wrapped) - 1)
        if total_height <= max_height:
            return font, size, wrapped, line_gap, fact_gap, total_height
    raise RuntimeError("Cyprus AI presentation facts do not fit full-bleed safe-zone")


def _glass_panel(
    overlay: Image.Image,
    box: tuple[int, int, int, int],
    *,
    radius: int,
) -> None:
    draw = ImageDraw.Draw(overlay)
    shadow = (box[0] + 4, box[1] + 6, box[2] + 4, box[3] + 6)
    draw.rounded_rectangle(shadow, radius=radius, fill=_GLASS_SHADOW)
    draw.rounded_rectangle(
        box,
        radius=radius,
        fill=_GLASS_FILL,
        outline=_GLASS_OUTLINE,
        width=2,
    )


def render_branded_ai_presentation(
    source_image_path: str | Path,
    *,
    headline: str,
    date_value: str,
    facts: Iterable[str],
    branding: str,
    output_path: str | Path,
) -> dict[str, object]:
    """Render accepted raw provider imagery full-bleed without changing its dedup identity."""
    source = Path(source_image_path)
    if not source.exists():
        raise RuntimeError(f"accepted provider image does not exist: {source}")

    with Image.open(source) as opened:
        canvas_rgba = ImageOps.fit(
            opened.convert("RGBA"),
            CANVAS_SIZE,
            method=Image.Resampling.LANCZOS,
            centering=(0.5, 0.5),
        )

    clean_facts = [str(item).strip() for item in facts if str(item).strip()][:3]
    display_facts = [_display_fact(item) for item in clean_facts]

    overlay = Image.new("RGBA", CANVAS_SIZE, (0, 0, 0, 0))
    _glass_panel(overlay, _TITLE_PANEL, radius=32)
    _glass_panel(overlay, _DATE_PANEL, radius=30)
    if display_facts:
        _glass_panel(overlay, _FACT_PANEL, radius=34)
    canvas = Image.alpha_composite(canvas_rgba, overlay).convert("RGB")
    draw = ImageDraw.Draw(canvas)

    title = str(headline).strip()
    title_font, title_font_size, title_origin, title_bbox = _fit_single_line(
        draw,
        title,
        _TITLE_SAFE,
        maximum_size=49,
        minimum_size=31,
    )
    draw.text(
        title_origin,
        title,
        font=title_font,
        fill=_TEXT_DARK,
        stroke_width=1,
        stroke_fill=_TEXT_STROKE,
    )

    brand_font = _font(22)
    brand_text = str(branding).strip()
    brand_origin = (72, 166)
    draw.text(brand_origin, brand_text, font=brand_font, fill=_TEXT_DARK)
    brand_bbox = draw.textbbox(brand_origin, brand_text, font=brand_font)
    if brand_bbox[2] > _TITLE_PANEL[2] - 18 or brand_bbox[3] > _TITLE_PANEL[3] - 14:
        raise RuntimeError("Cyprus AI presentation branding escapes title panel")

    date_text = _date_label(date_value)
    date_font = _font(30, bold=True)
    date_box = draw.textbbox((0, 0), date_text, font=date_font, stroke_width=1)
    date_x = _DATE_PANEL[0] + (_DATE_PANEL[2] - _DATE_PANEL[0] - (date_box[2] - date_box[0])) // 2
    date_y = _DATE_PANEL[1] + (_DATE_PANEL[3] - _DATE_PANEL[1] - (date_box[3] - date_box[1])) // 2
    date_origin = (date_x - date_box[0], date_y - date_box[1])
    draw.text(
        date_origin,
        date_text,
        font=date_font,
        fill=_TEXT_DARK,
        stroke_width=1,
        stroke_fill=_TEXT_STROKE,
    )
    date_bbox = draw.textbbox(date_origin, date_text, font=date_font, stroke_width=1)

    fact_layout: list[dict[str, object]] = []
    fact_font_size = 0
    if display_facts:
        fact_font, fact_font_size, wrapped_facts, line_gap, fact_gap, total_height = _fit_fact_layout(
            draw,
            display_facts,
            _FACT_SAFE,
        )
        y = _FACT_SAFE[1] + max(0, (_FACT_SAFE[3] - _FACT_SAFE[1] - total_height) // 2)
        for fact_index, (source_fact, display_fact, lines) in enumerate(
            zip(clean_facts, display_facts, wrapped_facts)
        ):
            origins: list[list[int]] = []
            bboxes: list[list[int]] = []
            for line_index, line in enumerate(lines):
                measured = draw.textbbox((0, 0), line, font=fact_font, stroke_width=1)
                origin = (_FACT_SAFE[0] - measured[0], y - measured[1])
                draw.text(
                    origin,
                    line,
                    font=fact_font,
                    fill=_TEXT_DARK,
                    stroke_width=1,
                    stroke_fill=_TEXT_STROKE,
                )
                bbox = draw.textbbox(origin, line, font=fact_font, stroke_width=1)
                if bbox[0] < _FACT_SAFE[0] or bbox[2] > _FACT_SAFE[2] or bbox[3] > _FACT_SAFE[3]:
                    raise RuntimeError("Cyprus AI presentation fact escapes safe-zone")
                origins.append([origin[0], origin[1]])
                bboxes.append(list(bbox))
                y = bbox[3]
                if line_index + 1 < len(lines):
                    y += line_gap
            fact_layout.append(
                {
                    "source_fact": source_fact,
                    "display_fact": display_fact,
                    "lines": lines,
                    "origins": origins,
                    "bboxes": bboxes,
                }
            )
            if fact_index + 1 < len(wrapped_facts):
                y += fact_gap

    output = Path(output_path).with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    source_sha256 = _sha256(source)
    info = PngImagePlugin.PngInfo()
    info.add_text("presentation_version", PRESENTATION_VERSION)
    info.add_text("layout_mode", "full_bleed_glass")
    info.add_text("source_sha256", source_sha256)
    info.add_text("headline", str(headline))
    info.add_text("date", str(date_value))
    canvas.save(output, format="PNG", pnginfo=info, compress_level=6)
    published_sha256 = _sha256(output)
    return {
        "path": str(output),
        "bytes": output.stat().st_size,
        "width": CANVAS_SIZE[0],
        "height": CANVAS_SIZE[1],
        "presentation_version": PRESENTATION_VERSION,
        "layout_mode": "full_bleed_glass",
        "source_sha256": source_sha256,
        "published_sha256": published_sha256,
        "headline": str(headline),
        "facts": clean_facts,
        "title_panel_bbox": list(_TITLE_PANEL),
        "date_panel_bbox": list(_DATE_PANEL),
        "facts_panel_bbox": list(_FACT_PANEL) if display_facts else None,
        "title_layout": {
            "origin": list(title_origin),
            "bbox": list(title_bbox),
            "font_size": title_font_size,
        },
        "date_layout": {
            "origin": list(date_origin),
            "bbox": list(date_bbox),
        },
        "fact_font_size": fact_font_size,
        "fact_layout": fact_layout,
    }


__all__ = ["CANVAS_SIZE", "PRESENTATION_VERSION", "render_branded_ai_presentation"]
