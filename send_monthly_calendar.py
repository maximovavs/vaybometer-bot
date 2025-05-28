#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
send_monthly_calendar.py
Отправляет компактную лунную сводку в Telegram-канал.
— группировка по фазам
— Markdown V2, чтобы **жирный** и _курсив_ не конфликтовали с HTML
"""

import os, json, asyncio, re
from pathlib import Path
from collections import OrderedDict
import pendulum
from telegram import Bot, error as tg_err

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))
TZ      = pendulum.timezone("Asia/Nicosia")

# ─────────────────────────── helpers ────────────────────────────
def esc(md: str) -> str:
    """Экранирует спец-символы для Markdown V2."""
    return re.sub(r'([_*\[\]()~`>#+\-=|{}.!])', r'\\\1', md)

def build_summary(rec: dict) -> str:
    fav = rec["favorable_days"]; unf = rec["unfavorable_days"]
    fmt = lambda arr: ", ".join(map(str, arr)) if arr else "—"
    lines = [
        f"✅ **Общие благоприятные дни месяца:** {fmt(fav.get('general', []))}",
        f"❌ **Общие неблагоприятные дни месяца:** {fmt(unf.get('general', []))}",
        "",
        f"✂️ *Стрижки:* {fmt(fav.get('haircut', []))}",
        f"✈️ *Путешествия:* {fmt(fav.get('travel', []))}",
        f"🛍️ *Покупки:* {fmt(fav.get('shopping', []))}",
        f"❤️ *Здоровье:* {fmt(fav.get('health', []))}",
        "",
        "*Что такое Void-of-Course?*\n"
        "Void-of-Course — интервал, когда Луна завершила все ключевые аспекты "
        "в текущем знаке и ещё не вошла в следующий. Энергия рассеивается, "
        "поэтому старт важных дел, подписания контрактов и крупные покупки "
        "лучше перенести на время после окончания V/C."
    ]
    return "\n".join(lines)

def build_month_message(data: OrderedDict) -> str:
    first = next(iter(data))
    hdr   = pendulum.parse(first).in_tz(TZ).format("MMMM YYYY").upper()
    msg   = [f"🌙 **Лунный календарь на {hdr}**"]

    segments = []
    last_name = None
    for date_str, rec in data.items():
        phase_full = rec["phase"]
        name = phase_full.split(" в ")[0]
        if name != last_name:
            segments.append({
                "name": name,
                "sign": rec["sign"],
                "phase": phase_full,
                "start": date_str,
                "end": date_str,
                "phase_time": rec["phase_time"][:16].replace("T"," "),
                "vc": rec["void_of_course"],
                "advice": rec["advice"][0] if rec["advice"] else "…"
            })
            last_name = name
        else:
            segments[-1]["end"] = date_str

    # Форматируем каждый сегмент
    for seg in segments:
        d1 = pendulum.parse(seg["start"]).format("D.MM")
        d2 = pendulum.parse(seg["end"]).format("D.MM")
        rng = f"{d1}–{d2}" if d1 != d2 else d1
        vc  = seg["vc"]
        vc_line = ""
        if vc["start"] and vc["end"]:
            vc_line = f"\nVoid-of-Course: {vc['start']} → {vc['end']}"
        msg.append(
            f"\n**{esc(seg['phase'])}**"
            f" ({seg['phase_time']}; {rng}){vc_line}\n"
            f"{esc(seg['advice'])}"
        )

    # Сводка
    msg.append("\n" + build_summary(next(iter(data.values()))))
    return "\n".join(msg)

# ─────────────────────────── main ───────────────────────────────
async def main():
    path = Path(__file__).parent / "lunar_calendar.json"
    if not path.exists():
        print("lunar_calendar.json not found"); return

    data = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=OrderedDict)
    text = build_month_message(data)

    bot = Bot(TOKEN)
    try:
        await bot.send_message(
            CHAT_ID,
            text,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True,
        )
        print("✅ Monthly report sent")
    except tg_err.TelegramError as e:
        print("❌ Telegram error:", e)

if __name__ == "__main__":
    asyncio.run(main())
