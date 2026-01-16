#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (Кипр/универсальный).

Утро: человечный обзор «на СЕГОДНЯ» + 🌇 закат сегодня.
Вечер: два списка «на ЗАВТРА» (морские/континентальные) + 🌅 рассвет завтра.
Астроблок — короткий, «по-человечески». Космопогода/воздух — только утром.

Важно:
- Kp как в мировом чате (NOAA) — USE_WORLD_KP=1.
- Защита от перепутанных аргументов tz/mode.
- Терпимый парсер входных списков городов.
- ASTRO_OFFSET — сдвиг даты для астроблока (в днях, по умолчанию 0).
"""

from __future__ import annotations
import os, re, json, html, asyncio, logging, math, datetime as dt, random, imghdr
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Union

import pendulum
from telegram import Bot, constants

from utils        import compass, get_fact, kmh_to_ms, smoke_index
from weather      import get_weather, fetch_tomorrow_temps, day_night_stats
from air          import get_air, get_sst, get_solar_wind
from pollen       import get_pollen
from radiation    import get_radiation
from gpt          import gpt_blurb, gpt_complete
from world_en.imagegen import generate_astro_image
from image_prompt_cy   import build_cyprus_evening_prompt

try:
    import requests  # type: ignore
except Exception:
    requests = None  # type: ignore

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────── базовые константы ──────────────────────────
CY_LAT, CY_LON = 34.707, 33.022
PRIMARY_CITY_NAME = os.getenv("PRIMARY_CITY", "Limassol")

CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True, parents=True)
USE_DAILY_LLM = os.getenv("DISABLE_LLM_DAILY", "").strip().lower() not in ("1", "true", "yes", "on")

# Kp-источник «как в мировом чате»
USE_WORLD_KP = os.getenv("USE_WORLD_KP", "1").strip().lower() in ("1", "true", "yes", "on")

# ────────────────────────── HTML/utils ──────────────────────────
def _escape_html(s: str) -> str:
    return html.escape(str(s), quote=False)


def _sanitize_line(s: str, max_len: int = 140) -> str:
    s = " ".join(str(s).split())
    s = re.sub(r"(.)\1{3,}", r"\1\1\1", s)
    return (_escape_html(s[: max_len - 1]) + "…") if len(s) > max_len else _escape_html(s)


def _looks_gibberish(s: str) -> bool:
    if re.search(r"(.)\1{5,}", s):
        return True
    letters = re.findall(r"[A-Za-zА-Яа-яЁё]", s)
    return len(set(letters)) <= 2 and len("".join(letters)) >= 10


# ────────────────────────── русские названия городов ──────────────────────────
_RU_CITIES_MAP = {
    "limassol": "Лимассол",
    "lemessos": "Лимассол",
    "larnaca": "Ларнака",
    "larnaka": "Ларнака",
    "nicosia": "Никосия",
    "lefkosia": "Никосия",
    "paphos": "Пафос",
    "pafos": "Пафос",
    "ayia napa": "Айя-Напа",
    "agia napa": "Айя-Напа",
    "aya napa": "Айя-Напа",
    "protaras": "Протарас",
    "troodos": "Тродос",
    "coral bay": "Корал-Бэй",
    "cape greco": "Кейп-Греко",
    "latchi": "Лачи",
    "governor's beach": "Пляж Говернора",
    "lady's mile": "Ледис-Майл",
    "curium": "Куриум",
    "kourion": "Куриум",
    "paramali": "Парамали",
    "pissouri": "Писсури",
    "avdimou": "Авдиму",
    "mazotos": "Мазотос",
    "kiti": "Кити",
    "mackenzie": "Маккензи",
    "ayia napa (nissi)": "Айя-Напа (Нисси)",
    "paphos (alykes)": "Пафос (Аликис)",
    "cape greco (konnos)": "Кейп-Греко (Коннос)",
}


def _ru_city(name: str) -> str:
    if not name:
        return name
    key = re.sub(r"\s+", " ", name).strip().lower()
    return _RU_CITIES_MAP.get(key, name if re.search(r"[А-Яа-яЁё]", name) else name.capitalize())


# ────────────────────────── водные активности/берег ──────────────────────────
KITE_WIND_GOOD_MIN = float(os.getenv("KITE_WIND_GOOD_MIN", "7"))
KITE_WIND_GOOD_MAX = float(os.getenv("KITE_WIND_GOOD_MAX", "12"))
KITE_GUST_RATIO_BAD = float(os.getenv("KITE_GUST_RATIO_BAD", "1.5"))
KITE_WAVE_WARN = float(os.getenv("KITE_WAVE_WARN", "2.5"))

SUP_WIND_GOOD_MAX = float(os.getenv("SUP_WIND_GOOD_MAX", "4"))
OFFSHORE_SUP_WIND_MIN = float(os.getenv("OFFSHORE_SUP_WIND_MIN", "5"))
SUP_WAVE_GOOD_MAX = float(os.getenv("SUP_WAVE_GOOD_MAX", "0.6"))
SURF_WAVE_GOOD_MIN = float(os.getenv("SURF_WAVE_GOOD_MIN", "0.9"))
SURF_WAVE_GOOD_MAX = float(os.getenv("SURF_WAVE_GOOD_MAX", "2.5"))
SURF_WIND_MAX = float(os.getenv("SURF_WIND_MAX", "10"))

WSUIT_NONE = float(os.getenv("WSUIT_NONE", "22"))
WSUIT_SHORTY = float(os.getenv("WSUIT_SHORTY", "20"))
WSUIT_32 = float(os.getenv("WSUIT_32", "17"))
WSUIT_43 = float(os.getenv("WSUIT_43", "14"))
WSUIT_54 = float(os.getenv("WSUIT_54", "12"))
WSUIT_65 = float(os.getenv("WSUIT_65", "10"))

SST_CACHE_TTL_MIN = int(os.getenv("SST_CACHE_TTL_MIN", "0"))
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


SHORE_PROFILE: Dict[str, float] = {
    "Limassol": 180.0,
    "Larnaca": 180.0,
    "Ayia Napa": 140.0,
    "Pafos": 210.0,
}
SPOT_SHORE_PROFILE: Dict[str, float] = {
    "Lady's Mile": 170.0,
    "Paramali": 210.0,
    "Kourion (Curium)": 210.0,
    "Governor's Beach": 180.0,
    "Pissouri": 220.0,
    "Avdimou": 210.0,
    "Larnaca Kite Beach (Kiti)": 180.0,
    "Mazotos": 180.0,
    "Mackenzie": 150.0,
    "Ayia Napa (Nissi)": 140.0,
    "Protaras": 135.0,
    "Cape Greco": 120.0,
    "Paphos (Alykes)": 230.0,
    "Coral Bay": 260.0,
    "Latchi": 320.0,
}


def _norm_key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


_SPOT_INDEX = {_norm_key(k): k for k in SPOT_SHORE_PROFILE.keys()}


def _parse_deg(val: Optional[str]) -> Optional[float]:
    if not val:
        return None
    try:
        return float(str(val).strip())
    except Exception:
        return None


def _env_city_key(city: str) -> str:
    return city.upper().replace(" ", "_")


def _spot_from_env(name: Optional[str]) -> Optional[Tuple[str, float]]:
    if not name:
        return None
    key = _norm_key(name)
    real = _SPOT_INDEX.get(key)
    return (real, SPOT_SHORE_PROFILE[real]) if real else None


def _shore_face_for_city(city: str) -> Tuple[Optional[float], Optional[str]]:
    face_env = _parse_deg(os.getenv(f"SHORE_FACE_{_env_city_key(city)}"))
    if face_env is not None:
        return face_env, f"ENV:SHORE_FACE_{_env_city_key(city)}"
    sp = _spot_from_env(os.getenv(f"SPOT_{_env_city_key(city)}") or os.getenv("ACTIVE_SPOT"))
    if sp:
        label, deg = sp
        return deg, label
    if city in SHORE_PROFILE:
        return SHORE_PROFILE[city], city
    return None, None


# ───────────── утилиты ─────────────
def _as_tz(tz: Union[pendulum.Timezone, str]) -> pendulum.Timezone:
    return pendulum.timezone(tz) if isinstance(tz, str) else tz


WMO_DESC = {
    0: "☀️ ясно",
    1: "⛅ ч.обл",
    2: "☁️ обл",
    3: "🌥 пасм",
    45: "🌫 туман",
    48: "🌫 изморозь",
    51: "🌦 морось",
    61: "🌧 дождь",
    71: "❄️ снег",
    95: "⛈ гроза",
}


def code_desc(c: Any) -> Optional[str]:
    try:
        return WMO_DESC.get(int(c))
    except Exception:
        return None


def _iter_city_pairs(cities) -> list[tuple[str, tuple[float, float]]]:
    """
    Нормализует вход в безопасный список [(name,(lat,lon))].
    Поддерживает:
      - {"City": (lat, lon)}
      - [("City", (lat, lon))] / [("City", lat, lon)]
    Игнорирует строки и битые записи.
    """
    out: list[tuple[str, tuple[float, float]]] = []
    if not cities:
        return out
    if isinstance(cities, dict):
        for k, v in list(cities.items()):
            try:
                if isinstance(v, (list, tuple)) and len(v) == 2:
                    la, lo = float(v[0]), float(v[1])
                    out.append((str(k), (la, lo)))
            except Exception:
                continue
        return out
    if isinstance(cities, str):
        return out
    try:
        iterable = list(cities)
    except Exception:
        return out
    for item in iterable:
        try:
            if (
                isinstance(item, (list, tuple))
                and len(item) == 2
                and isinstance(item[1], (list, tuple))
                and len(item[1]) == 2
            ):
                name = str(item[0])
                la, lo = float(item[1][0]), float(item[1][1])
                out.append((name, (la, lo)))
                continue
            if isinstance(item, (list, tuple)) and len(item) == 3:
                name = str(item[0])
                la, lo = float(item[1]), float(item[2])
                out.append((name, (la, lo)))
                continue
        except Exception:
            continue
    return out


# ───────────── рассвет/закат ─────────────
def _parse_iso_to_tz(s: str, tz: pendulum.tz.timezone.Timezone) -> Optional[pendulum.DateTime]:
    try:
        return pendulum.parse(str(s)).in_tz(tz)
    except Exception:
        return None


def _noaa_dt_from_utc_fraction(
    date_obj: pendulum.Date, ut_hours: float, tz: pendulum.tz.timezone.Timezone
):
    h = int(ut_hours)
    m = int(round((ut_hours - h) * 60))
    base = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, tz="UTC")
    return base.add(hours=h, minutes=m).in_tz(tz)


def _noaa_sun_times(
    date_obj: pendulum.Date, lat: float, lon: float, tz: pendulum.tz.timezone.Timezone
) -> tuple[Optional[pendulum.DateTime], Optional[pendulum.DateTime]]:
    """Мини-реализация алгоритма NOAA (зенит 90.833°)."""

    def _sun_utc(is_sunrise: bool) -> Optional[float]:
        N = date_obj.day_of_year
        lngHour = lon / 15.0
        t = N + ((6 - lngHour) / 24.0 if is_sunrise else (18 - lngHour) / 24.0)
        M = (0.9856 * t) - 3.289
        L = M + (1.916 * math.sin(math.radians(M))) + (0.020 * math.sin(math.radians(2 * M))) + 282.634
        L = (L + 360.0) % 360.0
        RA = math.degrees(math.atan(0.91764 * math.tan(math.radians(L))))
        RA = (RA + 360.0) % 360.0
        Lq = math.floor(L / 90.0) * 90.0
        RAq = math.floor(RA / 90.0) * 90.0
        RA += Lq - RAq
        RA /= 15.0
        sinDec = 0.39782 * math.sin(math.radians(L))
        cosDec = math.cos(math.asin(sinDec))
        zenith = math.radians(90.833)
        cosH = (math.cos(zenith) - (sinDec * math.sin(math.radians(lat)))) / (cosDec * math.cos(math.radians(lat)))
        if cosH > 1 or cosH < -1:
            return None
        H = (360 - math.degrees(math.acos(cosH))) if is_sunrise else math.degrees(math.acos(cosH))
        H /= 15.0
        T = H + RA - (0.06571 * t) - 6.622
        UT = (T - lngHour) % 24.0
        return UT

    try:
        ut_sr = _sun_utc(True)
        ut_ss = _sun_utc(False)
        sr = _noaa_dt_from_utc_fraction(date_obj, ut_sr, tz) if ut_sr is not None else None
        ss = _noaa_dt_from_utc_fraction(date_obj, ut_ss, tz) if ut_ss is not None else None
        return sr, ss
    except Exception:
        return None, None


def _sun_times_for_date(
    lat: float, lon: float, date_obj: pendulum.Date, tz: pendulum.tz.timezone.Timezone
) -> tuple[Optional[pendulum.DateTime], Optional[pendulum.DateTime]]:
    try:
        wm = get_weather(lat, lon) or {}
        daily = wm.get("daily") or {}
        times = daily.get("time") or daily.get("date") or []
        sunr = daily.get("sunrise") or daily.get("sunrise_time") or []
        suns = daily.get("sunset") or daily.get("sunset_time") or []
        idx = None
        for i, t in enumerate(times):
            dt_i = _parse_iso_to_tz(t, tz)
            if dt_i and dt_i.date() == date_obj:
                idx = i
                break
        if idx is not None:
            sr = _parse_iso_to_tz(sunr[idx], tz) if idx < len(sunr) else None
            ss = _parse_iso_to_tz(suns[idx], tz) if idx < len(suns) else None
            if sr or ss:
                return sr, ss
    except Exception:
        pass
    # фолбэки
    try:
        from astral.sun import sun
        from astral import LocationInfo

        loc = LocationInfo("", "", tz.name, float(lat), float(lon))
        s = sun(loc.observer, date=date_obj.to_date_string(), tzinfo=tz)
        return (
            pendulum.instance(s["sunrise"]).in_tz(tz),
            pendulum.instance(s["sunset"]).in_tz(tz),
        )
    except Exception:
        pass
    return _noaa_sun_times(date_obj, lat, lon, tz)


def _choose_sun_coords(sea_pairs, other_pairs) -> Tuple[float, float]:
    prim = (PRIMARY_CITY_NAME or "").strip().lower()

    def _find(pairs):
        for name, (la, lo) in pairs:
            if name.strip().lower() == prim:
                return (la, lo)
        return None

    sea_pairs = list(sea_pairs)
    other_pairs = list(other_pairs)
    cand = _find(sea_pairs) or _find(other_pairs)
    if not cand and sea_pairs:
        cand = sea_pairs[0][1]
    if not cand and other_pairs:
        cand = other_pairs[0][1]
    return cand if cand else (CY_LAT, CY_LON)


def sun_line_for_mode(mode: str, tz: pendulum.tz.timezone.Timezone, lat: float, lon: float) -> Optional[str]:
    m = (mode or "evening").lower()
    if m.startswith("morn"):
        date_use = pendulum.today(tz)
        _, ss = _sun_times_for_date(lat, lon, date_use, tz)
        if ss:
            return f"🌇 Закат сегодня: {ss.format('HH:mm')}"
    else:
        date_use = pendulum.today(tz).add(days=1)
        sr, _ = _sun_times_for_date(lat, lon, date_use, tz)
        if sr:
            return f"🌅 Рассвет завтра: {sr.format('HH:mm')}"
    return None


# ───────────── NOAA Kp (для утра) ─────────────
def _fetch_world_kp() -> Tuple[Optional[float], Optional[int]]:
    if not requests:
        return None, None
    try:
        url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
        r = requests.get(
            url,
            timeout=15,
            headers={
                "User-Agent": "VayboMeter/1.0",
                "Accept": "application/json",
                "Cache-Control": "no-cache",
            },
        )
        r.raise_for_status()
        data = r.json()
        rows = [row for row in data if isinstance(row, list) and len(row) >= 2][1:]
        if not rows:
            return None, None
        last = rows[-1]
        val = float(last[1]) if last[1] not in (None, "null", "") else None
        ts_iso = str(last[0]) if last[0] else None
        age_min = None
        if ts_iso:
            try:
                dt_utc = pendulum.parse(ts_iso).in_tz("UTC")
                age_min = int((pendulum.now("UTC") - dt_utc).total_minutes())
            except Exception:
                age_min = None
        return val, age_min
    except Exception:
        return None, None


def _kp_status_label(kp: Optional[float]) -> str:
    if kp is None:
        return "н/д"
    if kp < 3.0:
        return "спокойно"
    if kp < 5.0:
        return "умеренно"
    if kp < 6.0:
        return "активно"
    return "буря"


# ───────────── Астроблок ─────────────
ZODIAC = {
    "Овен": "♈",
    "Телец": "♉",
    "Близнецы": "♊",
    "Рак": "♋",
    "Лев": "♌",
    "Дева": "♍",
    "Весы": "♎",
    "Скорпион": "♏",
    "Стрелец": "♐",
    "Козерог": "♑",
    "Водолей": "♒",
    "Рыбы": "♓",
}


def zsym(s: str) -> str:
    for name, sym in ZODIAC.items():
        s = s.replace(name, sym)
    return s


def load_calendar(path: str = "lunar_calendar.json") -> dict:
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("days"), dict):
        return data["days"]
    return data if isinstance(data, dict) else {}


# ───────────── Лунные спец-события (для картинок) ─────────────
def _norm_phase_name(x: Any) -> str:
    return re.sub(r"[^a-zа-яё]+", "", str(x or "").strip().lower())


def _is_new_moon(phase_name: str) -> bool:
    p = _norm_phase_name(phase_name)
    return p in ("новолуние", "newmoon", "new")


def _is_full_moon(phase_name: str) -> bool:
    p = _norm_phase_name(phase_name)
    return p in ("полнолуние", "fullmoon", "full")


def lunar_special_event_for_date(date_local: pendulum.Date, tz_local: str = "Asia/Nicosia") -> Optional[Dict[str, Any]]:
    """
    Возвращает метку спец-события для даты (новолуние/полнолуние), если доступно из lunar_calendar.json.
    Формат: {"type": "new_moon"|"full_moon", "title": "...", "percent": int, "sign_from": "...", "sign_to": "...", "phase_name": "..."}
    """
    try:
        cal = load_calendar("lunar_calendar.json")
        if not isinstance(cal, dict):
            return None
        rec = cal.get(date_local.format("YYYY-MM-DD"), {}) or {}
        if not isinstance(rec, dict):
            return None

        phase_raw = (rec.get("phase_name") or rec.get("phase") or "").strip()
        phase_name = re.sub(r"^[^\wА-Яа-яЁё]+", "", phase_raw).split(",")[0].strip()

        percent = rec.get("percent") or rec.get("illumination") or rec.get("illum") or 0
        try:
            percent_i = int(round(float(percent)))
        except Exception:
            percent_i = 0

        # Знаки: встречаются разные ключи в разных календарях
        sign_from = (rec.get("sign") or rec.get("zodiac") or rec.get("sign_from") or rec.get("moon_sign") or "") or ""
        sign_to = (rec.get("sign_to") or rec.get("zodiac_to") or rec.get("signNext") or "") or ""
        if not sign_to:
            sign_to = sign_from

        if _is_new_moon(phase_name):
            return {
                "type": "new_moon",
                "title": "Новолуние",
                "percent": percent_i,
                "sign_from": sign_from,
                "sign_to": sign_to,
                "phase_name": phase_name,
            }
        if _is_full_moon(phase_name):
            return {
                "type": "full_moon",
                "title": "Полнолуние",
                "percent": percent_i,
                "sign_from": sign_from,
                "sign_to": sign_to,
                "phase_name": phase_name,
            }
    except Exception:
        return None
    return None



def _parse_voc_dt(s: str, tz: pendulum.tz.timezone.Timezone):
    if not s:
        return None
    try:
        return pendulum.parse(s).in_tz(tz)
    except Exception:
        pass
    try:
        dmy, hm = s.split()
        d, m = map(int, dmy.split("."))
        hh, mm = map(int, hm.split(":"))
        year = pendulum.today(tz).year
        return pendulum.datetime(year, m, d, hh, mm, tz=tz)
    except Exception:
        return None


def voc_interval_for_date(rec: dict, tz_local: str = "Asia/Nicosia"):
    if not isinstance(rec, dict):
        return None
    voc = rec.get("void_of_course") or rec.get("voc") or rec.get("void") or {}
    if not isinstance(voc, dict):
        return None
    s = voc.get("start") or voc.get("from") or voc.get("start_time")
    e = voc.get("end") or voc.get("to") or voc.get("end_time")
    if not s or not e:
        return None
    tz = pendulum.timezone(tz_local)
    t1 = _parse_voc_dt(s, tz)
    t2 = _parse_voc_dt(e, tz)
    if not t1 or not t2:
        return None
    return (t1, t2)


def _astro_llm_bullets(date_str: str, phase: str, percent: int, sign: str, voc_text: str) -> List[str]:
    cache_file = CACHE_DIR / f"astro_{date_str}.txt"
    if cache_file.exists():
        lines = [l.strip() for l in cache_file.read_text("utf-8").splitlines() if l.strip()]
        if lines:
            return lines[:4]

    if not USE_DAILY_LLM:
        return []

    system = (
        "Действуй как АстроЭксперт, ты лучше всех знаешь как энергии луны и звезд влияют на жизнь человека."
        "Ты делаешь очень короткую сводку астрособытий на указанную дату (2–3 строки). "
        "Пиши грамотно по-русски, без клише. Используй ТОЛЬКО данную информацию: "
        "фаза Луны, освещённость, знак Луны и интервал Void-of-Course. "
        "Не придумывай других планет и аспектов. Каждая строка начинается с эмодзи и содержит одну мысль."
     )

    prompt = (
        f"Дата: {date_str}. Фаза Луны: {phase or 'н/д'} ({percent}% освещённости). "
        f"Знак: {sign or 'н/д'}. VoC: {voc_text or 'нет'}."
    )

    try:
        txt = gpt_complete(prompt=prompt, system=system, temperature=0.2, max_tokens=220)
        raw_lines = [l.strip() for l in (txt or "").splitlines() if l.strip()]

        safe: List[str] = []
        emoji_cycle = ["🌙", "✨", "✅", "⚫️"]

        for l in raw_lines:
            l = re.sub(r"^[•\-\u2022]+\s*", "", l).strip()
            l = re.sub(r"\*", "", l).strip()  # убираем markdown-звёздочки
            l = _sanitize_line(l, 140)

            if l and not _looks_gibberish(l):
                if not re.match(r"^\W", l):
                    pref = emoji_cycle[min(len(safe), len(emoji_cycle) - 1)]
                    l = f"{pref} {l}"
                safe.append(l)

        if safe:
            cache_file.write_text("\n".join(safe[:4]), "utf-8")
            return safe[:4]

    except Exception as e:
        logging.warning("Astro LLM failed: %s", e)

    return []


# ───────────── благоприятные / неблагоприятные дни ─────────────
_FAVDAY_LABELS = {
    "general": ("✨", "общие дела"),
    "shopping": ("💰", "покупки"),
    "travel": ("✈️", "поездки"),
    "haircut": ("💇‍♀️", "стрижки"),
    "health": ("🩺", "здоровье"),
}


def _favday_status_for(day: int, bucket: dict | None) -> str | None:
    """
    Возвращает статус дня для одной категории:
      - "good"   — день есть только в favorable
      - "bad"    — день есть только в unfavorable
      - "mixed"  — день в обоих списках
      - None     — информации нет
    """
    if not isinstance(bucket, dict):
        return None
    fav = bucket.get("favorable") or []
    unf = bucket.get("unfavorable") or []

    in_f = day in fav
    in_u = day in unf

    if in_f and in_u:
        return "mixed"
    if in_f:
        return "good"
    if in_u:
        return "bad"
    return None


def _favdays_lines_for_date(rec: dict, date_local: pendulum.Date) -> list[str]:
    """
    Короткие строки про благоприятность ТЕКУЩЕГО дня месяца.

    Логика:
    - 'general' даёт общий фон: благоприятный / неблагоприятный / смешанный.
    - По остальным категориям показываем только те, где день явно благоприятный.
    """
    day = date_local.day

    root = rec.get("favorable_days") or rec.get("unfavorable_days") or {}
    if not isinstance(root, dict):
        return []

    lines: list[str] = []

    # Общий фон дня
    general_bucket = root.get("general") or {}
    st_general = _favday_status_for(day, general_bucket)

    if st_general == "good":
        lines.append("✅ Общий фон: благоприятный день.")
    elif st_general == "bad":
        lines.append("⚠️ Общий фон: неблагоприятный день.")
    elif st_general == "mixed":
        lines.append("➿ Общий фон: день с разным фоном — прислушивайся к себе.")

    # Остальные категории: показываем только благоприятные
    good_cats: list[str] = []

    for key, (icon, label) in _FAVDAY_LABELS.items():
        if key == "general":
            continue
        bucket = root.get(key) or {}
        st = _favday_status_for(day, bucket)
        if st == "good":
            good_cats.append(f"{icon} {label}")

    if good_cats:
        lines.append("💚 В плюсе: " + ", ".join(good_cats) + ".")

    return lines


def _advice_lines_from_rec(rec: dict) -> list[str]:
    """
    Берёт готовый текст совета из rec['advice'] (или похожих полей)
    и преобразует в 1–4 аккуратные строки.

    Важно:
    - убираем плейсхолдеры и markdown-символы '*'
    - не ограничиваемся 1 строкой, если текст короткий — дальше build_astro_section дополнит
    """
    if not isinstance(rec, dict):
        return []

    raw = (
        rec.get("advice")
        or rec.get("advice_ru")
        or rec.get("text")
        or rec.get("summary")
    )

    # advice может быть list[str] (как в твоём lunar_calendar.json) — превращаем в текст
    if isinstance(raw, list):
        raw = "\n".join([str(x).strip() for x in raw if str(x).strip()])

    if not isinstance(raw, str):
        return []

    raw = raw.strip()
    if not raw:
        return []


    # 1) Отбрасываем совсем короткие плейсхолдеры "… Луна" без фактики
    low = raw.lower()
    if len(raw) < 45 and ("луна" in low) and not any(
        x in low for x in (
            "%", "voc", "vоc", "void", "без курса", "знак",
            "♈", "♉", "♊", "♋", "♌", "♍", "♎", "♏", "♐", "♑", "♒", "♓"
        )
    ):
        return []

    if re.fullmatch(
        r"(?iu)[✨⭐🌙🌌\s]*\d{1,2}\s*(января|февраля|марта|апреля|мая|июня|июля|августа|сентября|октября|ноября|декабря)?\s*луна\s*",
        raw,
    ):
        return []

        # 1b) Отбрасываем плейсхолдеры вида "✨ 17 января наступает" (обрезанный заголовок без сути)
    if len(raw) < 45 and ("наступает" in low) and not any(
        x in low for x in (
            "новолуние", "полнолуние", "четверт", "%", "voc", "vоc", "void", "без курса", "знак",
            "♈", "♉", "♊", "♋", "♌", "♍", "♎", "♏", "♐", "♑", "♒", "♓"
        )
    ):
        return []


    # 2) Нормализация строк: без "•", без "*", с эмодзи-префиксом при необходимости
    lines: list[str] = []
    emoji_cycle = ["🌙", "✨", "✅", "⚫️"]

    for src in raw.splitlines():
        line = (src or "").strip()
        if not line:
            continue

        line = re.sub(r"^[•\-\u2022]+\s*", "", line).strip()
        line = re.sub(r"\*", "", line).strip()  # убираем markdown-звёздочки

        line = _sanitize_line(line, 140)
        if not line or _looks_gibberish(line):
            continue

        if not re.match(r"^\W", line):
            pref = emoji_cycle[min(len(lines), len(emoji_cycle) - 1)]
            line = f"{pref} {line}"

        lines.append(line)
        if len(lines) >= 4:
            break

    return lines[:4]



def build_astro_section(
    date_local: Optional[pendulum.Date] = None,
    tz_local: str = "Asia/Nicosia",
) -> str:
    tz = pendulum.timezone(tz_local)
    base_date = date_local or pendulum.today(tz)

    # Дополнительный сдвиг через ASTRO_OFFSET (в днях), если нужно
    try:
        offset_days = int(os.getenv("ASTRO_OFFSET", "0") or "0")
    except Exception:
        offset_days = 0

    work_date = base_date.add(days=offset_days) if offset_days else base_date
    date_key = work_date.format("YYYY-MM-DD")

    cal = load_calendar("lunar_calendar.json")
    rec = cal.get(date_key, {}) if isinstance(cal, dict) else {}

    phase_raw = (rec.get("phase_name") or rec.get("phase") or "").strip()
    phase_name = re.sub(r"^[^\wА-Яа-яЁё]+", "", phase_raw).split(",")[0].strip()

    percent = rec.get("percent") or rec.get("illumination") or rec.get("illum") or 0
    try:
        percent = int(round(float(percent)))
    except Exception:
        percent = 0

    sign_raw = (rec.get("sign") or rec.get("zodiac") or "").strip()

    # VoC
    voc_text = ""
    voc = voc_interval_for_date(rec, tz_local=tz_local)
    if voc:
        t1, t2 = voc
        voc_text = f"{t1.format('HH:mm')}–{t2.format('HH:mm')}"

    # ── знак: символ + локатив (в Тельце / в Козероге …)
    _sign2sym = {
        "Овен": "♈", "Телец": "♉", "Близнецы": "♊", "Рак": "♋",
        "Лев": "♌", "Дева": "♍", "Весы": "♎", "Скорпион": "♏",
        "Стрелец": "♐", "Козерог": "♑", "Водолей": "♒", "Рыбы": "♓",
        "♈": "♈", "♉": "♉", "♊": "♊", "♋": "♋", "♌": "♌", "♍": "♍",
        "♎": "♎", "♏": "♏", "♐": "♐", "♑": "♑", "♒": "♒", "♓": "♓",
    }
    _sign_loc = {
        "Овен": "Овне",
        "Телец": "Тельце",
        "Близнецы": "Близнецах",
        "Рак": "Раке",
        "Лев": "Льве",
        "Дева": "Деве",
        "Весы": "Весах",
        "Скорпион": "Скорпионе",
        "Стрелец": "Стрельце",
        "Козерог": "Козероге",
        "Водолей": "Водолее",
        "Рыбы": "Рыбах",
    }

    sign_sym = _sign2sym.get(sign_raw, "")
    if not sign_sym:
        # попробуем вытащить символ из phase, если он там есть
        ph = str(rec.get("phase") or "")
        m = re.search(r"[♈♉♊♋♌♍♎♏♐♑♒♓]", ph)
        if m:
            sign_sym = m.group(0)

    sign_loc = _sign_loc.get(sign_raw, "")
    if not sign_loc and sign_sym:
        # если есть только символ — оставим символ (чтобы не ломать падежи)
        sign_loc = sign_sym

    # ── Шаблон «как раньше»
    phase_l = (phase_name or "").lower()

    if "новол" in phase_l:
        moon_emoji = "🌑"
        phase_hint = "время намерений и мягкого планирования"
    elif "полн" in phase_l:
        moon_emoji = "🌕"
        phase_hint = "пик эмоций и результатов — лучше завершать, чем начинать"
    elif "убыв" in phase_l:
        moon_emoji = "🌘"
        phase_hint = "подходит для завершения дел и разгрузки"
    elif "раст" in phase_l:
        moon_emoji = "🌙"
        phase_hint = "подходит для укрепления планов и постепенного роста"
    else:
        moon_emoji = "🌙"
        phase_hint = "держи курс на простые и понятные шаги"

    if percent:
        if percent <= 20:
            illum_hint = "не спеши — сначала настрой и наблюдение"
        elif percent <= 60:
            illum_hint = "можно набирать темп, но без перегруза"
        elif percent <= 85:
            illum_hint = "хорошо для практических решений и закрепления"
        else:
            illum_hint = "эмоции ярче обычного — выбирай спокойный темп"
    else:
        illum_hint = "ориентируйся на самочувствие и простые планы"

    # Общий фон (по календарю благоприятных/неблагоприятных дней)
    day_n = int(getattr(work_date, "day", 0) or 0)
    fav_general = (rec.get("favorable_days") or {}).get("general") or {}
    fav_list = fav_general.get("favorable") or []
    unf_list = fav_general.get("unfavorable") or []
    is_fav = day_n in fav_list if day_n else False
    is_unf = day_n in unf_list if day_n else False

    if is_fav and not is_unf:
        bg_line = "✅ Общий фон: благоприятный день."
    elif is_unf and not is_fav:
        bg_line = "⚠️ Общий фон: неблагоприятный день."
    elif is_fav and is_unf:
        bg_line = "➿ Общий фон: день с разным фоном — прислушивайся к себе."
    else:
        bg_line = "➿ Общий фон: нейтрально — ориентируйся на самочувствие."

    # В плюсе: сначала берём категории из favorable_days (если заполнены), иначе — по знаку
    cat_map = {
        "shopping": "💰 покупки",
        "haircut": "💇‍♀️ стрижки",
        "travel": "✈️ поездки",
        "health": "💪 здоровье",
    }
    plus_bits: list[str] = []
    fav_days = rec.get("favorable_days") or {}
    for k, label in cat_map.items():
        k_rec = fav_days.get(k) or {}
        k_fav = k_rec.get("favorable") or []
        if day_n and day_n in k_fav:
            plus_bits.append(label)

    plus_map = {
        "♑": "💼 планы, 🧾 финансы, 🧱 структура",
        "♉": "💰 покупки, 🍲 тело, 🌿 стабильность",
        "♈": "🔥 старт, 🏃 энергия, 🎯 решительность",
        "♋": "🏠 дом, 💞 близкие, 🫖 забота",
        "♍": "🧹 порядок, 📋 рутина, 🧠 фокус",
        "♎": "🤝 договорённости, ⚖️ баланс, 🎨 эстетика",
        "♏": "🧿 глубина, 🧠 трансформация, 🔥 мотивация",
        "♐": "🧭 планы, ✈️ дороги, 📚 обучение",
        "♒": "💡 идеи, 🧑‍🤝‍🧑 сообщество, 🛠️ обновления",
        "♓": "🎵 интуиция, 🫧 восстановление, 🎨 творчество",
        "♌": "🌟 уверенность, 🎭 самовыражение, 💛 сердце",
        "♊": "🗣️ общение, 📩 связи, 🧩 гибкость",
    }

    if plus_bits:
        plus_line = f"💚 В плюсе: {', '.join(plus_bits)}."
    else:
        plus_hint = plus_map.get(sign_sym, "маленькие практические шаги, порядок, забота о себе")
        plus_line = f"💚 В плюсе: {plus_hint}."

    # База: 3–4 строки «как раньше»
    phase_disp = phase_name or "Луна"
    if sign_loc:
        tmpl1 = f"{moon_emoji} {phase_disp} в {sign_loc} — {phase_hint}."
    else:
        tmpl1 = f"{moon_emoji} {phase_disp} — {phase_hint}."

    tmpl2 = f"✨ {percent}% освещённости — {illum_hint}." if percent else f"✨ Освещённость: н/д — {illum_hint}."
    tmpl3 = bg_line
    tmpl4 = plus_line

    template_bullets = [
        _sanitize_line(tmpl1, 160),
        _sanitize_line(tmpl2, 160),
        _sanitize_line(tmpl3, 160),
        _sanitize_line(tmpl4, 160),
    ]

    # advice из календаря + long_desc (очень дозировано, чтобы не раздувать блок)
    bullets = _advice_lines_from_rec(rec) or []
    long_desc = (rec.get("long_desc") or "").strip()
    if long_desc:
        # возьмём первую фразу/кусок
        long_piece = re.split(r"[.!?]\s+", long_desc, maxsplit=1)[0].strip()
        if long_piece and long_piece not in bullets:
            bullets = [long_piece] + bullets

    # Если bullet-ов мало — можно дотянуть LLM (если доступен)
    need_min = 3
    extra: list[str] = []
    if len(bullets) < need_min:
        extra = _astro_llm_bullets(
            work_date.format("DD.MM.YYYY"),
            phase_name,
            int(percent or 0),
            sign_raw,
            voc_text,
        ) or []

    # Слияние (важный момент: template_bullets НЕ переопределяем ниже)
    merged: list[str] = []
    for src_list in (template_bullets, bullets, extra):
        for x in (src_list or []):
            x = _sanitize_line(str(x or "").strip(), 180)
            if not x:
                continue
            if x not in merged:
                merged.append(x)

    final_bullets = merged[:5] if merged else template_bullets[:4]

    # Заголовок по флагу (по умолчанию — без него, как в твоих старых примерах)
    lines: list[str] = []
    show_header = os.getenv("ASTRO_SHOW_HEADER", "0").strip().lower() in ("1", "true", "yes", "on")
    if show_header:
        lines.append("🌌 <b>Астрособытия</b>")

    lines += [zsym(x) for x in final_bullets]

    # VoC отдельной строкой, если есть и не продублирован
    if voc_text:
        low = " ".join(final_bullets).lower()
        if ("voc" not in low) and ("без курса" not in low):
            lines.append(f"⚫️ VoC {voc_text} — без новых стартов.")

    # Доп. строки «в плюсе» по категориям (если твой хелпер это формирует)
    lines += _favdays_lines_for_date(rec, work_date)

    return "\n".join(lines)



# ───────────── hourly/ветер/давление ─────────────
def _pick(d: Dict[str, Any], *keys, default=None):
    for k in keys:
        if k in d:
            return d[k]
    return default


def _hourly_times(wm: Dict[str, Any]) -> List[pendulum.DateTime]:
    hourly = wm.get("hourly") or {}
    times = hourly.get("time") or hourly.get("time_local") or hourly.get("timestamp") or []
    out: List[pendulum.DateTime] = []
    for t in times:
        try:
            out.append(pendulum.parse(str(t)))
        except Exception:
            continue
    return out


def _nearest_index_for_day(
    times: List[pendulum.DateTime], date_obj: pendulum.Date, prefer_hour: int, tz: pendulum.Timezone
) -> Optional[int]:
    if not times:
        return None
    target = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, prefer_hour, 0, tz=tz)
    best_i, best_diff = None, None
    for i, dt_i in enumerate(times):
        try:
            dt_local = dt_i.in_tz(tz)
        except Exception:
            dt_local = dt_i
        if dt_local.date() != date_obj:
            continue
        diff = abs((dt_local - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    return best_i


def _circular_mean_deg(deg_list: List[float]) -> Optional[float]:
    if not deg_list:
        return None
    x = sum(math.cos(math.radians(d)) for d in deg_list)
    y = sum(math.sin(math.radians(d)) for d in deg_list)
    if x == 0 and y == 0:
        return None
    ang = math.degrees(math.atan2(y, x))
    return (ang + 360.0) % 360.0


def pick_tomorrow_header_metrics(
    wm: Dict[str, Any], tz: pendulum.Timezone
) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)

    # FIX: используем today() вместо now(), чтобы WORK_DATE (pendulum.today monkeypatch) влиял корректно
    tomorrow = pendulum.today(tz).add(days=1).date()

    spd_arr = _pick(
        hourly,
        "windspeed_10m",
        "windspeed",
        "wind_speed_10m",
        "wind_speed",
        default=[],
    )
    dir_arr = _pick(
        hourly,
        "winddirection_10m",
        "winddirection",
        "wind_dir_10m",
        "wind_dir",
        default=[],
    )
    prs_arr = hourly.get("surface_pressure", []) or hourly.get("pressure", [])
    if times:
        idx_noon = _nearest_index_for_day(times, tomorrow, 12, tz)
        idx_morn = _nearest_index_for_day(times, tomorrow, 6, tz)
    else:
        idx_noon = idx_morn = None
    wind_ms = wind_dir = press_val = None
    trend = "→"
    if idx_noon is not None:
        try:
            spd = float(spd_arr[idx_noon]) if idx_noon < len(spd_arr) else None
        except Exception:
            spd = None
        try:
            wdir = float(dir_arr[idx_noon]) if idx_noon < len(dir_arr) else None
        except Exception:
            wdir = None
        try:
            p_noon = float(prs_arr[idx_noon]) if idx_noon < len(prs_arr) else None
        except Exception:
            p_noon = None
        try:
            p_morn = (
                float(prs_arr[idx_morn])
                if idx_morn is not None and idx_morn < len(prs_arr)
                else None
            )
        except Exception:
            p_morn = None
        wind_ms = kmh_to_ms(spd) if isinstance(spd, (int, float)) else None
        wind_dir = int(round(wdir)) if isinstance(wdir, (int, float)) else None
        press_val = int(round(p_noon)) if isinstance(p_noon, (int, float)) else None
        if isinstance(p_noon, (int, float)) and isinstance(p_morn, (int, float)):
            diff = p_noon - p_morn
            trend = "↑" if diff >= 0.3 else "↓" if diff <= -0.3 else "→"
    if wind_ms is None and times:
        idxs = [i for i, t in enumerate(times) if t.in_tz(tz).date() == tomorrow]
        if idxs:
            try:
                speeds = [float(spd_arr[i]) for i in idxs if i < len(spd_arr)]
            except Exception:
                speeds = []
            try:
                dirs = [float(dir_arr[i]) for i in idxs if i < len(dir_arr)]
            except Exception:
                dirs = []
            try:
                prs = [float(prs_arr[i]) for i in idxs if i < len(prs_arr)]
            except Exception:
                prs = []
            if speeds:
                wind_ms = kmh_to_ms(sum(speeds) / len(speeds))
            mean_dir = _circular_mean_deg(dirs)
            wind_dir = int(round(mean_dir)) if mean_dir is not None else wind_dir
            if prs:
                press_val = int(round(sum(prs) / len(prs)))
    if wind_ms is None or wind_dir is None or press_val is None:
        cur = wm.get("current") or {}
        if wind_ms is None:
            spd = cur.get("windspeed") or cur.get("wind_speed")
            wind_ms = kmh_to_ms(spd) if isinstance(spd, (int, float)) else wind_ms
        if wind_dir is None:
            wdir = cur.get("winddirection") or cur.get("wind_dir")
            wind_dir = int(round(float(wdir))) if isinstance(wdir, (int, float)) else wind_dir
        if press_val is None and isinstance(cur.get("pressure"), (int, float)):
            press_val = int(round(float(cur["pressure"])))
    return wind_ms, wind_dir, press_val, trend


# === индексы на завтра/шторм-флаги ============================
def _tomorrow_hourly_indices(wm: Dict[str, Any], tz: pendulum.Timezone) -> List[int]:
    times = _hourly_times(wm)

    # FIX: today() вместо now() для консистентности с WORK_DATE
    tom = pendulum.today(tz).add(days=1).date()

    idxs: List[int] = []
    for i, dt_i in enumerate(times):
        try:
            if dt_i.in_tz(tz).date() == tom:
                idxs.append(i)
        except Exception:
            pass
    return idxs


def _today_hourly_indices(wm: Dict[str, Any], tz: pendulum.Timezone) -> List[int]:
    times = _hourly_times(wm)

    # today() вместо now() для консистентности с WORK_DATE
    day = pendulum.today(tz).date()

    idxs: List[int] = []
    for i, dt_i in enumerate(times):
        try:
            if dt_i.in_tz(tz).date() == day:
                idxs.append(i)
        except Exception:
            pass
    return idxs

def storm_flags_for_tomorrow(wm: Dict[str, Any], tz: pendulum.Timezone) -> Dict[str, Any]:
    hourly = wm.get("hourly") or {}
    idxs = _tomorrow_hourly_indices(wm, tz)
    if not idxs:
        return {"warning": False}

    def _arr(*names, default=None):
        v = _pick(hourly, *names, default=default)
        return v if isinstance(v, list) else []


    def _vals(arr):
        out = []
        for i in idxs:
            if i < len(arr):
                try:
                    out.append(float(arr[i]))
                except Exception:
                    pass
        return out

    speeds_kmh = _vals(_arr("windspeed_10m", "windspeed", "wind_speed_10m", "wind_speed", default=[]))
    gusts_kmh = _vals(_arr("windgusts_10m", "wind_gusts_10m", "wind_gusts", default=[]))
    rain_mm_h = _vals(_arr("rain", default=[]))
    tprob = _vals(_arr("thunderstorm_probability", default=[]))

    max_speed_ms = kmh_to_ms(max(speeds_kmh)) if speeds_kmh else None
    max_gust_ms = kmh_to_ms(max(gusts_kmh)) if gusts_kmh else None
    heavy_rain = (max(rain_mm_h) >= 8.0) if rain_mm_h else False
    thunder = (max(tprob) >= 60) if tprob else False

    reasons = []
    if isinstance(max_speed_ms, (int, float)) and max_speed_ms >= 13:
        reasons.append(f"ветер до {max_speed_ms:.0f} м/с")
    if isinstance(max_gust_ms, (int, float)) and max_gust_ms >= 17:
        reasons.append(f"порывы до {max_gust_ms:.0f} м/с")
    if heavy_rain:
        reasons.append("сильный дождь")
    if thunder:
        reasons.append("гроза")

    return {
        "max_speed_ms": max_speed_ms,
        "max_gust_ms": max_gust_ms,
        "heavy_rain": heavy_rain,
        "thunder": thunder,
        "warning": bool(reasons),
        "warning_text": "⚠️ <b>Штормовое предупреждение</b>: " + ", ".join(reasons) if reasons else "",
    }


def storm_flags_for_today(wm: Dict[str, Any], tz: pendulum.Timezone) -> Dict[str, Any]:
    hourly = wm.get("hourly") or {}
    idxs = _today_hourly_indices(wm, tz)
    if not idxs:
        return {"warning": False}

    def _arr(*names, default=None):
        v = _pick(hourly, *names, default=default)
        return v if isinstance(v, list) else []

    def _vals(arr):
        out = []
        for i in idxs:
            if i < len(arr):
                try:
                    out.append(float(arr[i]))
                except Exception:
                    pass
        return out

    speeds_kmh = _vals(_arr("windspeed_10m", "windspeed", "wind_speed_10m", "wind_speed", default=[]))
    gusts_kmh = _vals(_arr("windgusts_10m", "wind_gusts_10m", "wind_gusts", default=[]))
    rain_mm_h = _vals(_arr("rain", default=[]))
    tprob = _vals(_arr("thunderstorm_probability", default=[]))

    max_speed_ms = kmh_to_ms(max(speeds_kmh)) if speeds_kmh else None
    max_gust_ms = kmh_to_ms(max(gusts_kmh)) if gusts_kmh else None
    heavy_rain = (max(rain_mm_h) >= 8.0) if rain_mm_h else False
    thunder = (max(tprob) >= 60) if tprob else False

    reasons = []
    if isinstance(max_speed_ms, (int, float)) and max_speed_ms >= 13:
        reasons.append(f"ветер до {max_speed_ms:.0f} м/с")
    if isinstance(max_gust_ms, (int, float)) and max_gust_ms >= 17:
        reasons.append(f"порывы до {max_gust_ms:.0f} м/с")
    if heavy_rain:
        reasons.append("сильный дождь")
    if thunder:
        reasons.append("гроза")

    return {
        "max_speed_ms": max_speed_ms,
        "max_gust_ms": max_gust_ms,
        "heavy_rain": heavy_rain,
        "thunder": thunder,
        "warning": bool(reasons),
        "warning_text": "⚠️ <b>Штормовое предупреждение</b>: " + ", ".join(reasons) if reasons else "",
    }



# ───────────── Air combo (только утро) ─────────────
def _aqi_bucket_label(aqi: Optional[float]) -> Optional[str]:
    if not isinstance(aqi, (int, float)):
        return None
    x = float(aqi)
    if x <= 50:
        return "низкий"
    if x <= 100:
        return "умеренный"
    if x <= 150:
        return "высокий"
    return "очень высокий"


def _is_air_bad(air_now: Dict[str, Any]) -> tuple[bool, str]:
    aqi = air_now.get("aqi")
    try:
        aqi_f = float(aqi) if aqi is not None else None
    except Exception:
        aqi_f = None
    if aqi_f is None:
        return False, ""
    if aqi_f <= 50:
        return False, "🟢 воздух в норме"
    if aqi_f <= 100:
        return True, "🟡 воздух умеренный — избегайте интенсивных тренировок на улице"
    return True, "🟠 воздух неблагоприятный — тренировки лучше перенести в помещение"


def _morning_combo_air_radiation_pollen(lat: float, lon: float) -> Optional[str]:
    air = get_air(lat, lon) or {}
    aqi = air.get("aqi")
    try:
        aqi_f = float(aqi) if aqi is not None else None
    except Exception:
        aqi_f = None

    lbl = _aqi_bucket_label(aqi_f)

    pm25 = air.get("pm25")
    pm10 = air.get("pm10")
    try:
        pm25_i = int(round(float(pm25))) if pm25 is not None else None
    except Exception:
        pm25_i = None
    try:
        pm10_i = int(round(float(pm10))) if pm10 is not None else None
    except Exception:
        pm10_i = None

    data_rad = get_radiation(lat, lon) or {}
    dose = data_rad.get("dose")
    dose_line = f"📟 {float(dose):.2f} μSv/h" if isinstance(dose, (int, float)) else None

    p = get_pollen() or {}
    risk = p.get("risk")

    parts: list[str] = []

    aqi_part = f"AQI {int(round(aqi_f))}" if isinstance(aqi_f, (int, float)) else "AQI н/д"
    if lbl:
        aqi_part += f" ({lbl})"
    parts.append(aqi_part)

    pm_part: list[str] = []
    if isinstance(pm25_i, int):
        pm_part.append(f"PM₂.₅ {pm25_i}")
    if isinstance(pm10_i, int):
        pm_part.append(f"PM₁₀ {pm10_i}")
    if pm_part:
        parts.append(" / ".join(pm_part))

    em_sm, lbl_sm = smoke_index(pm25, pm10)
    if isinstance(lbl_sm, str) and lbl_sm.lower() not in ("низкое", "низкий", "нет", "н/д"):
        parts.append(f"😮‍💨 задымление: {lbl_sm}")

    if dose_line:
        parts.append(dose_line)

    if isinstance(risk, str) and risk:
        parts.append(f"🌿 пыльца: {risk}")

    if not parts:
        return None

    return "🏭 " + " • ".join(parts)


# ───────────── городская строка ─────────────
def _deg_diff(a: float, b: float) -> float:
    return abs((a - b + 180) % 360 - 180)


def _cardinal(deg: Optional[float]) -> Optional[str]:
    if deg is None:
        return None
    dirs = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]
    idx = int((deg + 22.5) // 45) % 8
    return dirs[idx]


def _shore_class(city: str, wind_from_deg: Optional[float]) -> Tuple[Optional[str], Optional[str]]:
    if wind_from_deg is None:
        return None, None
    face_deg, src_label = _shore_face_for_city(city)
    if face_deg is None:
        return None, src_label
    diff = _deg_diff(wind_from_deg, face_deg)
    if diff <= 45:
        return "onshore", src_label
    if diff >= 135:
        return "offshore", src_label
    return "cross", src_label


def _fetch_wave_for_tomorrow(
    lat: float,
    lon: float,
    tz_obj: pendulum.Timezone,
    prefer_hour: int = 12,
    timeout_s: int = 18,
    retries: int = 2,
) -> Tuple[Optional[float], Optional[float]]:
    if not requests:
        return None, None

    url = "https://marine-api.open-meteo.com/v1/marine"
    params = {
        "latitude": lat,
        "longitude": lon,
        "hourly": "wave_height,wave_period",
        "timezone": tz_obj.name,
    }

    last_exc: Optional[Exception] = None
    for attempt in range(1, max(1, retries) + 1):
        try:
            r = requests.get(url, params=params, timeout=timeout_s)
            r.raise_for_status()
            j = r.json()

            hourly = j.get("hourly") or {}
            times = [pendulum.parse(t) for t in (hourly.get("time") or []) if t]
            idx = _nearest_index_for_day(
                times,
                pendulum.now(tz_obj).add(days=1).date(),
                prefer_hour,
                tz_obj,
            )
            if idx is None:
                return None, None

            h = hourly.get("wave_height") or []
            p = hourly.get("wave_period") or []
            w_h = float(h[idx]) if idx < len(h) and h[idx] is not None else None
            w_t = float(p[idx]) if idx < len(p) and p[idx] is not None else None
            return w_h, w_t

        except Exception as e:
            last_exc = e
            # не спамим логами: пишем только на последней попытке
            if attempt >= max(1, retries):
                logging.warning("marine fetch failed: %s", e)
                return None, None

    logging.warning("marine fetch failed: %s", last_exc)
    return None, None


def _wetsuit_hint(sst: Optional[float]) -> Optional[str]:
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


def _city_detail_line(
    city: str, la: float, lo: float, tz_obj: pendulum.Timezone, include_sst: bool
) -> tuple[Optional[float], Optional[str]]:
    tz_name = tz_obj.name
    tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
    if tmax is None:
        st_fb = day_night_stats(la, lo, tz=tz_name) or {}
        tmax = st_fb.get("t_day_max")
        tmin = st_fb.get("t_night_min")
    if tmax is None:
        return None, None
    tmin = tmin if tmin is not None else tmax

    wm = get_weather(la, lo) or {}
    wcx = (wm.get("daily", {}) or {}).get("weathercode", [])
    wcx = wcx[1] if isinstance(wcx, list) and len(wcx) > 1 else None
    descx = code_desc(wcx) or "—"

    wind_ms, wind_dir, press_val, press_trend = pick_tomorrow_header_metrics(wm, tz_obj)
    storm = storm_flags_for_tomorrow(wm, tz_obj)
    gust = storm.get("max_gust_ms")

    name_html = f"<b>{_escape_html(_ru_city(city))}</b>"
    temp_part = f"{round(float(tmax)):.0f}/{round(float(tmin)):.0f} °C"
    parts = [f"{name_html}: {temp_part}", f"{descx}"]
    if isinstance(wind_ms, (int, float)):
        wind_part = f"💨 {float(wind_ms):.1f} м/с"
        if isinstance(wind_dir, int):
            wind_part += f" ({compass(wind_dir)})"
        if isinstance(gust, (int, float)):
            wind_part += f" • порывы {float(gust):.0f}"
        parts.append(wind_part)
    if isinstance(press_val, int):
        parts.append(f" {press_val} гПа {press_trend}")
    if include_sst:
        sst = get_sst_cached(la, lo)
        if isinstance(sst, (int, float)):
            parts.append(f"🌊 {float(sst):.0f}")
    return float(tmax), " • ".join(parts)


def _water_highlights(city: str, la: float, lo: float, tz_obj: pendulum.Timezone) -> Optional[str]:
    wm = get_weather(la, lo) or {}
    wind_ms, wind_dir, _, _ = pick_tomorrow_header_metrics(wm, tz_obj)
    wave_h, _ = _fetch_wave_for_tomorrow(la, lo, tz_obj)

    def _gust_at_noon(wm0: Dict[str, Any], tz0: pendulum.Timezone) -> Optional[float]:
        hourly = wm0.get("hourly") or {}
        times = _hourly_times(wm0)

        # FIX: today() вместо now() — консистентность с WORK_DATE
        tom = pendulum.today(tz0).add(days=1).date()

        idx = _nearest_index_for_day(times, tom, 12, tz0)
        arr = _pick(hourly, "windgusts_10m", "wind_gusts_10m", "wind_gusts", default=[])
        if idx is not None and idx < len(arr):
            try:
                return kmh_to_ms(float(arr[idx]))
            except Exception:
                return None
        return None

    gust = _gust_at_noon(wm, tz_obj)
    sst = get_sst_cached(la, lo)
    wind_val = float(wind_ms) if isinstance(wind_ms, (int, float)) else None
    gust_val = float(gust) if isinstance(gust, (int, float)) else None
    card = _cardinal(float(wind_dir)) if isinstance(wind_dir, (int, float)) else None
    shore, shore_src = _shore_class(city, float(wind_dir) if isinstance(wind_dir, (int, float)) else None)

    kite_good = False
    if wind_val is not None:
        if KITE_WIND_GOOD_MIN <= wind_val <= KITE_WIND_GOOD_MAX:
            kite_good = True
        if shore == "offshore":
            kite_good = False
        if gust_val and wind_val and (gust_val / max(wind_val, 0.1) > KITE_GUST_RATIO_BAD):
            kite_good = False
        if wave_h is not None and wave_h >= KITE_WAVE_WARN:
            kite_good = False

    sup_good = False
    if wind_val is not None:
        if (wind_val <= SUP_WIND_GOOD_MAX) and (wave_h is None or wave_h <= SUP_WAVE_GOOD_MAX):
            sup_good = True
        if shore == "offshore" and wind_val >= OFFSHORE_SUP_WIND_MIN:
            sup_good = False

    surf_good = False
    if wave_h is not None:
        if SURF_WAVE_GOOD_MIN <= wave_h <= SURF_WAVE_GOOD_MAX and (
            wind_val is None or wind_val <= SURF_WIND_MAX
        ):
            surf_good = True

    goods: List[str] = []
    if kite_good:
        goods.append("Кайт/Винг/Винд")
    if sup_good:
        goods.append("SUP")
    if surf_good:
        goods.append("Сёрф")
    if not goods:
        return None

    dir_part = f" ({card}/{shore})" if card or shore else ""
    spot_part = (
        f" @{shore_src}"
        if shore_src
        and shore_src not in (city, f"ENV:SHORE_FACE_{_env_city_key(city)}")
        else ""
    )
    env_mark = " (ENV)" if shore_src and shore_src.startswith("ENV:") else ""
    suit_txt = _wetsuit_hint(sst)
    suit_part = f" • {suit_txt}" if suit_txt else ""
    return "🧜‍♂️ Отлично: " + "; ".join(goods) + spot_part + env_mark + dir_part + suit_part


# ───────────── хэштеги ─────────────
def hashtags_line(warm_city: Optional[str], cool_city: Optional[str]) -> str:
    base = ["#Кипр", "#погода", "#здоровье"]
    if warm_city:
        base.append("#" + _ru_city(warm_city).replace(" ", ""))
    if cool_city:
        base.append("#" + _ru_city(cool_city).replace(" ", ""))
    return " ".join(base[:5])


# ───────────── AI image helpers (storm / special moons) ─────────────
def storm_visual_cues_en(storm: Dict[str, Any]) -> str:
    reasons = []
    try:
        if isinstance(storm.get("max_speed_ms"), (int, float)) and storm["max_speed_ms"] >= 13:
            reasons.append("strong wind")
        if isinstance(storm.get("max_gust_ms"), (int, float)) and storm["max_gust_ms"] >= 17:
            reasons.append("violent gusts")
        if storm.get("heavy_rain"):
            reasons.append("heavy rain")
        if storm.get("thunder"):
            reasons.append("thunderstorm")
    except Exception:
        pass
    if not reasons:
        reasons = ["storm conditions"]

    return (
        "VISUAL STORM CUES: clearly show "
        + ", ".join(reasons)
        + " — dramatic dark clouds, visible wind streaks, rough waves, rain shafts, and a high-contrast sky. "
        "No calm sea. No 'nice weather' look."
    )


def moon_goddess_prompt_en(
    *,
    date: pendulum.Date,
    phase_title_ru: str,
    percent: int,
    sign_from: str,
    sign_to: str,
    is_storm: bool,
) -> Tuple[str, str]:
    sign_from_s = zsym(str(sign_from or "")).strip()
    sign_to_s = zsym(str(sign_to or "")).strip()
    change = f"{sign_from_s}→{sign_to_s}" if (sign_from_s and sign_to_s and sign_from_s != sign_to_s) else (sign_from_s or sign_to_s or "")
    mood = "stormy and powerful" if is_storm else "mystical and serene"

    prompt = f"""
Create a cinematic, premium square illustration for a Cyprus daily forecast post.
Theme: {phase_title_ru} ({percent}% illumination), Moon sign {change if change else 'unknown'}.
Date: {date.isoformat()}.

Scene: a moon goddess over the Mediterranean coast of Cyprus at night.
The Moon is large and clearly visible, with accurate illumination consistent with {phase_title_ru}.
Atmosphere: {mood}. Elegant, magical realism, high detail, soft volumetric light.
Include subtle Cyprus coastline cues (Mediterranean cliffs, coastal pines, moonlit sea).

Style: modern cinematic digital painting, sharp details, realistic lighting, no text, no watermark.
""".strip()
    return prompt, "moon_goddess"

def _build_cy_image_moods_for_evening(
    tz_obj: pendulum.Timezone,
    storm_warning: bool,
) -> tuple[str, str, str]:
    """
    Строит более живые описания моря/суши/астро для промта картинки на ЗАВТРА.
    Выбор детерминированный от даты (чтобы ретраи не меняли картинку).
    storm_warning передаётся извне (storm_flags_for_tomorrow(...)["warning"]).
    """
    try:
        stats = day_night_stats(CY_LAT, CY_LON, tz=tz_obj.name) or {}
        tmax = stats.get("t_day_max")
        tmin = stats.get("t_night_min")
    except Exception:
        tmax = tmin = None

    # Привязываем «настроение» к завтрашней дате (вечерний пост анонсирует завтра)
    tomorrow = pendulum.today(tz_obj).add(days=1).date()
    seed = int(tomorrow.toordinal() * 10007 + (17 if storm_warning else 0))
    rnd = random.Random(seed)

    # Море
    if storm_warning:
        marine_variants = [
            "windy Mediterranean evening with strong gusts, powerful waves and a dramatic sky",
            "stormy Cyprus coastline with rough sea, fast-moving clouds and very dynamic energy",
            "dark storm clouds above the coast, loud wind and textured waves, high contrast moonlight",
        ]
    elif isinstance(tmax, (int, float)) and tmax >= 30:
        marine_variants = [
            "hot shimmering Mediterranean evening with very warm water, almost tropical, ideal for a late swim",
            "glowing hot sunset by the sea, warm breeze and dense humid air, perfect for slow seaside walks",
        ]
    elif isinstance(tmax, (int, float)) and tmax >= 24:
        marine_variants = [
            "warm Mediterranean evening with gentle breeze and calm waves, perfect for relaxed seaside walks or SUP",
            "soft golden-hour light over a warm, friendly sea, ideal for coffee by the water and an easy swim",
        ]
    elif isinstance(tmax, (int, float)) and tmax >= 18:
        marine_variants = [
            "fresh but comfortable seaside evening, a bit of wind and small waves, good for a short walk in a light jacket",
            "cooler Mediterranean evening with slightly choppy water and clear air, more for watching waves than swimming",
        ]
    else:
        marine_variants = [
            "cool windy coastal evening with noticeable waves and crisp salty air, better for watching the sea from the shore",
            "chilly Mediterranean coastline with restless water and strong breeze, cozy if you have a warm hoodie",
        ]

    marine_mood = rnd.choice(marine_variants)

    # Суша / горы
    if isinstance(tmin, (int, float)) and tmin <= 8:
        inland_variants = [
            "cold mountain-like night in the inland hills, crisp, quiet air and very clear sky",
            "chilly highland evening with fresh, thin air and a sense of silence in the Troodos area",
        ]
    elif isinstance(tmin, (int, float)) and tmin <= 14:
        inland_variants = [
            "refreshing inland evening with cooler, calmer air and a grounded mountain mood",
            "mild but fresh night in inland towns, with cooler breezes and a quieter rhythm than the coast",
        ]
    else:
        inland_variants = [
            "soft inland evening with stable, gentle air and a slower, grounded pace",
            "warm, cozy night in inland areas, less humid than the sea, good for slow walks and conversations",
        ]

    inland_mood = rnd.choice(inland_variants)

    # Астро-настрой (не перегружаем — это просто «тон»)
    astro_variants = [
        "calm, grounded Moon energy supporting gentle planning and self-care for tomorrow",
        "soft, reflective sky mood, good for closing open loops and setting simple intentions for the next day",
        "balanced and stable cosmic weather, supporting rest, recovery and slow, conscious decisions",
        "a slightly electric, inspiring night-sky mood, good for creative ideas and light re-planning",
    ]
    astro_mood_en = rnd.choice(astro_variants)

    logging.info(
        "CY_IMG: moods chosen -> storm=%s, marine=%r, inland=%r, astro=%r",
        storm_warning,
        marine_mood,
        inland_mood,
        astro_mood_en,
    )
    return marine_mood, inland_mood, astro_mood_en


# ───────────── сообщение ─────────────
def build_message(
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
    mode: Optional[str] = None,
) -> str:
    # Защита от перепутанных аргументов (tz ←→ mode)
    if isinstance(tz, str) and tz.strip().lower() in ("morning", "evening", "am", "pm"):
        logging.warning("build_message: получен tz='%s' (похоже на mode). Перекладываю в mode.", tz)
        mode = tz
        tz = os.getenv("TZ", "Asia/Nicosia")

    logging.info(
        "build_message: mode=%s, tz=%s",
        (mode or "∅"),
        (tz if isinstance(tz, str) else getattr(tz, "name", "obj")),
    )

    tz_obj = _as_tz(tz)
    mode = (mode or os.getenv("POST_MODE") or os.getenv("MODE") or "evening").lower()
    is_morning = mode.startswith("morn")

    sea_pairs = _iter_city_pairs(sea_cities)
    other_pairs = _iter_city_pairs(other_cities)

    P: List[str] = []
    today = pendulum.today(tz_obj)
    tom = today.add(days=1)

    title_day = today if is_morning else tom
    title_word = "сегодня" if is_morning else "завтра"
    P.append(f"<b>{region_name}: погода на {title_word} ({title_day.format('DD.MM.YYYY')})</b>")

    wm_region = get_weather(CY_LAT, CY_LON) or {}
    storm_region = storm_flags_for_today(wm_region, tz_obj) if is_morning else storm_flags_for_tomorrow(wm_region, tz_obj)

    # === УТРО ===
    if is_morning:
        def _collect_city_tmax_list(spairs, opairs):
            all_pairs = list(spairs) + list(opairs)
            out: List[Tuple[str, float]] = []
            for city, (la, lo) in all_pairs:
                tmax, _line = _city_detail_line(city, la, lo, tz_obj, include_sst=False)
                if isinstance(tmax, (int, float)):
                    out.append((_ru_city(city), float(tmax)))
            return out

        rows = _collect_city_tmax_list(sea_pairs, other_pairs)
        warm = max(rows, key=lambda x: x[1]) if rows else None
        cool = min(rows, key=lambda x: x[1]) if rows else None

        fact = get_fact(today, region_name) or ""
        fact_short = re.sub(r"\s+", " ", fact).strip()
        greeting = "👋 Доброе утро!"
        if fact_short:
            greeting += f" {fact_short} "
        if warm and cool:
            spread = ""
            if abs(warm[1] - cool[1]) >= 0.5:
                spread = f" (диапазон {cool[1]:.0f}–{warm[1]:.0f}°)"
            greeting += (
                f"Теплее всего — {_ru_city(warm[0])} ({warm[1]:.0f}°), "
                f"прохладнее — {_ru_city(cool[0])} ({cool[1]:.0f}°){spread}."
            )
        P.append(greeting.strip())

        if storm_region.get("warning"):
            P.append(storm_region["warning_text"] + " Берегите планы и закладывайте время.")

        la_sun, lo_sun = _choose_sun_coords(sea_pairs, other_pairs)
        sun_line = sun_line_for_mode(mode, tz_obj, la_sun, lo_sun)
        if sun_line:
            P.append(sun_line)

        combo = _morning_combo_air_radiation_pollen(CY_LAT, CY_LON)
        if combo:
            P.append(combo)
            air_now = get_air(CY_LAT, CY_LON) or {}
            bad_air, tip = _is_air_bad(air_now)
            if bad_air and tip:
                P.append(f"ℹ️ {tip}")
        else:
            air_now = get_air(CY_LAT, CY_LON) or {}

        kp_val = None
        kp_age = None
        kp_label = "н/д"
        if USE_WORLD_KP:
            wv, age = _fetch_world_kp()
            kp_val, kp_age = wv, age
            kp_label = _kp_status_label(kp_val)

        sw = get_solar_wind() or {}
        v, n = sw.get("speed_kms"), sw.get("density")
        wind_status = sw.get("status", "н/д")
        parts_sw = []
        if isinstance(v, (int, float)):
            parts_sw.append(f"v {v:.0f} км/с")
        if isinstance(n, (int, float)):
            parts_sw.append(f"n {n:.1f} см⁻³")
        sw_tail = (
            " — " + wind_status
            if parts_sw and isinstance(wind_status, str) and wind_status not in ("", "н/д")
            else ""
        )
        sw_chunk = (", ".join(parts_sw) + sw_tail) if parts_sw or wind_status else "н/д"

        if isinstance(kp_val, (int, float)):
            age_txt = ""
            if isinstance(kp_age, int):
                age_txt = (
                    f", {kp_age // 60} ч назад"
                    if kp_age >= 180
                    else (f", {kp_age} мин назад" if kp_age >= 0 else "")
                )
            P.append(f"🧲 Космопогода: Kp {kp_val:.1f} ({kp_label}{age_txt}) • 🌬️ {sw_chunk}")
        else:
            P.append("🧲 Космопогода: Kp н/д • 🌬️ " + sw_chunk)

        bad_air, _ = _is_air_bad(air_now)
        air_icon = "🟢" if not bad_air else "🟡"
        storm = "без шторма" if not storm_region.get("warning") else "штормово"
        kp_status = _kp_status_label(kp_val)
        P.append(f"🔎 Итого: воздух {air_icon} • {storm} • Kp {kp_status}")

        tips = ["вода и завтрак"]
        if not bad_air:
            tips.append("20-мин прогулка до полудня")
        tips.append(
            "короткая растяжка вечером" if not storm_region.get("warning") else "без экранов за час до сна"
        )
        P.append("✅ Сегодня: " + ", ".join(tips) + ".")

        warm_name = warm[0] if warm else None
        cool_name = cool[0] if cool else None
        P.append(hashtags_line(warm_name, cool_name))
        return "\n".join(P)

    # === ВЕЧЕР ===
    if storm_region.get("warning"):
        P.append(storm_region["warning_text"])
        P.append("———")

    sea_rows: List[tuple[float, str]] = []
    for city, (la, lo) in sea_pairs:
        tmax, line = _city_detail_line(city, la, lo, tz_obj, include_sst=True)
        if tmax is not None and line:
            try:
                hl = _water_highlights(city, la, lo, tz_obj)
                if hl:
                    line = line + f"\n   {hl}"
            except Exception:
                pass
            sea_rows.append((float(tmax), line))
    if sea_rows:
        P.append(f"🏖 <b>{sea_label or 'Морские города'}</b>")
        sea_rows.sort(key=lambda x: x[0], reverse=True)
        medals = ["🥵", "😎", "😌", "🥶"]
        for i, (_, text) in enumerate(sea_rows[:5]):
            med = medals[i] if i < len(medals) else "•"
            P.append(f"{med} {text}")
        P.append("———")

    oth_rows: List[tuple[float, str]] = []
    for city, (la, lo) in other_pairs:
        tmax, line = _city_detail_line(city, la, lo, tz_obj, include_sst=False)
        if tmax is not None and line:
            oth_rows.append((float(tmax), line))
    if oth_rows:
        P.append("🏞 <b>Континентальные города</b>")
        oth_rows.sort(key=lambda x: x[0], reverse=True)
        for _, text in oth_rows:
            P.append(text)
        P.append("———")

    la_sun, lo_sun = _choose_sun_coords(sea_pairs, other_pairs)
    sun_line = sun_line_for_mode(mode, tz_obj, la_sun, lo_sun)
    if sun_line:
        P.append(sun_line)

    # Астроблок: используем ту же логическую дату, что и в заголовке (tomorrow),
    # плюс при необходимости дополнительный сдвиг через ASTRO_OFFSET.
    date_for_astro = tom
    P.append(build_astro_section(date_local=date_for_astro, tz_local=tz_obj.name))

    all_rows = sea_rows + oth_rows
    warm_name = cool_name = None
    if all_rows:
        all_rows_sorted = sorted(all_rows, key=lambda x: x[0], reverse=True)
        warm_name = re.sub(
            r"^<b>(.*?)</b>.*$", r"\1",
            all_rows_sorted[0][1].split(":")[0],
        )
        cool_name = re.sub(
            r"^<b>(.*?)</b>.*$", r"\1",
            all_rows_sorted[-1][1].split(":")[0],
        )
    P.append(hashtags_line(warm_name, cool_name))

    return "\n".join(P)



# ───────────── image validation (Telegram) ─────────────
def _is_telegram_image_ok(path: Union[str, Path], min_bytes: int = 8000) -> bool:
    """Cheap sanity-check to avoid Telegram 400 Image_process_failed on broken files."""
    try:
        p = Path(path)
        if not p.exists():
            return False
        size = p.stat().st_size
        if size < int(min_bytes):
            return False
        kind = imghdr.what(str(p))
        if kind in ("jpeg", "png", "webp"):
            return True
        # signature fallback
        head = p.open("rb").read(16)
        if head.startswith(b"\xff\xd8\xff"):
            return True  # JPEG
        if head.startswith(b"\x89PNG\r\n\x1a\n"):
            return True  # PNG
        if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
            return True  # WEBP
    except Exception:
        return False
    return False


def _image_debug_str(path: Union[str, Path]) -> str:
    try:
        p = Path(path)
        size = p.stat().st_size if p.exists() else -1
        kind = imghdr.what(str(p)) if p.exists() else None
        return f"kind={kind}, bytes={size}"
    except Exception:
        return "kind=?, bytes=?"


# ───────────── Telegram caption helper (Cyprus evening image) ─────────────
_TELEGRAM_PHOTO_CAPTION_LIMIT = 1024


def _cyprus_short_photo_caption(full_msg: str) -> str:
    """
    Короткая подпись под фото, когда полный текст не помещается в caption (лимит 1024).
    Делаем: заголовок + (если есть) строка про шторм + маркер, что полный текст ниже.
    """
    lines = [l for l in (full_msg or "").splitlines() if l.strip()]
    if not lines:
        return "⬇️ Полный прогноз — следующим сообщением."

    out: List[str] = [lines[0]]

    for l in lines[1:6]:
        if "Штормовое предупреждение" in l or l.strip().startswith("⚠️"):
            out.append(l)
            break

    out.append("⬇️ Полный прогноз — следующим сообщением.")
    caption = "\n".join(out).strip()

    if len(caption) > _TELEGRAM_PHOTO_CAPTION_LIMIT:
        caption = caption[: _TELEGRAM_PHOTO_CAPTION_LIMIT - 1] + "…"
    return caption

_TG_CAPTION_LIMIT = 1024

def _build_short_photo_caption(full_msg: str, max_len: int = _TG_CAPTION_LIMIT) -> str:
    """
    Короткая подпись к фото, чтобы не упираться в лимит Telegram caption=1024.
    Делаем безопасно (без обрезки HTML-тегов в середине).
    """
    lines = [l.strip() for l in (full_msg or "").splitlines() if l.strip()]
    if not lines:
        return "⬇️ Полный прогноз — следующим сообщением."

    header = lines[0]
    storm_line = next((l for l in lines if "Штормовое предупреждение" in l), "")

    cand = [header]
    if storm_line:
        cand.append(storm_line)
    cand.append("⬇️ Полный прогноз — следующим сообщением.")
    caption = "\n".join(cand)

    if len(caption) <= max_len:
        return caption

    # без storm_line
    caption = "\n".join([header, "⬇️ Полный прогноз — следующим сообщением."])
    if len(caption) <= max_len:
        return caption

    # крайний случай: оставляем только заголовок (почти всегда короткий)
    if len(header) <= max_len:
        return header

    # редкий случай: если заголовок очень длинный — аккуратно обрежем, но он обычно без длинных HTML
    return header[: max_len - 1] + "…"


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
    # Собираем текст сообщения (как и раньше)
    msg = build_message(
        region_name=region_name,
        sea_label=sea_label,
        sea_cities=sea_cities,
        other_label=other_label,
        other_cities=other_cities,
        tz=tz,
        mode=mode,
    )

    # Режим
    try:
        effective_mode = (mode or os.getenv("POST_MODE") or os.getenv("MODE") or "evening").lower()
    except Exception:
        effective_mode = "evening"

    # Флаг картинки
    cy_img_env = os.getenv("CY_IMG_ENABLED", "1")
    enable_img = cy_img_env.strip().lower() not in ("0", "false", "no", "off")

    logging.info(
        "CY_IMG: mode=%s, CY_IMG_ENABLED=%s -> enable_img=%s",
        effective_mode,
        cy_img_env,
        enable_img,
    )

    img_path: Optional[str] = None
    storm_warning: bool = False
    # 1) Генерация картинки (утро/вечер; приоритет: шторм > спец-луна > обычная)
    if enable_img and (effective_mode.startswith("evening") or effective_mode.startswith("morning")):
        try:
            tz_obj = _as_tz(tz)

            base_dt = pendulum.today(tz_obj)
            target_date = base_dt.date() if effective_mode.startswith("morning") else base_dt.add(days=1).date()

            # Шторм-флаг должен соответствовать фактическому дню поста
            wm_region = get_weather(CY_LAT, CY_LON) or {}
            storm_region = storm_flags_for_today(wm_region, tz_obj) if effective_mode.startswith("morning") else storm_flags_for_tomorrow(wm_region, tz_obj)
            storm_warning = bool(storm_region.get("warning"))

            # Лунный спец-эвент (если есть)
            special = lunar_special_event_for_date(target_date, tz_local=tz_obj.name)
            theme_tag = "storm" if storm_warning else (special.get("type") if isinstance(special, dict) else None) or "regular"

            prompt: str = ""
            style_name: str = "default"

            # A) Шторм — всегда главный
            if storm_warning:
                if effective_mode.startswith("evening"):
                    marine_mood, inland_mood, astro_mood_en = _build_cy_image_moods_for_evening(
                        tz_obj=tz_obj,
                        storm_warning=True,
                    )
                    prompt, style_name = build_cyprus_evening_prompt(
                        date=target_date,
                        marine_mood=marine_mood,
                        inland_mood=inland_mood,
                        astro_mood_en=astro_mood_en,
                        storm_warning=True,
                    )
                else:
                    prompt = f"""
Create a premium square illustration for a Cyprus morning forecast.
Date: {target_date.isoformat()}.

{storm_visual_cues_en(storm_region)}

Scene: Cyprus coastline and city silhouette at dawn, with clearly visible storm conditions.
Style: cinematic digital painting, high detail, realistic lighting, no text, no watermark.
""".strip()
                    style_name = "storm_morning"

                prompt = (prompt + "\n\n" + storm_visual_cues_en(storm_region)).strip()

            # B) Спец-луна (если не шторм)
            elif isinstance(special, dict) and special.get("type") in ("new_moon", "full_moon"):
                phase_title = str(special.get("title") or "Луна")
                percent = int(special.get("percent") or 0)
                sign_from = str(special.get("sign_from") or "")
                sign_to = str(special.get("sign_to") or sign_from or "")
                prompt, style_name = moon_goddess_prompt_en(
                    date=target_date,
                    phase_title_ru=phase_title,
                    percent=percent,
                    sign_from=sign_from,
                    sign_to=sign_to,
                    is_storm=False,
                )

                if effective_mode.startswith("morning"):
                    prompt = (
                        prompt
                        + "\n\nAdditional constraint: make it dawn / early morning light, but keep the Moon clearly visible."
                    )

            # C) Обычная картинка
            else:
                if effective_mode.startswith("evening"):
                    marine_mood, inland_mood, astro_mood_en = _build_cy_image_moods_for_evening(
                        tz_obj=tz_obj,
                        storm_warning=False,
                    )
                    prompt, style_name = build_cyprus_evening_prompt(
                        date=target_date,
                        marine_mood=marine_mood,
                        inland_mood=inland_mood,
                        astro_mood_en=astro_mood_en,
                        storm_warning=False,
                    )
                else:
                    # Утро: 5 стилей с авто-ротацией по дате
                    style_env = (os.getenv("CY_MORNING_STYLE", "auto") or "auto").strip().lower()
                    try:
                        seed_off = int(os.getenv("CY_MORNING_SEED_OFFSET", "0") or "0")
                    except Exception:
                        seed_off = 0

                    if style_env in ("1", "2", "3", "4", "5"):
                        style_idx = int(style_env)
                    else:
                        style_idx = ((int(target_date.toordinal()) + seed_off) % 5) + 1

                    style_variants = {
                        1: "cinematic realistic photo look, dramatic but calm",
                        2: "soft watercolor illustration, airy light, gentle textures",
                        3: "minimalist poster art, clean shapes, premium design",
                        4: "dreamy anime-inspired illustration, warm morning palette",
                        5: "3D stylized illustration, high-end render, soft shadows",
                    }
                    style_hint = style_variants.get(style_idx, style_variants[1])
                    style_name = f"cy_morning_{style_idx}"

                    try:
                        stats = day_night_stats(CY_LAT, CY_LON, tz=tz_obj.name) or {}
                        tmax = stats.get("t_day_max")
                        tmin = stats.get("t_night_min")
                    except Exception:
                        tmax = tmin = None

                    wm0 = get_weather(CY_LAT, CY_LON) or {}
                    wc0 = None
                    try:
                        wcx = (wm0.get("daily", {}) or {}).get("weathercode", [])
                        wc0 = wcx[0] if isinstance(wcx, list) and len(wcx) >= 1 else None
                    except Exception:
                        wc0 = None
                    desc0 = code_desc(wc0) or "clear conditions"

                    temp_hint = ""
                    try:
                        if isinstance(tmax, (int, float)) and isinstance(tmin, (int, float)):
                            temp_hint = f"Temperature range: {float(tmin):.0f}–{float(tmax):.0f}°C."
                    except Exception:
                        temp_hint = ""

                    prompt = f"""
Create a premium square illustration for a Cyprus morning forecast post.
Date: {target_date.isoformat()}.
Weather: {desc0}. {temp_hint}

Scene: Cyprus coastline (Mediterranean sea, rocks, coastal pines), morning sun near the horizon.
Style direction: {style_hint}.
No text, no watermark.
""".strip()

            logging.info(
                "CY_IMG: prompt selected -> mode=%s, date=%s, theme=%s, style=%s, len=%d",
                effective_mode,
                target_date.isoformat(),
                theme_tag,
                style_name,
                len(prompt),
            )

            img_dir = Path("cy_images")
            img_dir.mkdir(parents=True, exist_ok=True)

            safe_style = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(style_name) if style_name else "default")
            safe_theme = re.sub(r"[^a-zA-Z0-9_-]+", "_", str(theme_tag) if theme_tag else "regular")
            mode_tag = "morning" if effective_mode.startswith("morning") else "evening"
            img_file = img_dir / f"cyprus_{mode_tag}_{target_date.isoformat()}_{safe_theme}_{safe_style}.jpg"

            min_bytes = int(os.getenv("CY_IMG_MIN_BYTES", "8000") or "8000")
            max_gen = int(os.getenv("CY_IMG_GEN_ATTEMPTS", "2") or "2")

            for gen_try in range(1, max_gen + 1):
                logging.info(
                    "CY_IMG: calling generate_astro_image (%d/%d) -> %s",
                    gen_try,
                    max_gen,
                    img_file,
                )
                img_path = generate_astro_image(prompt, str(img_file))
                ok = bool(
                    img_path
                    and Path(img_path).exists()
                    and _is_telegram_image_ok(img_path, min_bytes=min_bytes)
                )
                logging.info(
                    "CY_IMG: generate_astro_image returned %r (%s), ok=%s",
                    img_path,
                    _image_debug_str(img_path) if img_path else "no-file",
                    ok,
                )
                if ok:
                    break
                # remove broken file (Telegram часто отвечает Image_process_failed на мусор 0–2KB)
                try:
                    if img_path and Path(img_path).exists():
                        Path(img_path).unlink()
                except Exception:
                    pass
                img_path = None

        except Exception as exc:
            logging.exception("Cyprus image generation failed: %s", exc)
            img_path = None
    else:
        logging.info(
            "CY_IMG: skipped image generation (enable_img=%s, mode=%s)",
            enable_img,
            effective_mode,
        )

    # 2) Отправка фото (если есть) + при необходимости полный текст вторым сообщением
    if img_path and Path(img_path).exists():
        try:
            min_bytes = int(os.getenv("CY_IMG_MIN_BYTES", "8000") or "8000")
            if not _is_telegram_image_ok(img_path, min_bytes=min_bytes):
                raise RuntimeError(f"invalid image file: {_image_debug_str(img_path)}")
            logging.info("CY_IMG: sending photo %s", img_path)

            need_split = len(msg) > _TELEGRAM_PHOTO_CAPTION_LIMIT
            caption = msg if not need_split else _cyprus_short_photo_caption(msg)

            with open(img_path, "rb") as f:
                sent = await bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=caption,
                    parse_mode=constants.ParseMode.HTML,
                )

            if need_split:
                try:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=msg,
                        parse_mode=constants.ParseMode.HTML,
                        disable_web_page_preview=True,
                        reply_to_message_id=getattr(sent, "message_id", None),
                    )
                except TypeError:
                    await bot.send_message(
                        chat_id=chat_id,
                        text=msg,
                        parse_mode=constants.ParseMode.HTML,
                        disable_web_page_preview=True,
                    )

            return

        except Exception as exc:
            logging.exception("Sending photo failed, fallback to text: %s", exc)

    # 3) Если хотели картинку, но не получилось — лог
    if enable_img and (effective_mode.startswith("evening") or effective_mode.startswith("morning")):
        logging.warning(
            "CY_IMG: image not sent (img_path=%r, exists=%s)",
            img_path,
            bool(img_path and Path(img_path).exists()),
        )

    # 4) Фолбэк: обычное текстовое сообщение
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
    tz: Union[pendulum.Timezone, str],
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
    "pick_tomorrow_header_metrics",
    "storm_flags_for_today",
    "storm_flags_for_tomorrow",
]
