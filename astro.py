#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import datetime as dt
import math
import swisseph as swe
from typing import Optional, List

SIGNS   = ["Козероге","Водолее","Рыбах","Овне", ...]  # тот же список
EFFECT = [ "фокусирует...", ... ]                  # тот же список
MOON_ICONS = "🌑🌒🌓🌔🌕🌖🌗🌘"

def moon_phase() -> str:
    jd   = swe.julday(*dt.datetime.utcnow().timetuple()[:3])
    sun  = swe.calc_ut(jd, swe.SUN)[0][0]
    moon = swe.calc_ut(jd, swe.MOON)[0][0]
    phase = ((moon-sun+360)%360)/360
    illum = round(abs(math.cos(math.pi*phase))*100)
    icon  = MOON_ICONS[int(phase*8)%8]
    name  = ("Новолуние" if illum<5 else ...)
    sign  = int(moon//30)
    return f"{icon} {name} в {SIGNS[sign]} ({illum} %) — {EFFECT[sign]}"

def planet_parade() -> Optional[str]:
    jd = swe.julday(*dt.datetime.utcnow().timetuple()[:3])
    lons = sorted(... )
    best = min(... )
    return "Мини-парад планет" if best<90 else None

def eta_aquarids() -> Optional[str]:
    y = dt.datetime.utcnow().timetuple().tm_yday
    return "Eta Aquarids (метеоры)" if 120<=y<=140 else None

def upcoming_event(days:int=3) -> Optional[str]:
    return f"Через {days} дня частное солнечное затмение" if days==3 else None

def astro_events() -> List[str]:
    ev = [moon_phase()]
    if p:=planet_parade():     ev.append(p)
    if m:=eta_aquarids():      ev.append(m)
    if u:=upcoming_event():    ev.append(u)
    return ev
