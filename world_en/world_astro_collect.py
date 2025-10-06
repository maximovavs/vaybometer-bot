# world_en/world_astro_collect.py
#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from pathlib import Path
import sys
sys.path.append(str(Path(__file__).resolve().parents[1]))

import datetime as dt
import json
import traceback
from typing import Optional, Tuple
from pytz import UTC

ROOT = Path(__file__).resolve().parents[1]
OUT  = Path(__file__).parent / "astro.json"

# ---------------- sign mapping ----------------

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

# в world_en/world_astro_collect.py (вверху файла)
import os

def energy_icon_pick(mode: str, phase_en: str, voc_len_min):
    mode = (mode or "phase").lower()          # phase | voc | static
    if mode == "voc":
        if voc_len_min is None: return "💡"
        return "🟢" if voc_len_min < 60 else ("🟡" if voc_len_min < 120 else "🟠")
    if mode == "static":
        return "💡"
    # phase (по умолчанию)
    return energy_icon_for_phase(phase_en)

def _sign_en_emoji(sign_raw: Optional[str]) -> Tuple[str, str]:
    s = (sign_raw or "").strip()
    if not s:
        return "—", ""
    low = s.lower()
    if low in _RU2EN_SIGNS:
        en = _RU2EN_SIGNS[low]
        return en, _EN_SIGNS[en.lower()][1]
    if low in _EN_SIGNS:
        return _EN_SIGNS[low]
    return s, ""

# ---------------- phase mapping ----------------

_PHASE_LC_MAP = {
    # RU
    "новолуние":            ("New Moon", "🌑"),
    "растущий серп":        ("Waxing Crescent", "🌒"),
    "первая четверть":      ("First Quarter", "🌓"),
    "растущая луна":        ("Waxing Moon", "🌔"),
    "растущая":             ("Waxing Moon", "🌔"),
    "полнолуние":           ("Full Moon", "🌕"),
    "убывающая луна":       ("Waning Moon", "🌖"),
    "убывающая":            ("Waning Moon", "🌖"),
    "последняя четверть":   ("Last Quarter", "🌗"),
    "убывающий серп":       ("Waning Crescent", "🌘"),
    # EN
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
    if not phase_name:
        return "—", ""
    low = phase_name.strip().lower()
    if low in _PHASE_LC_MAP:
        return _PHASE_LC_MAP[low]
    for key, val in _PHASE_LC_MAP.items():
        if key in low:
            return val
    return phase_name, ""

def energy_icon_for_phase(phase_label: str) -> str:
    s = (phase_label or "").lower()
    if "new moon" in s or "новолуние" in s:        return "🌑"
    if "full moon" in s or "полнолуние" in s:      return "🌕"
    if "first quarter" in s or "первая четверть" in s:  return "🌓"
    if "last quarter" in s or "последняя четверть" in s: return "🌗"
    if "waxing" in s or "растущ" in s:             return "🌔"
    if "waning" in s or "убыва" in s:              return "🌘"
    return "🔆"

# ---------------- helpers ----------------

def fmt_percent_or_none(x) -> Optional[int]:
    try:
        p = int(round(float(x)))
    except Exception:
        return None
    return p if 0 < p < 100 else None

def parse_voc_utc(start_s: Optional[str], end_s: Optional[str]) -> Tuple[Optional[dt.datetime], Optional[dt.datetime]]:
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
    if minutes is None: return ""
    if minutes >= 120:  return "🟠"
    if minutes >= 60:   return "🟡"
    return "🟢"

def voc_text_status(start_utc: Optional[dt.datetime],
                    end_utc: Optional[dt.datetime]) -> Tuple[str, str, Optional[int]]:
    if not start_utc or not end_utc:
        return "No VoC today UTC", "", None
    total_min = max(0, int((end_utc - start_utc).total_seconds() // 60))
    pretty = pretty_duration(total_min)
    rng = f"{start_utc.strftime('%H:%M')}–{end_utc.strftime('%H:%M')} UTC"
    now = dt.datetime.utcnow().replace(tzinfo=UTC)
    if now < start_utc:
        return f"VoC later today — {rng} ({pretty})", voc_badge_by_len(total_min), total_min
    if start_utc <= now <= end_utc:
        return f"VoC now — {rng} ({pretty})", voc_badge_by_len(total_min), total_min
    return f"VoC earlier today — {rng} ({pretty})", "", total_min

# ---------------- calendar IO ----------------

def read_calendar_today():
    cal_path = ROOT / "lunar_calendar.json"
    if not cal_path.exists():
        return None
    data = json.loads(cal_path.read_text(encoding="utf-8"))
    days = data.get("days") or {}
    today = dt.date.today().isoformat()
    return days.get(today)

# ---------------- energy ----------------

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

# ---------------- safe writer ----------------

def write_json_safe(path: Path, payload: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[astro] wrote: {path}")

# ---------------- main ----------------

def main():
    today = dt.date.today()
    weekday = dt.datetime.utcnow().strftime("%a")

    item = read_calendar_today() or {}

    phase_name = item.get("phase_name") or ""
    phase_pct  = item.get("percent")
    sign_raw   = item.get("sign") or ""
    voc_block  = item.get("void_of_course") or {}

    voc_start_str = (voc_block or {}).get("start")
    voc_end_str   = (voc_block or {}).get("end")
    start_utc, end_utc = parse_voc_utc(voc_start_str, voc_end_str)
    VOC_TEXT, VOC_BADGE_SMART, VOC_LEN_MIN = voc_text_status(start_utc, end_utc)
    VOC_LEN_PRETTY = pretty_duration(VOC_LEN_MIN) if isinstance(VOC_LEN_MIN, int) else ""

    sign_en, sign_emoji   = _sign_en_emoji(sign_raw)
    phase_en, phase_emoji = _phase_en_emoji(phase_name)
    energy_icon = energy_icon_pick(os.getenv("ENERGY_ICON_MODE","phase"), phase_en, VOC_LEN_MIN)

    energy_icon = energy_icon_for_phase(phase_en or phase_name)
    energy_line, advice_line = energy_and_tip(phase_name, int(phase_pct or 0), VOC_LEN_MIN)

    out = {
        "DATE": today.isoformat(),
        "WEEKDAY": weekday,
        "MOON_PHASE": phase_name or "—",
        "PHASE_EN": phase_en,
        "PHASE_EMOJI": phase_emoji,
        "MOON_PERCENT": fmt_percent_or_none(phase_pct),
        "MOON_SIGN": sign_en,
        "MOON_SIGN_EMOJI": sign_emoji,
        "VOC": VOC_TEXT,
        "VOC_TEXT": VOC_TEXT,
        "VOC_LEN": VOC_LEN_PRETTY,
        "VOC_BADGE": VOC_BADGE_SMART,
        "ENERGY_LINE": energy_line,
        "ENERGY_ICON": energy_icon,
        "ADVICE_LINE": advice_line,
    }

    write_json_safe(OUT, out)

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        # Фолбэк, чтобы не ронять пайплайн
        fb = {
            "DATE": dt.date.today().isoformat(),
            "WEEKDAY": dt.datetime.utcnow().strftime("%a"),
            "MOON_PHASE": "—",
            "PHASE_EN": "—",
            "PHASE_EMOJI": "",
            "MOON_PERCENT": None,
            "MOON_SIGN": "—",
            "MOON_SIGN_EMOJI": "",
            "VOC": "No VoC today UTC",
            "VOC_TEXT": "No VoC today UTC",
            "VOC_LEN": "",
            "VOC_BADGE": "",
            "ENERGY_LINE": "Keep plans light; tune into your body.",
            "ENERGY_ICON": "🔆",
            "ADVICE_LINE": "Focus on what matters.",
            "_error": f"{type(e).__name__}: {e}",
        }
        print("[astro] ERROR during collect:\n" + "".join(traceback.format_exc()))
        write_json_safe(OUT, fb)
