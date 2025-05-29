#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Формирует и отправляет «месячный» пост в Telegram
• группировка по «фаза + знак»
• жирное «даты – знак», курсивное описание
• отдельный блок Void-of-Course + пояснение
"""

import json, asyncio, os
from pathlib import Path
from collections import defaultdict
import pendulum
from telegram import Bot         # python-telegram-bot >=20,<21
from telegram.error import TelegramError

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))
TZ      = pendulum.timezone("Asia/Nicosia")

EMO = {                     # тот же словарь, что в генераторе
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
    "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘"
}

# ---------- helpers --------------------------------------------
def fmt_range(d1:str, d2:str)->str:
    """'2025-05-01', '2025-05-03' → 1–3 мая"""
    p1, p2 = pendulum.parse(d1), pendulum.parse(d2)
    if p1.month == p2.month:
        return f"{p1.day}–{p2.day} {p1.format('MMMM', locale='ru')}"
    return f"{p1.format('D MMM', locale='ru')}–{p2.format('D MMM', locale='ru')}"

def collect_segments(data:dict):
    """[{phase_name, sign, ...}] сгруппировано по фаза+знак"""
    segs = []
    buff = []
    last_key = None

    for date in sorted(data):
        rec = data[date]
        key = (rec["phase_name"], rec["sign"])
        if key != last_key and buff:
            segs.append(buff)
            buff = []
        buff.append((date, rec))
        last_key = key
    if buff:
        segs.append(buff)
    return segs

def build_message(data:dict)->str:
    # ── заголовок
    month = pendulum.parse(next(iter(data))).in_tz(TZ).format("MMMM YYYY", locale='ru').upper()
    lines = [f"🌙 <b>Лунный календарь на {month}</b>", ""]

    # ── основная часть
    for seg in collect_segments(data):
        first_date, first_rec = seg[0]
        last_date,  _         = seg[-1]
        emoji  = EMO[first_rec["phase_name"]]
        rng    = fmt_range(first_date, last_date)
        sign   = first_rec["sign"]
        desc   = first_rec.get("long_desc","").strip()

        lines.append(f"{emoji} <b>{rng} • {sign}</b>")
        if desc:
            lines.append(f"<i>{desc}</i>")
        lines.append("")                      # пустая строка-разделитель

    # ── сводка благоприятных дней (берём из первого объекта)
    cats = data[first_date]["favorable_days"]
    def fmt(cat, ico): 
        good = ", ".join(map(str, cats[cat]["favorable"]))
        bad  = ", ".join(map(str, cats[cat]["unfavorable"]))
        return f"{ico} <b>{cat.capitalize()}:</b> {good}" + (f"  •  {bad}" if bad else "")
    lines += [
        "✅ <b>Благоприятные дни:</b> "   + ", ".join(map(str, cats['general']['favorable'])),
        "❌ <b>Неблагоприятные:</b> "     + ", ".join(map(str, cats['general']['unfavorable'])),
        fmt("haircut","✂️"),
        fmt("travel","✈️"),
        fmt("shopping","🛍️"),
        fmt("health","❤️"),
        ""
    ]

    # ── Void-of-Course
    voc_lines = []
    for d, rec in data.items():
        s, e = rec["void_of_course"].values()
        if s and e:
            voc_lines.append(f"• {s} → {e}")
    if voc_lines:
        lines.append("<b>🕳️ Void-of-Course:</b>")
        lines.extend(voc_lines)
        lines.append("")
        lines.append(
            "<i>Void-of-Course</i> — интервал, когда Луна завершила все ключевые аспекты в знаке "
            "и ещё не вошла в следующий. В это время энергия рассеяна; старт важных дел лучше перенести."
        )

    return "\n".join(lines).strip()

# ---------- main -----------------------------------------------
async def main() -> None:
    path = Path("lunar_calendar.json")
    if not path.exists():
        print("❌ lunar_calendar.json not found")
        return

    data = json.loads(path.read_text(encoding="utf-8"))
    msg  = build_message(data)

    bot = Bot(token=TOKEN)
    try:
        await bot.send_message(
            CHAT_ID,
            msg,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        print("✅ Monthly post sent")
    except TelegramError as e:
        print(f"❌ Telegram error: {e}")

if __name__ == "__main__":
    asyncio.run(main())