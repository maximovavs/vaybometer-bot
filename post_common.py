#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post_common.py — VayboMeter (универсальный).

• Морские/континентальные города: детальные строки (дн/ночь, погода, ветер±порывы, RH min–max,
  давление±тренд; для прибрежных — ещё и 🌊 SST)
• Air + Safecast + пыльца
• Геомагнитка + солнечный ветер
• Шуман
• Астрособытия, вывод, рекомендации, факт дня
"""

from __future__ import annotations
import os
import re
import json
import asyncio
import logging
import math
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional, Union

import pendulum
from telegram import Bot, constants

from utils        import compass, get_fact, AIR_EMOJI, pm_color, kp_emoji, kmh_to_ms, smoke_index
from weather      import get_weather, fetch_tomorrow_temps, day_night_stats
from air          import get_air, get_sst, get_kp, get_solar_wind
from pollen       import get_pollen
from radiation    import get_radiation
from gpt          import gpt_blurb, gpt_complete  # gpt_complete — для микро-LLM в «Астрособытиях»

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# ────────────────────────── константы ──────────────────────────
CY_LAT, CY_LON = 34.707, 33.022  # базовые координаты региона (для общих источников)

# коэффициент перевода CPM -> μSv/h (можно переопределить ENV CPM_TO_USVH)
CPM_TO_USVH = float(os.getenv("CPM_TO_USVH", "0.000571"))
PRIMARY_CITY_NAME = os.getenv("PRIMARY_CITY", "Limassol")

# Кэш для микро-LLM «Астрособытий»
CACHE_DIR = Path(".cache")
CACHE_DIR.mkdir(exist_ok=True, parents=True)
USE_DAILY_LLM = os.getenv("DISABLE_LLM_DAILY", "").strip().lower() not in ("1", "true", "yes", "on")

# ──────────── утилита: принять tz как объект или как строку ────────────
def _as_tz(tz: Union[pendulum.Timezone, str]) -> pendulum.Timezone:
    if isinstance(tz, str):
        return pendulum.timezone(tz)
    return tz

# Мэппинг WMO-кодов в короткие текст+эмодзи
WMO_DESC = {
    0: "☀️ ясно", 1: "⛅ ч.обл", 2: "☁️ обл", 3: "🌥 пасм",
    45: "🌫 туман", 48: "🌫 изморозь", 51: "🌦 морось",
    61: "🌧 дождь", 71: "❄️ снег", 95: "⛈ гроза",
}
def code_desc(c: Any) -> Optional[str]:
    try:
        i = int(c)
    except Exception:
        return None
    return WMO_DESC.get(i)

def _iter_city_pairs(cities) -> list[tuple[str, tuple[float, float]]]:
    """Принимает dict {'Name': (lat,lon)} или iterable из пар и возвращает список пар."""
    if isinstance(cities, dict):
        return list(cities.items())
    try:
        return list(cities)
    except Exception:
        return []

# ───────────── Шуман: чтение JSON-истории ─────────────
def _read_schumann_history() -> List[Dict[str, Any]]:
    candidates: List[Path] = []
    env_path = os.getenv("SCHU_FILE")
    if env_path:
        candidates.append(Path(env_path))
    here = Path(__file__).parent
    candidates += [here / "schumann_hourly.json", here / "data" / "schumann_hourly.json", here.parent / "schumann_hourly.json"]

    for p in candidates:
        try:
            if p.exists():
                txt = p.read_text("utf-8").strip()
                data = json.loads(txt) if txt else []
                if isinstance(data, list):
                    return data
        except Exception as e:
            logging.warning("Schumann history read error from %s: %s", p, e)
    return []

def _schumann_trend(values: List[float], delta: float = 0.1) -> str:
    if not values:
        return "→"
    tail = values[-24:] if len(values) > 24 else values
    if len(tail) < 2:
        return "→"
    avg_prev = sum(tail[:-1]) / (len(tail) - 1)
    d = tail[-1] - avg_prev
    return "↑" if d >= delta else "↓" if d <= -delta else "→"

# ───────────── Шуман: вспомогалки ─────────────
def _freq_status(freq: Optional[float]) -> tuple[str, str]:
    """
    (label, code):
      🟢 в норме — 7.7..8.1
      🟡 колебания — 7.4..8.4, но вне зелёного коридора
      🔴 сильное отклонение — <7.4 или >8.4
    """
    if not isinstance(freq, (int, float)):
        return "🟡 колебания", "yellow"
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
    if not isinstance(ts, (int, float)):
        return False
    try:
        now_ts = pendulum.now("UTC").int_timestamp
        return (now_ts - int(ts)) > max_age_sec
    except Exception:
        return False

def get_schumann_with_fallback() -> Dict[str, Any]:
    """Пытаемся взять состояние из schumann.get_schumann(), иначе читаем JSON. Возвращаем унифицированный словарь."""
    try:
        import schumann  # локальный модуль сбора
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

    # фоллбэк: локальная история
    arr = _read_schumann_history()
    if not arr:
        return {"freq": None, "amp": None, "trend": "→",
                "trend_text": "стабильно", "status": "🟡 колебания", "status_code": "yellow",
                "h7_text": _h7_text(None, None), "h7_amp": None, "h7_spike": None,
                "interpretation": _gentle_interpretation("yellow"), "cached": True}

    amps: List[float] = []
    last: Optional[Dict[str, Any]] = None
    for rec in arr:
        if not isinstance(rec, dict):
            continue
        if isinstance(rec.get("amp"), (int, float)):
            amps.append(float(rec["amp"]))
        last = rec

    trend = _schumann_trend(amps)
    freq = (last.get("freq") if last else None)
    amp = (last.get("amp") if last else None)
    h7_amp = (last.get("h7_amp") if last else None)
    h7_spike = (last.get("h7_spike") if last else None)
    src = ((last or {}).get("src") or "").lower()
    cached = (src == "cache") or _is_stale((last or {}).get("ts"))

    status, code = _freq_status(freq)
    return {
        "freq": freq if isinstance(freq, (int, float)) else None,
        "amp": amp if isinstance(amp, (int, float)) else None,
        "trend": trend,
        "trend_text": _trend_text(trend),
        "status": status,
        "status_code": code,
        "h7_text": _h7_text(h7_amp, h7_spike),
        "h7_amp": h7_amp if isinstance(h7_amp, (int, float)) else None,
        "h7_spike": h7_spike if isinstance(h7_spike, bool) else None,
        "interpretation": _gentle_interpretation(code),
        "cached": cached,
    }

def _gentle_interpretation(code: str) -> str:
    if code == "green":
        return "Волны Шумана близки к норме — организм реагирует как на обычный день."
    if code == "yellow":
        return "Заметны колебания — возможна лёгкая чувствительность к погоде и настроению."
    return "Сильные отклонения — прислушивайтесь к самочувствию и снижайте перегрузки."

def schumann_line(s: Dict[str, Any]) -> str:
    """
    2 строки:
      1) (<статус>) [⏳ нет свежих чисел] • тренд: … • H7: …
      2) ℹ️ мягкая интерпретация
    """
    freq = s.get("freq")
    amp  = s.get("amp")
    trend_text = s.get("trend_text") or _trend_text(s.get("trend", "→"))
    status_lbl = s.get("status") or _freq_status(freq)[0]
    h7line = s.get("h7_text") or _h7_text(s.get("h7_amp"), s.get("h7_spike"))
    interp = s.get("interpretation") or _gentle_interpretation(s.get("status_code") or _freq_status(freq)[1])
    stale = " ⏳ нет свежих чисел" if s.get("cached") else ""

    # если чисел нет, печатаем без «Шуман: н/д/н/д»
    if not isinstance(freq, (int, float)) and not isinstance(amp, (int, float)):
        main = f"{status_lbl}{stale} • тренд: {trend_text} • {h7line}"
        return main + "\n" + interp

    fstr = f"{freq:.2f}" if isinstance(freq, (int, float)) else "н/д"
    astr = f"{amp:.2f} pT" if isinstance(amp, (int, float)) else "н/д"
    main = f"{status_lbl}{stale} • Шуман: {fstr} Гц / {astr} • тренд: {trend_text} • {h7line}"
    return main + "\n" + interp

# ───────────── Safecast / чтение файла ─────────────
def _read_json(path: Path) -> Optional[Dict[str, Any]]:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text("utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as e:
        logging.warning("Safecast read error from %s: %s", path, e)
        return None

def load_safecast() -> Optional[Dict[str, Any]]:
    """
    Ищем JSON:
      1) env SAFECAST_FILE
      2) ./data/safecast_cy.json
    Возвращаем None, если нет/устарело.
    """
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
    if not sc:
        return None

    # свежесть не старше 24 часов
    ts = sc.get("ts")
    if not isinstance(ts, (int, float)):
        return None
    now_ts = pendulum.now("UTC").int_timestamp
    if now_ts - int(ts) > 24 * 3600:
        return None
    return sc

# ───────────── риск/шкалы для радиации ─────────────
def safecast_usvh_risk(x: float) -> tuple[str, str]:
    if x <= 0.15:
        return "🟢", "низкий"
    if x <= 0.30:
        return "🟡", "умеренный"
    return "🔵", "выше нормы"

def official_usvh_risk(x: float) -> tuple[str, str]:
    if x <= 0.15:
        return "🟢", "низкий"
    if x <= 0.30:
        return "🟡", "повышенный"
    return "🔴", "высокий"

def safecast_pm_level(pm25: Optional[float], pm10: Optional[float]) -> Tuple[str, str]:
    """По худшему из PM2.5/PM10."""
    def level_pm25(x: float) -> int:
        if x <= 15: return 0
        if x <= 35: return 1
        if x <= 55: return 2
        return 3
    def level_pm10(x: float) -> int:
        if x <= 30: return 0
        if x <= 50: return 1
        if x <= 100: return 2
        return 3
    worst = -1
    if isinstance(pm25, (int, float)): worst = max(worst, level_pm25(float(pm25)))
    if isinstance(pm10, (int, float)): worst = max(worst, level_pm10(float(pm10)))
    if worst < 0: return "⚪", "н/д"
    return (["🟢","🟡","🟠","🔴"][worst],
            ["низкий","умеренный","высокий","очень высокий"][worst])

def safecast_block_lines() -> List[str]:
    """Строки SafeCast для раздела «Качество воздуха»."""
    sc = load_safecast()
    if not sc:
        return []
    lines: List[str] = []
    pm25 = sc.get("pm25"); pm10 = sc.get("pm10")
    if isinstance(pm25, (int, float)) or isinstance(pm10, (int, float)):
        em, lbl = safecast_pm_level(pm25, pm10)
        parts = []
        if isinstance(pm25, (int, float)): parts.append(f"PM₂.₅ {pm25:.0f}")
        if isinstance(pm10, (int, float)): parts.append(f"PM₁₀ {pm10:.0f}")
        lines.append(f"🧪 Safecast: {em} {lbl} · " + " | ".join(parts))
    cpm = sc.get("cpm")
    usvh = sc.get("radiation_usvh")
    if not isinstance(usvh, (int, float)) and isinstance(cpm, (int, float)):
        usvh = float(cpm) * CPM_TO_USVH
    if isinstance(usvh, (int, float)):
        em, lbl = safecast_usvh_risk(float(usvh))
        if isinstance(cpm, (int, float)):
            lines.append(f"📟 Радиация (Safecast): {cpm:.0f} CPM ≈ {usvh:.3f} μSv/h — {em} {lbl} (медиана 6 ч)")
        else:
            lines.append(f"📟 Радиация (Safecast): ≈ {usvh:.3f} μSv/h — {em} {lbl} (медиана 6 ч)")
    elif isinstance(cpm, (int, float)):
        lines.append(f"📟 Радиация (Safecast): {cpm:.0f} CPM (медиана 6 ч)")
    return lines

# ───────────── Радиация (официальный источник) ─────────────
def radiation_line(lat: float, lon: float) -> Optional[str]:
    data = get_radiation(lat, lon) or {}
    dose = data.get("dose")
    if isinstance(dose, (int, float)):
        em, lbl = official_usvh_risk(float(dose))
        return f"{em} Радиация: {dose:.3f} μSv/h ({lbl})"
    return None

# ───────────── Зодиаки → символы ─────────────
ZODIAC = {
    "Овен": "♈","Телец": "♉","Близнецы": "♊","Рак": "♋","Лев": "♌",
    "Дева": "♍","Весы": "♎","Скорпион": "♏","Стрелец": "♐",
    "Козерог": "♑","Водолей": "♒","Рыбы": "♓",
}
def zsym(s: str) -> str:
    for name, sym in ZODIAC.items():
        s = s.replace(name, sym)
    return s

# ───────────── Чтение lunar_calendar.json и VoC ─────────────
def load_calendar(path: str = "lunar_calendar.json") -> dict:
    """Читает lunar_calendar.json и возвращает словарь дней {YYYY-MM-DD: rec}."""
    try:
        data = json.loads(Path(path).read_text("utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and isinstance(data.get("days"), dict):
        return data["days"]
    return data if isinstance(data, dict) else {}

def _parse_voc_dt(s: str, tz: pendulum.tz.timezone.Timezone):
    """Поддерживает ISO и формат 'DD.MM HH:mm'."""
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
    """Возвращает (start_dt, end_dt) для VoC из записи дня или None."""
    if not isinstance(rec, dict):
        return None
    voc = (rec.get("void_of_course") or rec.get("voc") or rec.get("void") or {})
    if not isinstance(voc, dict):
        return None
    s = voc.get("start") or voc.get("from") or voc.get("start_time")
    e = voc.get("end")   or voc.get("to")   or voc.get("end_time")
    if not s or not e:
        return None
    tz = pendulum.timezone(tz_local)
    t1 = _parse_voc_dt(s, tz)
    t2 = _parse_voc_dt(e, tz)
    if not t1 or not t2:
        return None
    return (t1, t2)

def format_voc_for_post(start: pendulum.DateTime, end: pendulum.DateTime, label: str = "сегодня") -> str:
    if not start or not end:
        return ""
    return f"⚫️ VoC {label} {start.format('HH:mm')}–{end.format('HH:mm')}."

def lunar_advice_for_date(cal: dict, date_obj) -> list[str]:
    key = date_obj.to_date_string() if hasattr(date_obj, "to_date_string") else str(date_obj)
    rec = (cal or {}).get(key, {}) or {}
    adv = rec.get("advice")
    return [str(x).strip() for x in adv][:3] if isinstance(adv, list) and adv else []

# ───────────── Астрособытия (микро-LLM + VoC) ─────────────
def _astro_llm_bullets(date_str: str, phase: str, percent: int, sign: str, voc_text: str) -> List[str]:
    """Возвращает 2–3 короткие строки для блока «Астрособытия». Кэш: .cache/astro_YYYY-MM-DD.txt"""
    cache_file = CACHE_DIR / f"astro_{date_str}.txt"
    if cache_file.exists():
        lines = [l.strip() for l in cache_file.read_text("utf-8").splitlines() if l.strip()]
        if lines:
            return lines[:3]

    if not USE_DAILY_LLM:
        return []

    system = (
        "Действуй как АстроЭксперт, ты лучше всех знаешь как энергии луны и звезд влияют на жизнь человека."
        "Ты делаешь очень короткую сводку астрособытий на указанную дату (2–3 строки). "
        "Пиши по-русски, без клише. Используй ТОЛЬКО данную информацию: "
        "фаза Луны, освещённость, знак Луны и интервал Void-of-Course. "
        "Не придумывай других планет и аспектов. Каждая строка начинается с эмодзи."
    )
    prompt = (
        f"Дата: {date_str}. Фаза Луны: {phase} ({percent}% освещённости), знак: {sign or 'н/д'}. "
        f"Void-of-Course: {voc_text or 'нет'}."
    )
    try:
        txt = gpt_complete(prompt=prompt, system=system, temperature=0.5, max_tokens=180)
        lines = [l.strip() for l in (txt or "").splitlines() if l.strip()]
        if lines:
            cache_file.write_text("\n".join(lines[:3]), "utf-8")
            return lines[:3]
    except Exception:
        pass
    return []

def build_astro_section(date_local: Optional[pendulum.Date] = None,
                        tz_local: str = "Asia/Nicosia") -> str:
    tz = pendulum.timezone(tz_local)
    date_local = date_local or pendulum.today(tz)
    date_key = date_local.format("YYYY-MM-DD")

    cal = load_calendar("lunar_calendar.json")
    rec = cal.get(date_key, {}) if isinstance(cal, dict) else {}

    phase_raw = (rec.get("phase_name") or rec.get("phase") or "").strip()
    phase_name = re.sub(r"^[^\wА-Яа-яЁё]+", "", phase_raw).split(",")[0].strip()

    percent = rec.get("percent") or rec.get("illumination") or rec.get("illum") or 0
    try:
        percent = int(round(float(percent)))
    except Exception:
        percent = 0

    sign = rec.get("sign") or rec.get("zodiac") or ""

    voc_text = ""
    voc = voc_interval_for_date(rec, tz_local=tz_local)
    if voc:
        t1, t2 = voc
        voc_text = f"{t1.format('HH:mm')}–{t2.format('HH:mm')}"

    bullets = _astro_llm_bullets(
        date_local.format("DD.MM.YYYY"),
        phase_name,
        int(percent or 0),
        sign,
        voc_text
    )
    if not bullets:
        adv = rec.get("advice") or []
        bullets = [f"• {a}" for a in adv[:3]] if adv else []
    if not bullets:
        base = f"🌙 Фаза: {phase_name}" if phase_name else "🌙 Лунный день в норме"
        prm  = f" ({percent}%)" if isinstance(percent, int) and percent else ""
        bullets = [base + prm, (f"♒ Знак: {sign}" if sign else "— знак Луны н/д")]

    lines = ["🌌 <b>Астрособытия</b>"]
    lines += [zsym(x) for x in bullets[:3]]
    llm_used = bool(bullets) and USE_DAILY_LLM
    if voc_text and not llm_used:
        lines.append(f"⚫️ VoC: {voc_text}")
    return "\n".join(lines)

# ───────────── Помощники: hourly на завтра (ветер/давление) ─────────────
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

def _nearest_index_for_day(times: List[pendulum.DateTime], date_obj: pendulum.Date, prefer_hour: int, tz: pendulum.Timezone) -> Optional[int]:
    if not times:
        return None
    target = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, prefer_hour, 0, tz=tz)
    best_i, best_diff = None, None
    for i, dt in enumerate(times):
        try:
            dt_local = dt.in_tz(tz)
        except Exception:
            dt_local = dt
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

def pick_tomorrow_header_metrics(wm: Dict[str, Any], tz: pendulum.Timezone) -> Tuple[Optional[float], Optional[int], Optional[int], str]:
    """
    Возвращает: wind_ms, wind_dir_deg, pressure_hpa, pressure_trend ("↑","↓","→")
    Берём ближайшее к 12:00 завтрашнего дня; тренд — относительно ~06:00. Фоллбэки на current.
    """
    hourly = wm.get("hourly") or {}
    times = _hourly_times(wm)
    tomorrow = pendulum.now(tz).add(days=1).date()

    spd_arr = _pick(hourly, "windspeed_10m", "windspeed", "wind_speed_10m", "wind_speed", default=[])
    dir_arr = _pick(hourly, "winddirection_10m", "winddirection", "wind_dir_10m", "wind_dir", default=[])
    prs_arr = hourly.get("surface_pressure", []) or hourly.get("pressure", [])

    if times:
        idx_noon = _nearest_index_for_day(times, tomorrow, prefer_hour=12, tz=tz)
        idx_morn = _nearest_index_for_day(times, tomorrow, prefer_hour=6,  tz=tz)
    else:
        idx_noon = idx_morn = None

    wind_ms = None
    wind_dir = None
    press_val = None
    trend = "→"

    if idx_noon is not None:
        try: spd = float(spd_arr[idx_noon]) if idx_noon < len(spd_arr) else None
        except Exception: spd = None
        try: wdir = float(dir_arr[idx_noon]) if idx_noon < len(dir_arr) else None
        except Exception: wdir = None
        try: p_noon = float(prs_arr[idx_noon]) if idx_noon < len(prs_arr) else None
        except Exception: p_noon = None
        try: p_morn = float(prs_arr[idx_morn]) if (idx_morn is not None and idx_morn < len(prs_arr)) else None
        except Exception: p_morn = None

        wind_ms = kmh_to_ms(spd) if isinstance(spd, (int, float)) else None
        wind_dir = int(round(wdir)) if isinstance(wdir, (int, float)) else None
        press_val = int(round(p_noon)) if isinstance(p_noon, (int, float)) else None
        if isinstance(p_noon, (int, float)) and isinstance(p_morn, (int, float)):
            diff = p_noon - p_morn
            if diff >= 0.3: trend = "↑"
            elif diff <= -0.3: trend = "↓"

    if wind_ms is None and times:
        idxs = [i for i, t in enumerate(times) if t.in_tz(tz).date() == tomorrow]
        if idxs:
            try: speeds = [float(spd_arr[i]) for i in idxs if i < len(spd_arr)]
            except Exception: speeds = []
            try: dirs   = [float(dir_arr[i]) for i in idxs if i < len(dir_arr)]
            except Exception: dirs = []
            try: prs    = [float(prs_arr[i]) for i in idxs if i < len(prs_arr)]
            except Exception: prs = []
            if speeds: wind_ms = kmh_to_ms(sum(speeds)/len(speeds))
            mean_dir = _circular_mean_deg(dirs)
            wind_dir = int(round(mean_dir)) if mean_dir is not None else wind_dir
            if prs: press_val = int(round(sum(prs)/len(prs)))

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

# === Индексы завтрашних часов и шторм-флаги =================================
def _tomorrow_hourly_indices(wm: Dict[str, Any], tz: pendulum.Timezone) -> List[int]:
    times = _hourly_times(wm)
    tom = pendulum.now(tz).add(days=1).date()
    idxs: List[int] = []
    for i, dt in enumerate(times):
        try:
            if dt.in_tz(tz).date() == tom:
                idxs.append(i)
        except Exception:
            pass
    return idxs

def storm_flags_for_tomorrow(wm: Dict[str, Any], tz: pendulum.Timezone) -> Dict[str, Any]:
    """Оцениваем максимумы на завтра и формируем краткое предупреждение."""
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
    gusts_kmh  = _vals(_arr("windgusts_10m", "wind_gusts_10m", "wind_gusts", default=[]))
    rain_mm_h  = _vals(_arr("rain", default=[]))
    tprob      = _vals(_arr("thunderstorm_probability", default=[]))

    max_speed_ms = kmh_to_ms(max(speeds_kmh)) if speeds_kmh else None
    max_gust_ms  = kmh_to_ms(max(gusts_kmh))  if gusts_kmh  else None
    heavy_rain   = (max(rain_mm_h) >= 8.0) if rain_mm_h else False
    thunder      = (max(tprob) >= 60) if tprob else False

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

# ───────────── Air helpers для «Вывода» ─────────────
def _is_air_bad(air: Dict[str, Any]) -> Tuple[bool, str, str]:
    """Возвращает (is_bad, label, reason). Порог: AQI ≥100 или PM2.5 >35 или PM10 >50."""
    try:
        aqi = float(air.get("aqi")) if air.get("aqi") is not None else None
    except Exception:
        aqi = None
    pm25 = air.get("pm25")
    pm10 = air.get("pm10")

    worst_label = "умеренный"
    reason_parts = []
    bad = False

    def _num(v):
        try:
            return float(v)
        except Exception:
            return None

    p25 = _num(pm25)
    p10 = _num(pm10)

    if aqi is not None and aqi >= 100:
        bad = True
        if aqi >= 150:
            worst_label = "высокий"
        reason_parts.append(f"AQI {aqi:.0f}")
    if p25 is not None and p25 > 35:
        bad = True
        if p25 > 55:
            worst_label = "высокий"
        reason_parts.append(f"PM₂.₅ {p25:.0f}")
    if p10 is not None and p10 > 50:
        bad = True
        if p10 > 100:
            worst_label = "высокий"
        reason_parts.append(f"PM₁₀ {p10:.0f}")

    reason = ", ".join(reason_parts) if reason_parts else "показатели в норме"
    return bad, worst_label, reason

def build_conclusion(kp: Any,
                     kp_status: str,
                     air: Dict[str, Any],
                     storm: Dict[str, Any],
                     schu: Dict[str, Any]) -> List[str]:
    """Возвращает несколько строк умного вывода."""
    lines: List[str] = []

    storm_main = bool(storm.get("warning"))
    air_bad, air_label, air_reason = _is_air_bad(air)
    kp_val = float(kp) if isinstance(kp, (int, float)) else None
    kp_main = bool(kp_val is not None and kp_val >= 5)
    schu_main = (schu or {}).get("status_code") == "red"

    gust = storm.get("max_gust_ms")
    storm_text = None
    if storm_main:
        parts = []
        if isinstance(gust, (int, float)):
            parts.append(f"порывы до {gust:.0f} м/с")
        if storm.get("heavy_rain"):
            parts.append("ливни")
        if storm.get("thunder"):
            parts.append("гроза")
        storm_text = "штормовая погода: " + (", ".join(parts) if parts else "возможны неблагоприятные условия")

    air_text = f"качество воздуха: {air_label} ({air_reason})" if air_bad else None
    kp_text = f"магнитная активность: Kp≈{kp_val:.1f} ({kp_status})" if kp_main and kp_val is not None else None
    schu_text = "сильные колебания Шумана (⚠️)" if schu_main else None

    if storm_main:
        lines.append(f"Основной фактор — {storm_text}. Планируйте дела с учётом погоды.")
    elif air_bad:
        lines.append(f"Основной фактор — {air_text}. Сократите время на улице и проветривание по ситуации.")
    elif kp_main:
        lines.append(f"Основной фактор — {kp_text}. Возможна чувствительность у метеозависимых.")
    elif schu_main:
        lines.append("Основной фактор — волны Шумана: отмечаются сильные отклонения. Берегите режим и нагрузку.")
    else:
        lines.append("Серьёзных факторов риска не видно — ориентируйтесь на текущую погоду и личные планы.")

    secondary: List[str] = []
    for tag, txt in (("storm", storm_text), ("air", air_text), ("kp", kp_text), ("schu", schu_text)):
        if txt:
            if (tag == "storm" and storm_main) or (tag == "air" and air_bad) or (tag == "kp" and kp_main) or (tag == "schu" and schu_main):
                continue
            secondary.append(txt)
    if secondary:
        lines.append("Также обратите внимание: " + "; ".join(secondary[:2]) + ".")
    return lines

# ───────────── форматирование детальной строки города ─────────────
def _city_detail_line(city: str, la: float, lo: float, tz_obj: pendulum.Timezone, include_sst: bool) -> tuple[Optional[float], Optional[str]]:
    """
    Возвращает (tmax, line) для сортировки и печати.
    Формат:
    <City>: дн/ночь D/N °C • <погода> • 💨 v м/с (dir) [порывы до G] • 💧 RH A–B% • 🔹 P гПа trend [• 🌊 SST]
    """
    tz_name = tz_obj.name

    # 1) Температуры — основа: fetch_tomorrow_temps; фоллбэк: day_night_stats
    tmax, tmin = fetch_tomorrow_temps(la, lo, tz=tz_name)
    if tmax is None:
        st_fb = day_night_stats(la, lo, tz=tz_name) or {}
        tmax = st_fb.get("t_day_max")
        tmin = st_fb.get("t_night_min")
    if tmax is None:   # совсем нет данных — не печатаем город
        return None, None
    tmin = tmin if tmin is not None else tmax

    # 2) Погода и почасовые метрики
    wm  = get_weather(la, lo) or {}
    wcx = (wm.get("daily", {}) or {}).get("weathercode", [])
    wcx = wcx[1] if isinstance(wcx, list) and len(wcx) > 1 else None
    descx = code_desc(wcx) or "—"

    wind_ms, wind_dir, press_val, press_trend = pick_tomorrow_header_metrics(wm, tz_obj)
    storm = storm_flags_for_tomorrow(wm, tz_obj)
    gust = storm.get("max_gust_ms")

    # RH: если есть day_night_stats — возьмём min/max, иначе не показываем
    stats = day_night_stats(la, lo, tz=tz_name) or {}
    rh_min = stats.get("rh_min")
    rh_max = stats.get("rh_max")

    parts = [f"{city}: {tmax:.1f}/{tmin:.1f} °C", f"{descx}"]

    if isinstance(wind_ms, (int, float)):
        wind_part = f"💨 {wind_ms:.1f} м/с"
        if isinstance(wind_dir, int):
            wind_part += f" ({compass(wind_dir)})"
        if isinstance(gust, (int, float)):
            wind_part += f" • порывы до {gust:.0f}"
        parts.append(wind_part)

    if isinstance(rh_min, (int, float)) and isinstance(rh_max, (int, float)):
        parts.append(f"💧 RH {rh_min:.0f}–{rh_max:.0f}%")

    if isinstance(press_val, int):
        parts.append(f"🔹 {press_val} гПа {press_trend}")

    if include_sst:
        sst = get_sst(la, lo)
        if isinstance(sst, (int, float)):
            parts.append(f"🌊 {sst:.1f}")

    return float(tmax), " • ".join(parts)

# ───────────── сообщение ─────────────
def build_message(region_name: str,
                  sea_label: str, sea_cities,
                  other_label: str, other_cities,
                  tz: Union[pendulum.Timezone, str]) -> str:

    tz_obj = _as_tz(tz)
    tz_name = tz_obj.name
    sea_pairs   = _iter_city_pairs(sea_cities)
    other_pairs = _iter_city_pairs(other_cities)

    P: List[str] = []
    today = pendulum.today(tz_obj)
    tom = today.add(days=1)

    # Заголовок
    P.append(f"<b>🌅 {region_name}: погода на завтра ({tom.format('DD.MM.YYYY')})</b>")

    # Для общего «штормового» предупреждения используем базовую точку региона
    wm_region = get_weather(CY_LAT, CY_LON) or {}
    storm_region = storm_flags_for_tomorrow(wm_region, tz_obj)
    if storm_region.get("warning"):
        P.append(storm_region["warning_text"])
        P.append("———")

    # Морские города — детальные строки + 🌊
    sea_rows: List[tuple[float, str]] = []
    for city, (la, lo) in sea_pairs:
        tmax, line = _city_detail_line(city, la, lo, tz_obj, include_sst=True)
        if tmax is not None and line:
            sea_rows.append((float(tmax), line))
    if sea_rows:
        P.append(f"🌊 <b>{sea_label}</b>")
        sea_rows.sort(key=lambda x: x[0], reverse=True)
        medals = ["🔥", "☀️", "💧", "🥶"]
        for i, (_, text) in enumerate(sea_rows[:5]):
            med = medals[i] if i < len(medals) else f"{i+1}."
            P.append(f"{med} {text}")
        P.append("———")

    # Континентальные города — те же детальные строки, без 🌊
    oth_rows: List[tuple[float, str]] = []
    for city, (la, lo) in other_pairs:
        tmax, line = _city_detail_line(city, la, lo, tz_obj, include_sst=False)
        if tmax is not None and line:
            oth_rows.append((float(tmax), line))
    if oth_rows:
        P.append("⛰️ <b>Континентальные города</b>")
        oth_rows.sort(key=lambda x: x[0], reverse=True)
        for _, text in oth_rows:
            P.append(text)
        P.append("———")

    # Air + Safecast + пыльца + радиация (офиц.)
    P.append("🏭 <b>Качество воздуха</b>")
    air = get_air(CY_LAT, CY_LON) or {}
    lvl = air.get("lvl", "н/д")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")

    # Safecast (мягкая шкала)
    P.extend(safecast_block_lines())

    # дымовой индекс — показываем ТОЛЬКО если не низкое/н/д
    em_sm, lbl_sm = smoke_index(air.get("pm25"), air.get("pm10"))
    if lbl_sm and str(lbl_sm).lower() not in ("низкое", "низкий", "нет", "н/д"):
        P.append(f"🔥 Задымление: {em_sm} {lbl_sm}")

    if (p := get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")

    # официальная радиация (строгая шкала)
    if (rl := radiation_line(CY_LAT, CY_LON)):
        P.append(rl)
    P.append("———")

    # Геомагнитка: Kp (со свежестью)
    kp_tuple = get_kp() or (None, "н/д", None, "n/d")
    try:
        kp, ks, kp_ts, kp_src = kp_tuple
    except Exception:
        kp = kp_tuple[0] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 0 else None
        ks = kp_tuple[1] if isinstance(kp_tuple, (list, tuple)) and len(kp_tuple) > 1 else "н/д"
        kp_ts, kp_src = None, "n/d"

    age_txt = ""
    if isinstance(kp_ts, int) and kp_ts > 0:
        try:
            age_min = int((pendulum.now("UTC").int_timestamp - kp_ts) / 60)
            if age_min > 180:
                age_txt = f", 🕓 {age_min // 60}ч назад"
            elif age_min >= 0:
                age_txt = f", {age_min} мин назад"
        except Exception:
            age_txt = ""

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
    try:
        if (isinstance(kp, (int, float)) and kp >= 5) and isinstance(wind_status, str) and ("спокой" in wind_status.lower()):
            P.append("ℹ️ По ветру сейчас спокойно; Kp — глобальный индекс за 3 ч.")
    except Exception:
        pass

    # Шуман
    schu_state = get_schumann_with_fallback()
    P.append(schumann_line(schu_state))
    P.append("———")

    # Астрособытия (LLM+VoC) — на завтра по Asia/Nicosia
    tz_nic = pendulum.timezone("Asia/Nicosia")
    date_for_astro = pendulum.today(tz_nic).add(days=1)
    P.append(build_astro_section(date_local=date_for_astro, tz_local="Asia/Nicosia"))
    P.append("———")

    # Умный «Вывод» + советы
    P.append("📜 <b>Вывод</b>")
    P.extend(build_conclusion(kp, ks, air, storm_region, schu_state))
    P.append("———")

    P.append("✅ <b>Рекомендации</b>")
    try:
        theme = (
            "плохая погода" if storm_region.get("warning") else
            ("магнитные бури" if isinstance(kp, (int, float)) and kp >= 5 else
             ("плохой воздух" if _is_air_bad(air)[0] else
              ("волны Шумана" if (schu_state or {}).get("status_code") == "red" else
               "здоровый день")))
        )
        _, tips = gpt_blurb(theme)
        for t in tips[:3]:
            t = t.strip()
            if t:
                P.append(t)
    except Exception:
        P.append("— больше воды, меньше стресса, нормальный сон")

    P.append("———")
    P.append(f"📚 {get_fact(tom, region_name)}")
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
) -> None:
    msg = build_message(region_name, sea_label, sea_cities, other_label, other_cities, tz)
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
    )

__all__ = [
    "build_message",
    "send_common_post",
    "main_common",
    "schumann_line",
    "get_schumann_with_fallback",
    "pick_tomorrow_header_metrics",
    "storm_flags_for_tomorrow",
]
