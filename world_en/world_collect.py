import json, random, datetime as dt, os
from pathlib import Path
import requests
from pytz import UTC
from astral import LocationInfo
from astral.sun import sun

from world_en.settings_world_en import (
    HOT_CITIES, COLD_SPOTS, SUN_CITIES, VIBE_TIPS,
    YT_API_KEY, YT_CHANNEL_ID, YOUTUBE_PLAYLIST_IDS, FALLBACK_NATURE_LIST
)

ROOT = Path(__file__).resolve().parents[1]

def get_kp_and_solar():
    # Kp
    kp_url = "https://services.swpc.noaa.gov/products/noaa-planetary-k-index.json"
    r = requests.get(kp_url, timeout=20); r.raise_for_status()
    rows = r.json()
    last = rows[-1]  # ["time_tag","kp_index","a_running","station_count"]
    kp = float(last[1])

    # trend: сравним со значением ~3 часа назад
    ref = rows[-4] if len(rows) >= 4 else last
    trend = "stable"
    if float(last[1]) > float(ref[1]): trend = "up"
    if float(last[1]) < float(ref[1]): trend = "down"

    # Solar wind (speed km/s, density cm^-3)
    sw = requests.get(
        "https://services.swpc.noaa.gov/products/solar-wind/plasma-1-day.json",
        timeout=20
    ); sw.raise_for_status()
    sw_rows = sw.json()
    # header: time, density, speed, temperature
    den = float(sw_rows[-1][1]) if sw_rows[-1][1] not in ("", None) else None
    spd = float(sw_rows[-1][2]) if sw_rows[-1][2] not in ("", None) else None

    return kp, trend, spd, den

def get_schumann_amp():
    # читаем твой JSON, который уже обновляют твои джобы
    p = ROOT / "schumann_hourly.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        # ожидаем поле amp на последней записи
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
    # За сутки, порог ~4.5
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
    # Возвращаем одну фичу на пост
    return random.choice([earliest, latest])

def get_fx():
    # международный источник без ключа
    base = "USD"
    symbols = "EUR,CNY"
    url = f"https://api.exchangerate.host/latest?base={base}&symbols={symbols}"
    r = requests.get(url, timeout=20); r.raise_for_status()
    rates = r.json()["rates"]
    # дельты (вчера)
    y = (dt.date.today() - dt.timedelta(days=1)).isoformat()
    yurl = f"https://api.exchangerate.host/{y}?base={base}&symbols={symbols}"
    try:
        yr = requests.get(yurl, timeout=20); yr.raise_for_status()
        yrates = yr.json()["rates"]
        def fmt(val, prev):
            delta = val - prev
            sign = "+" if delta >= 0 else "-"
            return f"{val:.2f}", f"{sign}{abs(delta):.2f}"
    except Exception:
        def fmt(val, prev): return f"{val:.2f}", "—"

    eur, eur_d = fmt(rates["EUR"], rates.get("EUR", rates["EUR"]))
    cny, cny_d = fmt(rates["CNY"], rates.get("CNY", rates["CNY"]))
    return {
        "usd": {"value": 1.00, "delta": "—"},
        "eur": {"value": float(eur), "delta": eur_d},
        "cny": {"value": float(cny), "delta": cny_d},
    }

def pick_nature_break():
    # 1) если есть YT API — берём самый популярный шорт из последних 25
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
                # простая эвристика «шорт»: длительность <= 60s
                def is_short(item):
                    dur = item["contentDetails"]["duration"]  # ISO 8601
                    return any(x in dur for x in ("PT0M", "PT1M"))  # дешево, но работает
                shorts = [v for v in stats if is_short(v)]
                top = max(shorts, key=lambda v:int(v["statistics"].get("viewCount","0"))) if shorts else max(stats, key=lambda v:int(v["statistics"].get("viewCount","0")))
                return {
                    "title": top["snippet"]["title"],
                    "snippet": "60 seconds of calm",
                    "youtube_url": f"https://youtu.be/{top['id']}"
                }
        except Exception:
            pass
    # 2) playlist ids (без API ключа не вытащить элементы надёжно) — поэтому фолбэк ниже
    # 3) жёсткий фолбэк: случайный из списка
    if FALLBACK_NATURE_LIST:
        url = random.choice(FALLBACK_NATURE_LIST)
        return {"title": "Nature Break", "snippet": "Short calm from Miss Relax", "youtube_url": url}
    return {"title": "Nature Break", "snippet": "Short calm from Miss Relax", "youtube_url": "https://youtube.com/@misserrelax"}

def main():
    kp, trend, sw_speed, sw_den = get_kp_and_solar()
    amp, sch_status = get_schumann_amp()
    hottest, coldest = get_extremes()
    quake = get_strongest_quake()
    sun_tidbit = get_sunlight_tidbit()
    fx = get_fx()
    tip = random.choice(VIBE_TIPS)
    nature = pick_nature_break()

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
        "fx": fx,
        "tips": [tip],
        "nature_break": nature
    }
    (Path(__file__).parent / "daily.json").write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
