#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
send_monthly_calendar.py
Формирует компактную лунную сводку и шлёт в Telegram-канал.
Используется Markdown V2 с полным экранированием.
"""

import os, json, asyncio, re
from pathlib import Path
from collections import OrderedDict
import pendulum
from telegram import Bot, error as tg_err

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))
TZ      = pendulum.timezone("Asia/Nicosia")

# ────────────── Markdown V2 escape ──────────────
MDV2_SPECIAL = r"_*\[\]()~`>#+\-=|{}.!<>"
ESC_RE = re.compile(f"([{re.escape(MDV2_SPECIAL)}])")

def esc(txt: str) -> str:
    """Экранирует спец-символы Markdown V2 во всём тексте."""
    return ESC_RE.sub(r"\\\1", txt)

# ────────────── построение частей сообщения ──────────────
def fmt_range(d1: str, d2: str) -> str:
    a = pendulum.parse(d1).format("D.MM"); b = pendulum.parse(d2).format("D.MM")
    return f"{a}–{b}" if a != b else a

def build_summary(sample: dict) -> str:
    fav = sample["favorable_days"]; unf = sample["unfavorable_days"]
    gfav = ", ".join(map(str, fav.get("general", []))) or "—"
    gunf = ", ".join(map(str, unf.get("general", []))) or "—"
    def cat(tag): return ", ".join(map(str, fav.get(tag, []))) or "—"

    lines = [
        f"✅ **Общие благоприятные дни месяца:** {gfav}",
        f"❌ **Общие неблагоприятные дни месяца:** {gunf}",
        "",
        f"✂️ _Стрижки:_ {cat('haircut')}",
        f"✈️ _Путешествия:_ {cat('travel')}",
        f"🛍️ _Покупки:_ {cat('shopping')}",
        f"❤️ _Здоровье:_ {cat('health')}",
        "",
        esc("Что такое Void-of-Course?"),
        esc("Void-of-Course — интервал, когда Луна завершила все ключевые аспекты "
            "в текущем знаке и ещё не вошла в следующий. Энергия рассеивается, "
            "поэтому старт важных дел, сделки и крупные покупки лучше перенести "
            "на время после окончания V/C.")
    ]
    return "\n".join(lines)

def build_month_message(cal: OrderedDict) -> str:
    first_date = next(iter(cal))
    header = pendulum.parse(first_date).in_tz(TZ).format("MMMM YYYY").upper()
    out = [f"🌙 **Лунный календарь на {esc(header)}**"]

    # группируем по фазе
    segments = []
    last_name = None
    for d, rec in cal.items():
        name = rec["phase"].split(" в ")[0]
        if name != last_name:
            segments.append({
                "label"      : rec["phase"],
                "start"      : d,
                "end"        : d,
                "phase_time" : rec["phase_time"][:16].replace('T',' '),
                "vc"         : rec["void_of_course"],
                "advice"     : rec["advice"][0] if rec["advice"] else "…",
            })
            last_name = name
        else:
            segments[-1]["end"] = d

    # формируем текст сегментов
    for seg in segments:
        rng   = fmt_range(seg["start"], seg["end"])
        vc    = seg["vc"]
        vc_ln = ""
        if vc.get("start") and vc.get("end"):
            vc_ln = f"\nVoid-of-Course: {esc(vc['start'])} → {esc(vc['end'])}"
        out.append(
            "\n**" + esc(seg["label"]) + f"** ({esc(seg['phase_time'])}; {rng})"
            + vc_ln + "\n" + esc(seg["advice"])
        )

    out.append("\n" + build_summary(next(iter(cal.values()))))
    return "\n".join(out)

# ────────────── main ──────────────
async def main():
    path = Path(__file__).parent / "lunar_calendar.json"
    if not path.exists():
        print("lunar_calendar.json missing"); return

    data = json.loads(path.read_text("utf-8"), object_pairs_hook=OrderedDict)
    text = build_month_message(data)

    bot = Bot(TOKEN)
    try:
        await bot.send_message(
            CHAT_ID, text,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True
        )
        print("✅ Sent")
    except tg_err.TelegramError as e:
        print("❌ Telegram error:", e)

if __name__ == "__main__":
    asyncio.run(main())
