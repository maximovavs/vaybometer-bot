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
from world_en.settings_world_en import HOT_CITIES, COLD_SPOTS

OUT = Path(__file__).parent / "weekly.json"
HEADERS = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Accept": "application/json,text/plain",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# --------- helpers ---------

def _get_json(url, params=None, timeout=25):
    r = requests.get(url, params=params or {}, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.json()

def strongest_quake_week():
    urls = [
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/6.0_week.geojson",
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
    ]
    for url in urls:
        try:
            feats = _get_json(url).get("features", [])
            if not feats: 
                continue
            top = max(feats, key=lambda f: f["properties"]["mag"] or 0)
            mag = round(top["properties"]["mag"], 1)
            region = top["properties"]["place"]
            note = top["properties"].get("type","")
            return mag, region, note
        except Exception:
            continue
    return None, None, None

def openmeteo_week_extremes():
    """
    Безлоговый расчёт экстремумов за последние 7 суток.
    По каждому месту берём daily temperature_2m_max/min и собираем глобальные max/min.
    """
    hottest = None   # {"place":..., "temp":...}
    coldest = None
    def fetch_daily(lat, lon):
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min",
            "past_days": 7, "forecast_days": 1,
            "timezone": "UTC"
        }
        return _get_json(url, params=params).get("daily", {})
    # горячие
    for name, la, lo in HOT_CITIES:
        try:
            d = fetch_daily(la, lo)
            if not d: 
                continue
            mx = d.get("temperature_2m_max", [])
            if mx:
                loc_max = max(mx)
                if (hottest is None) or (loc_max > hottest["temp"]):
                    hottest = {"place": name, "temp": round(loc_max)}
        except Exception:
            continue
    # холодные
    for name, la, lo in COLD_SPOTS:
        try:
            d = fetch_daily(la, lo)
            if not d:
                continue
            mn = d.get("temperature_2m_min", [])
            if mn:
                loc_min = min(mn)
                if (coldest is None) or (loc_min < coldest["temp"]):
                    coldest = {"place": name, "temp": round(loc_min)}
        except Exception:
            continue
    return hottest, coldest

def kp_outlook_3d():
    """Грубый парсинг SWPC текста на 3 дня. Возвращает строку вида '3 / 2 / 4'."""
    try:
        txt = requests.get("https://services.swpc.noaa.gov/text/3-day-geomag-forecast.txt",
                           timeout=25, headers=HEADERS).text
        lines = [ln for ln in txt.splitlines() if "kp" in ln.lower()]
        nums = []
        for ln in lines:
            nums += [int(m.group(0)) for m in re.finditer(r"(?<!\d)[0-9](?!\d)", ln)]
            if len(nums) >= 3:
                break
        if len(nums) >= 3:
            return " / ".join(map(str, nums[:3])), nums[:3]
    except Exception:
        pass
    return "stable ~3", []

def calm_window_from_kp(vals):
    if not vals:
        return "Wed 09–12 (low Kp)"
    idx = min(range(len(vals)), key=lambda i: vals[i])
    day = (dt.datetime.utcnow().date() + dt.timedelta(days=idx+1)).strftime("%a")
    return f"{day} 09–12 (low Kp ~{vals[idx]})"

def moon_phase_name(d: dt.date):
    p = moon.phase(d)
    if p == 0: return "New Moon"
    if 0 < p < 7: return "Waxing Crescent"
    if p == 7: return "First Quarter"
    if 7 < p < 15: return "Waxing Gibbous"
    if p == 15: return "Full Moon"
    if 15 < p < 22: return "Waning Gibbous"
    if p == 22: return "Last Quarter"
    if 22 < p < 29: return "Waning Crescent"
    return "New Moon"

def reykjavik_sunset_today():
    try:
        loc = LocationInfo("Reykjavik", "", "UTC", 64.1466, -21.9426)
        s = sun(loc.observer, date=dt.date.today(), tzinfo=UTC)
        return s["sunset"].strftime("%H:%M")
    except Exception:
        return dt.datetime.utcnow().strftime("%H:%M")

# --------- main ---------

def main():
    today = dt.date.today()
    week_start = (today - dt.timedelta(days=today.weekday())).isoformat()
    week_end = today.isoformat()

    # 1) Землетрясение недели
    mag, region, note = strongest_quake_week()

    # 2) Экстремумы недели — считаем на лету
    hot, cold = openmeteo_week_extremes()

    # 3) Kp outlook + calm window
    kp_note, kp_vals = kp_outlook_3d()
    calm_win = calm_window_from_kp(kp_vals)

    # 4) Луна на конец следующей недели
    next_week_end = today + dt.timedelta(days=7)
    next_moon_phase = moon_phase_name(next_week_end)

    # 5) Валюты (6 штук)
    fx = fetch_rates("USD", ["EUR","CNY","JPY","INR","IDR"])
    fx_line_week = format_line(fx, order=["USD","EUR","CNY","JPY","INR","IDR"])

    out = {
        "WEEK_START": week_start,
        "WEEK_END": week_end,
        "TOP_QUAKE_MAG": mag or "—",
        "TOP_QUAKE_REGION": region or "—",
        "TOP_QUAKE_NOTE": note or "",
        "HOTTEST_WEEK_PLACE": (hot or {}).get("place","—"),
        "HOTTEST_WEEK": (hot or {}).get("temp","—"),
        "COLDEST_WEEK_PLACE": (cold or {}).get("place","—"),
        "COLDEST_WEEK": (cold or {}).get("temp","—"),
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
