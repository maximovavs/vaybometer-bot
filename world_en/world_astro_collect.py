#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, re, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT  = Path(__file__).parent / "astro.json"
LOCAL_TZ = os.getenv("ASTRO_LOCAL_TZ", "Europe/Kaliningrad")
UTC = ZoneInfo("UTC")

RU2EN_SIGNS = {"Овен":"Aries","Телец":"Taurus","Близнецы":"Gemini","Рак":"Cancer","Лев":"Leo","Дева":"Virgo","Весы":"Libra","Скорпион":"Scorpio","Стрелец":"Sagittarius","Козерог":"Capricorn","Водолей":"Aquarius","Рыбы":"Pisces"}

def _load():
    for p in [ROOT/"lunar_calendar.json", ROOT/"data"/"lunar_calendar.json"]:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return {}

def _entry_for(date_iso:str, data:dict):
    # 1) days[YYYY-MM-DD]
    days = (data or {}).get("days") or {}
    if date_iso in days: 
        return days[date_iso], None
    # 2) monthly VoC list: ищем в любом текстовом блоке/списке
    month = date_iso[5:7]; day = date_iso[8:10]  # "10","01"
    patterns = [
        rf"\b{day}\.{month}\s+(\d{{2}}:\d{{2}})\s*[–—-]\s*(\d{{2}}:\d{{2}})\b",  # 01.10 15:47–22:52
        rf"\b{int(day)}\.{int(month)}\s+(\d{{2}}:\d{{2}})\s*[–—-]\s*(\d{{2}}:\d{{2}})\b",
    ]
    text = json.dumps(data, ensure_ascii=False)
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            start, end = m.group(1), m.group(2)
            return None, {"start": f"{day}.{month} {start}", "end": f"{day}.{month} {end}"}
    return None, None

def _to_utc_str(date_iso:str, what:str):
    """what: 'start'/'end' like '01.10 15:47'"""
    m = re.match(r"^\s*(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})\s*$", what or "")
    if not m: 
        return None
    dd, MM, hh, mm = map(int, m.groups())
    Y = int(date_iso[:4])
    try:
        local = dt.datetime(Y, MM, dd, hh, mm, tzinfo=ZoneInfo(LOCAL_TZ))
        return local.astimezone(UTC).strftime("%H:%M")
    except Exception:
        return None

def main():
    today = dt.date.today().isoformat()
    data = _load()
    entry, voc_dict = _entry_for(today, data)

    # базовые поля
    percent = (entry or {}).get("percent")
    phase_name_ru = (entry or {}).get("phase_name") or ""
    sign_ru = (entry or {}).get("sign") or ""
    sign_en = RU2EN_SIGNS.get(sign_ru, sign_ru or "—")

    # VoC
    if not voc_dict:
        voc_dict = (entry or {}).get("void_of_course")
    voc = "—"
    if isinstance(voc_dict, dict):
        start_utc = _to_utc_str(today, voc_dict.get("start",""))
        end_utc   = _to_utc_str(today, voc_dict.get("end",""))
        if start_utc and end_utc:
            voc = f"{start_utc}–{end_utc} UTC"
        elif start_utc:
            voc = f"from {start_utc} UTC"
        elif end_utc:
            voc = f"until {end_utc} UTC"

    # простая англ. фаза по проценту
    p = (percent or 0)
    if "нов" in phase_name_ru.lower(): phase_en = "New Moon"
    elif "полн" in phase_name_ru.lower(): phase_en = "Full Moon"
    elif "первая четверть" in phase_name_ru.lower(): phase_en = "First Quarter"
    elif "последняя" in phase_name_ru.lower() or "третья" in phase_name_ru.lower(): phase_en = "Last Quarter"
    else:
        phase_en = ("Waxing Gibbous" if p > 50 else "Waxing Crescent") if p >= 0 and p <= 100 else "—"

    energy = "Push gently through small obstacles." if "quarter" in phase_en.lower() else "Keep plans light; tune into your body."
    tip = "Focus on what matters."

    out = {
        "DATE": today, "MOON_PHASE": phase_en, "MOON_PERCENT": int(p) if p is not None else "—",
        "MOON_SIGN": sign_en, "VOC_WINDOW_UTC": voc,
        "ASTRO_ENERGY_ONE_LINER": energy, "ASTRO_TIP": tip
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
