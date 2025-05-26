#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
send_monthly_calendar.py
Формирует компактный месячный отчёт и отправляет его в Telegram-канал.
Группировка по фазам, вывод Void-of-Course и краткого совета.
"""

import os, json, asyncio
from pathlib import Path
from collections import OrderedDict

import pendulum
from telegram import Bot, error as tg_err

# ────────── токен / канал ──────────
TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))
TZ      = pendulum.timezone("Asia/Nicosia")

# ────────── вспомогалки ──────────
ICON_PHASE = {
    "Новолуние":        "🌑",
    "Растущий серп":    "🌒",
    "Первая четверть":  "🌓",
    "Растущая Луна":    "🌔",
    "Полнолуние":       "🌕",
    "Убывающая Луна":   "🌖",
    "Последняя четверть":"🌗",
    "Убывающий серп":   "🌘",
}

def header(date_str: str) -> str:
    month_year = pendulum.parse(date_str).in_tz(TZ).format("MMMM YYYY").upper()
    return f"🌙 <b>Лунный календарь на {month_year}</b> (Asia/Nicosia)"

def group_by_phase(data: dict) -> list[dict]:
    """
    Возвращает список сегментов:
      {phase, icon, first_date, last_date, phase_time, vc, advice}
    """
    segments: list[dict] = []
    last_name = None
    for dstr, rec in data.items():
        name = rec["phase"].split(" в ")[0]
        icon = ICON_PHASE.get(name, "◻️")
        if name != last_name:
            segments.append(
                dict(
                    phase = rec["phase"],
                    name  = name,
                    icon  = icon,
                    first_date = dstr,
                    last_date  = dstr,
                    phase_time = rec.get("phase_time",""),
                    vc   = rec.get("void_of_course",{}),
                    advice = rec["advice"][0] if rec.get("advice") else "",
                )
            )
            last_name = name
        else:
            segments[-1]["last_date"] = dstr
    return segments

def format_segment(seg: dict) -> str:
    start = pendulum.parse(seg["first_date"]).format("D.MM")
    end   = pendulum.parse(seg["last_date"]).format("D.MM")
    phase_line = (
        f"<b>{seg['icon']} {seg['phase']}</b> "
        f"({pendulum.parse(seg['phase_time']).in_tz(TZ).format('DD.MM HH:mm')}; "
        f"{start}–{end})"
    )
    lines = [phase_line]

    vc = seg["vc"]
    if vc and vc.get("start") and vc.get("end"):
        lines.append(f"Void-of-Course: {vc['start']} → {vc['end']}")

    if seg["advice"]:
        lines.append(seg["advice"])

    return "\n".join(lines)

def build_summary(sample_record: dict) -> list[str]:
    """Сводка по категориям, берём из любого дня (они одинаковые)."""
    fav = sample_record["favorable_days"]
    unf = sample_record["unfavorable_days"]

    def fmt(lst): return ", ".join(map(str, sorted(lst))) if lst else "—"

    lines = ["", "✅ <b>Общие благоприятные дни месяца:</b> " + fmt(fav["general"])]
    if unf["general"]:
        lines.append("❌ <b>Общие неблагоприятные дни месяца:</b> " + fmt(unf["general"]))

    icons = {"haircut":"✂️ Стрижки", "travel":"✈️ Путешествия",
             "shopping":"🛍️ Покупки", "health":"❤️ Здоровье"}
    for key, label in icons.items():
        if fav.get(key):
            lines.append(f"{label}: {fmt(fav[key])}")
    return lines

def build_month_message(data: dict) -> str:
    data = OrderedDict(sorted(data.items()))         # хронологический порядок
    segs = group_by_phase(data)

    lines = [header(next(iter(data)) ), ""]
    for s in segs: lines += [format_segment(s), "—"]

    # убираем лишний «—» в конце
    if lines[-1] == "—": lines.pop()

    # добавляем сводку и объяснение V/C
    lines += build_summary(next(iter(data.values())))
    lines += [
        "", "<i><b>Что такое Void-of-Course?</b></i>",
        ("Void-of-Course (период «без курса») — это интервал, когда Луна завершила все ключевые "
         "аспекты в текущем знаке и ещё не вошла в следующий. Энергия дней рассеивается; "
         "для старта важных дел лучше дождаться окончания V/C.")
    ]
    return "\n".join(lines)

# ────────── основной асинхронный запуск ──────────
async def main() -> None:
    path = Path(__file__).parent / "lunar_calendar.json"
    if not path.exists():
        print("❌ lunar_calendar.json not found")
        return
    data = json.loads(path.read_text(encoding="utf-8"))
    msg  = build_month_message(data)

    bot = Bot(token=TOKEN)
    try:
        # Telegram лимит 4096 симв. – делим если нужно
        while msg:
            chunk, msg = msg[:4000], msg[4000:]
            await bot.send_message(
                CHAT_ID, chunk,
                parse_mode="HTML",
                disable_web_page_preview=True
            )
        print("✅ Monthly calendar sent.")
    except tg_err.TelegramError as e:
        print(f"❌ Telegram error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
