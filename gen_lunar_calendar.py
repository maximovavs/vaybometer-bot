#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gen_lunar_calendar.py
──────────────────────────────────────────────────────────────────────────────
Формирует файл lunar_calendar.json со всеми полями, нужными и для ежедневных
постов (короткие советы) и для месячного (длинные описания фаз + VoC).

• phase_name    – «Новолуние», «Растущий серп» и т.д.
• phase         – строка с эмодзи + названием фазы + знаком + процентом
• percent       – число (0–100)
• sign          – текстовый знак зодиака («Овен», «Телец» …)
• phase_time    – точное время начала фазы в формате ISO (Asia/Nicosia → JSON)
• advice        – массив из трёх советов (три строки: 💼…, ⛔…, 🪄…)
• long_desc     – 1–2 предложения на каждый уникальный тип фазы (масячный обзор)
• void_of_course: {"start": "DD.MM HH:mm", "end": "DD.MM HH:mm"}  (если нет – null)
• favorable_days / unfavorable_days – словари категорий CATS (карманные даты)
"""

import os
import json
import math
import asyncio
import random
from pathlib import Path
from typing import Dict, Any, List, Tuple

import pendulum
import swisseph as swe

TZ = pendulum.timezone("Asia/Nicosia")

# ───── GPT (по возможности) ────────────────────────────────────────────────
try:
    from openai import OpenAI
    GPT = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
except Exception:
    GPT = None

# ───── Эмодзи фаз ──────────────────────────────────────────────────────────
EMO = {
    "Новолуние":       "🌑",
    "Растущий серп":   "🌒",
    "Первая четверть": "🌓",
    "Растущая Луна":   "🌔",
    "Полнолуние":      "🌕",
    "Убывающая Луна":  "🌖",
    "Последняя четверть": "🌗",
    "Убывающий серп":  "🌘",
}

# ───── Фолбэк для длинного описания фазы ───────────────────────────────────
FALLBACK_LONG: Dict[str, str] = {
    "Новолуние":        "Нулевая точка цикла — закладывайте мечты и намерения.",
    "Растущий серп":    "Энергия прибавляется — время запускать новые задачи.",
    "Первая четверть":  "Первые трудности проявились, корректируйте курс и действуйте.",
    "Растущая Луна":    "Ускорение: расширяйте проекты, укрепляйте связи.",
    "Полнолуние":       "Кульминация: максимум эмоций и результатов.",
    "Убывающая Луна":   "Отпускаем лишнее, завершаем дела, наводим порядок.",
    "Последняя четверть": "Аналитика, ретроспектива и пересмотр стратегии.",
    "Убывающий серп":   "Отдых, ретриты, подготовка к новому циклу.",
}

# ───── Карманные даты для категорий ────────────────────────────────────────
CATS = {
    "general":  {"favorable": [2, 3, 9, 27],   "unfavorable": [13, 14, 24]},
    "haircut":  {"favorable": [2, 3, 9],       "unfavorable": []},
    "travel":   {"favorable": [4, 5],         "unfavorable": []},
    "shopping": {"favorable": [1, 2, 7],       "unfavorable": []},
    "health":   {"favorable": [20, 21, 27],    "unfavorable": []},
}

# ───── Перевод JD → pendulum DateTime (UTC) ─────────────────────────────────
def jd2dt(jd: float) -> pendulum.DateTime:
    """
    Конвертирует UT-юлианскую дату в pendulum.DateTime с tz="UTC".
    """
    timestamp = (jd - 2440587.5) * 86400.0
    return pendulum.from_timestamp(timestamp, tz="UTC")

# ───── Определение названия фазы по углу (angle) ────────────────────────────
def phase_name(angle: float) -> str:
    """
    Разбиваем круг 360° на 8 секторов (45° каждый), сдвиг 22.5°,
    чтобы границы соответствовали классическим названиям.
    """
    idx = int(((angle + 22.5) % 360) // 45)
    return [
        "Новолуние",
        "Растущий серп",
        "Первая четверть",
        "Растущая Луна",
        "Полнолуние",
        "Убывающая Луна",
        "Последняя четверть",
        "Убывающий серп",
    ][idx]

# ───── Вычисление фазы, процента освещённости и зодиака ────────────────────
def compute_phase(jd: float) -> Tuple[str, int, str]:
    """
    По JD (UT) вычисляет:
      • lon_s   – долготa Солнца
      • lon_m   – долготa Луны
      • angle   – фазовый угол = (lon_m - lon_s) mod 360
      • illum   – процент освещённости = round((1 - cos(angle)) / 2 * 100)
      • name    – одно из 8 названий фазы по углу
      • sign    – знак зодиака по долготе Луны (каждый 30°)
    """
    lon_s = swe.calc_ut(jd, swe.SUN)[0][0]
    lon_m = swe.calc_ut(jd, swe.MOON)[0][0]
    angle = (lon_m - lon_s) % 360.0
    illum = int(round((1 - math.cos(math.radians(angle))) / 2 * 100))
    name = phase_name(angle)
    sign = [
        "Овен", "Телец", "Близнецы", "Рак", "Лев", "Дева",
        "Весы", "Скорпион", "Стрелец", "Козерог", "Водолей", "Рыбы"
    ][int(lon_m // 30) % 12]
    return name, illum, sign

# ───── Void-of-Course (приближённый алгоритм) ──────────────────────────────
ASPECTS = {0, 60, 90, 120, 180}  # основные мажорные аспекты
ORBIS = 1.5                     # ±градусы погрешности
PLANETS = [
    swe.SUN, swe.MERCURY, swe.VENUS, swe.MARS,
    swe.JUPITER, swe.SATURN, swe.URANUS, swe.NEPTUNE, swe.PLUTO
]

def _has_major_lunar_aspect(jd: float) -> bool:
    """
    Проверяет, есть ли к этому моменту (JD) точный мажорный аспект Луны
    к любому из указанных PLANETS в пределах ORBIS.
    """
    lon_m = swe.calc_ut(jd, swe.MOON)[0][0]
    for p in PLANETS:
        lon_p = swe.calc_ut(jd, p)[0][0]
        diff = abs((lon_m - lon_p + 180) % 360 - 180)
        for asp in ASPECTS:
            if abs(diff - asp) <= ORBIS:
                return True
    return False

def compute_voc_for_day(jd_start: float) -> Dict[str, Any]:
    """
    Приближённо находит Void-of-Course (VOC) для Луны в пределах суток,
    начинающихся с jd_start (00:00 UT).
    Алгоритм:
      1) Определить знак Луны в jd_start (целая часть (lon_m // 30)).
      2) Шагами по 1 часу вперёд искать момент перехода Луны в следующий знак.
      3) От найденного перехода (sign_change) шагать назад по 10 минут,
         пока не встречается последний аспект → это будет начало VOC.
      4) Если начало и конец лежат за пределами тех же календарных суток (Asia/Nicosia),
         то возвращаем {start: null, end: null}.
      5) Иначе возвращаем {"start": "DD.MM HH:mm", "end": "DD.MM HH:mm"}.
    """
    # 1) Найдём знак Луны в jd_start
    sign0 = int(swe.calc_ut(jd_start, swe.MOON)[0][0] // 30)

    # 2) Идём вперёд шагами 1 час (1/24 суток), пока знак не изменится
    jd = jd_start
    step_forward = 1.0 / 24.0  # 1 час
    while True:
        jd += step_forward
        if int(swe.calc_ut(jd, swe.MOON)[0][0] // 30) != sign0:
            sign_change = jd
            break

    # 3) Идём назад от sign_change шагами 10 мин (10/1440 суток),
    #    пока не встретим аспект → это будет конец последнего аспекта
    jd_back = sign_change
    step_back = 10.0 / 1440.0  # 10 минут
    while jd_back > jd_start and not _has_major_lunar_aspect(jd_back):
        jd_back -= step_back

    voc_start = jd_back
    voc_end = sign_change

    # Конвертируем JD → pendulum.DateTime (UTC) → в локальную зону TZ
    start_dt = jd2dt(voc_start).in_tz(TZ)
    end_dt = jd2dt(voc_end).in_tz(TZ)

    # Проверяем, лежат ли оба в пределах одних и тех же календарных суток (Asia/Nicosia)
    cur_day = jd2dt(jd_start).in_tz(TZ).date()
    if start_dt.date() != cur_day and end_dt.date() != cur_day:
        return {"start": None, "end": None}

    return {
        "start": start_dt.format("DD.MM HH:mm"),
        "end":   end_dt.format("DD.MM HH:mm"),
    }

# ───── GPT-helpers для советов ─────────────────────────────────────────────
async def gpt_short(date: str, phase: str) -> List[str]:
    """
    Запрашивает у GPT-4O-Mini три однострочных совета с эмодзи:
      💼 (работа), ⛔ (что отложить), 🪄 (ритуал).
    Если нет GPT или юбозможности, возвращаем заранее заготовленный фолбэк.
    """
    if GPT:
        prompt = (
            f"Дата {date}, фаза {phase}. "
            "Действуй как профессиональный астролог, который строго и кратко дает "
            "три советы (каждый пункт с эмодзи):\n"
            "  💼 (работа/финансы)\n"
            "  ⛔ (что отложить)\n"
            "  🪄 (ритуал дня)\n"
            "Будь емок, как будто каждое слово дорого стоит."
        )
        try:
            resp = GPT.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.65
            )
            lines = [ln.strip() for ln in resp.choices[0].message.content.splitlines() if ln.strip()]
            return lines[:3]
        except Exception:
            pass

    # Фолбэк, если GPT недоступен
    return [
        "💼 Сфокусируйтесь на главном и не распыляйтесь.",
        "⛔ Отложите крупные решения и расходы.",
        "🪄 Сделайте короткую медитацию на внутреннюю гармонию."
    ]

async def gpt_long(name: str, month: str) -> str:
    """
    Запрашивает у GPT-4O-Mini 1–2 предложения общего описания энергии фазы.
    Если нет GPT или сбой – возвращаем FALLBACK_LONG[name].
    """
    if GPT:
        prompt = (
            f"Месяц {month}. Фаза {name}. "
            "Ты профессиональный астролог: дай 2 коротких предложения о том, "
            "какова энергия этого периода. Тон – экспертный и вдохновляющий."
        )
        try:
            resp = GPT.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                temperature=0.7
            )
            return resp.choices[0].message.content.strip()
        except Exception:
            pass

    return FALLBACK_LONG.get(name, "")

# ───── Основной генератор календаря ──────────────────────────────────────────
async def generate(year: int, month: int) -> Dict[str, Any]:
    """
    1) sweep.set_ephe_path('.') для доступности эфемерид
    2) Проходим по каждой дате месяца (1 → последний день)
    3) Вычисляем фазу, знак, процент, время фазы
    4) Запускаем асинхронные задачи:
         • gpt_short для каждого дня
         • gpt_long (один раз) для каждой уникальной фазы
    5) Вычисляем Void-of-Course для каждого дня
    6) Формируем базовую запись cal[d.to_date_string()]
    7) Ждём завершения всех GPT-задач и заполняем advice и long_desc
    """
    swe.set_ephe_path(".")
    first = pendulum.date(year, month, 1)
    last = first.end_of("month")

    cal: Dict[str, Any] = {}
    short_tasks: List[asyncio.Task] = []
    long_tasks: Dict[str, asyncio.Task] = {}

    # 3) Пробегаем каждую дату месяца
    d = first
    while d <= last:
        jd = swe.julday(d.year, d.month, d.day, 0.0)

        # Фаза, процент, знак
        name, illum, sign = compute_phase(jd)
        emoji = EMO.get(name, "")
        phase_time_iso = jd2dt(jd).in_tz(TZ).to_iso8601_string()

        # Планируем асинхронную задачу для short advice
        short_tasks.append(asyncio.create_task(gpt_short(d.to_date_string(), name)))

        # Если для этой фазы ещё не запланирована long_desc
        if name not in long_tasks:
            long_tasks[name] = asyncio.create_task(gpt_long(name, d.format("MMMM")))

        # Void-of-Course (приближённо) для текущего дня
        voc = compute_voc_for_day(jd)

        cal[d.to_date_string()] = {
            "phase_name": name,
            "phase": f"{emoji} {name} в {sign} ({illum}% освещ.)",
            "percent": illum,
            "sign": sign,
            "phase_time": phase_time_iso,
            "advice": [],           # будет заполнено ниже
            "long_desc": "",        # будет заполнено ниже
            "void_of_course": voc,
            "favorable_days": CATS,
            "unfavorable_days": CATS,
        }

        d = d.add(days=1)

    # 4) Дождаться советов для каждого дня (short advice)
    short_results = await asyncio.gather(*short_tasks, return_exceptions=True)
    idx = 0
    for day_str in sorted(cal.keys()):
        result = short_results[idx]
        if isinstance(result, Exception) or not isinstance(result, list):
            cal[day_str]["advice"] = ["💼 Сфокусируйтесь на главном.", "⛔ Отложите крупные решения.", "🪄 Медитация 5 минут."]
        else:
            cal[day_str]["advice"] = result[:3]
        idx += 1

    # 5) Дождаться long_desc для каждой уникальной фазы
    for ph_name, task in long_tasks.items():
        try:
            desc = await task
        except Exception:
            desc = FALLBACK_LONG.get(ph_name, "")
        # Подставляем в каждую запись, где phase_name == ph_name
        for rec in cal.values():
            if rec["phase_name"] == ph_name:
                rec["long_desc"] = desc

    return cal

# ───── Entry Point ─────────────────────────────────────────────────────────
async def _main():
    today = pendulum.today(TZ)  # текущая дата в TZ, но month/year без разницы
    data = await generate(today.year, today.month)
    out = Path(__file__).parent / "lunar_calendar.json"
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"✅ lunar_calendar.json сформирован для {today.format('MMMM YYYY')}")

if __name__ == "__main__":
    asyncio.run(_main())