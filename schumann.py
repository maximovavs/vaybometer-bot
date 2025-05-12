#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import random
from utils import _get

SCH_QUOTES = [
    "датчики молчат — ретрит 🌱",
    "кошачий мяу-фактор заглушил сенсоры 😸",
    "волны ушли ловить чаек 🐦",
    "показания медитируют 🧘",
    "данные в отпуске 🏝️",
    "Шуман спит — не будим 🔕",
    "тишина в эфире… 🎧",
]

def get_schumann() -> dict:
    for url in (
        "https://api.glcoherence.org/v1/earth",
        "https://gci-api.ucsd.edu/data/latest",
    ):
        j = _get(url)
        if j:
            try:
                if "data" in j:
                    j = j["data"]["sr1"]
                freq = j.get("frequency_1") or j.get("frequency")
                amp  = j.get("amplitude_1")  or j.get("amplitude")
                return {"freq": freq, "amp": amp, "high": freq and freq>8}
            except:
                pass
    return {"msg": random.choice(SCH_QUOTES)}
