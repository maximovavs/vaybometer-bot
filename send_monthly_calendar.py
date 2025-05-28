#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Отправляет компактный месячный лунный календарь:
• блоками по фазам
• краткое пояснение VoC
"""

import os, json, asyncio, html
from pathlib import Path
from collections import OrderedDict
import pendulum
from telegram import Bot, error as tg_err

TOKEN   = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = int(os.getenv("CHANNEL_ID", "0"))
TZ      = pendulum.timezone("Asia/Nicosia")

# ---------- helpers ----------
esc = lambda t: html.escape(t).replace("\xa0", " ")

def rng(a, b):
    pa, pb = (pendulum.parse(x).format("D.MM") for x in (a, b))
    return pa if pa == pb else f"{pa}–{pb}"

def summary(sample):
    fav, unf = sample["favorable_days"], sample["unfavorable_days"]
    g = lambda k, src: ", ".join(map(str, src.get(k, []))) or "—"
    parts = [
        f"✅ <b>Благоприятные дни:</b> {esc(g('general', fav))}",
        f"❌ <b>Неблагоприятные:</b> {esc(g('general', unf))}",
        "",
        f"✂️ Стрижки: {esc(g('haircut', fav))}",
        f"✈️ Путешествия: {esc(g('travel', fav))}",
        f"🛍️ Покупки: {esc(g('shopping', fav))}",
        f"❤️ Здоровье: {esc(g('health', fav))}",
        "",
        "<b>Что такое V/C?</b> "
        "V/C — «без курса»: Луна без новых аспектов и ещё не в новом знаке. "
        "Лучше планировать и завершать, а не стартовать."
    ]
    return "\n".join(parts)

# ---------- builder ----------
def build(cal: OrderedDict) -> str:
    first = next(iter(cal))
    head  = pendulum.parse(first).in_tz(TZ).format("MMMM YYYY").upper()
    out   = [f"🌙 <b>Лунный календарь на {esc(head)}</b>"]

    blocks, last = [], None
    for d, r in cal.items():
        name = r["phase"].split(" в ")[0]
        if name != last:
            blocks.append({
                "label":  esc(r["phase"]),
                "start":  d, "end": d,
                "time":   esc(r["phase_time"][:16].replace('T',' ')),
                "vc":     r["void_of_course"],
                "tip":    esc(r["advice"][0] if r["advice"] else "—")
            })
            last = name
        else:
            blocks[-1]["end"] = d

    for b in blocks:
        vc   = b["vc"]
        vcln = (f
