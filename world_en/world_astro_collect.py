#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, re, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT  = Path(__file__).parent / "astro.json"

# локальная зона, в которой составлен lunar_calendar.json
LOCAL_TZ = os.getenv("ASTRO_LOCAL_TZ", "Europe/Kaliningrad")
UTC = ZoneInfo("UTC")

RU2EN_SIGNS = {
    "Овен":"Aries","Телец":"Taurus","Близнецы":"Gemini","Рак":"Cancer",
    "Лев":"Leo","Дева":"Virgo","Весы":"Libra","Скорпион":"Scorpio",
    "Стрелец":"Sagittarius","Козерог":"Capricorn","Водолей":"Aquarius","Рыбы":"Pisces"
}

def _load_calendar():
    # читаем либо из корня, либо из data/
    for p in (ROOT / "lunar_calendar.json", ROOT / "data" / "lunar_calendar.json"):
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return {}

def _entry_for(date_iso: str, data: dict):
    """Пытаемся достать запись дня. Возвращаем (entry|None)."""
    days = (data or {}).get("days") or {}
    return days.get(date_iso)

def _parse_sign(entry: dict) -> str | None:
    """Сначала 'sign', если нет — попробуем вытащить из 'phase' после запятой."""
    sign_ru = (entry or {}).get("sign")
    if sign_ru:
        return RU2EN_SIGNS.get(sign_ru, sign_ru)
    phase = (entry or {}).get("phase") or ""
    # ... "Растущая Луна , Водолей"
    m = re.search(r",\s*([А-Яа-яЁёA-Za-z]+)\s*$", phase)
    if m:
        sru = m.group(1)
        return RU2EN_SIGNS.get(sru, sru)
    return None

_TIME_PAT = re.compile(r"^\s*(\d{1,2})\.(\d{1,2})\s+(\d{2}):(\d{2})\s*$")

def _to_utc_hhmm(date_iso: str, val: str | None) -> str | None:
    """Перевод 'dd.MM HH:MM' (локал.) в строку 'HH:MM' UTC для 'date_iso'."""
    if not val:
        return None
    m = _TIME_PAT.match(val)
    if not m:
        return None
    dd, MM, hh, mm = map(int, m.groups())
    Y = int(date_iso[:4])
    try:
        local = dt.datetime(Y, MM, dd, hh, mm, tzinfo=ZoneInfo(LOCAL_TZ))
        return local.astimezone(UTC).strftime("%H:%M")
    except Exception:
        return None

def _compose_phase_en(ru_name: str, percent):
    ru = (ru_name or "").lower()
    if "нов" in ru: return "New Moon"
    if "полн" in ru: return "Full Moon"
    if "первая четверть" in ru: return "First Quarter"
    if "последняя" in ru or "третья" in ru: return "Last Quarter"
    # остальное — по проценту
    p = percent if isinstance(percent, (int, float)) else None
    if p is None:
        return "Waxing"  # нейтральный фолбэк
    return "Waxing Gibbous" if p >= 50 else "Waxing Crescent"

def main():
    today_iso = dt.date.today().isoformat()  # TZ=UTC в workflow
    cal = _load_calendar()
    entry = _entry_for(today_iso, cal) or {}

    percent = entry.get("percent")
    phase_en = _compose_phase_en(entry.get("phase_name"), percent)
    sign_en = _parse_sign(entry) or "—"

    # VoC: берём из entry. Если null — считаем, что нет окна.
    voc = entry.get("void_of_course") or {}
    start_utc = _to_utc_hhmm(today_iso, voc.get("start"))
    end_utc   = _to_utc_hhmm(today_iso, voc.get("end"))
    voc_str = "—"
    if start_utc and end_utc:
        voc_str = f"{start_utc}–{end_utc} UTC"
    elif start_utc:
        voc_str = f"from {start_utc} UTC"
    elif end_utc:
        voc_str = f"until {end_utc} UTC"

    out = {
        "DATE": today_iso,
        "MOON_PHASE": phase_en,
        "MOON_PERCENT": (int(percent) if isinstance(percent,(int,float)) else "—"),
        "MOON_SIGN": sign_en,
        "VOC_WINDOW_UTC": voc_str,
        "ASTRO_ENERGY_ONE_LINER": (
            "Push gently through small obstacles." if "quarter" in phase_en.lower()
            else "Keep plans light; tune into your body."
        ),
        "ASTRO_TIP": "Focus on what matters."
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
