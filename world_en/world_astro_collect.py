#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, datetime as dt
from pathlib import Path
from astral import moon

OUT = Path(__file__).parent / "astro.json"

def phase_name(p):
    # p = 0..29.53, грубая карта
    names = [
        "New Moon","Waxing Crescent","First Quarter","Waxing Gibbous",
        "Full Moon","Waning Gibbous","Last Quarter","Waning Crescent"
    ]
    # приблизительная разбивка по восьмушкам
    idx = int(((p % 29.53) / 29.53) * 8) % 8
    return names[idx]

def main():
    today = dt.date.today()
    p = moon.phase(today)  # 0..29
    illum = int(round((1 - abs(15 - p)/15) * 100))  # простая аппроксимация %
    out = {
        "DATE": today.isoformat(),
        "MOON_PHASE": phase_name(p),
        "MOON_PERCENT": illum,
        "MOON_SIGN": "—",             # можно подтянуть из твоего lunar_calendar.json позднее
        "VOC_WINDOW_UTC": "—",
        "ASTRO_ENERGY_ONE_LINER": "Keep plans light; tune into your body.",
        "ASTRO_TIP": "60 seconds of slow breathing. Postpone heavy decisions."
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
