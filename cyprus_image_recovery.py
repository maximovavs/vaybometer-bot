#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Persistent provider health and deterministic Cyprus weather-card fallback."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import random
import re
from typing import Any, Iterable

from PIL import Image, ImageDraw, ImageFont, PngImagePlugin

from visual_context_cy import parse_visual_context_cy


LOCAL_WEATHER_CARD_VERSION = "cy_local_weather_card_v1"
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


def _font(size: int, *, bold: bool = False) -> ImageFont.ImageFont:
    filename = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    candidates = (
        filename,
        f"/usr/share/fonts/truetype/dejavu/{filename}",
        f"C:/Windows/Fonts/{'arialbd.ttf' if bold else 'arial.ttf'}",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except OSError:
            continue
    return ImageFont.load_default()


def _mix(a: tuple[int, int, int], b: tuple[int, int, int], ratio: float) -> tuple[int, int, int]:
    return tuple(round(a[index] * (1.0 - ratio) + b[index] * ratio) for index in range(3))


def _draw_cloud(draw: ImageDraw.ImageDraw, x: int, y: int, scale: float, fill: tuple[int, int, int, int]) -> None:
    parts = (
        (x, y + 28 * scale, x + 120 * scale, y + 88 * scale),
        (x + 28 * scale, y, x + 100 * scale, y + 78 * scale),
        (x + 72 * scale, y + 10 * scale, x + 154 * scale, y + 86 * scale),
    )
    for box in parts:
        draw.ellipse(tuple(round(value) for value in box), fill=fill)


def _weather_label(weather: str, *, haze: bool, severe_wind: bool) -> str:
    if severe_wind and weather not in {"rain", "storm"}:
        return "ВЕТЕР У МОРЯ"
    labels = {
        "storm": "ГРОЗОВОЙ ФРОНТ",
        "rain": "ЛОКАЛЬНЫЕ ДОЖДИ",
        "dusty": "ПЫЛЬНАЯ ДЫМКА",
        "hot": "ЖАРКИЙ ДЕНЬ",
        "cloudy": "ОБЛАЧНЫЙ ДЕНЬ",
        "mixed": "ПЕРЕМЕННАЯ ПОГОДА",
        "clear": "ЯСНЫЙ ДЕНЬ",
    }
    if haze and weather not in {"rain", "storm", "dusty"}:
        return "ВЛАЖНАЯ ДЫМКА"
    return labels.get(weather, "ПОГОДА НА ДЕНЬ")


def render_local_weather_card(
    final_text: str,
    *,
    target_date: str,
    post_type: str,
    output_path: str | Path,
    minimum_bytes: int,
) -> dict[str, Any]:
    """Render a deterministic, deliberately graphic weather card without a network call."""

    safe_date = _safe_target_date(target_date)
    mode = str(post_type or "").strip().lower()
    if mode not in {"morning", "evening"}:
        raise ValueError(f"invalid local weather-card post type: {post_type!r}")
    ctx = parse_visual_context_cy(final_text, post_type=mode)
    focus = "inland" if ctx.inland_heat_focus and not ctx.coastal_focus else "coastal"
    wind = ctx.gust_max if ctx.gust_max is not None else ctx.wind_max
    seed_text = "|".join(
        [
            safe_date,
            mode,
            str(ctx.weather_main),
            str(wind or ""),
            str(ctx.visibility_haze),
            str(ctx.actual_precipitation),
            focus,
            LOCAL_WEATHER_CARD_VERSION,
        ]
    )
    digest = hashlib.sha256(seed_text.encode("utf-8")).hexdigest()
    rng = random.Random(int(digest[:16], 16))

    size = 1080
    image = Image.new("RGB", (size, size))
    draw = ImageDraw.Draw(image)
    if mode == "morning":
        top, horizon, sea = (54, 112, 166), (163, 208, 220), (24, 116, 150)
    else:
        top, horizon, sea = (24, 34, 79), (127, 111, 146), (18, 69, 104)
    if ctx.weather_main in {"rain", "storm", "cloudy"}:
        top = _mix(top, (55, 67, 82), 0.45)
        horizon = _mix(horizon, (122, 139, 147), 0.42)
    elif ctx.weather_main in {"hot", "dusty"}:
        horizon = _mix(horizon, (224, 171, 111), 0.35)

    horizon_y = 590 if focus == "coastal" else 650
    for y in range(size):
        if y <= horizon_y:
            ratio = y / max(1, horizon_y)
            color = _mix(top, horizon, ratio)
        else:
            ratio = (y - horizon_y) / max(1, size - horizon_y)
            color = _mix(sea, (7, 45, 69), ratio)
        draw.line((0, y, size, y), fill=color)

    texture = Image.new("RGBA", image.size, (0, 0, 0, 0))
    texture_draw = ImageDraw.Draw(texture)
    for _ in range(9000):
        x = rng.randrange(size)
        y = rng.randrange(size)
        alpha = rng.randrange(4, 18)
        shade = 255 if rng.random() > 0.45 else 0
        texture_draw.point((x, y), fill=(shade, shade, shade, alpha))
    image = Image.alpha_composite(image.convert("RGBA"), texture)
    draw = ImageDraw.Draw(image, "RGBA")

    if focus == "coastal":
        for offset in range(0, 280, 34):
            y = horizon_y + 45 + offset
            amplitude = 10 + (offset // 34) * 2
            points = []
            for x in range(-30, size + 31, 30):
                points.append((x, y + round(amplitude * ((x // 30 + rng.randrange(3)) % 3 - 1) / 2)))
            draw.line(points, fill=(207, 235, 235, 95), width=4)
        draw.polygon(
            [(0, 830), (240, 784), (500, 816), (790, 748), (1080, 805), (1080, 1080), (0, 1080)],
            fill=(230, 206, 158, 110),
        )
    else:
        draw.polygon(
            [(0, 765), (170, 650), (325, 722), (520, 570), (735, 728), (900, 610), (1080, 750), (1080, 1080), (0, 1080)],
            fill=(38, 80, 72, 190),
        )
        draw.polygon(
            [(0, 830), (220, 738), (435, 830), (660, 710), (860, 810), (1080, 730), (1080, 1080), (0, 1080)],
            fill=(21, 57, 59, 225),
        )

    if ctx.weather_main in {"clear", "hot", "mixed"} and mode == "morning":
        draw.ellipse((785, 120, 935, 270), fill=(255, 231, 151, 225))
    if ctx.weather_main in {"cloudy", "rain", "storm", "mixed"} or ctx.visibility_haze:
        cloud_fill = (221, 228, 229, 215) if mode == "morning" else (166, 172, 192, 205)
        _draw_cloud(draw, 700, 155, 1.25, cloud_fill)
        _draw_cloud(draw, 825, 250, 0.72, cloud_fill)
    if ctx.weather_main in {"rain", "storm"} or ctx.actual_precipitation:
        for x in range(725, 1015, 34):
            draw.line((x, 345, x - 19, 418), fill=(191, 224, 239, 190), width=5)
    if ctx.severe_wind or (wind is not None and wind >= 12):
        for y, width in ((445, 220), (490, 300), (535, 175)):
            draw.arc((650, y - 45, 650 + width, y + 38), 190, 350, fill=(225, 244, 246, 185), width=5)

    # A clean abstract Cyprus silhouette: graphic brand element, not a generic photograph.
    island = [(135, 520), (205, 470), (315, 455), (390, 485), (465, 470), (520, 510), (455, 540), (365, 548), (300, 585), (210, 565)]
    draw.polygon(island, fill=(242, 181, 83, 225))
    draw.line(island + [island[0]], fill=(255, 224, 154, 235), width=5)

    title_font = _font(62, bold=True)
    label_font = _font(39, bold=True)
    detail_font = _font(27)
    tiny_font = _font(21, bold=True)
    label = "КИПР СЕГОДНЯ" if mode == "morning" else "КИПР ЗАВТРА"
    date_label = date.fromisoformat(safe_date).strftime("%d.%m.%Y")
    weather_label = _weather_label(
        str(ctx.weather_main),
        haze=bool(ctx.visibility_haze or ctx.dust_hint),
        severe_wind=bool(ctx.severe_wind),
    )
    detail_parts = ["побережье" if focus == "coastal" else "внутренние районы"]
    if wind is not None:
        detail_parts.append(f"ветер до {wind:g} м/с")
    if ctx.visibility_haze or ctx.dust_hint:
        detail_parts.append("дымка")
    if ctx.actual_precipitation:
        detail_parts.append("локальные осадки")

    panel = (68, 70, 650, 390)
    draw.rounded_rectangle(panel, radius=34, fill=(7, 26, 47, 178), outline=(255, 255, 255, 60), width=2)
    draw.text((105, 102), label, font=title_font, fill=(248, 250, 246, 255))
    draw.text((108, 183), date_label, font=detail_font, fill=(205, 226, 225, 255))
    draw.text((105, 245), weather_label, font=label_font, fill=(250, 193, 94, 255))
    draw.text((108, 318), " · ".join(detail_parts), font=detail_font, fill=(230, 238, 234, 255))
    draw.text((70, 1015), "VAYBOMETER · CYPRUS", font=tiny_font, fill=(230, 240, 236, 190))

    output = Path(output_path).with_suffix(".png")
    output.parent.mkdir(parents=True, exist_ok=True)
    png_info = PngImagePlugin.PngInfo()
    metadata = {
        "backend": "local_weather_card",
        "generator_version": LOCAL_WEATHER_CARD_VERSION,
        "target_date": safe_date,
        "post_type": mode,
        "weather_scenario": str(ctx.weather_main),
        "wind": "" if wind is None else f"{wind:g}",
        "cloud_haze": "1" if (ctx.visibility_haze or ctx.dust_hint) else "0",
        "focus": focus,
        "seed_sha256": digest,
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


__all__ = [
    "LOCAL_WEATHER_CARD_VERSION",
    "load_provider_health",
    "mark_provider_duplicate",
    "provider_health_exclusions",
    "provider_health_path",
    "record_provider_attempts",
    "render_local_weather_card",
    "write_provider_health",
]
