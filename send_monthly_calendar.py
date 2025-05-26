#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Компактный месячный отчёт в Telegram."""

import os, json, asyncio
from pathlib import Path
from collections import OrderedDict

import pendulum
from telegram import Bot, error as tg_err

TOKEN   = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = int(os.getenv("CHANNEL_ID", 0))
TZ      = pendulum.timezone("Asia/Nicosia")

ICON_PHASE = {
    "Новолуние":"🌑","Растущий серп":"🌒","Первая четверть":"🌓",
    "Растущая Луна":"🌔","Полнолуние":"🌕","Убывающая Луна":"🌖",
    "Последняя четверть":"🌗","Убывающий серп":"🌘",
}

# ────────── построение текста ──────────
def header(first_date:str)->str:
    month_year = pendulum.parse(first_date).in_tz(TZ).format("MMMM YYYY").upper()
    return f"🌙 <b>Лунный календарь на {month_year}</b> (Asia/Nicosia)"

def group_by_phase(data:dict)->list[dict]:
    segs, last=None, None
    segs=[]
    for dstr, rec in data.items():
        name = rec["phase"].split(" в ")[0]
        if name!=last:
            segs.append({
                "icon": ICON_PHASE.get(name,"◻️"),
                "title": rec["phase"],
                "phase_time": rec.get("phase_time",""),
                "first": dstr, "last": dstr,
                "vc":   rec.get("void_of_course",{}),
                "advice": (rec.get("advice") or [""])[0],
            }); last=name
        else:
            segs[-1]["last"]=dstr
    return segs

def fmt_seg(s:dict)->str:
    start = pendulum.parse(s["first"]).format("D.MM")
    end   = pendulum.parse(s["last"]).format("D.MM")
    pt    = pendulum.parse(s["phase_time"]).in_tz(TZ).format("DD.MM HH:mm") if s["phase_time"] else "—"
    lines=[f"<b>{s['icon']} {s['title']}</b> ({pt}; {start}–{end})"]
    if s["vc"].get("start") and s["vc"].get("end"):
        lines.append(f"Void-of-Course: {s['vc']['start']} → {s['vc']['end']}")
    if s["advice"]:
        lines.append(s["advice"])
    return "\n".join(lines)

def build_summary(sample:dict)->list[str]:
    fav = sample.get("favorable_days",{})
    unf = sample.get("unfavorable_days",{})
    def j(lst): return ", ".join(map(str,sorted(lst))) if lst else "—"
    lines=["", f"✅ <b>Общие благоприятные дни месяца:</b> {j(fav.get('general',[]))}"]
    if unf.get("general"):
        lines.append(f"❌ <b>Общие неблагоприятные дни месяца:</b> {j(unf['general'])}")
    icons={"haircut":"✂️ Стрижки","travel":"✈️ Путешествия",
           "shopping":"🛍️ Покупки","health":"❤️ Здоровье"}
    for k,lbl in icons.items():
        if fav.get(k):
            lines.append(f"{lbl}: {j(fav[k])}")
    return lines

def build_message(data:dict)->str:
    data=OrderedDict(sorted(data.items()))
    segs=group_by_phase(data)
    lines=[header(next(iter(data))),""]
    for s in segs: lines+= [fmt_seg(s), "—"]
    if lines[-1]=="—": lines.pop()
    lines+=build_summary(next(iter(data.values())))
    lines+=["",
        "<i><b>Что такое Void-of-Course?</b></i>",
        ("Void-of-Course — интервал, когда Луна завершила все ключевые аспекты "
         "в текущем знаке и ещё не вошла в следующий. Энергия рассеивается, "
         "поэтому старт важных дел, подписания договоров и крупные покупки "
         "лучше переносить на время после окончания V/C.")
    ]
    return "\n".join(lines)

# ────────── отправка ──────────
async def main():
    cal_file=Path(__file__).parent/"lunar_calendar.json"
    if not cal_file.exists():
        print("❌ lunar_calendar.json not found"); return
    data=json.loads(cal_file.read_text(encoding="utf-8"))
    text=build_message(data)

    bot=Bot(TOKEN)
    try:
        while text:
            chunk,text=text[:4000],text[4000:]
            await bot.send_message(CHAT_ID,chunk,
                parse_mode="HTML",disable_web_page_preview=True)
        print("✅ Monthly message sent")
    except tg_err.TelegramError as e:
        print(f"❌ Telegram error: {e}")

if __name__=="__main__":
    asyncio.run(main())
