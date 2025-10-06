# world_en/world_astro_collect.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

import datetime as dt
import json
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
OUT  = Path(__file__).parent / "astro.json"

# ---- sign mapping (RU/EN → EN + emoji)
SIGN_MAP = {
    # RU
    "Овен": ("Aries", "♈"), "Телец": ("Taurus", "♉"), "Близнецы": ("Gemini", "♊"),
    "Рак": ("Cancer", "♋"), "Лев": ("Leo", "♌"), "Дева": ("Virgo", "♍"),
    "Весы": ("Libra", "♎"), "Скорпион": ("Scorpio", "♏"), "Стрелец": ("Sagittarius", "♐"),
    "Козерог": ("Capricorn", "♑"), "Водолей": ("Aquarius", "♒"), "Рыбы": ("Pisces", "♓"),
    # EN (fallbacks)
    "Aries": ("Aries", "♈"), "Taurus": ("Taurus", "♉"), "Gemini": ("Gemini", "♊"),
    "Cancer": ("Cancer", "♋"), "Leo": ("Leo", "♌"), "Virgo": ("Virgo", "♍"),
    "Libra": ("Libra", "♎"), "Scorpio": ("Scorpio", "♏"), "Sagittarius": ("Sagittarius", "♐"),
    "Capricorn": ("Capricorn", "♑"), "Aquarius": ("Aquarius", "♒"), "Pisces": ("Pisces", "♓"),
}

# RU/EN phase → EN + optional emoji
PHASE_MAP = {
    "Новолуние": ("New Moon", "🌑"), "Полнолуние": ("Full Moon", "🌕"),
    "Первая четверть": ("First Quarter", "🌓"), "Последняя четверть": ("Last Quarter", "🌗"),
    "Растущая Луна": ("Waxing Moon", "🌔"), "Убывающая Луна": ("Waning Moon", "🌖"),
    # EN fallbacks
    "New Moon": ("New Moon", "🌑"), "Full Moon": ("Full Moon", "🌕"),
    "First Quarter": ("First Quarter", "🌓"), "Last Quarter": ("Last Quarter", "🌗"),
    "Waxing": ("Waxing Moon", "🌔"), "Waning": ("Waning Moon", "🌖"),
}

def _sign_en_emoji(sign: Optional[str]):
    if not sign:
        return "—", ""
    en, emoji = SIGN_MAP.get(sign, (sign, ""))
    return en, emoji

def _phase_en_emoji(phase_name: Optional[str]):
    if not phase_name:
        return "—", ""
    # точное совпадение или по ключевому слову
    if phase_name in PHASE_MAP:
        return PHASE_MAP[phase_name]
    low = phase_name.lower()
    for k, v in PHASE_MAP.items():
        if k.lower() in low:
            return v
    return phase_name, ""

# ---------- lunar calendar reading ----------

def read_calendar_today():
    cal_path = ROOT / "lunar_calendar.json"
    if not cal_path.exists():
        return None
    data = json.loads(cal_path.read_text(encoding="utf-8"))
    days = data.get("days") or {}
    today = dt.date.today().isoformat()
    return days.get(today)

def _parse_voc_datetime(s: Optional[str], base_date: dt.date) -> Optional[dt.datetime]:
    # input "04.10 04:32"
    if not s:
        return None
    try:
        dpart, tpart = s.split()
        day, month = map(int, dpart.split("."))
        hour, minute = map(int, tpart.split(":"))
        year = base_date.year
        return dt.datetime(year, month, day, hour, minute)
    except Exception:
        return None

def voc_duration_minutes(voc: dict | None, base_date: dt.date) -> Optional[int]:
    if not voc:
        return None
    start = _parse_voc_datetime(voc.get("start"), base_date)
    end   = _parse_voc_datetime(voc.get("end"), base_date)
    if not start or not end:
        return None
    if end <= start:
        end += dt.timedelta(days=1)
    return int((end - start).total_seconds() // 60)

def pretty_duration(mins: int) -> str:
    h, m = mins // 60, mins % 60
    if h and m: return f"≈{h}h {m:02d}m"
    if h:       return f"≈{h}h"
    return f"≈{m}m"

def format_voc(voc: dict | None) -> str:
    if not voc:
        return "—"
    s = (voc.get("start") or "").split()
    e = (voc.get("end") or "").split()
    ts = s[-1] if s else None
    te = e[-1] if e else None
    if ts and te:
        return f"{ts}–{te}"
    return "—"

def voc_badge(mins: Optional[int]) -> str:
    if mins is None: return ""
    if mins >= 180:  return "🟠"
    if mins >= 60:   return "🟡"
    return ""  # < 60 мин — считаем незначительным

# ---------- energy / tip logic ----------

def base_energy_tip(phase_name_ru: str, percent: int) -> tuple[str, str]:
    pn = (phase_name_ru or "").lower()
    if "новолуние" in pn or "new moon" in pn:
        return ("Set intentions; keep schedule light.", "Rest, plan, one gentle start.")
    if "первая четверть" in pn or "first quarter" in pn:
        return ("Take a clear step forward.", "One priority; short focused block.")
    if "растущ" in pn or "waxing" in pn:
        return ("Build momentum; refine work.", "Polish & iterate for 20–40 min.")
    if "полнолуние" in pn or "full moon" in pn:
        return ("Emotions peak; seek balance.", "Grounding + gratitude; avoid big decisions.")
    if "последняя четверть" in pn or "last quarter" in pn:
        return ("Wrap up & declutter.", "Finish, review, release extras.")
    if "убыва" in pn or "waning" in pn:
        return ("Slow down; restore energy.", "Light tasks, gentle body care.")
    return ("Keep plans light; tune into your body.", "Focus on what matters.")

def energy_and_tip(phase_name_ru: str, percent: int, voc_minutes: Optional[int]) -> tuple[str, str]:
    """VoC aware: ≥180m very light; ≥120m avoid launches; ≥60m flexible."""
    energy, tip = base_energy_tip(phase_name_ru, percent)
    if voc_minutes is None:
        return energy, tip
    dur = pretty_duration(voc_minutes)
    if voc_minutes >= 180:
        return (f"Long VoC ({dur}) — keep schedule very light; avoid launches.",
                "Routine, journaling, cleanup; move decisions after VoC.")
    if voc_minutes >= 120:
        return (f"VoC {dur} — avoid launches; favor routine.",
                "Safe tasks: maintenance, drafts, reading, rest.")
    if voc_minutes >= 60:
        return (f"Short VoC ({dur}) — keep tasks flexible.",
                tip)
    return energy, tip

# ---------- main ----------

def main():
    today = dt.date.today()
    weekday = dt.datetime.utcnow().strftime("%a")
    item = read_calendar_today() or {}

    phase_name  = item.get("phase_name") or ""
    phase_pct   = int(item.get("percent") or 0)
    sign_raw    = item.get("sign") or ""
    voc_block   = item.get("void_of_course")

    voc_text = format_voc(voc_block)
    voc_mins = voc_duration_minutes(voc_block, today)
    voc_len  = pretty_duration(voc_mins) if voc_mins is not None and voc_mins >= 60 else ""

    sign_en, sign_emoji       = _sign_en_emoji(sign_raw)
    phase_en, phase_emoji     = _phase_en_emoji(phase_name)
    energy_line, advice_line  = energy_and_tip(phase_name, phase_pct, voc_mins)

    out = {
        "DATE": today.isoformat(),
        "WEEKDAY": weekday,
        "MOON_PHASE": phase_name or "—",     # оригинал (может быть RU)
        "PHASE_EN": phase_en,                # EN-версия
        "PHASE_EMOJI": phase_emoji,          # эмодзи фазы, где есть
        "MOON_PERCENT": phase_pct if phase_pct is not None else "—",
        "MOON_SIGN": sign_en,
        "MOON_SIGN_EMOJI": sign_emoji,
        "VOC": voc_text,                     # "HH:MM–HH:MM" или "—"
        "VOC_LEN": voc_len,                  # "≈1h 45m" или ""
        "VOC_BADGE": voc_badge(voc_mins),    # 🟠 / 🟡 / ""
        "ENERGY_LINE": energy_line,
        "ADVICE_LINE": advice_line,
    }

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
