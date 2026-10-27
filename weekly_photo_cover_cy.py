#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Opt-in offline-only weekly Cyprus photo compositor. No weather/API access."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping
import hashlib
import json
import re
import math

SIZE = (1080, 1350)
RENDERER_VERSION = "cy_weekly_photo_r2_candidate"
ASSET_ROOT = Path(__file__).resolve().parent / "assets" / "weekly" / "cy"
MANIFEST = ASSET_ROOT / "manifest.json"
_HEADINGS = (
    "✨ Главный фон недели", "🌿 Смысл недели", "🌦 Погода", "🌊 Море",
    "🏄 Вода и спорт", "🏭 Воздух сейчас", "🧲 Космопогода сейчас",
    "🌙 Луна и астроритм (интерпретация)", "↔️ К прошлому недельному прогнозу",
    "✅ Как прожить неделю",
)
# Exact PR #104 fail-closed routine texts: no optimistic SUP/kite/spot claims.
_ROUTINE_SPORT = (
    "SUP: безопасность по спотам и времени не подтверждена; выход без локальной проверки не планировать.",
    "Кайт/винг: подходящие окна по спотам не подтверждены; порывы и направление проверить до выхода.",
    "Серф: зависит от фактической волны; скорее не главный сценарий недели.",
)
_PARTIAL = re.compile(r"не подтвержд|пока не собран|прогноз уточн|недостаточно данных|данные обнов|нет данных|неполный прогноз|не указана", re.I)
# Any independent warning not shown completely in 3-column photo => use legacy.
_HAZARD = re.compile(
    r"шторм|предупрежд|опасн|небезопасн|гроз|молни|ливн|дожд|осадк|порыв|"
    r"волн.{0,15}(?:высок|опасн|сильн)|высок.{0,15}волн|ветер.{0,12}сильн|"
    r"сильн.{0,12}ветер|туман|пыл|дымк|смог|голол|не выходить|отложить|"
    r"не рекоменд|неидеал|уф.{0,12}высок|высок.{0,12}уф|экстрем|жар",
    re.I,
)
_TEMP = re.compile(r"(?<!\d)(-?\d{1,2})\s*[–—−-]\s*(-?\d{1,2})\s*°\s*[CС]")
_SEA = re.compile(r"(?<!\d)(-?\d{1,2})(?:\s*[–—−-]\s*(-?\d{1,2}))?\s*°\s*[CС]")


def _sections(text: str) -> dict[str, list[str]]:
    result = {x: [] for x in _HEADINGS}
    active: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if line in result:
            active = line
        elif line and active:
            result[active].append(line)
    return result


def _source_guard(text: str) -> tuple[str, str, str, str]:
    """Never silently omit unsupported safety or incomplete-source facts."""
    if not text.strip() or not text.startswith("🗓 Вайб недели:"):
        raise ValueError("weekly source text missing")
    data = _sections(text)
    main = data["✨ Главный фон недели"]
    weather = data["🌦 Погода"]
    sea = data["🌊 Море"]
    if len(main) != 1 or len(weather) != 1 or len(sea) != 1:
        raise ValueError("source facts cannot be represented without omission")
    if tuple(data["🏄 Вода и спорт"]) != _ROUTINE_SPORT:
        raise ValueError("unknown or stronger water safety instruction: use legacy")
    # Examine EVERY source line, including the date header and stray preamble.
    # Only the three byte-exact PR #104 sports sentences (already verified
    # above, in the sports section) may be excluded from generic hazard scans.
    # No unknown preamble or unrecognized warning heading is allowed to hide
    # outside the known section extractor.
    in_section: str | None = None
    first_heading_seen = False
    for index, raw in enumerate(text.splitlines()):
        line = raw.strip()
        if not line:
            continue
        if line in _HEADINGS:
            first_heading_seen = True
            in_section = line
            continue
        if index and not first_heading_seen:
            raise ValueError("unrepresented weather/safety warning before weekly sections")
        if in_section == "🏄 Вода и спорт" and line in _ROUTINE_SPORT:
            continue
        if _HAZARD.search(line) or re.match(r"^[⚠🚨❗🛑]", line):
            raise ValueError("unrepresented weather/safety warning in full weekly text")
    if any(_PARTIAL.search(row) for rows in (main, weather, sea) for row in rows):
        raise ValueError("seven-day and marine facts are not complete")
    temp = _TEMP.search(weather[0])
    marine = _SEA.search(sea[0])
    if not temp or not marine or "Средняя вода" not in sea[0]:
        raise ValueError("missing sourced temperature or marine observation")
    # A future sea temperature may not be inferred from an observed SST.
    temperature = f"{temp.group(1)}–{temp.group(2)}°C"
    sea_label = (f"{marine.group(1)}–{marine.group(2)}°C" if marine.group(2) else f"{marine.group(1)}°C")
    return weather[0], temperature, sea_label, main[0]


# A weekly prose summary is not a trustworthy sky classifier on its own.
# Recognize only explicit sky conditions within the weather section, then
# cross-check optional sky cues in the headline against that same class.
_SKY_CUES = {
    "sunny": re.compile(r"\b(?:ясн\w*|солнечн\w*|безоблачн\w*)\b", re.I),
    "neutral": re.compile(
        r"\b(?:переменн\w*\s+облачност\w*|частичн\w*\s+облачност\w*|"
        r"облачн\w*\s+с\s+прояснени\w*)\b", re.I),
    "overcast": re.compile(
        r"\b(?:пасмурн\w*|(?:сплошн\w*|плотн\w*)\s+облачност\w*)\b", re.I),
}
_SKY_NEGATION_BEFORE = re.compile(
    r"\b(?:не|ни|без|нет|отсутству\w*)\b(?:\s+[\w-]+){0,3}\s+$", re.I
)
_SKY_NEGATION_AFTER = re.compile(
    r"^\s+(?:[\w-]+\s+){0,2}(?:не|нет|отсутству\w*)\b", re.I
)
_SKY_UNCERTAIN = re.compile(r"\b(?:возможн\w*|вероятн\w*|предположительно|"
                            r"неизвестн\w*|неясн\w*|неопредел[её]н\w*)\b", re.I)


def _sky_cues(source: str) -> set[str]:
    """Refuse negated, conflicting, speculative or insufficient sky evidence."""
    s = source.casefold()
    if _SKY_UNCERTAIN.search(s):
        raise ValueError("weekly sky state uncertain: use legacy")
    found: set[str] = set()
    for variant, rx in _SKY_CUES.items():
        for cue in rx.finditer(s):
            before = s[max(0, cue.start()-65):cue.start()]
            after = s[cue.end():cue.end()+55]
            if (_SKY_NEGATION_BEFORE.search(before)
                    or _SKY_NEGATION_AFTER.match(after)):
                raise ValueError("negated weekly sky cue: use legacy")
            found.add(variant)
    # Generic clouds are not proof of solid overcast or a safely sunny week.
    # E.g. `ясно и облачно` must not claim a pure clear sky.
    if re.search(r"\bоблачн\w*\b", s) and not ({"neutral", "overcast"} & found):
        raise ValueError("ambiguous weekly cloud state: use legacy")
    if len(found) > 1:
        raise ValueError("contradictory weekly sky cues: use legacy")
    return found


def _variant(main: str, weather: str) -> str:
    weather_cues = _sky_cues(weather)
    headline_cues = _sky_cues(main)
    if len(weather_cues) != 1:
        raise ValueError("weekly sky state not confirmed in weather source: use legacy")
    if headline_cues and headline_cues != weather_cues:
        raise ValueError("headline/weather sky contradiction: use legacy")
    return next(iter(weather_cues))


def _font(size: int, bold: bool = False):
    from PIL import ImageFont
    name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
    return ImageFont.truetype(name, size=size)


def _paint(draw: Any, xy: tuple[int, int], label: str, font: Any, inside: tuple[int, int, int, int], color: str) -> tuple[int,int,int,int]:
    bounds = draw.textbbox(xy, label, font=font, stroke_width=0)
    a,b,c,d=inside
    if not (a <= bounds[0] <= bounds[2] <= c and b <= bounds[1] <= bounds[3] <= d and 135 <= bounds[1] <= bounds[3] <= 1215):
        raise ValueError("weekly text outside measured Telegram safe zone")
    draw.text(xy, label, font=font, fill=color)
    return bounds


def _wrap(draw: Any, value: str, font: Any, limit: int, max_lines: int) -> list[str]:
    lines: list[str] = []
    for word in value.split():
        if draw.textbbox((0, 0), word, font=font)[2] > limit:
            raise ValueError("weekly fact contains overwide word")
        if lines and draw.textbbox((0, 0), lines[-1] + " " + word, font=font)[2] <= limit:
            lines[-1] += " " + word
        else:
            lines.append(word)
    if not 1 <= len(lines) <= max_lines:
        raise ValueError("weekly fact cannot fit without truncation")
    return lines


def _trusted_wmo_variant(weather_payload: Mapping[str, Any] | None, start: date) -> str:
    """Classify only exact provider-annotated island-wide 7-day WMO data.

    A single missing city/day, code, provider provenance, weather hazard or mixed
    weekly sky class must refuse the optional photo; it never changes the forecast.
    """
    from settings_cy import INLAND_CITIES, MARINE_CITIES

    cities = (*MARINE_CITIES, *INLAND_CITIES)
    if not isinstance(weather_payload, Mapping) or set(weather_payload) != set(cities):
        raise ValueError("unknown weekly WMO island coverage: use legacy")
    expected = [(start + timedelta(days=day)).isoformat() for day in range(7)]
    classes: set[str] = set()
    for city in cities:
        payload = weather_payload.get(city)
        if not isinstance(payload, Mapping):
            raise ValueError("unknown weekly WMO city source: use legacy")
        meta = payload.get("_weekly_meta")
        daily = payload.get("daily")
        if not isinstance(meta, Mapping) or not isinstance(daily, Mapping):
            raise ValueError("unknown weekly WMO provenance: use legacy")
        units = meta.get("normalized_units")
        if (meta.get("provider") != "open-meteo"
                or meta.get("weather_code_system") != "wmo"
                or meta.get("coverage_complete") is not True
                or meta.get("coverage_days") != 7
                or meta.get("requested_start") != expected[0]
                or meta.get("requested_end") != expected[-1]
                or meta.get("returned_dates") != expected
                or not isinstance(units, Mapping)
                or units.get("wind_speed") != "m/s"
                or units.get("precipitation") != "mm"):
            raise ValueError("unverified weekly WMO source metadata: use legacy")
        dates = daily.get("time", daily.get("date"))
        if ("time" in daily and "date" in daily and daily["time"] != daily["date"]):
            raise ValueError("conflicting weekly daily date aliases: use legacy")
        if not isinstance(dates, list) or dates != expected:
            raise ValueError("weekly WMO dates incomplete/unaligned: use legacy")
        if ("weather_code" in daily and "weathercode" in daily
                and daily["weather_code"] != daily["weathercode"]):
            raise ValueError("conflicting WMO code aliases: use legacy")
        codes = daily.get("weather_code", daily.get("weathercode"))
        if not isinstance(codes, list) or len(codes) != 7:
            raise ValueError("weekly WMO codes incomplete: use legacy")

        # Independent hazards must not contradict a sunny/neutral photo even
        # if the reported WMO code is nominally clear. These are existing
        # weekly source fields and conservative *photo-only* refusal gates.
        # No water-activity or publisher safety threshold is changed.
        for field, limit, strict_positive in (
            ("precipitation_probability_max", 40.0, False),
            ("precipitation_sum", 0.0, True),
            ("wind_gusts_10m_max", 10.0, False),
            ("uv_index_max", 6.0, False),
            ("temperature_2m_max", 33.0, False),
        ):
            values = daily.get(field)
            if not isinstance(values, list) or len(values) != 7:
                raise ValueError("missing weekly photo hazard source: use legacy")
            for value in values:
                if (type(value) not in (int, float) or not math.isfinite(value)
                        or value < 0 and field not in ("temperature_2m_max",)):
                    raise ValueError("invalid weekly photo hazard value: use legacy")
                if (value > limit if strict_positive else value >= limit):
                    raise ValueError("independent weekly photo weather hazard: use legacy")
        for code in codes:
            if type(code) is not int:  # reject None, booleans, decimal/NaN/inf
                raise ValueError("unknown weekly WMO code: use legacy")
            # WMO 0 clear; 1/2 partly cloudy; 3 overcast; anything else
            # includes fog, precipitation, snow, thunder and unknown codes.
            if code == 0:
                classes.add("sunny")
            elif code in (1, 2):
                classes.add("neutral")
            elif code == 3:
                classes.add("overcast")
            else:
                raise ValueError("hazardous/unknown weekly WMO code: use legacy")
    if len(classes) != 1:
        raise ValueError("mixed weekly WMO sky conditions: use legacy")
    return next(iter(classes))


def _trusted_variant(main: str, weather: str, weather_payload: Mapping[str, Any] | None, start: date) -> str:
    variant = _trusted_wmo_variant(weather_payload, start)
    # Editorial words are never positive evidence. They are ONLY a veto for
    # contradictions/negations, so an inserted `ясно` cannot select sunny.
    for prose in (main, weather):
        cues = _sky_cues(prose)
        if cues and cues != {variant}:
            raise ValueError("weekly text/WMO sky contradiction: use legacy")
    return variant


def render_photo_cover(text: str, *, start: date, output_path: str | Path,
                       trusted_weather: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from PIL import Image, ImageDraw, PngImagePlugin
    weather, temp, sea, main = _source_guard(text)
    doc = json.loads(MANIFEST.read_text("utf-8"))
    if doc.get("approved_for_production") is not True:
        raise ValueError("Cyprus weekly photo assets require independent visual approval")
    if doc.get("version") != "cy_weekly_photos_r2_candidate" or doc.get("size") != [1080, 1350]:
        raise ValueError("invalid weekly photo manifest")
    variant = _trusted_variant(main, weather, trusted_weather, start)
    item = doc.get("variants", {}).get(variant)
    if not isinstance(item, dict) or not item.get("approved"):
        raise ValueError("weekly scene is unapproved")
    asset = ASSET_ROOT / str(item.get("file", ""))
    if asset.parent.resolve() != ASSET_ROOT.resolve() or not asset.is_file():
        raise ValueError("missing/bad weekly asset path")
    raw = asset.read_bytes()
    if len(raw) > 8_000_000 or hashlib.sha256(raw).hexdigest() != item.get("sha256"):
        raise ValueError("weekly asset provenance mismatch")
    with Image.open(asset) as original:
        original.load()
        if original.size != SIZE or original.format != "JPEG":
            raise ValueError("weekly candidate must be full-resolution verified JPEG")
        photo = original.convert("RGBA")
    overlay = Image.new("RGBA", SIZE, (0, 0, 0, 0))
    layer = ImageDraw.Draw(overlay)
    boxes = ((44, 148, 1036, 351), (46, 913, 1034, 976), (45, 997, 1035, 1212))
    for rect in boxes:
        layer.rounded_rectangle(rect, radius=28, fill=(10, 28, 43, 235), outline=(234,242,247,176), width=2)
    image = Image.alpha_composite(photo, overlay).convert("RGB")
    draw = ImageDraw.Draw(image)
    painted=[]
    painted.append(_paint(draw,(74,164),"VAYBOMETER · CYPRUS",_font(22,True),boxes[0],"#DDEEF4"))
    painted.append(_paint(draw,(74,213),"ВАЙБ НЕДЕЛИ",_font(54,True),boxes[0],"#FFFFFF"))
    end = start + timedelta(days=6)
    painted.append(_paint(draw,(75,290),f"{start:%d.%m}–{end:%d.%m.%Y}",_font(26),boxes[0],"#E0EFF5"))
    painted.append(_paint(draw,(75,925),"SUP / КАЙТ / СЁРФ: ОКНА НЕ ПОДТВЕРЖДЕНЫ",_font(21,True),boxes[1],"#FFFFFF"))
    facts = (("ПОГОДА", weather.split(";", 1)[1].strip() if ";" in weather else weather), ("ТЕМПЕРАТУРА", temp), ("МОРЕ СЕЙЧАС", sea))
    # A nonhazardous forecast does not license claiming a specific sky scenario.
    # The photo is explicitly an illustration, while sourced numeric facts are copied.
    for i,(heading,value) in enumerate(facts):
        x=(72,403,736)[i]
        width=(293,287,287)[i]
        cell=(x,1022,x+width,1192)
        painted.append(_paint(draw,(x,1030),heading,_font(18,True),cell,"#D6EEF5"))
        for n,line in enumerate(_wrap(draw,value,_font(22,True),width,3)):
            painted.append(_paint(draw,(x,1078+n*36),line,_font(22,True),cell,"#FFFFFF"))
    meta = {"renderer_version":RENDERER_VERSION,"region":"cyprus","week_start":start.isoformat(),
            "asset_sha256":hashlib.sha256(raw).hexdigest(),"source_text_sha256":hashlib.sha256(text.encode()).hexdigest(),
            "variant":variant,"weather_provenance":"seven_day_island_wmo_all_cities", "temperature":temp,"marine_observation":sea,"photo_is_illustration":True,
            "safety_footer":"spot/time windows not confirmed","text_boxes":len(painted)}
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    info = PngImagePlugin.PngInfo()
    for k,v in meta.items(): info.add_text(k,str(v))
    image.save(out,format="PNG",pnginfo=info,optimize=True)
    return dict(meta,path=str(out),width=SIZE[0],height=SIZE[1])
