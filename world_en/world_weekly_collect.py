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

# ---------------- flags & formatting ----------------

CITY_TO_CC = {
    "Doha": "QA", "Kuwait City": "KW", "Phoenix": "US", "Dubai": "AE",
    "Jazan": "SA", "Ushuaia": "AR", "Reykjavik": "IS", "Vostok": "AQ",
    "Dome A": "AQ", "Yakutsk": "RU", "Oymyakon": "RU", "Verkhoyansk": "RU",
}

COUNTRY_TO_CC = {
    "Japan":"JP","Philippines":"PH","Chile":"CL","Mexico":"MX","Russia":"RU",
    "Indonesia":"ID","United States":"US","Argentina":"AR","Iceland":"IS",
    "China":"CN","Papua New Guinea":"PG","New Zealand":"NZ","Vanuatu":"VU",
    "Peru":"PE","Tonga":"TO","Italy":"IT","Greece":"GR","Turkey":"TR",
    "Qatar":"QA","Kuwait":"KW","Antarctica":"AQ"
}

THIN_MINUS = "\u2212"

def _country_flag(cc: str) -> str:
    if not cc or len(cc) != 2: return ""
    base = 0x1F1E6
    a, b = ord(cc[0].upper())-65, ord(cc[1].upper())-65
    if not (0 <= a < 26 and 0 <= b < 26): return ""
    return chr(base+a) + chr(base+b)

def place_with_flag(place: str) -> str:
    """'City, CC' → добавляет 🇨🇨. Или пытается по CITY_TO_CC/COUNTRY_TO_CC."""
    if not place: return "—"
    s = place.strip()
    # 1) явный ISO2
    m = re.search(r",\s*([A-Z]{2})$", s)
    if m:
        fl = _country_flag(m.group(1))
        return f"{s} {fl}".strip()
    # 2) по базе городов
    cc = CITY_TO_CC.get(s.split(",")[0].strip())
    if cc:
        return f"{s} {_country_flag(cc)}".strip()
    # 3) по стране после последней запятой
    parts = [p.strip() for p in s.split(",")]
    if len(parts) >= 2:
        cc = COUNTRY_TO_CC.get(parts[-1])
        if cc:
            return f"{s} {_country_flag(cc)}".strip()
    return s

def append_flag_if_country_at_end(region: str) -> str:
    """USGS place: '29 km W of El Hoyo, Argentina' → +🇦🇷 если страна известна."""
    if not region: return "—"
    parts = [p.strip() for p in region.split(",")]
    if len(parts) >= 2:
        country = parts[-1]
        cc = COUNTRY_TO_CC.get(country)
        if cc:
            return f"{region} {_country_flag(cc)}"
    return region

def fmt_temp_c(v) -> str:
    """'−5°C' с тонким минусом, либо '5°C'."""
    if v is None:
        return "—"
    try:
        n = int(round(float(v)))
    except Exception:
        return str(v)
    if n < 0:
        return f"{THIN_MINUS}{abs(n)}°C"
    return f"{n}°C"

def kp_level_emoji(kp_val) -> str:
    try: k = float(kp_val or 0)
    except: k = 0.0
    if k >= 7: return "🔴"
    if k >= 5: return "🟠"
    if k >= 3: return "🟡"
    return "🟢"

# ---------------- fetch helpers ----------------

def _get_json(url, params=None, timeout=25):
    r = requests.get(url, params=params or {}, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.json()

def strongest_quake_week():
    """
    Возвращает (mag, region, depth_km, time_utc).
    Берём максимальное M из weekly-ленты USGS.
    """
    urls = [
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/6.0_week.geojson",
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
    ]
    for url in urls:
        try:
            feats = _get_json(url).get("features", [])
            if not feats: 
                continue
            top = max(feats, key=lambda f: (f["properties"]["mag"] or 0))
            mag = round(top["properties"]["mag"], 1)
            region = top["properties"]["place"] or ""
            depth_km = round(top["geometry"]["coordinates"][2]) if top.get("geometry") else None
            t_utc = dt.datetime.utcfromtimestamp(top["properties"]["time"]/1000).strftime("%H:%M")
            return mag, region, depth_km, t_utc
        except Exception:
            continue
    return None, None, None, None

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
    try:
        txt = requests.get(
            "https://services.swpc.noaa.gov/text/3-day-geomag-forecast.txt",
            timeout=25, headers=HEADERS
        ).text
        lines = [ln for ln in txt.splitlines() if "kp" in ln.lower()]
        import re
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
    """Формат: Wed 09–12 UTC (low Kp) 🟢"""
    if not vals:
        return "Wed 09–12 UTC (low Kp) 🟢"
    idx = min(range(len(vals)), key=lambda i: vals[i])
    day = (dt.datetime.utcnow().date() + dt.timedelta(days=idx+1)).strftime("%a")
    emoji = kp_level_emoji(vals[idx])
    return f"{day} 09–12 UTC (low Kp) {emoji}"

def reykjavik_sunset_today():
    try:
        loc = LocationInfo("Reykjavik", "", "UTC", 64.1466, -21.9426)
        s = sun(loc.observer, date=dt.date.today(), tzinfo=UTC)
        return s["sunset"].strftime("%H:%M")
    except Exception:
        return dt.datetime.utcnow().strftime("%H:%M")

# ---------------- YouTube: top short 7d ----------------

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

# ---------------- main ----------------

def main():
    today = dt.date.today()
    week_start = (today - dt.timedelta(days=today.weekday())).isoformat()
    week_end = today.isoformat()

    # 1) Землетрясение недели — с глубиной и временем
    mag, region, depth_km, time_utc = strongest_quake_week()

    # 2) Экстремумы недели
    hot, cold = openmeteo_week_extremes()

    # 3) Kp outlook + calm window
    kp_note, kp_vals = kp_outlook_3d()
    calm_win = calm_window_from_kp(kp_vals)
    kp_emoji_week = kp_level_emoji(min(kp_vals) if kp_vals else 2.5)

    # 4) Фаза на конец след. недели
    next_week_end = today + dt.timedelta(days=7)
    p = moon.phase(next_week_end)
    if p == 0: next_moon_phase = "New Moon"
    elif 0 < p < 7: next_moon_phase = "Waxing Crescent"
    elif p == 7: next_moon_phase = "First Quarter"
    elif 7 < p < 15: next_moon_phase = "Waxing Gibbous"
    elif p == 15: next_moon_phase = "Full Moon"
    elif 15 < p < 22: next_moon_phase = "Waning Gibbous"
    elif p == 22: next_moon_phase = "Last Quarter"
    elif 22 < p < 29: next_moon_phase = "Waning Crescent"
    else: next_moon_phase = "New Moon"

    # 5) Валюты
    fx = fetch_rates("USD", ["EUR","CNY","JPY","INR","IDR"])
    fx_line_week = format_line(fx, order=["USD","EUR","CNY","JPY","INR","IDR"])
    # две строки (majors / EM)
    parts = fx_line_week.split(" • ")
    line1 = [seg for seg in parts if any(x in seg for x in ["USD","EUR","CNY","JPY"])]
    line2 = [seg for seg in parts if any(x in seg for x in ["INR","IDR"])]
    fx_line_week_line1 = " • ".join(line1) if line1 else fx_line_week
    fx_line_week_line2 = " • ".join(line2) if line2 else "—"

    # 6) YouTube
    nb = top_short_7d() or {
        "title": "Nature Break",
        "snippet": "Short calm from Miss Relax",
        "youtube_url": (random.choice(FALLBACK_NATURE_LIST) if FALLBACK_NATURE_LIST else "https://youtube.com/@misserrelax"),
        "thumb": None,
        "source": "fallback"
    }

    # Sun highlight (один флаг — прямо здесь)
    sun_place_flagged = place_with_flag("Reykjavik, IS")
    sun_time = reykjavik_sunset_today()

    # Форматированные температуры (тонкий минус)
    hot_place_flagged = place_with_flag((hot or {}).get("place","—"))
    cold_place_flagged = place_with_flag((cold or {}).get("place","—"))
    hot_temp_fmt  = fmt_temp_c((hot or {}).get("temp"))
    cold_temp_fmt = fmt_temp_c((cold or {}).get("temp"))

    # Землетрясение с флагом
    quake_region_flagged = append_flag_if_country_at_end(region or "")

    out = {
        "WEEK_START": week_start,
        "WEEK_END": week_end,

        # Quake (расширено)
        "TOP_QUAKE_MAG": mag or "—",
        "TOP_QUAKE_REGION": region or "—",
        "TOP_QUAKE_REGION_FLAGGED": quake_region_flagged,
        "TOP_QUAKE_DEPTH": depth_km if depth_km is not None else "—",
        "TOP_QUAKE_TIME_UTC": time_utc or "—",

        # Температурные экстремумы (и формат)
        "HOTTEST_WEEK_PLACE": (hot or {}).get("place","—"),
        "HOTTEST_WEEK":       (hot or {}).get("temp","—"),
        "COLDEST_WEEK_PLACE": (cold or {}).get("place","—"),
        "COLDEST_WEEK":       (cold or {}).get("temp","—"),

        "HOTTEST_WEEK_PLACE_FLAGGED": hot_place_flagged,
        "COLDEST_WEEK_PLACE_FLAGGED": cold_place_flagged,
        "HOTTEST_WEEK_FMT": hot_temp_fmt,
        "COLDEST_WEEK_FMT": cold_temp_fmt,

        # Calm window + kp
        "CALM_WINDOW_UTC": calm_win,
        "KP_EMOJI_WEEK": kp_emoji_week,

        # Sun highlight (сразу с флагом)
        "SUN_HIGHLIGHT_PLACE": sun_place_flagged,
        "SUN_HIGHLIGHT_TIME": sun_time,

        "NEXT_MOON_PHASE": next_moon_phase,
        "NEXT_KP_NOTE": kp_note,

        # FX
        "fx_line_week": fx_line_week,
        "fx_line_week_line1": fx_line_week_line1,
        "fx_line_week_line2": fx_line_week_line2,

        # Nature Break
        "TOP_NATURE_TITLE": nb["title"],
        "TOP_NATURE_SNIPPET": nb["snippet"],
        "TOP_NATURE_URL": nb["youtube_url"],
        "TOP_NATURE_THUMB": nb.get("thumb"),
    }

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    import random
    main()
