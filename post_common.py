#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Cyprus / universal helper).

This module is designed to be imported by region-specific entrypoints (e.g., post_cy.py).
It focuses on reliability and predictable formatting:

Morning (for today):
  - Human-friendly summary + 🌇 sunset today
  - Air / space blocks are allowed (air is recommended; space is optional)
  - Optional AI image (5-style rotation by date)

Evening (announce tomorrow):
  - Two lists (marine / inland) for TOMORROW + 🌅 sunrise tomorrow
  - Compact astro section (optional)

FX-only (noon MSK):
  - A short standalone currency rates message with deltas vs previous cached value.

Key fixes included:
  - UV warning threshold raised to 6+ (was often too noisy at 3+).
  - Deterministic morning style rotation (5 styles) based on local date.
  - Hardened network calls (timeouts, retries) + graceful fallbacks.
  - Persistent state stored under VAYBOMETER_CACHE_DIR (default: .cache).

Environment (common):
  TELEGRAM_TOKEN, CHANNEL_ID, CHANNEL_ID_TEST
  TZ (default: Asia/Nicosia)
  VAYBOMETER_CACHE_DIR (default: .cache)

Cyprus images (morning):
  CY_IMG_ENABLED=1/0
  CY_MORNING_STYLE=auto|1|2|3|4|5
  CY_MORNING_SEED_OFFSET=0
  CY_MORNING_ASPECT=1:1 (passed through to imagegen if used)

Optional toggles:
  DISABLE_LLM_DAILY=1/0   (kept for compatibility; not required here)
  DISABLE_SPACE=1/0       (if you have a space-weather block elsewhere)

City config (optional):
  - If "cities_cy.json" exists in the working directory, it will be used.
    Expected structure:
      {
        "marine": [{"name": "Limassol", "lat": 34.68, "lon": 33.05}, ...],
        "inland": [{"name": "Troodos", "lat": 34.91, "lon": 32.86}, ...]
      }
  - Otherwise, defaults are used.

This file is intentionally self-contained (requests + pendulum).
"""

from __future__ import annotations

import os
import json
import time
import html
import hashlib
import datetime as _dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    import pendulum
except Exception:  # pragma: no cover
    pendulum = None  # type: ignore


# ----------------------------
# Utilities
# ----------------------------

def _log(level: str, msg: str) -> None:
    print(f"{level}: {msg}", flush=True)


def env_str(key: str, default: str = "") -> str:
    v = os.getenv(key)
    return default if v is None else str(v)


def env_bool(key: str, default: bool = False) -> bool:
    v = (os.getenv(key) or "").strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on", "y")


def env_int(key: str, default: int = 0) -> int:
    v = (os.getenv(key) or "").strip()
    try:
        return int(v)
    except Exception:
        return default


def ensure_dir(path: str) -> str:
    os.makedirs(path, exist_ok=True)
    return path


def cache_dir() -> str:
    return ensure_dir(env_str("VAYBOMETER_CACHE_DIR", ".cache"))


def load_json(path: str, default: Any) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save_json(path: str, obj: Any) -> None:
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    os.replace(tmp, path)


def stable_int(s: str) -> int:
    # Deterministic integer hash for seeds
    h = hashlib.sha256(s.encode("utf-8")).hexdigest()
    return int(h[:12], 16)


def html_escape(s: str) -> str:
    return html.escape(s, quote=False)


# ----------------------------
# City config
# ----------------------------

@dataclass(frozen=True)
class City:
    name: str
    lat: float
    lon: float


DEFAULT_MARINE: List[City] = [
    City("Limassol", 34.675, 33.044),
    City("Larnaca", 34.918, 33.623),
    City("Paphos", 34.772, 32.429),
    City("Ayia Napa", 34.989, 34.001),
    City("Protaras", 35.012, 34.058),
]

DEFAULT_INLAND: List[City] = [
    City("Nicosia", 35.185, 33.382),
    City("Troodos", 34.920, 32.860),
]


def load_cy_cities() -> Tuple[List[City], List[City]]:
    """
    Loads cities for Cyprus. Priority:
      1) cities_cy.json
      2) defaults
    """
    path = "cities_cy.json"
    if os.path.exists(path):
        try:
            raw = load_json(path, {})
            marine = [City(c["name"], float(c["lat"]), float(c["lon"])) for c in raw.get("marine", [])]
            inland = [City(c["name"], float(c["lat"]), float(c["lon"])) for c in raw.get("inland", [])]
            if marine and inland:
                return marine, inland
        except Exception as e:
            _log("WARNING", f"Failed to parse {path}: {e}. Using defaults.")
    return DEFAULT_MARINE, DEFAULT_INLAND


# ----------------------------
# Open-Meteo helpers
# ----------------------------

WEATHER_CODE_MAP: Dict[int, Tuple[str, str]] = {
    0: ("☀️", "ясно"),
    1: ("🌤️", "в основном ясно"),
    2: ("⛅", "переменная облачность"),
    3: ("☁️", "пасмурно"),
    45: ("🌫️", "туман"),
    48: ("🌫️", "изморозь/туман"),
    51: ("🌦️", "морось"),
    53: ("🌦️", "морось"),
    55: ("🌧️", "морось сильная"),
    61: ("🌧️", "дождь"),
    63: ("🌧️", "дождь"),
    65: ("🌧️", "сильный дождь"),
    66: ("🌨️", "ледяной дождь"),
    67: ("🌨️", "ледяной дождь"),
    71: ("🌨️", "снег"),
    73: ("🌨️", "снег"),
    75: ("❄️", "сильный снег"),
    77: ("❄️", "снежные зерна"),
    80: ("🌦️", "ливни"),
    81: ("🌧️", "ливни"),
    82: ("⛈️", "очень сильные ливни"),
    85: ("🌨️", "снегопады"),
    86: ("❄️", "сильные снегопады"),
    95: ("⛈️", "гроза"),
    96: ("⛈️", "гроза с градом"),
    99: ("⛈️", "гроза с сильным градом"),
}


def wcode_emoji(code: Optional[int]) -> str:
    if code is None:
        return "•"
    return WEATHER_CODE_MAP.get(int(code), ("•", ""))[0]


def wcode_text(code: Optional[int]) -> str:
    if code is None:
        return ""
    return WEATHER_CODE_MAP.get(int(code), ("", ""))[1]


def _requests_get(url: str, params: Dict[str, Any], timeout: int = 15, retries: int = 2) -> Dict[str, Any]:
    last_err: Optional[Exception] = None
    for i in range(retries + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            last_err = e
            if i < retries:
                time.sleep(0.6 * (i + 1))
                continue
    raise RuntimeError(f"GET failed: {url} ({last_err})")


def fetch_daily_forecast(city: City, date_local: _dt.date, tz: str) -> Dict[str, Any]:
    """
    Returns a normalized dict for one day of forecast.
    """
    url = "https://api.open-meteo.com/v1/forecast"
    params = {
        "latitude": city.lat,
        "longitude": city.lon,
        "daily": ",".join([
            "weathercode",
            "temperature_2m_max",
            "temperature_2m_min",
            "precipitation_sum",
            "windgusts_10m_max",
            "windspeed_10m_max",
            "uv_index_max",
            "sunrise",
            "sunset",
        ]),
        "timezone": tz,
        "temperature_unit": "celsius",
        "wind_speed_unit": "ms",
        "precipitation_unit": "mm",
        "start_date": date_local.isoformat(),
        "end_date": date_local.isoformat(),
    }
    raw = _requests_get(url, params=params, timeout=18, retries=2)
    daily = raw.get("daily") or {}
    # Open-Meteo returns lists even for single-day range.
    def _pick(key: str) -> Optional[float]:
        arr = daily.get(key)
        if isinstance(arr, list) and arr:
            return arr[0]
        return None

    def _pick_str(key: str) -> Optional[str]:
        arr = daily.get(key)
        if isinstance(arr, list) and arr:
            return str(arr[0])
        return None

    out: Dict[str, Any] = {
        "city": city.name,
        "date": date_local.isoformat(),
        "wcode": int(_pick("weathercode")) if _pick("weathercode") is not None else None,
        "tmax": _pick("temperature_2m_max"),
        "tmin": _pick("temperature_2m_min"),
        "precip_mm": _pick("precipitation_sum") or 0.0,
        "gust_ms": _pick("windgusts_10m_max") or 0.0,
        "wind_ms": _pick("windspeed_10m_max") or 0.0,
        "uv_max": _pick("uv_index_max"),
        "sunrise": _pick_str("sunrise"),
        "sunset": _pick_str("sunset"),
    }
    return out


def fetch_air_quality(city: City, tz: str) -> Dict[str, Any]:
    """
    Fetches current-ish AQI / PM2.5 (hourly) from Open-Meteo Air Quality API.
    Uses the last available hour in the response.
    """
    url = "https://air-quality-api.open-meteo.com/v1/air-quality"
    params = {
        "latitude": city.lat,
        "longitude": city.lon,
        "hourly": "us_aqi,pm2_5",
        "timezone": tz,
    }
    raw = _requests_get(url, params=params, timeout=15, retries=2)
    hourly = raw.get("hourly") or {}
    times = hourly.get("time") or []
    aqi = hourly.get("us_aqi") or []
    pm25 = hourly.get("pm2_5") or []
    if not (isinstance(times, list) and isinstance(aqi, list) and isinstance(pm25, list)):
        return {"ok": False}

    # pick last non-null
    idx = None
    for i in range(len(times) - 1, -1, -1):
        if i < len(aqi) and i < len(pm25) and aqi[i] is not None:
            idx = i
            break
    if idx is None:
        return {"ok": False}
    try:
        return {
            "ok": True,
            "time": str(times[idx]),
            "aqi": int(aqi[idx]),
            "pm25": float(pm25[idx]) if pm25[idx] is not None else None,
        }
    except Exception:
        return {"ok": False}


def aqi_category(aqi: int) -> str:
    # US AQI categories
    if aqi <= 50:
        return "хороший"
    if aqi <= 100:
        return "умеренный"
    if aqi <= 150:
        return "неблагоприятный для чувствительных"
    if aqi <= 200:
        return "неблагоприятный"
    if aqi <= 300:
        return "очень неблагоприятный"
    return "опасный"


# ----------------------------
# FX (CBR) helpers
# ----------------------------

def fetch_fx_cbr() -> Dict[str, Any]:
    """
    Fetches FX rates from CBR JSON mirror (widely used and stable for RUB context).
    Returns dict with date and rates for USD, EUR, TRY (if present).
    """
    url = "https://www.cbr-xml-daily.ru/daily_json.js"
    raw = _requests_get(url, params={}, timeout=20, retries=2)
    val = raw.get("Valute") or {}

    def _rate(code: str) -> Optional[float]:
        try:
            r = val.get(code, {}).get("Value")
            return float(r) if r is not None else None
        except Exception:
            return None

    def _nom(code: str) -> int:
        try:
            n = val.get(code, {}).get("Nominal")
            return int(n) if n else 1
        except Exception:
            return 1

    # Normalize TRY to 1 TRY if nominal is 10/100
    out = {
        "ok": True,
        "date": raw.get("Date") or raw.get("PreviousDate") or "",
        "USD_RUB": _rate("USD"),
        "EUR_RUB": _rate("EUR"),
        "TRY_RUB": (_rate("TRY") / _nom("TRY")) if (_rate("TRY") is not None) else None,
    }
    return out


def fx_delta_cache_path() -> str:
    return os.path.join(cache_dir(), "fx_cache.json")


def format_delta(curr: Optional[float], prev: Optional[float]) -> str:
    if curr is None or prev is None:
        return ""
    d = curr - prev
    arrow = "▲" if d > 0 else ("▼" if d < 0 else "•")
    return f" {arrow}{abs(d):.2f}"


def build_fx_message(date_local: _dt.date) -> str:
    fx = fetch_fx_cbr()
    cache_path = fx_delta_cache_path()
    prev = load_json(cache_path, {})
    prev_usd = prev.get("USD_RUB")
    prev_eur = prev.get("EUR_RUB")
    prev_try = prev.get("TRY_RUB")

    # Save new for next delta comparison
    save_json(cache_path, {
        "updated_at": _dt.datetime.utcnow().isoformat() + "Z",
        "USD_RUB": fx.get("USD_RUB"),
        "EUR_RUB": fx.get("EUR_RUB"),
        "TRY_RUB": fx.get("TRY_RUB"),
    })

    # Message
    dstr = date_local.strftime("%d.%m.%Y")
    lines = [f"💱 <b>Курсы валют</b> ({dstr}, 12:00 МСК)"]

    usd = fx.get("USD_RUB")
    eur = fx.get("EUR_RUB")
    tr = fx.get("TRY_RUB")

    if usd is not None:
        lines.append(f"• USD/RUB: <b>{usd:.2f}</b>{format_delta(usd, prev_usd)}")
    if eur is not None:
        lines.append(f"• EUR/RUB: <b>{eur:.2f}</b>{format_delta(eur, prev_eur)}")
    if tr is not None:
        lines.append(f"• TRY/RUB: <b>{tr:.2f}</b>{format_delta(tr, prev_try)}")

    if len(lines) == 1:
        lines.append("Данные временно недоступны.")
    return "\n".join(lines)


# ----------------------------
# Morning image style rotation (5 styles)
# ----------------------------

def cy_style_for_date(date_local: _dt.date) -> int:
    """
    Deterministic style rotation (1..5) by local date (plus optional seed offset).
    """
    style_env = env_str("CY_MORNING_STYLE", "auto").strip().lower()
    if style_env in ("1", "2", "3", "4", "5"):
        return int(style_env)
    seed_off = env_int("CY_MORNING_SEED_OFFSET", 0)
    # Use ordinal for rotation; stable across reruns.
    style = ((date_local.toordinal() + seed_off) % 5) + 1
    return style


def cy_style_prompt(style_id: int) -> str:
    # Short descriptors passed to an image generator (if connected).
    prompts = {
        1: "watercolor postcard, soft light, Mediterranean coast, calm mood",
        2: "clean minimal flat illustration, pastel palette, weather-themed icons",
        3: "3D cartoon render, warm colors, friendly character, gentle atmosphere",
        4: "Japanese woodblock print style, high contrast, stylized clouds and waves",
        5: "cinematic photo look, shallow depth of field, golden hour, ultra realistic",
    }
    return prompts.get(style_id, prompts[1])


def maybe_generate_morning_image(
    date_local: _dt.date,
    summary: str,
    focus_city: City,
) -> Optional[bytes]:
    """
    Optional hook to external image generator.

    Recommended integration: create a separate module imagegen.py exposing:
      generate_cyprus_morning_image(prompt: str, style_id: int, seed: int, aspect: str) -> bytes

    If unavailable, returns None.
    """
    if not env_bool("CY_IMG_ENABLED", False):
        return None

    aspect = env_str("CY_MORNING_ASPECT", "1:1").strip() or "1:1"
    style_id = cy_style_for_date(date_local)
    seed = stable_int(f"{date_local.isoformat()}|{style_id}|{focus_city.name}")

    prompt = (
        f"Cyprus morning vibe. Location: {focus_city.name}. "
        f"Weather summary: {summary}. "
        f"Style: {cy_style_prompt(style_id)}."
    )

    try:
        import importlib
        imagegen = importlib.import_module("imagegen")
        fn = getattr(imagegen, "generate_cyprus_morning_image", None)
        if not callable(fn):
            _log("WARNING", "imagegen.generate_cyprus_morning_image not found; skipping image.")
            return None
        img_bytes = fn(prompt=prompt, style_id=style_id, seed=seed, aspect=aspect)
        if isinstance(img_bytes, (bytes, bytearray)) and len(img_bytes) > 1000:
            _log("INFO", f"Generated morning image (style {style_id}, seed {seed}, aspect {aspect}).")
            return bytes(img_bytes)
        _log("WARNING", "Image generator returned empty payload; skipping image.")
        return None
    except Exception as e:
        _log("WARNING", f"Morning image generation failed; skipping image. ({e})")
        return None


# ----------------------------
# Message building
# ----------------------------

def _fmt_temp(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{int(round(v))}°"


def _time_hhmm(iso_dt: Optional[str], tz: str) -> str:
    if not iso_dt:
        return "—"
    try:
        if pendulum is None:
            return iso_dt[11:16]
        dt = pendulum.parse(iso_dt)
        if dt.timezone_name != tz:
            dt = dt.in_tz(tz)
        return dt.format("HH:mm")
    except Exception:
        return iso_dt[11:16] if len(iso_dt) >= 16 else iso_dt


def _summarize_day(city_days: List[Dict[str, Any]]) -> Dict[str, Any]:
    tmax_vals = [(d.get("tmax"), d.get("city")) for d in city_days if d.get("tmax") is not None]
    tmin_vals = [(d.get("tmin"), d.get("city")) for d in city_days if d.get("tmin") is not None]

    warmest = max(tmax_vals, key=lambda x: x[0]) if tmax_vals else (None, None)
    coldest = min(tmin_vals, key=lambda x: x[0]) if tmin_vals else (None, None)

    overall_min = min([v for v, _ in tmin_vals], default=None)
    overall_max = max([v for v, _ in tmax_vals], default=None)

    gust_max = max([float(d.get("gust_ms") or 0.0) for d in city_days], default=0.0)
    precip_max = max([float(d.get("precip_mm") or 0.0) for d in city_days], default=0.0)
    uv_max = max([float(d.get("uv_max") or 0.0) for d in city_days], default=0.0)

    return {
        "overall_min": overall_min,
        "overall_max": overall_max,
        "warmest_city": warmest[1],
        "warmest_t": warmest[0],
        "coldest_city": coldest[1],
        "coldest_t": coldest[0],
        "gust_max": gust_max,
        "precip_max": precip_max,
        "uv_max": uv_max,
    }


def _storm_warning(gust_ms: float, precip_mm: float, any_thunder: bool) -> Optional[str]:
    if gust_ms >= 20:
        return f"⚠️ Штормовое предупреждение: порывы до {int(round(gust_ms))} м/с. Берегите планы и закладывайте время."
    if any_thunder and precip_mm >= 10:
        return "⚠️ Возможны грозы и ливни. Проверьте планы на открытом воздухе."
    if precip_mm >= 25:
        return "⚠️ Ожидаются сильные осадки. Учитывайте дороги и видимость."
    return None


def _uv_warning(uv_max: float) -> Optional[str]:
    # Fix: warn from 6+ (high) instead of 3+
    if uv_max >= 11:
        return "☀️ UV очень высокий (11+). Крем SPF, очки, тень в полдень."
    if uv_max >= 8:
        return "☀️ UV высокий (8–10). SPF обязателен, особенно с 11:00 до 15:00."
    if uv_max >= 6:
        return "☀️ UV повышенный (6–7). SPF и головной убор будут кстати."
    return None


def build_cyprus_morning(date_local: _dt.date, tz: str = "Asia/Nicosia") -> Tuple[str, Optional[bytes]]:
    marine, inland = load_cy_cities()
    cities = marine + inland

    days: List[Dict[str, Any]] = []
    any_thunder = False
    sunset_any = None

    for c in cities:
        try:
            d = fetch_daily_forecast(c, date_local, tz)
            days.append(d)
            wc = d.get("wcode")
            if wc is not None and int(wc) in (95, 96, 99):
                any_thunder = True
            if not sunset_any and d.get("sunset"):
                sunset_any = d.get("sunset")
        except Exception as e:
            _log("WARNING", f"Forecast failed for {c.name}: {e}")

    snap = _summarize_day(days)
    dstr = date_local.strftime("%d.%m.%Y")

    lines: List[str] = [f"<b>Кипр: погода на сегодня</b> ({dstr})"]

    lo = snap.get("overall_min")
    hi = snap.get("overall_max")
    w_city = snap.get("warmest_city")
    c_city = snap.get("coldest_city")

    if lo is not None and hi is not None and w_city and c_city:
        lines.append(
            f"👋 Доброе утро! Сегодня диапазон {int(round(lo))}–{int(round(hi))}°. "
            f"Теплее всего — {html_escape(w_city)} ({_fmt_temp(snap.get('warmest_t'))}), "
            f"прохладнее — {html_escape(c_city)} ({_fmt_temp(snap.get('coldest_t'))})."
        )
    else:
        lines.append("👋 Доброе утро! Данные по погоде обновляются; ниже — ориентиры по городам.")

    sw = _storm_warning(float(snap.get("gust_max") or 0.0), float(snap.get("precip_max") or 0.0), any_thunder)
    if sw:
        lines.append(sw)

    uvw = _uv_warning(float(snap.get("uv_max") or 0.0))
    if uvw:
        lines.append(uvw)

    if sunset_any:
        lines.append(f"🌇 Закат сегодня: {_time_hhmm(sunset_any, tz)}")

    # Air quality (Limassol)
    try:
        aq = fetch_air_quality(DEFAULT_MARINE[0], tz)
    except Exception as e:
        _log("WARNING", f"AQI fetch failed: {e}")
        aq = {"ok": False}

    if aq.get("ok"):
        aqi = int(aq["aqi"])
        pm25 = aq.get("pm25")
        pm25_s = f"{pm25:.0f}" if isinstance(pm25, (int, float)) else "—"
        lines.append(f"🏭 AQI {aqi} ({aqi_category(aqi)}) • PM₂.₅ {pm25_s} μg/m³")

    if days:
        def _row(d: Dict[str, Any]) -> str:
            em = wcode_emoji(d.get("wcode"))
            return f"{html_escape(d.get('city',''))}: {_fmt_temp(d.get('tmax'))}/{_fmt_temp(d.get('tmin'))} {em}"
        sample = days[:5]
        lines.append("—")
        lines.append("Коротко по городам: " + " • ".join(_row(d) for d in sample))

    img_bytes = None
    try:
        summary = f"{wcode_text(days[0].get('wcode')) if days else 'weather'}; {int(round(hi)) if hi is not None else ''}C"
        img_bytes = maybe_generate_morning_image(date_local, summary=summary, focus_city=DEFAULT_MARINE[0])
    except Exception as e:
        _log("WARNING", f"Image hook failed: {e}")
        img_bytes = None

    return "\n".join(lines).strip(), img_bytes


def build_cyprus_evening(date_local: _dt.date, tz: str = "Asia/Nicosia") -> str:
    tomorrow = date_local + _dt.timedelta(days=1)
    marine, inland = load_cy_cities()

    def _city_lines(group: List[City]) -> List[str]:
        out = []
        for c in group:
            try:
                d = fetch_daily_forecast(c, tomorrow, tz)
                em = wcode_emoji(d.get("wcode"))
                out.append(f"• {html_escape(c.name)}: <b>{_fmt_temp(d.get('tmax'))}</b> / {_fmt_temp(d.get('tmin'))} {em}")
            except Exception as e:
                _log("WARNING", f"Tomorrow forecast failed for {c.name}: {e}")
        return out

    sunrise_any = None
    try:
        d0 = fetch_daily_forecast(marine[0], tomorrow, tz)
        sunrise_any = d0.get("sunrise")
    except Exception:
        sunrise_any = None

    dstr = tomorrow.strftime("%d.%m.%Y")
    lines: List[str] = [f"<b>Кипр: погода на завтра</b> ({dstr})"]
    if sunrise_any:
        lines.append(f"🌅 Рассвет завтра: {_time_hhmm(sunrise_any, tz)}")
    lines.append("—")
    lines.append("<b>🌊 Морские города</b>")
    m = _city_lines(marine)
    lines += (m if m else ["• данные временно недоступны"])
    lines.append("—")
    lines.append("<b>⛰️ Континент / горы</b>")
    i = _city_lines(inland)
    lines += (i if i else ["• данные временно недоступны"])

    return "\n".join(lines).strip()


# ----------------------------
# Telegram sending
# ----------------------------

def tg_send_message(token: str, chat_id: str, text: str, disable_preview: bool = True) -> None:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": bool(disable_preview),
    }
    r = requests.post(url, json=payload, timeout=20)
    if not r.ok:
        raise RuntimeError(f"Telegram sendMessage failed: {r.status_code} {r.text}")


def tg_send_photo(token: str, chat_id: str, photo_bytes: bytes, caption: str) -> None:
    url = f"https://api.telegram.org/bot{token}/sendPhoto"
    files = {"photo": ("image.png", photo_bytes)}
    data = {
        "chat_id": chat_id,
        "caption": caption,
        "parse_mode": "HTML",
    }
    r = requests.post(url, data=data, files=files, timeout=40)
    if not r.ok:
        raise RuntimeError(f"Telegram sendPhoto failed: {r.status_code} {r.text}")


def resolve_chat_id(to_test: bool = False, explicit_chat_id: Optional[str] = None) -> str:
    if explicit_chat_id:
        return explicit_chat_id
    if to_test:
        cid = env_str("CHANNEL_ID_TEST", "").strip()
        if cid:
            return cid
    cid = env_str("CHANNEL_ID", "").strip()
    if not cid:
        raise RuntimeError("CHANNEL_ID is not set.")
    return cid


# ----------------------------
# Public entrypoints for region scripts
# ----------------------------

def post_cy_morning(date_local: _dt.date, to_test: bool = False, chat_id: Optional[str] = None) -> None:
    token = env_str("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN is not set.")
    tz = env_str("TZ", "Asia/Nicosia")
    real_chat = resolve_chat_id(to_test=to_test, explicit_chat_id=chat_id)

    text, img = build_cyprus_morning(date_local, tz=tz)
    if img:
        tg_send_photo(token, real_chat, img, caption=text)
    else:
        tg_send_message(token, real_chat, text)


def post_cy_evening(date_local: _dt.date, to_test: bool = False, chat_id: Optional[str] = None) -> None:
    token = env_str("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN is not set.")
    tz = env_str("TZ", "Asia/Nicosia")
    real_chat = resolve_chat_id(to_test=to_test, explicit_chat_id=chat_id)

    text = build_cyprus_evening(date_local, tz=tz)
    tg_send_message(token, real_chat, text)


def post_fx_only(date_local: _dt.date, to_test: bool = False, chat_id: Optional[str] = None) -> None:
    token = env_str("TELEGRAM_TOKEN", "").strip()
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN is not set.")
    real_chat = resolve_chat_id(to_test=to_test, explicit_chat_id=chat_id)

    text = build_fx_message(date_local)
    tg_send_message(token, real_chat, text)


# ----------------------------
# Minimal CLI (optional)
# ----------------------------

def _parse_date(s: str) -> _dt.date:
    return _dt.date.fromisoformat(s)


def _today_in_tz(tz: str) -> _dt.date:
    if pendulum is not None:
        return pendulum.now(tz).date()
    return _dt.date.today()


def main() -> None:
    import argparse
    p = argparse.ArgumentParser(description="VayboMeter post_common helper (Cyprus).")
    p.add_argument("--mode", choices=["morning", "evening", "fx-only"], required=True)
    p.add_argument("--date", help="Override date (YYYY-MM-DD). Interpreted as local date.", default="")
    p.add_argument("--to-test", action="store_true", help="Send to test channel.")
    p.add_argument("--chat-id", default="", help="Explicit chat_id override.")
    args = p.parse_args()

    tz = env_str("TZ", "Asia/Nicosia")
    d = _parse_date(args.date) if args.date else _today_in_tz(tz)
    chat = args.chat_id.strip() or None

    if args.mode == "morning":
        post_cy_morning(d, to_test=args.to_test, chat_id=chat)
    elif args.mode == "evening":
        post_cy_evening(d, to_test=args.to_test, chat_id=chat)
    else:
        post_fx_only(d, to_test=args.to_test, chat_id=chat)


if __name__ == "__main__":
    main()


# ---------------------------------------------------------------------
# Compatibility: main_common (expected by post_cy.py in some repositories)
# ---------------------------------------------------------------------

def main_common(*args: Any, **kwargs: Any) -> None:
    """
    Backwards-compatible entrypoint expected by some region scripts:

      from post_common import main_common
      if __name__ == "__main__":
          main_common()

    Supported call styles:
      - main_common()                       -> uses current sys.argv (expects --mode ...)
      - main_common(argv=[...])             -> uses provided argv (without program name)
      - main_common([...])                  -> same as argv
      - main_common("morning")              -> convenience: builds argv for that mode
      - main_common(mode="morning", date="YYYY-MM-DD", to_test=True, chat_id="...")

    This wrapper will prefer direct function calls if mode/date/to_test/chat_id are provided.
    """
    # Direct-kwargs path (most explicit; avoids argparse surprises)
    mode = (kwargs.get("mode") or "").strip().lower()
    date_s = (kwargs.get("date") or "").strip()
    to_test = bool(kwargs.get("to_test", False))

    def _normalize_chat_id(v):
        """Accept int chat_id (aiogram-friendly) or str (e.g., @channelname)."""
        if v is None:
            return None
        if isinstance(v, int):
            return v
        s = str(v).strip()
        if not s:
            return None
        try:
            return int(s)
        except Exception:
            return s

    chat_id = _normalize_chat_id(kwargs.get("chat_id") or kwargs.get("chat"))

    tz = env_str("TZ", "Asia/Nicosia")
    date_local = _parse_date(date_s) if date_s else _today_in_tz(tz)

    if mode in ("morning", "evening", "fx-only"):
        if mode == "morning":
            post_cy_morning(date_local, to_test=to_test, chat_id=chat_id)
            return
        if mode == "evening":
            post_cy_evening(date_local, to_test=to_test, chat_id=chat_id)
            return
        post_fx_only(date_local, to_test=to_test, chat_id=chat_id)
        return

    # argv-based path
    argv = kwargs.get("argv", None)
    if argv is None and args:
        argv = args[0]

    # Convenience: main_common("morning")
    if isinstance(argv, str):
        s = argv.strip().lower()
        if s in ("morning", "evening", "fx-only"):
            return main_common(mode=s, date=date_s, to_test=to_test, chat_id=(chat_id or ""))
        argv = None

    if isinstance(argv, (list, tuple)):
        import sys as _sys
        _sys.argv = [_sys.argv[0]] + [str(x) for x in argv]
        main()
        return

    # Fallback: use current sys.argv
    main()
