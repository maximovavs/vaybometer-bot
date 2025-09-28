#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, datetime as dt
from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

import requests
from pytz import UTC
from world_en.fx_intl import fetch_rates, format_line

OUT = Path(__file__).parent / "weekly.json"

def strongest_quake_week():
    urls = [
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/6.0_week.geojson",
        "https://earthquake.usgs.gov/earthquakes/feed/v1.0/summary/4.5_week.geojson",
    ]
    for url in urls:
        try:
            r = requests.get(url, timeout=20); r.raise_for_status()
            feats = r.json().get("features", [])
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

def main():
    today = dt.date.today()
    week_start = (today - dt.timedelta(days=today.weekday())).isoformat()
    week_end = today.isoformat()

    mag, region, note = strongest_quake_week()
    fx = fetch_rates("USD", ["EUR","CNY","JPY"])
    fx_line_week = format_line(fx, order=["USD","EUR","CNY","JPY"])

    out = {
        "WEEK_START": week_start,
        "WEEK_END": week_end,
        "TOP_QUAKE_MAG": mag or "—",
        "TOP_QUAKE_REGION": region or "—",
        "TOP_QUAKE_NOTE": note or "",
        "HOTTEST_WEEK_PLACE": "—",
        "HOTTEST_WEEK": "—",
        "COLDEST_WEEK_PLACE": "—",
        "COLDEST_WEEK": "—",
        "CALM_WINDOW_UTC": "Wed 09–12 (low Kp)",
        "SUN_HIGHLIGHT_PLACE": "Reykjavik, IS",
        "SUN_HIGHLIGHT_TIME": dt.datetime.utcnow().strftime("%H:%M"),
        "TOP_NATURE_TITLE": "Nature Break",
        "fx_line_week": fx_line_week
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
