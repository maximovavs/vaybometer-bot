#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
send_monthly_calendar.py — полная версия (паритет с KLD), адаптированная под Кипр.

Функции:
- Читает lunar_calendar.json и рендерит «короткое резюме» + «детальный календарь по дням».
- Делит длинный текст на чанки ≤ 4096 символов (ограничение Telegram).
- HTML-экранирование, чтобы не ломать разметку.
- Отправляет последовательно несколько сообщений + прикрепляет JSON-файл.
- Ретраи отправки (3 попытки, экспоненциальный бэкофф).
- Гибкая маршрутизация чата:
    1) CHANNEL_ID_OVERRIDE (CLI: --chat-id) — самый высокий приоритет
    2) если TO_TEST ∈ {1,true,yes,on} или CLI: --to-test → CHANNEL_ID_TEST
    3) иначе → CHANNEL_ID
  Совместимость: fallbacks на *_KLG при отсутствии кипрских переменных.

CLI:
    --chat-id <id>   — принудительный chat_id
    --to-test        — публиковать в тестовый канал
    --no-file        — не прикладывать файл lunar_calendar.json
    --dry-run        — печатать в stdout вместо отправки

Ожидаемые ENV:
- TELEGRAM_TOKEN (или TELEGRAM_TOKEN_KLG)
- CHANNEL_ID, CHANNEL_ID_TEST (или *_KLG фоллбэки)
- CHANNEL_ID_OVERRIDE (опционально)
- TO_TEST=1|true|yes|on (опционально)
- TZ (по умолчанию Asia/Nicosia)
- LUNAR_JSON_PATH (по умолчанию lunar_calendar.json)
"""

from __future__ import annotations
import os
import sys
import json
import time
import html
import argparse
from pathlib import Path
from typing import Any, Dict, List, Tuple, Optional

import requests
import pendulum

# ───────────────────────────── Конфиг/ENV ─────────────────────────────

def _envb(name: str) -> bool:
    return (os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on"))

TZ = pendulum.timezone(os.getenv("TZ", "Asia/Nicosia"))

TOKEN = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_TOKEN_KLG") or ""
if not TOKEN:
    # дадим осмысленную ошибку, чтобы workflow сразу подсказал, что не так
    raise RuntimeError("TELEGRAM_TOKEN не задан (ни TELEGRAM_TOKEN, ни TELEGRAM_TOKEN_KLG)")

JSON_PATH = Path(os.getenv("LUNAR_JSON_PATH", "lunar_calendar.json"))

# ───────────────────────────── Telegram API ───────────────────────────

TG_MAX = 4096

def _post_json(url: str, **data: Any) -> Dict[str, Any]:
    r = requests.post(url, data=data, timeout=30)
    r.raise_for_status()
    return r.json()

def _post_multipart(url: str, files: Dict[str, Any], data: Dict[str, Any]) -> Dict[str, Any]:
    r = requests.post(url, data=data, files=files, timeout=60)
    r.raise_for_status()
    return r.json()

def tg_send_message(token: str, chat_id: str, text: str, parse_mode: str = "HTML",
                    disable_web_page_preview: bool = True) -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    return _post_json(url, chat_id=chat_id, text=text, parse_mode=parse_mode,
                      disable_web_page_preview=str(disable_web_page_preview).lower())

def tg_send_document(token: str, chat_id: str, file_path: Path, caption: str = "") -> Dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/sendDocument"
    with file_path.open("rb") as f:
        files = {"document": (file_path.name, f, "application/json")}
        data = {"chat_id": chat_id, "caption": caption}
        return _post_multipart(url, files=files, data=data)

def _retry(fn, *args, **kwargs) -> Dict[str, Any]:
    delay = 2.0
    for i in range(3):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            if i == 2:
                raise
            time.sleep(delay)
            delay *= 2
    return {}  # не дойдём

# ───────────────────────────── Утилиты форматирования ─────────────────

RUS_MONTHS_NOM = {
    1: "январь", 2: "февраль", 3: "март", 4: "апрель",
    5: "май", 6: "июнь", 7: "июль", 8: "август",
    9: "сентябрь", 10: "октябрь", 11: "ноябрь", 12: "декабрь",
}

PHASE_EMO = {
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
    "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘",
}

def esc(s: Any) -> str:
    return html.escape(str(s or ""), quote=False)

def chunk_text(text: str, limit: int = TG_MAX) -> List[str]:
    if len(text) <= limit:
        return [text]
    parts: List[str] = []
    cur = ""
    for line in text.splitlines(True):
        if len(cur) + len(line) > limit:
            if cur:
                parts.append(cur)
                cur = ""
            # если строка длиннее лимита — жёстко режем
            while len(line) > limit:
                parts.append(line[:limit])
                line = line[limit:]
        cur += line
    if cur:
        parts.append(cur)
    return parts

# ───────────────────────────── Рендер календаря ──────────────────────

def _summarize_calendar(cal: Dict[str, Any]) -> str:
    days: Dict[str, Any] = cal.get("days", {})
    if not days:
        return "🌙 <b>Лунный календарь</b>\nДанные не найдены."

    # месяц/год по первой дате
    sample_date = sorted(days.keys())[0]
    dt = pendulum.parse(sample_date, tz=TZ)
    month_name = RUS_MONTHS_NOM.get(dt.month, f"{dt.month:02d}")
    year = dt.year

    # ключевые фазы
    phase_dates: Dict[str, List[int]] = {k: [] for k in ("Новолуние","Первая четверть","Полнолуние","Последняя четверть")}
    for dstr, rec in sorted(days.items()):
        ph = str(rec.get("phase_name") or "")
        if ph in phase_dates:
            phase_dates[ph].append(int(dstr[-2:]))

    # VoC статистика
    month_voc = cal.get("month_voc", []) or []
    voc_lines: List[str] = []
    for it in month_voc[:6]:
        s, e = it.get("start"), it.get("end")
        if s and e:
            voc_lines.append(f"• {esc(s)}–{esc(e)}")

    lines: List[str] = []
    lines.append(f"🌙 <b>Лунный календарь на {esc(month_name)} {year}</b>")
    lines.append("")
    for name in ("Новолуние","Первая четверть","Полнолуние","Последняя четверть"):
        dates = phase_dates[name]
        if dates:
            emo = PHASE_EMO.get(name, "•")
            lines.append(f"{emo} <b>{esc(name)}:</b> " + ", ".join(str(x) for x in dates))
    if month_voc:
        lines.append("")
        lines.append(f"⚫️ <b>Void of Course</b> — всего: {len(month_voc)}")
        lines.extend(voc_lines)
    lines.append("")
    lines.append("Файл календаря во вложении.")
    return "\n".join(lines)

def _render_detail(cal: Dict[str, Any]) -> str:
    """Детальный рендер по дням, близко к KLD: дата • фаза • знак • % • VoC и 1–2 совета."""
    days: Dict[str, Any] = cal.get("days", {})
    if not days:
        return ""

    lines: List[str] = []
    for dstr, rec in sorted(days.items()):
        dt = pendulum.parse(dstr, tz=TZ)
        dd = dt.format("DD.MM")
        phase = str(rec.get("phase") or rec.get("phase_name") or "")
        sign = str(rec.get("sign") or "")
        perc = rec.get("percent")
        perc_s = f"{int(perc)}%" if isinstance(perc, (int, float)) else "—"

        head = f"<b>{esc(dd)}</b> • {esc(phase)}"
        if sign:
            head += f" • {esc(sign)}"
        head += f" • {esc(perc_s)}"
        lines.append(head)

        # VoC (локальный)
        voc = rec.get("void_of_course") or rec.get("voc") or {}
        if isinstance(voc, dict):
            s, e = voc.get("start"), voc.get("end")
            if s and e:
                lines.append(f"  ⚫️ VoC: {esc(s)}–{esc(e)}")

        # Советы
        adv = rec.get("advice") or []
        if isinstance(adv, list) and adv:
            # возьмём до 2 строк на день
            for a in adv[:2]:
                a = str(a or "").strip()
                if a:
                    # не экранируем эмодзи/буллеты, но HTML — да
                    lines.append(f"  • {esc(a)}")
        lines.append("")  # пустая строка-разделитель

    return "\n".join(lines).rstrip()

# ───────────────────────────── Выбор чата ────────────────────────────

def resolve_chat_id(cli_chat_id: Optional[str], cli_to_test: bool) -> str:
    override = (cli_chat_id or os.getenv("CHANNEL_ID_OVERRIDE") or "").strip()
    if override:
        return override

    # режим теста — CLI флаг выше по приоритету, затем ENV
    to_test = cli_to_test or _envb("TO_TEST")
    if to_test:
        chat = os.getenv("CHANNEL_ID_TEST", "") or os.getenv("CHANNEL_ID_TEST_KLG", "")
        if chat:
            return chat

    # основной канал
    chat = os.getenv("CHANNEL_ID", "") or os.getenv("CHANNEL_ID_KLG", "")
    if not chat:
        # максимально информативно
        raise RuntimeError("Не найден chat_id: задайте CHANNEL_ID или CHANNEL_ID_TEST (или *_KLG), "
                           "либо передайте --chat-id/CHANNEL_ID_OVERRIDE")
    return chat

# ───────────────────────────── Главный сценарий ─────────────────────

def main(argv: Optional[List[str]] = None) -> int:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("--chat-id", dest="chat_id", default=None)
    p.add_argument("--to-test", dest="to_test", action="store_true", default=False)
    p.add_argument("--no-file", dest="no_file", action="store_true", default=False)
    p.add_argument("--dry-run", dest="dry_run", action="store_true", default=False)
    p.add_argument("-h", "--help", action="help", help="show this help message and exit")
    args = p.parse_args(argv)

    if not JSON_PATH.exists():
        raise FileNotFoundError(f"{JSON_PATH} не найден. Сначала сгенерируйте lunar_calendar.json")

    cal = json.loads(JSON_PATH.read_text(encoding="utf-8"))

    # 1) Короткое резюме
    summary = _summarize_calendar(cal)
    # 2) Детальный блок
    detail = _render_detail(cal)

    # Нарежем на чанки (первым уйдёт summary)
    chunks: List[str] = []
    chunks.extend(chunk_text(summary))
    if detail:
        chunks.extend(chunk_text(detail))

    chat_id = resolve_chat_id(args.chat_id, args.to_test)
    print(f"→ Sending to chat: {chat_id}")

    if args.dry_run:
        print("---- DRY RUN ----")
        for i, ch in enumerate(chunks, 1):
            print(f"\n--- Message {i}/{len(chunks)} ({len(ch)} chars) ---\n{ch}")
        if not args.no_file:
            print(f"\n--- File attach ---\n{JSON_PATH}")
        return 0

    # Отправка сообщений с ретраями
    for ch in chunks:
        _retry(tg_send_message, TOKEN, chat_id, ch)

    # Прикрепляем файл (по умолчанию)
    if not args.no_file:
        _retry(tg_send_document, TOKEN, chat_id, JSON_PATH, caption="lunar_calendar.json")

    return 0


if __name__ == "__main__":
    sys.exit(main())
