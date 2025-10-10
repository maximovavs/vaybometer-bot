# world_en/world_collect.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import os
import re
import json
import random
import datetime as dt
from pathlib import Path
from typing import Optional, Tuple

import requests
from astral.sun import sun
from astral import LocationInfo
from pytz import UTC

# локальные импорты
from world_en.fx_intl import fetch_rates, format_line
from world_en.settings_world_en import (
    HOT_CITIES, COLD_SPOTS, SUN_CITIES, VIBE_TIPS,
    YT_API_KEY, YT_CHANNEL_ID, YOUTUBE_PLAYLIST_IDS, FALLBACK_NATURE_LIST
)

ROOT = Path(__file__).resolve().parents[1]
OUT  = Path(__file__).parent / "daily.json"

HEADERS = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Accept": "application/json,text/plain",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

# ----------------- helpers: safe http/json -----------------

def _get_json(url: str, params: dict | None = None, timeout: int = 20):
    r = requests.get(url, params=params or {}, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.json()

def _kp_badge(kp: Optional[float]) -> str:
    try:
        x = float(kp)
    except Exception:
        return ""
    if x >= 5:
        return "🟠"
    if x >= 4:
        return "🟡"
    return "🟢"

# ----------------- cosmic weather -----------------

def fetch_kp_now() -> Optional[float]:
    """
    Берём последний 1-минутный Kp (усреднение от NOAA).
    Если не удалось — None.
    """
    # Набор безопасных источников; берём первый удачный.
    urls = [
        # 1-minute estimated Kp (NOAA SWPC)
        "https://services.swpc.noaa.gov/products/noaa-estimated-planetary-k-index-1-minute.json",
        # 3-hour Kp (берём последний интервал и делим на 10 если формат другой)
        "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json",
    ]
    for url in urls:
        try:
            data = _get_json(url)
            if isinstance(data, list):
                # многие SWPC json'ы отдают первую строку как заголовки
                if data and isinstance(data[0], list) and "time_tag" in ",".join(map(str, data[0])):
                    rows = data[1:]
                else:
                    rows = data
                last = rows[-1]
                # ищем число в последних 2–3 ячейках
                vals = [v for v in last[-3:] if isinstance(v, (int, float, str))]
                for v in reversed(vals):
                    try:
                        x = float(v)
                        # некоторые фиды выдают 0..90 (x10) — нормализуем
                        if x > 9:
                            x = x / 10.0
                        if 0 <= x <= 9:
                            return round(x, 2)
                    except Exception:
                        continue
        except Exception:
            continue
    return None

def fetch_solar_wind() -> Tuple[Optional[float], Optional[float]]:
    """
    Возвращает (speed_km_s, density_cm3) или (None, None).
    """
    try:
        # SWPC 1-day plasma — скорость (km/s) и плотность (1/cm^3)
        data = _get_json("https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json")
        if data and isinstance(data, list):
            rows = data[1:] if data and "time_tag" in ",".join(map(str, data[0])) else data
            last = rows[-1]
            # формат: time_tag, density, speed, temperature
            dens = float(last[1]) if last[1] is not None else None
            spd  = float(last[2]) if last[2] is not None else None
            return (round(spd, 0) if spd is not None else None,
                    round(dens, 0) if dens is not None else None)
    except Exception:
        pass
    return None, None

def read_schumann_amp_delta() -> Optional[float]:
    """
    Пытаемся взять последнюю амплитуду из локального файла schumann_hourly.json,
    возвращаем отклонение от базового 7.83 Гц (может быть отрицательным).
    Если структура неизвестна — вернём None.
    """
    try:
        p = ROOT / "schumann_hourly.json"
        if not p.exists():
            return None
        data = json.loads(p.read_text(encoding="utf-8"))
        # пробуем наиболее распространённые варианты
        series = data.get("series") or data.get("data") or []
        if isinstance(series, list) and series:
            last = series[-1]
            # last может быть dict или list
            if isinstance(last, dict):
                amp = last.get("amp") or last.get("amplitude") or last.get("value")
            else:
                # ищем число в хвосте
                cand = [v for v in last if isinstance(v, (int, float))]
                amp = cand[-1] if cand else None
            if amp is not None:
                return round(float(amp) - 7.83, 2)
    except Exception:
        pass
    return None

# ----------------- earth live -----------------

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
            depth_km = round(top["geometry"]["coordinates"][2], 0) if top.get("geometry") else None
            # время в UTC «HH:MM»
            t = dt.datetime.utcfromtimestamp(p["time"]/1000.0).strftime("%H:%M") if p.get("time") else ""
            return mag, place, depth_km, t
        except Exception:
            continue
    return None, None, None, ""

def openmeteo_hottest_coldest_today():
    """
    Считаем экстремумы за «сегодня» по выбранным точкам, чтобы не зависеть от внешних логов.
    """
    hottest = None  # {"place":..., "temp":...}
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

def reykjavik_sun_time():
    """
    Маленький tidbit: время восхода для Reykjavik (UTC).
    Если астрон. расчёт не удался — отдаём текущее UTC.
    """
    try:
        loc = LocationInfo("Reykjavik", "", "UTC", 64.1466, -21.9426)
        s = sun(loc.observer, date=dt.date.today(), tzinfo=UTC)
        return "Reykjavik, IS", s["sunrise"].strftime("%H:%M")
    except Exception:
        return "Reykjavik, IS", dt.datetime.utcnow().strftime("%H:%M")

# ----------------- money / tip -----------------

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

# ----------------- YouTube top short (48h) -----------------

_ISO_DUR_RE = re.compile(r"^PT(?:(\d+)M)?(?:(\d+)S)?$")

def _is_short(iso: str) -> bool:
    m = _ISO_DUR_RE.fullmatch(iso or "")
    if not m:
        return False
    return (int(m.group(1) or 0) * 60 + int(m.group(2) or 0)) <= 60

def youtube_top_short_48h() -> Tuple[str, str]:
    api = os.getenv("YT_API_KEY", YT_API_KEY or "")
    ch  = os.getenv("YT_CHANNEL_ID", YT_CHANNEL_ID or "")
    if not (api and ch):
        # Фолбэк — первая ссылка из списка
        url = (FALLBACK_NATURE_LIST or ["https://youtube.com/@misserrelax"])[0]
        return "Nature Break", url

    cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=48)).replace(microsecond=0).isoformat() + "Z"
    try:
        # Берём последние свежие видео
        search = _get_json(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "key": api, "channelId": ch, "part": "id",
                "type": "video", "order": "date",
                "maxResults": 50, "publishedAfter": cutoff
            }
        )
        ids = [it["id"]["videoId"] for it in search.get("items", []) if it.get("id", {}).get("videoId")]
        if not ids:
            # fallback — просто последние
            search = _get_json(
                "https://www.googleapis.com/youtube/v3/search",
                params={"key": api, "channelId": ch, "part": "id", "type": "video", "order": "date", "maxResults": 20}
            )
            ids = [it["id"]["videoId"] for it in search.get("items", []) if it.get("id", {}).get("videoId")]
            if not ids:
                raise RuntimeError("empty ids")

        items = _get_json(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"key": api, "id": ",".join(ids), "part": "snippet,statistics,contentDetails"}
        ).get("items", [])

        pool = [v for v in items if _is_short(v["contentDetails"]["duration"])] or items
        if not pool:
            raise RuntimeError("empty pool")

        top = max(pool, key=lambda v: int(v["statistics"].get("viewCount", "0")))
        title = top["snippet"]["title"]
        url = f"https://youtu.be/{top['id']}?utm_source=telegram&utm_medium=worldvibemeter&utm_campaign=daily_favorite"
        return title, url
    except Exception:
        url = (FALLBACK_NATURE_LIST or ["https://youtube.com/@misserrelax"])[0]
        return "Nature Break", url

# ----------------- main -----------------

def main() -> dict:
    today = dt.date.today()
    weekday = dt.datetime.utcnow().strftime("%a")

    # Cosmic Weather
    kp = fetch_kp_now()
    sw_speed, sw_dens = fetch_solar_wind()
    sch_amp_delta = read_schumann_amp_delta()

    # Earth Live
    hot, cold = openmeteo_hottest_coldest_today()
    qmag, qplace, qdepth, qtime = strongest_quake_24h()
    sun_city, sun_time = reykjavik_sun_time()

    # Money & Tip
    fx_line = fx_line_today()
    tip_txt = pick_tip_text(today)

    # YouTube
    n_title, n_url = youtube_top_short_48h()

    out = {
        # header
        "DATE": today.isoformat(),
        "WEEKDAY": weekday,

        # cosmic weather
        "KP_NOW": f"{kp:.2f}" if isinstance(kp, (int, float)) else "—",
        "KP_BADGE": _kp_badge(kp),
        "SCHUMANN_AMP": f"{sch_amp_delta:+.2f}" if isinstance(sch_amp_delta, (int, float)) else "—",
        "SW_SPEED": f"{int(sw_speed)}" if isinstance(sw_speed, (int, float)) else "—",
        "SW_DENS": f"{int(sw_dens)}" if isinstance(sw_dens, (int, float)) else "—",

        # earth live
        "HOTTEST_PLACE": (hot or {}).get("place", "—"),
        "HOTTEST_TEMP":  (hot or {}).get("temp", "—"),
        "COLDEST_PLACE": (cold or {}).get("place", "—"),
        "COLDEST_TEMP":  (cold or {}).get("temp", "—"),
        "QUAKE_MAG":     qmag or "—",
        "QUAKE_REGION":  qplace or "—",
        "QUAKE_DEPTH":   qdepth or "—",
        "QUAKE_TIME":    qtime or "",
        "SUN_CITY":      sun_city,
        "SUN_TIME":      sun_time,  # 'HH:MM' UTC

        # money
        "fx_line": fx_line,

        # tip
        "TIP_BADGE": _kp_badge(kp),
        "TIP_TEXT": tip_txt,
        "TIP_SEC": "60 sec",

        # nature card (для шаблона и/или второго сообщения)
        "NATURE_TITLE": n_title,
        "NATURE_URL": n_url,
    }
    return out


if __name__ == "__main__":
    try:
        data = main()
        if not isinstance(data, dict):
            raise TypeError(f"main() returned {type(data).__name__}, expected dict")
    except Exception as e:
        # fail-safe, чтобы пайплайн не падал
        print(f"[daily][ERROR] main() failed: {e}")
        data = {
            "DATE": dt.date.today().isoformat(),
            "WEEKDAY": dt.datetime.utcnow().strftime("%a"),
            "KP_NOW": "—", "KP_BADGE": "",
            "SCHUMANN_AMP": "—", "SW_SPEED": "—", "SW_DENS": "—",
            "HOTTEST_PLACE": "—", "HOTTEST_TEMP": "—",
            "COLDEST_PLACE": "—", "COLDEST_TEMP": "—",
            "QUAKE_MAG": "—", "QUAKE_REGION": "—", "QUAKE_DEPTH": "—", "QUAKE_TIME": "",
            "SUN_CITY": "Reykjavik, IS", "SUN_TIME": dt.datetime.utcnow().strftime("%H:%M"),
            "fx_line": "USD 1.0000 (+0.00%)",
            "TIP_BADGE": "", "TIP_TEXT": "Keep plans light; tune into your body.", "TIP_SEC": "60 sec",
            "NATURE_TITLE": "Nature Break",
            "NATURE_URL": (FALLBACK_NATURE_LIST or ["https://youtube.com/@misserrelax"])[0],
        }
    try:
        OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[daily] wrote {OUT} ({OUT.stat().st_size} bytes)")
    except Exception as e:
        print(f"[daily][FATAL] failed to write {OUT}: {e}")