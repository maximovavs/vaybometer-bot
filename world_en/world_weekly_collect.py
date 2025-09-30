#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, re, datetime as dt
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

import requests
from astral import moon
from astral.sun import sun
from astral import LocationInfo
from pytz import UTC

from world_en.fx_intl import fetch_rates, format_line

OUT = Path(__file__).parent / "weekly.json"
LOG_DIR = Path(__file__).parent / "logs"
HEADERS = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Accept": "application/json,text/plain",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# ---------- helpers ----------

def _get(url: str, timeout: int = 20, headers: dict | None = None, as_text: bool = False):
    r = requests.get(url, timeout=timeout, headers=headers or HEADERS)
    r.raise_for_status()
    return r.text if as_text else r.json()

def strongest_quake_week():
    """Возвращает максимальный магнитудой толчок за 7 дней."""
    urls = [
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/6.0_week.geojson",
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
    ]
    for url in urls:
        try:
            feats = _get(url).get("features", [])
            if not feats:
                continue
            top = max(feats, key=lambda f: f["properties"]["mag"] or 0)
            mag = round(top["properties"]["mag"], 1)
            region = top["properties"]["place"]
            note = top["properties"].get("type", "")
            return mag, region, note
        except Exception:
            continue
    return None, None, None

def weekly_extremes():
    """
    Читает world_en/logs/extremes.jsonl (пишется daily-сборщиком)
    и возвращает: hottest_place, hottest_temp, coldest_place, coldest_temp за последние 7 дней.
    """
    path = LOG_DIR / "extremes.jsonl"
    if not path.exists():
        return None, None, None, None

    week_ago = dt.date.today() - dt.timedelta(days=7)
    hot = cold = None
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                it = json.loads(line)
                d = dt.date.fromisoformat(it["date"])
                if d < week_ago:
                    continue
                ht = it.get("hottest_temp")
                ct = it.get("coldest_temp")
                if ht is not None:
                    if not hot or ht > hot["temp"]:
                        hot = {"place": it.get("hottest_place"), "temp": ht}
                if ct is not None:
                    if not cold or ct < cold["temp"]:
                        cold = {"place": it.get("coldest_place"), "temp": ct}
            except Exception:
                continue
    return (hot or {}).get("place"), (hot or {}).get("temp"), (cold or {}).get("place"), (cold or {}).get("temp")

def kp_outlook_3d():
    """
    Грубый парсинг SWPC текста на 3 дня. Возвращает:
    - список из трёх чисел (оценка Kp по дням),
    - строку-описание для Weekly.
    """
    try:
        txt = _get("https://services.swpc.noaa.gov/text/3-day-geomag-forecast.txt", as_text=True)
        # Ищем три первых одиночных числа 0..9 после слов Kp или Kp-index
        lines = [ln for ln in txt.splitlines() if "kp" in ln.lower()]
        digits = []
        for ln in lines:
            digits += [int(m.group(0)) for m in re.finditer(r"(?<!\d)[0-9](?!\d)", ln)]
            if len(digits) >= 3:
                break
        vals = digits[:3]
        if len(vals) == 3:
            return vals, " / ".join(map(str, vals))
    except Exception:
        pass
    return [], "stable ~3"

def calm_window_from_kp(vals):
    """
    Выбирает день с минимальным прогнозным Kp.
    Возвращает строку вида 'Tue 09–12 (low Kp ~3)'.
    """
    if not vals or len(vals) < 1:
        return "Wed 09–12 (low Kp)"
    idx = min(range(len(vals)), key=lambda i: vals[i])
    # День idx: 0 — завтра, 1 — послезавтра, 2 — +2
    day = (dt.datetime.utcnow().date() + dt.timedelta(days=idx+1)).strftime("%a")
    return f"{day} 09–12 (low Kp ~{vals[idx]})"

def moon_phase_name(d: dt.date):
    """Имя фазы на английском по дате."""
    p = moon.phase(d)  # 0..29
    if p == 0:
        return "New Moon"
    if 0 < p < 7:
        return "Waxing Crescent"
    if p == 7:
        return "First Quarter"
    if 7 < p < 15:
        return "Waxing Gibbous"
    if p == 15:
        return "Full Moon"
    if 15 < p < 22:
        return "Waning Gibbous"
    if p == 22:
        return "Last Quarter"
    if 22 < p < 29:
        return "Waning Crescent"
    return "New Moon"

def reykjavik_sunset_today():
    """Для милого факта: закат в Рейкьявике сегодня (UTC)."""
    try:
        loc = LocationInfo("Reykjavik", "", "UTC", 64.1466, -21.9426)
        s = sun(loc.observer, date=dt.date.today(), tzinfo=UTC)
        return s["sunset"].strftime("%H:%M")
    except Exception:
        return dt.datetime.utcnow().strftime("%H:%M")

# ---------- main ----------

def main():
    today = dt.date.today()
    week_start = (today - dt.timedelta(days=today.weekday())).isoformat()
    week_end = today.isoformat()

    # 1) Землетрясение недели
    mag, region, note = strongest_quake_week()

    # 2) Экстремумы недели из дневного лога
    hp, ht, cp, ct = weekly_extremes()

    # 3) Kp-прогноз и «calm window»
    kp_vals, kp_note = kp_outlook_3d()
    calm_win = calm_window_from_kp(kp_vals)

    # 4) Следующая недельная луна (фаза на конец следующей недели)
    next_week_end = today + dt.timedelta(days=7)
    next_moon_phase = moon_phase_name(next_week_end)

    # 5) Валюты
    fx = fetch_rates("USD", ["EUR","CNY","JPY","INR","IDR"])
    fx_line_week = format_line(fx, order=["EUR","CNY","JPY","INR","IDR"])

    out = {
        "WEEK_START": week_start,
        "WEEK_END": week_end,
        "TOP_QUAKE_MAG": mag or "—",
        "TOP_QUAKE_REGION": region or "—",
        "TOP_QUAKE_NOTE": note or "",
        "HOTTEST_WEEK_PLACE": hp or "—",
        "HOTTEST_WEEK": ht or "—",
        "COLDEST_WEEK_PLACE": cp or "—",
        "COLDEST_WEEK": ct or "—",
        "CALM_WINDOW_UTC": calm_win,
        "SUN_HIGHLIGHT_PLACE": "Reykjavik, IS",
        "SUN_HIGHLIGHT_TIME": reykjavik_sunset_today(),
        "TOP_NATURE_TITLE": "Nature Break",
        "fx_line_week": fx_line_week,
        "NEXT_MOON_PHASE": next_moon_phase,
        "NEXT_KP_NOTE": kp_note
    }

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
