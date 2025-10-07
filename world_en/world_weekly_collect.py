#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, json, datetime as dt, random
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

# ---------------- Flags / helpers ----------------

COUNTRY_TO_CC = {
    "Japan":"JP","Philippines":"PH","Chile":"CL","Mexico":"MX","Russia":"RU",
    "Indonesia":"ID","United States":"US","Argentina":"AR","Iceland":"IS",
    "China":"CN","Papua New Guinea":"PG","New Zealand":"NZ","Vanuatu":"VU",
    "Peru":"PE","Tonga":"TO","Italy":"IT","Greece":"GR","Turkey":"TR",
}

CITY_TO_CC = {
    "Doha": "QA", "Kuwait City": "KW", "Phoenix": "US", "Dubai": "AE",
    "Jazan": "SA", "Ushuaia": "AR", "Reykjavik": "IS", "Vostok": "AQ",
    "Dome A": "AQ", "Yakutsk": "RU", "Oymyakon": "RU", "Verkhoyansk": "RU",
}

def _country_flag(cc: str) -> str:
    if not cc or len(cc) != 2: return ""
    base = 0x1F1E6
    a, b = ord(cc[0].upper())-65, ord(cc[1].upper())-65
    if not (0 <= a <= 25 and 0 <= b <= 25): return ""
    return chr(base+a) + chr(base+b)

def _append_flag_to_place(s: str) -> str:
    """Пробует поставить флаг по стране (последний сегмент) или по базе CITY_TO_CC."""
    if not s: return "—"
    parts = [p.strip() for p in s.split(",")]
    cc = None
    if len(parts) >= 2:
        cc = COUNTRY_TO_CC.get(parts[-1])
    if not cc:
        cc = CITY_TO_CC.get(parts[0])
    fl = _country_flag(cc) if cc else ""
    return f"{s} {fl}".strip()

def kp_level_emoji(kp_val: float | int | None) -> str:
    try:
        k = float(kp_val or 0)
    except Exception:
        k = 0.0
    if k >= 7: return "🔴"
    if k >= 5: return "🟠"
    if k >= 3: return "🟡"
    return "🟢"

def pretty_temp(value) -> str:
    """Строка температуры с тонким минусом U+2212."""
    try:
        v = int(round(float(value)))
    except Exception:
        return "—"
    s = f"{v}"
    if s.startswith("-"):
        s = "−" + s[1:]  # заменяем на тонкий минус
    return s

def _get_json(url, params=None, timeout=25):
    r = requests.get(url, params=params or {}, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.json()

# ---------------- Data fetchers ----------------

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
                    hottest = {"place": name, "temp": loc_max}
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
                    coldest = {"place": name, "temp": loc_min}
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

def calm_window_string_from_kp(vals):
    """
    «Wed 09–12 UTC (low Kp) 🟢» — день выбираем по минимальному Kp среди ближайших 3-х.
    """
    if not vals:
        return "Wed 09–12 UTC (low Kp) 🟢", "🟢"
    idx = min(range(len(vals)), key=lambda i: vals[i])
    day = (dt.datetime.utcnow().date() + dt.timedelta(days=idx+1)).strftime("%a")
    emoji = kp_level_emoji(vals[idx])
    return f"{day} 09–12 UTC (low Kp) {emoji}", emoji

def reykjavik_sunset_today():
    try:
        loc = LocationInfo("Reykjavik", "", "UTC", 64.1466, -21.9426)
        s = sun(loc.observer, date=dt.date.today(), tzinfo=UTC)
        return s["sunset"].strftime("%H:%M")
    except Exception:
        return dt.datetime.utcnow().strftime("%H:%M")

# ---------------- YouTube (top short 7d) ----------------

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

    # 1) Землетрясение недели
    mag, region, note = strongest_quake_week()

    # 2) Экстремумы недели
    hot, cold = openmeteo_week_extremes()

    # 3) Kp outlook + calm window
    kp_note, kp_vals = kp_outlook_3d()
    calm_win_str, kp_emoji_week = calm_window_string_from_kp(kp_vals)

    # 4) Луна на конец следующей недели
    next_week_end = today + dt.timedelta(days=7)
    p = moon.phase(next_week_end)
    def phase_name(pval):
        if pval == 0: return "New Moon"
        if 0 < pval < 7: return "Waxing Crescent"
        if pval == 7: return "First Quarter"
        if 7 < pval < 15: return "Waxing Gibbous"
        if pval == 15: return "Full Moon"
        if 15 < pval < 22: return "Waning Gibbous"
        if pval == 22: return "Last Quarter"
        if 22 < pval < 29: return "Waning Crescent"
        return "New Moon"
    next_moon_phase = phase_name(p)

    # 5) Валюты (6 штук)
    fx = fetch_rates("USD", ["EUR","CNY","JPY","INR","IDR"])
    fx_line_week = format_line(fx, order=["USD","EUR","CNY","JPY","INR","IDR"])
    # разбивка на две строки: majors / Asia EM
    parts = fx_line_week.split(" • ")
    fx_line_week_line1 = " • ".join(seg for seg in parts if any(x in seg for x in ["USD","EUR","CNY","JPY"]))
    fx_line_week_line2 = " • ".join(seg for seg in parts if any(x in seg for x in ["INR","IDR"])) or "—"

    # 6) YouTube short за 7 дней
    nb = top_short_7d() or {
        "title": "Nature Break",
        "snippet": "Short calm from Miss Relax",
        "youtube_url": (random.choice(FALLBACK_NATURE_LIST) if FALLBACK_NATURE_LIST else "https://youtube.com/@misserrelax"),
        "thumb": None,
        "source": "fallback"
    }

    # Флаги и «тонкий минус» температуры
    hot_place_flag = _append_flag_to_place((hot or {}).get("place","—"))
    cold_place_flag = _append_flag_to_place((cold or {}).get("place","—"))
    hot_temp = pretty_temp((hot or {}).get("temp"))
    cold_temp = pretty_temp((cold or {}).get("temp"))

    out = {
        "WEEK_START": week_start,
        "WEEK_END": week_end,

        "TOP_QUAKE_MAG": mag or "—",
        "TOP_QUAKE_REGION": region or "—",
        "TOP_QUAKE_REGION_FLAGGED": _append_flag_to_place(region or "—"),
        "TOP_QUAKE_NOTE": note or "",

        "HOTTEST_WEEK_PLACE": (hot or {}).get("place","—"),
        "HOTTEST_WEEK": hot_temp,
        "COLDEST_WEEK_PLACE": (cold or {}).get("place","—"),
        "COLDEST_WEEK": cold_temp,

        "HOTTEST_WEEK_PLACE_FLAGGED": hot_place_flag,
        "COLDEST_WEEK_PLACE_FLAGGED": cold_place_flag,

        "CALM_WINDOW_UTC": calm_win_str,            # уже с "… UTC (low Kp) 🟢"
        "KP_EMOJI_WEEK": kp_emoji_week,             # на всякий случай отдельно
        "SUN_HIGHLIGHT_PLACE": "Reykjavik, IS",
        "SUN_HIGHLIGHT_TIME": reykjavik_sunset_today(),

        "NEXT_MOON_PHASE": next_moon_phase,
        "NEXT_KP_NOTE": kp_note,
        "KP_VALS": kp_vals,                         # вдруг пригодится

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
    main()
