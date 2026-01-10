#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Cyprus repo).

Критичные правила для Кипра:
- Morning-пост Кипра ВСЕГДА БЕЗ FX-блока (никаких "💱 Курсы ..."), даже если fx.py существует.
- Источники/координаты для воздуха/погоды/заката должны быть кипрскими (не KLD).
- Картинки: 5 стилей, стиль выбирается детерминированно от даты (YYYY-MM-DD),
  не "скачет" при ретраях в тот же день, меняется между днями.
  Имя файла содержит style_id, чтобы избежать перезаписи и "залипания" кэша.
  Morning-картинка — по данным дня; Evening — по данным завтрашнего дня (или по DAY_OFFSET).
"""

from __future__ import annotations

import os
import re
import json
import html
import logging
import datetime as dt
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Union
import urllib.request
import urllib.error
import urllib.parse
import hashlib
import random

import pendulum

# Telegram (aiogram/telegram-bot style compatible: python-telegram-bot)
try:
    from telegram import Bot, constants  # type: ignore
except Exception:  # pragma: no cover
    Bot = Any  # type: ignore
    class constants:  # type: ignore
        class ParseMode:  # type: ignore
            HTML = "HTML"

# ────────────────────────── Optional project imports ──────────────────────────
try:
    from utils import compass, get_fact  # type: ignore
except Exception:  # pragma: no cover
    def compass(deg: float) -> str:  # type: ignore
        dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
        try:
            i = int((float(deg) + 22.5) // 45) % 8
            return dirs[i]
        except Exception:
            return "—"
    def get_fact(date_local: pendulum.DateTime, region_name: str) -> str:  # type: ignore
        return ""

# Weather / Air / etc — use repo modules if present, otherwise fallback to Open-Meteo
try:
    from weather import get_weather as _get_weather_repo  # type: ignore
except Exception:
    _get_weather_repo = None  # type: ignore

try:
    from air import get_air as _get_air_repo, get_sst as _get_sst_repo, get_kp as _get_kp_repo, get_solar_wind as _get_solar_wind_repo  # type: ignore
except Exception:
    _get_air_repo = None  # type: ignore
    _get_sst_repo = None  # type: ignore
    _get_kp_repo = None  # type: ignore
    _get_solar_wind_repo = None  # type: ignore

try:
    from pollen import get_pollen as _get_pollen_repo  # type: ignore
except Exception:
    _get_pollen_repo = None  # type: ignore

try:
    from radiation import get_radiation as _get_radiation_repo  # type: ignore
except Exception:
    _get_radiation_repo = None  # type: ignore

# LLM (optional)
try:
    from gpt import gpt_complete  # type: ignore
except Exception:
    gpt_complete = None  # type: ignore

# requests is optional; urllib is sufficient
try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

# ────────────────────────── Images ──────────────────────────
try:
    # primary (as in your Cyprus bot)
    from world_en.imagegen import generate_astro_image  # type: ignore
except Exception:
    try:
        from imagegen import generate_astro_image  # type: ignore
    except Exception:
        generate_astro_image = None  # type: ignore

try:
    from image_prompt_kld import build_kld_evening_prompt  # type: ignore
except Exception:
    build_kld_evening_prompt = None  # type: ignore

try:
    from image_prompt_kld import build_kld_morning_prompt  # type: ignore
except Exception:
    build_kld_morning_prompt = None  # type: ignore

try:
    from image_prompt_cy import build_cyprus_evening_prompt  # type: ignore
except Exception:
    build_cyprus_evening_prompt = None  # type: ignore

try:
    from image_prompt_cy_morning import build_cyprus_morning_prompt, MorningMetrics  # type: ignore
except Exception:
    build_cyprus_morning_prompt = None  # type: ignore
    MorningMetrics = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────── ENV flags ──────────────────────────
def _env_on(name: str, default: bool) -> bool:
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in ("1", "true", "yes", "on")


POST_MODE = (os.getenv("POST_MODE") or "evening").strip().lower()
# NOTE: do NOT rely on this global DAY_OFFSET inside functions that may be called with mode="morning" at runtime.
DAY_OFFSET = int(os.getenv("DAY_OFFSET", "0" if POST_MODE.startswith("morning") else "1"))
ASTRO_OFFSET = int(os.getenv("ASTRO_OFFSET", str(DAY_OFFSET)))

SHOW_AIR = _env_on("SHOW_AIR", POST_MODE != "evening")
SHOW_SPACE = _env_on("SHOW_SPACE", POST_MODE != "evening")
SHOW_SCHUMANN = _env_on("SHOW_SCHUMANN", POST_MODE != "evening")

DEBUG_WATER = os.getenv("DEBUG_WATER", "").strip().lower() in ("1", "true", "yes", "on")
DISABLE_SCHUMANN = os.getenv("DISABLE_SCHUMANN", "").strip().lower() in ("1", "true", "yes", "on")

# LLM parameters
USE_DAILY_LLM = os.getenv("DISABLE_LLM_DAILY", "").strip().lower() not in ("1", "true", "yes", "on")
ASTRO_LLM_TEMP = float(os.getenv("ASTRO_LLM_TEMP", "0.7"))

# ────────────────────────── base constants ──────────────────────────
NBSP = "\u00A0"
RUB = "\u20BD"

# Defaults
CY_LAT_DEFAULT = float(os.getenv("CY_LAT", "35.1856"))  # Nicosia approx
CY_LON_DEFAULT = float(os.getenv("CY_LON", "33.3823"))  # Nicosia approx
KLD_LAT_DEFAULT = float(os.getenv("KLD_LAT", "54.71"))
KLD_LON_DEFAULT = float(os.getenv("KLD_LON", "20.51"))

CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True, parents=True)

# Storm thresholds
STORM_GUST_MS = float(os.getenv("STORM_GUST_MS", "15"))
ALERT_GUST_MS = float(os.getenv("ALERT_GUST_MS", "20"))
ALERT_RAIN_MM_H = float(os.getenv("ALERT_RAIN_MM_H", "10"))
ALERT_TSTORM_PROB_PC = float(os.getenv("ALERT_TSTORM_PROB_PC", "70"))

# ────────────────────────── Open-Meteo fallback fetch ──────────────────────────
def _http_get_json(url: str, timeout: int = 20) -> Dict[str, Any]:
    req = urllib.request.Request(url, headers={"User-Agent": "VayboMeter/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:  # nosec B310
        raw = r.read().decode("utf-8", errors="replace")
    return json.loads(raw)


def _get_weather_fallback(lat: float, lon: float, tz_name: str = "Asia/Nicosia") -> Dict[str, Any]:
    tz = tz_name or "UTC"
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat:.5f}&longitude={lon:.5f}"
        f"&timezone={urllib.parse.quote(tz)}"
        "&forecast_days=7"
        "&hourly=temperature_2m,wind_speed_10m,wind_direction_10m,wind_gusts_10m,pressure_msl,rain,thunderstorm_probability,uv_index,uv_index_clear_sky"
        "&daily=temperature_2m_max,temperature_2m_min,weathercode,sunrise,sunset,uv_index_max"
    )
    try:
        return _http_get_json(url, timeout=20)
    except Exception as e:  # pragma: no cover
        logging.warning("weather fallback failed: %s", e)
        return {}


def get_weather(lat: float, lon: float, tz_name: str = "Asia/Nicosia") -> Dict[str, Any]:
    if _get_weather_repo is not None:
        try:
            # Most repos keep signature get_weather(lat, lon) and infer TZ internally.
            return _get_weather_repo(lat, lon) or {}
        except Exception as e:
            logging.warning("weather repo failed (%s); using fallback", e)
    return _get_weather_fallback(lat, lon, tz_name=tz_name)


def get_air(lat: float, lon: float) -> Dict[str, Any]:
    if _get_air_repo is not None:
        try:
            return _get_air_repo(lat, lon) or {}
        except Exception:
            return {}
    return {}


def get_sst(lat: float, lon: float) -> Optional[float]:
    if _get_sst_repo is not None:
        try:
            return _get_sst_repo(lat, lon)  # type: ignore[misc]
        except Exception:
            return None
    return None


def get_pollen() -> Dict[str, Any]:
    if _get_pollen_repo is not None:
        try:
            return _get_pollen_repo() or {}
        except Exception:
            return {}
    return {}


def get_radiation(lat: float, lon: float) -> Dict[str, Any]:
    if _get_radiation_repo is not None:
        try:
            return _get_radiation_repo(lat, lon) or {}
        except Exception:
            return {}
    return {}


def get_solar_wind() -> Dict[str, Any]:
    if _get_solar_wind_repo is not None:
        try:
            return _get_solar_wind_repo() or {}
        except Exception:
            return {}
    return {}


# ────────────────────────── Deterministic "now" for tests ──────────────────────────
def _now_like_work_date(tz_obj: pendulum.Timezone) -> pendulum.DateTime:
    wd = (os.getenv("WORK_DATE") or "").strip()
    if wd:
        try:
            return pendulum.parse(wd).in_tz(tz_obj)
        except Exception:
            pass
    return pendulum.now(tz_obj)


# ────────────────────────── Text helpers ──────────────────────────
def _sanitize_line(s: str, max_len: int = 200) -> str:
    s = (s or "").replace("\r", " ").replace("\n", " ").strip()
    s = re.sub(r"\s{2,}", " ", s).strip()
    if len(s) > max_len:
        s = s[: max_len - 1].rstrip() + "…"
    return s


def _int_or_nd(v: Any) -> str:
    try:
        if v is None:
            return "н/д"
        return str(int(round(float(v))))
    except Exception:
        return "н/д"


def _safe_slug(s: str) -> str:
    s = (s or "").strip().lower()
    s = re.sub(r"[^a-z0-9_-]+", "_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s or "default"


def _hashtag(s: str) -> str:
    s = (s or "").strip()
    if not s:
        return ""
    s = re.sub(r"[^\w]+", "", s, flags=re.UNICODE)
    if not s:
        return ""
    return "#" + s


def _fmt_delta(d: Any) -> str:
    try:
        x = float(d)
    except Exception:
        return "±0"
    sign = "+" if x > 0 else ""
    return f"{sign}{x:.2f}"


def _is_cyprus_region(region_name: str) -> bool:
    rn = (region_name or "").strip().lower()
    return ("cyprus" in rn) or ("кипр" in rn) or (rn in ("cy", "cyp", "nicosia", "лимассол", "лимаcсол"))


def _cyprus_title(region_name: str) -> str:
    return "Кипр" if _is_cyprus_region(region_name) else (region_name or "—")


# ────────────────────────── City pairs helpers ──────────────────────────
CityPairs = List[Tuple[str, Tuple[float, float]]]

def _normalize_city_pairs(cities: Any) -> CityPairs:
    out: CityPairs = []
    if not cities:
        return out

    if isinstance(cities, dict):
        for k, v in cities.items():
            try:
                if isinstance(v, (tuple, list)) and len(v) == 2:
                    out.append((str(k), (float(v[0]), float(v[1]))))
            except Exception:
                continue
        return out

    if isinstance(cities, (list, tuple)):
        for item in cities:
            try:
                if isinstance(item, (tuple, list)) and len(item) == 2:
                    name = str(item[0])
                    coords = item[1]
                    if isinstance(coords, (tuple, list)) and len(coords) == 2:
                        out.append((name, (float(coords[0]), float(coords[1]))))
                        continue
                if isinstance(item, dict):
                    name = str(item.get("name") or item.get("city") or item.get("title") or "")
                    la = item.get("lat")
                    lo = item.get("lon")
                    if name and la is not None and lo is not None:
                        out.append((name, (float(la), float(lo))))
            except Exception:
                continue
    return out


def _pick_ref_pair_for_region(
    sea_pairs: CityPairs,
    other_pairs: CityPairs,
    region_name: str,
) -> Tuple[str, Tuple[float, float]]:
    if other_pairs:
        return other_pairs[0][0], other_pairs[0][1]
    if sea_pairs:
        return sea_pairs[0][0], sea_pairs[0][1]
    if _is_cyprus_region(region_name):
        return "Nicosia", (CY_LAT_DEFAULT, CY_LON_DEFAULT)
    return "Kaliningrad", (KLD_LAT_DEFAULT, KLD_LON_DEFAULT)


def _pick_ref_coords(pairs: CityPairs, default: Tuple[float, float]) -> Tuple[float, float]:
    pairs = list(pairs or [])
    if pairs:
        return pairs[0][1]
    return default


def _iter_city_pairs(cities: Any) -> CityPairs:
    return _normalize_city_pairs(cities)


# ────────────────────────── Weather parsing helpers ──────────────────────────
def _parse_dt_list(items: Any) -> List[pendulum.DateTime]:
    out: List[pendulum.DateTime] = []
    if not isinstance(items, list):
        return out
    for x in items:
        try:
            if x is None:
                continue
            out.append(pendulum.parse(str(x)))
        except Exception:
            continue
    return out


def _hourly_times(wm: Dict[str, Any]) -> List[pendulum.DateTime]:
    hourly = (wm or {}).get("hourly") or {}
    return _parse_dt_list(hourly.get("time") or [])


def _daily_times(wm: Dict[str, Any]) -> List[pendulum.Date]:
    daily = (wm or {}).get("daily") or {}
    times = daily.get("time") or []
    out: List[pendulum.Date] = []
    if not isinstance(times, list):
        return out
    for t in times:
        try:
            out.append(pendulum.parse(str(t)).date())
        except Exception:
            try:
                out.append(pendulum.from_format(str(t), "YYYY-MM-DD").date())
            except Exception:
                continue
    return out


def _nearest_index_for_day(times: List[pendulum.DateTime], date_obj: pendulum.Date, hour: int, tz: pendulum.Timezone) -> Optional[int]:
    best_i: Optional[int] = None
    best_delta = None
    target = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, hour, 0, 0, tz=tz)
    for i, t in enumerate(times):
        try:
            tt = t.in_tz(tz)
            if tt.date() != date_obj:
                continue
            d = abs((tt - target).total_seconds())
            if best_delta is None or d < best_delta:
                best_delta = d
                best_i = i
        except Exception:
            continue
    return best_i


def _fetch_temps_for_offset(lat: float, lon: float, tz_name: str, offset_days: int) -> Tuple[Optional[float], Optional[float], Optional[int]]:
    tz_obj = pendulum.timezone(tz_name) if tz_name else pendulum.timezone("UTC")
    wm = get_weather(lat, lon, tz_name=tz_obj.name) or {}
    daily = wm.get("daily") or {}
    dts = _daily_times(wm)
    target = _now_like_work_date(tz_obj).add(days=int(offset_days)).date()

    tmax_arr = daily.get("temperature_2m_max") or daily.get("temperature_max_2m") or []
    tmin_arr = daily.get("temperature_2m_min") or daily.get("temperature_min_2m") or []
    code_arr = daily.get("weathercode") or daily.get("weather_code") or daily.get("weather_code_day") or []

    if dts and target in dts:
        idx = dts.index(target)
        try:
            tmax = float(tmax_arr[idx]) if idx < len(tmax_arr) and tmax_arr[idx] is not None else None
        except Exception:
            tmax = None
        try:
            tmin = float(tmin_arr[idx]) if idx < len(tmin_arr) and tmin_arr[idx] is not None else None
        except Exception:
            tmin = None
        try:
            wcode = int(code_arr[idx]) if idx < len(code_arr) and code_arr[idx] is not None else None
        except Exception:
            wcode = None
        return tmax, tmin, wcode

    # Fallback: take first element
    try:
        tmax = float(tmax_arr[0]) if tmax_arr and tmax_arr[0] is not None else None
    except Exception:
        tmax = None
    try:
        tmin = float(tmin_arr[0]) if tmin_arr and tmin_arr[0] is not None else None
    except Exception:
        tmin = None
    try:
        wcode = int(code_arr[0]) if code_arr and code_arr[0] is not None else None
    except Exception:
        wcode = None
    return tmax, tmin, wcode


def day_night_stats(lat: float, lon: float, tz: str = "Asia/Nicosia", offset_days: int = 1) -> Dict[str, Any]:
    tz_obj = pendulum.timezone(tz) if tz else pendulum.timezone("UTC")
    wm = get_weather(lat, lon, tz_name=tz_obj.name) or {}
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)
    temps = hourly.get("temperature_2m") or []
    date_obj = _now_like_work_date(tz_obj).add(days=int(offset_days)).date()
    idxs = [i for i, t in enumerate(times) if hasattr(t, "in_tz") and t.in_tz(tz_obj).date() == date_obj]
    vals: List[float] = []
    for i in idxs:
        if i < len(temps) and temps[i] is not None:
            try:
                vals.append(float(temps[i]))
            except Exception:
                pass
    if not vals:
        tmax, tmin, _ = _fetch_temps_for_offset(lat, lon, tz_obj.name, offset_days)
        return {"t_day_max": tmax, "t_night_min": tmin}
    return {"t_day_max": max(vals), "t_night_min": min(vals)}


def pick_header_metrics_for_offset(wm: Dict[str, Any], tz_obj: pendulum.Timezone, offset_days: int) -> Tuple[Optional[float], Optional[float], Optional[int], str]:
    hourly = (wm or {}).get("hourly") or {}
    times = _hourly_times(wm)
    date_obj = _now_like_work_date(tz_obj).add(days=int(offset_days)).date()
    idx_noon = _nearest_index_for_day(times, date_obj, 12, tz_obj)
    if idx_noon is None:
        return None, None, None, ""

    ws_arr = hourly.get("wind_speed_10m") or []
    wd_arr = hourly.get("wind_direction_10m") or []
    pr_arr = hourly.get("pressure_msl") or hourly.get("pressure_msl_hpa") or []

    wind_ms = None
    wind_dir = None
    press = None

    try:
        if idx_noon < len(ws_arr) and ws_arr[idx_noon] is not None:
            w = float(ws_arr[idx_noon])
            wind_ms = w / 3.6 if w > 20 else w  # heuristic: km/h -> m/s
    except Exception:
        wind_ms = None
    try:
        if idx_noon < len(wd_arr) and wd_arr[idx_noon] is not None:
            wind_dir = float(wd_arr[idx_noon])
    except Exception:
        wind_dir = None
    try:
        if idx_noon < len(pr_arr) and pr_arr[idx_noon] is not None:
            press = int(round(float(pr_arr[idx_noon])))
    except Exception:
        press = None

    trend = ""
    try:
        j = max(0, idx_noon - 3)
        if j < len(pr_arr) and pr_arr[j] is not None and pr_arr[idx_noon] is not None:
            d = float(pr_arr[idx_noon]) - float(pr_arr[j])
            if d > 1.5:
                trend = "↗︎"
            elif d < -1.5:
                trend = "↘︎"
            else:
                trend = "→"
    except Exception:
        trend = ""

    return wind_ms, wind_dir, press, trend


def pick_tomorrow_header_metrics(wm: Dict[str, Any], tz_obj: pendulum.Timezone) -> Tuple[Optional[float], Optional[float], Optional[int], str]:
    return pick_header_metrics_for_offset(wm, tz_obj, 1)


# ────────────────────────── Weather codes ──────────────────────────
WEATHER_CODE_DESC_RU: Dict[int, str] = {
    0: "ясно",
    1: "в основном ясно",
    2: "переменная облачность",
    3: "пасмурно",
    45: "туман",
    48: "изморозь/туман",
    51: "морось",
    53: "морось",
    55: "сильная морось",
    56: "ледяная морось",
    57: "ледяная морось",
    61: "дождь",
    63: "дождь",
    65: "сильный дождь",
    66: "ледяной дождь",
    67: "ледяной дождь",
    71: "снег",
    73: "снег",
    75: "сильный снег",
    77: "снежные зёрна",
    80: "ливни",
    81: "ливни",
    82: "сильные ливни",
    85: "снегопад",
    86: "сильный снегопад",
    95: "гроза",
    96: "гроза с градом",
    99: "гроза с сильным градом",
}

WEATHER_CODE_EMOJI: Dict[int, str] = {
    0: "☀️",
    1: "🌤️",
    2: "⛅️",
    3: "☁️",
    45: "🌫️",
    48: "🌫️",
    51: "🌦️",
    53: "🌦️",
    55: "🌧️",
    61: "🌧️",
    63: "🌧️",
    65: "🌧️",
    71: "🌨️",
    73: "🌨️",
    75: "❄️",
    80: "🌦️",
    81: "🌦️",
    82: "🌧️",
    95: "⛈️",
    96: "⛈️",
    99: "⛈️",
}

def code_desc(wcode: Optional[int]) -> str:
    if isinstance(wcode, int):
        return WEATHER_CODE_DESC_RU.get(wcode, f"код {wcode}")
    return "н/д"

def code_emoji(wcode: Optional[int]) -> str:
    if isinstance(wcode, int):
        return WEATHER_CODE_EMOJI.get(wcode, "🌡️")
    return "🌡️"

# ────────────────────────── AQI mapping ──────────────────────────
def aqi_risk_ru(aqi: Any) -> str:
    try:
        v = float(aqi)
    except Exception:
        return "н/д"
    if v <= 50:
        return "низкий"
    if v <= 100:
        return "умеренный"
    if v <= 150:
        return "высокий"
    return "очень высокий"

# ────────────────────────── Tips ──────────────────────────
_TIPS: Dict[str, List[str]] = {
    "здоровый день": [
        "Планируйте день мягко и оставьте 30 минут на прогулку.",
        "Пейте воду равномерно и делайте короткие паузы для дыхания.",
        "Если есть возможность — короткая растяжка или йога вечером.",
    ],
    "плохой воздух": [
        "Интенсивные тренировки лучше перенести в помещение.",
        "Проветривание — по самочувствию, избегайте магистралей.",
        "Тёплый душ и больше воды помогут снизить раздражение слизистых.",
    ],
    "плохая погода": [
        "Закладывайте время на дорогу и держите планы гибкими.",
        "Слои одежды и капюшон — лучший выбор при порывистом ветре.",
        "Сделайте вечер спокойнее: тёплый напиток, меньше экранов.",
    ],
    "магнитные бури": [
        "Снизьте кофеин, добавьте воды и делайте паузы в течение дня.",
        "Старайтесь не перегружать график и беречь сон.",
        "Лёгкая прогулка и дыхание помогут стабилизировать тонус.",
    ],
}
def safe_tips(theme: str) -> List[str]:
    key = (theme or "").strip().lower()
    if key in _TIPS:
        return _TIPS[key][:3]
    return ["Берегите режим и планируйте день мягко."]

# ────────────────────────── Water activity thresholds ──────────────────────────
WSUIT_NONE = float(os.getenv("WSUIT_NONE", "22"))
WSUIT_SHORTY = float(os.getenv("WSUIT_SHORTY", "20"))
WSUIT_32 = float(os.getenv("WSUIT_32", "17"))
WSUIT_43 = float(os.getenv("WSUIT_43", "14"))
WSUIT_54 = float(os.getenv("WSUIT_54", "12"))
WSUIT_65 = float(os.getenv("WSUIT_65", "10"))

def wetsuit_hint_by_sst(sst: Optional[float]) -> Optional[str]:
    if not isinstance(sst, (int, float)):
        return None
    t = float(sst)
    if t >= WSUIT_NONE:
        return None
    if t >= WSUIT_SHORTY:
        return "гидрокостюм шорти 2 мм"
    if t >= WSUIT_32:
        return "гидрокостюм 3/2 мм"
    if t >= WSUIT_43:
        return "гидрокостюм 4/3 мм (боты)"
    if t >= WSUIT_54:
        return "гидрокостюм 5/4 мм (боты, перчатки)"
    if t >= WSUIT_65:
        return "гидрокостюм 5/4 мм + капюшон (боты, перчатки)"
    return "гидрокостюм 6/5 мм + капюшон (боты, перчатки)"

# ────────────────────────── Schumann fallback (kept) ──────────────────────────
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text("utf-8"))
    except Exception:
        return None

def _schu_freq_status(freq: Optional[float]) -> tuple[str, str]:
    if not isinstance(freq, (int, float)):
        return "🟡 колебания", "yellow"
    f = float(freq)
    if 7.4 <= f <= 8.4:
        return ("🟢 в норме", "green") if (7.7 <= f <= 8.1) else ("🟡 колебания", "yellow")
    return "🔴 сильное отклонение", "red"

def get_schumann_with_fallback() -> Dict[str, Any]:
    if DISABLE_SCHUMANN:
        return {"freq": None, "status": "⚪ отключено", "status_code": "grey"}
    try:
        import schumann  # type: ignore
        if hasattr(schumann, "get_schumann"):
            payload = schumann.get_schumann() or {}
            return {
                "freq": payload.get("freq"),
                "status": payload.get("status") or _schu_freq_status(payload.get("freq"))[0],
                "status_code": payload.get("status_code") or _schu_freq_status(payload.get("freq"))[1],
            }
    except Exception:
        pass
    here = Path(__file__).parent
    js = _read_json(here / "data" / "schumann_hourly.json") or {}
    st, code = _schu_freq_status(js.get("freq"))
    return {"freq": js.get("freq"), "status": st, "status_code": code}

def schumann_line(s: Dict[str, Any]) -> Optional[str]:
    if (s or {}).get("status_code") == "green":
        return None
    f = s.get("freq")
    fstr = f"{f:.2f} Гц" if isinstance(f, (int, float)) else "н/д"
    return f"{s.get('status', 'н/д')} • Шуман: {fstr}"

# ────────────────────────── Radiation helper ──────────────────────────
def _rad_risk(usvh: float) -> Tuple[str, str]:
    if usvh <= 0.15:
        return "🟢", "низкий"
    if usvh <= 0.30:
        return "🟡", "повышенный"
    return "🔴", "высокий"

def radiation_line(lat: float, lon: float) -> Optional[str]:
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if isinstance(dose, (int, float)):
        em, lbl = _rad_risk(float(dose))
        return f"{em} Радиация: {float(dose):.3f} μSv/h — {lbl}"
    return None

# ────────────────────────── UVI ──────────────────────────
def uvi_label(x: float) -> str:
    if x < 3:
        return "низкий"
    if x < 6:
        return "умеренный"
    if x < 8:
        return "высокий"
    if x < 11:
        return "очень высокий"
    return "экстремальный"

def uvi_for_offset(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> Dict[str, Any]:
    daily = wm.get("daily") or {}
    hourly = wm.get("hourly") or {}
    date_obj = _now_like_work_date(tz).add(days=int(offset_days)).date()

    uvi_now = None
    try:
        times = hourly.get("time") or []
        uvi_arr = hourly.get("uv_index") or hourly.get("uv_index_clear_sky") or []
        for t, v in zip(times, uvi_arr):
            if t and str(t).startswith(date_obj.to_date_string()) and isinstance(v, (int, float)):
                uvi_now = float(v)
                break
    except Exception:
        uvi_now = None

    uvi_max = None
    try:
        dts = _daily_times(wm)
        if dts and date_obj in dts:
            idx = dts.index(date_obj)
            arr = daily.get("uv_index_max") or []
            if idx < len(arr) and arr[idx] is not None:
                uvi_max = float(arr[idx])
    except Exception:
        pass

    if uvi_max is None:
        try:
            times = hourly.get("time") or []
            uvi_arr = hourly.get("uv_index") or hourly.get("uv_index_clear_sky") or []
            vals = []
            for t, v in zip(times, uvi_arr):
                if t and str(t).startswith(date_obj.to_date_string()) and isinstance(v, (int, float)):
                    vals.append(float(v))
            if vals:
                uvi_max = max(vals)
        except Exception:
            pass

    return {"uvi": uvi_now, "uvi_max": uvi_max}

# ────────────────────────── FX (morning) ──────────────────────────
def fx_morning_line(date_local: pendulum.DateTime, tz: pendulum.Timezone) -> Optional[str]:
    """
    ВАЖНО:
    - Для Кипра (таймзона Asia/Nicosia) FX-блок должен быть всегда выключен.
      Чтобы включить — выставь ENV: ALLOW_FX_NICOSIA=1
    """
    try:
        tz_name = tz.name if hasattr(tz, "name") else str(tz)
    except Exception:
        tz_name = str(tz)

    if tz_name in ("Asia/Nicosia", "Europe/Nicosia") and os.getenv("ALLOW_FX_NICOSIA", "").strip().lower() not in ("1", "true", "yes", "on"):
        return None

    try:
        import importlib
        fx = importlib.import_module("fx")
        rates = fx.get_rates(date=date_local, tz=tz) or {}  # type: ignore[attr-defined]
    except Exception as e:
        logging.info("FX morning: нет fx.get_rates: %s", e)
        return None

    def token(code: str, name: str) -> str:
        r = rates.get(code) or {}
        val = r.get("value")
        dlt = r.get("delta")
        try:
            vs = f"{float(val):.2f}"
        except Exception:
            vs = "н/д"
        return f"{name} {vs} {RUB} ({_fmt_delta(dlt)})"

    return "💱 Курсы (утро): " + " • ".join([token("USD", "USD"), token("EUR", "EUR"), token("CNY", "CNY")])

# ────────────────────────── Storm helpers ──────────────────────────
def _day_indices(wm: Dict[str, Any], tz: pendulum.Timezone, offset: int) -> List[int]:
    times = _hourly_times(wm)
    date_obj = _now_like_work_date(tz).add(days=int(offset)).date()
    idxs = []
    for i, dt_ in enumerate(times):
        try:
            if dt_.in_tz(tz).date() == date_obj:
                idxs.append(i)
        except Exception:
            pass
    return idxs

def _vals(arr: Any, idxs: List[int]) -> List[float]:
    out: List[float] = []
    arr = arr or []
    for i in idxs:
        if i < len(arr) and arr[i] is not None:
            try:
                out.append(float(arr[i]))
            except Exception:
                pass
    return out

def storm_short_text(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> str:
    hourly = wm.get("hourly") or {}
    idxs = _day_indices(wm, tz, offset_days)
    if not idxs:
        return "без шторма"
    gusts_kmh = _vals(hourly.get("wind_gusts_10m") or hourly.get("windgusts_10m") or [], idxs)
    rain = _vals(hourly.get("rain") or [], idxs)
    thp = _vals(hourly.get("thunderstorm_probability") or [], idxs)
    gmax = max(gusts_kmh, default=0) / 3.6
    if gmax >= STORM_GUST_MS or max(rain, default=0) >= ALERT_RAIN_MM_H or max(thp, default=0) >= ALERT_TSTORM_PROB_PC:
        return "шторм"
    return "без шторма"

def storm_alert_line(wm: Dict[str, Any], tz: pendulum.Timezone, offset_days: int) -> Optional[str]:
    hourly = wm.get("hourly") or {}
    idxs = _day_indices(wm, tz, offset_days)
    if not idxs:
        return None
    gust_kmh = _vals(hourly.get("wind_gusts_10m") or hourly.get("windgusts_10m") or [], idxs)
    rain = _vals(hourly.get("rain") or [], idxs)
    thp = _vals(hourly.get("thunderstorm_probability") or [], idxs)
    g_max = max(gust_kmh, default=0) / 3.6
    r_max = max(rain, default=0)
    t_max = max(thp, default=0)
    parts = []
    if g_max >= ALERT_GUST_MS:
        parts.append(f"порывы до {int(round(g_max))} м/с")
    if r_max >= ALERT_RAIN_MM_H:
        parts.append(f"дождь до {int(round(r_max))} мм/ч")
    if t_max >= ALERT_TSTORM_PROB_PC:
        parts.append(f"гроза до {int(round(t_max))}%")
    if parts:
        return "⚠️ Штормовое предупреждение: " + "; ".join(parts)
    return None

def _sunset_hhmm_for_offset(wm: Dict[str, Any], tz_obj: pendulum.Timezone, offset_days: int) -> Optional[str]:
    daily = wm.get("daily") or {}
    ss_arr = daily.get("sunset") or []
    dts = _daily_times(wm)
    target = _now_like_work_date(tz_obj).add(days=int(offset_days)).date()
    try:
        if dts and target in dts:
            idx = dts.index(target)
            if idx < len(ss_arr) and ss_arr[idx]:
                return pendulum.parse(ss_arr[idx]).in_tz(tz_obj).format("HH:mm")
    except Exception:
        pass
    try:
        if ss_arr and ss_arr[0]:
            return pendulum.parse(ss_arr[0]).in_tz(tz_obj).format("HH:mm")
    except Exception:
        pass
    return None

# ────────────────────────── Kp global — minimal fallback ──────────────────────────
def _kp_global_swpc() -> Tuple[Optional[float], str, Optional[int], str]:
    if _get_kp_repo is not None:
        try:
            payload = _get_kp_repo() or {}
            kp_val = payload.get("kp") if isinstance(payload, dict) else None
            status = payload.get("status") if isinstance(payload, dict) else None
            age_min = payload.get("age_min") if isinstance(payload, dict) else None
            src = payload.get("src") if isinstance(payload, dict) else None
            try:
                kp_val = float(kp_val) if kp_val is not None else None
            except Exception:
                kp_val = None
            return kp_val, str(status or "н/д"), (int(age_min) if isinstance(age_min, (int, float)) else None), str(src or "repo")
        except Exception:
            pass
    return None, "н/д", None, "n/a"

def _kp_age_human(age_min: Optional[int]) -> str:
    if not isinstance(age_min, int) or age_min < 0:
        return ""
    if age_min >= 90:
        return f"{age_min // 60} ч назад"
    return f"{age_min} мин назад"

# ────────────────────────── Cyprus morning message (NO FX) ──────────────────────────
def _air_circle_and_advice(air_risk: str) -> tuple[str, str]:
    r = (air_risk or "н/д").strip().lower()
    if r in ("низкий",):
        return "🟢", "можно гулять и тренироваться на улице"
    if r in ("умеренный",):
        return "🟡", "лучше без интенсивных нагрузок; проветривание по самочувствию"
    if r in ("высокий", "очень высокий"):
        return "🟠", "тренировки — в помещении; на улице короче и спокойнее"
    return "⚪", "ориентируйтесь на самочувствие"

def build_message_cyprus_morning(
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
) -> str:
    tz_obj = pendulum.timezone(tz) if isinstance(tz, str) else tz
    date_local = _now_like_work_date(tz_obj).start_of("day")
    date_str = date_local.format("DD.MM.YYYY")

    sea_pairs = _normalize_city_pairs(sea_cities)
    other_pairs = _normalize_city_pairs(other_cities)
    all_pairs = other_pairs + sea_pairs

    ref_city, (ref_lat, ref_lon) = _pick_ref_pair_for_region(sea_pairs, other_pairs, region_name)
    wm_ref = get_weather(ref_lat, ref_lon, tz_name=tz_obj.name) or {}

    warm_city = None
    cool_city = None
    warm = None
    cool = None
    for city, (la, lo) in all_pairs:
        tmax, _, _ = _fetch_temps_for_offset(la, lo, tz_obj.name, 0)
        if not isinstance(tmax, (int, float)):
            continue
        tv = float(tmax)
        if warm is None or tv > warm:
            warm = tv
            warm_city = city
        if cool is None or tv < cool:
            cool = tv
            cool_city = city

    if warm is None or cool is None:
        tmax_ref, _, _ = _fetch_temps_for_offset(ref_lat, ref_lon, tz_obj.name, 0)
        if isinstance(tmax_ref, (int, float)):
            warm = cool = float(tmax_ref)
            warm_city = warm_city or ref_city
            cool_city = cool_city or ref_city

    warm_i = int(round(warm)) if isinstance(warm, (int, float)) else None
    cool_i = int(round(cool)) if isinstance(cool, (int, float)) else None

    fact_text = _sanitize_line((get_fact(date_local, _cyprus_title(region_name)) or "").strip(), max_len=120)
    fact_part = f"{fact_text} " if fact_text else ""

    warm_city_disp = warm_city or ref_city or "—"
    cool_city_disp = cool_city or ref_city or "—"
    warm_part = f"{warm_city_disp} ({warm_i}°)" if warm_i is not None else f"{warm_city_disp} (н/д)"
    cool_part = f"{cool_city_disp} ({cool_i}°)" if cool_i is not None else f"{cool_city_disp} (н/д)"
    range_part = f"(диапазон {cool_i}–{warm_i}°)." if (warm_i is not None and cool_i is not None) else ""

    header = f"{_cyprus_title(region_name)}: погода на сегодня ({date_str})"
    greet = f"👋 Доброе утро! {fact_part}Теплее всего — {warm_part}, прохладнее — {cool_part} {range_part}".strip()
    greet = re.sub(r"\s{2,}", " ", greet).strip()

    storm_line = storm_alert_line(wm_ref, tz_obj, offset_days=0)
    if storm_line:
        storm_line = storm_line.rstrip(".") + " Берегите планы и закладывайте время."

    uvi_line = None
    try:
        uvi_info = uvi_for_offset(wm_ref, tz_obj, 0)
        uvi_val = None
        if isinstance(uvi_info.get("uvi_max"), (int, float)):
            uvi_val = float(uvi_info["uvi_max"])
        elif isinstance(uvi_info.get("uvi"), (int, float)):
            uvi_val = float(uvi_info["uvi"])
        if isinstance(uvi_val, (int, float)) and uvi_val >= 3:
            uvi_line = f"☀️ УФ: {uvi_val:.0f} — {uvi_label(uvi_val)} • SPF 30+ и головной убор"
    except Exception:
        uvi_line = None

    rad_line = radiation_line(ref_lat, ref_lon)

    sunset = _sunset_hhmm_for_offset(wm_ref, tz_obj, 0)
    sunset_line = f"🌇 Закат сегодня: {sunset}" if sunset else "🌇 Закат сегодня: н/д"

    air = get_air(ref_lat, ref_lon) or {}
    try:
        aqi_val = float(air.get("aqi")) if air.get("aqi") is not None else None
    except Exception:
        aqi_val = None
    aqi_i = int(round(aqi_val)) if isinstance(aqi_val, (int, float)) else None
    air_risk = aqi_risk_ru(aqi_val)
    circle, air_advice = _air_circle_and_advice(air_risk)

    pm25_int = _int_or_nd(air.get("pm25"))
    pm10_int = _int_or_nd(air.get("pm10"))

    pollen = get_pollen() or {}
    pollen_risk = str(pollen.get("risk")).strip() if pollen.get("risk") else ""
    aqi_txt = f"{aqi_i}" if aqi_i is not None else "н/д"
    air_metrics_line = f"🏭 AQI {aqi_txt} ({air_risk}) • PM₂.₅ {pm25_int} / PM₁₀ {pm10_int}"
    if pollen_risk:
        air_metrics_line += f" • 🌿 пыльца: {pollen_risk}"

    air_advice_line = f"ℹ️ {circle} воздух {air_risk} — {air_advice}"

    kp_val, kp_status, kp_age_min, kp_src = _kp_global_swpc()
    age_h = _kp_age_human(kp_age_min)
    kp_part = f"Kp {kp_val:.1f} ({kp_status}{', ' + age_h if age_h else ''})" if isinstance(kp_val, (int, float)) else "Kp н/д"

    sw = get_solar_wind() or {}
    v = sw.get("speed_kms")
    n = sw.get("density")
    vtxt = f"v {float(v):.0f} км/с" if isinstance(v, (int, float)) else None
    ntxt = f"n {float(n):.1f} см⁻³" if isinstance(n, (int, float)) else None
    sw_bits = ", ".join([x for x in (vtxt, ntxt) if x])
    sw_status = sw.get("status", "н/д")
    sw_part = f" • 🌬️ SW {sw_bits} — {sw_status}" if sw_bits else ""
    space_line = f"🧲 Космопогода: {kp_part}{sw_part}"

    storm_short = "шторм" if storm_line else "без шторма"
    kp_short = kp_status if isinstance(kp_val, (int, float)) else "н/д"
    itogo = f"🔎 Итого: воздух {circle} • {storm_short} • Kp {kp_short}"

    theme = "плохая погода" if storm_line else ("плохой воздух" if air_risk in ("высокий", "очень высокий") else "здоровый день")
    tip_one = (safe_tips(theme) or ["Берегите режим и планируйте день мягко."])[0]
    tip_one = _sanitize_line(str(tip_one), max_len=110)
    today_line = f"✅ Сегодня: {tip_one.rstrip('.')}".strip()

    tags = ["#Кипр", "#погода", "#здоровье"]
    if warm_city_disp:
        tags.append(_hashtag(warm_city_disp))
    if cool_city_disp and cool_city_disp != warm_city_disp:
        tags.append(_hashtag(cool_city_disp))
    tags = [t for t in tags if t]

    lines: List[str] = [header, greet]
    if storm_line:
        lines.append(storm_line)
    if uvi_line:
        lines.append(uvi_line)
    if rad_line:
        lines.append(rad_line)
    lines.append(sunset_line)
    lines.append(air_metrics_line)
    lines.append(air_advice_line)
    lines.append(space_line)
    lines.append(itogo)
    lines.append(today_line)
    lines.append(" ".join(tags[:6]))
    return "\n".join(lines)

# ────────────────────────── Morning compact (non-CY) ──────────────────────────
def build_message_morning_compact(
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
) -> str:
    tz_obj = pendulum.timezone(tz) if isinstance(tz, str) else tz
    date_local = _now_like_work_date(tz_obj).start_of("day")
    day_off = 0

    header = f"<b>🌅 {html.escape(region_name)}: погода на сегодня ({date_local.format('DD.MM.YYYY')})</b>"
    fact_text = get_fact(date_local, region_name)
    fact_text = fact_text.strip() if isinstance(fact_text, str) else ""
    fact_line = f"🌾 Доброе утро! {html.escape(fact_text)}" if fact_text else "🌾 Доброе утро!"

    sea_pairs = _normalize_city_pairs(sea_cities)
    other_pairs = _normalize_city_pairs(other_cities)
    ref_city, (ref_lat, ref_lon) = _pick_ref_pair_for_region(sea_pairs, other_pairs, region_name)

    wm_ref = get_weather(ref_lat, ref_lon, tz_name=tz_obj.name) or {}
    t_day, t_night, wcode = _fetch_temps_for_offset(ref_lat, ref_lon, tz_obj.name, day_off)
    wind_ms, wind_dir_deg, press_val, press_trend = pick_header_metrics_for_offset(wm_ref, tz_obj, day_off)

    desc = code_desc(wcode) or "—"
    tday_i = int(round(t_day)) if isinstance(t_day, (int, float)) else None
    tnight_i = int(round(t_night)) if isinstance(t_night, (int, float)) else None
    temp_txt = f"{tday_i}/{tnight_i}{NBSP}°C" if (tday_i is not None and tnight_i is not None) else "н/д"
    if isinstance(wind_ms, (int, float)) and wind_dir_deg is not None:
        wind_txt = f"💨 {wind_ms:.1f} м/с ({compass(wind_dir_deg)})"
    elif isinstance(wind_ms, (int, float)):
        wind_txt = f"💨 {wind_ms:.1f} м/с"
    else:
        wind_txt = "💨 н/д"
    press_txt = f"🔹 {press_val} гПа {press_trend}" if isinstance(press_val, int) else "🔹 н/д"
    main_line = f"Погода: 🏙️ {html.escape(ref_city)} — {temp_txt} • {html.escape(desc)} • {wind_txt} • {press_txt}."

    warm_city, warm_vals = None, None
    cold_city, cold_vals = None, None
    for city, (la, lo) in other_pairs:
        tmax, tmin, _ = _fetch_temps_for_offset(la, lo, tz_obj.name, day_off)
        if tmax is None:
            continue
        if warm_vals is None or tmax > warm_vals[0]:
            warm_city, warm_vals = city, (tmax, tmin or tmax)
        if cold_vals is None or tmax < cold_vals[0]:
            cold_city, cold_vals = city, (tmax, tmin or tmax)
    warm_txt = f"{warm_city} {int(round(warm_vals[0]))}/{int(round(warm_vals[1]))}{NBSP}°C" if warm_city and warm_vals else "н/д"
    cold_txt = f"{cold_city} {int(round(cold_vals[0]))}/{int(round(cold_vals[1]))}{NBSP}°C" if cold_city and cold_vals else "н/д"

    sst_hint = None
    for _, (la, lo) in sea_pairs:
        try:
            s = get_sst(la, lo)
            if isinstance(s, (int, float)):
                sst_hint = s
                break
        except Exception:
            pass
    suit = wetsuit_hint_by_sst(sst_hint)
    sea_txt = f"Море: {suit}." if suit else "Море: н/д."

    sunset = _sunset_hhmm_for_offset(wm_ref, tz_obj, day_off)
    sunset_line = f"🌇 Закат сегодня: {sunset}" if sunset else "🌇 Закат: н/д"

    fx_line = fx_morning_line(pendulum.now(tz_obj), tz_obj)

    air = get_air(ref_lat, ref_lon) or {}
    try:
        aqi = air.get("aqi")
        aqi_i = int(round(float(aqi))) if isinstance(aqi, (int, float)) else "н/д"
    except Exception:
        aqi_i = "н/д"

    pm25_int = _int_or_nd(air.get("pm25"))
    pm10_int = _int_or_nd(air.get("pm10"))
    pollen = get_pollen() or {}
    pollen_risk = str(pollen.get("risk")).strip() if pollen.get("risk") else ""

    air_risk = aqi_risk_ru(aqi)
    air_emoji_main = "🟠" if air_risk in ("высокий", "очень высокий") else ("🟡" if air_risk == "умеренный" else "🟢")

    air_line = f"🏭 Воздух: {air_emoji_main} {air_risk} (AQI {aqi_i}) • PM₂.₅ {pm25_int} / PM₁₀ {pm10_int}"
    if pollen_risk:
        air_line += f" • 🌿 пыльца: {pollen_risk}"

    uvi_info = uvi_for_offset(wm_ref, tz_obj, day_off)
    uvi_line = None
    try:
        uvi_val = None
        if isinstance(uvi_info.get("uvi_max"), (int, float)):
            uvi_val = float(uvi_info["uvi_max"])
        elif isinstance(uvi_info.get("uvi"), (int, float)):
            uvi_val = float(uvi_info["uvi"])
        if isinstance(uvi_val, (int, float)) and uvi_val >= 3:
            uvi_line = f"☀️ УФ: {uvi_val:.0f} — {uvi_label(uvi_val)} • SPF 30+ и головной убор"
    except Exception:
        pass

    kp_val, kp_status, kp_age_min, kp_src = _kp_global_swpc()
    age_txt = ""
    if isinstance(kp_age_min, int):
        age_txt = f", 🕓 {kp_age_min // 60}ч назад" if kp_age_min > 180 else f", 🕓 {kp_age_min} мин назад"
    kp_chunk = f"Kp {kp_val:.1f} ({kp_status}{age_txt})" if isinstance(kp_val, (int, float)) else "Kp н/д"

    sw = get_solar_wind() or {}
    v = sw.get("speed_kms")
    n = sw.get("density")
    vtxt = f"v {float(v):.0f} км/с" if isinstance(v, (int, float)) else None
    ntxt = f"n {float(n):.1f} см⁻³" if isinstance(n, (int, float)) else None
    parts = [p for p in (vtxt, ntxt) if p]
    sw_chunk = (" • 🌬️ SW " + ", ".join(parts) + f" — {sw.get('status', 'н/д')}") if parts else ""
    space_line = "🧲 Космопогода: " + kp_chunk + (sw_chunk or "")

    storm_line_alert = storm_alert_line(wm_ref, tz_obj, offset_days=day_off)
    official_rad = radiation_line(ref_lat, ref_lon)
    schu_line = schumann_line(get_schumann_with_fallback()) if SHOW_SCHUMANN else None

    storm_short = storm_short_text(wm_ref, tz_obj, offset_days=day_off)
    kp_short = kp_status if isinstance(kp_val, (int, float)) else "н/д"
    itogo = f"🔎 Итого: воздух {air_emoji_main} • {storm_short} • Kp {kp_short}"

    theme = (
        "магнитные бури"
        if (isinstance(kp_val, (int, float)) and kp_val >= 5)
        else ("плохой воздух" if air_risk in ("высокий", "очень высокий") else "здоровый день")
    )
    today_line = "✅ Сегодня: " + "; ".join(safe_tips(theme)) + "."

    P: List[str] = [
        header,
        fact_line,
        main_line,
        f"Погреться: {warm_txt}; остыть: {cold_txt}. {sea_txt}",
        "",
        sunset_line,
        "———",
    ]
    if fx_line:
        P.append(fx_line)
    P.append("———")
    P.append(air_line)
    if uvi_line:
        P.append(uvi_line)
    if SHOW_SPACE:
        P.append(space_line)
    if storm_line_alert:
        P.append(storm_line_alert)
    if official_rad:
        P.append(official_rad)
    if schu_line:
        P.append(schu_line)
    P.append("")
    P.append(itogo)
    P.append(today_line)
    P.append("")
    tag_region = "#" + re.sub(r"[^0-9A-Za-zА-Яа-я]+", "", region_name).lower()
    tags = [tag_region, "#погода", "#здоровье", "#сегодня"]
    for city in (warm_city, cold_city):
        if not city:
            continue
        tag_city = "#" + re.sub(r"[^0-9A-Za-zА-Яа-я]+", "", str(city)).lower()
        if tag_city and tag_city not in tags:
            tags.append(tag_city)
    P.append(" ".join(tags[:6]))
    return "\n".join(P)

# ────────────────────────── Astro section (minimal, with cache) ──────────────────────────
_ZODIAC_SYMBOLS = {
    "aries": "♈︎", "овен": "♈︎",
    "taurus": "♉︎", "телец": "♉︎",
    "gemini": "♊︎", "близнецы": "♊︎",
    "cancer": "♋︎", "рак": "♋︎",
    "leo": "♌︎", "лев": "♌︎",
    "virgo": "♍︎", "дева": "♍︎",
    "libra": "♎︎", "весы": "♎︎",
    "scorpio": "♏︎", "скорпион": "♏︎",
    "sagittarius": "♐︎", "стрелец": "♐︎",
    "capricorn": "♑︎", "козерог": "♑︎",
    "aquarius": "♒︎", "водолей": "♒︎",
    "pisces": "♓︎", "рыбы": "♓︎",
}

def zsym(text: str) -> str:
    if not text:
        return ""
    out = text
    for k, v in _ZODIAC_SYMBOLS.items():
        out = re.sub(rf"\b{k}\b", v, out, flags=re.IGNORECASE)
    return out

def load_calendar() -> Dict[str, Any]:
    here = Path(__file__).parent
    candidates = [
        here / "data" / "lunar_calendar.json",
        here / "lunar_calendar.json",
        Path("lunar_calendar.json"),
    ]
    for p in candidates:
        cal = _read_json(p)
        if isinstance(cal, dict) and cal:
            return cal
    return {}

def voc_interval_for_date(rec: Dict[str, Any], tz_local: str = "Asia/Nicosia") -> Optional[Tuple[pendulum.DateTime, pendulum.DateTime]]:
    a = rec.get("voc_start") or rec.get("voc_from") or rec.get("vocStart") or rec.get("void_start")
    b = rec.get("voc_end") or rec.get("voc_to") or rec.get("vocEnd") or rec.get("void_end")
    if not a or not b:
        return None
    try:
        tz = pendulum.timezone(tz_local)
    except Exception:
        tz = pendulum.timezone("UTC")
    try:
        t1 = pendulum.parse(str(a)).in_tz(tz)
        t2 = pendulum.parse(str(b)).in_tz(tz)
        return t1, t2
    except Exception:
        return None

def _astro_markers_from_rec(rec: Dict[str, Any]) -> List[str]:
    items: List[str] = []
    for k in ("events", "markers", "highlights"):
        v = rec.get(k)
        if isinstance(v, list):
            items.extend([str(x).strip() for x in v if str(x).strip()])
    # flags
    if rec.get("eclipse"):
        items.append("затмение")
    if rec.get("retrograde"):
        items.append("ретроград")
    if rec.get("full_moon") or (str(rec.get("phase") or "").lower() in ("full", "full moon", "полнолуние")):
        items.append("полнолуние")
    if rec.get("new_moon") or (str(rec.get("phase") or "").lower() in ("new", "new moon", "новолуние")):
        items.append("новолуние")
    seen = set()
    out: List[str] = []
    for x in items:
        lx = x.lower()
        if lx in seen:
            continue
        seen.add(lx)
        out.append(x)
    return out[:5]

def _astro_llm_bullets(date_key: str, phase_name: str, percent: Optional[int], sign: Optional[str], voc_text: Optional[str]) -> List[str]:
    cache_path = CACHE_DIR / f"astro_{date_key}.txt"
    if cache_path.exists():
        try:
            txt = cache_path.read_text("utf-8").strip()
            lines = [x.strip() for x in txt.split("\n") if x.strip()]
            bullets = [x if x.startswith("•") else "• " + x for x in lines][:3]
            if bullets:
                return bullets
        except Exception:
            pass

    bullets: List[str] = []
    if USE_DAILY_LLM and gpt_complete is not None:
        try:
            prompt = (
                "Сгенерируй 3 коротких пункта (каждый до 90 символов) для блока астрособытий дня. "
                f"Дата: {date_key}. Фаза: {phase_name or 'н/д'}{f' ({percent}%)' if percent else ''}. "
                f"Знак: {sign or 'н/д'}. VoC: {voc_text or 'нет/н/д'}. "
                "Пиши по-русски, нейтрально, без эзотерических утверждений, больше про настроение и планирование."
            )
            txt = str(gpt_complete(prompt, temperature=ASTRO_LLM_TEMP) or "").strip()
            lines = [x.strip("• ").strip() for x in txt.split("\n") if x.strip()]
            bullets = ["• " + _sanitize_line(x, 110) for x in lines][:3]
        except Exception:
            bullets = []

    if not bullets:
        base = []
        if voc_text:
            base.append("• Во время VoC лучше избегать важных стартов и подписаний.")
        if phase_name:
            base.append(f"• {phase_name}: день для спокойных решений и умеренного темпа.")
        if sign:
            base.append(f"• Фокус дня: {sign} — выбирайте простые и понятные задачи.")
        base.append("• Берегите энергию: меньше спешки, больше структуры и воды.")
        bullets = base[:3]

    try:
        cache_path.write_text("\n".join(bullets), "utf-8")
    except Exception:
        pass
    return bullets

def build_astro_section(astro_date=None, tz_obj=None, *, date_local=None, tz_local: str = "Asia/Nicosia") -> str:
    if date_local is None and astro_date is not None:
        date_local = astro_date
    if tz_obj is not None and getattr(tz_obj, "name", None) and (not tz_local or tz_local == "Asia/Nicosia"):
        tz_local = tz_obj.name

    try:
        tz = pendulum.timezone(tz_local)
    except Exception:
        tz = pendulum.timezone("UTC")

    if date_local is None:
        date_local = _now_like_work_date(tz).start_of("day")
    else:
        try:
            if hasattr(date_local, "in_tz"):
                date_local = date_local.in_tz(tz)
        except Exception:
            pass

    date_key = date_local.format("YYYY-MM-DD")
    cal = load_calendar()
    rec = cal.get(date_key, {}) if isinstance(cal, dict) else {}

    phase_name = str(rec.get("phase") or rec.get("phase_name") or "").strip()
    try:
        percent = int(rec.get("illumination") or rec.get("illumination_percent") or rec.get("percent") or 0) or None
    except Exception:
        percent = None
    sign = str(rec.get("sign") or rec.get("zodiac") or rec.get("moon_sign") or "").strip() or None

    voc_interval = voc_interval_for_date(rec, tz_local=tz_local)
    voc_text: Optional[str] = None
    if voc_interval:
        try:
            t1, t2 = voc_interval
            voc_text = f"{t1.format('HH:mm')}–{t2.format('HH:mm')}"
        except Exception:
            voc_text = None

    marker_items = _astro_markers_from_rec(rec)
    marker_line = "✅ " + " • ".join(marker_items) if marker_items else ""

    moon_bits = []
    if phase_name:
        moon_bits.append(f"🌙 {phase_name}{(' ('+str(percent)+'%)') if isinstance(percent,int) and percent else ''}")
    elif isinstance(percent, int) and percent:
        moon_bits.append(f"🌙 Освещённость: {percent}%")
    if sign:
        moon_bits.append(zsym(sign))
    moon_line = " • ".join(moon_bits).strip()

    bullets = _astro_llm_bullets(date_key, phase_name, percent, sign, voc_text)

    lines = ["📻 <b>Астрособытия</b>"]
    if marker_line:
        lines.append(zsym(marker_line))
    if moon_line:
        lines.append(zsym(moon_line))
    for b in bullets[:3]:
        b = str(b).strip()
        if not b:
            continue
        if not b.startswith(("•", "✨", "🌙", "⚡️")):
            b = "• " + b
        lines.append(zsym(b))
    if voc_text:
        lines.append(f"⚫️ VoC: {voc_text}")
    return "\n".join(lines)

# ────────────────────────── Legacy evening message (simple but stable) ──────────────────────────
def build_message_legacy_evening(
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
) -> str:
    tz_obj = pendulum.timezone(tz) if isinstance(tz, str) else tz
    off = int(os.getenv("DAY_OFFSET", "1"))
    date_local = _now_like_work_date(tz_obj).add(days=off).start_of("day")
    date_str = date_local.format("DD.MM.YYYY")

    sea_pairs = _normalize_city_pairs(sea_cities)
    other_pairs = _normalize_city_pairs(other_cities)
    ref_city, (ref_lat, ref_lon) = _pick_ref_pair_for_region(sea_pairs, other_pairs, region_name)
    wm_ref = get_weather(ref_lat, ref_lon, tz_name=tz_obj.name) or {}

    header = f"<b>🌆 {html.escape(region_name)}: прогноз на {date_str}</b>"
    storm_line = storm_alert_line(wm_ref, tz_obj, offset_days=off)
    sunset = _sunset_hhmm_for_offset(wm_ref, tz_obj, off)
    sunset_line = f"🌇 Закат: {sunset}" if sunset else ""

    def city_line(city: str, la: float, lo: float) -> str:
        tmax, tmin, wcode = _fetch_temps_for_offset(la, lo, tz_obj.name, off)
        tmax_i = int(round(tmax)) if isinstance(tmax, (int, float)) else None
        tmin_i = int(round(tmin)) if isinstance(tmin, (int, float)) else None
        temp = f"{tmax_i}/{tmin_i}°" if (tmax_i is not None and tmin_i is not None) else "н/д"
        return f"• {html.escape(city)}: {temp} {code_emoji(wcode)}"

    lines: List[str] = [header]
    if storm_line:
        lines.append(html.escape(storm_line))
    if sunset_line:
        lines.append(html.escape(sunset_line))

    if sea_pairs:
        lines.append(f"<b>{html.escape(sea_label or 'Морские города')}</b>")
        for city, (la, lo) in sea_pairs:
            lines.append(city_line(city, la, lo))

    if other_pairs:
        lines.append(f"<b>{html.escape(other_label or 'Другие города')}</b>")
        for city, (la, lo) in other_pairs:
            lines.append(city_line(city, la, lo))

    if SHOW_AIR:
        air = get_air(ref_lat, ref_lon) or {}
        aqi = air.get("aqi")
        aqi_txt = _int_or_nd(aqi)
        air_risk = aqi_risk_ru(aqi)
        pm25_int = _int_or_nd(air.get("pm25"))
        pm10_int = _int_or_nd(air.get("pm10"))
        pollen = get_pollen() or {}
        pollen_risk = str(pollen.get("risk")).strip() if pollen.get("risk") else ""
        air_line = f"🏭 AQI {aqi_txt} ({air_risk}) • PM₂.₅ {pm25_int} / PM₁₀ {pm10_int}"
        if pollen_risk:
            air_line += f" • 🌿 пыльца: {pollen_risk}"
        lines.append(html.escape(air_line))

    try:
        lines.append(build_astro_section(date_local=date_local, tz_obj=tz_obj, tz_local=tz_obj.name))
    except Exception:
        pass

    storm_short = storm_short_text(wm_ref, tz_obj, offset_days=off)
    lines.append(f"🔎 Итого: {html.escape(storm_short)} • планы держите гибкими.")
    return "\n".join(lines)

# ────────────────────────── Image style rotation ──────────────────────────
CY_STYLE_PRESETS_EN: list[str] = [
    "Visual style preset 1/5: cinematic Mediterranean photography, natural light, realistic textures, soft depth of field, no text.",
    "Visual style preset 2/5: watercolor illustration, airy washes, delicate brush texture, soft pastel palette, no text.",
    "Visual style preset 3/5: minimal vector poster, clean shapes, bold composition, limited palette, no text.",
    "Visual style preset 4/5: vintage travel postcard, subtle grain, warm tones, slightly faded ink, no text.",
    "Visual style preset 5/5: 3D clay / stop-motion look, cute tactile materials, soft studio light, no text.",
]
KLD_STYLE_PRESETS_EN: list[str] = [
    "Visual style preset 1/5: cinematic Baltic coast photography, dramatic clouds, realistic textures, no text.",
    "Visual style preset 2/5: watercolor northern seascape, cool palette, airy brushwork, no text.",
    "Visual style preset 3/5: minimal vector poster, Baltic shoreline silhouettes, limited palette, no text.",
    "Visual style preset 4/5: vintage travel postcard, Baltic seaside, subtle film grain, no text.",
    "Visual style preset 5/5: 3D clay / stop-motion look, cozy northern evening, soft light, no text.",
]

def _seeded_rng_for_date(date_obj: pendulum.Date, salt: int = 0) -> random.Random:
    s = date_obj.to_date_string()
    base = int(hashlib.sha1(f"{s}:{salt}".encode("utf-8")).hexdigest()[:8], 16)
    return random.Random(base)

def _pick_style_id(
    *,
    date_for_image: pendulum.Date,
    region_key: str,
    effective_mode: str,
    n_styles: int = 5,
) -> int:
    rotation = (os.getenv("IMG_STYLE_ROTATION", "date") or "date").strip().lower()
    seed_offset_env = os.getenv("CY_STYLE_SEED_OFFSET")
    if seed_offset_env is None:
        seed_offset_env = os.getenv("CY_MORNING_STYLE_SEED_OFFSET", "0")
    try:
        seed_offset = int(seed_offset_env or "0")
    except Exception:
        seed_offset = 0

    salt = f"{date_for_image.to_date_string()}:{region_key}:{effective_mode}"
    if rotation in ("run", "pipeline", "test"):
        run_token = (
            os.getenv("GITHUB_RUN_NUMBER")
            or os.getenv("GITHUB_RUN_ID")
            or os.getenv("GITHUB_SHA")
            or pendulum.now("UTC").format("YYYYMMDDHHmm")
        )
        salt = f"{salt}:{run_token}"

    base = int(hashlib.md5(salt.encode("utf-8")).hexdigest()[:8], 16)
    return int((base + seed_offset) % int(n_styles))

def _build_kld_image_moods_for_evening(
    tz_obj: pendulum.Timezone,
    sea_pairs: CityPairs,
    other_pairs: CityPairs,
    date_for_image: Optional[pendulum.Date] = None,
) -> tuple[str, str, str]:
    if date_for_image is None:
        date_for_image = _now_like_work_date(tz_obj).add(days=1).date()
    rng = _seeded_rng_for_date(date_for_image, salt=771)

    la_sea, lo_sea = _pick_ref_coords(sea_pairs, (KLD_LAT_DEFAULT, KLD_LON_DEFAULT))
    la_inland, lo_inland = _pick_ref_coords(other_pairs, (KLD_LAT_DEFAULT, KLD_LON_DEFAULT))

    marine_mood = "cool Baltic seaside evening with long sandy beaches and fresh wind from the sea"
    inland_mood = "quieter inland forests, lakes and the city of Kaliningrad with grounded, slower energy"

    try:
        stats_sea = day_night_stats(la_sea, lo_sea, tz=tz_obj.name, offset_days=1) or {}
    except Exception:
        stats_sea = {}
    try:
        stats_inland = day_night_stats(la_inland, lo_inland, tz=tz_obj.name, offset_days=1) or {}
    except Exception:
        stats_inland = {}

    tmax_sea = stats_sea.get("t_day_max")
    tmin_inland = stats_inland.get("t_night_min")
    tmax_inland = stats_inland.get("t_day_max")

    try:
        wm_sea = get_weather(la_sea, lo_sea, tz_name=tz_obj.name) or {}
    except Exception:
        wm_sea = {}

    stormy = bool(storm_alert_line(wm_sea, tz_obj, offset_days=1))

    if stormy:
        marine_variants = [
            "stormy Baltic evening with strong onshore wind, high waves and dramatic clouds over the sea",
            "very windy Baltic coastline, restless waves, blowing sand and low heavy clouds above the water",
            "rough Baltic sea with powerful gusts, whitecaps and wild sky — more for watching from shelter than walking on the pier",
        ]
    else:
        if isinstance(tmax_sea, (int, float)) and tmax_sea >= 22:
            marine_variants = [
                "rarely warm Baltic seaside evening with almost summer air, gentle waves and long golden light over the horizon",
                "unusually warm Baltic evening, people stay outside longer, the sea looks softer and friendlier than usual",
            ]
        elif isinstance(tmax_sea, (int, float)) and tmax_sea >= 17:
            marine_variants = [
                "mild Baltic evening with noticeable but pleasant wind, fresh air and soft, steady waves along the long beaches",
                "cool-but-comfortable seaside evening, good for a long walk along the promenade with a hood or light jacket",
            ]
        elif isinstance(tmax_sea, (int, float)) and tmax_sea >= 10:
            marine_variants = [
                "cool Baltic shoreline with brisk wind, choppy waves and a feeling of early autumn even if the calendar says otherwise",
                "fresh, slightly harsh seaside evening — good for a short walk and hot tea afterwards",
            ]
        else:
            marine_variants = [
                "cold Baltic evening with dark restless water, strong wind and air that bites your cheeks — better with a scarf and hood",
                "very chilly Baltic coastline, almost winter-like mood: rough sea, cold wind and a desire to warm hands on a mug of tea indoors",
            ]
    marine_mood = rng.choice(marine_variants)

    if isinstance(tmin_inland, (int, float)) and tmin_inland <= -5:
        inland_variants = [
            "frosty inland night with crunchy snow, very clear air and glowing windows in quiet streets of Kaliningrad and small towns",
            "freezing cold evening inland, still air, frost on branches and bright moonlight over hidden lakes and forests",
        ]
    elif isinstance(tmin_inland, (int, float)) and tmin_inland <= 0:
        inland_variants = [
            "cold inland evening around zero with damp air, bare branches and glistening roads, the city lights reflecting in wet asphalt",
            "chilly, slightly wet inland mood, more about quick walks and then hot tea at home",
        ]
    elif isinstance(tmax_inland, (int, float)) and tmax_inland >= 20:
        inland_variants = [
            "warm inland evening with soft air, slow walks along rivers and lakes and a relaxed city rhythm",
            "rare warm night in Kaliningrad: open windows, slow conversations and air that still keeps some heat from the day",
        ]
    else:
        inland_variants = [
            "typical mixed northern inland evening: cool but calmer than the sea, more about forests, courtyards and quiet streets",
            "balanced inland mood with fresher air than in summer, softer wind than at the coast and a slower, grounded rhythm",
        ]
    inland_mood = rng.choice(inland_variants)
    astro_mood_en = "calm, grounded northern sky energy supporting rest and reflection" if not stormy else "restless sky mood that favours flexibility and gentle self-care"
    return marine_mood, inland_mood, astro_mood_en

def _build_cy_image_moods_for_date(
    tz_obj: pendulum.Timezone,
    sea_pairs: CityPairs,
    other_pairs: CityPairs,
    region_name: str,
    date_for_image: pendulum.Date,
) -> tuple[str, str, str]:
    rng = _seeded_rng_for_date(date_for_image, salt=331)

    la_sea, lo_sea = _pick_ref_coords(sea_pairs, (CY_LAT_DEFAULT, CY_LON_DEFAULT))
    la_inland, lo_inland = _pick_ref_coords(other_pairs, (CY_LAT_DEFAULT, CY_LON_DEFAULT))

    try:
        base = _now_like_work_date(tz_obj).date()
        off = int((date_for_image - base).days)
    except Exception:
        off = 0

    wm_ref = get_weather(la_inland, lo_inland, tz_name=tz_obj.name) or {}
    stormy = bool(storm_alert_line(wm_ref, tz_obj, offset_days=off))

    tmax_sea, _, _ = _fetch_temps_for_offset(la_sea, lo_sea, tz_obj.name, off)
    tmax_inland, _, _ = _fetch_temps_for_offset(la_inland, lo_inland, tz_obj.name, off)

    if stormy:
        marine_variants = [
            "dramatic Mediterranean coast of Cyprus with strong wind, restless sea surface and fast-moving clouds",
            "stormy Cyprus shoreline, choppy waves and dark textured sky over the water — cinematic and intense",
        ]
    else:
        if isinstance(tmax_sea, (int, float)) and tmax_sea >= 22:
            marine_variants = [
                "warm Cyprus seaside with gentle waves, turquoise water highlights and a soft golden glow",
                "late-afternoon Mediterranean calm: warm air, smooth sea and bright, clean horizon",
            ]
        elif isinstance(tmax_sea, (int, float)) and tmax_sea >= 16:
            marine_variants = [
                "mild Cyprus coastal mood with fresh sea breeze and clear sky, inviting for a long walk by the water",
                "balanced Mediterranean coastline: pleasant air, light wind, soft reflections on the sea",
            ]
        else:
            marine_variants = [
                "cooler Cyprus coastline with crisp air, deeper blue tones and a quiet, reflective sea mood",
                "fresh winter-sun Mediterranean shore: cooler air, clear visibility, calm but cooler atmosphere",
            ]
    marine_mood = rng.choice(marine_variants)

    if isinstance(tmax_inland, (int, float)) and tmax_inland >= 22:
        inland_variants = [
            "warm inland Cyprus vibe: sunlit streets, relaxed pace, soft shadows and dry, clean air",
            "bright inland Mediterranean day with warm tones, gentle light and calm energy",
        ]
    elif isinstance(tmax_inland, (int, float)) and tmax_inland >= 15:
        inland_variants = [
            "mild inland Cyprus: comfortable air, soft light and a steady, unhurried rhythm",
            "pleasant inland mood with fresh breeze, olive trees and clear sky",
        ]
    else:
        inland_variants = [
            "cooler inland Cyprus mood: crisp air, quiet streets and a calm, grounded feeling",
            "fresh winter inland Cyprus: clear light, cooler tones, calm and minimal atmosphere",
        ]
    inland_mood = rng.choice(inland_variants)
    astro_mood_en = "gentle eastern Mediterranean sky energy: calm and restorative" if not stormy else "dynamic sky mood: stay flexible and protect your energy"
    return marine_mood, inland_mood, astro_mood_en

def _as_tz(tz: Union[pendulum.Timezone, str]) -> pendulum.Timezone:
    if isinstance(tz, pendulum.Timezone):
        return tz
    try:
        return pendulum.timezone(str(tz))
    except Exception:
        return pendulum.timezone("Asia/Nicosia")

def _should_regen_image(path: Path) -> bool:
    force = str(os.getenv("IMG_REGEN", "")).strip().lower() in ("1", "true", "yes", "on")
    if force:
        return True
    if not path.exists():
        return True
    try:
        return path.stat().st_size < 5_000
    except Exception:
        return True

# ────────────────────────── Public build_message ──────────────────────────
def build_message(
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
    mode: Optional[str] = None,
) -> str:
    effective_mode = (mode or os.getenv("POST_MODE") or "evening").strip().lower()

    if effective_mode.startswith("morning") and _is_cyprus_region(region_name):
        return build_message_cyprus_morning(region_name, sea_label, sea_cities, other_label, other_cities, tz)

    if effective_mode.startswith("morning"):
        return build_message_morning_compact(region_name, sea_label, sea_cities, other_label, other_cities, tz)

    return build_message_legacy_evening(region_name, sea_label, sea_cities, other_label, other_cities, tz)

# ────────────────────────── send_common_post ──────────────────────────
async def send_common_post(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz,
    mode: Optional[str] = None,
) -> None:
    msg = build_message(
        region_name=region_name,
        sea_label=sea_label,
        sea_cities=sea_cities,
        other_label=other_label,
        other_cities=other_cities,
        tz=tz,
        mode=mode,
    )

    try:
        effective_mode = (mode or os.getenv("POST_MODE") or os.getenv("MODE") or "evening").lower()
    except Exception:
        effective_mode = "evening"

    cy_img_env = os.getenv("CY_IMG_ENABLED")
    img_env = cy_img_env if cy_img_env is not None else os.getenv("IMG_ENABLED")
    kld_img_env = os.getenv("KLD_IMG_ENABLED")
    if img_env is None and kld_img_env is not None:
        img_env = kld_img_env
    if img_env is None:
        img_env = "1"

    enable_img = str(img_env).strip().lower() not in ("0", "false", "no", "off")

    tz_name = tz if isinstance(tz, str) else getattr(tz, "name", "")
    is_cyprus = _is_cyprus_region(region_name) or tz_name in ("Asia/Nicosia", "Europe/Nicosia")
    log_prefix = "CY_IMG" if is_cyprus else "KLD_IMG"

    logging.info(
        "%s: send_common_post called, mode=%s, tz=%s, CY_IMG_ENABLED=%s, IMG_ENABLED=%s, KLD_IMG_ENABLED=%s -> enable_img=%s",
        log_prefix,
        effective_mode,
        tz_name or "obj",
        os.getenv("CY_IMG_ENABLED"),
        os.getenv("IMG_ENABLED"),
        os.getenv("KLD_IMG_ENABLED"),
        enable_img,
    )

    img_path: Optional[str] = None

    prompt_builder = None
    prompt_kind: Optional[str] = None

    if is_cyprus:
        if effective_mode.startswith("morning") and build_cyprus_morning_prompt is not None and MorningMetrics is not None:  # type: ignore[name-defined]
            prompt_builder = build_cyprus_morning_prompt  # type: ignore[name-defined]
            prompt_kind = "cy_morning"
        elif build_cyprus_evening_prompt is not None:  # type: ignore[name-defined]
            prompt_builder = build_cyprus_evening_prompt  # type: ignore[name-defined]
            prompt_kind = "cy_evening"
    else:
        prompt_builder = build_kld_evening_prompt
        prompt_kind = "kld_evening"
        if effective_mode.startswith("morning") and build_kld_morning_prompt is not None:  # type: ignore[name-defined]
            prompt_builder = build_kld_morning_prompt  # type: ignore[name-defined]
            prompt_kind = "kld_morning"

    if enable_img and generate_astro_image is not None and prompt_builder is not None and effective_mode.startswith(("evening", "morning")):
        try:
            tz_obj = _as_tz(tz)
            default_off = 0 if effective_mode.startswith("morning") else 1
            off_days = int(os.getenv("DAY_OFFSET", str(default_off)))

            base_dt = _now_like_work_date(tz_obj)
            date_for_image = base_dt.add(days=off_days).date()

            sea_pairs = _iter_city_pairs(sea_cities)
            other_pairs = _iter_city_pairs(other_cities)

            ref_city, (ref_lat, ref_lon) = _pick_ref_pair_for_region(sea_pairs, other_pairs, region_name)
            wm_ref = get_weather(ref_lat, ref_lon, tz_name=tz_obj.name) or {}
            storm_warning = bool(storm_alert_line(wm_ref, tz_obj, offset_days=off_days))

            region_key = "cy" if is_cyprus else "kld"
            style_id = _pick_style_id(date_for_image=date_for_image, region_key=region_key, effective_mode=effective_mode, n_styles=5)

            if is_cyprus:
                marine_mood, inland_mood, astro_mood_en = _build_cy_image_moods_for_date(
                    tz_obj=tz_obj,
                    sea_pairs=sea_pairs,
                    other_pairs=other_pairs,
                    region_name=region_name,
                    date_for_image=date_for_image,
                )
            else:
                marine_mood, inland_mood, astro_mood_en = _build_kld_image_moods_for_evening(
                    tz_obj=tz_obj, sea_pairs=sea_pairs, other_pairs=other_pairs, date_for_image=date_for_image
                )

            if prompt_kind == "cy_morning":
                warm_city = None
                cool_city = None
                warm_temp = None
                cool_temp = None
                for city, (la, lo) in (other_pairs + sea_pairs):
                    tmax, _, _ = _fetch_temps_for_offset(la, lo, tz_obj.name, off_days)
                    if not isinstance(tmax, (int, float)):
                        continue
                    tv = float(tmax)
                    if warm_temp is None or tv > warm_temp:
                        warm_temp = tv
                        warm_city = city
                    if cool_temp is None or tv < cool_temp:
                        cool_temp = tv
                        cool_city = city

                sunset_hhmm = _sunset_hhmm_for_offset(wm_ref, tz_obj, off_days)

                air = get_air(ref_lat, ref_lon) or {}
                try:
                    aqi_value = float(air.get("aqi")) if air.get("aqi") is not None else None
                except Exception:
                    aqi_value = None

                kp_value, kp_status, kp_age_min, kp_src = _kp_global_swpc()

                def _aqi_bucket(v: Optional[float]) -> Optional[str]:
                    if v is None:
                        return None
                    if v <= 50:
                        return "низкий"
                    if v <= 100:
                        return "умеренный"
                    return "высокий"

                def _kp_bucket(v: Optional[float]) -> Optional[str]:
                    if v is None:
                        return None
                    if v <= 3:
                        return "спокойно"
                    if v <= 5:
                        return "умеренно"
                    if v <= 7:
                        return "буря"
                    return "сильная буря"

                metrics = MorningMetrics(  # type: ignore[misc,name-defined]
                    warm_city=warm_city,
                    warm_temp_c=(float(warm_temp) if isinstance(warm_temp, (int, float)) else None),
                    cool_city=cool_city,
                    cool_temp_c=(float(cool_temp) if isinstance(cool_temp, (int, float)) else None),
                    sunset_hhmm=sunset_hhmm,
                    aqi_value=aqi_value,
                    aqi_bucket=_aqi_bucket(aqi_value),
                    kp_value=(float(kp_value) if isinstance(kp_value, (int, float)) else None),
                    kp_bucket=_kp_bucket(float(kp_value)) if isinstance(kp_value, (int, float)) else None,
                    storm_warning=storm_warning,
                )

                style = os.getenv("CY_MORNING_STYLE", "auto")
                aspect = os.getenv("CY_MORNING_ASPECT", "1:1")

                dt_local = pendulum.datetime(
                    date_for_image.year, date_for_image.month, date_for_image.day, 8, 0, 0, tz=tz_obj
                )

                prompt, style_name, style_id_from_builder = prompt_builder(  # type: ignore[misc]
                    date_local=dt_local,
                    metrics=metrics,
                    region_name="Cyprus",
                    style=style,
                    seed_offset=0,
                    aspect=aspect,
                    no_text=True,
                )

                try:
                    if isinstance(style_id_from_builder, int):
                        style_id = int(style_id_from_builder) % 5
                except Exception:
                    pass

                preset = CY_STYLE_PRESETS_EN[style_id]
                prompt = (prompt or "").rstrip() + "\n\n" + preset
                style_name = f"{style_name}_s{style_id}"

            else:
                try:
                    prompt, style_name = prompt_builder(  # type: ignore[misc]
                        date=date_for_image,
                        marine_mood=marine_mood,
                        inland_mood=inland_mood,
                        astro_mood_en=astro_mood_en,
                        storm_warning=storm_warning,
                    )
                except TypeError:
                    prompt, style_name = prompt_builder(  # type: ignore[misc]
                        date=date_for_image,
                        marine_mood=marine_mood,
                        inland_mood=inland_mood,
                        astro_mood_en=astro_mood_en,
                    )

                if is_cyprus:
                    preset = CY_STYLE_PRESETS_EN[style_id]
                    prompt = (prompt or "").rstrip() + "\n\n" + preset
                    style_name = f"{style_name}_s{style_id}"
                else:
                    preset = KLD_STYLE_PRESETS_EN[style_id]
                    prompt = (prompt or "").rstrip() + "\n\n" + preset
                    style_name = f"{style_name}_s{style_id}"

            safe_style = _safe_slug(style_name or "default")
            img_prefix = "cy" if is_cyprus else "kld"
            img_dir = Path(f"{img_prefix}_images")
            img_dir.mkdir(parents=True, exist_ok=True)

            img_file = img_dir / f"{img_prefix}_{effective_mode}_{date_for_image.isoformat()}_s{style_id}_{safe_style}.jpg"

            logging.info(
                "%s: %s image target=%s (builder=%s, style_id=%s, rotation=%s)",
                log_prefix,
                effective_mode,
                img_file,
                prompt_kind,
                style_id,
                os.getenv("IMG_STYLE_ROTATION", "date"),
            )

            if _should_regen_image(img_file):
                ok = generate_astro_image(prompt, str(img_file))  # type: ignore[misc]
            else:
                ok = True

            if ok and img_file.exists():
                img_path = str(img_file)
            else:
                logging.warning("%s: gen returned False or file missing; fallback to text", log_prefix)

        except Exception as e:
            logging.warning("%s: error in image generation: %s", log_prefix, e)
    else:
        logging.info(
            "%s: skip image (enable_img=%s, effective_mode=%s, gen=%s, prompt_fn=%s)",
            log_prefix,
            enable_img,
            effective_mode,
            bool(generate_astro_image),
            bool(prompt_builder),
        )

    if img_path and Path(img_path).exists():
        caption = msg
        if len(caption) > 1000:
            caption = caption[:1000].rstrip()
        try:
            logging.info("%s: sending photo %s", log_prefix, img_path)
            with open(img_path, "rb") as f:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=caption,
                    parse_mode=constants.ParseMode.HTML,
                )
            if caption != msg:
                await bot.send_message(
                    chat_id=chat_id,
                    text=msg,
                    parse_mode=constants.ParseMode.HTML,
                    disable_web_page_preview=True,
                )
            return
        except Exception as exc:
            logging.exception("%s: sending photo failed, fallback to text: %s", log_prefix, exc)

    logging.info("%s: sending plain text message", log_prefix)
    await bot.send_message(
        chat_id=chat_id,
        text=msg,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True,
    )

async def main_common(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz,
    mode: Optional[str] = None,
) -> None:
    await send_common_post(
        bot=bot,
        chat_id=chat_id,
        region_name=region_name,
        sea_label=sea_label,
        sea_cities=sea_cities,
        other_label=other_label,
        other_cities=other_cities,
        tz=tz,
        mode=mode,
    )

__all__ = [
    "build_message",
    "send_common_post",
    "main_common",
    "schumann_line",
    "get_schumann_with_fallback",
    "pick_header_metrics_for_offset",
    "pick_tomorrow_header_metrics",
    "radiation_line",
    "build_astro_section",
]
