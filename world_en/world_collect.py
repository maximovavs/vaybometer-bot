# world_en/world_collect.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re
import json
import datetime as dt
from pathlib import Path
from typing import Optional, Tuple

import requests
from astral.sun import sun
from astral import LocationInfo
from pytz import UTC

from world_en.fx_intl import fetch_rates, format_line
from world_en.settings_world_en import (
    HOT_CITIES, COLD_SPOTS, SUN_CITIES, VIBE_TIPS,
    YT_API_KEY, YT_CHANNEL_ID, FALLBACK_NATURE_LIST
)

ROOT = Path(__file__).resolve().parents[1]
OUT  = Path(__file__).parent / "daily.json"

HEADERS = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Accept": "application/json,text/plain",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# ---------------- HTTP helpers ----------------

def _get_json(url: str, params: dict | None = None, timeout: int = 20):
    r = requests.get(url, params=params or {}, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.json()

# ---------------- Cosmic weather ----------------

def fetch_kp_now() -> Optional[float]:
    """Пробуем несколько SWPC источников; нормализуем в диапазон 0..9."""
    urls = [
        "https://services.swpc.noaa.gov/products/noaa-estimated-planetary-k-index-1-minute.json",
        "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    ]
    for url in urls:
        try:
            data = _get_json(url)
            if isinstance(data, list):
                rows = data[1:] if data and isinstance(data[0], list) and "time_tag" in ",".join(map(str, data[0])) else data
                last = rows[-1]
                vals = [v for v in last[-3:] if isinstance(v, (int, float, str))]
                for v in reversed(vals):
                    try:
                        x = float(v)
                        if x > 9:
                            x = x / 10.0
                        if 0 <= x <= 9:
                            return round(x, 2)
                    except Exception:
                        continue
        except Exception:
            continue
    return None

def kp_badge(kp: Optional[float]) -> str:
    try:
        k = float(kp)
    except Exception:
        return ""
    if k >= 5: return "🟠"
    if k >= 4: return "🟡"
    return "🟢"

def kp_note(kp: Optional[float]) -> str:
    try:
        k = float(kp)
    except Exception:
        return ""
    if k < 2:   return "quiet"
    if k < 3.5: return "calm to moderate"
    if k < 5:   return "active"
    return "storm conditions"

def fetch_solar_wind() -> Tuple[Optional[float], Optional[float]]:
    """Возвращает (speed_km_s, density_cm3)."""
    try:
        data = _get_json("https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json")
        rows = data[1:] if data and "time_tag" in ",".join(map(str, data[0])) else data
        last = rows[-1]
        dens = float(last[1]) if last[1] is not None else None
        spd  = float(last[2]) if last[2] is not None else None
        return (round(spd, 0) if spd is not None else None,
                round(dens, 0) if dens is not None else None)
    except Exception:
        return None, None

def solar_note(speed: Optional[float], density: Optional[float]) -> str:
    if speed is None and density is None:
        return ""
    s = speed or 0
    if s < 380:  base = "very gentle stream"
    elif s < 450: base = "gentle stream"
    elif s < 600: base = "moderate stream"
    else:        base = "fast stream"
    if density is not None:
        if density < 3: base += " • low density"
        elif density > 12: base += " • dense flow"
    return base

def read_schumann_amp_delta() -> Optional[float]:
    """Δамплитуды относительно 7.83 Гц из локального schumann_hourly.json."""
    try:
        p = ROOT / "schumann_hourly.json"
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        series = data.get("series") or data.get("data") or []
        if isinstance(series, list) and series:
            last = series[-1]
            if isinstance(last, dict):
                amp = last.get("amp") or last.get("amplitude") or last.get("value")
            else:
                cand = [v for v in last if isinstance(v, (int, float))]
                amp = cand[-1] if cand else None
            if amp is not None:
                return round(float(amp) - 7.83, 2)
    except Exception:
        pass
    return None

def schumann_status(delta: Optional[float]) -> str:
    if delta is None:
        return "—"
    if abs(delta) < 0.3:
        return "baseline"
    return "above baseline" if delta > 0 else "below baseline"

# ---------------- Earth live ----------------

def strongest_quake_24h():
    urls = [
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/6.0_day.geojson",
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson",
    ]
    for url in urls:
        try:
            feats = _get_json(url).get("features", [])
            if not feats:
                continue
            top = max(feats, key=lambda f: f["properties"]["mag"] or 0)
            p = top["properties"]
            mag = round(p["mag"], 1) if p.get("mag") is not None else None
            place = p.get("place", "—")
            depth_km = round(top["geometry"]["coordinates"][2], 1) if top.get("geometry") else None
            t_utc = dt.datetime.utcfromtimestamp(p["time"]/1000.0).strftime("%H:%M") if p.get("time") else ""
            return mag, place, depth_km, t_utc
        except Exception:
            continue
    return None, None, None, ""

def openmeteo_hottest_coldest_today():
    hottest = None
    coldest = None

    def fetch_daily(lat, lon):
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min",
            "past_days": 0, "forecast_days": 1,
            "timezone": "UTC"
        }
        return _get_json(url, params=params).get("daily", {})

    for name, la, lo in HOT_CITIES:
        try:
            d = fetch_daily(la, lo)
            mx = d.get("temperature_2m_max", [])
            if mx:
                loc_max = max(mx)
                if (hottest is None) or (loc_max > hottest["temp"]):
                    hottest = {"place": name, "temp": round(loc_max)}
        except Exception:
            continue

    for name, la, lo in COLD_SPOTS:
        try:
            d = fetch_daily(la, lo)
            mn = d.get("temperature_2m_min", [])
            if mn:
                loc_min = min(mn)
                if (coldest is None) or (loc_min < coldest["temp"]):
                    coldest = {"place": name, "temp": round(loc_min)}
        except Exception:
            continue

    return hottest, coldest

def sun_tidbit_today():
    """Берём рассвет для Reykjavik (как в твоих примерах)."""
    try:
        loc = LocationInfo("Reykjavik, IS", "", "UTC", 64.1466, -21.9426)
        s = sun(loc.observer, date=dt.date.today(), tzinfo=UTC)
        return "Sunrise", "Reykjavik, IS", s["sunrise"].strftime("%H:%M")
    except Exception:
        return "Sunrise", "Reykjavik, IS", dt.datetime.utcnow().strftime("%H:%M")

# ---------------- Money / Tip ----------------

def fx_line_today() -> str:
    try:
        fx = fetch_rates("USD", ["EUR", "CNY", "JPY", "INR", "IDR"])
        return format_line(fx, order=["USD","EUR","CNY","JPY","INR","IDR"])
    except Exception:
        return "USD 1.0000 (+0.00%)"

def pick_tip_text(today: dt.date) -> str:
    if not VIBE_TIPS:
        return "Sip water and take 10 slow breaths."
    idx = today.toordinal() % len(VIBE_TIPS)
    return VIBE_TIPS[idx]

# ---------------- Main ----------------

def main() -> dict:
    today = dt.date.today()
    weekday = dt.datetime.utcnow().strftime("%a")

    # Cosmic
    kp = fetch_kp_now()
    sw_speed, sw_dens = fetch_solar_wind()
    sch_delta = read_schumann_amp_delta()

    # Earth
    hot, cold = openmeteo_hottest_coldest_today()
    qmag, qplace, qdepth, qtime = strongest_quake_24h()
    sun_label, sun_place, sun_time = sun_tidbit_today()

    # Money + Tip
    fx_line = fx_line_today()
    tip_txt = pick_tip_text(today)

    # ---------- payload expected by daily_en.j2 ----------
    out = {
        "DATE": today.isoformat(),
        "WEEKDAY": weekday,

        # Cosmic Weather (names as in template)
        "KP": f"{kp:.2f}" if isinstance(kp, (int, float)) else "—",
        "KP_SHORT": f"{kp:.1f}" if isinstance(kp, (int, float)) else "—",
        "KP_TREND_EMOJI": "—",                  # без ряда тренда
        "KP_NOTE": kp_note(kp),

        "SCHUMANN_STATUS": schumann_status(sch_delta),
        "SCHUMANN_AMP": (f"{sch_delta:+.2f}" if isinstance(sch_delta, (int, float)) else "—"),

        "SOLAR_WIND_SPEED": f"{int(sw_speed)}" if isinstance(sw_speed, (int, float)) else "—",
        "SOLAR_WIND_DENSITY": f"{int(sw_dens)}" if isinstance(sw_dens, (int, float)) else "—",
        "SOLAR_NOTE": solar_note(sw_speed, sw_dens),

        # Earth Live
        "HOTTEST_PLACE": (hot or {}).get("place", "—"),
        "HOTTEST_TEMP":  (hot or {}).get("temp", "—"),
        "COLDEST_PLACE": (cold or {}).get("place", "—"),
        "COLDEST_TEMP":  (cold or {}).get("temp", "—"),
        "QUAKE_MAG":     qmag or "—",
        "QUAKE_REGION":  qplace or "—",
        "QUAKE_DEPTH":   (f"{qdepth:.1f}" if isinstance(qdepth, (int, float)) else "—"),
        "QUAKE_TIME":    qtime or "",

        "SUN_TIDBIT_LABEL": sun_label,
        "SUN_TIDBIT_PLACE": sun_place,
        "SUN_TIDBIT_TIME":  sun_time,

        # Money
        "fx_line": fx_line,

        # Vibe Tip line in template
        "VIBE_EMOJI": kp_badge(kp),
        "TIP_TEXT": tip_txt,
    }
    return out


if __name__ == "__main__":
    try:
        data = main()
        if not isinstance(data, dict):
            raise TypeError(f"main() returned {type(data).__name__}, expected dict")
    except Exception as e:
        print(f"[daily][ERROR] main() failed: {e}")
        data = {
            "DATE": dt.date.today().isoformat(),
            "WEEKDAY": dt.datetime.utcnow().strftime("%a"),
            "KP": "—", "KP_SHORT": "—", "KP_TREND_EMOJI": "—", "KP_NOTE": "",
            "SCHUMANN_STATUS": "—", "SCHUMANN_AMP": "—",
            "SOLAR_WIND_SPEED": "—", "SOLAR_WIND_DENSITY": "—", "SOLAR_NOTE": "",
            "HOTTEST_PLACE": "—", "HOTTEST_TEMP": "—",
            "COLDEST_PLACE": "—", "COLDEST_TEMP": "—",
            "QUAKE_MAG": "—", "QUAKE_REGION": "—", "QUAKE_DEPTH": "—", "QUAKE_TIME": "",
            "SUN_TIDBIT_LABEL": "Sunrise", "SUN_TIDBIT_PLACE": "Reykjavik, IS",
            "SUN_TIDBIT_TIME": dt.datetime.utcnow().strftime("%H:%M"),
            "fx_line": "USD 1.0000 (+0.00%)",
            "VIBE_EMOJI": "", "TIP_TEXT": "Keep plans light; tune into your body.",
        }
    try:
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[daily] wrote {OUT} ({OUT.stat().st_size} bytes)")
    except Exception as e:
        print(f"[daily][FATAL] failed to write {OUT}: {e}")