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
    YT_API_KEY, YT_CHANNEL_ID, YOUTUBE_PLAYLIST_IDS, FALLBACK_NATURE_LIST
)

OUT = Path(__file__).parent / "weekly.json"

HEADERS = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Accept": "application/json,text/plain",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# ---- flags --------------------------------------------------------

CITY_TO_CC = {
    "Doha": "QA", "Kuwait City": "KW", "Phoenix": "US", "Dubai": "AE",
    "Jazan": "SA", "Ushuaia": "AR", "Reykjavik": "IS", "Vostok": "AQ",
    "Dome A": "AQ", "Yakutsk": "RU", "Oymyakon": "RU", "Verkhoyansk": "RU",
}

def _country_flag(cc: str) -> str:
    if not cc or len(cc) != 2: return ""
    base = 0x1F1E6
    a, b = ord(cc[0].upper())-65, ord(cc[1].upper())-65
    if not (0 <= a < 26 and 0 <= b < 26): return ""
    return chr(base+a) + chr(base+b)

def _with_flag(place: str) -> str:
    if not place: return "—"
    place = place.strip()
    m = re.search(r",\s*([A-Z]{2})$", place)
    cc = m.group(1) if m else CITY_TO_CC.get(place.split(",")[0].strip())
    fl = _country_flag(cc) if cc else ""
    return f"{place} {fl}".strip()

# ---- helpers ------------------------------------------------------

# --- tiny helpers for flags/format ---
def _country_flag(cc: str) -> str:
    if not cc or len(cc) != 2: return ""
    base = 0x1F1E6
    a, b = ord(cc[0].upper())-65, ord(cc[1].upper())-65
    if not (0 <= a <= 25 and 0 <= b <= 25): return ""
    return chr(base+a) + chr(base+b)

COUNTRY_TO_CC = {
    "Japan":"JP","Philippines":"PH","Chile":"CL","Mexico":"MX","Russia":"RU",
    "Indonesia":"ID","United States":"US","Argentina":"AR","Iceland":"IS",
    "China":"CN","Papua New Guinea":"PG","New Zealand":"NZ","Vanuatu":"VU",
    "Peru":"PE","Tonga":"TO","Italy":"IT","Greece":"GR","Turkey":"TR",
    # при нужде дополним
}

def _append_flag_to_place(s: str) -> str:
    if not s: return "—"
    # ищем страну после последней запятой
    parts = [p.strip() for p in s.split(",")]
    if len(parts) >= 2:
        country = parts[-1]
        cc = COUNTRY_TO_CC.get(country)
        if cc: return f"{s} {_country_flag(cc)}"
    return s

def kp_level_emoji(kp_val: float | int | None) -> str:
    try: k = float(kp_val or 0)
    except: k = 0.0
    if k >= 7: return "🔴"
    if k >= 5: return "🟠"
    if k >= 3: return "🟡"
    return "🟢"

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
    """Экстремумы последних 7 суток по нашему списку мест."""
    def fetch_daily(lat, lon):
        return _get_json(
            "https://api.open-meteo.com/v1/forecast",
            params={
                "latitude": lat, "longitude": lon,
                "daily": "temperature_2m_max,temperature_2m_min",
                "past_days": 7, "forecast_days": 1, "timezone": "UTC"
            }
        ).get("daily", {})

    hottest = None
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

    coldest = None
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
    """Возвращает строку прогноза Kp на 3 дня и список чисел."""
    import re
    try:
        txt = requests.get(
            "https://services.swpc.noaa.gov/text/3-day-geomag-forecast.txt",
            timeout=25, headers=HEADERS
        ).text
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

def reykjavik_sunset_today():
    try:
        loc = LocationInfo("Reykjavik", "", "UTC", 64.1466, -21.9426)
        s = sun(loc.observer, date=dt.date.today(), tzinfo=UTC)
        return s["sunset"].strftime("%H:%M")
    except Exception:
        return dt.datetime.utcnow().strftime("%H:%M")

# ---- YouTube (top short за 7 дней + чистый заголовок) ------------

def _yt_iso_to_seconds(iso: str) -> int:
    if not iso: return 0
    m = re.fullmatch(r"^PT(?:(\d+)M)?(?:(\d+)S)?$", iso)
    return (int(m.group(1) or 0)*60 + int(m.group(2) or 0)) if m else 0

def _clean_title(t: str, limit: int = 80) -> str:
    if not t: return "Nature Break"
    t = re.sub(r"#\w+", "", t)
    t = re.sub(r"\s{2,}", " ", t).strip()
    return t if len(t) <= limit else t[:limit-1] + "…"

def _thumb_for_video(video_id: str) -> str:
    # oEmbed (лучше подтягивает превью вертикальных Shorts)
    try:
        j = _get_json("https://www.youtube.com/oembed",
                      params={"url": f"https://youtu.be/{video_id}", "format":"json"},
                      timeout=15)
        if "thumbnail_url" in j:
            return j["thumbnail_url"]
    except Exception:
        pass
    return f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg"

def top_short_7d():
    api = os.getenv("YT_API_KEY", YT_API_KEY)
    ch  = os.getenv("YT_CHANNEL_ID", YT_CHANNEL_ID)
    if not (api and ch):
        return None
    cutoff = (dt.datetime.utcnow() - dt.timedelta(days=7)).replace(microsecond=0).isoformat() + "Z"
    try:
        search = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={"key": api, "channelId": ch, "part":"id", "type":"video",
                    "order":"date", "maxResults": 50, "publishedAfter": cutoff},
            timeout=20
        ).json()
        ids = [it["id"]["videoId"] for it in search.get("items", []) if it.get("id", {}).get("videoId")]
        if not ids:
            return None
        stats = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"key": api, "id": ",".join(ids), "part":"snippet,statistics,contentDetails"},
            timeout=20
        ).json().get("items", [])
        pool = [v for v in stats if _yt_iso_to_seconds(v["contentDetails"]["duration"]) <= 60] or stats
        if not pool: return None
        top = max(pool, key=lambda v: int(v["statistics"].get("viewCount","0")))
        vid = top["id"]
        title = _clean_title(top["snippet"]["title"])
        url = f"https://youtu.be/{vid}?utm_source=telegram&utm_medium=worldvibemeter&utm_campaign=weekly_favorite"
        return {
            "title": title,
            "snippet": "Short calm from Miss Relax",
            "youtube_url": url,
            "thumb": _thumb_for_video(vid),
            "source": "api-7d"
        }
    except Exception:
        return None

# ---- main ---------------------------------------------------------

def main():
    today = dt.date.today()
    week_start = (today - dt.timedelta(days=today.weekday())).isoformat()
    week_end = today.isoformat()

    mag, region, note = strongest_quake_week()
    hot, cold = openmeteo_week_extremes()

    kp_note, kp_vals = kp_outlook_3d()
    calm_win = calm_window_from_kp(kp_vals)

    next_week_end = today + dt.timedelta(days=7)
    p = moon.phase(next_week_end)
    def phase_name(d):
        if p == 0: return "New Moon"
        if 0 < p < 7: return "Waxing Crescent"
        if p == 7: return "First Quarter"
        if 7 < p < 15: return "Waxing Gibbous"
        if p == 15: return "Full Moon"
        if 15 < p < 22: return "Waning Gibbous"
        if p == 22: return "Last Quarter"
        if 22 < p < 29: return "Waning Crescent"
        return "New Moon"
    next_moon_phase = phase_name(next_week_end)

    fx = fetch_rates("USD", ["EUR","CNY","JPY","INR","IDR"])
    fx_line_week = format_line(fx, order=["USD","EUR","CNY","JPY","INR","IDR"])

    nb = top_short_7d() or {
        "title": "Nature Break",
        "snippet": "Short calm from Miss Relax",
        "youtube_url": (random.choice(FALLBACK_NATURE_LIST) if FALLBACK_NATURE_LIST else "https://youtube.com/@misserrelax"),
        "thumb": None,
        "source": "fallback"
    }
    # quake region with flag
        out_quake_region_flagged = _append_flag_to_place(out.get("TOP_QUAKE_REGION") or "")
        
        # flags for hottest/coldest уже могли быть; если нет — добавим простую версию:
        hot_flagged = _append_flag_to_place((hot or {}).get("place","—"))
        cold_flagged = _append_flag_to_place((cold or {}).get("place","—"))
        
        # KPI emoji for the calm window day — возьмём минимум из kp_vals
        kp_vals = out.get("KP_VALS") or []  # если у тебя уже есть, иначе из функции kp_outlook_3d() верни список
        kp_emoji_week = kp_level_emoji(min(kp_vals) if kp_vals else 2.5)
        
        # FX две строки
        fxline = fx_line_week
        # разбивка: majors и Азия EM
        fx_line_week_line1 = " • ".join(seg for seg in fxline.split(" • ") if any(x in seg for x in ["EUR","CNY","JPY","USD"]))
        fx_line_week_line2 = " • ".join(seg for seg in fxline.split(" • ") if any(x in seg for x in ["INR","IDR"]))
        if not fx_line_week_line2:
            fx_line_week_line2 = "—"


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

        # те же места — но с флагом
        "HOTTEST_WEEK_PLACE_FLAGGED": _with_flag((hot or {}).get("place","—")),
        "COLDEST_WEEK_PLACE_FLAGGED": _with_flag((cold or {}).get("place","—")),

        "CALM_WINDOW_UTC": calm_win,
        "SUN_HIGHLIGHT_PLACE": "Reykjavik, IS",
        "SUN_HIGHLIGHT_TIME": reykjavik_sunset_today(),

        "NEXT_MOON_PHASE": next_moon_phase,
        "NEXT_KP_NOTE": kp_note,

        "fx_line_week": fx_line_week,

        # Nature Break
        "TOP_NATURE_TITLE": nb["title"],
        "TOP_NATURE_SNIPPET": nb["snippet"],
        "TOP_NATURE_URL": nb["youtube_url"],
        "TOP_NATURE_THUMB": nb.get("thumb"),
        
        "TOP_QUAKE_REGION_FLAGGED": out_quake_region_flagged,
        "HOTTEST_WEEK_PLACE_FLAGGED": hot_flagged,
        "COLDEST_WEEK_PLACE_FLAGGED": cold_flagged,
        "KP_EMOJI_WEEK": kp_emoji_week,
        "fx_line_week_line1": fx_line_week_line1,
        "fx_line_week_line2": fx_line_week_line2,
        }

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    import random
    main()
