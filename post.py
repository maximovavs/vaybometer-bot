#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter (Кипр), рендер «как в KLD».
Самодостаточная версия, без зависимости от post_common.py.
"""

from __future__ import annotations
import os, sys, asyncio, logging, json, math, re
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

import pendulum
from telegram import Bot, error as tg_err

from utils import (
    compass, get_fact, AIR_EMOJI, pm_color, kp_emoji,
    kmh_to_ms, smoke_index, _get
)
from weather import get_weather, fetch_tomorrow_temps, day_night_stats
from air import get_air, get_sst, get_kp, get_solar_wind
from pollen import get_pollen
from schumann import get_schumann
import radiation  # ☢️

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ───────── Константы региона ─────────
TZ = pendulum.timezone("Asia/Nicosia")
TODAY = pendulum.today(TZ)
TOMORROW = TODAY.add(days=1).date()

LIM_LAT, LIM_LON = 34.707, 33.022  # Limassol — якорная точка

CITIES: Dict[str, Tuple[float, float]] = {
    "Limassol":  (34.707, 33.022),
    "Nicosia":   (35.170, 33.360),
    "Pafos":     (34.776, 32.424),
    "Ayia Napa": (34.988, 34.012),
    "Troodos":   (34.916, 32.823),
    "Larnaca":   (34.916, 33.624),
}
COASTAL = {"Limassol", "Larnaca", "Pafos", "Ayia Napa"}

WMO_DESC = {
    0: "☀️ ясно", 1: "⛅ ч.обл", 2: "☁️ обл", 3: "🌥 пасм",
    45: "🌫 туман", 48: "🌫 изморозь", 51: "🌦 морось",
    61: "🌧 дождь", 71: "❄️ снег", 95: "⛈ гроза",
}
def code_desc(c: object) -> Optional[str]:
    try: return WMO_DESC.get(int(c))
    except Exception: return None

# ───────── helpers по часовкам ─────────
def _hourly_times(wm: Dict[str, Any]) -> List[pendulum.DateTime]:
    h = wm.get("hourly") or {}
    times = h.get("time") or h.get("time_local") or h.get("timestamp") or []
    out: List[pendulum.DateTime] = []
    for t in times:
        try: out.append(pendulum.parse(str(t)))
        except Exception: ...
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

# ───────── ветер/давление в шапку (как KLD) ─────────
def pick_tomorrow_header_metrics(wm: Dict[str, Any]) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    """
    Возвращает: wind_ms, wind_dir_deg, pressure_hpa, trend('↑'/'↓'/'→').
    • Берём ближайшее к 12:00 завтра (для тренда сравниваем с ~06:00).
    • На фоллбэке — среднее по всем завтрашним часам.
    """
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)
    idx_noon = _nearest_index(times, TOMORROW, 12)
    idx_morn = _nearest_index(times, TOMORROW, 6)

    spd = hourly.get("wind_speed_10m") or hourly.get("windspeed_10m") or hourly.get("windspeed") or hourly.get("wind_speed") or []
    dr  = hourly.get("wind_direction_10m") or hourly.get("winddirection_10m") or hourly.get("winddirection") or hourly.get("wind_direction") or []
    pr  = hourly.get("surface_pressure") or hourly.get("pressure") or []
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
    return wind_ms, wind_dir, press_val, trend

# ───────── шторм-флаги (завтра) ─────────
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

# ───────── Шуман: фоллбэк из локального JSON ─────────
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

def schumann_line(s: Dict[str, Any]) -> str:
    freq = s.get("freq"); amp = s.get("amp")
    trend_text = s.get("trend_text", "стабильно")
    cached = s.get("cached", False)
    status = s.get("status", "🟡 колебания")
    stale = " ⏳ нет свежих чисел" if cached else ""
    if not isinstance(freq, (int, float)) and not isinstance(amp, (int, float)):
        return f"{status}{stale} • тренд: {trend_text} • H7: — нет данных"
    return f"{status}{stale} • Шуман: {freq:.2f} Гц / {amp:.1f} pT • тренд: {trend_text} • H7: — н/д"

# ───────── Астрособытия из lunar_calendar.json ─────────
def _load_calendar(path: str = "lunar_calendar.json") -> dict:
    try: data = json.loads(Path(path).read_text("utf-8"))
    except Exception: return {}
    if isinstance(data, dict) and isinstance(data.get("days"), dict):
        return data["days"]
    return data if isinstance(data, dict) else {}

_ZODIAC = {"Овен":"♈","Телец":"♉","Близнецы":"♊","Рак":"♋","Лев":"♌","Дева":"♍","Весы":"♎","Скорпион":"♏","Стрелец":"♐","Козерог":"♑","Водолей":"♒","Рыбы":"♓"}
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

def build_astro_section(date_local: pendulum.DateTime, tz_local: str) -> str:
    cal = _load_calendar("lunar_calendar.json")
    rec = cal.get(date_local.format("YYYY-MM-DD"), {}) if isinstance(cal, dict) else {}
    phase_raw = (rec.get("phase_name") or rec.get("phase") or "").strip()
    phase_name = re.sub(r"^[^\wА-Яа-яЁё]+", "", phase_raw).split(",")[0].strip() or "Луна"
    percent = rec.get("percent") or rec.get("illumination") or rec.get("illum") or 0
    try: percent = int(round(float(percent)))
    except Exception: percent = 0
    sign = rec.get("sign") or rec.get("zodiac") or ""
    base = f"{phase_name} ({percent}%)" if percent else phase_name
    if sign: base += f" в {_zsym(sign)}"
    lines = [f"🌌 <b>Астрособытия</b>", f"🌙 {base}."]
    voc = _voc_interval(rec, tz_local=tz_local)
    if voc:
        t1, t2 = voc
        lines.append(f"⏳ Период без курса: {t1.format('HH:mm')}–{t2.format('HH:mm')}.")
    # LLM 1–2 мягкие подсказки
    if os.getenv("DISABLE_LLM_DAILY","0").lower() not in ("1","true","yes","on"):
        try:
            from gpt import gpt_blurb
            _, tips = gpt_blurb("астрология")
            tips = [t.strip() for t in tips if t.strip()][:2]
            lines += tips
        except Exception:
            pass
    return "\n".join(lines)

# ───────── Air → оценка риска ─────────
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

def build_conclusion(kp: Optional[float], kp_status: str,
                     air: Dict[str, Any],
                     storm: Dict[str, Any],
                     schu: Dict[str, Any]) -> List[str]:
    lines: List[str] = []
    air_bad, air_label, air_reason = _is_air_bad(air)
    gust_ms = storm.get("max_gust_ms")
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

# ───────── Рейтинг городов (устойчивый) ─────────
def build_cities_block() -> List[str]:
    """KLD-стиль: дн/ночь, краткое описание, 🌊. С фоллбэком на daily.*"""
    tz_name = TZ.name
    temps: Dict[str, Tuple[float, float, int, Optional[float]]] = {}
    for city, (la, lo) in CITIES.items():
        tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
        if tmax is None or tmin is None:
            # фоллбэк через daily массивы
            wm = get_weather(la, lo) or {}
            dl = wm.get("daily", {}) or {}
            try:
                tmxs = dl.get("temperature_2m_max") or dl.get("tmax") or []
                tmns = dl.get("temperature_2m_min") or dl.get("tmin") or []
                if isinstance(tmxs, list) and len(tmxs) > 1 and tmax is None:
                    tmax = float(tmxs[1])
                if isinstance(tmns, list) and len(tmns) > 1 and tmin is None:
                    tmin = float(tmns[1])
            except Exception:
                pass
        if tmax is None or tmin is None:
            continue  # совсем нет данных — пропускаем
        wc = (get_weather(la, lo) or {}).get("daily", {}).get("weathercode", [])
        wc = wc[1] if isinstance(wc, list) and len(wc) > 1 else 0
        sst = get_sst(la, lo) if city in COASTAL else None
        temps[city] = (tmax, tmin, wc, sst)

    lines = ["🎖️ <b>Города (д./н. °C, погода, 🌊)</b>"]
    if not temps:
        lines.append("— н/д —"); return lines
    medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣"]
    for i, (city, (d, n, wc, sst)) in enumerate(
        sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:6]
    ):
        desc = code_desc(wc)
        line = f"{medals[i]} {city}: {d:.0f}/{n:.0f} °C"
        if desc: line += f" • {desc}"
        if sst is not None: line += f" • 🌊 {sst:.1f}"
        lines.append(line)
    return lines

# ───────── Шапка «как в KLD» (Limassol) ─────────
def build_header_line() -> str:
    stats = day_night_stats(LIM_LAT, LIM_LON, tz=TZ.name)
    wm = get_weather(LIM_LAT, LIM_LON) or {}
    storm = storm_flags_for_tomorrow(wm)
    wcarr = (wm.get("daily", {}) or {}).get("weathercode", [])
    wc = wcarr[1] if isinstance(wcarr, list) and len(wcarr) > 1 else None

    rh_min, rh_max = stats.get("rh_min"), stats.get("rh_max")
    t_day_max, t_night_min = stats.get("t_day_max"), stats.get("t_night_min")
    wind_ms, wind_dir, press_hpa, p_trend = pick_tomorrow_header_metrics(wm)
    wind_part = (
        f"💨 {wind_ms:.1f} м/с ({compass(wind_dir)})" if isinstance(wind_ms, (int, float)) and wind_dir is not None
        else (f"💨 {wind_ms:.1f} м/с" if isinstance(wind_ms, (int, float)) else "💨 н/д")
    )
    gust = storm.get("max_gust_ms")
    if isinstance(gust, (int, float)):
        wind_part += f" порывы до {gust:.0f}"

    parts = [
        f"🏙️ Limassol: дн/ночь {t_day_max:.0f}/{t_night_min:.0f} °C" if (t_day_max is not None and t_night_min is not None) else "🏙️ Limassol: дн/ночь н/д",
        (code_desc(wc) or None),
        wind_part,
        (f"💧 RH {rh_min:.0f}–{rh_max:.0f}%" if rh_min is not None and rh_max is not None else None),
        (f"🔹 {press_hpa} гПа {p_trend}" if isinstance(press_hpa, int) else None),
    ]
    return " • ".join([x for x in parts if x])

# ───────── Радиация / Safecast ─────────
def safecast_block_lines(lat: float, lon: float) -> List[str]:
    out: List[str] = []
    try:
        rd = radiation.get_radiation(lat, lon) or {}
        val = rd.get("value") or rd.get("dose")
        cpm = rd.get("cpm")
        if isinstance(val, (int, float)) or isinstance(cpm, (int, float)):
            lvl_txt, dot = "в норме", "🟢"
            if isinstance(val, (int, float)) and val >= 0.4: lvl_txt, dot = "выше нормы", "🔵"
            elif isinstance(val, (int, float)) and val >= 0.2: lvl_txt, dot = "повышено", "🟡"
            if isinstance(cpm, (int, float)):
                out.append(f"📟 Радиация (Safecast): {int(round(cpm))} CPM ≈ {float(val):.3f} μSv/h — {dot} {lvl_txt}")
            else:
                out.append(f"📟 Радиация: {float(val):.3f} μSv/h — {dot} {lvl_txt}")
    except Exception:
        pass
    return out

# ───────── Сообщение целиком ─────────
def build_message() -> str:
    P: List[str] = []
    P.append(f"<b>🌅 Кипр: погода на завтра ({TOMORROW.strftime('%d.%m.%Y')})</b>")
    P.append("———")

    # Шапка по Лимассолу (как в KLD)
    P.append(build_header_line())
    P.append("———")

    # Города (устойчивый рейтинг)
    P.extend(build_cities_block())
    P.append("———")

    # Air (как в KLD) + Safecast + дымовой индекс
    P.append("🏭 <b>Качество воздуха</b>")
    air = get_air(LIM_LAT, LIM_LON) or {}
    lvl = air.get("lvl", "н/д")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    P.extend(safecast_block_lines(LIM_LAT, LIM_LON))
    # Дымовой индекс — только если не «низкий/н/д»
    em_sm, lbl_sm = smoke_index(air.get("pm25"), air.get("pm10"))
    if lbl_sm and str(lbl_sm).lower() not in ("низкое", "низкий", "нет", "н/д"):
        P.append(f"🔥 Задымление: {em_sm} {lbl_sm}")
    P.append("———")

    # Пыльца
    pol = get_pollen()
    if pol:
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {pol['tree']} | Травы: {pol['grass']} | Сорняки: {pol['weed']} — риск {pol['risk']}")
        P.append("———")

    # Геомагнитка (со свежестью)
    kp_tuple = get_kp() or (None, "н/д", None, "n/d")
    try:
        kp, ks, kp_ts, _ = kp_tuple
    except Exception:
        kp = kp_tuple[0] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) else None
        ks, kp_ts = "н/д", None

    age_txt = ""
    if isinstance(kp_ts, int) and kp_ts > 0:
        try:
            age_min = int((pendulum.now("UTC").int_timestamp - kp_ts) / 60)
            age_txt = f", 🕓 {age_min // 60}ч назад" if age_min > 180 else (f", {age_min} мин назад" if age_min >= 0 else "")
        except Exception:
            pass

    if isinstance(kp, (int, float)):
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks}{age_txt})")
    else:
        P.append("🧲 Геомагнитка: н/д")

    # Солнечный ветер
    sw = get_solar_wind() or {}
    bz = sw.get("bz"); bt = sw.get("bt"); v = sw.get("speed_kms"); n = sw.get("density")
    wind_status = sw.get("status", "н/д")
    parts = []
    if isinstance(bz, (int, float)): parts.append(f"Bz {bz:.1f} nT")
    if isinstance(bt, (int, float)): parts.append(f"Bt {bt:.1f} nT")
    if isinstance(v,  (int, float)): parts.append(f"v {v:.0f} км/с")
    if isinstance(n,  (int, float)): parts.append(f"n {n:.1f} см⁻³")
    if parts:
        P.append("🌬️ Солнечный ветер: " + ", ".join(parts) + f" — {wind_status}")
        P.append("ℹ️ По ветру сейчас " + (wind_status if isinstance(wind_status,str) else "н/д") + "; Kp — глобальный индекс за 3 ч.")
    P.append("———")

    # Шуман
    schu_state = get_schumann_with_fallback()
    P.append(schumann_line(schu_state))
    P.append("———")

    # Астрособытия
    P.append(build_astro_section(pendulum.today(TZ).add(days=1), TZ.name))
    P.append("———")

    # «Вывод»
    wm_anchor = get_weather(LIM_LAT, LIM_LON) or {}
    storm = storm_flags_for_tomorrow(wm_anchor)
    P.append("📜 <b>Вывод</b>")
    P.extend(build_conclusion(kp, ks or "н/д", air, storm, schu_state))
    P.append("———")

    # Рекомендации
    from gpt import gpt_blurb
    try:
        theme = (
            "плохая погода" if storm.get("warning") else
            ("магнитные бури" if isinstance(kp, (int, float)) and kp >= 5 else
             ("плохой воздух" if _is_air_bad(air)[0] else "здоровый день"))
        )
        _, tips = gpt_blurb(theme)
        tips = [t.strip() for t in tips if t.strip()][:3]
        if tips: P.extend(tips)
        else:    P.append("— больше воды, меньше стресса, нормальный сон")
    except Exception:
        P.append("— больше воды, меньше стресса, нормальный сон")
    P.append("———")

    # Факт дня
    P.append(f"📚 {get_fact(TOMORROW, 'Кипр')}")
    return "\n".join(P)

# ───────── Отправка (дробим по 3600) ─────────
async def send_text(bot: Bot, chat_id: int, text: str) -> None:
    chunks: List[str] = []
    cur, cur_len = [], 0
    for line in text.split("\n"):
        if cur_len + len(line) + 1 > 3600 and cur:
            chunks.append("\n".join(cur)); cur, cur_len = [line], len(line) + 1
        else:
            cur.append(line); cur_len += len(line) + 1
    if cur: chunks.append("\n".join(cur))
    for i, part in enumerate(chunks):
        await bot.send_message(chat_id=chat_id, text=part, parse_mode="HTML", disable_web_page_preview=True)
        if i < len(chunks) - 1: await asyncio.sleep(0.4)

async def main() -> None:
    token = (os.getenv("TELEGRAM_TOKEN") or "").strip()
    chat_id_env = (os.getenv("CHANNEL_ID") or "").strip()
    try: chat_id = int(chat_id_env) if chat_id_env else 0
    except Exception: chat_id = 0
    if not token or chat_id == 0:
        logging.error("Не заданы TELEGRAM_TOKEN и/или CHANNEL_ID")
        raise SystemExit(1)
    txt = build_message()
    logging.info("Preview: %s", txt[:220].replace("\n", " | "))
    await send_text(Bot(token=token), chat_id, txt)

if __name__ == "__main__":
    asyncio.run(main())
