#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
send_monthly_calendar.py (Cyprus-ready)

Постит месячный лунный календарь в Telegram.
Работает и с кипрскими, и с калининградскими именами переменных окружения.

Приоритет выбора чата:
1) CHANNEL_ID_OVERRIDE (если задан)
2) Если TO_TEST ∈ {1,true,yes,on} → CHANNEL_ID_TEST
3) Иначе → CHANNEL_ID
Фолбэк для совместимости: *_KLG.

Токен:
- TELEGRAM_TOKEN (основной)
- fallback: TELEGRAM_TOKEN_KLG

Отправляет:
1) Текстовое резюме месяца
2) Файл lunar_calendar.json как документ
"""

from __future__ import annotations
import os
import json
from pathlib import Path
from typing import Any, Dict, List, Tuple

import requests
import pendulum

# ───────────────────────── env / конфиг ─────────────────────────

def _envb(name: str) -> bool:
    return (os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on"))

TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_TOKEN_KLG") or ""
if not TOKEN:
    raise RuntimeError("TELEGRAM_TOKEN не задан (или TELEGRAM_TOKEN_KLG)")

OVERRIDE = (os.getenv("CHANNEL_ID_OVERRIDE") or "").strip()
TO_TEST  = _envb("TO_TEST")

CHAT = ""
if OVERRIDE:
    CHAT = OVERRIDE
else:
    if TO_TEST:
        CHAT = os.getenv("CHANNEL_ID_TEST", "")
        if not CHAT:
            # совместимость с KLD
            CHAT = os.getenv("CHANNEL_ID_TEST_KLG", "")
    if not CHAT:
        CHAT = os.getenv("CHANNEL_ID", "") or os.getenv("CHANNEL_ID_KLG", "")

if not CHAT:
    raise RuntimeError("CHANNEL_ID не задан (или CHANNEL_ID_KLG), а также нет CHANNEL_ID_TEST при TO_TEST=1")

TZ = pendulum.timezone(os.getenv("TZ", "Asia/Nicosia"))
JSON_PATH = Path(os.getenv("LUNAR_JSON_PATH", "lunar_calendar.json"))

# ───────────────────────── helpers ──────────────────────────────

RUS_MONTHS_NOM = {
    1: "январь", 2: "февраль", 3: "март", 4: "апрель",
    5: "май", 6: "июнь", 7: "июль", 8: "август",
    9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
}

PHASE_EMO = {
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
    "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘",
}

def tg(method: str, **data: Any) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{TOKEN}/{method}"
    r = requests.post(url, data=data, timeout=30)
    r.raise_for_status()
    return r.json()

def tg_send_message(chat_id: str, text: str, parse_mode: str = "HTML", disable_web_page_preview: bool = True):
    return tg("sendMessage", chat_id=chat_id, text=text, parse_mode=parse_mode,
              disable_web_page_preview=str(disable_web_page_preview).lower())

def tg_send_document(chat_id: str, file_path: Path, caption: str = ""):
    url = f"https://api.telegram.org/bot{TOKEN}/sendDocument"
    with file_path.open("rb") as f:
        files = {"document": (file_path.name, f, "application/json")}
        data = {"chat_id": chat_id, "caption": caption}
        r = requests.post(url, data=data, files=files, timeout=60)
        r.raise_for_status()
        return r.json()

# ───────────────────────── форматирование ───────────────────────

def _summarize_calendar(cal: Dict[str, Any]) -> str:
    days: Dict[str, Any] = cal.get("days", {})
    if not days:
        return "🌙 <b>Лунный календарь</b>\nДанные не найдены."

    # определим месяц/год по любой дате из словаря
    sample_date = sorted(days.keys())[0]
    dt = pendulum.parse(sample_date, tz=TZ)
    month_name = RUS_MONTHS_NOM.get(dt.month, f"{dt.month:02d}")
    year = dt.year

    # соберём ключевые фазы и даты
    phase_dates: Dict[str, List[int]] = {k: [] for k in ("Новолуние","Первая четверть","Полнолуние","Последняя четверть")}
    for dstr, rec in sorted(days.items()):
        ph = str(rec.get("phase_name") or "")
        if ph in phase_dates:
            phase_dates[ph].append(int(dstr[-2:]))

    # VoC статистика
    month_voc = cal.get("month_voc", []) or []
    voc_lines: List[str] = []
    for it in month_voc[:5]:
        s, e = it.get("start"), it.get("end")
        if s and e:
            voc_lines.append(f"• {s}–{e}")

    lines: List[str] = []
    lines.append(f"🌙 <b>Лунный календарь на {month_name} {year}</b>")
    lines.append("")
    for name in ("Новолуние","Первая четверть","Полнолуние","Последняя четверть"):
        dates = phase_dates[name]
        if dates:
            emo = PHASE_EMO.get(name, "•")
            lines.append(f"{emo} <b>{name}:</b> " + ", ".join(str(x) for x in dates))
    if month_voc:
        lines.append("")
        lines.append(f"⚫️ <b>Void of Course</b> — всего: {len(month_voc)}")
        lines.extend(voc_lines)
    lines.append("")
    lines.append("Файл календаря во вложении.")
    return "\n".join(lines)

# ───────────────────────── main ─────────────────────────────────

def main() -> None:
    if not JSON_PATH.exists():
        raise FileNotFoundError(f"{JSON_PATH} не найден. Сначала сгенерируйте lunar_calendar.json")

    cal = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    text = _summarize_calendar(cal)

    # 1) Текст
    print(f"→ Sending to chat: {CHAT}")
    tg_send_message(CHAT, text)

    # 2) Документ
    tg_send_document(CHAT, JSON_PATH, caption="lunar_calendar.json")

if __name__ == "__main__":
    main()