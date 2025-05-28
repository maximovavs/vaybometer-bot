#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os, json, asyncio, html
from pathlib import Path
from collections import OrderedDict
import pendulum
from telegram import Bot, error as tg_err

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))
TZ      = pendulum.timezone("Asia/Nicosia")

# ---------- helpers ----------
def esc(t: str) -> str:           # HTML-escape + невидимый &nbsp;-> обычный пробел
    return html.escape(t).replace("\xa0", " ")

def fmt_range(a: str, b: str) -> str:
    pa = pendulum.parse(a).format("D.MM")
    pb = pendulum.parse(b).format("D.MM")
    return pa if pa == pb else f"{pa}–{pb}"

def build_summary(sample: dict) -> str:
    fav, unf = sample["favorable_days"], sample["unfavorable_days"]
    def lst(tag, src): return ", ".join(map(str, src.get(tag, []))) or "—"
    return "\n".join([
        f"✅ <b>Общие благоприятные дни месяца:</b> {esc(lst('general', fav))}",
        f"❌ <b>Общие неблагоприятные дни месяца:</b> {esc(lst('general', unf))}",
        "",
        f"✂️ <i>Стрижки:</i> {esc(lst('haircut', fav))}",
        f"✈️ <i>Путешествия:</i> {esc(lst('travel', fav))}",
        f"🛍️ <i>Покупки:</i> {esc(lst('shopping', fav))}",
        f"❤️ <i>Здоровье:</i> {esc(lst('health', fav))}",
        "",
        "<b>Что такое Void-of-Course?</b>",
        esc("Void-of-Course — интервал, когда Луна завершила все основные "
            "аспекты в текущем знаке и ещё не вошла в следующий. В это время "
            "энергия рассеяна, поэтому старт важных дел, подписания договоров "
            "и крупные покупки лучше перенести до окончания V/C.")
    ])

# ---------- main builder ----------
def build_month_message(cal: OrderedDict) -> str:
    first = next(iter(cal))
    header = pendulum.parse(first).in_tz(TZ).format("MMMM YYYY").upper()
    lines  = [f"🌙 <b>Лунный календарь на {esc(header)}</b>"]

    segs, last = [], None
    for d, rec in cal.items():
        name = rec["phase"].split(" в ")[0]
        if name != last:
            segs.append({
                "label":  esc(rec["phase"]),
                "start":  d,
                "end":    d,
                "time":   esc(rec["phase_time"][:16].replace('T',' ')),
                "vc":     rec["void_of_course"],
                "tip":    esc(rec["advice"][0] if rec["advice"] else "…")
            })
            last = name
        else:
            segs[-1]["end"] = d

    for s in segs:
        rng = fmt_range(s["start"], s["end"])
        vc  = s["vc"]
        vc_line = (f"\n<i>Void-of-Course:</i> {esc(vc['start'])} → {esc(vc['end'])}"
                   if vc.get("start") and vc.get("end") else "")
        lines.append(
            f"\n<b>{s['label']}</b> ({s['time']}; {rng})"
            f"{vc_line}\n{s['tip']}"
        )

    lines.append("\n" + build_summary(next(iter(cal.values()))))
    return "\n".join(lines)

# ---------- telegram send ----------
async def main() -> None:
    path = Path(__file__).parent / "lunar_calendar.json"
    if not path.exists():
        print("lunar_calendar.json not found"); return
    data = json.loads(path.read_text('utf-8'), object_pairs_hook=OrderedDict)
    txt  = build_month_message(data)

    bot = Bot(TOKEN)
    try:
        await bot.send_message(
            CHAT_ID, txt,
            parse_mode="HTML",
            disable_web_page_preview=True
        )
        print("✅ monthly calendar sent")
    except tg_err.TelegramError as e:
        print("❌ Telegram error:", e)

if __name__ == "__main__":
    asyncio.run(main())
