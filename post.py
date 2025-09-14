#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота (Кипр).

• Прогноз на завтра (день/ночь, ветер, порывы, RH, давление)
• Рейтинг городов (с SST для прибрежных)
• Качество воздуха + пыльца + ☢️ Радиация
• Kp-индекс (+ «свежесть») + Солнечный ветер
• Резонанс Шумана (фоллбэк из JSON)
• Астрособытия (из lunar_calendar.json; знаки → ♈-♓ + VoC + LLM-буллеты)
• Умный «Вывод» + рекомендации
• Факт дня
"""

from __future__ import annotations
import os, json, logging, asyncio, re, math
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

import pendulum
from telegram import Bot, error as tg_err

from utils   import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji, kmh_to_ms, smoke_index, _get
from weather import get_weather, fetch_tomorrow_temps, day_night_stats
from air     import get_air, get_sst
from pollen  import get_pollen
from schumann import get_schumann
from gpt     import gpt_blurb  # (LLM-советы)
import radiation  # ☢️

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────── базовые константы ──────────────────
TZ        = pendulum.timezone("Asia/Nicosia")
TODAY     = pendulum.today(TZ)
TOMORROW  = TODAY.add(days=1).date()

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))
if not TOKEN or CHAT_ID == 0:
    logging.error("Не заданы TELEGRAM_TOKEN и/или CHANNEL_ID")
    raise SystemExit(1)

# Координаты (Кипр)
CITIES: Dict[str, Tuple[float, float]] = {
    "Nicosia":   (35.170, 33.360),
    "Larnaca":   (34.916, 33.624),
    "Limassol":  (34.707, 33.022),
    "Pafos":     (34.776, 32.424),
    "Troodos":   (34.916, 32.823),
    "Ayia Napa": (34.988, 34.012),
}
COASTAL_CITIES = {"Larnaca", "Limassol", "Pafos", "Ayia Napa"}

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
        try:
            out.append(pendulum.parse(str(t)))
        except Exception:
            pass
    return out

def _nearest_index(times: List[pendulum.DateTime], date_obj: pendulum.Date, prefer_hour: int) -> Optional[int]:
    if not times:
        return None
    target = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, prefer_hour, 0, tz=TZ)
    best_i, best_diff = None, None
    for i, dt in enumerate(times:
    ):
        try:
            dt_local = dt.in_tz(TZ)
        except Exception:
            dt_local = dt
        if dt_local.date() != date_obj:
            continue
        diff = abs((dt_local - target).total_seconds())
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
    if not deg_list:
        return None
    x = sum(math.cos(math.radians(d)) for d in deg_list)
    y = sum(math.sin(math.radians(d)) for d in deg_list)
    if x == 0 and y == 0:
        return None
    ang = math.degrees(math.atan2(y, x))
    return (ang + 360.0) % 360.0

# ────────── ветер/давление в шапку + порывы ──────────
def pick_header_metrics(wm: Dict[str, Any]) -> Tuple[Optional[float], Optional[int], Optional[int], str, Optional[float]]:
    """
    Возвращает: wind_ms, wind_dir_deg, pressure_hpa, pressure_trend(↑/↓/→), gust_max_ms
    • Берём ближайшее к 12:00 завтра (для тренда сравниваем с ~06:00).
    • gust_max_ms — максимум за весь завтрашний день.
    """
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)
    idx_noon = _nearest_index(times, TOMORROW, 12)
    idx_morn = _nearest_index(times, TOMORROW, 6)

    # массивы синонимов
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
        if p_noon is not None:
            press_val = int(round(p_noon))
        if (p_noon is not None) and (p_morn is not None):
            diff = p_noon - p_morn
            trend = "↑" if diff >= 0.3 else "↓" if diff <= -0.3 else "→"

    # fallback: среднее по дню
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

    # gusts за день
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

# ────────── шторм-флаги по завтрашним часам ──────────
def storm_flags_for_tomorrow(wm: Dict[str, Any]) -> Dict[str, Any]:
    hourly = wm.get("hourly") or {}
    idxs = _tomorrow_indices(wm)
    if not idxs:
        return {"warning": False}

    def _arr(*names, default=None):
        for n in names:
            v = hourly.get(n)
            if isinstance(v, list):
                return v
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

# ────────── NOAA: Kp с «свежестью» ──────────
def fetch_kp_recent() -> Tuple[Optional[float], Optional[str], Optional[int]]:
    try:
        j = _get("https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json", timeout=20).json()
        if isinstance(j, list) and len(j) >= 2:
            last = j[-1]
            # ["time_tag","kp_index","a_index","station_count"]
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
    # Пытаемся взять summary; если не вышло — None
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
            # оценка состояния
            bzv = vals.get("bz"); vv = vals.get("v_kms"); dn = vals.get("n")
            danger = (bzv is not None and bzv <= -10) or (vv is not None and vv >= 600) or (dn is not None and dn >= 20)
            warn   = (bzv is not None and bzv <= -6) or (vv is not None and vv >= 500) or (dn is not None and dn >= 10)
            mood = "буря" if danger else ("возмущённо" if warn else "спокойно")
            vals["mood"] = mood
            return vals
    except Exception:
        pass
    return None

# ────────── Шуман: фоллбэк / рендер 2 строки ──────────
def _trend_text(sym: str) -> str:
    return {"↑": "растёт", "↓": "снижается", "→": "стабильно"}.get(sym, "стабильно")

def _trend_from_series(vals: List[float], delta: float = 0.1) -> str:
    tail = vals[-24:] if len(vals) > 24 else vals
    if len(tail) < 2:
        return "→"
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
            freqs = [float(x["freq"]) for x in arr if isinstance(x.get("freq"), (int, float))]
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
    """Возвращает 2 строки: статус+числа и мягкую интерпретацию."""
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

# ────────── Лунный календарь (как в KLD: из lunar_calendar.json) ──────────
def _load_calendar(path: str = "lunar_calendar.json") -> dict:
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("days"), dict):
        return data["days"]
    return data if isinstance(data, dict) else {}

_ZODIAC = {
    "Овен": "♈","Телец": "♉","Близнецы": "♊","Рак": "♋","Лев": "♌",
    "Дева": "♍","Весы": "♎","Скорпион": "♏","Стрелец": "♐",
    "Козерог": "♑","Водолей": "♒","Рыбы": "♓",
}
def _zsym(s: str) -> str:
    for k,v in _ZODIAC.items():
        s = s.replace(k, v)
    return s

def _parse_voc_dt(s: str, tz: pendulum.Timezone):
    if not s: return None
    try:
        return pendulum.parse(s).in_tz(tz)
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
    if bullets:
        lines += [_zsym(str(x)) for x in bullets[:3]]
    else:
        base = f"🌙 Фаза: {phase_name}"
        prm  = f" ({percent}%)" if percent else ""
        lines += [base + prm, (_zsym(f"Знак: {sign}") if sign else "— знак Луны н/д")]

    voc = _voc_interval(rec, tz_local=tz.name)
    if voc:
        t1, t2 = voc
        lines.append(f"⚫️ VoC: {t1.format('HH:mm')}–{t2.format('HH:mm')}")

    # LLM-добавка (не более 2 строк)
    if os.getenv("DISABLE_LLM_DAILY","0").lower() not in ("1","true","yes","on"):
        try:
            _, tips = gpt_blurb("астрология")
            tips = [t.strip() for t in tips if t.strip()][:2]
            if tips:
                lines += tips
        except Exception:
            pass

    return lines

# ────────── «умный вывод» ──────────
def build_conclusion(kp: Optional[float], kp_status: Optional[str],
                     air: Dict[str, Any],
                     storm: Dict[str, Any],
                     schu: Dict[str, Any]) -> List[str]:
    lines: List[str] = []

    air_bad, air_label, air_reason = _is_air_bad(air)
    storm_main = isinstance(storm.get("max_gust_ms"), (int, float)) and storm["max_gust_ms"] >= 17
    kp_main    = isinstance(kp, (int, float)) and kp >= 5
    schu_main  = (schu or {}).get("status","").startswith("🔴")

    storm_text = f"штормовая погода: порывы до {storm['max_gust_ms']:.0f} м/с" if storm_main else None
    air_text   = f"качество воздуха: {air_label} ({air_reason})" if air_bad else None
    kp_text    = f"магнитная активность: Kp≈{kp:.1f} ({kp_status})" if kp_main else None
    schu_text  = "сильные отклонения волн Шумана" if schu_main else None

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
    primary = lines[0]
    rest = [t for t in secondary if t not in primary]
    if rest:
        lines.append("Также обратите внимание: " + "; ".join(rest[:2]) + ".")
    return lines

# ───────────────────────── build_msg ─────────────────────────
def build_msg() -> str:
    P: List[str] = []

    # Заголовок
    P.append(f"<b>🌅 Добрый вечер! Погода на завтра ({TOMORROW.strftime('%d.%m.%Y')})</b>")

    # Ср. SST
    sst_vals = [t for c in COASTAL_CITIES if (t:=get_sst(*CITIES[c])) is not None]
    P.append(f"🌊 Ср. темп. моря: {sum(sst_vals)/len(sst_vals):.1f} °C" if sst_vals
             else "🌊 Ср. темп. моря: н/д")
    P.append("———")

    # Город по умолчанию
    primary = os.getenv("PRIMARY_CITY", "Limassol")
    lat, lon = CITIES.get(primary, CITIES["Limassol"])

    # Прогноз/статистика на завтра
    wm  = get_weather(lat, lon) or {}
    st  = day_night_stats(lat, lon, tz=TZ.name) or {}
    t_day_max   = st.get("t_day_max")
    t_night_min = st.get("t_night_min")
    rh_min, rh_max = st.get("rh_min"), st.get("rh_max")
    wcarr = (wm.get("daily", {}) or {}).get("weathercode", [])
    wc = wcarr[1] if isinstance(wcarr, list) and len(wcarr) > 1 else None

    wind_ms, wind_dir, press_hpa, p_trend, gust_max = pick_header_metrics(wm)
    storm = storm_flags_for_tomorrow(wm)

    wind_part = (
        f"💨 {wind_ms:.1f} м/с ({compass(wind_dir)})" if isinstance(wind_ms, (int, float)) and wind_dir is not None
        else (f"💨 {wind_ms:.1f} м/с" if isinstance(wind_ms, (int, float)) else "💨 н/д")
    )
    if isinstance(storm.get("max_gust_ms"), (int, float)):
        wind_part += f" (порывы до {storm['max_gust_ms']:.0f})"

    parts = [
        f"🏙️ {primary}: {t_day_max:.0f}/{t_night_min:.0f} °C" if (t_day_max is not None and t_night_min is not None) else f"🏙️ {primary}: н/д",
        (code_desc(wc) or None),
        wind_part,
        (f"💧 RH {rh_min:.0f}–{rh_max:.0f}%" if rh_min is not None and rh_max is not None else None),
        (f"🔹 {press_hpa} гПа {('↑' if p_trend=='↑' else '↓' if p_trend=='↓' else '→')}" if isinstance(press_hpa, int) else None),
    ]
    P.append(" • ".join([p for p in parts if p]))
    P.append("———")

    if storm.get("warning"):
        P.append(storm["warning_text"])
        P.append("———")

    # Рейтинг городов
    temps: Dict[str,Tuple[float,float,int,Optional[float]]] = {}
    for city,(la,lo) in CITIES.items():
        d,n = fetch_tomorrow_temps(la,lo, tz=TZ.name)
        if d is None:
            continue
        wcx  = (get_weather(la,lo) or {}).get("daily",{}).get("weathercode",[])
        wcx  = wcx[1] if isinstance(wcx,list) and len(wcx)>1 else 0
        sst = get_sst(la,lo) if city in COASTAL_CITIES else None
        temps[city] = (d, n if n is not None else d, wcx, sst)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (д./н. °C, погода, 🌊)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣"]
        for i,(city,(d,n,wc,sst)) in enumerate(sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:6]):
            line = f"{medals[i]} {city}: {d:.1f}/{n:.1f}, {(code_desc(wc) or '—')}"
            if sst is not None: line += f", 🌊 {sst:.1f}"
            P.append(line)
        P.append("———")

    # Воздух + пыльца
    air = get_air(lat, lon) or {}
    lvl = air.get("lvl","н/д")
    aqi = air.get("aqi","н/д")
    p25 = air.get("pm25"); p10 = air.get("pm10")
    sm_emo, sm_txt = smoke_index(p25, p10)
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {aqi}) | PM₂.₅: {pm_color(p25)} | PM₁₀: {pm_color(p10)}")
    P.append(f"{sm_emo} дымовой индекс: {sm_txt}")
    if (p:=get_pollen(lat=lat, lon=lon)):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")
    P.append("———")

    # ☢️ Радиация
    rad = radiation.get_radiation(lat, lon) or {}
    dose = rad.get("value") or rad.get("dose")
    if isinstance(dose, (int, float)):
        P.append(f"☢️ Радиация: {float(dose):.3f} µSv/h")
        P.append("———")

    # Геомагнитка + солнечный ветер
    kp, kp_status, age_h = fetch_kp_recent()
    if isinstance(kp, (int, float)):
        age_note = "" if (age_h is None or age_h <= 0) else f" • {age_h} ч назад"
        P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({kp_status}){age_note}")
    else:
        P.append("🧲 Геомагнитка: н/д")
    sw = fetch_solar_wind()
    if sw:
        # компактно
        bz = sw.get("bz"); bt = sw.get("bt"); v = sw.get("v_kms"); n = sw.get("n")
        def fmt(x, suf=""): return (f"{x:.0f}{suf}" if isinstance(x,(int,float)) else "н/д")
        P.append(f"☀️ SW: Bz {fmt(bz,' nT')} • Bt {fmt(bt,' nT')} • V {fmt(v,' км/с')} • n {fmt(n,' см⁻³')} — {sw.get('mood','-')}")
    P.append("———")

    # Шуман
    schu_state = get_schumann_with_fallback()
    P.extend(schumann_lines(schu_state))
    P.append("———")

    # Астрособытия (из календаря)
    P.extend(build_astro_section_for_tomorrow())
    P.append("———")

    # «Умный» вывод
    P.append("📜 <b>Вывод</b>")
    P.extend(build_conclusion(kp, kp_status, air, storm, schu_state))
    P.append("———")

    # Рекомендации (LLM)
    try:
        theme = (
            "плохая погода" if storm.get("warning") else
            ("магнитные бури" if isinstance(kp,(int,float)) and kp >= 5 else
             ("плохой воздух" if _is_air_bad(air)[0] else "здоровый день"))
        )
        _, tips = gpt_blurb(theme)
        for tip in [t.strip() for t in tips[:3]]:
            if tip:
                P.append(tip)
    except Exception:
        P.append("— больше воды, меньше стресса, нормальный сон")
    P.append("———")

    # Факт дня
    P.append(f"📚 {get_fact(TOMORROW)}")
    return "\n".join(P)

# ─────────────── отправка ───────────────
async def send_main_post(bot: Bot) -> None:
    txt = build_msg()
    logging.info("Preview: %s", txt[:200].replace('\n',' | '))
    try:
        await bot.send_message(chat_id=CHAT_ID, text=txt,
                               parse_mode="HTML", disable_web_page_preview=True)
        logging.info("Отправлено ✓")
    except tg_err.TelegramError as e:
        logging.error("Telegram error: %s", e)

async def main() -> None:
    await send_main_post(Bot(token=TOKEN))

if __name__ == "__main__":
    asyncio.run(main())
