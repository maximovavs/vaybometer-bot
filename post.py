#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
post.py — вечерний пост VayboMeter-бота (Кипр).

• Прогноз на завтра (температура, ветер, порывы, давление)
• Рейтинг городов (с SST для прибрежных)
• Качество воздуха + пыльца + ☢️ Радиация
• Kp-индекс + резонанс Шумана (фоллбэк из JSON)
• Астрособытия (знак Луны → ♈-♓)
• Умный «Вывод» + рекомендации
• Факт дня
"""

from __future__ import annotations
import os, json, logging, asyncio, re, math
from pathlib import Path
from typing import Dict, Any, Tuple, List, Optional

import pendulum
from telegram import Bot, error as tg_err

from utils   import compass, clouds_word, get_fact, AIR_EMOJI, pm_color, kp_emoji, kmh_to_ms
from weather import get_weather, fetch_tomorrow_temps
from air     import get_air, get_sst, get_kp
from pollen  import get_pollen
from schumann import get_schumann
from astro   import astro_events
from gpt     import gpt_blurb
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
def code_desc(c: Any) -> str:
    try:
        return WMO_DESC.get(int(c), "—")
    except Exception:
        return "—"

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

def _nearest_index(times: List[pendulum.DateTime], date_obj: pendulum.Date, prefer_hour: int) -> Optional[int]:
    if not times:
        return None
    target = pendulum.datetime(date_obj.year, date_obj.month, date_obj.day, prefer_hour, 0, tz=TZ)
    best_i, best_diff = None, None
    for i, dt in enumerate(times):
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

# ────────── стрелка давления (на базе trend) ──────────
def pressure_arrow(trend: str) -> str:
    return {"↑":"↑","↓":"↓","→":"→"}.get(trend, "→")

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

    # локальный кэш истории
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
    """
    (is_bad, label, reason). Порог: AQI ≥100 или PM2.5 >35 или PM10 >50
    """
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
                     gust_ms: Optional[float],
                     schu: Dict[str, Any]) -> List[str]:
    """Формирует умные выводы по основному и вторичным факторам."""
    lines: List[str] = []
    air_bad, air_label, air_reason = _is_air_bad(air)
    storm_main = isinstance(gust_ms, (int, float)) and gust_ms >= 17  # ~ ≥17 м/с — ощутимые порывы
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
        # уберём основной из списка
        primary = lines[0]
        rest = [t for t in secondary if t not in primary]
        if rest:
            lines.append("Также обратите внимание: " + "; ".join(rest[:2]) + ".")
    return lines

# ────────── Зодиак-замена для астрособытий ──────────
_ZODIAC = {
    "овен": "♈", "телец": "♉", "близнецы": "♊", "рак": "♋",
    "лев": "♌", "дева": "♍", "весы": "♎", "скорпион": "♏",
    "стрелец": "♐", "козерог": "♑", "водолей": "♒", "рыбы": "♓",
}
_z_regex = re.compile(r"\b[Вв]\s+(%s)" % "|".join(_ZODIAC.keys()), flags=re.I)
def zodiac_replace(s: str) -> str:
    def sub(m): return " " + _ZODIAC[m.group(1).lower()]
    return _z_regex.sub(sub, s)

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

    # Город по умолчанию (можно переопределить через PRIMARY_CITY)
    primary = os.getenv("PRIMARY_CITY", "Limassol")
    lat, lon = CITIES.get(primary, CITIES["Limassol"])

    # Прогноз, температуры
    w   = get_weather(lat, lon) or {}
    day_max, night_min = fetch_tomorrow_temps(lat, lon, tz=TZ.name)

    wind_ms, wind_dir, press_hpa, p_trend, gust_max = pick_header_metrics(w)

    avg_t  = ((day_max + night_min)/2) if (day_max is not None and night_min is not None) else None
    parts = [
        (f"🌡️ Ср. темп: {avg_t:.0f} °C" if isinstance(avg_t, (int, float)) else None),
        clouds_word((w.get("current") or {}).get("clouds", 0)),
        (f"💨 {wind_ms:.1f} м/с ({compass(wind_dir)})" if isinstance(wind_ms, (int, float)) and wind_dir is not None
            else (f"💨 {wind_ms:.1f} м/с" if isinstance(wind_ms, (int, float)) else "💨 н/д")),
        (f"(порывы до {gust_max:.0f})" if isinstance(gust_max, (int, float)) else None),
        (f"💧 {press_hpa} гПа {pressure_arrow(p_trend)}" if isinstance(press_hpa, int) else None),
    ]
    P.append(" • ".join([p for p in parts if p]))
    P.append("———")

    # Рейтинг городов
    temps: Dict[str,Tuple[float,float,int,Optional[float]]] = {}
    for city,(la,lo) in CITIES.items():
        d,n = fetch_tomorrow_temps(la,lo, tz=TZ.name)
        if d is None: 
            continue
        wc  = (get_weather(la,lo) or {}).get("daily",{}).get("weathercode",[])
        wc  = wc[1] if isinstance(wc,list) and len(wc)>1 else 0
        sst = get_sst(la,lo) if city in COASTAL_CITIES else None
        temps[city] = (d, n if n is not None else d, wc, sst)

    if temps:
        P.append("🎖️ <b>Рейтинг городов (д./н. °C, погода, 🌊)</b>")
        medals = ["🥇","🥈","🥉","4️⃣","5️⃣","6️⃣"]
        for i,(city,(d,n,wc,sst)) in enumerate(sorted(temps.items(), key=lambda kv: kv[1][0], reverse=True)[:6]):
            line = f"{medals[i]} {city}: {d:.1f}/{n:.1f}, {code_desc(wc)}"
            if sst is not None: line += f", 🌊 {sst:.1f}"
            P.append(line)
        P.append("———")

    # Air + pollen (нужны координаты)
    air = get_air(lat, lon) or {}
    lvl = air.get("lvl","н/д")
    P.append("🏭 <b>Качество воздуха</b>")
    P.append(f"{AIR_EMOJI.get(lvl,'⚪')} {lvl} (AQI {air.get('aqi','н/д')}) | "
             f"PM₂.₅: {pm_color(air.get('pm25'))} | PM₁₀: {pm_color(air.get('pm10'))}")
    if (p:=get_pollen()):
        P.append("🌿 <b>Пыльца</b>")
        P.append(f"Деревья: {p['tree']} | Травы: {p['grass']} | Сорняки: {p['weed']} — риск {p['risk']}")

    # ☢️ Радиация
    rad = radiation.get_radiation(lat, lon) or {}
    val = rad.get("value") or rad.get("dose")
    if isinstance(val, (int, float)):
        P.append(f"☢️ Радиация: {float(val):.3f} µSv/h")
    P.append("———")

    # Геомагнитка + Шуман
    try:
        kp, ks = get_kp()
    except Exception:
        kp, ks = None, "н/д"
    P.append(f"{kp_emoji(kp)} Геомагнитка: Kp={kp:.1f} ({ks})" if isinstance(kp, (int, float)) else "🧲 Геомагнитка: н/д")

    schu_state = get_schumann_with_fallback()
    P.extend(schumann_lines(schu_state))
    P.append("———")

    # Астрособытия (с заменой знака)
    P.append("🌌 <b>Астрособытия</b>")
    astro_lines = astro_events(offset_days=1, show_all_voc=True)
    formatted = [zodiac_replace(l) for l in astro_lines] if astro_lines else ["— нет данных —"]
    P.extend(formatted)
    P.append("———")

    # «Умный» вывод
    P.append("📜 <b>Вывод</b>")
    P.extend(build_conclusion(kp, ks, air, gust_max, schu_state))
    P.append("———")

    # рекомендации
    try:
        theme = (
            "плохая погода" if isinstance(gust_max, (int, float)) and gust_max >= 17 else
            ("магнитные бури" if isinstance(kp, (int, float)) and kp >= 5 else
             ("плохой воздух" if _is_air_bad(air)[0] else "здоровый день"))
        )
        _, tips = gpt_blurb(theme)
        for tip in tips[:3]:
            t = tip.strip()
            if t:
                P.append(t)
    except Exception:
        P.append("— больше воды, меньше стресса, нормальный сон")
    P.append("———")

    # факт дня
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
