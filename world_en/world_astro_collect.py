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

# --- маппинги знаков (RU/EN) → EN + emoji
SIGN_MAP = {
    # RU → (EN, emoji)
    "Овен": ("Aries", "♈"), "Телец": ("Taurus", "♉"), "Близнецы": ("Gemini", "♊"),
    "Рак": ("Cancer", "♋"), "Лев": ("Leo", "♌"), "Дева": ("Virgo", "♍"),
    "Весы": ("Libra", "♎"), "Скорпион": ("Scorpio", "♏"), "Стрелец": ("Sagittarius", "♐"),
    "Козерог": ("Capricorn", "♑"), "Водолей": ("Aquarius", "♒"), "Рыбы": ("Pisces", "♓"),
    # EN → (EN, emoji) на всякий
    "Aries": ("Aries", "♈"), "Taurus": ("Taurus", "♉"), "Gemini": ("Gemini", "♊"),
    "Cancer": ("Cancer", "♋"), "Leo": ("Leo", "♌"), "Virgo": ("Virgo", "♍"),
    "Libra": ("Libra", "♎"), "Scorpio": ("Scorpio", "♏"), "Sagittarius": ("Sagittarius", "♐"),
    "Capricorn": ("Capricorn", "♑"), "Aquarius": ("Aquarius", "♒"), "Pisces": ("Pisces", "♓"),
}

def _sign_en_emoji(sign: Optional[str]):
    if not sign:
        return "—", ""
    en, emoji = SIGN_MAP.get(sign, (sign, ""))
    return en, emoji

# Энергия/совет по фазе (RU/EN поддержка)
def energy_and_tip(phase_name_ru: str, percent: int) -> tuple[str, str]:
    pn = (phase_name_ru or "").lower()
    # ключевые слова RU/EN
    if "новолуние" in pn or "new moon" in pn:
        return ("Set intentions, keep schedule light.", "Rest, plan, one gentle start.")
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
    # дефолт
    return ("Keep plans light; tune into your body.", "Focus on what matters.")

def read_calendar_today():
    """Читает lunar_calendar.json и отдаёт данные за сегодня, если есть."""
    cal_path = ROOT / "lunar_calendar.json"
    if not cal_path.exists():
        return None
    data = json.loads(cal_path.read_text(encoding="utf-8"))
    days = data.get("days") or {}
    today = dt.date.today().isoformat()
    return days.get(today)

def format_voc(voc: dict | None) -> str:
    if not voc:
        return "—"
    start = voc.get("start")
    end   = voc.get("end")
    # В календаре формат типа "04.10 04:32" — берём время после пробела
    def only_time(s: Optional[str]) -> Optional[str]:
        if not s: return None
        parts = s.split()
        return parts[-1] if parts else s
    s = only_time(start)
    e = only_time(end)
    if s and e:
        return f"{s}–{e}"
    return "—"

def main():
    today = dt.date.today()
    weekday = dt.datetime.utcnow().strftime("%a")
    item = read_calendar_today() or {}

    # Из календаря (RU)
    phase_ru  = item.get("phase_name") or ""
    phase_pct = item.get("percent") or 0
    sign_ru   = item.get("sign") or ""
    voc_text  = format_voc(item.get("void_of_course"))

    # Конвертируем знак к EN + emoji
    sign_en, sign_emoji = _sign_en_emoji(sign_ru)

    # Энергия/совет
    energy_line, advice_line = energy_and_tip(phase_ru, int(phase_pct or 0))

    out = {
        "DATE": today.isoformat(),
        "WEEKDAY": weekday,
        "MOON_PHASE": phase_ru if phase_ru else "—",
        "MOON_PERCENT": phase_pct if phase_pct is not None else "—",
        "MOON_SIGN": sign_en,
        "MOON_SIGN_EMOJI": sign_emoji,
        "VOC": voc_text,
        "ENERGY_LINE": energy_line,
        "ADVICE_LINE": advice_line,
    }

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")

if __name__ == "__main__":
    main()
