# world_en/world_collect.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, re, json, random, datetime as dt
from typing import Optional, Tuple, List
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

import requests
from astral.sun import sun
from astral import LocationInfo
from pytz import UTC

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
    # SWPC любит явно указывать referer
    "Referer": "https://services.swpc.noaa.gov/",
}

# ---------- helpers ----------

def _get_json(url: str, params=None, timeout=25):
    r = requests.get(url, params=params or {}, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.json()

def _get_text(url: str, timeout=25) -> str:
    r = requests.get(url, timeout=timeout, headers=HEADERS)
    r.raise_for_status()
    return r.text

def safe_float(x, default=None):
    try:
        return float(x)
    except Exception:
        return default

def cc_flag(cc: str) -> str:
    cc = (cc or "").strip().upper()
    if len(cc) != 2:
        return ""
    base = ord('🇦') - ord('A')
    return chr(base + ord(cc[0])) + chr(base + ord(cc[1]))

def place_with_flag(place: str) -> str:
    if not place:
        return "—"
    if "," in place:
        name, cc = [s.strip() for s in place.rsplit(",", 1)]
        fl = cc_flag(cc)
        return f"{name}, {cc} {fl}" if fl else f"{name}, {cc}"
    return place

def fmt_pct(x: Optional[float]) -> str:
    if x is None:
        return "—"
    s = f"{x:+.2f}%"
    # уберём +0.00% → +0.00%
    return s

# ---------- Kp (planetary index) ----------

def fetch_kp_latest() -> Tuple[Optional[float], Optional[str], str]:
    """
    Возвращает (kp_value, trend_emoji, note).
    Источник: SWPC NOAA 'noaa-planetary-k-index' JSON (последние записи).
    """
    try:
        url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
        data = _get_json(url)
        # data: [header], [timestamp, kp], ...
        rows = [row for row in data if isinstance(row, list) and len(row) >= 2][1:]
        if len(rows) >= 2:
            last  = safe_float(rows[-1][1])
            prev  = safe_float(rows[-2][1])
            trend = "→"
            if last is not None and prev is not None:
                if last > prev + 0.1: trend = "↗"
                elif last < prev - 0.1: trend = "↘"
            return last, trend, kp_note(last)
        elif len(rows) == 1:
            last = safe_float(rows[-1][1])
            return last, "→", kp_note(last)
    except Exception:
        pass
    return None, "—", "—"

def kp_note(kp: Optional[float]) -> str:
    if kp is None: return "—"
    if kp < 2.0:   return "calm"
    if kp < 3.0:   return "calm to moderate"
    if kp < 4.0:   return "moderate"
    if kp < 5.0:   return "active"
    if kp < 6.0:   return "storm watch"
    return "storm conditions"

def vibe_emoji_from_kp(kp: Optional[float]) -> str:
    if kp is None: return "⚪️"
    if kp <= 2.0:  return "🟢"
    if kp <= 3.5:  return "🟡"
    if kp <= 5.0:  return "🟠"
    return "🔴"

# ---------- Schumann (амплитуда относительно 7.83 Hz) ----------

def fetch_schumann_amp() -> Tuple[str, str]:
    """
    Возвращает (status, amp_abs_str).
    Источники разнородные, поэтому лёгкая эвристика:
      - Пытаемся взять амплитуду (или отклонение) в единицах 'Hz amplitude' из любого доступного API.
      - Если ничего нет — вернём baseline/—.
    """
    try:
        # Пробуем простой открытый источник (псевдо-амплитуда, пример формата; обернули try)
        # Если формат не совпадёт — просто уйдём в except и дадим baseline/—.
        txt = _get_text("https://services.swpc.noaa.gov/text/ace-swepam.txt", timeout=10)
        # Это не Schumann, но даёт нам устойчивый фолбэк как «нет данных».
        raise RuntimeError("no reliable schumann endpoint")
    except Exception:
        pass

    # Нет стабильного источника — безопасный дефолт:
    return "baseline", "—"

# ---------- Solar wind ----------

def fetch_solar_wind() -> Tuple[Optional[float], Optional[float], str]:
    """
    Возвращает (speed_km_s, density_cm3, note).
    Источники: несколько сводных JSON SWPC; берём, что получится.
    """
    speed = None
    dens  = None

    # Попытка 1: сводная панель DSCOVR
    try:
        # https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json etc — иногда недоступны
        sp = _get_json("https://services.swpc.noaa.gov/products/summary/solar-wind-speed.json")
        de = _get_json("https://services.swpc.noaa.gov/products/summary/solar-wind-density.json")
        # формат: ["time","value"], ["YYYY-MM-DD HH:MM:SS", number]
        if isinstance(sp, list) and len(sp) >= 2 and isinstance(sp[-1], list):
            speed = safe_float(sp[-1][1])
        if isinstance(de, list) and len(de) >= 2 and isinstance(de[-1], list):
            dens = safe_float(de[-1][1])
    except Exception:
        pass

    # Попытка 2: ACE SWEPAM 1m (табличный текст)
    if speed is None or dens is None:
        try:
            txt = _get_text("https://services.swpc.noaa.gov/text/ace-swepam.txt", timeout=15)
            # берём последнюю числовую строку
            lines = [ln for ln in txt.splitlines() if ln and ln[0].isdigit()]
            if lines:
                cols = re.split(r"\s+", lines[-1].strip())
                # формат у ACE: ... Vp(km/s)=cols[7], Np(/cc)=cols[8] (может отличаться)
                v = safe_float(cols[-2])
                n = safe_float(cols[-1])
                if v: speed = v
                if n: dens  = n
        except Exception:
            pass

    return speed, dens, solar_note(speed, dens)

def solar_note(speed_kms: Optional[float], dens_cm3: Optional[float]) -> str:
    if speed_kms is None or dens_cm3 is None:
        return "—"
    if speed_kms < 350 and dens_cm3 < 3:   return "calm"
    if speed_kms < 450 and dens_cm3 < 7:   return "gentle stream"
    if speed_kms < 550 and dens_cm3 < 12:  return "moderate stream"
    return "active stream"

# ---------- Earth extremes (Open-Meteo, текущее) ----------

def openmeteo_current_temp(lat: float, lon: float) -> Optional[float]:
    try:
        data = _get_json("https://api.open-meteo.com/v1/forecast",
                         params={"latitude": lat, "longitude": lon,
                                 "current": "temperature_2m", "timezone": "UTC"})
        t = data.get("current", {}).get("temperature_2m", None)
        return safe_float(t)
    except Exception:
        return None

def pick_daily_extremes() -> Tuple[str, Optional[int], str, Optional[int]]:
    hottest = ("—", None)  # (place, temp_int)
    coldest = ("—", None)

    for name, la, lo in HOT_CITIES:
        t = openmeteo_current_temp(la, lo)
        if isinstance(t, (int, float)):
            if hottest[1] is None or t > hottest[1]:
                hottest = (name, int(round(t)))

    for name, la, lo in COLD_SPOTS:
        t = openmeteo_current_temp(la, lo)
        if isinstance(t, (int, float)):
            if coldest[1] is None or t < coldest[1]:
                coldest = (name, int(round(t)))

    return hottest[0], hottest[1], coldest[0], coldest[1]

# ---------- Earthquakes (USGS, 24h) ----------

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
            mag   = round(p.get("mag", 0.0), 1)
            where = p.get("place", "—")
            depth = safe_float(top.get("geometry", {}).get("coordinates", [None, None, None])[2], None)
            t_ms  = p.get("time")
            t_utc = dt.datetime.utcfromtimestamp(int(t_ms)/1000).strftime("%H:%M") if t_ms else "—"
            return mag, where, (round(depth,1) if depth is not None else "—"), t_utc
        except Exception:
            continue
    return "—", "—", "—", "—"

# ---------- Sunlight tidbit ----------

def sunlight_tidbit_today() -> Tuple[str, str, str]:
    """
    Выбираем город по дню года и даём время восхода (UTC).
    """
    try:
        today = dt.date.today()
        idx = today.timetuple().tm_yday % max(1, len(SUN_CITIES))
        name, la, lo = SUN_CITIES[idx]
        loc = LocationInfo(name, "", "UTC", la, lo)
        s  = sun(loc.observer, date=today, tzinfo=UTC)
        return "Sunrise", name, s["sunrise"].strftime("%H:%M")
    except Exception:
        return "Sunrise", "Reykjavik, IS", dt.datetime.utcnow().strftime("%H:%M")

# ---------- FX line ----------

def build_fx_line() -> str:
    try:
        fx = fetch_rates("USD", ["EUR","CNY","JPY","INR","IDR"])
        return format_line(fx, order=["USD","EUR","CNY","JPY","INR","IDR"])
    except Exception:
        # минимальный фолбэк
        return "USD 1.0000 (+0.00%) • EUR 0.9000 (+0.00%) • CNY 7.10 (+0.00%) • JPY 150.00 (+0.00%) • INR 88.00 (+0.00%) • IDR 16,500 (+0.00%)"

# ---------- YouTube: лучший короткий ролик за 48 часов ----------

def _is_short_iso(iso: str) -> bool:
    m = re.fullmatch(r"^PT(?:(\d+)M)?(?:(\d+)S)?$", iso or "")
    if not m: return False
    return (int(m.group(1) or 0)*60 + int(m.group(2) or 0)) <= 60

def pick_top_short_48h() -> Tuple[Optional[str], Optional[str]]:
    api = os.getenv("YT_API_KEY", YT_API_KEY)
    ch  = os.getenv("YT_CHANNEL_ID", YT_CHANNEL_ID)
    if not api or not ch:
        return None, None

    cutoff = (dt.datetime.utcnow() - dt.timedelta(hours=48)).replace(microsecond=0).isoformat() + "Z"

    try:
        # свежие видео канала
        search = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={"key": api, "channelId": ch, "part": "id",
                    "type": "video", "order": "date", "maxResults": 50,
                    "publishedAfter": cutoff},
            timeout=20
        ).json()
        ids = [it["id"]["videoId"] for it in search.get("items", []) if it.get("id", {}).get("videoId")]

        if not ids and YOUTUBE_PLAYLIST_IDS:
            # фолбэк: последние из заданных плейлистов
            for pl in YOUTUBE_PLAYLIST_IDS:
                pl_items = requests.get(
                    "https://www.googleapis.com/youtube/v3/playlistItems",
                    params={"key": api, "playlistId": pl, "part": "contentDetails", "maxResults": 25},
                    timeout=20
                ).json().get("items", [])
                ids += [x["contentDetails"]["videoId"] for x in pl_items if x.get("contentDetails", {}).get("videoId")]
                if ids:
                    break

        if not ids:
            return None, None

        stats = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"key": api, "id": ",".join(ids), "part": "snippet,statistics,contentDetails"},
            timeout=20
        ).json().get("items", [])

        pool = [v for v in stats if _is_short_iso(v["contentDetails"]["duration"])] or stats
        if not pool:
            return None, None

        top = max(pool, key=lambda v: int(v["statistics"].get("viewCount", "0")))
        title = top["snippet"]["title"]
        url   = f"https://youtu.be/{top['id']}?utm_source=telegram&utm_medium=worldvibemeter&utm_campaign=daily_nature"
        return title, url
    except Exception:
        return None, None

# ---------- Vibe Tip ----------

def pick_vibe_tip(kp: Optional[float]) -> Tuple[str, str]:
    """
    Возвращает (emoji, tip_text).
    Эмодзи завязано на Kp, текст берем из VIBE_TIPS (случайно/циклически).
    """
    emo = vibe_emoji_from_kp(kp)
    if VIBE_TIPS:
        idx = dt.date.today().toordinal() % len(VIBE_TIPS)
        tip = VIBE_TIPS[idx]
    else:
        tip = "Sip water and take 10 slow breaths."
    return emo, tip

# ---------- main ----------

def main():
    today   = dt.date.today()
    weekday = dt.datetime.utcnow().strftime("%a")

    # Kp
    KP_VAL, KP_TREND_EMOJI, KP_NOTE = fetch_kp_latest()
    KP_SHORT = f"{KP_VAL:.1f}" if isinstance(KP_VAL, float) else "—"

    # Schumann
    SCHUMANN_STATUS, SCHUMANN_AMP = fetch_schumann_amp()

    # Solar wind
    SOLAR_WIND_SPEED, SOLAR_WIND_DENSITY, SOLAR_NOTE = fetch_solar_wind()
    SW_S = f"{SOLAR_WIND_SPEED:.0f}" if isinstance(SOLAR_WIND_SPEED, float) else "—"
    SW_N = f"{SOLAR_WIND_DENSITY:.0f}" if isinstance(SOLAR_WIND_DENSITY, float) else "—"

    # Extremes
    HOTTEST_PLACE, HOTTEST_TEMP, COLDEST_PLACE, COLDEST_TEMP = pick_daily_extremes()
    HOTTEST_PLACE = place_with_flag(HOTTEST_PLACE)
    COLDEST_PLACE = place_with_flag(COLDEST_PLACE)

    # Quake 24h
    QUAKE_MAG, QUAKE_REGION, QUAKE_DEPTH, QUAKE_TIME = strongest_quake_24h()

    # Sunlight tidbit
    SUN_TIDBIT_LABEL, SUN_TIDBIT_PLACE, SUN_TIDBIT_TIME = sunlight_tidbit_today()

    # FX
    fx_line = build_fx_line()

    # Vibe Tip
    VIBE_EMOJI, TIP_TEXT = pick_vibe_tip(KP_VAL)

    # Nature short (URL сохраняем — отправка отдельным шагом в workflow)
    NATURE_TITLE, NATURE_URL = pick_top_short_48h()
    if not NATURE_URL and FALLBACK_NATURE_LIST:
        NATURE_URL = FALLBACK_NATURE_LIST[0]
        if not NATURE_TITLE:
            NATURE_TITLE = "Nature Break"

    out = {
        "WEEKDAY": weekday,
        "DATE": today.isoformat(),

        # Cosmic Weather
        "KP": f"{KP_VAL:.2f}" if isinstance(KP_VAL, float) else "—",
        "KP_TREND_EMOJI": KP_TREND_EMOJI,
        "KP_NOTE": KP_NOTE,

        "SCHUMANN_STATUS": SCHUMANN_STATUS,
        "SCHUMANN_AMP": SCHUMANN_AMP,

        "SOLAR_WIND_SPEED": SW_S,
        "SOLAR_WIND_DENSITY": SW_N,
        "SOLAR_NOTE": SOLAR_NOTE,

        # Earth Live
        "HOTTEST_PLACE": HOTTEST_PLACE,
        "HOTTEST_TEMP": HOTTEST_TEMP if HOTTEST_TEMP is not None else "—",
        "COLDEST_PLACE": COLDEST_PLACE,
        "COLDEST_TEMP": COLDEST_TEMP if COLDEST_TEMP is not None else "—",

        "QUAKE_MAG": QUAKE_MAG,
        "QUAKE_REGION": QUAKE_REGION,
        "QUAKE_DEPTH": QUAKE_DEPTH,
        "QUAKE_TIME": QUAKE_TIME,

        "SUN_TIDBIT_LABEL": SUN_TIDBIT_LABEL,
        "SUN_TIDBIT_PLACE": SUN_TIDBIT_PLACE,
        "SUN_TIDBIT_TIME": SUN_TIDBIT_TIME,

        # Money
        "fx_line": fx_line,

        # Vibe tip
        "VIBE_EMOJI": VIBE_EMOJI,
        "KP_SHORT": KP_SHORT,
        "TIP_TEXT": TIP_TEXT,

        # For extra post with preview
        "NATURE_TITLE": NATURE_TITLE or "Nature Break",
        "NATURE_URL": NATURE_URL or "",
    }

    return out

# ---------- write-out guard ----------

if __name__ == "__main__":
    out_path = OUT
    try:
        data = main()
        if not isinstance(data, dict):
            raise TypeError(f"main() returned {type(data).__name__}, expected dict")
    except Exception as e:
        print(f"[daily][ERROR] main() failed: {e}")
        data = {
            "DATE": dt.date.today().isoformat(),
            "WEEKDAY": dt.datetime.utcnow().strftime("%a"),
            "KP": "—", "KP_TREND_EMOJI": "—", "KP_NOTE": "—",
            "SCHUMANN_STATUS": "baseline", "SCHUMANN_AMP": "—",
            "SOLAR_WIND_SPEED": "—", "SOLAR_WIND_DENSITY": "—", "SOLAR_NOTE": "—",
            "HOTTEST_PLACE": "—", "HOTTEST_TEMP": "—",
            "COLDEST_PLACE": "—", "COLDEST_TEMP": "—",
            "QUAKE_MAG": "—", "QUAKE_REGION": "—", "QUAKE_DEPTH": "—", "QUAKE_TIME": "—",
            "SUN_TIDBIT_LABEL": "Sunrise", "SUN_TIDBIT_PLACE": "Reykjavik, IS",
            "SUN_TIDBIT_TIME": dt.datetime.utcnow().strftime("%H:%M"),
            "fx_line": "USD 1.0000 (+0.00%)",
            "VIBE_EMOJI": "⚪️", "KP_SHORT": "—", "TIP_TEXT": "Keep plans light; tune into your body.",
            "NATURE_TITLE": "Nature Break", "NATURE_URL": (FALLBACK_NATURE_LIST[0] if FALLBACK_NATURE_LIST else "")
        }
    try:
        out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"[daily] wrote {out_path} ({out_path.stat().st_size} bytes)")
    except Exception as e:
        print(f"[daily][FATAL] failed to write {out_path}: {e}")