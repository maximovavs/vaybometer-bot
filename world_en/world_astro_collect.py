#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# world_en/world_astro_collect.py

import os, json, re, datetime as dt
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
OUT  = Path(__file__).parent / "astro.json"

# Локальная таймзона для VoC (можно переопределить в workflow env)
LOCAL_TZ = os.getenv("ASTRO_LOCAL_TZ", "Europe/Kaliningrad")
UTC = ZoneInfo("UTC")

RU2EN_SIGNS = {
    "Овен":"Aries","Телец":"Taurus","Близнецы":"Gemini","Рак":"Cancer","Лев":"Leo","Дева":"Virgo",
    "Весы":"Libra","Скорпион":"Scorpio","Стрелец":"Sagittarius","Козерог":"Capricorn",
    "Водолей":"Aquarius","Рыбы":"Pisces"
}

ADVICE_MAP = {
    "Сфокусируйся на главном": "Focus on what matters.",
    "Отложи крупные решения": "Postpone big decisions.",
    "5-минутная медитация": "Take a 5-minute meditation.",
}

def ru_phase_to_en(name: str, percent: int | None) -> str:
    n = (name or "").lower()
    p = percent or 0
    if "нов" in n: return "New Moon"
    if "первая четверть" in n: return "First Quarter"
    if "последняя четверть" in n or "третья четверть" in n: return "Last Quarter"
    if "полн" in n: return "Full Moon"
    if "растущ" in n:
        return "Waxing Gibbous" if p >= 50 else "Waxing Crescent"
    if "убыва" in n:
        return "Waning Gibbous" if p > 50 else "Waning Crescent"
    # fallback по проценту
    if p == 0: return "New Moon"
    if 0 < p < 50: return "Waxing Crescent"
    if p == 50: return "First Quarter"
    if 50 < p < 100: return "Waxing Gibbous"
    return "Full Moon"

def load_calendar():
    for p in [ROOT / "lunar_calendar.json", ROOT / "data" / "lunar_calendar.json"]:
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    return None

def pick_today_entry(data: dict, today_iso: str):
    days = (data or {}).get("days", {})
    if today_iso in days:
        return days[today_iso]
    # ближайшая запись (если день не найден)
    try:
        keys = sorted(days.keys())
        if not keys: return None
        # выбираем по минимальной разнице дат
        today = dt.date.fromisoformat(today_iso)
        best = min(keys, key=lambda k: abs(dt.date.fromisoformat(k) - today))
        return days[best]
    except Exception:
        return None

def parse_voc(entry: dict, day_iso: str) -> str:
    voc = (entry or {}).get("void_of_course") or {}
    start = voc.get("start"); end = voc.get("end")
    if not start and not end:
        return "—"

    def parse_ddmm_hhmm(s: str):
        m = re.match(r"\s*(\d{2})\.(\d{2})\s+(\d{2}):(\d{2})\s*$", s or "")
        if not m: return None
        dd, MM, hh, mm = map(int, m.groups())
        # Год берём из day_iso
        Y = int(day_iso[:4])
        try:
            return dt.datetime(Y, MM, dd, hh, mm)
        except ValueError:
            return None

    def to_utc_str(dt_naive: dt.datetime | None) -> str | None:
        if not dt_naive: return None
        dt_local = dt_naive.replace(tzinfo=ZoneInfo(LOCAL_TZ))
        return dt_local.astimezone(UTC).strftime("%H:%M")

    s_utc = to_utc_str(parse_ddmm_hhmm(start)) if start else None
    e_utc = to_utc_str(parse_ddmm_hhmm(end)) if end else None

    if s_utc and e_utc:
        return f"{s_utc}–{e_utc} UTC"
    if s_utc: return f"from {s_utc} UTC"
    if e_utc: return f"until {e_utc} UTC"
    return "—"

def pick_tip(advice_list: list[str] | None) -> str:
    if not advice_list: 
        return "60 seconds of slow breathing."
    # берём первую понятную фразу и переводим
    for ru in advice_list:
        base = ru.replace("💼 ","").replace("⛔ ","").replace("🪄 ","").strip().rstrip(".")
        if base in ADVICE_MAP:
            return ADVICE_MAP[base]
    # фолбэк
    return "Light planning, gentle focus."

def energy_line(phase_en: str) -> str:
    p = phase_en.lower()
    if "new moon" in p or "waxing crescent" in p: return "Start small; nurture new ideas."
    if "first quarter" in p: return "Push gently through small obstacles."
    if "waxing gibbous" in p: return "Refine and build momentum."
    if "full moon" in p: return "Observe feelings; celebrate progress."
    if "waning gibbous" in p: return "Share, review, and declutter."
    if "last quarter" in p: return "Close loops and simplify."
    if "waning crescent" in p: return "Rest, reflect, prepare to reset."
    return "Keep plans light; tune into your body."

def main():
    today = dt.date.today().isoformat()
    data = load_calendar()
    entry = pick_today_entry(data, today)

    # дефолты
    phase_name_ru = (entry or {}).get("phase_name") or ""
    percent = (entry or {}).get("percent") or 0
    sign_ru = (entry or {}).get("sign") or ""
    advice = (entry or {}).get("advice") or []

    phase_en = ru_phase_to_en(phase_name_ru, percent)
    sign_en = RU2EN_SIGNS.get(sign_ru, sign_ru or "—")
    voc_str = parse_voc(entry, today)
    tip_en = pick_tip(advice)
    energy = energy_line(phase_en)

    out = {
        "DATE": today,
        "MOON_PHASE": phase_en,
        "MOON_PERCENT": int(percent) if percent is not None else "—",
        "MOON_SIGN": sign_en,
        "VOC_WINDOW_UTC": voc_str,
        "ASTRO_ENERGY_ONE_LINER": energy,
        "ASTRO_TIP": tip_en
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
