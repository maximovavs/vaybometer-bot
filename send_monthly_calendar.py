#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Отправляет «месячный» пост в TG-канал.

• группировка по фазе (без разбиения на знаки)
• в заголовке сегмента показываем все знаки, встречающиеся в диапазоне
• авто-нарезка на ≤ 4096 симв.
• отдельный блок Void-of-Course + пояснение
"""

import json, asyncio, os
from pathlib import Path
from collections import defaultdict, OrderedDict
import pendulum
from telegram import Bot, Message
from telegram.error import TelegramError

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))
TZ      = pendulum.timezone("Asia/Nicosia")
MAX_LEN = 4096                       # лимит Telegram

EMO = {
    "Новолуние"        :"🌑",
    "Растущий серп"    :"🌒",
    "Первая четверть"  :"🌓",
    "Растущая Луна"    :"🌔",
    "Полнолуние"       :"🌕",
    "Убывающая Луна"   :"🌖",
    "Последняя четверть":"🌗",
    "Убывающий серп"   :"🌘",
}

# ── helpers ───────────────────────────────────────────
def fmt_range(d1:str, d2:str)->str:
    p1, p2 = pendulum.parse(d1), pendulum.parse(d2)
    if p1.month == p2.month:
        return f"{p1.day}–{p2.day} {p1.format('MMMM', locale='ru')}"
    return f"{p1.format('D MMM', locale='ru')}–{p2.format('D MMM', locale='ru')}"

def split_chunks(text:str, limit:int=MAX_LEN):
    parts, buf = [], []
    for ln in text.splitlines(keepends=True):
        if sum(len(l) for l in buf) + len(ln) > limit:
            parts.append("".join(buf).rstrip())
            buf = []
        buf.append(ln)
    if buf: parts.append("".join(buf).rstrip())
    return parts

# ── message builder ──────────────────────────────────
def build_message(data:dict)->str:
    first_date = pendulum.parse(next(iter(data))).in_tz(TZ)
    month_name = first_date.format("MMMM YYYY", locale='ru').upper()
    lines = [f"🌙 <b>Лунный календарь на {month_name}</b>", ""]

    # 1. сегменты по фазе
    segs = OrderedDict()                         # {phase: [(date, rec), ...]}
    for date in sorted(data):
        rec = data[date]
        segs.setdefault(rec["phase_name"], []).append((date, rec))

    for phase, items in segs.items():
        emoji   = EMO[phase]
        d1, _   = items[0]
        d2, _   = items[-1]
        signs   = ", ".join(OrderedDict.fromkeys(i["sign"] for _, i in items))  # уникальные в порядке появления
        rng     = fmt_range(d1, d2)
        lines.append(f"{emoji} <b>{rng}</b> • {signs}")
        desc = items[0].get("long_desc","").strip()
        if desc:
            lines.append(f"<i>{desc}</i>")
        lines.append("")

    # 2. сводки благоприятных / неблагоприятных
    cats = data[next(iter(data))]["favorable_days"]

    def cat_row(cat, icon):
        fav = ", ".join(map(str, cats[cat]["favorable"]))
        bad = cats[cat]["unfavorable"]
        row = f"{icon} <b>{cat.capitalize()}:</b> {fav}"
        if bad:
            row += f"  •  {', '.join(map(str,bad))}"
        return row

    lines += [
        "✅ <b>Благоприятные дни:</b> "   + ", ".join(map(str, cats['general']['favorable'])),
        "❌ <b>Неблагоприятные:</b> "     + ", ".join(map(str, cats['general']['unfavorable'])),
        cat_row("haircut","✂️"),
        cat_row("travel","✈️"),
        cat_row("shopping","🛍️"),
        cat_row("health","❤️"),
        ""
    ]

    # 3. Void-of-Course
    voc_lines = []
    for rec in data.values():
        v = rec["void_of_course"]
        if v["start"] and v["end"]:
            voc_lines.append(f"• {v['start']} → {v['end']}")
    if voc_lines:
        lines.append("<b>🕳️ Void-of-Course:</b>")
        lines.extend(voc_lines)
        lines.append("")
        lines.append(
            "<i>Void-of-Course</i> — период, когда Луна завершила все аспекты в знаке "
            "и ещё не вошла в следующий; энергия рассеяна — новые начинания лучше отложить."
        )

    return "\n".join(lines).strip()

# ── main ──────────────────────────────────────────────
async def main():
    data_path = Path("lunar_calendar.json")
    if not data_path.exists():
        print("❌ lunar_calendar.json отсутствует"); return
    data = json.loads(data_path.read_text(encoding="utf-8"))
    text = build_message(data)
    chunks = split_chunks(text)

    bot = Bot(TOKEN)
    first: Message | None = None
    try:
        for i, part in enumerate(chunks):
            msg = await bot.send_message(
                CHAT_ID, part,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_to_message_id=first.id if i and first else None
            )
            if i == 0: first = msg
        print(f"✅ Отправлено сообщений: {len(chunks)}")
    except TelegramError as e:
        print(f"❌ Telegram error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
