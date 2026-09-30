from __future__ import annotations

from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Mapping

from PIL import Image, ImageDraw, ImageFont, ImageStat, PngImagePlugin

CATALOG_VERSION = "cy_curated_fallback_v1"
ATLAS_PATH = Path(__file__).resolve().parent / "assets" / "fallback" / "cy_fallback_atlas.jpg"
ATLAS_CELL_SIZE = (240, 300)
OUTPUT_SIZE = (1080, 1350)
BRANDING = "VAYBOMETER · CYPRUS"

_ASSET_ORDER = (
    "cy_clear_01", "cy_clear_02", "cy_clear_03", "cy_hot_01",
    "cy_sunset_01", "cy_windy_sea_01", "cy_rain_01", "cy_thunderstorm_01",
    "cy_dust_01", "cy_overcast_01", "cy_dust_02", "cy_partly_cloudy_01",
    "cy_overcast_02", "cy_sunset_02", "cy_fog_01", "cy_dust_extreme_01",
)
_SCENARIO_POOLS = {
    "clear": ("cy_clear_01", "cy_clear_02", "cy_clear_03"),
    "hot": ("cy_hot_01", "cy_clear_03"),
    "partly_cloudy": ("cy_partly_cloudy_01",),
    "overcast": ("cy_overcast_01", "cy_overcast_02"),
    "rain": ("cy_rain_01",),
    "thunderstorm": ("cy_thunderstorm_01",),
    "strong_wind": ("cy_windy_sea_01",),
    "fog": ("cy_fog_01",),
    "dust": ("cy_dust_01", "cy_dust_02"),
    "dust_extreme": ("cy_dust_extreme_01",),
    "sunset": ("cy_sunset_01", "cy_sunset_02"),
}

def _asset_box(asset_id: str) -> tuple[int, int, int, int]:
    index = _ASSET_ORDER.index(asset_id)
    col, row = index % 4, index // 4
    w, h = ATLAS_CELL_SIZE
    return col * w, row * h, (col + 1) * w, (row + 1) * h

def scenario_for_context(ctx: Any) -> str:
    visibility = str(getattr(ctx, "visibility_condition", "") or "").lower()
    dust_class = str(getattr(ctx, "dust_vs_fog_classification", "") or "").lower()
    if bool(getattr(ctx, "explicit_storm", False)) or bool(getattr(ctx, "inland_thunder_risk", False)):
        return "thunderstorm"
    if bool(getattr(ctx, "actual_precipitation", False)):
        return "rain"
    if visibility in {"dense_fog", "fog", "mist"} or dust_class in {"fog", "mist", "dense_fog"}:
        return "fog"
    dust = (
        visibility == "dust_haze"
        or bool(getattr(ctx, "dust_hint", None))
        or str(getattr(ctx, "primary_weather", "")) == "dusty"
        or dust_class == "dust_haze"
    )
    if dust:
        values = [getattr(ctx, "current_visibility_m", None), getattr(ctx, "morning_min_visibility_m", None)]
        visible = [float(value) for value in values if isinstance(value, (int, float))]
        if visible and min(visible) <= 4000:
            return "dust_extreme"
        return "dust"
    if (
        bool(getattr(ctx, "severe_wind", False))
        or bool(getattr(ctx, "strong_wind", False))
        or str(getattr(ctx, "sea_state_hint", "")) == "rough"
    ):
        return "strong_wind"
    primary = str(getattr(ctx, "primary_weather", "") or "").lower()
    temp_max = getattr(ctx, "temp_max", None)
    if primary == "hot" or (isinstance(temp_max, (int, float)) and float(temp_max) >= 33.0):
        return "hot"
    if primary in {"cloudy", "overcast"}:
        return "overcast"
    if primary in {"mixed", "partly_cloudy"}:
        return "partly_cloudy"
    period = str(getattr(ctx, "visual_forecast_period", "") or "").lower()
    if any(token in period for token in ("sunset", "twilight")):
        return "sunset"
    return "clear"

def select_asset(ctx: Any, *, target_date: str, post_type: str) -> tuple[str, str, tuple[str, ...]]:
    scenario = scenario_for_context(ctx)
    pool = _SCENARIO_POOLS[scenario]
    seed = hashlib.sha256(f"{target_date}|{post_type}|{scenario}|{CATALOG_VERSION}".encode("utf-8")).digest()
    asset_id = pool[int.from_bytes(seed[:4], "big") % len(pool)]
    return scenario, asset_id, pool

def _font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf",
    )
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    return ImageFont.truetype("DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf", size=size)

def _text_color(image: Image.Image, box: tuple[int, int, int, int]) -> tuple[int, int, int]:
    sample = image.crop(box).resize((1, 1))
    r, g, b = ImageStat.Stat(sample).mean[:3]
    luminance = 0.2126 * r + 0.7152 * g + 0.0722 * b
    return (9, 31, 46) if luminance >= 145 else (250, 252, 250)

def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] <= max_width:
            current = candidate
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines

def render_curated_cover(
    ctx: Any,
    facts: Mapping[str, str],
    *,
    target_date: str,
    post_type: str,
    output_path: str | Path,
    minimum_bytes: int,
    extra_metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    scenario, asset_id, pool = select_asset(ctx, target_date=target_date, post_type=post_type)
    if not ATLAS_PATH.exists():
        raise RuntimeError(f"Cyprus curated fallback atlas is missing: {ATLAS_PATH}")
    with Image.open(ATLAS_PATH) as atlas:
        image = atlas.convert("RGB").crop(_asset_box(asset_id)).resize(OUTPUT_SIZE, Image.Resampling.LANCZOS)

    draw = ImageDraw.Draw(image)
    title_box = (42, 84, 710, 230)
    date_box = (865, 84, 1034, 240)
    facts_box = (38, 828, 762, 1232)
    title_color = _text_color(image, title_box)
    facts_color = _text_color(image, facts_box)
    date_color = _text_color(image, date_box)
    title_font = _font(49, bold=True)
    brand_font = _font(22, bold=False)
    date_font = _font(30, bold=True)
    fact_font = _font(40, bold=True)

    headline = str(facts.get("headline") or "КИПР СЕГОДНЯ")
    stroke = (255, 255, 255) if title_color[0] < 100 else (0, 0, 0)
    draw.text((70, 102), headline, font=title_font, fill=title_color, stroke_width=1, stroke_fill=stroke)
    draw.text((72, 166), BRANDING, font=brand_font, fill=title_color)
    date_label = date.fromisoformat(target_date).strftime("%d.%m")
    db = draw.textbbox((0, 0), date_label, font=date_font)
    dx = date_box[0] + (date_box[2] - date_box[0] - (db[2] - db[0])) // 2
    dy = date_box[1] + (date_box[3] - date_box[1] - (db[3] - db[1])) // 2
    draw.text((dx, dy), date_label, font=date_font, fill=date_color)

    values = [str(facts.get(key) or "") for key in ("primary_fact", "secondary_fact", "tertiary_fact")]
    values = [re.sub(r"^[^\wА-ЯЁ+]+\s*", "", value, flags=re.I) for value in values if value]
    y = 865
    layout: list[dict[str, Any]] = []
    for value in values[:3]:
        lines = _wrap(draw, value, fact_font, 645)
        font = fact_font
        if len(lines) > 2:
            font = _font(34, bold=True)
            lines = _wrap(draw, value, font, 645)
        origins = []
        for line in lines[:2]:
            stroke = (255, 255, 255) if facts_color[0] < 100 else (0, 0, 0)
            draw.text((78, y), line, font=font, fill=facts_color, stroke_width=1, stroke_fill=stroke)
            origins.append([78, y])
            y += 50 if font is fact_font else 44
        layout.append({"source_fact": value, "lines": lines[:2], "origins": origins})
        y += 22

    rendered_lines = [BRANDING, headline, *values]
    rendered_text = "\n".join(rendered_lines[:5])
    cache_payload = {
        "catalog_version": CATALOG_VERSION,
        "asset_id": asset_id,
        "scenario": scenario,
        "target_date": target_date,
        "post_type": post_type,
        "headline": headline,
        "facts": values,
    }
    cache_json = json.dumps(cache_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cache_key = f"{CATALOG_VERSION}:{hashlib.sha256(cache_json.encode('utf-8')).hexdigest()}"
    output = Path(output_path).with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata = {
        "backend": "local_informative_cover",
        "renderer_version": CATALOG_VERSION,
        "generator_version": CATALOG_VERSION,
        "catalog_version": CATALOG_VERSION,
        "branding": BRANDING,
        "branding_bbox": json.dumps(list(title_box), separators=(",", ":")),
        "curated_scenario": scenario,
        "curated_asset_id": asset_id,
        "curated_pool": json.dumps(list(pool), ensure_ascii=False, separators=(",", ":")),
        "cover_variant": asset_id,
        "target_date": target_date,
        "post_type": post_type,
        "headline": headline,
        "primary_fact": str(facts.get("primary_fact") or ""),
        "secondary_fact": str(facts.get("secondary_fact") or ""),
        "tertiary_fact": str(facts.get("tertiary_fact") or ""),
        "fact_layout": json.dumps(layout, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        "rendered_text": rendered_text,
        "palette": scenario,
        "cache_key": cache_key,
    }
    if extra_metadata:
        metadata.update({str(key): value for key, value in extra_metadata.items()})
    info = PngImagePlugin.PngInfo()
    for key, value in metadata.items():
        info.add_text(key, str(value))
    image.save(output, format="PNG", pnginfo=info, compress_level=6)
    if output.stat().st_size <= int(minimum_bytes):
        image.save(output, format="PNG", pnginfo=info, compress_level=0)
    if output.stat().st_size <= int(minimum_bytes):
        raise RuntimeError(f"curated Cyprus fallback cover is too small: {output.stat().st_size}")
    return {"path": str(output), "bytes": output.stat().st_size, "metadata": metadata}
