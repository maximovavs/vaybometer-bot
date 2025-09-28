#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json, datetime as dt, re
from pathlib import Path

OUT = Path(__file__).parent / "astro.json"
ROOT = Path(__file__).resolve().parents[1]

RU2EN = {
    "Овен":"Aries","Телец":"Taurus","Близнецы":"Gemini","Рак":"Cancer","Лев":"Leo","Дева":"Virgo",
    "Весы":"Libra","Скорпион":"Scorpio","Стрелец":"Sagittarius","Козерог":"Capricorn","Водолей":"Aquarius","Рыбы":"Pisces"
}

def _load_lunar_json():
    for p in [ROOT/"lunar_calendar.json", ROOT/"data"/"lunar_calendar.json"]:
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                pass
    return None

def _norm_date(s:str) -> str:
    m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
    if m: return m.group(1)
    m = re.search(r"(\d{2})[.\-/](\d{2})[.\-/](\d{4})", s)
    if m: return f"{m.group(3)}-{m.group(2)}-{m.group(1)}"
    return s

def _find_today_entry(data, today_iso: str):
    if isinstance(data, dict):
        # может быть словарём с ключами дат
        cand = data.get(today_iso) or data.get(today_iso.replace("-","/"))
        if cand: return cand
        # или массив внутри
        data = data.get("days") or data.get("items") or data.get("records") or []
    if isinstance(data, list):
        for it in data:
            d = _norm_date(str(it.get("date") or it.get("day") or it.get("dt") or ""))
            if d == today_iso:
                return it
    return None

def main():
    today = dt.date.today().isoformat()
    entry = _load_lunar_json()
    e = _find_today_entry(entry, today) if entry else None

    moon_sign = "—"
    voc = "—"

    if e:
        sign = e.get("moon_sign") or e.get("sign") or e.get("moonSign") or e.get("sign_ru") or e.get("sign_en")
        if sign:
            moon_sign = RU2EN.get(sign, sign)
        # пробуем разные ключи для VoC
        for k in ("void_of_course","VoC","voc","voidCourse","void"):
            if k in e:
                val = e[k]
                if isinstance(val, dict):
                    start = val.get("start_utc") or val.get("start") or val.get("from")
                    end   = val.get("end_utc")   or val.get("end")   or val.get("to")
                    if start and end:
                        voc = f"{start}–{end} UTC"
                        break
                elif isinstance(val, str) and re.search(r"\d{2}:\d{2}", val):
                    voc = val
                    break

    # базовая подсказка
    tip = "60 seconds of slow breathing. Postpone heavy decisions." if voc != "—" else "Light planning, gentle focus."

    out = {
        "DATE": today,
        "MOON_PHASE": "—",          # можно добавить через astral.moon.phase
        "MOON_PERCENT": "—",
        "MOON_SIGN": moon_sign,
        "VOC_WINDOW_UTC": voc,
        "ASTRO_ENERGY_ONE_LINER": "Keep plans light; tune into your body.",
        "ASTRO_TIP": tip
    }
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
