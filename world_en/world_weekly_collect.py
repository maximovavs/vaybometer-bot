#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, json, datetime as dt
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

import requests
from astral import moon
from astral.sun import sun
from astral import LocationInfo
from pytz import UTC

from world_en.fx_intl import fetch_rates, format_line
from world_en.settings_world_en import (
    HOT_CITIES, COLD_SPOTS,
    YT_API_KEY, YT_CHANNEL_ID, FALLBACK_NATURE_LIST
)

OUT = Path(__file__).parent / "weekly.json"
LOG_EXTREMES = Path(__file__).parent / "logs" / "extremes.jsonl"

HEADERS = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Accept": "application/json,text/plain",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# ---------------- helpers ----------------

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
            note = top["properties"].get("type", "")
            return mag, region, note
        except Exception:
            continue
    return None, None, None

def _read_extremes_from_log(days_back=7):
    """Читает world_en/logs/extremes.jsonl за N суток и отдаёт глобальные hot/cold."""
    if not LOG_EXTREMES.exists():
        return None, None
    try:
        cutoff = dt.date.today() - dt.timedelta(days=days_back-1)
        hottest = None   # {"place":..., "temp":...}
        coldest = None
        with LOG_EXTREMES.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                d = rec.get("date")
                if not d:
                    continue
                try:
                    d_date = dt.date.fromisoformat(d)
                except Exception:
                    continue
                if d_date < cutoff:
                    continue
                h_t = rec.get("hottest_temp")
                c_t = rec.get("coldest_temp")
                if isinstance(h_t, (int, float)):
                    if (hottest is None) or (h_t > hottest["temp"]):
                        hottest = {"place": rec.get("hottest_place", "—"), "temp": round(h_t)}
                if isinstance(c_t, (int, float)):
                    if (coldest is None) or (c_t < coldest["temp"]):
                        coldest = {"place": rec.get("coldest_place", "—"), "temp": round(c_t)}
        return hottest, coldest
    except Exception:
        return None, None

def openmeteo_week_extremes():
    """Фолбэк: считаем экстремумы за 7 суток из Open-Meteо по спискам городов."""
    hottest = None
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

def kp_outlook_3d():
    """Парсинг SWPC 3-day geomag forecast. Возвращает ('3 / 2 / 4', [3,2,4])."""
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

# --------------- YouTube weekly favorite ---------------

def _yt_is_short_duration(iso_dur: str) -> bool:
    if not iso_dur:
        return False
    m = re.fullmatch(r"PT(?:(\d+)M)?(?:(\d+)S)?", iso_dur)
    if not m:
        return False
    minutes = int(m.group(1) or 0)
    seconds = int(m.group(2) or 0)
    return minutes*60 + seconds <= 60

def _md_escape(s: str) -> str:
    return (s.replace("\\", "\\\\")
             .replace("[","\\[").replace("]","\\]")
             .replace("(","\\(").replace(")","\\)")
             .replace("_","\\_").replace("*","\\*"))

def _weekly_top_short(days_window: int = 7):
    """
    Самый просматриваемый шорт за N дней:
    - берём последние 50 видео канала (без publishedAfter),
    - локально фильтруем по publishedAt >= cutoff,
    - среди коротких (≤60с) выбираем максимум по viewCount.
    """
    api = os.getenv("YT_API_KEY", YT_API_KEY)
    ch  = os.getenv("YT_CHANNEL_ID", YT_CHANNEL_ID)
    if not (api and ch):
        return None, None, "fallback"

    try:
        search = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "key": api, "channelId": ch, "part": "id,snippet",
                "maxResults": 50, "order": "date", "type": "video"
            }, timeout=20
        ).json()
        items = search.get("items", [])
        cutoff = dt.datetime.utcnow() - dt.timedelta(days=days_window)

        ids = []
        for it in items:
            vid = it.get("id", {}).get("videoId")
            published_at = it.get("snippet", {}).get("publishedAt")
            if not vid or not published_at:
                continue
            try:
                pub = dt.datetime.fromisoformat(published_at.replace("Z", "+00:00"))
            except Exception:
                continue
            if pub >= cutoff:
                ids.append(vid)

        if not ids:
            ids = [it.get("id", {}).get("videoId") for it in items if it.get("id", {}).get("videoId")]
            ids = ids[:20]

        if not ids:
            return None, None, "fallback"

        stats = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"key": api, "id": ",".join(ids), "part": "snippet,statistics,contentDetails"},
            timeout=20
        ).json().get("items", [])
        shorts = [v for v in stats if _yt_is_short_duration(v["contentDetails"]["duration"])] or stats
        if not shorts:
            return None, None, "fallback"

        top = max(shorts, key=lambda v: int(v["statistics"].get("viewCount", "0")))
        title = _md_escape(top["snippet"]["title"])
        url = f"https://youtu.be/{top['id']}?utm_source=telegram&utm_medium=worldvibemeter&utm_campaign=weekly_favorite"
        return title, url, "api"
    except Exception:
        return None, None, "fallback"

# ---------------- main ----------------

def main():
    today = dt.date.today()
    week_start = (today - dt.timedelta(days=today.weekday())).isoformat()
    week_end = today.isoformat()

    # 1) Землетрясение недели
    mag, region, note = strongest_quake_week()

    # 2) Экстремумы недели: сначала из логов daily, затем фолбэк на open-meteo
    hot, cold = _read_extremes_from_log(days_back=7)
    if hot is None and cold is None:
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

    # 6) Community favorite (топ-шорт за 7 дней)
    top_title, top_url, top_src = _weekly_top_short(7)

    out = {
        "WEEK_START": week_start,
        "WEEK_END": week_end,
        "TOP_QUAKE_MAG": mag or "—",
        "TOP_QUAKE_REGION": region or "—",
        "TOP_QUAKE_NOTE": note or "",
        "HOTTEST_WEEK_PLACE": (hot or {}).get("place", "—"),
        "HOTTEST_WEEK": (hot or {}).get("temp", "—"),
        "COLDEST_WEEK_PLACE": (cold or {}).get("place", "—"),
        "COLDEST_WEEK": (cold or {}).get("temp", "—"),
        "CALM_WINDOW_UTC": calm_win,
        "SUN_HIGHLIGHT_PLACE": "Reykjavik, IS",
        "SUN_HIGHLIGHT_TIME": reykjavik_sunset_today(),
        "TOP_NATURE_TITLE": top_title or "Nature Break",
        "TOP_NATURE_URL": top_url or (FALLBACK_NATURE_LIST[0] if FALLBACK_NATURE_LIST else "https://youtube.com/@misserrelax"),
        "TOP_NATURE_SOURCE": top_src,
        "fx_line_week": fx_line_week,
        "NEXT_MOON_PHASE": next_moon_phase,
        "NEXT_KP_NOTE": kp_note
    }

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
