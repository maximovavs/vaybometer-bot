#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота (Кипр), рендер «как в KLD».
"""

from __future__ import annotations
import os, json, logging, asyncio, re, math, sys
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

import pendulum
from telegram import Bot, error as tg_err

from utils   import (
    compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji,
    kmh_to_ms, smoke_index, _get
)
from weather import get_weather, fetch_tomorrow_temps, day_night_stats
from air     import get_air, get_sst
from pollen  import get_pollen
from schumann import get_schumann
from gpt     import gpt_blurb
import radiation  # ☢️

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────── базовые константы ──────────────────
TZ        = pendulum.timezone("Asia/Nicosia")
TODAY     = pendulum.today(TZ)
TOMORROW  = TODAY.add(days=1).date()

# Координаты (Кипр)
CITIES: Dict[str, Tuple[float, float]] = {
    "Limassol":  (34.707, 33.022),
    "Nicosia":   (35.170, 33.360),
    "Pafos":     (34.776, 32.424),
    "Ayia Napa": (34.988, 34.012),
    "Troodos":   (34.916, 32.823),
    "Larnaca":   (34.916, 33.624),
}
COASTAL_CITIES = {"Larnaca", "Limassol", "Pafos", "Ayia Napa"}
RATING_ORDER = ["Limassol","Nicosia","Pafos","Ayia Napa","Troodos","Larnaca"]

WMO_DESC = {
    0: "☀️ ясно", 1: "⛅ ч.обл", 2: "☁️ обл", 3: "🌥 пасм",
    45: "🌫 туман", 48: "🌫 изморозь", 51: "🌦 морось",
    61: "🌧 дождь", 71: "❄️ снег", 95: "⛈ гроза",
}
def code_desc(c: Any) -> Optional[str]:
    try:
        return WMO_DESC.get(int(c))
    except Exception:
        return None

# ────────── helpers: время/часовки для завтра ──────────
def _hourly_times(wm: Dict[str, Any]) -> List[pendulum.DateTime]:
    hourly = wm.get("hourly") or {}
    times = hourly.get("time") or hourly.get("time_local") or hourly.get("timestamp") or []
    out: List[pendulum.DateTime] = []
    for t in times:
        try: out.append(pendulum.parse(str(t)))
        except Exception: pass
    return out

def _nearest_index(times: List[pendulum.DateTime], date_obj: pendulum.Date, prefer_hour: int) -> Optional[int]:
    if not times: return None
    target = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, prefer_hour, 0, tz=TZ)
    best_i, best_diff = None, None
    for i, dt in enumerate(times):
        try: dl = dt.in_tz(TZ)
        except Exception: dl = dt
        if dl.date() != date_obj: continue
        diff = abs((dl - target).total_seconds())
        if best_diff is None or diff < best_diff:
            best_i, best_diff = i, diff
    return best_i

def _tomorrow_indices(wm: Dict[str, Any]) -> List[int]:
    times = _hourly_times(wm)
    idxs: List[int] = []
    for i, dt in enumerate(times):
        try:
            if dt.in_tz(TZ).date() == TOMORROW:
                idxs.append(i)
        except Exception:
            pass
    return idxs

def _circular_mean_deg(deg_list: List[float]) -> Optional[float]:
    if not deg_list: return None
    x = sum(math.cos(math.radians(d)) for d in deg_list)
    y = sum(math.sin(math.radians(d)) for d in deg_list)
    if x == 0 and y == 0: return None
    ang = math.degrees(math.atan2(y, x))
    return (ang + 360.0) % 360.0

# ────────── ветер/давление в шапку + порывы ──────────
def pick_header_metrics(wm: Dict[str, Any]) -> Tuple[Optional[float], Optional[int], Optional[int], str, Optional[float]]:
    """Возвращает: wind_ms, wind_dir_deg, pressure_hpa, pressure_trend(↑/↓/→), gust_max_ms"""
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)
    idx_noon = _nearest_index(times, TOMORROW, 12)
    idx_morn = _nearest_index(times, TOMORROW, 6)

    spd = hourly.get("wind_speed_10m") or hourly.get("windspeed_10m") or hourly.get("windspeed") or []
    dr  = hourly.get("wind_direction_10m") or hourly.get("winddirection_10m") or hourly.get("winddirection") or []
    pr  = hourly.get("surface_pressure") or hourly.get("pressure") or []
    gs  = hourly.get("wind_gusts_10m") or hourly.get("wind_gusts") or hourly.get("windgusts_10m") or []

    wind_ms = wind_dir = press_val = None
    trend = "→"

    if idx_noon is not None:
        try: wind_ms = kmh_to_ms(float(spd[idx_noon])) if idx_noon < len(spd) else None
        except Exception: pass
        try: wind_dir = int(round(float(dr[idx_noon]))) if idx_noon < len(dr) else None
        except Exception: pass
        try: p_noon = float(pr[idx_noon]) if idx_noon < len(pr) else None
        except Exception: p_noon = None
        try: p_morn = float(pr[idx_morn]) if (idx_morn is not None and idx_morn < len(pr)) else None
        except Exception: p_morn = None
        if p_noon is not None: press_val = int(round(p_noon))
        if (p_noon is not None) and (p_morn is not None):
            diff = p_noon - p_morn
            trend = "↑" if diff >= 0.3 else "↓" if diff <= -0.3 else "→"

    if wind_ms is None or wind_dir is None or press_val is None:
        idxs = _tomorrow_indices(wm)
        if idxs:
            try: spds = [float(spd[i]) for i in idxs if i < len(spd)]
            except Exception: spds = []
            try: dirs = [float(dr[i]) for i in idxs if i < len(dr)]
            except Exception: dirs = []
            try: prs = [float(pr[i]) for i in idxs if i < len(pr)]
            except Exception: prs = []
            if spds: wind_ms = kmh_to_ms(sum(spds)/len(spds))
            md = _circular_mean_deg(dirs)
            wind_dir = int(round(md)) if md is not None else wind_dir
            if prs: press_val = int(round(sum(prs)/len(prs)))

    gust_max_ms = None
    idxs = _tomorrow_indices(wm)
    if gs and idxs:
        vals = []
        for i in idxs:
            if i < len(gs):
                try: vals.append(float(gs[i]))
                except Exception: ...
        if vals:
            gust_max_ms = kmh_to_ms(max(vals))

    return wind_ms, wind_dir, press_val, trend, gust_max_ms

def pressure_arrow(trend: str) -> str:
    return {"↑":"↑","↓":"↓","→":"→"}.get(trend, "→")

# ────────── шторм-флаги ──────────
def storm_flags_for_tomorrow(wm: Dict[str, Any]) -> Dict[str, Any]:
    hourly = wm.get("hourly") or {}
    idxs = _tomorrow_indices(wm)
    if not idxs: return {"warning": False}

    def _arr(*names, default=None):
        for n in names:
            v = hourly.get(n)
            if isinstance(v, list): return v
        return default or []

    def _vals(arr):
        out = []
        for i in idxs:
            if i < len(arr):
                try: out.append(float(arr[i]))
                except Exception: pass
        return out

    speeds = _vals(_arr("windspeed_10m", "windspeed", "wind_speed_10m", "wind_speed"))
    gusts  = _vals(_arr("windgusts_10m", "wind_gusts_10m", "wind_gusts"))
    rain   = _vals(_arr("rain"))
    tprob  = _vals(_arr("thunderstorm_probability"))

    max_speed_ms = kmh_to_ms(max(speeds)) if speeds else None
    max_gust_ms  = kmh_to_ms(max(gusts))  if gusts  else None
    heavy_rain   = (max(rain) >= 8.0) if rain else False
    thunder      = (max(tprob) >= 60) if tprob else False

    reasons = []
    if isinstance(max_speed_ms, (int, float)) and max_speed_ms >= 13: reasons.append(f"ветер до {max_speed_ms:.0f} м/с")
    if isinstance(max_gust_ms,  (int, float)) and max_gust_ms  >= 17: reasons.append(f"порывы до {max_gust_ms:.0f} м/с")
    if heavy_rain: reasons.append("сильный дождь")
    if thunder:    reasons.append("гроза")

    return {
        "max_speed_ms": max_speed_ms,
        "max_gust_ms": max_gust_ms,
        "heavy_rain": heavy_rain,
        "thunder": thunder,
        "warning": bool(reasons),
        "warning_text": "⚠️ <b>Штормовое предупреждение</b>: " + ", ".join(reasons) if reasons else "",
    }

# ────────── NOAA: Kp + свежесть ──────────
def fetch_kp_recent() -> Tuple[Optional[float], Optional[str], Optional[int]]:
    try:
        j = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json", timeout=20).json()
        if isinstance(j, list) and len(j) >= 2:
            last = j[-1]
            t = pendulum.parse(str(last[0])).in_tz("UTC")
            kp = float(last[1])
            age_h = int((pendulum.now("UTC") - t).total_hours())
            status = "спокойно" if kp < 3 else ("умеренно" if kp < 5 else "буря")
            return kp, status, age_h
    except Exception:
        pass
    return None, None, None

# ────────── NOAA: Солнечный ветер ──────────
def fetch_solar_wind() -> Optional[Dict[str, float|str]]:
    try:
        j = _get("https://services.swpc.noaa.gov/products/summary/solar-wind.json", timeout=20).json()
        def pick(obj, key):
            x = obj.get(key)
            if isinstance(x, dict):
                return x.get("value")
            return None
        if isinstance(j, dict):
            bz = pick(j, "bz"); bt = pick(j, "bt")
            v  = pick(j, "speed"); n = pick(j, "density")
            vals = {}
            for k,vv in (("bz",bz),("bt",bt),("v_kms",v),("n",n)):
                try: vals[k] = float(vv) if vv is not None else None
                except Exception: vals[k] = None
            bzv = vals.get("bz"); vv = vals.get("v_kms"); dn = vals.get("n")
            danger = (bzv is not None and bzv <= -10) or (vv is not None and vv >= 600) or (dn is not None and dn >= 20)
            warn   = (bzv is not None and bzv <= -6)  or (vv is not None and vv >= 500) or (dn is not None and dn >= 10)
            vals["mood"] = "буря" if danger else ("возмущённо" if warn else "спокойно")
            return vals
    except Exception:
        pass
    return None

# ────────── Шуман (с фоллбэком) ──────────
def _trend_text(sym: str) -> str:
    return {"↑": "растёт", "↓": "снижается", "→": "стабильно"}.get(sym, "стабильно")

def _trend_from_series(vals: List[float], delta: float = 0.1) -> str:
    tail = vals[-24:] if len(vals) > 24 else vals
    if len(tail) < 2: return "→"
    avg_prev = sum(tail[:-1])/(len(tail)-1)
    d = tail[-1] - avg_prev
    return "↑" if d >= delta else "↓" if d <= -delta else "→"

def get_schumann_with_fallback() -> Dict[str, Any]:
    sch = (get_schumann() or {})
    if isinstance(sch.get("freq"), (int, float)) and isinstance(sch.get("amp"), (int, float)):
        sch["cached"] = False
        sch["trend_text"] = _trend_text(sch.get("trend", "→"))
        return sch

    cache = Path(__file__).parent / "schumann_hourly.json"
    if cache.exists():
        try:
            arr = json.loads(cache.read_text(encoding="utf-8")) or []
            amps  = [float(x["amp"])  for x in arr if isinstance(x.get("amp"), (int, float))]
            last  = arr[-1] if arr else {}
            trend = _trend_from_series(amps) if amps else "→"
            return {
                "freq": float(last["freq"]) if isinstance(last.get("freq"), (int, float)) else None,
                "amp":  float(last["amp"])  if isinstance(last.get("amp"),  (int, float)) else None,
                "trend": trend,
                "trend_text": _trend_text(trend),
                "cached": True,
                "status": "🟢 в норме" if (isinstance(last.get("freq"), (int, float)) and 7.7 <= float(last["freq"]) <= 8.1)
                          else ("🟡 колебания" if isinstance(last.get("freq"), (int, float)) and 7.4 <= float(last["freq"]) <= 8.4
                                else "🔴 сильное отклонение")
            }
        except Exception:
            pass
    return {"freq": None, "amp": None, "trend": "→", "trend_text": "стабильно", "cached": True, "status": "🟡 колебания"}

def schumann_lines(s: Dict[str, Any]) -> List[str]:
    freq = s.get("freq"); amp = s.get("amp")
    trend_text = s.get("trend_text", "стабильно")
    cached = s.get("cached", False)
    status = s.get("status", "🟡 колебания")
    stale = " ⏳ нет свежих чисел" if cached else ""
    if not isinstance(freq, (int, float)) and not isinstance(amp, (int, float)):
        return [f"{status}{stale} • тренд: {trend_text} • H7: — нет данных",
                "Волны Шумана близки к норме или колеблются в пределах дня."]
    main = f"{status}{stale} • Шуман: {freq:.2f} Гц / {amp:.1f} pT • тренд: {trend_text} • H7: — н/д"
    return [main, "Информация носит ориентировочный характер; ориентируйтесь на самочувствие."]

# ────────── Air → оценка риска для «Вывода» ──────────
def _is_air_bad(air: Dict[str, Any]) -> Tuple[bool, str, str]:
    def _num(x):
        try: return float(x)
        except Exception: return None
    aqi = _num(air.get("aqi"))
    p25 = _num(air.get("pm25"))
    p10 = _num(air.get("pm10"))
    bad = False
    label = "умеренный"
    reasons = []
    if aqi is not None and aqi >= 100:
        bad = True; reasons.append(f"AQI {aqi:.0f}")
        if aqi >= 150: label = "высокий"
    if p25 is not None and p25 > 35:
        bad = True; reasons.append(f"PM₂.₅ {p25:.0f}")
        if p25 > 55: label = "высокий"
    if p10 is not None and p10 > 50:
        bad = True; reasons.append(f"PM₁₀ {p10:.0f}")
        if p10 > 100: label = "высокий"
    return bad, label, ", ".join(reasons) if reasons else "показатели в норме"

# ────────── Лунный календарь ──────────
def _load_calendar(path: str = "lunar_calendar.json") -> dict:
    try: data = json.loads(Path(path).read_text("utf-8"))
    except Exception: return {}
    if isinstance(data, dict) and isinstance(data.get("days"), dict):
        return data["days"]
    return data if isinstance(data, dict) else {}

_ZODIAC = {
    "Овен": "♈","Телец": "♉","Близнецы": "♊","Рак": "♋","Лев": "♌",
    "Дева": "♍","Весы": "♎","Скорпион": "♏","Стрелец": "♐",
    "Козерог": "♑","Водолей": "♒","Рыбы": "♓",
}
def _zsym(s: str) -> str:
    for k,v in _ZODIAC.items(): s = s.replace(k, v)
    return s

def _parse_voc_dt(s: str, tz: pendulum.Timezone):
    if not s: return None
    try: return pendulum.parse(s).in_tz(tz)
    except Exception:
        try:
            dmy, hm = s.split()
            d, m = map(int, dmy.split("."))
            hh, mm = map(int, hm.split(":"))
            year = pendulum.today(tz).year
            return pendulum.datetime(year, m, d, hh, mm, tz=tz)
        except Exception:
            return None

def _voc_interval(rec: dict, tz_local: str = "Asia/Nicosia"):
    voc = (rec or {}).get("void_of_course") or (rec or {}).get("voc") or (rec or {}).get("void") or {}
    if not isinstance(voc, dict): return None
    s = voc.get("start") or voc.get("from") or voc.get("start_time")
    e = voc.get("end")   or voc.get("to")   or voc.get("end_time")
    if not s or not e: return None
    tz = pendulum.timezone(tz_local)
    t1 = _parse_voc_dt(s, tz); t2 = _parse_voc_dt(e, tz)
    if not t1 or not t2: return None
    return t1, t2

def build_astro_section_for_tomorrow() -> List[str]:
    tz = TZ
    date_local = pendulum.today(tz).add(days=1)
    cal = _load_calendar("lunar_calendar.json")
    rec = cal.get(date_local.format("YYYY-MM-DD"), {}) if isinstance(cal, dict) else {}

    phase_raw = (rec.get("phase_name") or rec.get("phase") or "").strip()
    phase_name = re.sub(r"^[^\wА-Яа-яЁё]+", "", phase_raw).split(",")[0].strip() or "Луна"
    percent = rec.get("percent") or rec.get("illumination") or rec.get("illum") or 0
    try: percent = int(round(float(percent)))
    except Exception: percent = 0
    sign = rec.get("sign") or rec.get("zodiac") or ""
    bullets = rec.get("advice") or []

    lines = ["🌌 <b>Астрособытия</b>"]
    # компактная строка как в KLD
    base = f"{phase_name} ({percent}%)" if percent else phase_name
    if sign: base += f" в {_zsym(sign)}"
    lines.append(f"🌙 {base}.")
    voc = _voc_interval(rec, tz_local=tz.name)
    if voc:
        t1, t2 = voc
        lines.append(f"⏳ Период без курса: {t1.format('HH:mm')}–{t2.format('HH:mm')}.")
    # 1–2 мягкие рекомендации
    if os.getenv("DISABLE_LLM_DAILY","0").lower() not in ("1","true","yes","on"):
        try:
            _, tips = gpt_blurb("астрология")
            tips = [t.strip() for t in tips if t.strip()][:2]
            for t in tips: lines.append(t)
        except Exception:
            pass
    return lines

# ────────── «Умный вывод» ──────────
def build_conclusion(kp: Optional[float], kp_status: str,
                     air: Dict[str, Any],
                     gust_ms: Optional[float],
                     schu: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    air_bad, air_label, air_reason = _is_air_bad(air)
    storm_main = isinstance(gust_ms, (int, float)) and gust_ms >= 17
    kp_main = isinstance(kp, (int, float)) and kp >= 5
    schu_main = (schu or {}).get("status","").startswith("🔴")

    storm_text = f"штормовая погода: порывы до {gust_ms:.0f} м/с" if storm_main else None
    air_text   = f"качество воздуха: {air_label} ({air_reason})" if air_bad else None
    kp_text    = f"магнитная активность: Kp≈{kp:.1f} ({kp_status})" if kp_main else None
    schu_text  = "сильные колебания Шумана" if schu_main else None

    if storm_main:
        lines.append(f"Основной фактор — {storm_text}. Планируйте дела с учётом погоды.")
    elif air_bad:
        lines.append(f"Основной фактор — {air_text}. Сократите время на улице и проветривание по ситуации.")
    elif kp_main:
        lines.append(f"Основной фактор — {kp_text}. Возможна чувствительность у метеозависимых.")
    elif schu_main:
        lines.append("Основной фактор — волны Шумана: отмечаются сильные отклонения.")
    else:
        lines.append("Серьёзных факторов риска не видно — ориентируйтесь на текущую погоду и планы.")

    secondary = [t for t in (storm_text, air_text, kp_text, schu_text) if t]
    if secondary:
        primary = lines[0]
        rest = [t for t in secondary if t not in primary]
        if rest:
            lines.append("Также обратите внимание: " + "; ".join(rest[:2]) + ".")
    return lines

# ────────── формат строки города ──────────
def _city_line(city: str, la: float, lo: float) -> str:
    wm  = get_weather(la, lo) or {}
    st  = day_night_stats(la, lo, tz=TZ.name) or {}
    t_day, t_night = st.get("t_day_max"), st.get("t_night_min")
    rh_min, rh_max = st.get("rh_min"), st.get("rh_max")
    wcarr = (wm.get("daily", {}) or {}).get("weathercode", [])
    wc = wcarr[1] if isinstance(wcarr, list) and len(wcarr) > 1 else None
    wind_ms, wind_dir, press_hpa, p_trend, gust_max = pick_header_metrics(wm)
    sst = get_sst(la, lo) if city in COASTAL_CITIES else None

    parts = [
        f"{city}: {(t_day if t_day is not None else 'н/д')}/{(t_night if t_night is not None else 'н/д')} °C",
        (code_desc(wc) or "—"),
        (f"💨 {wind_ms:.1f} м/с ({compass(wind_dir)})" if isinstance(wind_ms,(int,float)) and wind_dir is not None
            else (f"💨 {wind_ms:.1f} м/с" if isinstance(wind_ms,(int,float)) else "💨 н/д")),
        (f"порывы до {gust_max:.0f}" if isinstance(gust_max,(int,float)) else None),
        (f"💧 RH {rh_min:.0f}–{rh_max:.0f}%" if rh_min is not None and rh_max is not None else None),
        (f"🔹 {press_hpa} гПа {pressure_arrow(p_trend)}" if isinstance(press_hpa,int) else None),
        (f"🌊 {sst:.1f}" if isinstance(sst,(int,float)) else None),
    ]
    return " • ".join([p for p in parts if p])

# ───────────────────────── build_msg ─────────────────────────
def build_msg() -> str:
    P: List[str] = []
    P.append(f"<b>🌅 Кипр: погода на завтра ({TOMORROW.strftime('%d.%m.%Y')})</b>")
    P.append("———")

    # Рейтинг/перечень городов — подробно
    P.append("🎖️ <b>Города (д./н. °C, погода, ветер, RH, давление, 🌊)</b>")
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣"]
    # сортировка по дневной температуре, но выводим все
    temps_for_sort = []
    for city in RATING_ORDER:
        la, lo = CITIES[city]
        d,_ = fetch_tomorrow_temps(la, lo, tz=TZ.name)
        temps_for_sort.append((city, d if d is not None else -999))
    order = [c for c,_ in sorted(temps_for_sort, key=lambda x: x[1], reverse=True)]
    for i, city in enumerate(order[:len(medals)]):
        la, lo = CITIES[city]
        P.append(f"{medals[i]} " + _city_line(city, la, lo))
    P.append("———")

    # Качество воздуха + дым (по координатам Лимассола)
    la0, lo0 = CITIES["Limassol"]
    air = (get_air(la0, lo0) or {})
    lvl = air.get("lvl","н/д")
    aqi = air.get("aqi","н/д")
    p25 = air.get("pm25")
    p10 = air.get("pm10")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {aqi}) | PM₂.₅: {pm_color(p25)} | PM₁₀: {pm_color(p10)}")
    if (p25 is not None) or (p10 is not None):
        sm_emo, sm_txt = smoke_index(p25, p10)
        P.append(f"{sm_emo} дымовой индекс: {sm_txt}")

    # ☢️ Радиация
    rad = radiation.get_radiation(la0, lo0) or {}
    val = rad.get("value") or rad.get("dose")
    cpm = rad.get("cpm")
    if isinstance(val,(int,float)) or isinstance(cpm,(int,float)):
        # простая оценка уровня
        lvl_txt, dot = "в норме", "🟢"
        if isinstance(val,(int,float)) and val >= 0.4: lvl_txt, dot = "выше нормы", "🔵"
        elif isinstance(val,(int,float)) and val >= 0.2: lvl_txt, dot = "повышено", "🟡"
        if isinstance(cpm,(int,float)):
            P.append(f"📟 Радиация (Safecast): {int(round(cpm))} CPM ≈ {float(val):.3f} μSv/h — {dot} {lvl_txt}")
        else:
            P.append(f"📟 Радиация: {float(val):.3f} μSv/h — {dot} {lvl_txt}")
    P.append("———")

    # Геомагнитка + солнечный ветер
    kp, ks, age_h = fetch_kp_recent()
    if isinstance(kp, (int, float)):
        freshness = f", 🕓 {age_h}ч назад" if isinstance(age_h,int) else ""
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks}{freshness})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    sw = fetch_solar_wind()
    if sw:
        bz = sw.get("bz"); bt = sw.get("bt"); v = sw.get("v_kms"); n = sw.get("n")
        mood = sw.get("mood","")
        parts = []
        if isinstance(bz,(int,float)): parts.append(f"Bz {bz:.1f} nT")
        if isinstance(bt,(int,float)): parts.append(f"Bt {bt:.1f} nT")
        if isinstance(v,(int,float)):  parts.append(f"v {v:.0f} км/с")
        if isinstance(n,(int,float)):  parts.append(f"n {n:.1f} см⁻³")
        P.append("🌬️ Солнечный ветер: " + (", ".join(parts) if parts else "н/д") + (f" — {mood}" if mood else ""))
        P.append("ℹ️ По ветру сейчас " + (mood if mood else "нет данных") + "; Kp — глобальный индекс за 3 ч.")
    P.append("———")

    # Пыльца
    pol = get_pollen()
    if pol:
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pol['tree']} | Травы: {pol['grass']} | Сорняки: {pol['weed']} — риск {pol['risk']}")
        P.append("———")

    # Шуман
    schu_state = get_schumann_with_fallback()
    P.extend(schumann_lines(schu_state))
    P.append("———")

    # Астрособытия
    P.extend(build_astro_section_for_tomorrow())
    P.append("———")

    # «Вывод»
    # используем максимальные порывы из города-лидера (первой строки рейтинга)
    lead_city = RATING_ORDER[0]
    gust_for_concl = None
    try:
        wm_lead = get_weather(*CITIES[lead_city]) or {}
        _,_,_,_,gust_for_concl = pick_header_metrics(wm_lead)
    except Exception:
        pass

    P.append("📜 <b>Вывод</b>")
    P.extend(build_conclusion(kp, ks or "н/д", air, gust_for_concl, schu_state))
    P.append("———")

    # Рекомендации
    try:
        theme = (
            "плохая погода" if isinstance(gust_for_concl,(int,float)) and gust_for_concl >= 17 else
            ("магнитные бури" if isinstance(kp,(int,float)) and kp >= 5 else
             ("плохой воздух" if _is_air_bad(air)[0] else "здоровый день"))
        )
        _, tips = gpt_blurb(theme)
        tips = [t.strip() for t in tips if t.strip()][:3]
        if tips:
            P.extend(tips)
        else:
            P.append("— больше воды, меньше стресса, нормальный сон")
    except Exception:
        P.append("— больше воды, меньше стресса, нормальный сон")
    P.append("———")

    # Факт дня
    P.append(f"📚 {get_fact(TOMORROW)}")
    return "\n".join(P)

# ─────────────── отправка ───────────────
async def send_text(bot: Bot, chat_id: int, text: str) -> None:
    chunks: List[str] = []
    cur, cur_len = [], 0
    for line in text.split("\n"):
        if cur_len + len(line) + 1 > 3600 and cur:
            chunks.append("\n".join(cur)); cur, cur_len = [line], len(line)+1
        else:
            cur.append(line); cur_len += len(line)+1
    if cur: chunks.append("\n".join(cur))
    for i, part in enumerate(chunks):
        await bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML", disable_web_page_preview=True)
        if i < len(chunks)-1: await asyncio.sleep(0.4)

async def main() -> None:
    token = os.getenv("TELEGRAM_TOKEN", "").strip()
    chat_id_env = (os.getenv("CHANNEL_ID") or "").strip()
    try: chat_id = int(chat_id_env) if chat_id_env else 0
    except Exception: chat_id = 0

    # CLI: --chat-id <id>, --dry-run
    dry_run = "--dry-run" in sys.argv
    if "--chat-id" in sys.argv:
        try:
            chat_id = int(sys.argv[sys.argv.index("--chat-id")+1])
        except Exception:
            pass

    if not token or chat_id == 0:
        logging.error("Не заданы TELEGRAM_TOKEN и/или CHANNEL_ID")
        raise SystemExit(1)

    txt = build_msg()
    logging.info("Resolved CHANNEL_ID: %s | dry_run=%s", chat_id, dry_run)

    if dry_run:
        print(txt); return

    bot = Bot(token=token)
    try:
        await send_text(bot, chat_id, txt)
        logging.info("Отправлено ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)

if __name__ == "__main__":
    asyncio.run(main())
