#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

import json, random, datetime as dt, re
import requests
from astral.sun import sun
from astral import LocationInfo
from pytz import UTC

from world_en.fx_intl import fetch_rates, format_line
from world_en.settings_world_en import (
    HOT_CITIES, COLD_SPOTS, VIBE_TIPS,
    YT_API_KEY, YT_CHANNEL_ID, FALLBACK_NATURE_LIST
)

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).parent
DAILY_PATH = HERE / "daily.json"
ASTRO_PATH = HERE / "astro.json"

HEADERS = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Accept": "application/json,text/plain",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# ---------- tiny helpers ----------

def _read_json_safe(p: Path) -> dict:
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}

def _country_flag(cc: str) -> str:
    if not cc or len(cc) != 2: return ""
    base = 0x1F1E6
    a, b = ord(cc[0].upper())-65, ord(cc[1].upper())-65
    if not (0 <= a < 26 and 0 <= b < 26): return ""
    return chr(base+a) + chr(base+b)

CITY_TO_CC = {
    "Kuwait City": "KW", "Doha": "QA", "Phoenix": "US", "Dubai": "AE",
    "Jazan": "SA", "Vostok": "AQ", "Dome A": "AQ", "Ushuaia": "AR",
    "Yakutsk": "RU", "Oymyakon": "RU", "Verkhoyansk": "RU",
    "Reykjavik": "IS",
}

def _with_flag(place: str) -> str:
    if not place: return "—"
    place = place.strip()
    m = re.search(r",\s*([A-Z]{2})$", place)
    cc = m.group(1) if m else CITY_TO_CC.get(place.split(",")[0].strip(), "")
    fl = _country_flag(cc) if cc else ""
    return f"{place} {fl}".strip()

def kp_emoji(k: float | int | None) -> str:
    try: k = float(k or 0)
    except: k = 0.0
    if k >= 7: return "🔴"
    if k >= 5: return "🟠"
    if k >= 3: return "🟡"
    return "🟢"

def pretty_minus(n: float | int | str | None) -> str:
    if n is None: return "—"
    try:
        v = int(round(float(n)))
    except Exception:
        return str(n)
    s = f"{abs(v)}"
    return s if v >= 0 else f"−{s}"

# ---------- external fetchers ----------

def fetch_kp_now() -> tuple[str, float | None]:
    url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
    try:
        j = requests.get(url, timeout=20, headers=HEADERS).json()
        rows = j[1:] if isinstance(j, list) else []
        if not rows: return "—", None
        vals = []
        for r in rows[-3:]:
            try:
                vals.append(float(r[1]))
            except Exception:
                continue
        if not vals: return "—", None
        k = sum(vals)/len(vals)
        return f"{k:.2f}", k
    except Exception:
        return "—", None

def fetch_solar_wind() -> tuple[str, str]:
    url = "https://services.swpc.noaa.gov/products/solar-wind/plasma-5-minute.json"
    try:
        j = requests.get(url, timeout=20, headers=HEADERS).json()
        if not isinstance(j, list) or len(j) < 2: return "—", "—"
        header, last = j[0], j[-1]
        row = dict(zip(header, last))
        spd = row.get("speed") or row.get("velocity")
        den = row.get("density") or row.get("proton_density")
        spd_s = f"{int(round(float(spd)))}" if spd not in (None, "") else "—"
        den_s = f"{int(round(float(den)))}" if den not in (None, "") else "—"
        return spd_s, den_s
    except Exception:
        return "—", "—"

def strongest_quake_24h():
    try:
        url = "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_day.geojson"
        feats = requests.get(url, timeout=20, headers=HEADERS).json().get("features", [])
        if not feats: return None
        cutoff = dt.datetime.utcnow() - dt.timedelta(hours=24)
        cand = [f for f in feats if dt.datetime.utcfromtimestamp(f["properties"]["time"]/1000.0) >= cutoff] or feats
        top = max(cand, key=lambda f: f["properties"]["mag"] or 0.0)
        p = top["properties"]
        mag = round(p.get("mag") or 0.0, 1)
        region = p.get("place") or "—"
        depth_km = round((top.get("geometry", {}).get("coordinates", [None, None, 0])[2] or 0.0))
        t_utc = dt.datetime.utcfromtimestamp(p["time"]/1000.0).strftime("%H:%M")
        return {"mag": mag, "region": region, "depth_km": depth_km, "time_utc": t_utc}
    except Exception:
        return None

def openmeteo_extremes_today():
    def fetch_daily(lat, lon):
        url = "https://api.open-meteo.com/v1/forecast"
        params = {
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min",
            "past_days": 1, "forecast_days": 1, "timezone": "UTC"
        }
        return requests.get(url, params=params, timeout=20, headers=HEADERS).json().get("daily", {})

    hottest = None
    for name, la, lo in HOT_CITIES:
        try:
            mx = fetch_daily(la, lo).get("temperature_2m_max", [])
            if mx:
                t = max(mx)
                if (hottest is None) or (t > hottest["temp"]):
                    hottest = {"place": name, "temp": int(round(t))}
        except Exception:
            continue

    coldest = None
    for name, la, lo in COLD_SPOTS:
        try:
            mn = fetch_daily(la, lo).get("temperature_2m_min", [])
            if mn:
                t = min(mn)
                if (coldest is None) or (t < coldest["temp"]):
                    coldest = {"place": name, "temp": int(round(t))}
        except Exception:
            continue

    return hottest, coldest

def reykjavik_sunrise_today() -> str:
    try:
        loc = LocationInfo("Reykjavik", "", "UTC", 64.1466, -21.9426)
        s = sun(loc.observer, date=dt.date.today(), tzinfo=UTC)
        return s["sunrise"].strftime("%H:%M")
    except Exception:
        return "—"

def schumann_amp_from_file() -> str:
    p = ROOT / "schumann_hourly.json"
    try:
        j = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return "—"
    try:
        if isinstance(j, list):
            for item in reversed(j):
                if isinstance(item, dict):
                    for key in ("amp", "amplitude", "amp_avg"):
                        if key in item:
                            return f"{float(item[key]):.2f}"
        elif isinstance(j, dict):
            series = j.get("series") or j.get("data") or []
            if isinstance(series, list):
                for item in reversed(series):
                    if isinstance(item, dict):
                        for key in ("amp", "amplitude", "amp_avg"):
                            if key in item:
                                return f"{float(item[key]):.2f}"
    except Exception:
        pass
    return "—"

# ---------- YouTube: top short in last 48h ----------

def _yt_is_short_duration(iso_dur: str) -> bool:
    m = re.fullmatch(r"^PT(?:(\d+)M)?(?:(\d+)S)?$", iso_dur or "")
    if not m: return False
    return (int(m.group(1) or 0) * 60 + int(m.group(2) or 0)) <= 60

def top_short_48h() -> tuple[str | None, str | None]:
    """
    Самый просматриваемый шорт за последние 48 часов на канале.
    Возвращает (title, url) или (None, None) при ошибке.
    """
    api = (YT_API_KEY or "").strip()
    ch  = (YT_CHANNEL_ID or "").strip()
    if not (api and ch):
        return None, None

    cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=48)).replace(microsecond=0).isoformat() + "Z"
    try:
        # 1) свежие видео канала
        search = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "key": api,
                "channelId": ch,
                "part": "id",
                "type": "video",
                "order": "date",
                "maxResults": 50,
                "publishedAfter": cutoff,
            },
            timeout=20
        ).json()
        ids = [it["id"]["videoId"] for it in search.get("items", []) if it.get("id", {}).get("videoId")]
        if not ids:
            return None, None

        # 2) вытягиваем статистику + длительность
        stats = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"key": api, "id": ",".join(ids), "part": "snippet,statistics,contentDetails"},
            timeout=20
        ).json().get("items", [])

        pool = [v for v in stats if _yt_is_short_duration(v["contentDetails"]["duration"])] or stats
        if not pool:
            return None, None

        top = max(pool, key=lambda v: int(v["statistics"].get("viewCount", "0")))
        title = top["snippet"]["title"]
        url = f"https://youtu.be/{top['id']}"
        return title, url
    except Exception:
        return None, None

# ---------- astro bridge ----------

def load_astro_dict() -> dict:
    try:
        from world_en.world_astro_collect import main as astro_main  # type: ignore
        rv = astro_main()
        if isinstance(rv, dict) and rv:
            return rv
    except Exception:
        pass
    return _read_json_safe(ASTRO_PATH)

# ---------- main ----------

def main():
    astro = load_astro_dict()

    today = dt.date.today().isoformat()
    weekday = dt.datetime.utcnow().strftime("%a")

    # Cosmic Weather
    kp_txt, kp_val = fetch_kp_now()
    sw_speed, sw_density = fetch_solar_wind()
    sch_amp = schumann_amp_from_file()

    # Earth Live
    hot, cold = openmeteo_extremes_today()
    hot_place = _with_flag((hot or {}).get("place", ""))
    hot_t = pretty_minus((hot or {}).get("temp"))
    cold_place = _with_flag((cold or {}).get("place", ""))
    cold_t = pretty_minus((cold or {}).get("temp"))

    quake = strongest_quake_24h() or {}
    sun_rise = reykjavik_sunrise_today()

    # Money
    try:
        fx = fetch_rates("USD", ["EUR","CNY","JPY","INR","IDR"])
        fx_line = format_line(fx, order=["USD","EUR","CNY","JPY","INR","IDR"])
    except Exception:
        fx_line = "—"

    # Vibe Tip
    tip_core = random.choice(VIBE_TIPS) if VIBE_TIPS else "Sip water and take 10 slow breaths."
    tip_badge = kp_emoji(kp_val)
    tip_meta = f"(Kp {kp_txt} • 60 sec)" if kp_txt and kp_txt != "—" else "(60 sec)"

    # Nature / YouTube
    yt_title, yt_url = top_short_48h()
    if not yt_url:
        # запасной вариант
        yt_url = (FALLBACK_NATURE_LIST[0] if FALLBACK_NATURE_LIST else "https://youtube.com/@misserrelax")
        yt_title = yt_title or "Nature Break"

    out = {
        # base
        "DATE": astro.get("DATE") or today,
        "WEEKDAY": astro.get("WEEKDAY") or weekday,

        # ---- Cosmic Weather ----
        "KP_NOW": kp_txt,
        "KP_VALUE": kp_val,
        "KP_BADGE": tip_badge,
        "SCHUMANN_AMP": sch_amp,
        "SOLAR_WIND_SPEED": sw_speed,
        "SOLAR_WIND_DENSITY": sw_density,

        # ---- Earth Live ----
        "HOTTEST_PLACE": (hot or {}).get("place") or "—",
        "HOTTEST_PLACE_FLAGGED": hot_place or "—",
        "HOTTEST_TEMP": hot_t if hot_t != "—" else "",
        "COLDEST_PLACE": (cold or {}).get("place") or "—",
        "COLDEST_PLACE_FLAGGED": cold_place or "—",
        "COLDEST_TEMP": cold_t if cold_t != "—" else "",
        "QUAKE_MAG": quake.get("mag", "—"),
        "QUAKE_REGION": quake.get("region", "—"),
        "QUAKE_DEPTH_KM": quake.get("depth_km", "—"),
        "QUAKE_UTC": quake.get("time_utc", "—"),
        "SUN_TIDBIT_PLACE": "Reykjavik, IS",
        "SUN_TIDBIT_TIME": sun_rise,
        "SUN_HIGHLIGHT_PLACE": "Reykjavik, IS",
        "SUN_HIGHLIGHT_TIME": sun_rise,

        # ---- Money ----
        "fx_line": fx_line,

        # ---- Astro passthrough ----
        "MOON_PHASE": astro.get("MOON_PHASE") or "—",
        "PHASE_EN": astro.get("PHASE_EN") or "—",
        "PHASE_EMOJI": astro.get("PHASE_EMOJI") or "",
        "MOON_PERCENT": astro.get("MOON_PERCENT"),
        "MOON_SIGN": astro.get("MOON_SIGN") or "—",
        "MOON_SIGN_EMOJI": astro.get("MOON_SIGN_EMOJI") or "",
        "VOC": astro.get("VOC") or astro.get("VOC_TEXT") or "No VoC today UTC",
        "VOC_TEXT": astro.get("VOC_TEXT") or astro.get("VOC") or "No VoC today UTC",
        "VOC_LEN": astro.get("VOC_LEN") or "",
        "VOC_BADGE": astro.get("VOC_BADGE") or "",
        "VOC_IS_ACTIVE": bool(astro.get("VOC_IS_ACTIVE")),

        # ---- Vibe Tip ----
        "TIP_TEXT": tip_core,
        "TIP_META": tip_meta,
        "TIP_BADGE": tip_badge,

        # ---- Nature video ----
        "NATURE_TITLE": yt_title or "Nature Break",
        "NATURE_SNIPPET": "60 seconds of calm",
        "NATURE_URL": yt_url,
    }

    # синонимы для старых шаблонов
    out.setdefault("KP", out["KP_NOW"])
    out.setdefault("SCHUMANN", out["SCHUMANN_AMP"])
    out.setdefault("SW_SPEED", out["SOLAR_WIND_SPEED"])
    out.setdefault("SW_DENSITY", out["SOLAR_WIND_DENSITY"])

    DAILY_PATH.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[daily] wrote {DAILY_PATH} ({DAILY_PATH.stat().st_size} bytes)")


if __name__ == "__main__":
    main()