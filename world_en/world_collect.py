#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import json, random, datetime as dt
from pathlib import Path
import requests
from pytz import UTC
from astral import LocationInfo
from astral.sun import sun

from world_en.settings_world_en import (
    HOT_CITIES, COLD_SPOTS, SUN_CITIES, VIBE_TIPS,
    YT_API_KEY, YT_CHANNEL_ID, YOUTUBE_PLAYLIST_IDS, FALLBACK_NATURE_LIST
)
from world_en.fx_intl import fetch_rates, format_line  # <— добавлено

ROOT = Path(__file__).resolve().parents[1]

def get_kp_and_solar():
    kp_url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
    r = requests.get(kp_url, timeout=20); r.raise_for_status()
    rows = r.json()
    last = rows[-1]
    kp = float(last[1])
    ref = rows[-4] if len(rows) >= 4 else last
    trend = "stable"
    if float(last[1]) > float(ref[1]): trend = "up"
    if float(last[1]) < float(ref[1]): trend = "down"

    sw = requests.get(
        "https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json",
        timeout=20
    ); sw.raise_for_status()
    sw_rows = sw.json()
    den = float(sw_rows[-1][1]) if sw_rows[-1][1] not in ("", None) else None
    spd = float(sw_rows[-1][2]) if sw_rows[-1][2] not in ("", None) else None
    return kp, trend, spd, den

def get_schumann_amp():
    p = ROOT / "schumann_hourly.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        last = data[-1] if isinstance(data, list) else data
        amp = last.get("amp") or last.get("h7_amp") or None
        status = "baseline" if not amp or amp < 1.5 else ("elevated" if amp < 3 else "spike")
        return amp, status
    except Exception:
        return None, "n/a"

def meteo_current_temp(lat, lon):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m"
    r = requests.get(url, timeout=20); r.raise_for_status()
    return float(r.json()["current"]["temperature_2m"])

def get_extremes():
    hottest = None
    for name, la, lo in HOT_CITIES:
        try:
            t = meteo_current_temp(la, lo)
            if not hottest or t > hottest["temp_c"]:
                hottest = {"place": name, "temp_c": round(t)}
        except Exception:
            continue
    coldest = None
    for name, la, lo in COLD_SPOTS:
        try:
            t = meteo_current_temp(la, lo)
            if not coldest or t < coldest["temp_c"]:
                coldest = {"place": name, "temp_c": round(t)}
        except Exception:
            continue
    return hottest, coldest

def get_strongest_quake():
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
    r = requests.get(url, timeout=20); r.raise_for_status()
    feats = r.json()["features"]
    if not feats: return None
    top = max(feats, key=lambda f: f["properties"]["mag"] or 0)
    mag = round(top["properties"]["mag"], 1)
    region = top["properties"]["place"]
    depth = round(top["geometry"]["coordinates"][2])
    t = dt.datetime.fromtimestamp(top["properties"]["time"]/1000, tz=UTC).strftime("%H:%M")
    return {"mag": mag, "region": region, "depth_km": depth, "time_utc": t}

def get_sunlight_tidbit():
    today = dt.date.today()
    earliest = None
    latest = None
    for name, la, lo in SUN_CITIES:
        loc = LocationInfo(name, "", "UTC", la, lo)
        s = sun(loc.observer, date=today, tzinfo=UTC)
        sr = s["sunrise"].strftime("%H:%M")
        ss = s["sunset"].strftime("%H:%M")
        if not earliest or sr < earliest["time_utc"]:
            earliest = {"label":"Earliest sunrise", "place": name, "time_utc": sr}
        if not latest or ss > latest["time_utc"]:
            latest = {"label":"Latest sunset", "place": name, "time_utc": ss}
    return random.choice([earliest, latest])

def pick_nature_break():
    if YT_API_KEY and YT_CHANNEL_ID:
        try:
            search = requests.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={
                    "key": YT_API_KEY, "channelId": YT_CHANNEL_ID,
                    "part":"id", "maxResults": 25, "order":"date", "type":"video"
                }, timeout=20
            ).json()
            ids = [it["id"]["videoId"] for it in search.get("items", [])]
            if ids:
                stats = requests.get(
                    "https://www.googleapis.com/youtube/v3/videos",
                    params={"key":YT_API_KEY, "id":",".join(ids), "part":"snippet,statistics,contentDetails"},
                    timeout=20
                ).json()["items"]
                def is_short(item):
                    dur = item["contentDetails"]["duration"]
                    return any(x in dur for x in ("PT0M", "PT1M"))
                shorts = [v for v in stats if is_short(v)] or stats
                top = max(shorts, key=lambda v:int(v["statistics"].get("viewCount","0")))
                return {
                    "title": top["snippet"]["title"],
                    "snippet": "60 seconds of calm",
                    "youtube_url": f"https://youtu.be/{top['id']}"
                }
        except Exception:
            pass
    if FALLBACK_NATURE_LIST:
        import random
        url = random.choice(FALLBACK_NATURE_LIST)
        return {"title": "Nature Break", "snippet": "Short calm from Miss Relax", "youtube_url": url}
    return {"title": "Nature Break", "snippet": "Short calm from Miss Relax", "youtube_url": "https://youtube.com/@misserrelax"}

def main():
    # --- сбор ---
    kp, trend, sw_speed, sw_den = get_kp_and_solar()
    amp, sch_status = get_schumann_amp()
    hottest, coldest = get_extremes()
    quake = get_strongest_quake()
    sun_tidbit = get_sunlight_tidbit()

    # FX через fx_intl
    fx_data = fetch_rates("USD", ["EUR","CNY","JPY"])
    fx_line = format_line(fx_data, order=["USD","EUR","CNY","JPY"])

    tip = random.choice(VIBE_TIPS)
    nature = pick_nature_break()

    # --- вложенная структура (как раньше) ---
    out = {
        "date_utc": dt.date.today().isoformat(),
        "weekday_en": dt.datetime.utcnow().strftime("%a"),
        "cosmic": {
            "kp": kp, "kp_trend": trend,
            "schumann_amp": amp, "schumann_status": sch_status,
            "solar_wind_speed_kms": sw_speed, "solar_wind_density_cmc": sw_den,
            "notes": {
                "kp_note": "calm to moderate" if kp < 5 else "stormy window",
                "solar_note": "gentle stream" if (sw_speed or 0) < 550 else "fast stream",
            }
        },
        "earth": {
            "hottest": hottest,
            "coldest": coldest,
            "strongest_quake": quake,
            "sun_tidbit": sun_tidbit
        },
        "fx": fx_data,                 # <-- кладём полный объект
        "fx_line": fx_line,            # <-- и готовую строку
        "tips": [tip],
        "nature_break": nature
    }

    # --- плоские поля под шаблон daily_en.j2 ---
    trend_emoji = {"up":"↗︎","down":"↘︎","stable":"→"}.get(trend, "→")
    flat = {
        "WEEKDAY": out["weekday_en"],
        "DATE": out["date_utc"],
        "KP": kp,
        "KP_TREND_EMOJI": trend_emoji,
        "KP_NOTE": out["cosmic"]["notes"]["kp_note"],
        "SCHUMANN_STATUS": sch_status,
        "SCHUMANN_AMP": (None if amp is None else amp),
        "SOLAR_WIND_SPEED": sw_speed or "—",
        "SOLAR_WIND_DENSITY": sw_den or "—",
        "SOLAR_NOTE": out["cosmic"]["notes"]["solar_note"],
        "HOTTEST_PLACE": (hottest or {}).get("place","—"),
        "HOTTEST_TEMP": (hottest or {}).get("temp_c","—"),
        "COLDEST_PLACE": (coldest or {}).get("place","—"),
        "COLDEST_TEMP": (coldest or {}).get("temp_c","—"),
        "QUAKE_MAG": (quake or {}).get("mag","—"),
        "QUAKE_REGION": (quake or {}).get("region","—"),
        "QUAKE_DEPTH": (quake or {}).get("depth_km","—"),
        "SUN_TIDBIT_LABEL": (sun_tidbit or {}).get("label","Sun highlight"),
        "SUN_TIDBIT_PLACE": (sun_tidbit or {}).get("place","—"),
        "SUN_TIDBIT_TIME": (sun_tidbit or {}).get("time_utc","—"),
        "TIP_TEXT": tip,
        "NATURE_TITLE": nature["title"],
        "NATURE_SNIPPET": nature["snippet"],
        "NATURE_URL": nature["youtube_url"],
        # На случай, если где-то ещё используешь старые USD/EUR/CNY:
        "USD": "1.00", "USD_DELTA": "—",
        "EUR": f"{out['fx']['items']['EUR']['rate']:.2f}" if out['fx']['items']['EUR']['rate'] is not None else "—",
        "EUR_DELTA": (lambda x: f"{x:+.2f}%" if x is not None else "—")(out['fx']['items']['EUR']['chg_pct']),
        "CNY": f"{out['fx']['items']['CNY']['rate']:.2f}" if out['fx']['items']['CNY']['rate'] is not None else "—",
        "CNY_DELTA": (lambda x: f"{x:+.2f}%" if x is not None else "—")(out['fx']['items']['CNY']['chg_pct']),
        "fx_line": fx_line  # на всякий случай дублируем в плоском виде
    }

    out.update(flat)
    (Path(__file__).parent / "daily.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

if __name__ == "__main__":
    main()
