#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Кипр/универсальный).

Утро: «человечный» обзор без блоков, + 🌇 закат СЕГОДНЯ.
Вечер: единый список городов, + 🌅 рассвет ЗАВТРА.
Астроблок и рекомендации — через gpt.py (логика моделей там).
"""

from __future__ import annotations
import os, re, json, html, asyncio, logging, math
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Union

import pendulum
from telegram import Bot, constants

from utils        import compass, get_fact, AIR_EMOJI, kmh_to_ms, smoke_index
from weather      import get_weather, fetch_tomorrow_temps, day_night_stats
from air          import get_air, get_sst, get_kp, get_solar_wind
from pollen       import get_pollen
from radiation    import get_radiation
from gpt          import gpt_blurb, gpt_complete

try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────── базовые константы ──────────────────────────
CY_LAT, CY_LON = 34.707, 33.022
CPM_TO_USVH = float(os.getenv("CPM_TO_USVH", "0.000571"))
PRIMARY_CITY_NAME = os.getenv("PRIMARY_CITY", "Limassol")

CACHE_DIR = Path(".cache"); CACHE_DIR.mkdir(exist_ok=True, parents=True)
USE_DAILY_LLM = os.getenv("DISABLE_LLM_DAILY", "").strip().lower() not in ("1","true","yes","on")
DISABLE_SCHUMANN = os.getenv("DISABLE_SCHUMANN", "").strip().lower() in ("1","true","yes","on")

# ────────────────────────── LLM safety ──────────────────────────
DISABLE_LLM_TIPS = os.getenv("DISABLE_LLM_TIPS", "").strip().lower() in ("1","true","yes","on")
ASTRO_LLM_TEMP = float(os.getenv("ASTRO_LLM_TEMP", "0.2"))

# ───────────── LLM-надстройки (по флагам) ─────────────
ENABLE_FACT_LLM     = os.getenv("ENABLE_FACT_LLM", "").strip().lower() in ("1","true","yes","on")
FACT_LLM_TEMP       = float(os.getenv("FACT_LLM_TEMP", "0.2"))

ENABLE_SUMMARY_LLM  = os.getenv("ENABLE_SUMMARY_LLM", "").strip().lower() in ("1","true","yes","on")
SUMMARY_LLM_TEMP    = float(os.getenv("SUMMARY_LLM_TEMP", "0.2"))

SAFE_TIPS_FALLBACKS = {
    "здоровый день": ["🚶 30–40 мин лёгкой активности.", "🥤 Больше воды и короткие паузы.", "😴 7–9 часов сна — приоритет."],
    "плохая погода": ["🧥 Слои + непромокаемая куртка.", "🌧 Перенесите дела под крышу.", "🚗 Заложите время на дорогу."],
    "магнитные бури": ["🧘 Берегите нервную систему.", "💧 Пейте воду, больше магния/калия.", "📵 Меньше экранов вечером."],
    "плохой воздух": ["😮‍💨 Сократите нагрузку на улице.", "🪟 Проветривайте короче, фильтры в помощь.", "🏃 Тренировки — в помещении."],
    "волны Шумана": ["🧘 Спокойный темп дня.", "🍵 Лёгкая еда, тёплые напитки.", "😴 Ранний отход ко сну."],
}

# ───────────── Локализация городов для приветствия ─────────────
RU_NAMES = {
    "Nicosia": "Никосия", "Nicosía": "Никосия",
    "Troodos": "Троодос", "Troodos Mountains": "Троодос",
    "Limassol": "Лимасол", "Larnaca": "Ларнака",
    "Pafos": "Пафос", "Paphos": "Пафос",
    "Ayia Napa": "Айя-Напа", "Protaras": "Протарас",
}
def _ru_city(name: str) -> str: return RU_NAMES.get(name, name)

def _escape_html(s: str) -> str:
    return html.escape(str(s), quote=False)

def _sanitize_line(s: str, max_len: int = 140) -> str:
    s = " ".join(str(s).split())
    s = re.sub(r"(.)\1{3,}", r"\1\1\1", s)
    return (_escape_html(s[:max_len-1]) + "…") if len(s) > max_len else _escape_html(s)

def _looks_gibberish(s: str) -> bool:
    if re.search(r"(.)\1{5,}", s): return True
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", s)
    return (len(set(letters)) <= 2 and len("".join(letters)) >= 10)

def safe_tips(theme: str) -> list[str]:
    theme_key = (theme or "здоровый день").strip().lower()
    if DISABLE_LLM_TIPS:
        return SAFE_TIPS_FALLBACKS.get(theme_key, SAFE_TIPS_FALLBACKS["здоровый день"])
    try:
        _, tips = gpt_blurb(theme_key)
        out: list[str] = []
        for t in (tips or [])[:3]:
            t = _sanitize_line(t, 140)
            if t and not _looks_gibberish(t): out.append(t)
        if out: return out
    except Exception as e:
        logging.warning("LLM tips failed: %s", e)
    return SAFE_TIPS_FALLBACKS.get(theme_key, SAFE_TIPS_FALLBACKS["здоровый день"])

# ────────────────────────── ENV TUNABLES (водные активности) ──────────────────────────
KITE_WIND_MIN        = float(os.getenv("KITE_WIND_MIN",        "6"))
KITE_WIND_GOOD_MIN   = float(os.getenv("KITE_WIND_GOOD_MIN",   "7"))
KITE_WIND_GOOD_MAX   = float(os.getenv("KITE_WIND_GOOD_MAX",   "12"))
KITE_WIND_STRONG_MAX = float(os.getenv("KITE_WIND_STRONG_MAX", "18"))
KITE_GUST_RATIO_BAD  = float(os.getenv("KITE_GUST_RATIO_BAD",  "1.5"))
KITE_WAVE_WARN       = float(os.getenv("KITE_WAVE_WARN",       "2.5"))

SUP_WIND_GOOD_MAX    = float(os.getenv("SUP_WIND_GOOD_MAX",    "4"))
SUP_WIND_OK_MAX      = float(os.getenv("SUP_WIND_OK_MAX",      "6"))
SUP_WIND_EDGE_MAX    = float(os.getenv("SUP_WIND_EDGE_MAX",    "8"))
SUP_WAVE_GOOD_MAX    = float(os.getenv("SUP_WAVE_GOOD_MAX",    "0.6"))
SUP_WAVE_OK_MAX      = float(os.getenv("SUP_WAVE_OK_MAX",      "0.8"))
SUP_WAVE_BAD_MIN     = float(os.getenv("SUP_WAVE_BAD_MIN",     "1.5"))
OFFSHORE_SUP_WIND_MIN= float(os.getenv("OFFSHORE_SUP_WIND_MIN","5"))

SURF_WAVE_GOOD_MIN   = float(os.getenv("SURF_WAVE_GOOD_MIN",   "0.9"))
SURF_WAVE_GOOD_MAX   = float(os.getenv("SURF_WAVE_GOOD_MAX",   "2.5"))
SURF_WIND_MAX        = float(os.getenv("SURF_WIND_MAX",        "10"))

WSUIT_NONE  = float(os.getenv("WSUIT_NONE",  "22"))
WSUIT_SHORTY= float(os.getenv("WSUIT_SHORTY","20"))
WSUIT_32    = float(os.getenv("WSUIT_32",   "17"))
WSUIT_43    = float(os.getenv("WSUIT_43",   "14"))
WSUIT_54    = float(os.getenv("WSUIT_54",   "12"))
WSUIT_65    = float(os.getenv("WSUIT_65",   "10"))

# ────────────────────────── SST cache ──────────────────────────
SST_CACHE_TTL_MIN = int(os.getenv("SST_CACHE_TTL_MIN", "0"))  # 0 => бессрочно
_SST_CACHE: Dict[Tuple[float, float], Tuple[float, int]] = {}

def _sst_key(la: float, lo: float) -> Tuple[float, float]:
    return (round(float(la), 3), round(float(lo), 3))

def get_sst_cached(la: float, lo: float) -> Optional[float]:
    key = _sst_key(la, lo)
    now = pendulum.now("UTC").int_timestamp
    if key in _SST_CACHE:
        val, ts = _SST_CACHE[key]
        if SST_CACHE_TTL_MIN <= 0 or (now - ts) <= SST_CACHE_TTL_MIN * 60:
            return val
    val = get_sst(la, lo)
    if isinstance(val, (int, float)):
        _SST_CACHE[key] = (float(val), now)
        return float(val)
    return _SST_CACHE.get(key, (None, 0))[0]

# ────────────────────────── береговая линия/споты ──────────────────────────
SHORE_PROFILE: Dict[str, float] = {"Limassol":180.0, "Larnaca":180.0, "Ayia Napa":140.0, "Pafos":210.0}
SPOT_SHORE_PROFILE: Dict[str, float] = {
    "Lady's Mile":170.0,"Paramali":210.0,"Kourion (Curium)":210.0,"Governor's Beach":180.0,"Pissouri":220.0,
    "Avdimou":210.0,"Larnaca Kite Beach (Kiti)":180.0,"Mazotos":180.0,"Mackenzie":150.0,"Ayia Napa (Nissi)":140.0,
    "Protaras":135.0,"Cape Greco":120.0,"Paphos (Alykes)":230.0,"Coral Bay":260.0,"Latchi":320.0,
}
def _norm_key(s: str) -> str: return re.sub(r"[^a-z0-9]", "", s.lower())
_SPOT_INDEX = {_norm_key(k): k for k in SPOT_SHORE_PROFILE.keys()}
def _parse_deg(val: Optional[str]) -> Optional[float]:
    if not val: return None
    try: return float(str(val).strip())
    except Exception: return None
def _env_city_key(city: str) -> str: return city.upper().replace(" ", "_")
def _spot_from_env(name: Optional[str]) -> Optional[Tuple[str,float]]:
    if not name: return None
    key = _norm_key(name); real = _SPOT_INDEX.get(key)
    return (real, SPOT_SHORE_PROFILE[real]) if real else None
def _shore_face_for_city(city: str) -> Tuple[Optional[float], Optional[str]]:
    face_env = _parse_deg(os.getenv(f"SHORE_FACE_{_env_city_key(city)}"))
    if face_env is not None: return face_env, f"ENV:SHORE_FACE_{_env_city_key(city)}"
    spot_env = os.getenv(f"SPOT_{_env_city_key(city)}")
    sp = _spot_from_env(spot_env) if spot_env else _spot_from_env(os.getenv("ACTIVE_SPOT"))
    if sp: label, deg = sp; return deg, label
    if city in SHORE_PROFILE: return SHORE_PROFILE[city], city
    return None, None

# ───────────── утилиты ─────────────
def _as_tz(tz: Union[pendulum.Timezone, str]) -> pendulum.Timezone:
    return pendulum.timezone(tz) if isinstance(tz, str) else tz

WMO_DESC = {0:"☀️ ясно",1:"⛅ ч.обл",2:"☁️ обл",3:"🌥 пасм",45:"🌫 туман",48:"🌫 изморозь",51:"🌦 морось",61:"🌧 дождь",71:"❄️ снег",95:"⛈ гроза"}
def code_desc(c: Any) -> Optional[str]:
    try: return WMO_DESC.get(int(c))
    except Exception: return None

def _iter_city_pairs(cities) -> list[tuple[str, tuple[float, float]]]:
    if isinstance(cities, dict): return list(cities.items())
    try: return list(cities)
    except Exception: return []

# ───────────── Рассвет/закат — weather → astral → NOAA ─────────────
def _parse_iso_to_tz(s: str, tz: pendulum.tz.timezone.Timezone) -> Optional[pendulum.DateTime]:
    try: return pendulum.parse(str(s)).in_tz(tz)
    except Exception: return None

def _noaa_dt_from_utc_fraction(date_obj: pendulum.Date, ut_hours: float, tz: pendulum.tz.timezone.Timezone):
    h = int(ut_hours)
    m = int(round((ut_hours - h) * 60))
    base = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, tz="UTC")
    return base.add(hours=h, minutes=m).in_tz(tz)

def _noaa_sun_times(date_obj: pendulum.Date, lat: float, lon: float, tz: pendulum.tz.timezone.Timezone)\
        -> tuple[Optional[pendulum.DateTime], Optional[pendulum.DateTime]]:
    """Мини-реализация алгоритма NOAA (зенит 90.833°)."""
    def _sun_utc(is_sunrise: bool) -> Optional[float]:
        N  = date_obj.day_of_year
        lngHour = lon / 15.0
        t = N + ((6 - lngHour)/24.0 if is_sunrise else (18 - lngHour)/24.0)
        M = (0.9856*t) - 3.289
        L = M + (1.916*math.sin(math.radians(M))) + (0.020*math.sin(math.radians(2*M))) + 282.634
        L = (L + 360.0) % 360.0
        RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L))))
        RA = (RA + 360.0) % 360.0
        Lq = (math.floor(L/90.0))*90.0; RAq = (math.floor(RA/90.0))*90.0
        RA += (Lq - RAq); RA /= 15.0
        sinDec = 0.39782 * math.sin(math.radians(L))
        cosDec = math.cos(math.asin(sinDec))
        zenith = math.radians(90.833)
        cosH = (math.cos(zenith) - (sinDec*math.sin(math.radians(lat)))) / (cosDec*math.cos(math.radians(lat)))
        if cosH > 1 or cosH < -1: return None
        H = (360 - math.degrees(math.acos(cosH))) if is_sunrise else math.degrees(math.acos(cosH))
        H /= 15.0
        T = H + RA - (0.06571*t) - 6.622
        UT = (T - lngHour) % 24.0
        return UT
    try:
        ut_sr = _sun_utc(True); ut_ss = _sun_utc(False)
        sr = _noaa_dt_from_utc_fraction(date_obj, ut_sr, tz) if ut_sr is not None else None
        ss = _noaa_dt_from_utc_fraction(date_obj, ut_ss, tz) if ut_ss is not None else None
        return sr, ss
    except Exception:
        return None, None

def _sun_times_for_date(lat: float, lon: float, date_obj: pendulum.Date, tz: pendulum.tz.timezone.Timezone)\
        -> tuple[Optional[pendulum.DateTime], Optional[pendulum.DateTime]]:
    # 1) из weather (Open-Meteo)
    try:
        wm = get_weather(lat, lon) or {}
        daily = wm.get("daily") or {}
        times = daily.get("time") or daily.get("date") or []
        sunr  = daily.get("sunrise") or daily.get("sunrise_time") or []
        suns  = daily.get("sunset")  or daily.get("sunset_time")  or []
        idx = None
        for i, t in enumerate(times):
            dt_i = _parse_iso_to_tz(t, tz)
            if dt_i and dt_i.date() == date_obj:
                idx = i; break
        if idx is not None:
            sr = _parse_iso_to_tz(sunr[idx], tz) if idx < len(sunr) else None
            ss = _parse_iso_to_tz(suns[idx], tz) if idx < len(suns) else None
            if sr or ss: return sr, ss
    except Exception:
        pass
    # 2) фолбэк на astral
    try:
        from astral.sun import sun
        from astral import LocationInfo
        loc = LocationInfo("", "", tz.name, float(lat), float(lon))
        s = sun(loc.observer, date=date_obj.to_date_string(), tzinfo=tz)
        return (pendulum.instance(s["sunrise"]).in_tz(tz), pendulum.instance(s["sunset"]).in_tz(tz))
    except Exception:
        pass
    # 3) последний фолбэк — NOAA
    return _noaa_sun_times(date_obj, lat, lon, tz)

def _choose_sun_coords(sea_pairs, other_pairs) -> Tuple[float,float]:
    """PRIMARY_CITY → первый морской → первый любой → координаты региона."""
    prim = (PRIMARY_CITY_NAME or "").strip().lower()
    def _find(pairs):
        for name,(la,lo) in pairs:
            if name.strip().lower()==prim: return (la,lo)
        return None
    sea_pairs = list(sea_pairs); other_pairs = list(other_pairs)
    cand = _find(sea_pairs) or _find(other_pairs)
    if not cand and sea_pairs: cand = sea_pairs[0][1]
    if not cand and other_pairs: cand = other_pairs[0][1]
    return cand if cand else (CY_LAT, CY_LON)

def sun_line_for_mode(mode: str, tz: pendulum.tz.timezone.Timezone,
                      lat: float, lon: float) -> Optional[str]:
    m = (mode or "evening").lower()
    if m.startswith("morn"):
        date_use = pendulum.today(tz)
        _, ss = _sun_times_for_date(lat, lon, date_use, tz)
        if ss: return f"🌇 Закат сегодня: {ss.format('HH:mm')}"
    else:
        date_use = pendulum.today(tz).add(days=1)
        sr, _ = _sun_times_for_date(lat, lon, date_use, tz)
        if sr: return f"🌅 Рассвет завтра: {sr.format('HH:mm')}"
    return None

# ───────────── Шуман ─────────────
def _read_schumann_history() -> List[Dict[str, Any]]:
    candidates: List[Path] = []
    env_path = os.getenv("SCHU_FILE")
    if env_path: candidates.append(Path(env_path))
    here = Path(__file__).parent
    candidates += [here / "schumann_hourly.json", here / "data" / "schumann_hourly.json", here.parent / "schumann_hourly.json"]
    for p in candidates:
        try:
            if p.exists():
                txt = p.read_text("utf-8").strip()
                data = json.loads(txt) if txt else []
                if isinstance(data, list): return data
        except Exception as e:
            logging.warning("Schumann history read error from %s: %s", p, e)
    return []

def _schumann_trend(values: List[float], delta: float = 0.1) -> str:
    if not values: return "→"
    tail = values[-24:] if len(values) > 24 else values
    if len(tail) < 2: return "→"
    avg_prev = sum(tail[:-1]) / (len(tail) - 1)
    d = tail[-1] - avg_prev
    return "↑" if d >= delta else "↓" if d <= -delta else "→"

def _freq_status(freq: Optional[float]) -> tuple[str, str]:
    if not isinstance(freq, (int, float)): return "🟡 колебания", "yellow"
    f = float(freq)
    if 7.4 <= f <= 8.4:
        return ("🟢 в норме", "green") if (7.7 <= f <= 8.1) else ("🟡 колебания", "yellow")
    return "🔴 сильное отклонение", "red"

def _trend_text(sym: str) -> str:
    return {"↑": "растёт", "↓": "снижается", "→": "стабильно"}.get(sym, "стабильно")

def _h7_text(h7_amp: Optional[float], h7_spike: Optional[bool]) -> str:
    if isinstance(h7_amp, (int, float)):
        return f"H7: {h7_amp:.1f} (⚡ всплеск)" if h7_spike else f"H7: {h7_amp:.1f} — спокойно"
    return "H7: — нет данных"

def _is_stale(ts: Any, max_age_sec: int = 7200) -> bool:
    if not isinstance(ts, (int, float)): return False
    try:
        now_ts = pendulum.now("UTC").int_timestamp
        return (now_ts - int(ts)) > max_age_sec
    except Exception:
        return False

def get_schumann_with_fallback() -> Dict[str, Any]:
    try:
        import schumann
        if hasattr(schumann, "get_schumann"):
            payload = schumann.get_schumann() or {}
            cached = bool(payload.get("cached"))
            if not cached and isinstance(payload.get("ts"), (int, float)) and _is_stale(payload["ts"]):
                cached = True
            return {
                "freq": payload.get("freq"),
                "amp": payload.get("amp"),
                "trend": payload.get("trend", "→"),
                "trend_text": payload.get("trend_text") or _trend_text(payload.get("trend", "→")),
                "status": payload.get("status") or _freq_status(payload.get("freq"))[0],
                "status_code": payload.get("status_code") or _freq_status(payload.get("freq"))[1],
                "h7_text": payload.get("h7_text") or _h7_text(payload.get("h7_amp"), payload.get("h7_spike")),
                "h7_amp": payload.get("h7_amp"),
                "h7_spike": payload.get("h7_spike"),
                "interpretation": payload.get("interpretation") or _gentle_interpretation(
                    payload.get("status_code") or _freq_status(payload.get("freq"))[1]
                ),
                "cached": cached,
            }
    except Exception:
        pass
    arr = _read_schumann_history()
    if not arr:
        return {"freq": None, "amp": None, "trend": "→",
                "trend_text": "стабильно", "status": "🟡 колебания", "status_code": "yellow",
                "h7_text": _h7_text(None, None), "h7_amp": None, "h7_spike": None,
                "interpretation": _gentle_interpretation("yellow"), "cached": True}
    amps: List[float] = []; last: Optional[Dict[str, Any]] = None
    for rec in arr:
        if isinstance(rec, dict) and isinstance(rec.get("amp"), (int, float)):
            amps.append(float(rec["amp"]))
        last = rec
    trend = _schumann_trend(amps)
    freq = (last.get("freq") if last else None)
    amp  = (last.get("amp")  if last else None)
    h7_amp = (last.get("h7_amp") if last else None)
    h7_spike = (last.get("h7_spike") if last else None)
    src = ((last or {}).get("src") or "").lower()
    cached = (src == "cache") or _is_stale((last or {}).get("ts"))
    status, code = _freq_status(freq)
    return {
        "freq": freq if isinstance(freq, (int, float)) else None,
        "amp":  amp  if isinstance(amp,  (int, float)) else None,
        "trend": trend, "trend_text": _trend_text(trend),
        "status": status, "status_code": code,
        "h7_text": _h7_text(h7_amp, h7_spike),
        "h7_amp": h7_amp if isinstance(h7_amp, (int, float)) else None,
        "h7_spike": h7_spike if isinstance(h7_spike, bool) else None,
        "interpretation": _gentle_interpretation(code),
        "cached": cached,
    }

def _gentle_interpretation(code: str) -> str:
    if code == "green":  return "Волны Шумана близки к норме — организм реагирует как на обычный день."
    if code == "yellow": return "Заметны колебания — возможна лёгкая чувствительность."
    return "Сильные отклонения — снижайте перегрузки и наблюдайте самочувствие."

def schumann_line(s: Dict[str, Any]) -> str:
    freq = s.get("freq"); amp = s.get("amp")
    trend_text = s.get("trend_text") or _trend_text(s.get("trend", "→"))
    status_lbl = s.get("status") or _freq_status(freq)[0]
    h7line = s.get("h7_text") or _h7_text(s.get("h7_amp"), s.get("h7_spike"))
    interp = s.get("interpretation") or _gentle_interpretation(s.get("status_code") or _freq_status(freq)[1])
    stale = " ⏳ нет свежих чисел" if s.get("cached") else ""
    if not isinstance(freq, (int, float)) and not isinstance(amp, (int, float)):
        return f"{status_lbl}{stale} • тренд: {trend_text} • {h7line}\n{interp}"
    fstr = f"{freq:.2f}" if isinstance(freq, (int, float)) else "н/д"
    astr = f"{amp:.2f} pT" if isinstance(amp, (int, float)) else "н/д"
    return f"{status_lbl}{stale} • Шуман: {fstr} Гц / {astr} • тренд: {trend_text} • {h7line}\n{interp}"

# ───────────── Safecast ─────────────
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists(): return None
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        logging.warning("Safecast read error from %s: %s", path, e)
        return None

def load_safecast() -> Optional[Dict[str, Any]]:
    paths: List[Path] = []
    if os.getenv("SAFECAST_FILE"):
        paths.append(Path(os.getenv("SAFECAST_FILE")))
    here = Path(__file__).parent
    paths.append(here / "data" / "safecast_cy.json")
    sc: Optional[Dict[str, Any]] = None
    for p in paths:
        sc = _read_json(p)
        if sc:
            break
    if not sc: return None
    ts = sc.get("ts")
    if not isinstance(ts, (int, float)): return None
    now_ts = pendulum.now("UTC").int_timestamp
    if now_ts - int(ts) > 24 * 3600: return None
    return sc

def safecast_usvh_risk(x: float) -> tuple[str, str]:
    if x <= 0.15: return "🟢", "низкий"
    if x <= 0.30: return "🟡", "умеренный"
    return "🔵", "выше нормы"

def official_usvh_risk(x: float) -> tuple[str, str]:
    if x <= 0.15: return "🟢", "низкий"
    if x <= 0.30: return "🟡", "повышенный"
    return "🔴", "высокий"

def safecast_pm_level(pm25: Optional[float], pm10: Optional[float]) -> Tuple[str, str]:
    def l25(x: float) -> int: return 0 if x<=15 else 1 if x<=35 else 2 if x<=55 else 3
    def l10(x: float) -> int: return 0 if x<=30 else 1 if x<=50 else 2 if x<=100 else 3
    worst = -1
    if isinstance(pm25,(int,float)): worst=max(worst,l25(float(pm25)))
    if isinstance(pm10,(int,float)): worst=max(worst,l10(float(pm10)))
    if worst<0: return "⚪","н/д"
    return (["🟢","🟡","🟠","🔴"][worst], ["низкий","умеренный","высокий","очень высокий"][worst])

def safecast_block_lines() -> List[str]:
    sc = load_safecast()
    if not sc: return []
    lines: List[str] = []
    pm25, pm10 = sc.get("pm25"), sc.get("pm10")
    if isinstance(pm25,(int,float)) or isinstance(pm10,(int,float)):
        em,lbl = safecast_pm_level(pm25,pm10)
        parts=[]
        if isinstance(pm25,(int,float)): parts.append(f"PM₂.₅ {pm25:.0f}")
        if isinstance(pm10,(int,float)): parts.append(f"PM₁₀ {pm10:.0f}")
        lines.append(f"🧪 Safecast: {em} {lbl} · " + " | ".join(parts))
    cpm = sc.get("cpm"); usvh = sc.get("radiation_usvh")
    if not isinstance(usvh,(int,float)) and isinstance(cpm,(int,float)):
        usvh = float(cpm) * CPM_TO_USVH
    if isinstance(usvh,(int,float)):
        em,lbl = safecast_usvh_risk(float(usvh))
        if isinstance(cpm,(int,float)):
            lines.append(f"📟 Радиация (Safecast): {cpm:.0f} CPM ≈ {usvh:.3f} μSv/h — {em} {lbl} (медиана 6 ч)")
        else:
            lines.append(f"📟 Радиация (Safecast): ≈ {usvh:.3f} μSv/h — {em} {lbl} (медиана 6 ч)")
    elif isinstance(cpm,(int,float)):
        lines.append(f"📟 Радиация (Safecast): {cpm:.0f} CPM (медиана 6 ч)")
    return lines

def radiation_line(lat: float, lon: float) -> Optional[str]:
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if isinstance(dose,(int,float)):
        em,lbl = official_usvh_risk(float(dose))
        return f"{em} Радиация: {dose:.3f} μSv/h ({lbl})"
    return None

# ───────────── Астроблок ─────────────
ZODIAC = {"Овен":"♈","Телец":"♉","Близнецы":"♊","Рак":"♋","Лев":"♌","Дева":"♍","Весы":"♎","Скорпион":"♏","Стрелец":"♐","Козерог":"♑","Водолей":"♒","Рыбы":"♓"}
def zsym(s: str) -> str:
    for name,sym in ZODIAC.items(): s = s.replace(name, sym)
    return s

def load_calendar(path: str = "lunar_calendar.json") -> dict:
    try: data = json.loads(Path(path).read_text("utf-8"))
    except Exception: return {}
    if isinstance(data, dict) and isinstance(data.get("days"), dict): return data["days"]
    return data if isinstance(data, dict) else {}

def _parse_voc_dt(s: str, tz: pendulum.tz.timezone.Timezone):
    if not s: return None
    try: return pendulum.parse(s).in_tz(tz)
    except Exception: pass
    try:
        dmy, hm = s.split(); d,m = map(int,dmy.split(".")); hh,mm = map(int,hm.split(":"))
        year = pendulum.today(tz).year
        return pendulum.datetime(year, m, d, hh, mm, tz=tz)
    except Exception: return None

def voc_interval_for_date(rec: dict, tz_local: str = "Asia/Nicosia"):
    if not isinstance(rec, dict): return None
    voc = (rec.get("void_of_course") or rec.get("voc") or rec.get("void") or {}) 
    if not isinstance(voc, dict): return None
    s = voc.get("start") or rec.get("from") or voc.get("start_time")
    e = voc.get("end")   or rec.get("to")   or voc.get("end_time")
    if not s or not e: return None
    tz = pendulum.timezone(tz_local)
    t1 = _parse_voc_dt(s, tz); t2 = _parse_voc_dt(e, tz)
    if not t1 or not t2: return None
    return (t1, t2)

def _astro_llm_bullets(date_str: str, phase: str, percent: int, sign: str, voc_text: str) -> List[str]:
    cache_file = CACHE_DIR / f"astro_{date_str}.txt"
    if cache_file.exists():
        lines = [l.strip() for l in cache_file.read_text("utf-8").splitlines() if l.strip()]
        if lines: return lines[:3]
    if not USE_DAILY_LLM: return []
    system = ("Действуй как АстроЭксперт: сделай 2–3 очень короткие строки про день. "
              "Используй только: фаза Луны, освещённость, знак Луны, интервал VoC. Без выдумок.")
    prompt = (f"Дата: {date_str}. Фаза Луны: {phase or 'н/д'} ({percent}% освещённости). "
              f"Знак: {sign or 'н/д'}. VoC: {voc_text or 'нет'}.")
    try:
        txt = gpt_complete(prompt=prompt, system=system, temperature=ASTRO_LLM_TEMP, max_tokens=160)
        raw_lines = [l.strip() for l in (txt or "").splitlines() if l.strip()]
        safe: List[str] = []
        for l in raw_lines:
            l = _sanitize_line(l, 120)
            if l and not _looks_gibberish(l):
                if not re.match(r"^\W", l): l = "• " + l
                safe.append(l)
        if safe:
            cache_file.write_text("\n".join(safe[:3]), "utf-8")
            return safe[:3]
    except Exception as e:
        logging.warning("Astro LLM failed: %s", e)
    return []

def build_astro_section(date_local: Optional[pendulum.Date] = None, tz_local: str = "Asia/Nicosia") -> str:
    tz = pendulum.timezone(tz_local)
    date_local = date_local or pendulum.today(tz)
    date_key = date_local.format("YYYY-MM-DD")
    cal = load_calendar("lunar_calendar.json")
    rec = cal.get(date_key, {}) if isinstance(cal, dict) else {}
    phase_raw = (rec.get("phase_name") or rec.get("phase") or "").strip()
    phase_name = re.sub(r"^[^\wА-Яа-яЁё]+", "", phase_raw).split(",")[0].strip()
    percent = rec.get("percent") or rec.get("illumination") or rec.get("illum") or 0
    try: percent = int(round(float(percent)))
    except Exception: percent = 0
    sign = rec.get("sign") or rec.get("zodiac") or ""
    voc_text = ""
    voc = voc_interval_for_date(rec, tz_local=tz_local)
    if voc:
        t1, t2 = voc; voc_text = f"{t1.format('HH:mm')}–{t2.format('HH:mm')}"
    bullets = _astro_llm_bullets(date_local.format("DD.MM.YYYY"), phase_name, int(percent or 0), sign, voc_text)
    if not bullets:
        adv = rec.get("advice") or []
        bullets = [f"• {a}" for a in adv[:3]] if adv else []
    if not bullets:
        base = f"🌙 Фаза: {phase_name}" if phase_name else "🌙 Лунный день в норме"
        prm  = f" ({percent}%)" if isinstance(percent, int) and percent else ""
        bullets = [base + prm, (f"♒ Знак: {sign}" if sign else "— знак Луны н/д")]
    lines = ["🌌 <b>Астрособытия</b>"]
    lines += [zsym(x) for x in bullets[:3]]
    return "\n".join(lines)

# ───────────── «Факт дня» через LLM (опционально) ─────────────
def pretty_fact_line(date_obj: pendulum.Date, region_name: str) -> str:
    raw = get_fact(date_obj, region_name)
    if not raw:
        return ""
    if not (ENABLE_FACT_LLM and USE_DAILY_LLM):
        return f"📚 {_escape_html(raw)}"

    cache_file = CACHE_DIR / f"fact_{date_obj.format('YYYY-MM-DD')}.txt"
    if cache_file.exists():
        try:
            txt = cache_file.read_text("utf-8").strip()
            if txt: return txt
        except Exception:
            pass

    system = (
        "Ты — один человек-эксперт: health-коуч, специалист по функциональной медицине и психолог, "
        "аккуратный редактор. Пиши по-дружески, коротко, без штампов."
    )
    prompt = (
        "Переформатируй этот факт в 1–2 очень короткие строки, дружелюбно и тепло. "
        "Не добавляй сведений, которых нет в факте. Одна уместная эмодзи в начале приветствуется. "
        f"\nФакт: «{raw}»."
    )

    try:
        txt = (gpt_complete(prompt=prompt, system=system, temperature=FACT_LLM_TEMP, max_tokens=160) or "").strip()
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        out = " ".join(lines[:2]) if lines else ""
        if not out or _looks_gibberish(out):
            return f"📚 {_escape_html(raw)}"
        out = _sanitize_line(out, 220)
        if not out.startswith(("📚","📖","📘","📜")):
            out = "📚 " + out
        cache_file.write_text(out, "utf-8")
        return out
    except Exception:
        return f"📚 {_escape_html(raw)}"

# ───────────── Persona-подпись (дружеская, 1 строка) ─────────────
def human_persona_line(kp: Optional[float], storm_region: Dict[str, Any], air_now: Dict[str, Any]) -> str:
    if isinstance(kp, (int, float)) and kp >= 5:
        return "💚 Мягкий режим: вода/магний, экраны дозируйте, ранний сон."
    if storm_region.get("warning"):
        return "🧥 Слои и дела под крышей, закладываем время и дышим 4-7-8."
    bad_air, _ = _is_air_bad(air_now or {})
    if bad_air:
        return "😮‍💨 Умерьте нагрузки на улице, проветривайте короче — фильтры в помощь."
    return ""

# ───────────── Микро-дайджест дня (опционально, LLM) ─────────────
def pretty_summary_line(mode: str,
                        storm_region: Dict[str, Any],
                        kp: Optional[float], ks: str,
                        air_now: Dict[str, Any],
                        schu_state: Optional[Dict[str, Any]] = None) -> str:
    if not (ENABLE_SUMMARY_LLM and USE_DAILY_LLM):
        return ""
    mode_key = (mode or "evening").split()[0]
    tz = pendulum.timezone(os.getenv("TZ", "Asia/Nicosia"))
    date_key = pendulum.today(tz).format("YYYY-MM-DD")
    cache_file = CACHE_DIR / f"sum_{mode_key}_{date_key}.txt"
    if cache_file.exists():
        try:
            txt = cache_file.read_text("utf-8").strip()
            if txt: return txt
        except Exception:
            pass

    storm_txt = storm_region.get("warning_text") or ("без шторма" if not storm_region.get("warning") else "штормовые факторы")
    kp_txt = "н/д"
    if isinstance(kp, (int, float)):
        kp_txt = f"{kp:.1f} ({ks})" if ks else f"{kp:.1f}"
    aqi = air_now.get("aqi")
    try: aqi_f = float(aqi) if aqi is not None else None
    except Exception: aqi_f = None
    air_lbl = _aqi_bucket_label(aqi_f) or "н/д"
    schu_lbl = (schu_state or {}).get("status") or ""

    system = ("Ты — один человек-эксперт: коуч по здоровью, функциональная медицина и психолог. "
              "Тон — дружеский, спокойный, без штампов.")
    prompt = (
        "Собери 1–2 очень короткие строки-дайджеста, начинай с «Коротко:». "
        "Только перефразируй данные, не добавляй новых фактов. "
        "Скажи, где стоит быть аккуратным, и добавь мягкий позитив.\n"
        f"Данные: шторм — {storm_txt}; Kp — {kp_txt}; воздух — {air_lbl}; Шуман — {schu_lbl}."
    )

    try:
        txt = (gpt_complete(prompt=prompt, system=system, temperature=SUMMARY_LLM_TEMP, max_tokens=160) or "").strip()
        lines = [l.strip() for l in txt.splitlines() if l.strip()]
        out = " ".join(lines[:2]) if lines else ""
        if not out or _looks_gibberish(out):
            return ""
        out = _sanitize_line(out, 220)
        cache_file.write_text(out, "utf-8")
        return out
    except Exception:
        return ""

# ───────────── hourly/ветер/давление ─────────────
def _pick(d: Dict[str, Any], *keys, default=None):
    for k in keys:
        if k in d: return d[k]
    return default

def _hourly_times(wm: Dict[str, Any]) -> List[pendulum.DateTime]:
    hourly = wm.get("hourly") or {}
    times = hourly.get("time") or hourly.get("time_local") or hourly.get("timestamp") or []
    out: List[pendulum.DateTime] = []
    for t in times:
        try: out.append(pendulum.parse(str(t)))
        except Exception: continue
    return out

def _nearest_index_for_day(times: List[pendulum.DateTime], date_obj: pendulum.Date, prefer_hour: int, tz: pendulum.Timezone) -> Optional[int]:
    if not times: return None
    target = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, prefer_hour, 0, tz=tz)
    best_i, best_diff = None, None
    for i, dt in enumerate(times):
        try: dt_local = dt.in_tz(tz)
        except Exception: dt_local = dt
        if dt_local.date() != date_obj: continue
        diff = abs((dt_local - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    return best_i

def _circular_mean_deg(deg_list: List[float]) -> Optional[float]:
    if not deg_list: return None
    x = sum(math.cos(math.radians(d)) for d in deg_list)
    y = sum(math.sin(math.radians(d)) for d in deg_list)
    if x == 0 and y == 0: return None
    ang = math.degrees(math.atan2(y, x))
    return (ang + 360.0) % 360.0

def pick_tomorrow_header_metrics(wm: Dict[str, Any], tz: pendulum.Timezone) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)
    tomorrow = pendulum.now(tz).add(days=1).date()
    spd_arr = _pick(hourly, "windspeed_10m","windspeed","wind_speed_10m","wind_speed", default=[])
    dir_arr = _pick(hourly, "winddirection_10m","winddirection","wind_dir_10m","wind_dir", default=[])
    prs_arr = hourly.get("surface_pressure", []) or hourly.get("pressure", [])
    if times:
        idx_noon = _nearest_index_for_day(times, tomorrow, 12, tz)
        idx_morn = _nearest_index_for_day(times, tomorrow, 6,  tz)
    else:
        idx_noon = idx_morn = None
    wind_ms = wind_dir = press_val = None; trend = "→"
    if idx_noon is not None:
        try: spd = float(spd_arr[idx_noon]) if idx_noon < len(spd_arr) else None
        except Exception: spd = None
        try: wdir = float(dir_arr[idx_noon]) if idx_noon < len(dir_arr) else None
        except Exception: wdir = None
        try: p_noon = float(prs_arr[idx_noon]) if idx_noon < len(prs_arr) else None
        except Exception: p_noon = None
        try: p_morn = float(prs_arr[idx_morn]) if idx_morn is not None and idx_morn < len(prs_arr) else None
        except Exception: p_morn = None
        wind_ms = kmh_to_ms(spd) if isinstance(spd, (int, float)) else None
        wind_dir = int(round(wdir)) if isinstance(wdir, (int, float)) else None
        press_val = int(round(p_noon)) if isinstance(p_noon, (int, float)) else None
        if isinstance(p_noon,(int,float)) and isinstance(p_morn,(int,float)):
            diff = p_noon - p_morn; trend = "↑" if diff>=0.3 else "↓" if diff<=-0.3 else "→"
    if wind_ms is None and times:
        idxs = [i for i,t in enumerate(times) if t.in_tz(tz).date()==tomorrow]
        if idxs:
            try: speeds=[float(spd_arr[i]) for i in idxs if i < len(spd_arr)]
            except Exception: speeds=[]
            try: dirs=[float(dir_arr[i]) for i in idxs if i < len(dir_arr)]
            except Exception: dirs=[]
            try: prs=[float(prs_arr[i]) for i in idxs if i < len(prs_arr)]
            except Exception: prs=[]
            if speeds: wind_ms = kmh_to_ms(sum(speeds)/len(speeds))
            mean_dir = _circular_mean_deg(dirs)
            wind_dir = int(round(mean_dir)) if mean_dir is not None else wind_dir
            if prs: press_val = int(round(sum(prs)/len(prs)))
    if wind_ms is None or wind_dir is None or press_val is None:
        cur = wm.get("current") or {}
        if wind_ms is None:
            spd = cur.get("windspeed") or cur.get("wind_speed")
            wind_ms = kmh_to_ms(spd) if isinstance(spd,(int,float)) else wind_ms
        if wind_dir is None:
            wdir = cur.get("winddirection") or cur.get("wind_dir")
            wind_dir = int(round(float(wdir))) if isinstance(wdir,(int,float)) else wind_dir
        if press_val is None and isinstance(cur.get("pressure"),(int,float)):
            press_val = int(round(float(cur["pressure"])))
    return wind_ms, wind_dir, press_val, trend

# === индексы на завтра и шторм-флаги ============================
def _tomorrow_hourly_indices(wm: Dict[str, Any], tz: pendulum.Timezone) -> List[int]:
    times = _hourly_times(wm); tom = pendulum.now(tz).add(days=1).date()
    idxs: List[int] = []
    for i, dt in enumerate(times):
        try:
            if dt.in_tz(tz).date() == tom: idxs.append(i)
        except Exception: pass
    return idxs

def storm_flags_for_tomorrow(wm: Dict[str, Any], tz: pendulum.Timezone) -> Dict[str, Any]:
    hourly = wm.get("hourly") or {}
    idxs = _tomorrow_hourly_indices(wm, tz)
    if not idxs: return {"warning": False}
    def _arr(*names, default=None):
        v = _pick(hourly, *names, default=default)
        return v if isinstance(v, list) else []
    def _vals(arr):
        out=[]
        for i in idxs:
            if i < len(arr):
                try: out.append(float(arr[i]))
                except Exception: pass
        return out
    speeds_kmh = _vals(_arr("windspeed_10m","windspeed","wind_speed_10m","wind_speed", default=[]))
    gusts_kmh  = _vals(_arr("windgusts_10m","wind_gusts_10m","wind_gusts", default=[]))
    rain_mm_h  = _vals(_arr("rain", default=[]))
    tprob      = _vals(_arr("thunderstorm_probability", default=[]))
    max_speed_ms = kmh_to_ms(max(speeds_kmh)) if speeds_kmh else None
    max_gust_ms  = kmh_to_ms(max(gusts_kmh))  if gusts_kmh  else None
    heavy_rain   = (max(rain_mm_h) >= 8.0) if rain_mm_h else False
    thunder      = (max(tprob) >= 60) if tprob else False
    reasons=[]
    if isinstance(max_speed_ms,(int,float)) and max_speed_ms >= 13: reasons.append(f"ветер до {max_speed_ms:.0f} м/с")
    if isinstance(max_gust_ms,(int,float)) and max_gust_ms >= 17: reasons.append(f"порывы до {max_gust_ms:.0f} м/с")
    if heavy_rain: reasons.append("сильный дождь")
    if thunder: reasons.append("гроза")
    return {"max_speed_ms": max_speed_ms, "max_gust_ms": max_gust_ms, "heavy_rain": heavy_rain,
            "thunder": thunder, "warning": bool(reasons),
            "warning_text": "⚠️ <b>Штормовое предупреждение</b>: " + ", ".join(reasons) if reasons else ""}

# ───────────── Air → вывод и советы ─────────────
def _aqi_bucket_label(aqi: Optional[float]) -> Optional[str]:
    if not isinstance(aqi, (int, float)): return None
    x = float(aqi)
    if x <= 50:   return "низкий"
    if x <= 100:  return "умеренный"
    if x <= 150:  return "высокий"
    return "очень высокий"

def _is_air_bad(air_now: Dict[str, Any]) -> tuple[bool, str]:
    aqi = air_now.get("aqi")
    try: aqi_f = float(aqi) if aqi is not None else None
    except Exception: aqi_f = None
    if aqi_f is None: return False, ""
    if aqi_f <= 50:   return False, "🟢 воздух в норме"
    if aqi_f <= 100:  return True,  "🟡 воздух умеренный — избегайте интенсивных тренировок на улице"
    return True, "🟠 воздух неблагоприятный — тренировки лучше перенести в помещение"

def _morning_combo_air_radiation_pollen(lat: float, lon: float) -> Optional[str]:
    air = get_air(lat, lon) or {}
    aqi = air.get("aqi")
    try: aqi_f = float(aqi) if aqi is not None else None
    except Exception: aqi_f = None
    lbl = _aqi_bucket_label(aqi_f)
    pm25 = air.get("pm25"); pm10 = air.get("pm10")
    try: pm25_i = int(round(float(pm25))) if pm25 is not None else None
    except Exception: pm25_i = None
    try: pm10_i = int(round(float(pm10))) if pm10 is not None else None
    except Exception: pm10_i = None
    dose_line = None
    data_rad = get_radiation(lat, lon) or {}
    dose = data_rad.get("dose")
    if isinstance(dose,(int,float)): dose_line = f"📟 {float(dose):.2f} μSv/h"
    p = get_pollen() or {}; risk = p.get("risk")
    parts = []
    aqi_part = f"AQI {int(round(aqi_f))}" if isinstance(aqi_f,(int,float)) else "AQI н/д"
    if lbl: aqi_part += f" ({lbl})"
    parts.append(aqi_part)
    pm_part = []
    if isinstance(pm25_i,int): pm_part.append(f"PM₂.₅ {pm25_i}")
    if isinstance(pm10_i,int): pm_part.append(f"PM₁₀ {pm10_i}")
    if pm_part: parts.append(" / ".join(pm_part))
    if dose_line: parts.append(dose_line)
    if isinstance(risk,str) and risk: parts.append(f"🌿 риск: {risk}")
    if not parts: return None
    return "🏭 " + " • ".join(parts)

# ───────────── «городская» строка ─────────────
def _city_detail_line(city: str, la: float, lo: float, tz_obj: pendulum.Timezone, include_sst: bool)\
        -> tuple[Optional[float], Optional[str]]:
    tz_name = tz_obj.name
    tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
    if tmax is None:
        st_fb = day_night_stats(la, lo, tz=tz_name) or {}
        tmax = st_fb.get("t_day_max"); tmin = st_fb.get("t_night_min")
    if tmax is None: return None, None
    tmin = tmin if tmin is not None else tmax

    wm  = get_weather(la, lo) or {}
    wcx = (wm.get("daily", {}) or {}).get("weathercode", [])
    wcx = wcx[1] if isinstance(wcx, list) and len(wcx) > 1 else None
    descx = code_desc(wcx) or "—"

    wind_ms, wind_dir, press_val, press_trend = pick_tomorrow_header_metrics(wm, tz_obj)
    storm = storm_flags_for_tomorrow(wm, tz_obj); gust = storm.get("max_gust_ms")

    parts = [f"{city}: {tmax:.1f}/{tmin:.1f} °C", f"{descx}"]
    if isinstance(wind_ms,(int,float)):
        wind_part = f"💨 {wind_ms:.1f} м/с"
        if isinstance(wind_dir,int): wind_part += f" ({compass(wind_dir)})"
        if isinstance(gust,(int,float)): wind_part += f" • порывы до {gust:.0f}"
        parts.append(wind_part)
    if isinstance(press_val,int): parts.append(f"🔹 {press_val} гПа {press_trend}")
    if include_sst:
        sst = get_sst_cached(la, lo)
        if isinstance(sst,(int,float)): parts.append(f"🌊 {sst:.1f}")
    return float(tmax), " • ".join(parts)

# ───────────── Водные активности ─────────────
def _deg_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)

def _cardinal(deg: Optional[float]) -> Optional[str]:
    if deg is None: return None
    dirs = ["N","NE","E","SE","S","SW","W","NW"]; idx = int((deg + 22.5) // 45) % 8
    return dirs[idx]

def _shore_class(city: str, wind_from_deg: Optional[float]) -> Tuple[Optional[str], Optional[str]]:
    if wind_from_deg is None: return None, None
    face_deg, src_label = _shore_face_for_city(city)
    if face_deg is None: return None, src_label
    diff = _deg_diff(wind_from_deg, face_deg)
    if diff <= 45:  return "onshore", src_label
    if diff >= 135: return "offshore", src_label
    return "cross", src_label

def _fetch_wave_for_tomorrow(lat: float, lon: float, tz_obj: pendulum.Timezone,
                             prefer_hour: int = 12) -> Tuple[Optional[float], Optional[float]]:
    if not requests: return None, None
    try:
        url = "https://marine-api.open-meteo.com/v1/marine"
        params = {"latitude": lat,"longitude": lon,"hourly": "wave_height,wave_period","timezone": tz_obj.name}
        r = requests.get(url, params=params, timeout=10); r.raise_for_status()
        j = r.json(); hourly = j.get("hourly") or {}
        times = [pendulum.parse(t) for t in (hourly.get("time") or []) if t]
        idx = _nearest_index_for_day(times, pendulum.now(tz_obj).add(days=1).date(), prefer_hour, tz_obj)
        if idx is None: return None, None
        h = hourly.get("wave_height") or []; p = hourly.get("wave_period") or []
        w_h = float(h[idx]) if idx < len(h) and h[idx] is not None else None
        w_t = float(p[idx]) if idx < len(p) and p[idx] is not None else None
        return w_h, w_t
    except Exception as e:
        logging.warning("marine fetch failed: %s", e)
        return None, None

def _wetsuit_hint(sst: Optional[float]) -> Optional[str]:
    if not isinstance(sst, (int, float)): return None
    t = float(sst)
    if t >= WSUIT_NONE:   return None
    if t >= WSUIT_SHORTY: return "гидрокостюм шорти 2 мм"
    if t >= WSUIT_32:     return "гидрокостюм 3/2 мм"
    if t >= WSUIT_43:     return "гидрокостюм 4/3 мм (боты)"
    if t >= WSUIT_54:     return "гидрокостюм 5/4 мм (боты, перчатки)"
    if t >= WSUIT_65:     return "гидрокостюм 5/4 мм + капюшон (боты, перчатки)"
    return "гидрокостюм 6/5 мм + капюшон (боты, перчатки)"

def _water_highlights(city: str, la: float, lo: float, tz_obj: pendulum.Timezone) -> Optional[str]:
    wm = get_weather(la, lo) or {}
    wind_ms, wind_dir, _, _ = pick_tomorrow_header_metrics(wm, tz_obj)
    wave_h, _ = _fetch_wave_for_tomorrow(la, lo, tz_obj)
    def _gust_at_noon(wm: Dict[str, Any], tz: pendulum.Timezone) -> Optional[float]:
        hourly = wm.get("hourly") or {}; times  = _hourly_times(wm)
        idx = _nearest_index_for_day(times, pendulum.now(tz).add(days=1).date(), 12, tz)
        arr = _pick(hourly, "windgusts_10m","wind_gusts_10m","wind_gusts", default=[])
        if idx is not None and idx < len(arr):
            try: return kmh_to_ms(float(arr[idx]))
            except Exception: return None
        return None
    gust = _gust_at_noon(wm, tz_obj); sst  = get_sst_cached(la, lo)
    wind_val = float(wind_ms) if isinstance(wind_ms,(int,float)) else None
    gust_val = float(gust)    if isinstance(gust,(int,float)) else None
    card = _cardinal(float(wind_dir)) if isinstance(wind_dir,(int,float)) else None
    shore, shore_src = _shore_class(city, float(wind_dir) if isinstance(wind_dir,(int,float)) else None)

    kite_good = False
    if wind_val is not None:
        if KITE_WIND_GOOD_MIN <= wind_val <= KITE_WIND_GOOD_MAX: kite_good = True
        if shore == "offshore": kite_good = False
        if gust_val and wind_val and (gust_val / max(wind_val, 0.1) > KITE_GUST_RATIO_BAD): kite_good = False
        if wave_h is not None and wave_h >= KITE_WAVE_WARN: kite_good = False

    sup_good = False
    if wind_val is not None:
        if (wind_val <= SUP_WIND_GOOD_MAX) and (wave_h is None or wave_h <= SUP_WAVE_GOOD_MAX): sup_good = True
        if shore == "offshore" and wind_val >= OFFSHORE_SUP_WIND_MIN: sup_good = False

    surf_good = False
    if wave_h is not None:
        if SURF_WAVE_GOOD_MIN <= wave_h <= SURF_WAVE_GOOD_MAX and (wind_val is None or wind_val <= SURF_WIND_MAX):
            surf_good = True

    goods: List[str] = []
    if kite_good: goods.append("Кайт/Винг/Винд")
    if sup_good:  goods.append("SUP")
    if surf_good: goods.append("Сёрф")
    if not goods: return None

    dir_part  = f" ({card}/{shore})" if card or shore else ""
    spot_part = f" @{shore_src}" if shore_src and shore_src not in (city, f"ENV:SHORE_FACE_{_env_city_key(city)}") else ""
    env_mark  = " (ENV)" if shore_src and shore_src.startswith("ENV:") else ""
    suit_txt  = _wetsuit_hint(sst); suit_part = f" • {suit_txt}" if suit_txt else ""
    return "🧜‍♂️ Отлично: " + "; ".join(goods) + spot_part + env_mark + dir_part + suit_part

# ───────────── вывод/советы (вечер) ─────────────
def build_conclusion(kp_val, ks, air_now, storm_region, schu_state) -> List[str]:
    out: List[str] = []
    pm25 = air_now.get("pm25"); pm10 = air_now.get("pm10"); aqi = air_now.get("aqi")
    emoji, smoke = smoke_index(pm25, pm10)
    aqi_part = f"{AIR_EMOJI.get('хороший','🟢')} AQI {int(round(aqi))}" if isinstance(aqi,(int,float)) else "AQI н/д"
    pm_part = " • ".join([f"PM₂.₅ {int(round(pm25))}" if isinstance(pm25,(int,float)) else "",
                          f"PM₁₀ {int(round(pm10))}"  if isinstance(pm10,(int,float)) else ""]).replace("  • ","").strip(" •")
    air_line = f"🏭 Воздух: {aqi_part}" + (f" • {pm_part}" if pm_part else "") + (f" • {emoji} дымка {smoke}" if smoke!="н/д" else "")
    out.append(air_line)
    if isinstance(kp_val,(int,float)):
        kp_color = "🟢" if kp_val < 5 else "🔴"
        shu_status = (schu_state or {}).get("status") or "колебания"
        out.append(f"🧲 {kp_color} Kp={kp_val:.1f} ({ks}) • 📡 Шуман — {shu_status}")
    else:
        out.append("🧲 Kp: н/д")
    bad_air, _ = _is_air_bad(air_now)
    verdict = "📌 День комфортный."
    if storm_region.get("warning"): verdict = "📌 День с оговорками: непогода."
    if isinstance(kp_val,(int,float)) and kp_val >= 5: verdict = "📌 День с оговорками: магнитные бури."
    if bad_air: verdict = "📌 День с оговорками: воздух не зелёный."
    out.append(verdict); return out

# ───────────── утро — сборка «человечного» блока ─────────────
def _collect_city_tmax_list(sea_pairs, other_pairs, tz_obj) -> List[Tuple[str, float]]:
    all_pairs = list(sea_pairs) + list(other_pairs)
    out: List[Tuple[str,float]] = []
    for city, (la, lo) in all_pairs:
        tmax, _ = _city_detail_line(city, la, lo, tz_obj, include_sst=False)
        if isinstance(tmax,(int,float)): out.append((city, float(tmax)))
    return out

def _format_all_cities_temps_compact(rows: List[Tuple[str, float]], max_cities: int = None) -> str:
    if not rows: return ""
    rows = sorted(rows, key=lambda x: x[1], reverse=True)
    if max_cities is None:
        try: max_cities = int(os.getenv("MORNING_CITY_TEMPS_MAX", "3"))
        except Exception: max_cities = 3
    top = rows[:max_cities]
    spread = f"{rows[-1][1]:.1f}–{rows[0][1]:.1f}°"
    short = ", ".join([f"{name} {t:.1f}°" for name, t in top])
    suffix = "…" if len(rows) > max_cities else ""
    return f"🌡️ По городам: {short}{suffix} (диапазон {spread})"

# ───────────── сообщение ─────────────
def build_message(region_name: str,
                  sea_label: str, sea_cities,
                  other_label: str, other_cities,
                  tz: Union[pendulum.Timezone, str],
                  mode: Optional[str] = None) -> str:

    tz_obj = _as_tz(tz)
    mode = (mode or os.getenv("POST_MODE") or os.getenv("MODE") or "evening").lower()
    is_morning = mode.startswith("morn")

    sea_pairs   = _iter_city_pairs(sea_cities)
    other_pairs = _iter_city_pairs(other_cities)

    P: List[str] = []
    today = pendulum.today(tz_obj); tom = today.add(days=1)
    # Заголовок без эмодзи
    P.append(f"<b>{region_name}: погода на завтра ({tom.format('DD.MM.YYYY')})</b>")

    wm_region = get_weather(CY_LAT, CY_LON) or {}
    storm_region = storm_flags_for_tomorrow(wm_region, tz_obj)

    # === УТРО ===
    if is_morning:
        rows = _collect_city_tmax_list(sea_pairs, other_pairs, tz_obj)
        warm = max(rows, key=lambda x: x[1]) if rows else None
        cool = min(rows, key=lambda x: x[1]) if rows else None

        greeting = "👋 Доброе утро!"
        if warm and cool:
            spread = ""
            if abs(warm[1] - cool[1]) >= 0.5:
                spread = f" (диапазон {cool[1]:.0f}–{warm[1]:.0f}°)"
            greeting += (
                f" Сегодня теплее всего — {_ru_city(warm[0])} ({warm[1]:.0f}°), "
                f"прохладнее — {_ru_city(cool[0])} ({cool[1]:.0f}°){spread}."
            )
        P.append(greeting)

        # Без длинного списка «🌡️ По городам …»

        if storm_region.get("warning"):
            P.append(storm_region["warning_text"] + " Берегите планы и закладывайте время.")

        la_sun, lo_sun = _choose_sun_coords(sea_pairs, other_pairs)
        sun_line = sun_line_for_mode(mode, tz_obj, la_sun, lo_sun)
        if sun_line: P.append(sun_line)

        combo = _morning_combo_air_radiation_pollen(CY_LAT, CY_LON)
        if combo:
            P.append(combo)
            air_now = get_air(CY_LAT, CY_LON) or {}
            bad_air, tip = _is_air_bad(air_now)
            if bad_air and tip:
                P.append(f"ℹ️ {tip}")

        kp_tuple = get_kp() or (None, "н/д", None, "n/d")
        try: kp, ks, kp_ts, _ = kp_tuple
        except Exception:
            kp = kp_tuple[0] if isinstance(kp_tuple,(list,tuple)) and len(kp_tuple)>0 else None
            ks = kp_tuple[1] if isinstance(kp_tuple,(list,tuple)) and len(kp_tuple)>1 else "н/д"
            kp_ts = None
        age_txt = ""
        if isinstance(kp_ts,int) and kp_ts>0:
            try:
                age_min = int((pendulum.now("UTC").int_timestamp - kp_ts) / 60)
                age_txt = f", 🕓 {age_min // 60} ч назад" if age_min > 180 else (f", {age_min} мин назад" if age_min >= 0 else "")
            except Exception: age_txt = ""

        sw = get_solar_wind() or {}
        v, n = sw.get("speed_kms"), sw.get("density"); wind_status = sw.get("status", "н/д")
        parts_sw = []
        if isinstance(v,(int,float)): parts_sw.append(f"v {v:.0f} км/с")
        if isinstance(n,(int,float)): parts_sw.append(f"n {n:.1f} см⁻³")
        sw_chunk = (", ".join(parts_sw) + (f" — {wind_status}" if parts_sw else "")) if parts_sw or wind_status else "н/д"

        # цветовой маркер Kp
        kp_mark = ""
        if isinstance(kp,(int,float)):
            if kp >= 5: kp_mark = "🔴 "
            elif kp < 4: kp_mark = "🟢 "

        if isinstance(kp,(int,float)):
            P.append(f"{kp_mark}🧲 Kp={kp:.1f} ({ks}{age_txt}) • 🌬️ {sw_chunk}")
            try:
                ws = (wind_status or "")
                if kp >= 5 or ("спокой" in ws.lower() or "calm" in ws.lower()):
                    P.append("ℹ️ Kp — глобальный индекс за 3 ч.")
            except Exception:
                pass
        else:
            P.append(f"🧲 Kp: н/д • 🌬️ {sw_chunk}")

        # — микро-дайджест и persona-подпись (опционально)
        try:
            air_now2 = get_air(CY_LAT, CY_LON) or {}
            sum_line = pretty_summary_line("morning", storm_region, kp if isinstance(kp,(int,float)) else None, ks, air_now2)
            if sum_line: P.append(sum_line)
            persona = human_persona_line(kp if isinstance(kp,(int,float)) else None, storm_region, air_now2)
            if persona: P.append(persona)
        except Exception:
            pass

        # тёплая концовка
        P.append("Хорошего дня и бережного темпа 😊")

        return "\n".join(P)

    # === ВЕЧЕР ===
    if storm_region.get("warning"):
        P.append(storm_region["warning_text"]); P.append("———")

    sea_names = {name for name, _ in sea_pairs}
    all_rows: List[tuple[float, str]] = []
    for city, (la, lo) in list(sea_pairs) + list(other_pairs):
        include_sst = city in sea_names or city in SHORE_PROFILE
        tmax, line = _city_detail_line(city, la, lo, tz_obj, include_sst=include_sst)
        if tmax is not None and line:
            if include_sst:
                try:
                    hl = _water_highlights(city, la, lo, tz_obj)
                    if hl: line = line + f"\n   {hl}"
                except Exception: pass
            all_rows.append((float(tmax), line))
    if all_rows:
        P.append("🏙 <b>Города</b>")
        all_rows.sort(key=lambda x: x[0], reverse=True)
        medals = ["🥵","😎","😌","🥶"]
        for i, (_, text) in enumerate(all_rows):
            med = medals[i] if i < len(medals) else "•"
            P.append(f"{med} {text}")
        P.append("———")

    la_sun, lo_sun = _choose_sun_coords(sea_pairs, other_pairs)
    sun_line = sun_line_for_mode(mode, tz_obj, la_sun, lo_sun)
    if sun_line: P.append(sun_line)

    schu_state = {} if DISABLE_SCHUMANN else get_schumann_with_fallback()
    if not DISABLE_SCHUMANN:
        P.append(schumann_line(schu_state)); P.append("———")

    tz_nic = pendulum.timezone("Asia/Nicosia")
    date_for_astro = pendulum.today(tz_nic).add(days=1)
    P.append(build_astro_section(date_local=date_for_astro, tz_local="Asia/Nicosia"))
    P.append("———")

    P.append("📜 <b>Вывод</b>")
    air_now = get_air(CY_LAT, CY_LON) or {}
    kp_tuple = get_kp() or (None, "н/д", None, "n/d")
    try: kp_val, ks, _, _ = kp_tuple
    except Exception:
        kp_val = kp_tuple[0] if isinstance(kp_tuple,(list,tuple)) and len(kp_tuple)>0 else None
        ks = kp_tuple[1] if isinstance(kp_tuple,(list,tuple)) and len(kp_tuple)>1 else "н/д"
    P.extend(build_conclusion(kp_val, ks, air_now, storm_region, schu_state))

    # — микро-дайджест и persona-подпись (опционально)
    try:
        air_now2 = get_air(CY_LAT, CY_LON) or {}
        sum_line = pretty_summary_line("evening", storm_region, kp_val if isinstance(kp_val,(int,float)) else None, ks, air_now2, schu_state)
        if sum_line: P.append(sum_line)
        persona = human_persona_line(kp_val if isinstance(kp_val,(int,float)) else None, storm_region, air_now2)
        if persona: P.append(persona)
    except Exception:
        pass

    P.append("———")
    P.append(pretty_fact_line(tom, region_name))
    return "\n".join(P)

# ───────────── отправка ─────────────
async def send_common_post(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
    mode: Optional[str] = None,
) -> None:
    msg = build_message(region_name, sea_label, sea_cities, other_label, other_cities, tz, mode=mode)
    await bot.send_message(chat_id=chat_id, text=msg, parse_mode=constants.ParseMode.HTML, disable_web_page_preview=True)

async def main_common(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
    mode: Optional[str] = None,
) -> None:
    await send_common_post(bot, chat_id, region_name, sea_label, sea_cities, other_cities, tz, mode)

__all__ = [
    "build_message","send_common_post","main_common",
    "schumann_line","get_schumann_with_fallback",
    "pick_tomorrow_header_metrics","storm_flags_for_tomorrow",
    "pretty_fact_line","pretty_summary_line","human_persona_line",
]
