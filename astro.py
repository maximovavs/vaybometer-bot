#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
astro.py

• Иконки фаз Луны MOON_ICONS (от новолуния до полнолуния).
• Функция upcoming_event(days=3) возвращает преданонс (пример).
• astro_events() → список актуальных астрособытий + опциональный преданонс.
"""

import math
import datetime as dt
from typing import List, Optional

import swisseph as swe

# Знаки зодиака и их влияние
SIGNS = [
    "Овне", "Тельце", "Близнецах", "Раке", "Льве", "Деве",
    "Весах", "Скорпионе", "Стрельце", "Козероге", "Водолее", "Рыбах",
]
EFFECT = [
    "фокусирует на деле",
    "дарит странные идеи",
    "усиливает эмпатию",
    "придаёт смелости",
    "настраивает на комфорт",
    "повышает коммуникабельность",
    "усиливает заботу",
    "разжигает творческий огонь",
    "настраивает на порядок",
    "заставляет искать баланс",
    "поднимает страсть",
    "толкает к приключениям",
]

# Иконки фаз Луны от новой до последней
MOON_ICONS = "🌑🌒🌓🌔🌕🌖🌗🌘"


def moon_phase() -> str:
    """
    Вычисляет текущую лунную фазу, процент освещённости и знак Луны.
    Возвращает строку вида:
        "<icon> <Название фазы> в <Знак> (<%> освещ.) — <Эффект>"
    """
    # юлианская дата текущего дня UTC
    now = dt.datetime.utcnow()
    jd = swe.julday(now.year, now.month, now.day)
    sun_lon = swe.calc_ut(jd, swe.SUN)[0][0]
    moon_lon = swe.calc_ut(jd, swe.MOON)[0][0]

    # фазовый угол [0,1)
    phase = ((moon_lon - sun_lon + 360) % 360) / 360
    illum = round(abs(math.cos(math.pi * phase)) * 100)

    # выбор иконки и названия фазы
    icon = MOON_ICONS[int(phase * len(MOON_ICONS)) % len(MOON_ICONS)]
    if illum < 5:
        name = "Новолуние"
    elif phase < 0.5:
        name = "Растущая Луна"
    elif illum > 95:
        name = "Полнолуние"
    else:
        name = "Убывающая Луна"

    # знак по долготе Луны
    sign = SIGNS[int(moon_lon // 30) % 12]

    return f"{icon} {name} в {sign} ({illum}% освещ.) — {EFFECT[int(moon_lon // 30) % 12]}"


def planet_parade() -> Optional[str]:
    """
    Проверяет, есть ли мини-парад из трёх планет в секторе < 90°.
    Если есть — возвращает "Мини-парад планет", иначе None.
    """
    now = dt.datetime.utcnow()
    jd = swe.julday(now.year, now.month, now.day)
    planets = [swe.MERCURY, swe.VENUS, swe.MARS, swe.JUPITER, swe.SATURN]
    lons = sorted(swe.calc_ut(jd, p)[0][0] for p in planets)
    # минимальный разброс трёх подряд идущих
    for i in range(len(lons) - 2):
        span = (lons[i+2] - lons[i]) % 360
        if span < 90:
            return "Мини-парад планет"
    return None


def eta_aquarids() -> Optional[str]:
    """
    Возвращает событие метеорного потока Eta Aquarids,
    если текущий день года в диапазоне 120–140 (примерно май).
    """
    doy = dt.datetime.utcnow().timetuple().tm_yday
    if 120 <= doy <= 140:
        return "Eta Aquarids (метеоры)"
    return None


def upcoming_event(days: int = 3) -> Optional[str]:
    """
    Заглушка для предстоящего астрологического события через заданное
    количество дней. Сейчас для days==3 возвращает пример.
    """
    if days == 3:
        return "Через 3 дня частное солнечное затмение"
    return None


def astro_events() -> List[str]:
    """
    Собирает список астрологических событий:
      1) текущая лунная фаза
      2) мини-парад планет (если есть)
      3) Eta Aquarids (если идёт)
      4) преданонс upcoming_event()
    """
    ev: List[str] = []
    ev.append(moon_phase())
    if p := planet_parade():
        ev.append(p)
    if m := eta_aquarids():
        ev.append(m)
    if ann := upcoming_event():
        ev.append(ann)
    return ev
