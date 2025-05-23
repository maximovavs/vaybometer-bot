#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Простой генератор lunar_calendar.json на основе API farmsense.net.
Запускает запрос по каждому дню месяца, сохраняет phase/advice/favorable/unfavorable.
"""

import pendulum
import requests
import json
from pathlib import Path

YEAR  = 2025
MONTH = 5

out: dict = {}
for day in range(1, 32):
    try:
        d = pendulum.date(YEAR, MONTH, day)
    except ValueError:
        continue  # пропускаем несуществующие 31-е в апреле и т.п.
    ts = int(d.int_timestamp)
    # обращаемся к farmsense.net API
    resp = requests.get("https://api.farmsense.net/v1/moonphases/", params={"d": ts}, timeout=10)
    data = resp.json()[0]
    # составляем «фазу»
    phase = data["Phase"]  # например "Waxing Crescent"
    illum = data["Illumination"]  # в процентах
    phase_str = f"{phase} ({illum}%)"
    # здесь placeholders для advice/favorable/unfavorable
    advice = "—"  
    favorable   = []  
    unfavorable = []  

    out[d.to_date_string()] = {
        "phase":      phase_str,
        "advice":     advice,
        "favorable":   favorable,
        "unfavorable": unfavorable
    }

# пишем в файл
path = Path(__file__).parent / "lunar_calendar.json"
path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"✅ lunar_calendar.json сгенерирован: {path}")
