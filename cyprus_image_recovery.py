#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistent provider health and deterministic Cyprus weather-card fallback."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import random
import re
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFilter, ImageFont, PngImagePlugin

from visual_context_cy import parse_visual_context_cy


LOCAL_WEATHER_CARD_VERSION = "cy_local_atmospheric_visual_v2"
LOCAL_INFORMATIVE_COVER_VERSION = "cy_local_informative_cover_v3"
PROVIDER_HEALTH_SCHEMA_VERSION = 1
_PROVIDER_NAMES = ("pollinations", "stable_horde", "custom")
_INVALID_ERROR_CATEGORIES = {
    "invalid_base64",
    "invalid_image",
    "invalid_response",
    "no_generations",
    "censored",
}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _utc_iso(value: datetime | None = None) -> str:
    current = value or _utc_now()
    return current.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _safe_namespace(namespace: str) -> str:
    value = str(namespace or "test").strip().lower()
    return "prod" if value in {"prod", "production"} else "test"


def _safe_target_date(target_date: str) -> str:
    value = str(target_date or "").strip()
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError(f"invalid Cyprus provider-health target date: {target_date!r}")
    date.fromisoformat(value)
    return value


def provider_health_path(target_date: str, post_type: str, namespace: str) -> Path:
    safe_date = _safe_target_date(target_date)
    safe_type = str(post_type or "").strip().lower()
    if safe_type not in {"morning", "evening"}:
        raise ValueError(f"invalid Cyprus provider-health post type: {post_type!r}")
    root = Path(os.getenv("CY_IMAGE_PROVIDER_HEALTH_DIR", ".cache/cy_image_provider_health"))
    return root / _safe_namespace(namespace) / f"{safe_date}-{safe_type}.json"


def _provider_record() -> dict[str, Any]:
    return {
        "repeated_dhash": "",
        "repeated_phash": "",
        "duplicate_count": 0,
        "invalid_response_count": 0,
        "consecutive_failures": 0,
        "excluded_until_utc": "",
        "last_error_type": "",
        "last_attempt_utc": "",
        "run_id": "",
    }


def _fresh_health(target_date: str, post_type: str, namespace: str) -> dict[str, Any]:
    return {
        "schema_version": PROVIDER_HEALTH_SCHEMA_VERSION,
        "target_date": _safe_target_date(target_date),
        "post_type": str(post_type).strip().lower(),
        "namespace": _safe_namespace(namespace),
        "updated_at_utc": "",
        "providers": {name: _provider_record() for name in _PROVIDER_NAMES},
    }


def load_provider_health(target_date: str, post_type: str, namespace: str) -> dict[str, Any]:
    fresh = _fresh_health(target_date, post_type, namespace)
    path = provider_health_path(target_date, post_type, namespace)
    try:
        data = json.loads(path.read_text("utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return fresh
    if not isinstance(data, dict):
        return fresh
    if (
        data.get("target_date") != fresh["target_date"]
        or data.get("post_type") != fresh["post_type"]
        or data.get("namespace") != fresh["namespace"]
    ):
        return fresh
    providers = data.get("providers")
    if not isinstance(providers, dict):
        return fresh
    merged = fresh
    for name in _PROVIDER_NAMES:
        stored = providers.get(name)
        if isinstance(stored, dict):
            merged["providers"][name].update(
                {key: stored[key] for key in merged["providers"][name] if key in stored}
            )
    merged["updated_at_utc"] = str(data.get("updated_at_utc") or "")
    return merged


def write_provider_health(payload: dict[str, Any]) -> Path:
    path = provider_health_path(
        str(payload.get("target_date") or ""),
        str(payload.get("post_type") or ""),
        str(payload.get("namespace") or "test"),
    )
    payload["updated_at_utc"] = _utc_iso()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", "utf-8")
    tmp.replace(path)
    return path


def _parse_utc(value: object) -> datetime | None:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def provider_health_exclusions(payload: dict[str, Any], *, now: datetime | None = None) -> set[str]:
    current = (now or _utc_now()).astimezone(timezone.utc)
    excluded: set[str] = set()
    providers = payload.get("providers") if isinstance(payload, dict) else None
    if not isinstance(providers, dict):
        return excluded
    for name, record in providers.items():
        if not isinstance(record, dict):
            continue
        excluded_until = _parse_utc(record.get("excluded_until_utc"))
        if excluded_until is not None and excluded_until > current:
            excluded.add(str(name))
    return excluded


def record_provider_attempts(
    payload: dict[str, Any],
    attempts: Iterable[dict[str, Any]],
    *,
    run_id: str = "",
) -> None:
    providers = payload.setdefault("providers", {})
    now = _utc_iso()
    for attempt in attempts:
        name = str(attempt.get("backend") or "").strip().lower()
        if name == "horde":
            name = "stable_horde"
        if name not in _PROVIDER_NAMES:
            continue
        record = providers.setdefault(name, _provider_record())
        result = str(attempt.get("result") or "failed").strip().lower()
        error_category = str(attempt.get("error_category") or "").strip().lower()
        error_type = str(
            attempt.get("error_type")
            or error_category
            or ("" if result == "success" else "BackendReturnedNoImage")
        )
        record["last_attempt_utc"] = now
        record["run_id"] = str(run_id or "")
        record["last_error_type"] = error_type[:120]
        if result == "success":
            record["consecutive_failures"] = 0
            record["last_error_type"] = ""
        else:
            record["consecutive_failures"] = int(record.get("consecutive_failures") or 0) + 1
            if result == "invalid" or error_category in _INVALID_ERROR_CATEGORIES:
                record["invalid_response_count"] = int(record.get("invalid_response_count") or 0) + 1


def mark_provider_duplicate(
    payload: dict[str, Any],
    backend: str,
    *,
    dhash: str,
    phash: str,
    stuck: bool,
    run_id: str = "",
) -> None:
    name = "stable_horde" if str(backend).strip().lower() == "horde" else str(backend).strip().lower()
    if name not in _PROVIDER_NAMES:
        return
    providers = payload.setdefault("providers", {})
    record = providers.setdefault(name, _provider_record())
    record["duplicate_count"] = int(record.get("duplicate_count") or 0) + 1
    record["repeated_dhash"] = str(dhash or "")
    record["repeated_phash"] = str(phash or "")
    record["last_attempt_utc"] = _utc_iso()
    record["run_id"] = str(run_id or "")
    if stuck:
        target = date.fromisoformat(str(payload["target_date"])) + timedelta(days=2)
        record["excluded_until_utc"] = datetime.combine(target, datetime.min.time(), tzinfo=timezone.utc).isoformat().replace(
            "+00:00", "Z"
        )
        record["last_error_type"] = "ProviderRepeatedPerceptualOutput"


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    return tuple(round(a[index] * (1.0 - ratio) + b[index] * ratio) for index in range(3))


_LOCAL_VISUAL_VARIANTS = (
    "open_sea_dawn",
    "evening_sea_horizon",
    "windy_coastal_twilight",
    "hazy_hot_inland",
    "troodos_evening",
    "cloudy_promontory",
    "minimal_moonlit_coast",
)


def _has_near_full_moon(text: str) -> bool:
    return bool(
        re.search(r"полнолун|full\s+moon|(?:9[5-9]|100)\s*%", text or "", re.I)
    )


def _select_local_visual_variant(
    *,
    mode: str,
    weather: str,
    focus: str,
    severe_wind: bool,
    near_full_moon: bool,
    digest: str,
) -> str:
    if mode == "morning":
        if focus == "inland" or weather in {"hot", "dusty"}:
            candidates = ("hazy_hot_inland", "open_sea_dawn")
        elif weather in {"cloudy", "rain", "storm"}:
            candidates = ("cloudy_promontory", "open_sea_dawn")
        else:
            candidates = ("open_sea_dawn", "cloudy_promontory")
    elif near_full_moon:
        candidates = ("minimal_moonlit_coast", "evening_sea_horizon")
    elif severe_wind:
        candidates = ("windy_coastal_twilight", "evening_sea_horizon")
    elif focus == "inland":
        candidates = ("troodos_evening", "hazy_hot_inland")
    elif weather in {"cloudy", "rain", "storm"}:
        candidates = ("cloudy_promontory", "windy_coastal_twilight")
    else:
        candidates = ("evening_sea_horizon", "minimal_moonlit_coast")
    return candidates[int(digest[16:24], 16) % len(candidates)]


def _radial_glow(
    image: Image.Image,
    center: tuple[int, int],
    radius: int,
    color: tuple[int, int, int],
    opacity: int,
) -> Image.Image:
    glow = Image.new("RGBA", image.size, (0, 0, 0, 0))
    glow_draw = ImageDraw.Draw(glow, "RGBA")
    x, y = center
    glow_draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=(*color, opacity))
    glow = glow.filter(ImageFilter.GaussianBlur(radius=max(18, radius // 2)))
    return Image.alpha_composite(image.convert("RGBA"), glow)


def _cloud_layer(
    image: Image.Image,
    rng: random.Random,
    *,
    mode: str,
    weather: str,
    windy: bool,
) -> Image.Image:
    cloud = Image.new("RGBA", image.size, (0, 0, 0, 0))
    cloud_draw = ImageDraw.Draw(cloud, "RGBA")
    count = 12 if weather in {"cloudy", "rain", "storm"} else 6
    base_alpha = 70 if mode == "morning" else 78
    if weather in {"rain", "storm"}:
        base_alpha += 45
    for index in range(count):
        width = rng.randint(190, 470) + (100 if windy else 0)
        height = rng.randint(35, 105)
        x = rng.randint(-180, 1000)
        y = rng.randint(60, 520)
        shade = rng.randint(175, 228) if mode == "morning" else rng.randint(82, 150)
        alpha = min(180, base_alpha + rng.randint(-20, 25))
        cloud_draw.ellipse((x, y, x + width, y + height), fill=(shade, shade + 4, shade + 10, alpha))
        if index % 3 == 0:
            cloud_draw.ellipse(
                (x + width * 0.2, y - height * 0.35, x + width * 0.76, y + height * 0.7),
                fill=(shade, shade + 4, shade + 10, max(20, alpha - 20)),
            )
    cloud = cloud.filter(ImageFilter.GaussianBlur(radius=26 if windy else 34))
    return Image.alpha_composite(image.convert("RGBA"), cloud)


def _draw_sea(
    image: Image.Image,
    rng: random.Random,
    *,
    horizon_y: int,
    windy: bool,
    moon_x: int | None,
) -> Image.Image:
    water = Image.new("RGBA", image.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(water, "RGBA")
    line_count = 20 if windy else 15
    for index in range(line_count):
        depth = (index + 1) / line_count
        y = horizon_y + 12 + round(depth * (1080 - horizon_y - 22))
        amplitude = 1.5 + depth * (8 if windy else 4)
        frequency = 0.009 + rng.random() * 0.012
        phase = rng.random() * math.tau
        alpha = round(16 + depth * (58 if windy else 42))
        segment_count = 4 if windy else 3
        for _ in range(segment_count):
            start_x = rng.randint(-40, 960)
            length = rng.randint(90, 330)
            points = [
                (
                    x,
                    round(y + math.sin(x * frequency + phase) * amplitude + rng.uniform(-1.5, 1.5)),
                )
                for x in range(start_x, min(1120, start_x + length), 18)
            ]
            if len(points) > 1:
                draw.line(points, fill=(205, 231, 232, alpha), width=1 + (index % 6 == 0))
    if moon_x is not None:
        for index in range(15):
            y = horizon_y + 22 + index * 22
            spread = 16 + index * 6
            x_shift = rng.randint(-22, 22)
            segment_width = max(18, round(spread * rng.uniform(0.45, 1.05)))
            draw.line(
                (moon_x - segment_width + x_shift, y, moon_x + segment_width + x_shift, y),
                fill=(225, 230, 211, max(12, 68 - index * 3)),
                width=1 + (index % 4 == 0),
            )
    water = water.filter(ImageFilter.GaussianBlur(radius=0.7 if windy else 1.0))
    return Image.alpha_composite(image.convert("RGBA"), water)


def _draw_mountain_layers(
    image: Image.Image,
    rng: random.Random,
    *,
    horizon_y: int,
    mode: str,
) -> Image.Image:
    colors = (
        (111, 113, 102, 90),
        (63, 78, 76, 155),
        (32, 55, 57, 235),
    ) if mode == "evening" else (
        (154, 139, 113, 80),
        (104, 108, 91, 150),
        (57, 82, 70, 225),
    )
    mountains = Image.new("RGBA", image.size, (0, 0, 0, 0))
    mountain_draw = ImageDraw.Draw(mountains, "RGBA")
    for layer, color in enumerate(colors):
        base_y = horizon_y + 28 + layer * 105
        amplitude = 42 + layer * 24
        phase_a = rng.random() * math.tau
        phase_b = rng.random() * math.tau
        ridge: list[tuple[int, int]] = []
        for x in range(-30, 1111, 18):
            y = (
                base_y
                - math.sin(x * (0.0048 + layer * 0.0005) + phase_a) * amplitude
                - math.sin(x * 0.0105 + phase_b) * amplitude * 0.34
                + rng.uniform(-3.5, 3.5)
            )
            ridge.append((x, round(y)))
        mountain_draw.polygon([(-30, 1080), *ridge, (1110, 1080)], fill=color)
    mountains = mountains.filter(ImageFilter.GaussianBlur(radius=1.2))
    return Image.alpha_composite(image.convert("RGBA"), mountains)


def _render_local_atmospheric_visual_v2(
    final_text: str,
    *,
    target_date: str,
    post_type: str,
    output_path: str | Path,
    minimum_bytes: int,
) -> dict[str, Any]:
    """Render a deterministic full-bleed atmospheric Cyprus visual without a network call."""

    safe_date = _safe_target_date(target_date)
    mode = str(post_type or "").strip().lower()
    if mode not in {"morning", "evening"}:
        raise ValueError(f"invalid local weather-card post type: {post_type!r}")
    ctx = parse_visual_context_cy(final_text, post_type=mode)
    focus = "inland" if ctx.inland_heat_focus and not ctx.coastal_focus else "coastal"
    wind = ctx.gust_max if ctx.gust_max is not None else ctx.wind_max
    near_full_moon = mode == "evening" and _has_near_full_moon(final_text)
    seed_text = "|".join(
        [
            safe_date,
            mode,
            str(ctx.weather_main),
            str(wind or ""),
            str(ctx.visibility_haze),
            str(ctx.actual_precipitation),
            focus,
            str(near_full_moon),
            LOCAL_WEATHER_CARD_VERSION,
        ]
    )
    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))
    weather = str(ctx.weather_main)
    variant = _select_local_visual_variant(
        mode=mode,
        weather=weather,
        focus=focus,
        severe_wind=bool(ctx.severe_wind or (wind is not None and wind >= 12)),
        near_full_moon=near_full_moon,
        digest=digest,
    )

    palette_map = {
        "open_sea_dawn": ("aegean_dawn", "soft_daylight", (55, 102, 146), (190, 206, 195), (23, 91, 120)),
        "evening_sea_horizon": ("violet_afterglow", "blue_hour", (24, 31, 72), (176, 112, 104), (13, 47, 73)),
        "windy_coastal_twilight": ("wind_slate_twilight", "twilight", (37, 49, 78), (133, 125, 137), (15, 58, 78)),
        "hazy_hot_inland": ("copper_haze", "hazy_daylight" if mode == "morning" else "late_twilight", (122, 116, 108), (222, 176, 126), (85, 72, 57)),
        "troodos_evening": ("troodos_indigo", "blue_hour", (29, 35, 70), (132, 104, 115), (27, 53, 58)),
        "cloudy_promontory": ("clouded_cyprus", "soft_daylight" if mode == "morning" else "twilight", (61, 77, 94), (151, 158, 157), (20, 66, 82)),
        "minimal_moonlit_coast": ("moonlit_ink", "moonlit_evening", (16, 24, 57), (84, 92, 119), (9, 39, 62)),
    }
    palette_name, time_of_day, top, horizon, lower = palette_map[variant]
    if weather in {"rain", "storm"}:
        top = _mix(top, (34, 47, 61), 0.42)
        horizon = _mix(horizon, (91, 109, 119), 0.38)

    size = 1080
    image = Image.new("RGBA", (size, size), (*top, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    inland_variant = variant in {"hazy_hot_inland", "troodos_evening"}
    horizon_y = 625 if inland_variant else 590
    for y in range(size):
        if y <= horizon_y:
            ratio = y / max(1, horizon_y)
            color = _mix(top, horizon, ratio)
        else:
            ratio = (y - horizon_y) / max(1, size - horizon_y)
            color = _mix(lower, _mix(lower, (4, 21, 34), 0.62), ratio)
        draw.line((0, y, size, y), fill=(*color, 255))

    if mode == "morning":
        glow_x = 230 + int(digest[24:28], 16) % 520
        image = _radial_glow(image, (glow_x, 250), 190, (255, 218, 159), 90)
    else:
        glow_x = 120 + int(digest[24:28], 16) % 360
        image = _radial_glow(image, (glow_x, horizon_y - 25), 190, (235, 151, 117), 62)
    image = _cloud_layer(
        image,
        rng,
        mode=mode,
        weather=weather,
        windy=variant == "windy_coastal_twilight" or bool(ctx.severe_wind),
    )
    draw = ImageDraw.Draw(image, "RGBA")

    moon_x: int | None = None
    if mode == "evening" and (near_full_moon or variant == "minimal_moonlit_coast"):
        moon_x = 760 + int(digest[28:32], 16) % 150
        moon_y = 185 + int(digest[32:36], 16) % 85
        moon_radius = 34 + int(digest[36:38], 16) % 7
        image = _radial_glow(image, (moon_x, moon_y), 92, (225, 229, 213), 82)
        moon = Image.new("RGBA", image.size, (0, 0, 0, 0))
        moon_draw = ImageDraw.Draw(moon, "RGBA")
        moon_draw.ellipse(
            (moon_x - moon_radius, moon_y - moon_radius, moon_x + moon_radius, moon_y + moon_radius),
            fill=(230, 230, 210, 238),
        )
        for _ in range(6):
            crater_radius = rng.randint(2, 7)
            crater_x = moon_x + rng.randint(-moon_radius // 2, moon_radius // 2)
            crater_y = moon_y + rng.randint(-moon_radius // 2, moon_radius // 2)
            moon_draw.ellipse(
                (crater_x - crater_radius, crater_y - crater_radius, crater_x + crater_radius, crater_y + crater_radius),
                fill=(172, 178, 169, rng.randint(14, 32)),
            )
        image = Image.alpha_composite(image.convert("RGBA"), moon)
        draw = ImageDraw.Draw(image, "RGBA")

    if inland_variant:
        image = _draw_mountain_layers(image, rng, horizon_y=horizon_y, mode=mode)
    else:
        image = _draw_sea(
            image,
            rng,
            horizon_y=horizon_y,
            windy=variant == "windy_coastal_twilight" or bool(ctx.severe_wind),
            moon_x=moon_x,
        )
        land = Image.new("RGBA", image.size, (0, 0, 0, 0))
        land_draw = ImageDraw.Draw(land, "RGBA")
        if variant == "cloudy_promontory":
            land_draw.polygon(
                ((690, 695), (790, 625), (900, 648), (1000, 590), (1080, 610), (1080, 1080), (790, 1080)),
                fill=(19, 48, 52, 232),
            )
        elif variant in {"evening_sea_horizon", "minimal_moonlit_coast"}:
            land_draw.polygon(
                ((0, 915), (180, 875), (350, 902), (545, 840), (720, 888), (890, 825), (1080, 855), (1080, 1080), (0, 1080)),
                fill=(9, 31, 38, 225),
            )
        else:
            land_draw.polygon(
                ((0, 930), (170, 870), (340, 900), (545, 855), (720, 914), (900, 860), (1080, 900), (1080, 1080), (0, 1080)),
                fill=(31, 61, 52, 190 if mode == "morning" else 225),
            )
        land = land.filter(ImageFilter.GaussianBlur(radius=0.8))
        image = Image.alpha_composite(image.convert("RGBA"), land)
        draw = ImageDraw.Draw(image, "RGBA")

    if variant == "windy_coastal_twilight":
        for index in range(34):
            x = 40 + index * 34 + rng.randint(-8, 8)
            height = rng.randint(65, 160)
            draw.line((x, 1080, x + rng.randint(18, 45), 1080 - height), fill=(14, 39, 36, 220), width=3)
    if weather in {"rain", "storm"} or ctx.actual_precipitation:
        for _ in range(90):
            x = rng.randint(-50, 1120)
            y = rng.randint(250, 900)
            length = rng.randint(16, 44)
            draw.line((x, y, x - 9, y + length), fill=(183, 207, 216, rng.randint(24, 70)), width=1)

    texture = Image.new("RGBA", image.size, (0, 0, 0, 0))
    texture_draw = ImageDraw.Draw(texture)
    for _ in range(15000):
        x = rng.randrange(size)
        y = rng.randrange(size)
        alpha = rng.randrange(2, 13)
        shade = 255 if rng.random() > 0.45 else 0
        texture_draw.point((x, y), fill=(shade, shade, shade, alpha))
    image = Image.alpha_composite(image.convert("RGBA"), texture)

    output = Path(output_path).with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    png_info = PngImagePlugin.PngInfo()
    metadata = {
        "backend": "local_weather_card",
        "generator_version": LOCAL_WEATHER_CARD_VERSION,
        "renderer_version": LOCAL_WEATHER_CARD_VERSION,
        "target_date": safe_date,
        "post_type": mode,
        "weather_scenario": weather,
        "wind": "" if wind is None else f"{wind:g}",
        "cloud_haze": "1" if (ctx.visibility_haze or ctx.dust_hint) else "0",
        "focus": focus,
        "seed_sha256": digest,
        "local_visual_variant": variant,
        "local_palette": palette_name,
        "local_time_of_day": time_of_day,
        "local_weather_scenario": weather,
        "rendered_text": "",
    }
    for key, value in metadata.items():
        png_info.add_text(key, str(value))
    image.convert("RGB").save(output, format="PNG", pnginfo=png_info, compress_level=6)
    if output.stat().st_size <= int(minimum_bytes):
        image.convert("RGB").save(output, format="PNG", pnginfo=png_info, compress_level=0)
    with Image.open(output) as verify_image:
        if verify_image.size != (size, size) or verify_image.format != "PNG":
            raise RuntimeError("local weather card has invalid dimensions or format")
        verify_image.verify()
    if output.stat().st_size <= int(minimum_bytes):
        raise RuntimeError(
            f"local weather card is too small: {output.stat().st_size} bytes; must exceed {minimum_bytes}"
        )
    return {"path": str(output), "bytes": output.stat().st_size, "metadata": metadata}


def _cover_font(size: int, *, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = (
        "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
    )
    paths = [
        *(Path("C:/Windows/Fonts") / name for name in names),
        *(Path("/usr/share/fonts/truetype/dejavu") / name for name in names),
        *(Path("/usr/local/share/fonts") / name for name in names),
    ]
    for candidate in paths:
        try:
            return ImageFont.truetype(str(candidate), size=size)
        except OSError:
            continue
    for name in names:
        try:
            return ImageFont.truetype(name, size=size)
        except OSError:
            continue
    raise RuntimeError("no Cyrillic TrueType font available for Cyprus informative cover")


_COVER_FACT_FONT_MAX = 52
_COVER_FACT_FONT_MIN = 34
_COVER_FACT_TEXT_LEFT = 128
_COVER_FACT_TEXT_RIGHT = 952
_COVER_FACT_CARD_LEFT = 92
_COVER_FACT_CARD_RIGHT = 988
_COVER_FACT_CARD_TOP = 431
_COVER_FACT_CARD_BOTTOM_LIMIT = 988
_COVER_FACT_PADDING_Y = 24
_COVER_FACT_LINE_GAP = 10
_COVER_FACT_CARD_GAP = 24


def _cover_textbbox(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.FreeTypeFont,
) -> tuple[int, int, int, int]:
    left, top, right, bottom = draw.textbbox((0, 0), value, font=font)
    return int(left), int(top), int(right), int(bottom)


def _wrap_cover_fact_words(
    draw: ImageDraw.ImageDraw,
    value: str,
    font: ImageFont.FreeTypeFont,
    *,
    max_width: int,
    max_lines: int = 2,
) -> list[str] | None:
    words = value.split()
    if not words:
        return []
    lines: list[str] = []
    current = ""
    for word in words:
        word_box = _cover_textbbox(draw, word, font)
        if word_box[2] - word_box[0] > max_width:
            return None
        candidate = f"{current} {word}".strip()
        box = _cover_textbbox(draw, candidate, font)
        if box[2] - box[0] <= max_width:
            current = candidate
            continue
        if not current or len(lines) >= max_lines - 1:
            return None
        lines.append(current)
        current = word
    if current:
        lines.append(current)
    return lines if len(lines) <= max_lines else None


def _fit_cover_fact_lines(
    draw: ImageDraw.ImageDraw,
    value: str,
) -> tuple[ImageFont.FreeTypeFont, int, list[str]]:
    """Fit a complete fact without clipping or splitting any word."""
    max_width = _COVER_FACT_TEXT_RIGHT - _COVER_FACT_TEXT_LEFT
    for size in range(_COVER_FACT_FONT_MAX, _COVER_FACT_FONT_MIN - 1, -1):
        font = _cover_font(size, bold=True)
        box = _cover_textbbox(draw, value, font)
        if box[2] - box[0] <= max_width:
            return font, size, [value]
    for size in range(_COVER_FACT_FONT_MAX, _COVER_FACT_FONT_MIN - 1, -1):
        font = _cover_font(size, bold=True)
        lines = _wrap_cover_fact_words(draw, value, font, max_width=max_width, max_lines=2)
        if lines:
            return font, size, lines
    raise RuntimeError(f"Cyprus informative-cover fact cannot fit safely: {value!r}")


def _draw_cover_fact_cards(
    draw: ImageDraw.ImageDraw,
    values: list[str],
    *,
    accent: tuple[int, int, int],
) -> list[dict[str, Any]]:
    """Draw up to three bounded fact cards and return pixel-layout diagnostics."""
    layouts: list[dict[str, Any]] = []
    card_top = _COVER_FACT_CARD_TOP
    for source_value in values[:3]:
        display = re.sub(r"^[^\wА-ЯЁ]+\s*", "", source_value, flags=re.I)
        font, font_size, lines = _fit_cover_fact_lines(draw, display)
        raw_boxes = [_cover_textbbox(draw, line, font) for line in lines]
        line_heights = [box[3] - box[1] for box in raw_boxes]
        content_height = sum(line_heights) + _COVER_FACT_LINE_GAP * max(0, len(lines) - 1)
        card_bottom = card_top + _COVER_FACT_PADDING_Y * 2 + content_height
        if card_bottom > _COVER_FACT_CARD_BOTTOM_LIMIT:
            raise RuntimeError("Cyprus informative-cover fact cards exceed the safe vertical area")

        card_bbox = (_COVER_FACT_CARD_LEFT, card_top, _COVER_FACT_CARD_RIGHT, card_bottom)
        draw.rounded_rectangle(card_bbox, radius=28, fill=(255, 255, 255, 150))
        line_top = card_top + _COVER_FACT_PADDING_Y
        line_layouts: list[dict[str, Any]] = []
        for line, raw_box in zip(lines, raw_boxes):
            origin = (
                _COVER_FACT_TEXT_LEFT - raw_box[0],
                line_top - raw_box[1],
            )
            pixel_bbox = tuple(int(value) for value in draw.textbbox(origin, line, font=font))
            if not (
                pixel_bbox[0] >= _COVER_FACT_TEXT_LEFT
                and pixel_bbox[2] <= _COVER_FACT_TEXT_RIGHT
                and pixel_bbox[1] >= card_top
                and pixel_bbox[3] <= card_bottom
            ):
                raise RuntimeError(f"Cyprus informative-cover line escaped its card: {line!r}")
            draw.text(origin, line, font=font, fill=(*accent, 255))
            line_layouts.append(
                {
                    "text": line,
                    "origin": [int(origin[0]), int(origin[1])],
                    "bbox": list(pixel_bbox),
                }
            )
            line_top = pixel_bbox[3] + _COVER_FACT_LINE_GAP

        layouts.append(
            {
                "source_fact": source_value,
                "display_text": display,
                "font_size": font_size,
                "card_bbox": list(card_bbox),
                "lines": line_layouts,
            }
        )
        card_top = card_bottom + _COVER_FACT_CARD_GAP
    return layouts


def _cover_number(value: float | None) -> str:
    if not isinstance(value, (int, float)):
        return ""
    return f"{float(value):.1f}".rstrip("0").rstrip(".")


def _cover_aqi(final_text: str) -> tuple[str, str] | None:
    match = re.search(r"\bAQI\s*[:=]?\s*(\d{1,3})\b", str(final_text or ""), re.I)
    if not match:
        return None
    value = int(match.group(1))
    label = "ХОРОШИЙ" if value <= 50 else "УМЕРЕННЫЙ" if value <= 100 else "ПОВЫШЕННЫЙ"
    return str(value), label


def _informative_cover_facts(final_text: str, *, post_type: str) -> tuple[object, dict[str, str]]:
    ctx = parse_visual_context_cy(final_text, post_type=post_type)
    headline = "КИПР СЕГОДНЯ" if post_type == "morning" else "КИПР ЗАВТРА"
    facts: list[str] = []
    hottest = ctx.hottest_city or ""
    if ctx.temp_max is not None:
        if hottest == "Никосия":
            facts.append(f"🔥 ДО {_cover_number(ctx.temp_max)}° В НИКОСИИ")
        elif hottest:
            facts.append(f"🔥 ДО {_cover_number(ctx.temp_max)}° · {hottest.upper()}")
        else:
            facts.append(f"🔥 ДО {_cover_number(ctx.temp_max)}° НА КИПРЕ")
    if ctx.gust_max is not None:
        location = "У МОРЯ" if ctx.coastal_focus else "НА ОСТРОВЕ"
        facts.append(f"💨 ПОРЫВЫ ДО {_cover_number(ctx.gust_max)} М/С {location}")
    if ctx.explicit_storm and not ctx.actual_precipitation:
        facts.append("⚠️ ШТОРМОВОЙ ВЕТЕР")
    elif ctx.actual_precipitation:
        facts.append("🌧 ДОЖДЬ МЕСТАМИ")
    elif ctx.visibility_condition in {"dense_fog", "fog", "mist"}:
        facts.append("🌫 ТУМАН УТРОМ")
    elif ctx.sea_temp_min is not None and ctx.sea_temp_max is not None:
        low = _cover_number(ctx.sea_temp_min)
        high = _cover_number(ctx.sea_temp_max)
        facts.append(f"🌊 МОРЕ {low}°" if low == high else f"🌊 МОРЕ {low}–{high}°")
    elif (aqi := _cover_aqi(final_text)) is not None:
        value, label = aqi
        prefix = "🏭 ВОЗДУХ СЕЙЧАС:" if post_type == "evening" else "🏭"
        facts.append(f"{prefix} AQI {value} · {label}")
    if not facts:
        qualitative = {
            "clear": "☀️ ЯСНЫЙ ДЕНЬ",
            "mixed": "⛅ ПЕРЕМЕННАЯ ОБЛАЧНОСТЬ",
            "cloudy": "☁️ ОБЛАЧНЫЙ ДЕНЬ",
            "hot": "🔥 ЖАРКИЙ ДЕНЬ",
            "dusty": "🌫 СУХАЯ ПЫЛЕВАЯ ДЫМКА",
            "fog": "🌫 ТУМАН УТРОМ",
            "rain": "🌧 ДОЖДЬ МЕСТАМИ",
        }
        facts.append(qualitative.get(ctx.primary_weather, "ПОГОДА НА КИПРЕ"))
    primary_fact = facts[0]
    secondary_fact = facts[1] if len(facts) > 1 else ""
    tertiary_fact = facts[2] if len(facts) > 2 else ""
    return ctx, {
        "headline": headline,
        "primary_fact": primary_fact,
        "secondary_fact": secondary_fact,
        "tertiary_fact": tertiary_fact,
    }


def _cover_palette(ctx: object) -> tuple[str, tuple[int, int, int], tuple[int, int, int], tuple[int, int, int]]:
    primary = str(getattr(ctx, "primary_weather", "unknown"))
    if bool(getattr(ctx, "explicit_storm", False)):
        return "storm", (35, 50, 68), (85, 108, 126), (236, 242, 245)
    if bool(getattr(ctx, "actual_precipitation", False)):
        return "rain", (97, 126, 148), (184, 202, 213), (245, 249, 250)
    if primary == "fog":
        return "fog", (193, 202, 202), (237, 235, 224), (39, 67, 73)
    if primary == "dusty":
        return "dust", (202, 176, 130), (244, 226, 191), (70, 72, 62)
    if primary == "hot":
        return "hot", (231, 185, 103), (255, 242, 207), (24, 81, 119)
    if bool(getattr(ctx, "strong_wind", False)):
        return "wind", (107, 174, 211), (228, 244, 249), (23, 78, 113)
    return "fair", (101, 177, 211), (245, 237, 199), (25, 76, 107)


def _draw_cover_weather_motif(draw: ImageDraw.ImageDraw, ctx: object, accent: tuple[int, int, int]) -> None:
    # Purposefully graphic, not pseudo-photographic: sun, sea and factual hazard marks.
    draw.ellipse((770, 105, 945, 280), fill=(*accent, 42), outline=(*accent, 115), width=5)
    for offset in range(4):
        y = 835 + offset * 34
        draw.arc((90, y - 30, 990, y + 55), 195, 345, fill=(*accent, 115 - offset * 15), width=5)
    if bool(getattr(ctx, "strong_wind", False)):
        for index, width in enumerate((280, 390, 330)):
            y = 345 + index * 47
            draw.arc((660 - width, y, 660, y + 60), 190, 350, fill=(*accent, 130), width=5)
    if str(getattr(ctx, "primary_weather", "")) == "fog":
        for index in range(5):
            y = 305 + index * 28
            draw.line((640, y, 965 - index * 22, y), fill=(*accent, 70), width=8)
    if bool(getattr(ctx, "actual_precipitation", False)):
        for x in range(720, 970, 48):
            draw.line((x, 320, x - 13, 362), fill=(*accent, 145), width=5)


def render_local_informative_cover(
    final_text: str,
    *,
    target_date: str,
    post_type: str,
    output_path: str | Path,
    minimum_bytes: int,
) -> dict[str, Any]:
    """Render a deterministic factual Cyprus cover after network providers fail."""

    safe_date = _safe_target_date(target_date)
    mode = str(post_type or "").strip().lower()
    if mode not in {"morning", "evening"}:
        raise ValueError(f"invalid Cyprus informative-cover post type: {post_type!r}")
    ctx, facts = _informative_cover_facts(final_text, post_type=mode)
    palette, top, bottom, accent = _cover_palette(ctx)
    rendered_lines = [facts["headline"]]
    rendered_lines.extend(value for key, value in facts.items() if key != "headline" and value)
    rendered_text = "\n".join(rendered_lines[:4])
    cache_payload = {
        "renderer_version": LOCAL_INFORMATIVE_COVER_VERSION,
        "target_date": safe_date,
        "post_type": mode,
        "visual_forecast_period": ctx.visual_forecast_period,
        "primary_weather": ctx.primary_weather,
        "hazards": ctx.hazards,
        "scene_focus": ctx.scene_focus,
        "headline": facts["headline"],
        "primary_fact": facts["primary_fact"],
        "secondary_fact": facts["secondary_fact"],
        "tertiary_fact": facts["tertiary_fact"],
        "actual_precipitation": ctx.actual_precipitation,
        "explicit_storm": ctx.explicit_storm,
        "severe_wind": ctx.severe_wind,
        "visibility_condition": ctx.visibility_condition,
    }
    cache_json = json.dumps(cache_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    cache_key = f"{LOCAL_INFORMATIVE_COVER_VERSION}:{hashlib.sha256(cache_json.encode('utf-8')).hexdigest()}"

    size = 1080
    image = Image.new("RGBA", (size, size), (*top, 255))
    draw = ImageDraw.Draw(image, "RGBA")
    for y in range(size):
        draw.line((0, y, size, y), fill=(*_mix(top, bottom, y / (size - 1)), 255))
    _draw_cover_weather_motif(draw, ctx, accent)
    draw.rounded_rectangle((68, 58, 1012, 1012), radius=48, fill=(255, 255, 255, 26), outline=(255, 255, 255, 88), width=3)
    title_font = _cover_font(74, bold=True)
    small_font = _cover_font(28, bold=False)
    draw.text((100, 105), facts["headline"], font=title_font, fill=(*accent, 255))
    draw.text((102, 205), "VAYBOMETER · WEATHER BRIEF", font=small_font, fill=(*accent, 175))
    fact_values = [facts["primary_fact"], facts["secondary_fact"], facts["tertiary_fact"]]
    fact_layout = _draw_cover_fact_cards(
        draw,
        [item for item in fact_values if item],
        accent=accent,
    )
    fact_layout_json = json.dumps(fact_layout, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    output = Path(output_path).with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    metadata: dict[str, Any] = {
        "backend": "local_informative_cover",
        "generator_version": LOCAL_INFORMATIVE_COVER_VERSION,
        "renderer_version": LOCAL_INFORMATIVE_COVER_VERSION,
        "target_date": safe_date,
        "post_type": mode,
        "visual_forecast_period": ctx.visual_forecast_period,
        "primary_weather": ctx.primary_weather,
        "hazards": ",".join(ctx.hazards),
        "scene_focus": ctx.scene_focus,
        "headline": facts["headline"],
        "primary_fact": facts["primary_fact"],
        "secondary_fact": facts["secondary_fact"],
        "tertiary_fact": facts["tertiary_fact"],
        "fact_layout": fact_layout_json,
        "actual_precipitation": str(bool(ctx.actual_precipitation)).lower(),
        "explicit_storm": str(bool(ctx.explicit_storm)).lower(),
        "severe_wind": str(bool(ctx.severe_wind)).lower(),
        "rain_graphics": str(bool(ctx.actual_precipitation)).lower(),
        "storm_graphics": str(bool(ctx.explicit_storm)).lower(),
        "rendered_text": rendered_text,
        "palette": palette,
        "cache_key": cache_key,
    }
    png_info = PngImagePlugin.PngInfo()
    for key, value in metadata.items():
        png_info.add_text(key, str(value))
    image.convert("RGB").save(output, format="PNG", pnginfo=png_info, compress_level=6)
    if output.stat().st_size <= int(minimum_bytes):
        image.convert("RGB").save(output, format="PNG", pnginfo=png_info, compress_level=0)
    with Image.open(output) as verify_image:
        if verify_image.size != (size, size) or verify_image.format != "PNG":
            raise RuntimeError("local informative cover has invalid dimensions or format")
        verify_image.verify()
    if output.stat().st_size <= int(minimum_bytes):
        raise RuntimeError(
            f"local informative cover is too small: {output.stat().st_size} bytes; must exceed {minimum_bytes}"
        )
    return {"path": str(output), "bytes": output.stat().st_size, "metadata": metadata}


def render_local_weather_card(
    final_text: str,
    *,
    target_date: str,
    post_type: str,
    output_path: str | Path,
    minimum_bytes: int,
) -> dict[str, Any]:
    """Compatibility entry point; atmospheric v2 is retired from fallback selection."""

    return render_local_informative_cover(
        final_text,
        target_date=target_date,
        post_type=post_type,
        output_path=output_path,
        minimum_bytes=minimum_bytes,
    )


__all__ = [
    "LOCAL_WEATHER_CARD_VERSION",
    "LOCAL_INFORMATIVE_COVER_VERSION",
    "load_provider_health",
    "mark_provider_duplicate",
    "provider_health_exclusions",
    "provider_health_path",
    "record_provider_attempts",
    "render_local_weather_card",
    "render_local_informative_cover",
    "write_provider_health",
]
