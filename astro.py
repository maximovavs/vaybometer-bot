#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import math
import datetime as dt
import swisseph as swe
from typing import Optional, List

# Названия знаков и их эффекты
SIGNS = [
    "Козероге", "Водолее", "Рыбах", "Овне",
    "Тельце", "Близнецах", "Раке", "Льве",
    "Деве", "Весах", "Скорпионе", "Стрельце"
]
EFFECT = [
    "фокусирует на деле", "дарит странные идеи", "усиливает эмпатию",
    "придаёт смелости", "настраивает на комфорт", "повышает коммуникабельность",
    "усиливает заботу", "разжигает творческий огонь", "настраивает на порядок",
    "заставляет искать баланс", "поднимает страсть", "толкает к приключениям"
]

# Круговая шкала иконок фаз
MOON_ICONS = "🌑🌒🌓🌔🌕🌖🌗🌘"

def moon_phase() -> str:
    """
    Возвращает строку вида:
      "<иконка> <название фазы> в <знак> (<illum> %) — <эффект>"
    """
    # юлианская дата сегодня
    jd = swe.julday(*dt.datetime.utcnow().timetuple()[:3])
    sun_lon  = swe.calc_ut(jd, swe.SUN)[0][0]
    moon_lon = swe.calc_ut(jd, swe.MOON)[0][0]
    # фаза 0…1
    phase = ((moon_lon - sun_lon + 360) % 360) / 360
    # освещённость
    illum = round(abs(math.cos(math.pi * phase)) * 100)
    # выбираем иконку по фазе
    icon = MOON_ICONS[int(phase * len(MOON_ICONS)) % len(MOON_ICONS)]
    # имя фазы
    if illum < 5:
        name = "Новолуние"
    elif phase < 0.5:
        name = "Растущая Луна"
    elif illum > 95:
        name = "Полнолуние"
    else:
        name = "Убывающая Луна"
    # знак и эффект (накрываем % 12, чтобы не выйти за границы)
    sign_index = (int(moon_lon // 30)) % 12
    sign   = SIGNS[sign_index]
    effect = EFFECT[sign_index]

    return f"{icon} {name} в {sign} ({illum} %) — {effect}"

def planet_parade() -> Optional[str]:
    """
    Мини-парад планет, если 3 любых планеты лежат в одном секторе < 90°.
    """
    jd = swe.julday(*dt.datetime.utcnow().timetuple()[:3])
    lons = sorted(
        swe.calc_ut(jd, body)[0][0]
        for body in (swe.MERCURY, swe.VENUS, swe.MARS, swe.JUPITER, swe.SATURN)
    )
    best = min((lons[i+2] - lons[i]) % 360 for i in range(len(lons) - 2))
    return "Мини-парад планет" if best < 90 else None

def eta_aquarids() -> Optional[str]:
    """Пиковый период метеорного потока Eta Aquarids (дн. 120–140)."""
    yday = dt.datetime.utcnow().timetuple().tm_yday
    return "Eta Aquarids (метеоры)" if 120 <= yday <= 140 else None

def upcoming_event(days: int = 3) -> Optional[str]:
    """
    Заглушка для преданонса события через N дней.
    Пока возвращает только для days==3.
    """
    if days == 3:
        return "Через 3 дня частное солнечное затмение"
    return None

def astro_events() -> List[str]:
    """
    Собирает список всех астрособытий:
      • текущая фаза
      • мини-парад, если есть
      • Eta Aquarids, если в диапазоне
      • преданонс через upcoming_event()
    """
    ev: List[str] = [moon_phase()]
    if p := planet_parade():
        ev.append(p)
    if m := eta_aquarids():
        ev.append(m)
    if u := upcoming_event():
        ev.append(u)
    return ev
