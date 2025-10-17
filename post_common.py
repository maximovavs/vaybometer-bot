#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Кипр/универсальный).

Утро: «человечный» обзор на СЕГОДНЯ, + 🌇 закат СЕГОДНЯ, Kp как в world_*.
Вечер: единый список городов на ЗАВТРА, + 🌅 рассвет ЗАВТРА.
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
from air          import get_air, get_sst
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

SAFE_TIPS_FALLBACKS = {
    "здоровый день": ["🚶 30–40 мин лёгкой активности.", "🥤 Больше воды и короткие паузы.", "😴 7–9 часов сна — приоритет."],
    "плохая погода": ["🧥 Слои + непромокаемая куртка.", "🌧 Перенесите дела под крышу.", "🚗 Заложите время на дорогу."],
    "магнитные бури": ["🧘 Берегите нервную систему.", "💧 Пейте воду, больше магния/калия.", "📵 Меньше экранов вечером."],
    "плохой воздух": ["😮‍💨 Сократите нагрузку на улице.", "🪟 Проветривайте короче, фильтры в помощь.", "🏃 Тренировки — в помещении."],
    "волны Шумана": ["🧘 Спокойный темп дня.", "🍵 Лёгкая еда, тёплые напитки.", "😴 Ранний отход ко сну."],
}

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

# ───────────── утилиты ─────────────
def _as_tz(tz: Union[pendulum.Timezone, str]) -> pendulum.Timezone:
    return pendulum.timezone(tz) if isinstance(tz, str) else tz

WMO_DESC = {0:"☀️ ясно",1:"⛅ ч.обл",2:"☁️ обл",3:"🌥 пасм",45:"🌫 туман",48:"🌫 изморозь",51:"🌦 морось",61:"🌧 дождь",71:"❄️ снег",95:"⛈ гроза"}
def code_desc(c: Any) -> Optional[str]:
    try: return WMO_DESC.get(int(c))
    except Exception: return None

def _iter_city_pairs(cities) -> list[tuple[str, tuple[float, float]]]:
    """
    Нормализует разные формы:
      - {"City": (lat, lon)}
      - [("City", (lat, lon)), ("Town", (lat, lon))]
      - [("City", lat, lon)]
      - генераторы/итераторы
    Игнорирует строки и битые записи.
    """
    out: list[tuple[str, tuple[float, float]]] = []

    if not cities:
        return out

    # dict -> items
    if isinstance(cities, dict):
        for k, v in list(cities.items()):
            try:
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    la, lo = float(v[0]), float(v[1])
                    out.append((str(k), (la, lo)))
            except Exception:
                continue
        return out

    # одиночные строки — точно не города с координатами
    if isinstance(cities, str):
        return out

    # общий случай: итерируем
    try:
        iterable = list(cities)
    except Exception:
        return out

    for item in iterable:
        try:
            # ("City",(lat,lon))
            if isinstance(item, (list, tuple)) and len(item) == 2 and isinstance(item[1], (list, tuple)) and len(item[1]) == 2:
                name = str(item[0]); la, lo = float(item[1][0]), float(item[1][1])
                out.append((name, (la, lo)))
                continue

            # ("City", lat, lon)
            if isinstance(item, (list, tuple)) and len(item) == 3:
                name = str(item[0]); la, lo = float(item[1]), float(item[2])
                out.append((name, (la, lo)))
                continue

            # строка — пропускаем
            if isinstance(item, str):
                continue
        except Exception:
            continue

    return out

# ───────────── Рассвет/закат — weather → astral → NOAA ─────────────
def _parse_iso_to_tz(s: str, tz: pendulum.tz.timezone.Timezone) -> Optional[pendulum.DateTime]:
    try: return pendulum.parse(str(s)).in_tz(tz)
    except Exception: return None

def _noaa_dt_from_utc_fraction(date_obj: pendulum.Date, ut_hours: float, tz: pendulum.tz.timezone.Timezone):
    h = int(ut_hours); m = int(round((ut_hours - h) * 60))
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
        RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L)))); RA = (RA + 360.0) % 360.0
        Lq = (math.floor(L/90.0))*90.0; RAq = (math.floor(RA/90.0))*90.0
        RA += (Lq - RAq); RA /= 15.0
        sinDec = 0.39782 * math.sin(math.radians(L)); cosDec = math.cos(math.asin(sinDec))
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

# ───────────── Kp (NOAA как в world_*, кеш 10 мин) ─────────────
_KP_CACHE = {"ts": 0, "val": None, "status": "н/д", "trend": "—", "obs_ts": None}

def _kp_status_ru(kp: Optional[float]) -> str:
    if kp is None: return "н/д"
    k = float(kp)
    if k < 2.0: return "спокойно"
    if k < 3.0: return "спокойно–умеренно"
    if k < 4.0: return "умеренно"
    if k < 5.0: return "активно"
    if k < 6.0: return "шторм-наблюдение"
    return "буря"

def fetch_kp_latest_world(ttl_sec: int = 600) -> Tuple[Optional[float], str, Optional[int], str]:
    """
    Возвращает (kp_value, status_ru, obs_ts_utc, trend_emoji).
    Источник: SWPC NOAA 'noaa-planetary-k-index.json' (как в world_*).
    """
    if not requests:
        return None, "н/д", None, "—"
    now = pendulum.now("UTC").int_timestamp
    if (_KP_CACHE["ts"] and now - int(_KP_CACHE["ts"]) <= ttl_sec):
        return _KP_CACHE["val"], _KP_CACHE["status"], _KP_CACHE["obs_ts"], _KP_CACHE["trend"]

    val = None; status = "н/д"; trend = "—"; obs_ts = None
    try:
        url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
        r = requests.get(url, timeout=20)
        r.raise_for_status()
        data = r.json()
        rows = [row for row in data if isinstance(row, list) and len(row) >= 2][1:]
        if rows:
            last = rows[-1]
            prev = rows[-2] if len(rows) >= 2 else None
            try: val = float(last[1])
            except Exception: val = None
            if prev is not None:
                try:
                    pv = float(prev[1])
                    if val is not None and pv is not None:
                        if val > pv + 0.1: trend = "↗"
                        elif val < pv - 0.1: trend = "↘"
                        else: trend = "→"
                except Exception:
                    pass
            try:
                ts_str = str(last[0])  # 'YYYY-MM-DD HH:MM:SS'
                obs_ts = pendulum.parse(ts_str, tz="UTC").int_timestamp
            except Exception:
                obs_ts = None
            status = _kp_status_ru(val)
    except Exception as e:
        logging.warning("Kp fetch failed: %s", e)

    _KP_CACHE.update({"ts": now, "val": val, "status": status, "trend": trend, "obs_ts": obs_ts})
    return val, status, obs_ts, trend

# ───────────── Шуман (ускоренная версия; можно отключить через env) ─────────────
def _gentle_interpretation(code: str) -> str:
    if code == "green":  return "Волны Шумана близки к норме — организм реагирует как на обычный день."
    if code == "yellow": return "Заметны колебания — возможна лёгкая чувствительность."
    return "Сильные отклонения — снижайте перегрузки и наблюдайте самочувствие."

def get_schumann_with_fallback() -> Dict[str, Any]:
    # лёгкая оболочка: можно подменить модулем schumann, если есть
    try:
        import schumann
        if hasattr(schumann, "get_schumann"):
            payload = schumann.get_schumann() or {}
            if isinstance(payload, dict): return payload
    except Exception:
        pass
    # дефолт
    return {"status": "🟡 колебания", "status_code": "yellow", "trend": "→", "trend_text": "стабильно",
            "freq": None, "amp": None, "h7_text": "H7: — нет данных", "cached": True,
            "interpretation": _gentle_interpretation("yellow")}

def schumann_line(s: Dict[str, Any]) -> str:
    freq = s.get("freq"); amp = s.get("amp")
    trend_text = s.get("trend_text") or "стабильно"
    status_lbl = s.get("status") or "🟡 колебания"
    h7line = s.get("h7_text") or "H7: — нет данных"
    stale = " ⏳ нет свежих чисел" if s.get("cached") else ""
    if not isinstance(freq, (int, float)) and not isinstance(amp, (int, float)):
        return f"{status_lbl}{stale} • тренд: {trend_text} • {h7line}\n{_gentle_interpretation('yellow')}"
    fstr = f"{freq:.2f}" if isinstance(freq, (int, float)) else "н/д"
    astr = f"{amp:.2f} pT" if isinstance(amp, (int, float)) else "н/д"
    return f"{status_lbl}{stale} • Шуман: {fstr} Гц / {astr} • тренд: {trend_text} • {h7line}\n{_gentle_interpretation(s.get('status_code','yellow'))}"

# ───────────── Safecast (необязательный блок; оставляем как есть) ─────────────
def radiation_line(lat: float, lon: float) -> Optional[str]:
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if isinstance(dose,(int,float)):
        em,lbl = ("🟢","низкий") if dose<=0.15 else (("🟡","повышенный") if dose<=0.30 else ("🔴","высокий"))
        return f"{em} Радиация: {dose:.3f} μSv/h ({lbl})"
    return None

# ───────────── Астроблок (минимально) ─────────────
def build_astro_section(date_local: Optional[pendulum.Date] = None, tz_local: str = "Asia/Nicosia") -> str:
    # оставляем простой аккуратный блок — как было
    try:
        tz = pendulum.timezone(tz_local)
        date_local = date_local or pendulum.today(tz)
        cal = json.loads(Path("lunar_calendar.json").read_text("utf-8"))
        rec = cal.get("days", {}).get(date_local.format("YYYY-MM-DD"), {})
    except Exception:
        rec = {}
    phase = (rec.get("phase_name") or rec.get("phase") or "Луна").strip()
    sign  = rec.get("sign") or ""
    bullets = [f"• Фаза: {phase}", f"• Знак: {sign}"] if sign else [f"• Фаза: {phase}"]
    return "🌌 <b>Астрособытия</b>\n" + "\n".join(bullets[:3])

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

# === индексы и шторм-флаги ============================
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

# ───────────── «городская» строка (для вечера/завтра) ─────────────
def _city_detail_line(city: str, la: float, lo: float, tz_obj: pendulum.Timezone, include_sst: bool)\
        -> tuple[Optional[float], Optional[str]]:
    tz_name = tz_obj.name
    tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
    if tmax is None:
        st_fb = day_night_stats(la, lo, tz=tz_name) or {}
        tmax = st_fb.get("t_day_max"); tmin = st_fb.get("t_night_min")
    if tmax is None: return None, None

    wm  = get_weather(la, lo) or {}
    wcx = (wm.get("daily", {}) or {}).get("weathercode", [])
    wcx = wcx[1] if isinstance(wcx, list) and len(wcx) > 1 else None
    descx = code_desc(wcx) or "—"

    wind_ms, wind_dir, press_val, press_trend = pick_tomorrow_header_metrics(wm, tz_obj)
    storm = storm_flags_for_tomorrow(wm, tz_obj); gust = storm.get("max_gust_ms")

    parts = [f"{city}: {tmax:.1f}/{(tmin if tmin is not None else tmax):.1f} °C", f"{descx}"]
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

# ───────────── вспомогательные «человечные» строки ─────────────
def _is_air_bad(air_now: Dict[str, Any]) -> tuple[bool, str]:
    aqi = air_now.get("aqi")
    try: aqi_f = float(aqi) if aqi is not None else None
    except Exception: aqi_f = None
    if aqi_f is None: return False, ""
    if aqi_f <= 50:   return False, "🟢 воздух в норме"
    if aqi_f <= 100:  return True,  "🟡 воздух умеренный — избегайте интенсивных тренировок на улице"
    return True, "🟠 воздух неблагоприятный — тренировки лучше перенести в помещение"

def pretty_fact_line(date_obj: pendulum.Date, region_name: str) -> str:
    try:
        txt = get_fact(date_obj, region_name) or ""
    except Exception:
        txt = ""
    if not txt: return ""
    txt = re.sub(r"\s+", " ", txt).strip()
    if len(txt) > 160:
        txt = txt[:159].rsplit(" ", 1)[0] + "…"
    return f"📚 Факт дня: {txt}"

def pretty_summary_line(mode: str, storm: Dict[str,Any], kp: Optional[float], ks: str, air_now: Dict[str,Any], schu: Dict[str,Any]|None=None) -> str:
    bits=[]
    # воздух
    bad_air, _tip = _is_air_bad(air_now); bits.append("воздух ок" if not bad_air else "воздух осторожно")
    # шторм
    bits.append("без шторма" if not storm.get("warning") else "непогода")
    # kp
    if isinstance(kp,(int,float)): bits.append(f"Kp {ks}")
    return "🔎 Итого: " + " • ".join(bits)

def human_persona_line(kp: Optional[float], storm: Dict[str,Any], air_now: Dict[str,Any]) -> str:
    if isinstance(kp,(int,float)) and kp >= 5:
        return "✅ Режим: вода/магний, спокойные тренировки, ранний сон."
    if storm.get("warning"):
        return "✅ Режим: дождевик и запас времени на дорогу."
    bad_air,_ = _is_air_bad(air_now)
    if bad_air:
        return "✅ Режим: прогулки короче, тренировки — в помещении."
    return "✅ Режим: вода и короткие прогулки."

# ───────────── сообщение ─────────────
def build_message(region_name: str,
                  sea_label: str, sea_cities,
                  other_label: str, other_cities,
                  tz: Union[pendulum.Timezone, str],
                  mode: Optional[str] = None) -> str:

    # Защита от перепутанных аргументов (tz ←→ mode)
    if isinstance(tz, str) and tz.strip().lower() in ("morning", "evening", "am", "pm"):
        logging.warning("build_message: получен tz='%s' (похоже на mode). Перекладываю в mode.", tz)
        mode = tz
        tz = os.getenv("TZ", "Asia/Nicosia")

    logging.info("build_message: mode=%s, tz=%s",
                 (mode or "∅"),
                 (tz if isinstance(tz, str) else getattr(tz, 'name', 'obj')))

    tz_obj = _as_tz(tz)
    mode = (mode or os.getenv("POST_MODE") or os.getenv("MODE") or "evening").lower()
    is_morning = mode.startswith("morn")

    # Нормализуем входные города максимально терпимо
    sea_pairs   = _iter_city_pairs(sea_cities)
    other_pairs = _iter_city_pairs(other_cities)
    all_pairs   = list(sea_pairs) + list(other_pairs)

    P: List[str] = []
    today = pendulum.today(tz_obj); tom = today.add(days=1)

    # Заголовок корректный
    label = "сегодня" if is_morning else "завтра"
    date_label = (today if is_morning else tom).format("DD.MM.YYYY")
    P.append(f"<b>{region_name}: погода на {label} ({date_label})</b>")

    wm_region = get_weather(CY_LAT, CY_LON) or {}

    # маленький локальный расчёт шторм-флагов для СЕГОДНЯ/ЗАВТРА
    def _storm_flags_for_day_offset(day_offset: int) -> Dict[str, Any]:
        hourly = wm_region.get("hourly") or {}
        times  = _hourly_times(wm_region)
        target_date = (today if day_offset == 0 else tom).date()
        idxs: List[int] = []
        for i, dt in enumerate(times):
            try:
                if dt.in_tz(tz_obj).date() == target_date:
                    idxs.append(i)
            except Exception:
                pass
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

    storm_today    = _storm_flags_for_day_offset(0)
    storm_tomorrow = _storm_flags_for_day_offset(1)

    # === УТРО (СЕГОДНЯ) ===
    if is_morning:
        # tmax СЕГОДНЯ
        rows: List[Tuple[str, float]] = []
        for city, (la, lo) in all_pairs:
            st = day_night_stats(la, lo, tz=tz_obj.name) or {}
            tmax = st.get("t_day_max")
            if isinstance(tmax, (int, float)):
                rows.append((city, float(tmax)))

        warm = max(rows, key=lambda x: x[1]) if rows else None
        cool = min(rows, key=lambda x: x[1]) if rows else None

        greeting = "👋 Доброе утро!"
        if warm and cool:
            spread = ""
            if abs(warm[1] - cool[1]) >= 0.5:
                spread = f" (диапазон {cool[1]:.0f}–{warm[1]:.0f}°)"
            greeting += (
                f" Сегодня теплее всего — {warm[0]} ({warm[1]:.0f}°), "
                f"прохладнее — {cool[0]} ({cool[1]:.0f}°){spread}."
            )
        P.append(greeting)

        # Факт дня — короткий хук
        try:
            fact_line = pretty_fact_line(today, region_name)
            if fact_line: P.append(fact_line)
        except Exception:
            pass

        if storm_today.get("warning"):
            P.append(storm_today["warning_text"] + " Берегите планы и закладывайте время.")

        la_sun, lo_sun = _choose_sun_coords(sea_pairs, other_pairs)
        sun_line = sun_line_for_mode(mode, tz_obj, la_sun, lo_sun)  # Закат сегодня
        if sun_line: P.append(sun_line)

        combo = _morning_combo_air_radiation_pollen(CY_LAT, CY_LON)
        if combo:
            P.append(combo)
            air_now = get_air(CY_LAT, CY_LON) or {}
            bad_air, tip = _is_air_bad(air_now)
            if bad_air and tip: P.append(f"ℹ️ {tip}")

        # Kp — как в world_*
        kp, ks, kp_ts, kp_trend = fetch_kp_latest_world()
        age_txt = ""
        if isinstance(kp_ts,int) and kp_ts>0:
            try:
                age_min = int((pendulum.now("UTC").int_timestamp - kp_ts) / 60)
                age_txt = f", {age_min // 60} ч назад" if age_min >= 60 else (f", {age_min} мин назад" if age_min >= 0 else "")
            except Exception: age_txt = ""
        if isinstance(kp,(int,float)):
            P.append(f"🧲 Kp≈{kp:.1f} ({ks}{age_txt})")
        else:
            P.append("🧲 Kp: н/д")

        # солнечный ветер — отдельной строкой, без «спокойно»
        sw = {}  # можно подключить ваш get_solar_wind(), если нужен
        try:
            from air import get_solar_wind  # type: ignore
            sw = get_solar_wind() or {}
        except Exception:
            sw = {}
        v, n = sw.get("speed_kms"), sw.get("density")
        parts_sw = []
        if isinstance(v,(int,float)): parts_sw.append(f"v≈{v:.0f} км/с")
        if isinstance(n,(int,float)): parts_sw.append(f"n≈{n:.1f}")
        if parts_sw: P.append("🌬️ Солнечный ветер: " + ", ".join(parts_sw))

        # микро-дайджест и «persona»
        try:
            air_now2 = get_air(CY_LAT, CY_LON) or {}
            sum_line = pretty_summary_line("morning", storm_today, kp if isinstance(kp,(int,float)) else None, ks, air_now2)
            if sum_line: P.append(sum_line)
            persona = human_persona_line(kp if isinstance(kp,(int,float)) else None, storm_today, air_now2)
            if persona: P.append(persona)
        except Exception:
            pass

        P.append("Хорошего дня и бережного темпа 😊")
        return "\n".join(P)

    # === ВЕЧЕР (ЗАВТРА) ===
    storm_region = storm_tomorrow

    if storm_region.get("warning"):
        P.append(storm_region["warning_text"]); P.append("———")

    sea_names = {name for name, _ in sea_pairs}
    all_rows_out: List[tuple[float, str]] = []
    for city, (la, lo) in list(sea_pairs) + list(other_pairs):
        include_sst = city in sea_names or city in SHORE_PROFILE
        tmax, line = _city_detail_line(city, la, lo, tz_obj, include_sst=include_sst)
        if tmax is not None and line:
            all_rows_out.append((float(tmax), line))
    if all_rows_out:
        P.append("🏙 <b>Города</b>")
        all_rows_out.sort(key=lambda x: x[0], reverse=True)
        medals = ["🥵","😎","😌","🥶"]
        for i, (_, text) in enumerate(all_rows_out):
            med = medals[i] if i < len(medals) else "•"
            P.append(f"{med} {text}")
        P.append("———")

    la_sun, lo_sun = _choose_sun_coords(sea_pairs, other_pairs)
    sun_line = sun_line_for_mode(mode, tz_obj, la_sun, lo_sun)  # Рассвет завтра
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
    kp_val, ks, _, _ = fetch_kp_latest_world()
    P.extend(build_conclusion(kp_val, ks, air_now, storm_region, schu_state))

    # микро-дайджест и «persona»
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

# ───────────── комбо-строка утро: воздух/пыльца/радиация ─────────────
def _aqi_bucket_label(aqi: Optional[float]) -> Optional[str]:
    if not isinstance(aqi, (int, float)): return None
    x = float(aqi)
    if x <= 50:   return "низкий"
    if x <= 100:  return "умеренный"
    if x <= 150:  return "высокий"
    return "очень высокий"

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
    if isinstance(risk,str) and risk: parts.append(f"🌿 пыльца {risk}")
    if not parts: return None
    return "🏭 " + " • ".join(parts)

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

# ДОЛЖНО БЫТЬ
await send_common_post(bot, chat_id, region_name, sea_label, sea_cities, other_label, other_cities, tz, mode)
__all__ = [
    "build_message","send_common_post","main_common",
    "schumann_line","get_schumann_with_fallback",
    "pick_tomorrow_header_metrics","storm_flags_for_tomorrow",
    "fetch_kp_latest_world",
]
