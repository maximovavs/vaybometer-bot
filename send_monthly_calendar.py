#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Формирует и отправляет “месячный” пост.
• группировка по «фаза + знак»
• авто-нарезка на ≤4096 симв.
• отдельный блок Void-of-Course
"""

import json, asyncio, os, math, textwrap
from pathlib import Path
from collections import defaultdict
import pendulum
from telegram import Bot, Message
from telegram.error import TelegramError

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))
TZ      = pendulum.timezone("Asia/Nicosia")
MAX_LEN = 4096                     # Telegram hard-limit

EMO = {
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓","Растущая Луна":"🌔",
    "Полнолуние":"🌕","Убывающая Луна":"🌖","Последняя четверть":"🌗","Убывающий серп":"🌘",
}

# ── helpers ────────────────────────────────────────────
def fmt_range(d1:str, d2:str)->str:
    p1, p2 = pendulum.parse(d1), pendulum.parse(d2)
    if p1.month == p2.month:
        return f"{p1.day}–{p2.day} {p1.format('MMMM', locale='ru')}"
    return f"{p1.format('D MMM', locale='ru')}–{p2.format('D MMM', locale='ru')}"

def collect_segments(data:dict):
    segs, buf, last = [], [], None
    for date in sorted(data):
        rec = data[date]
        key = (rec["phase_name"], rec["sign"])
        if key != last and buf:
            segs.append(buf); buf=[]
        buf.append((date, rec)); last = key
    if buf: segs.append(buf)
    return segs

def build_message(data:dict)->str:
    month = pendulum.parse(next(iter(data))).in_tz(TZ).format("MMMM YYYY", locale='ru').upper()
    lines = [f"🌙 <b>Лунный календарь на {month}</b>", ""]

    # основной текст
    for seg in collect_segments(data):
        d1, r1 = seg[0]
        d2, _  = seg[-1]
        emoji  = EMO[r1["phase_name"]]
        rng    = fmt_range(d1, d2)
        sign   = r1["sign"]
        lines.append(f"{emoji} <b>{rng} • {sign}</b>")
        if desc := r1.get("long_desc","").strip():
            lines.append(f"<i>{desc}</i>")
        lines.append("")

    # сводки
    cats = data[next(iter(data))]["favorable_days"]
    def row(cat, ico):
        good = ", ".join(map(str, cats[cat]["favorable"]))
        bad  = cats[cat]["unfavorable"]
        line = f"{ico} <b>{cat.capitalize()}:</b> {good}"
        if bad: line += f"  •  {', '.join(map(str,bad))}"
        return line
    lines += [
        "✅ <b>Благоприятные дни:</b> "   + ", ".join(map(str, cats['general']['favorable'])),
        "❌ <b>Неблагоприятные:</b> "     + ", ".join(map(str, cats['general']['unfavorable'])),
        row("haircut","✂️"),
        row("travel","✈️"),
        row("shopping","🛍️"),
        row("health","❤️"),
        ""
    ]

    # VoC
    voc = [f"• {v['start']} → {v['end']}"
           for v in (rec["void_of_course"] for rec in data.values())
           if v["start"] and v["end"]]
    if voc:
        lines.append("<b>🕳️ Void-of-Course:</b>")
        lines.extend(voc)
        lines.append("")
        lines.append(
            "<i>Void-of-Course</i> — период, когда Луна завершила все аспекты в знаке "
            "и ещё не вошла в следующий; энергия рассеяна, новые начинания лучше отложить."
        )

    return "\n".join(lines).strip()

def split_chunks(text:str, limit:int=MAX_LEN):
    """делим по пустым строкам, чтобы не резать середину слова"""
    parts, buf = [], []
    for line in text.splitlines(keepends=True):
        if sum(len(l) for l in buf)+len(line) > limit:
            parts.append("".join(buf).rstrip())
            buf = []
        buf.append(line)
    if buf: parts.append("".join(buf).rstrip())
    return parts

# ── main ───────────────────────────────────────────────
async def main():
    data_file = Path("lunar_calendar.json")
    if not data_file.exists():
        print("❌ lunar_calendar.json not found"); return

    message = build_message(json.loads(data_file.read_text(encoding="utf-8")))
    chunks  = split_chunks(message)

    bot = Bot(TOKEN)
    first_msg: Message | None = None
    try:
        for idx, chunk in enumerate(chunks):
            sent = await bot.send_message(
                CHAT_ID,
                chunk,
                parse_mode="HTML",
                disable_web_page_preview=True,
                reply_to_message_id=first_msg.id if idx and first_msg else None
            )
            if idx == 0: first_msg = sent
        print(f"✅ Sent {len(chunks)} Telegram message(s)")
    except TelegramError as e:
        print(f"❌ Telegram error: {e}")

if __name__ == "__main__":
    asyncio.run(main())