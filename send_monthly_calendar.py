#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, asyncio, re
from pathlib import Path
from collections import OrderedDict
import pendulum
from telegram import Bot, error as tg_err

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))
TZ      = pendulum.timezone("Asia/Nicosia")

# ---------- Markdown-V2 escape ----------
MDV2_CHARS = r"_*\[\]()~`>#+\-=|{}.!<>"
ESC_RE = re.compile(f"([{re.escape(MDV2_CHARS)}])")
def esc(t: str) -> str: return ESC_RE.sub(r"\\\1", t)

# ---------- helpers ----------
def fmt_range(d1: str, d2: str) -> str:
    a = pendulum.parse(d1).format("D.MM"); b = pendulum.parse(d2).format("D.MM")
    return a if a == b else f"{a}–{b}"

def build_summary(sample: dict) -> str:
    fav, unf = sample["favorable_days"], sample["unfavorable_days"]
    def lst(tag, d): 
        vals = d.get(tag, [])
        return ", ".join(map(str, vals)) if vals else "—"
    lines = [
        f"✅ **Общие благоприятные дни месяца:** {lst('general', fav)}",
        f"❌ **Общие неблагоприятные дни месяца:** {lst('general', unf)}",
        "",
        f"✂️ _Стрижки:_ {lst('haircut', fav)}",
        f"✈️ _Путешествия:_ {lst('travel', fav)}",
        f"🛍️ _Покупки:_ {lst('shopping', fav)}",
        f"❤️ _Здоровье:_ {lst('health', fav)}",
        "",
        esc("Что такое Void-of-Course?"),
        esc("Void-of-Course — интервал, когда Луна завершила все основные "
            "аспекты в текущем знаке и ещё не вошла в следующий. В это время "
            "энергия рассеяна, поэтому старт важных дел и крупные покупки "
            "лучше отложить до конца V/C.")
    ]
    return "\n".join(lines)

# ---------- main builder ----------
def build_month_message(cal: OrderedDict) -> str:
    first = next(iter(cal))
    header = pendulum.parse(first).in_tz(TZ).format("MMMM YYYY").upper()
    out = [f"🌙 **Лунный календарь на {esc(header)}**"]

    segments, last_name = [], None
    for dt, rec in cal.items():
        name = rec["phase"].split(" в ")[0]
        if name != last_name:
            segments.append({
                "label" : esc(rec["phase"]),
                "start" : dt,
                "end"   : dt,
                "time"  : esc(rec["phase_time"][:16].replace('T',' ')),
                "vc"    : rec["void_of_course"],
                "tip"   : esc(rec["advice"][0] if rec["advice"] else "…"),
            })
            last_name = name
        else:
            segments[-1]["end"] = dt

    for seg in segments:
        rng = fmt_range(seg["start"], seg["end"])
        vc  = seg["vc"]; vc_line = ""
        if vc.get("start") and vc.get("end"):
            vc_line = f"\nVoid\\-of\\-Course: {esc(vc['start'])} → {esc(vc['end'])}"
        # скобки и двоеточие экранируем: \(   \)  \:
        out.append(
            f"\n**{seg['label']}** \\({seg['time']}; {rng}\\)"
            f"{vc_line}\n{seg['tip']}"
        )

    out.append("\n" + build_summary(next(iter(cal.values()))))
    return "\n".join(out)

# ---------- telegram send ----------
async def main():
    path = Path(__file__).parent / "lunar_calendar.json"
    if not path.exists():
        print("lunar_calendar.json missing"); return
    data = json.loads(path.read_text('utf-8'), object_pairs_hook=OrderedDict)
    txt  = build_month_message(data)

    bot = Bot(TOKEN)
    try:
        await bot.send_message(
            CHAT_ID, txt,
            parse_mode="MarkdownV2",
            disable_web_page_preview=True
        )
        print("✅ monthly calendar sent")
    except tg_err.TelegramError as e:
        print("❌ Telegram error:", e)

if __name__ == "__main__":
    asyncio.run(main())