#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))  # чтобы import world_en.* точно работал

import os, re
import json, random, datetime as dt
import requests
from pytz import UTC
from astral import LocationInfo
from astral.sun import sun

from world_en.settings_world_en import (
    HOT_CITIES, COLD_SPOTS, SUN_CITIES, VIBE_TIPS,
    YT_API_KEY, YT_CHANNEL_ID, YOUTUBE_PLAYLIST_IDS, FALLBACK_NATURE_LIST
)
from world_en.fx_intl import fetch_rates, format_line

ROOT = Path(__file__).resolve().parents[1]

HEADERS = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Accept": "application/json,text/plain",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# --- простая база: город/топоним → ISO2 (добавляй по мере встреч)
CITY_TO_CC = {
    "Doha": "QA",
    "Kuwait City": "KW",
    "Phoenix": "US",
    "Jazan": "SA",
    "Dubai": "AE",
    "Ushuaia": "AR",
    "Reykjavik": "IS",
    "Vostok": "AQ",
    "Dome A": "AQ",
    "Yakutsk": "RU",
    "Oymyakon": "RU",
    "Verkhoyansk": "RU",
    "Death Valley": "US",
    "Tomioka": "JP",
    # при желании пополним списком позже
}

# -------------------- утилиты --------------------

def _country_flag(cc: str) -> str:
    if not cc or len(cc) != 2: return ""
    base = 0x1F1E6
    a = ord(cc[0].upper()) - ord('A')
    b = ord(cc[1].upper()) - ord('A')
    if a < 0 or b < 0 or a > 25 or b > 25: return ""
    return chr(base + a) + chr(base + b)

def _with_flag(place: str) -> str:
    """Добавляет флаг. Сначала ищем ', CC' в конце; если нет — пробуем CITY_TO_CC по основному топониму."""
    if not place:
        return "—"
    place = place.strip()

    # 1) явный ISO2 в конце
    m = re.search(r",\s*([A-Z]{2})$", place)
    cc = m.group(1) if m else None

    # 2) попытка по базе: берём первую часть до запятой (или всё слово)
    if not cc:
        base_name = place.split(",")[0].strip()
        cc = CITY_TO_CC.get(base_name)

    fl = _country_flag(cc) if cc else ""
    return f"{place} {fl}".strip()

def kp_level_emoji(kp) -> str:
    try:
        k = float(kp or 0)
    except Exception:
        k = 0.0
    if k >= 7: return "🔴"   # strong storm
    if k >= 5: return "🟠"   # stormy window
    if k >= 3: return "🟡"   # active
    return "🟢"              # calm

# -------------------- NOAA / SWPC --------------------

def get_kp_and_solar():
    # Kp-индекс
    kp_url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
    r = requests.get(kp_url, timeout=20, headers=HEADERS); r.raise_for_status()
    rows = r.json()
    last = rows[-1]
    kp = float(last[1])

    ref = rows[-4] if len(rows) >= 4 else last
    trend = "stable"
    if float(last[1]) > float(ref[1]): trend = "up"
    if float(last[1]) < float(ref[1]): trend = "down"

    # Скорость/плотность солнечного ветра
    sw = requests.get(
        "https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json",
        timeout=20, headers=HEADERS
    ); sw.raise_for_status()
    sw_rows = sw.json()
    # header: time, density, speed, temperature
    den = sw_rows[-1][1]
    spd = sw_rows[-1][2]
    den = None if den in ("", None) else round(float(den), 2)
    spd = None if spd in ("", None) else round(float(spd), 1)
    return kp, trend, spd, den

# -------------------- Schumann --------------------

def get_schumann_amp():
    """Читает последние ~24 значения амплитуды из schumann_hourly.json и классифицирует статус."""
    p = ROOT / "schumann_hourly.json"
    if not p.exists():
        p = ROOT / "data" / "schumann_hourly.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        arr = data if isinstance(data, list) else [data]
        # берём последние 24 значения
        amps = [x.get("amp") or x.get("h7_amp") for x in arr[-24:] if isinstance(x, dict)]
        amps = [abs(a) for a in amps if isinstance(a, (int, float))]
        last = amps[-1] if amps else None
        if not amps or last is None:
            return None, "blackout"
        median = sorted(amps)[len(amps)//2]
        if last < 0.3 or len(amps) < 3:
            status = "blackout" if last < 0.3 else "baseline"
        elif last > max(3.0, median * 2.0):
            status = "spike"
        elif last > max(1.5, median * 1.2):
            status = "elevated"
        else:
            status = "baseline"
        return round(last, 2), status
    except Exception:
        return None, "n/a"

# -------------------- Температурные экстремумы --------------------

def meteo_current_temp(lat, lon):
    url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m"
    r = requests.get(url, timeout=20, headers=HEADERS); r.raise_for_status()
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

# -------------------- Землетрясения --------------------

def get_strongest_quake():
    url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
    r = requests.get(url, timeout=20, headers=HEADERS); r.raise_for_status()
    feats = r.json()["features"]
    if not feats: return None
    top = max(feats, key=lambda f: f["properties"]["mag"] or 0)
    mag = round(top["properties"]["mag"], 1)
    region = top["properties"]["place"]
    depth = round(top["geometry"]["coordinates"][2])
    t = dt.datetime.fromtimestamp(top["properties"]["time"]/1000, tz=UTC).strftime("%H:%M")
    return {"mag": mag, "region": region, "depth_km": depth, "time_utc": t}

# -------------------- Солнце: ранний восход / поздний закат --------------------

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

# -------------------- YouTube helpers --------------------

def _yt_iso_to_seconds(iso: str) -> int:
    if not iso: return 0
    m = re.fullmatch(r"^PT(?:(\d+)M)?(?:(\d+)S)?$", iso)
    if not m: return 0
    return int(m.group(1) or 0) * 60 + int(m.group(2) or 0)

def _clean_title(t: str, limit: int = 60) -> str:
    if not t: return "Nature Break"
    t = re.sub(r"#\w+", "", t).strip()
    t = re.sub(r"\s{2,}", " ", t)
    return (t if len(t) <= limit else t[:limit-1] + "…")

def _daily_top_short(hours_window: int = 48):
    """Самый просматриваемый шорт за последние hours_window часов; фолбэк на последние загрузки."""
    api = os.getenv("YT_API_KEY", YT_API_KEY)
    ch  = os.getenv("YT_CHANNEL_ID", YT_CHANNEL_ID)
    if not (api and ch):
        return None

    cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=hours_window)) \
                .replace(microsecond=0).isoformat() + "Z"

    try:
        search = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "key": api, "channelId": ch, "part": "id",
                "type": "video", "order": "date", "maxResults": 50,
                "publishedAfter": cutoff
            }, timeout=20
        ).json()
        ids = [it["id"]["videoId"] for it in search.get("items", []) if it.get("id", {}).get("videoId")]

        if not ids:
            alt = requests.get(
                "https://www.googleapis.com/youtube/v3/search",
                params={"key": api, "channelId": ch, "part": "id", "type": "video", "order": "date", "maxResults": 20},
                timeout=20
            ).json()
            ids = [it["id"]["videoId"] for it in alt.get("items", []) if it.get("id", {}).get("videoId")]
            if not ids:
                return None

        stats = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"key": api, "id": ",".join(ids), "part": "snippet,statistics,contentDetails"},
            timeout=20
        ).json().get("items", [])

        pool = [v for v in stats if _yt_iso_to_seconds(v["contentDetails"]["duration"]) <= 60] or stats
        if not pool:
            return None

        top = max(pool, key=lambda v: int(v["statistics"].get("viewCount", "0")))
        title = _clean_title(top["snippet"]["title"])
        url = f"https://youtu.be/{top['id']}?utm_source=telegram&utm_medium=worldvibemeter&utm_campaign=daily_favorite"
        return {"title": title, "snippet": "60 seconds of calm", "youtube_url": url, "source": "api-48h"}
    except Exception:
        return None

def pick_nature_break():
    # 1) Топ-шорт за 48 часов
    top = _daily_top_short(48)
    if top:
        return top

    # 2) Последние загрузки → топ по просмотрам (фолбэк)
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
                    return _yt_iso_to_seconds(item["contentDetails"]["duration"]) <= 60
                shorts = [v for v in stats if is_short(v)] or stats
                topv = max(shorts, key=lambda v:int(v["statistics"].get("viewCount","0")))
                url = f"https://youtu.be/{topv['id']}?utm_source=telegram&utm_medium=worldvibemeter&utm_campaign=nature_break"
                return {
                    "title": _clean_title(topv["snippet"]["title"]),
                    "snippet": "60 seconds of calm",
                    "youtube_url": url,
                    "source": "api-recent"
                }
        except Exception:
            pass

    # 3) Фолбэк-список
    if FALLBACK_NATURE_LIST:
        url = random.choice(FALLBACK_NATURE_LIST)
        if "utm_" not in url:
            url += ("&" if "?" in url else "?") + "utm_source=telegram&utm_medium=worldvibemeter&utm_campaign=nature_break"
        return {"title": "Nature Break", "snippet": "Short calm from Miss Relax", "youtube_url": url, "source": "fallback"}
    return {"title": "Nature Break", "snippet": "Short calm from Miss Relax", "youtube_url": "https://youtube.com/@misserrelax", "source": "none"}

# -------------------- Vibe Tip (адаптивный) --------------------

def smart_tip(kp: float, sch_status: str) -> str:
    try:
        k = float(kp or 0)
    except Exception:
        k = 0.0
    s = (sch_status or "").lower()
    if k >= 6:
        return "Grounding: 2-minute box-breathing + warm tea."
    if k >= 5:
        return "Go gentle: slower pace, extra water, one thing at a time."
    if s in ("spike", "elevated"):
        return "Short walk + deep nasal breathing for 60 sec."
    return random.choice(VIBE_TIPS)

# -------------------- Main --------------------

def main():
    # --- сбор ---
    kp, trend, sw_speed, sw_den = get_kp_and_solar()
    amp, sch_status = get_schumann_amp()
    hottest, coldest = get_extremes()
    quake = get_strongest_quake()
    sun_tidbit = get_sunlight_tidbit()

    # FX: 6 валют
    fx_data = fetch_rates("USD", ["EUR","CNY","JPY","INR","IDR"])
    fx_line = format_line(fx_data, order=["USD","EUR","CNY","JPY","INR","IDR"])

    # Адаптивный совет
    tip = smart_tip(kp, sch_status)

    # YouTube
    nature = pick_nature_break()

    # --- вложенная структура ---
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
        "fx": fx_data,
        "fx_line": fx_line,
        "tips": [tip],
        "nature_break": nature
    }

    # --- лог для weekly (экстремумы) ---
    log_dir = Path(__file__).parent / "logs"
    log_dir.mkdir(exist_ok=True)
    log = {
        "date": out["date_utc"],
        "hottest_place": (hottest or {}).get("place"),
        "hottest_temp": (hottest or {}).get("temp_c"),
        "coldest_place": (coldest or {}).get("place"),
        "coldest_temp": (coldest or {}).get("temp_c"),
    }
    with (log_dir / "extremes.jsonl").open("a", encoding="utf-8") as f:
        f.write(json.dumps(log, ensure_ascii=False) + "\n")

    # --- плоские поля под шаблон daily_en.j2 ---
    trend_emoji = {"up":"↑","down":"↓","stable":"→"}.get(trend, "→")

    hottest_place = (hottest or {}).get("place","—")
    coldest_place = (coldest or {}).get("place","—")

    flat = {
        "WEEKDAY": out["weekday_en"],
        "DATE": out["date_utc"],
        "QUAKE_TIME": (quake or {}).get("time_utc","—"),
        "VIBE_EMOJI": kp_level_emoji(kp),
        "KP": kp,
        "KP_TREND_EMOJI": trend_emoji,
        "KP_NOTE": out["cosmic"]["notes"]["kp_note"],
        "KP_SHORT": f"{float(kp):.1f}" if kp is not None else "—",
        "SCHUMANN_STATUS": sch_status,
        "SCHUMANN_AMP": (None if amp is None else amp),
        "SOLAR_WIND_SPEED": sw_speed or "—",
        "SOLAR_WIND_DENSITY": sw_den or "—",
        "SOLAR_NOTE": out["cosmic"]["notes"]["solar_note"],
        # -> с флагами:
        "HOTTEST_PLACE": _with_flag(hottest_place),
        "HOTTEST_TEMP": (hottest or {}).get("temp_c","—"),
        "COLDEST_PLACE": _with_flag(coldest_place),
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
        "EUR": f"{out['fx']['items']['EUR']['rate']:.4f}" if out['fx']['items']['EUR']['rate'] is not None else "—",
        "EUR_DELTA": (lambda x: f"{x:+.2f}%" if x is not None else "—")(out['fx']['items']['EUR']['chg_pct']),
        "CNY": f"{out['fx']['items']['CNY']['rate']:.4f}" if out['fx']['items']['CNY']['rate'] is not None else "—",
        "CNY_DELTA": (lambda x: f"{x:+.2f}%" if x is not None else "—")(out['fx']['items']['CNY']['chg_pct']),
        "fx_line": fx_line
    }

    out.update(flat)
    (Path(__file__).parent / "daily.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

if __name__ == "__main__":
    main()
