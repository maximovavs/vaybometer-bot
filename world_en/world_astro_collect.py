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
_RU2EN_SIGNS = {
    "овен": "Aries", "телец": "Taurus", "близнецы": "Gemini",
    "рак": "Cancer", "лев": "Leo", "дева": "Virgo",
    "весы": "Libra", "скорпион": "Scorpio", "стрелец": "Sagittarius",
    "козерог": "Capricorn", "водолей": "Aquarius", "рыбы": "Pisces",
}
_EN_SIGNS = {
    "aries": ("Aries", "♈"), "taurus": ("Taurus", "♉"),
    "gemini": ("Gemini", "♊"), "cancer": ("Cancer", "♋"),
    "leo": ("Leo", "♌"), "virgo": ("Virgo", "♍"),
    "libra": ("Libra", "♎"), "scorpio": ("Scorpio", "♏"),
    "sagittarius": ("Sagittarius", "♐"), "capricorn": ("Capricorn", "♑"),
    "aquarius": ("Aquarius", "♒"), "pisces": ("Pisces", "♓"),
}

def _sign_en_emoji(sign_raw: Optional[str]) -> Tuple[str, str]:
    """Принимает рус/англ название знака и возвращает (EN, emoji)."""
    s = (sign_raw or "").strip()
    if not s:
        return "—", ""
    low = s.lower()
    # RU → EN
    if low in _RU2EN_SIGNS:
        en = _RU2EN_SIGNS[low]
        return en, _EN_SIGNS[en.lower()][1]
    # EN как есть
    if low in _EN_SIGNS:
        return _EN_SIGNS[low]
    # фолбэк
    return s, ""

# ---- phase mapping (RU/EN → EN + emoji для всех стадий)
# Поддержаны все основные стадии: 🌑/🌒/🌓/🌔/🌕/🌖/🌗/🌘
_PHASE_LC_MAP = {
    # RU точные/ключевые
    "новолуние":            ("New Moon", "🌑"),
    "растущий серп":        ("Waxing Crescent", "🌒"),
    "первая четверть":      ("First Quarter", "🌓"),
    "растущая луна":        ("Waxing Moon", "🌔"),      # обобщённо
    "растущая":             ("Waxing Moon", "🌔"),
    "полнолуние":           ("Full Moon", "🌕"),
    "убывающая луна":       ("Waning Moon", "🌖"),      # обобщённо
    "убывающая":            ("Waning Moon", "🌖"),
    "последняя четверть":   ("Last Quarter", "🌗"),
    "убывающий серп":       ("Waning Crescent", "🌘"),

    # EN точные/ключевые
    "new moon":             ("New Moon", "🌑"),
    "waxing crescent":      ("Waxing Crescent", "🌒"),
    "first quarter":        ("First Quarter", "🌓"),
    "waxing gibbous":       ("Waxing Gibbous", "🌔"),
    "waxing":               ("Waxing Moon", "🌔"),
    "full moon":            ("Full Moon", "🌕"),
    "waning gibbous":       ("Waning Gibbous", "🌖"),
    "last quarter":         ("Last Quarter", "🌗"),
    "waning crescent":      ("Waning Crescent", "🌘"),
    "waning":               ("Waning Moon", "🌖"),
}

def _phase_en_emoji(phase_name: Optional[str]) -> Tuple[str, str]:
    """Вернёт (EN-название, emoji). Ищет по точному совпадению и по ключевому слову (case-insensitive)."""
    if not phase_name:
        return "—", ""
    low = phase_name.strip().lower()
    # точное совпадение
    if low in _PHASE_LC_MAP:
        return _PHASE_LC_MAP[low]
    # поиск по ключевым словам
    for key, val in _PHASE_LC_MAP.items():
        if key in low:
            return val
    # фолбэк — оригинал без эмодзи
    return phase_name, ""

# ---------- helpers ----------

def fmt_percent_or_none(x) -> Optional[int]:
    """Вернёт целое 1..99, иначе None (для скрытия скобок в шаблоне)."""
    try:
        p = int(round(float(x)))
    except Exception:
        return None
    return p if 0 < p < 100 else None

def parse_voc_utc(start_s: Optional[str], end_s: Optional[str]) -> Tuple[Optional[dt.datetime], Optional[dt.datetime]]:
    """
    Принимает строки 'HH:MM' или 'DD.MM HH:MM' (UTC) и возвращает aware datetime в UTC.
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
        else:         # 'HH:MM' -> сегодня
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
    Варианты:
      - 'No VoC today'
      - 'VoC passed earlier today (HH:MM–HH:MM UTC)'
      - 'VoC now HH:MM–HH:MM UTC (≈1h 45m)'
      - 'HH:MM–HH:MM UTC (≈1h 45m)' — если ещё не началось
    """
    if not start_utc or not end_utc:
        return "No VoC today", "", None

    total_min = max(0, int((end_utc - start_utc).total_seconds() // 60))
    badge = voc_badge_by_len(total_min)
    pretty = pretty_duration(total_min)
    rng = f"{start_utc.strftime('%H:%M')}–{end_utc.strftime('%H:%M')} UTC"

    now = dt.datetime.utcnow().replace(tzinfo=UTC)
    if now < start_utc:
        return f"{rng} ({pretty})", badge, total_min
    if start_utc <= now <= end_utc:
        return f"VoC now {rng} ({pretty})", badge, total_min
    return f"VoC passed earlier today ({rng})", "⚪️", total_min

# ---------- lunar calendar reading ----------

def read_calendar_today():
    cal_path = ROOT / "lunar_calendar.json"
    if not cal_path.exists():
        return None
    data = json.loads(cal_path.read_text(encoding="utf-8"))
    days = data.get("days") or {}
    today = dt.date.today().isoformat()
    return days.get(today)

# (совместимость со старыми помощниками: локальный парсер VoC в календаре)
def _parse_voc_datetime(s: Optional[str], base_date: dt.date) -> Optional[dt.datetime]:
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
    energy, tip = base_energy_tip(phase_name_ru, percent)
    if voc_minutes is None:
        return energy, tip
    if voc_minutes >= 180:
        return ("Long VoC — keep schedule very light; avoid launches.",
                "Routine, journaling, cleanup; move decisions after VoC.")
    if voc_minutes >= 120:
        return ("VoC — avoid launches; favor routine.",
                "Safe tasks: maintenance, drafts, reading, rest.")
    if voc_minutes >= 60:
        return ("Short VoC — keep tasks flexible.", tip)
    return energy, tip

# ---------- main ----------

def main():
    today = dt.date.today()
    weekday = dt.datetime.utcnow().strftime("%a")

    item = read_calendar_today() or {}

    # исходники из календаря
    phase_name  = item.get("phase_name") or ""          # RU-строка фазы (может быть пусто)
    phase_pct   = item.get("percent")                   # число 0..100 (может быть None/"")
    sign_raw    = item.get("sign") or ""                # RU- или EN-название знака
    voc_block   = item.get("void_of_course") or {}      # {"start": "...", "end": "..."}

    # --- VoC: умные статусы (no / passed / now / upcoming) ---
    voc_start_str = (voc_block or {}).get("start")
    voc_end_str   = (voc_block or {}).get("end")
    start_utc, end_utc = parse_voc_utc(voc_start_str, voc_end_str)
    VOC_TEXT, VOC_BADGE_SMART, VOC_LEN_MIN = voc_text_status(start_utc, end_utc)
    VOC_LEN_PRETTY = pretty_duration(VOC_LEN_MIN) if isinstance(VOC_LEN_MIN, int) else ""

    # --- Луна: EN-названия и эмодзи ---
    sign_en, sign_emoji   = _sign_en_emoji(sign_raw)
    phase_en, phase_emoji = _phase_en_emoji(phase_name)

    # Энергия/совет без дублирования длительности (внутри не вставляем время VoC)
    energy_line, advice_line = energy_and_tip(phase_name, int(phase_pct or 0), VOC_LEN_MIN)

    out = {
        "DATE": today.isoformat(),
        "WEEKDAY": weekday,

        # Луна
        "MOON_PHASE": phase_name or "—",                 # оригинал (может быть RU)
        "PHASE_EN": phase_en,                            # EN-название фазы
        "PHASE_EMOJI": phase_emoji,                      # эмодзи фазы (все стадии поддержаны)
        "MOON_PERCENT": fmt_percent_or_none(phase_pct),  # скрываем 0%/100%
        "MOON_SIGN": sign_en,
        "MOON_SIGN_EMOJI": sign_emoji,

        # VoC
        "VOC": VOC_TEXT,                 # для обратной совместимости
        "VOC_TEXT": VOC_TEXT,            # умный текст
        "VOC_LEN": VOC_LEN_PRETTY,       # "≈1h 45m" или ""
        "VOC_BADGE": VOC_BADGE_SMART,    # 🟢/🟡/🟠/⚪️

        # Энергия/совет
        "ENERGY_LINE": energy_line,
        "ADVICE_LINE": advice_line,
    }

    OUT.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
