# world_en/world_astro_collect.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

import datetime as dt
import json
from typing import Optional, Tuple
from pytz import UTC

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

# ---- phase mapping
# Точное соответствие фраз → (EN-название, emoji).
# Для "Растущая/Убывающая" дополнительно ниже учитываем процент, чтобы выбрать Crescent/Gibbous.
PHASE_EXACT = {
    "Новолуние": ("New Moon", "🌑"),
    "Полнолуние": ("Full Moon", "🌕"),
    "Первая четверть": ("First Quarter", "🌓"),
    "Последняя четверть": ("Last Quarter", "🌗"),
    # EN fallbacks
    "New Moon": ("New Moon", "🌑"),
    "Full Moon": ("Full Moon", "🌕"),
    "First Quarter": ("First Quarter", "🌓"),
    "Last Quarter": ("Last Quarter", "🌗"),
}
# ключевые слова для распознавания растущей/убывающей
KW_WAXING = ("Растущ", "Waxing")
KW_WANING = ("Убыва", "Waning")

def fmt_percent_or_none(x) -> Optional[int]:
    """Вернёт целое 1..99, иначе None (скроет скобки в шаблоне)."""
    try:
        p = int(round(float(x)))
    except Exception:
        return None
    return p if 0 < p < 100 else None

# ---------- VoC parsing / status ----------

def parse_voc_utc(start_s: Optional[str], end_s: Optional[str]) -> Tuple[Optional[dt.datetime], Optional[dt.datetime]]:
    """
    Принимает строки 'HH:MM' или 'DD.MM HH:MM' (UTC) и возвращает aware-datetime в UTC.
    Если нет данных — (None, None).
    """
    if not start_s or not end_s:
        return None, None

    def _parse_one(s: str) -> dt.datetime:
        s = s.strip()
        today = dt.datetime.utcnow().date()
        if " " in s:  # 'DD.MM HH:MM'
            dpart, tpart = s.split()
            d, m = map(int, dpart.split("."))
            hh, mm = map(int, tpart.split(":"))
            return dt.datetime(today.year, m, d, hh, mm, tzinfo=UTC)
        else:         # 'HH:MM'
            hh, mm = map(int, s.split(":"))
            return dt.datetime(today.year, today.month, today.day, hh, mm, tzinfo=UTC)

    try:
        return _parse_one(start_s), _parse_one(end_s)
    except Exception:
        return None, None

def pretty_duration(mins: int) -> str:
    h, m = mins // 60, mins % 60
    if h and m: return f"≈{h}h {m:02d}m"
    if h:       return f"≈{h}h"
    return f"≈{m}m"

def voc_badge_by_len(minutes: int) -> str:
    if minutes >= 120: return "🟠"
    if minutes >= 60:  return "🟡"
    return "🟢"

def voc_text_status(start_utc: Optional[dt.datetime], end_utc: Optional[dt.datetime]) -> Tuple[str, str, Optional[int]]:
    """
    Возвращает (VOC_TEXT, VOC_BADGE, VOC_LEN_MIN).
    Варианты текста:
     - 'No VoC today'
     - 'VoC passed earlier today (HH:MM–HH:MM UTC)'
     - 'VoC now HH:MM–HH:MM UTC (≈1h 45m)'
     - 'HH:MM–HH:MM UTC (≈1h 45m)' — если ещё не началось
    """
    if not start_utc or not end_utc:
        return "No VoC today", "", None

    total_min = max(0, int((end_utc - start_utc).total_seconds() // 60))
    badge_len = voc_badge_by_len(total_min)
    rng = f"{start_utc.strftime('%H:%M')}–{end_utc.strftime('%H:%M')} UTC"
    pretty = pretty_duration(total_min)

    now = dt.datetime.utcnow().replace(tzinfo=UTC)
    if now < start_utc:
        return f"{rng} ({pretty})", badge_len, total_min
    if start_utc <= now <= end_utc:
        return f"VoC now {rng} ({pretty})", badge_len, total_min
    return f"VoC passed earlier today ({rng})", "⚪️", total_min

def voc_minutes_if_active(start_utc: Optional[dt.datetime],
                          end_utc: Optional[dt.datetime]) -> Optional[int]:
    """Вернёт длительность окна в минутах, но только если VoC идёт прямо сейчас."""
    if not start_utc or not end_utc:
        return None
    now = dt.datetime.utcnow().replace(tzinfo=UTC)
    if start_utc <= now <= end_utc:
        return int((end_utc - start_utc).total_seconds() // 60)
    return None

# ---------- lunar calendar reading ----------

def read_calendar_today():
    cal_path = ROOT / "lunar_calendar.json"
    if not cal_path.exists():
        return None
    data = json.loads(cal_path.read_text(encoding="utf-8"))
    days = data.get("days") or {}
    today = dt.date.today().isoformat()
    return days.get(today)

# ---------- sign / phase helpers ----------

def _sign_en_emoji(sign: Optional[str]):
    if not sign:
        return "—", ""
    en, emoji = SIGN_MAP.get(sign, (sign, ""))
    return en, emoji

def _phase_from_name_and_percent(name: Optional[str], percent: Optional[int]):
    """
    Возвращает (EN, emoji) для фазы.
    Для 'Растущая/Убывающая' учитывает процент, чтобы выбрать Crescent/Gibbous.
    """
    if not name:
        return "—", ""

    # точное совпадение (четверти/новолуние/полнолуние)
    if name in PHASE_EXACT:
        return PHASE_EXACT[name]

    # EN точные
    if name in PHASE_EXACT:
        return PHASE_EXACT[name]

    low = name.lower()

    # растущая
    if any(k.lower() in low for k in KW_WAXING):
        p = None
        try:
            p = int(percent) if percent is not None else None
        except Exception:
            p = None
        if p is not None and p < 50:
            return "Waxing Crescent", "🌒"
        else:
            return "Waxing Gibbous", "🌔"

    # убывающая
    if any(k.lower() in low for k in KW_WANING):
        p = None
        try:
            p = int(percent) if percent is not None else None
        except Exception:
            p = None
        if p is not None and p > 50:
            return "Waning Gibbous", "🌖"
        else:
            return "Waning Crescent", "🌘"

    # fallback — вернём исходник без эмодзи
    return name, ""

# ---------- energy / tip logic ----------

def base_energy_tip(phase_name_ru: str, percent: Optional[int]) -> tuple[str, str]:
    pn = (phase_name_ru or "").lower()
    if "новолуние" in pn or "new moon" in pn:
        return ("Set intentions; keep schedule light.", "Rest, plan, one gentle start.")
    if "первая четверть" in pn or "first quarter" in pn:
        return ("Take a clear step forward.", "One priority; short focused block.")
    if "растущ" in pn or "waxing" in pn:
        # разделять на crescent/gibbous не критично для текста
        return ("Build momentum; refine work.", "Polish & iterate for 20–40 min.")
    if "полнолуние" in pn or "full moon" in pn:
        return ("Emotions peak; seek balance.", "Grounding + gratitude; avoid big decisions.")
    if "последняя четверть" in pn or "last quarter" in pn:
        return ("Wrap up & declutter.", "Finish, review, release extras.")
    if "убыва" in pn or "waning" in pn:
        return ("Slow down; restore energy.", "Light tasks, gentle body care.")
    return ("Keep plans light; tune into your body.", "Focus on what matters.")

def energy_icon_for_phase(phase_en: str) -> str:
    """Лёгкая иконка энергии по фазам — используется рядом со словом Energy."""
    pe = (phase_en or "").lower()
    if "new moon" in pe: return "🌑"
    if "first quarter" in pe: return "🌓"
    if "waxing crescent" in pe: return "🌒"
    if "waxing gibbous" in pe: return "🌔"
    if "full moon" in pe: return "🌕"
    if "last quarter" in pe: return "🌗"
    if "waning gibbous" in pe: return "🌖"
    if "waning crescent" in pe: return "🌘"
    return ""

def energy_and_tip(phase_name_ru: str, percent: Optional[int], voc_minutes_active: Optional[int]) -> tuple[str, str]:
    """
    Возвращает (energy_line, tip_line).
    Если VoC активен сейчас — учитываем его длительность,
    иначе даём базовые рекомендации по фазе.
    """
    if voc_minutes_active is not None:
        if voc_minutes_active >= 180:
            return ("Long VoC — keep schedule very light; avoid launches.",
                    "Routine, journaling, cleanup; move decisions after VoC.")
        if voc_minutes_active >= 120:
            return ("VoC — avoid launches; favor routine.",
                    "Safe tasks: maintenance, drafts, reading, rest.")
        if voc_minutes_active >= 60:
            return ("Short VoC — keep tasks flexible.",
                    "Gentle pace; soft focus & breaks.")
    # базовые по фазе
    return base_energy_tip(phase_name_ru, percent)

# ---------- main ----------

def main():
    today = dt.date.today()
    weekday = dt.datetime.utcnow().strftime("%a")

    item = read_calendar_today() or {}

    # исходники из календаря
    phase_name  = item.get("phase_name") or ""          # RU фаза
    phase_pct   = item.get("percent")                   # 0..100 (может быть None/"")
    sign_raw    = item.get("sign") or ""                # RU/EN знак
    voc_block   = item.get("void_of_course") or {}      # {"start":"...", "end":"..."}

    # --- VoC: умный статус (no / later / now / earlier) ---
    voc_start_str = (voc_block or {}).get("start")
    voc_end_str   = (voc_block or {}).get("end")
    start_utc, end_utc = parse_voc_utc(voc_start_str, voc_end_str)
    VOC_TEXT, VOC_BADGE, VOC_LEN_MIN = voc_text_status(start_utc, end_utc)
    VOC_LEN_PRETTY = pretty_duration(VOC_LEN_MIN) if isinstance(VOC_LEN_MIN, int) else ""

    # --- Луна: EN-название и эмодзи (с учётом процента для crescent/gibbous) ---
    sign_en,  sign_emoji   = _sign_en_emoji(sign_raw)
    phase_en, phase_emoji  = _phase_from_name_and_percent(phase_name, phase_pct)
    energy_icon            = energy_icon_for_phase(phase_en or phase_name)

    # Энергия/совет: учитываем VoC ТОЛЬКО если оно активно сейчас
    voc_active_mins = voc_minutes_if_active(start_utc, end_utc)
    energy_line, advice_line = energy_and_tip(phase_name, int(phase_pct or 0), voc_active_mins)

    out = {
        "DATE": today.isoformat(),
        "WEEKDAY": weekday,

        # Луна
        "MOON_PHASE": phase_name or "—",
        "PHASE_EN": phase_en,
        "PHASE_EMOJI": phase_emoji,
        "MOON_PERCENT": fmt_percent_or_none(phase_pct),
        "MOON_SIGN": sign_en,
        "MOON_SIGN_EMOJI": sign_emoji,

        # VoC
        "VOC": VOC_TEXT,               # обратная совместимость
        "VOC_TEXT": VOC_TEXT,
        "VOC_LEN": VOC_LEN_PRETTY,
        "VOC_BADGE": VOC_BADGE,
        "VOC_IS_ACTIVE": voc_active_mins is not None,

        # Энергия/совет
        "ENERGY_ICON": energy_icon,
        "ENERGY_LINE": energy_line,
        "ADVICE_LINE": advice_line,
    }

    # Пишем ТОЛЬКО astro.json
    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[astro] wrote {OUT} ({OUT.stat().st_size} bytes)")

if __name__ == "__main__":
    main()
