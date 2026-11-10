#!/usr/bin/env python3
"""Optional Cyprus weekly architectural illustration after the strict R10 photo gate.

This is editorial landmark art, NOT an image of forecast weather. The existing
weather/marine warning text is authoritative and sent before the image.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping
import hashlib
import json
import re

SIZE = (1080, 1350)
ASSET_ROOT = Path(__file__).resolve().parent / "assets" / "weekly" / "cy" / "architecture"
MANIFEST = ASSET_ROOT / "manifest.json"
RENDERER_VERSION = "cy_weekly_landmark_r12_candidate"


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size)


def _section(text: str, heading: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    values = []
    for i, line in enumerate(lines):
        if line == heading and i + 1 < len(lines):
            for following in lines[i+1:]:
                if following:
                    values.append(following)
                    break
    return values[0] if len(values) == 1 else ""


def _facts(text: str) -> tuple[str, str]:
    # Only copy a number when the weekly source explicitly presents that fact.
    weather = _section(text, "🌦 Погода")
    sea = _section(text, "🌊 Море")
    temp = re.search(r"(?<!\d)(-?\d{1,2})\s*[–—−-]\s*(-?\d{1,2})\s*°\s*[CС]", weather)
    sst = re.search(r"Средняя вода\s+(\d{1,2})(?:\s*[–—−-]\s*(\d{1,2}))?\s*°\s*[CС]", sea)
    # An incomplete 7-day forecast does not justify a full-week temperature range.
    partial = re.search(r"не подтвержд|пока не собран|неполный|недостаточно данных", weather, re.I)
    temp_label = f"{temp.group(1)}–{temp.group(2)}°C" if temp and not partial else "УТОЧНЯЕТСЯ"
    sea_label = (f"{sst.group(1)}–{sst.group(2)}°C" if sst.group(2) else f"{sst.group(1)}°C") if sst else "УТОЧНЯЕТСЯ"
    return temp_label, sea_label


def render_landmark_cover(text: str, *, start: date, output_path: str | Path,
                          trusted_weather: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """No network, provider calls, forecast inference, or safety-policy changes."""
    from PIL import Image, ImageDraw, PngImagePlugin

    if not isinstance(text, str) or not text.startswith("🗓 Вайб недели:"):
        raise ValueError("R12: missing weekly source text")
    # Independent fail-closed safety preflight: the presence of an approved
    # artwork is NEVER evidence of harmless weather. Reuse the existing R10
    # complete-island source and WMO guards, not an invented R12 threshold.
    from weekly_photo_cover_cy import _source_guard, _trusted_variant
    weather, _, _, main = _source_guard(text)
    sky = _trusted_variant(main, weather, trusted_weather, start)
    if sky not in ("sunny", "neutral"):
        raise ValueError("R12: sunny landmark art incompatible with verified sky")
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    if manifest.get("schema") != "cy_weekly_landmark_r12_v1" or manifest.get("size") != [1080, 1350]:
        raise ValueError("R12: invalid asset manifest")
    available = [x for x in manifest.get("assets", []) if x.get("approved") is True]
    if not available:
        raise ValueError("R12: no independently approved landmark assets")
    item = available[start.isocalendar().week % len(available)]
    asset = ASSET_ROOT / str(item.get("file", ""))
    if asset.parent.resolve() != ASSET_ROOT.resolve() or not asset.is_file():
        raise ValueError("R12: bad asset path")
    raw = asset.read_bytes()
    if (not 0 < len(raw) < 8_000_000 or hashlib.sha256(raw).hexdigest() != item.get("sha256")):
        raise ValueError("R12: asset SHA mismatch")
    with Image.open(asset) as photo:
        photo.load()
        if photo.size != SIZE or photo.format != "JPEG":
            raise ValueError("R12: approved photograph geometry mismatch")
        image = photo.convert("RGBA")

    # Decorative photography never claims to depict the week's sky; there is
    # NO positive water/weather icon and no inference of warnings being absent.
    overlay = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    layer = ImageDraw.Draw(overlay)
    layer.rounded_rectangle((47, 145, 1033, 357), radius=30,
                            fill=(14, 34, 53, 197), outline=(236, 243, 244, 172), width=2)
    layer.rounded_rectangle((44, 929, 1036, 1211), radius=29,
                            fill=(23, 37, 48, 199), outline=(236, 243, 244, 176), width=2)
    layer.rounded_rectangle((46, 838, 690, 912), radius=17,
                            fill=(12, 29, 43, 199))
    image = Image.alpha_composite(image, overlay).convert("RGB")
    d = ImageDraw.Draw(image)

    def write(x: int, y: int, value: str, font_size: int, *, bold: bool = False,
              color: str = "#FFFFFF", max_right: int = 1005) -> tuple[int, int, int, int]:
        font = _font(font_size, bold)
        box = d.textbbox((x, y), value, font=font)
        if not (55 <= box[0] and box[2] <= max_right and 135 <= box[1] <= box[3] <= 1215):
            raise ValueError("R12: text outside Telegram safe zone")
        d.text((x, y), value, font=font, fill=color)
        return box

    write(81, 165, "VAYBOMETER · КИПР", 23, bold=True, color="#C8E6E7")
    write(81, 208, "ВАЙБ НЕДЕЛИ", 61, bold=True)
    end = start + timedelta(days=6)
    write(82, 291, f"{start:%d.%m} — {end:%d.%m.%Y}", 30, color="#FFF0C9")

    city = str(item.get("city_ru", ""))
    if not city or len(city) > 40:
        raise ValueError("R12: invalid landmark label")
    write(75, 862, f"{city.upper()} · АРХИТЕКТУРНАЯ ИЛЛЮСТРАЦИЯ", 19,
          color="#FDE4B9")

    temp, sea = _facts(text)
    for label, value, x, width in (
        ("ТЕМПЕРАТУРА", temp, 77, 282),
        ("СРЕДНЯЯ ВОДА", sea, 413, 286),
        ("БЕЗОПАСНОСТЬ", "СМ. ПОСТ", 744, 274),
    ):
        write(x, 965, label, 17, bold=True, color="#C8E6E7", max_right=x+width)
        size = 27 if value == "УТОЧНЯЕТСЯ" else 33
        if value == "УТОЧНЯЕТСЯ": size = 23
        write(x, 1027, value, size, bold=True, max_right=x+width)
    write(80, 1149, "ПРЕДУПРЕЖДЕНИЯ И УСЛОВИЯ НА СПОТАХ — В ТЕКСТЕ ПОСТА", 16,
          color="#FFF0C9")

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    meta = {
        "renderer_version": RENDERER_VERSION, "region": "cyprus",
        "landmark": str(item.get("id")), "location": city, "week_start": start.isoformat(),
        "source_text_sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "asset_sha256": hashlib.sha256(raw).hexdigest(),
        "temperature": temp, "marine_observation": sea,
        "photo_is_illustration": True, "visual_weather_assertion": "none",
        "r12_safety_preflight": "r10_full_source_and_verified_wmo",
        "verified_sky_class": sky,
        "warning_text_changed": False, "safety_footer": "details in authoritative Telegram text",
    }
    pnginfo = PngImagePlugin.PngInfo()
    for k, v in meta.items():
        pnginfo.add_text(k, str(v))
    image.save(out, format="PNG", pnginfo=pnginfo, optimize=True)
    return dict(meta, path=str(out), width=1080, height=1350)
