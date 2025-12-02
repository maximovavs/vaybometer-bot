#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
post_common.py

Общий модуль формирования и отправки ежедневных постов
для Кипра, Калининграда и др. регионов.

Задачи:
- собрать данные (погода, море, "космопогода", пыльца, радиация);
- сформировать текст поста;
- отправить его в Telegram (и при необходимости в другие каналы).

Модуль не привязан к конкретному региону — всё настраивается
через аргументы main_common().
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

import pytz
from aiogram import Bot
from aiogram.enums import ParseMode

import fx
import pollen
import radiation
import safe_cast as safecast
import schumann
import settings_cy
import settings_world_en
import utils
import weather

# ---------------------------------------------------------------------------
# ЛОГИРОВАНИЕ
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)
if not logger.handlers:
    h = logging.StreamHandler()
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    h.setFormatter(fmt)
    logger.addHandler(h)
logger.setLevel(logging.INFO)

ROOT = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# ОБЩИЕ ДАТА-КЛАССЫ
# ---------------------------------------------------------------------------


@dataclass
class CityWeather:
    name: str
    lat: float
    lon: float
    temp_min: Optional[float] = None
    temp_max: Optional[float] = None
    code: Optional[int] = None
    code_emoji: str = ""
    wind_speed: Optional[float] = None
    wind_gusts: Optional[float] = None
    wind_dir_short: str = ""
    pressure: Optional[float] = None
    pressure_trend: str = ""
    water_temp: Optional[float] = None
    water_comment: str = ""
    sup_comment: str = ""


# ---------------------------------------------------------------------------
# ВРЕМЯ / ДАТА
# ---------------------------------------------------------------------------


def local_today(tz_name: str) -> date:
    tz = pytz.timezone(tz_name)
    now = datetime.now(tz)
    return now.date()


def local_now(tz_name: str) -> datetime:
    tz = pytz.timezone(tz_name)
    return datetime.now(tz)


def fmt_date_human(d: date, tz_name: str) -> str:
    # d уже локальная дата, tz_name — только для красоты
    # Пока не используем локализацию месяца
    return d.strftime("%d.%m.%Y")


# ---------------------------------------------------------------------------
# УТИЛИТЫ ДЛЯ ПОГОДЫ
# ---------------------------------------------------------------------------


def _iter_city_pairs(
    cities: Mapping[str, Tuple[float, float]]
) -> Iterable[Tuple[str, Tuple[float, float]]]:
    """
    Унифицированный итератор по словарю городов:
    { "Limassol": (lat, lon), ... } -> итерируемся по (name, (lat, lon)).
    """
    for name, ll in cities.items():
        yield name, ll


def _coerce_city_list(
    cities_source: Sequence[Tuple[str, Tuple[float, float]]]
) -> List[CityWeather]:
    """
    Приводим список (name, (lat, lon)) к списку CityWeather.
    """
    result: List[CityWeather] = []
    for name, (lat, lon) in cities_source:
        result.append(CityWeather(name=name, lat=lat, lon=lon))
    return result


# ---------------------------------------------------------------------------
# ФОРМАТИРОВАНИЕ ТЕМПЕРАТУРЫ, ВЕТРА, ДАВЛЕНИЯ
# ---------------------------------------------------------------------------

def fmt_temp(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{round(v):d}"


def fmt_wind_speed(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{v:.1f}"


def fmt_pressure(v: Optional[float]) -> str:
    if v is None:
        return "—"
    return f"{int(round(v))}"


def trend_arrow(trend: float) -> str:
    if trend > 0.5:
        return "↑"
    if trend < -0.5:
        return "↓"
    return "→"


# ---------------------------------------------------------------------------
# ЗАГРУЗКА ДАННЫХ
# ---------------------------------------------------------------------------


def load_json(path: Path) -> Any:
    if not path.exists():
        logger.warning("JSON not found: %s", path)
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.error("Failed to read JSON %s: %s", path, e)
        return None


# ---------------------------------------------------------------------------
# СБОР ПОГОДЫ ДЛЯ ГОРОДОВ
# ---------------------------------------------------------------------------


def enrich_weather_for_city_list(
    city_list: List[CityWeather],
    weather_data: Mapping[str, Any],
    water_data: Optional[Mapping[str, Any]] = None,
    sup_map: Optional[Mapping[str, str]] = None,
) -> None:
    """
    Мутатирующая функция: заполняем объекты CityWeather данными из
    заранее собранных структур weather_data / water_data.
    """
    for city in city_list:
        wd = weather_data.get(city.name) or {}
        city.temp_min = wd.get("temp_min")
        city.temp_max = wd.get("temp_max")
        city.code = wd.get("code")
        city.code_emoji = wd.get("code_emoji", "")
        city.wind_speed = wd.get("wind_speed")
        city.wind_gusts = wd.get("wind_gusts")
        city.wind_dir_short = wd.get("wind_dir_short", "")
        city.pressure = wd.get("pressure")
        city.pressure_trend = wd.get("pressure_trend", "")

        if water_data:
            w = water_data.get(city.name) or {}
            city.water_temp = w.get("water_temp")
            city.water_comment = w.get("water_comment", "")

        if sup_map:
            city.sup_comment = sup_map.get(city.name, "")


# ---------------------------------------------------------------------------
# ТЕКСТОВЫЕ БЛОКИ ДЛЯ ПОГОДЫ
# ---------------------------------------------------------------------------


def build_city_weather_line(city: CityWeather, is_sea: bool = False) -> str:
    """
    Формирует основную строку по городу:
    "😎 Лимассол: 21/13 °C • 🌥 пасм • 💨 3.3 м/с (ЮВ) • порывы 8 • 1010 гПа ↓ • 🌊 24"
    """
    temp_str = f"{fmt_temp(city.temp_max)}/{fmt_temp(city.temp_min)} °C"

    wind_str = f"💨 {fmt_wind_speed(city.wind_speed)} м/с"
    if city.wind_dir_short:
        wind_str += f" ({city.wind_dir_short})"
    if city.wind_gusts is not None:
        wind_str += f" • порывы {int(round(city.wind_gusts))}"

    press_str = ""
    if city.pressure is not None:
        arrow = city.pressure_trend or ""
        if not arrow:
            arrow = "→"
        press_str = f" • {fmt_pressure(city.pressure)} гПа {arrow}"

    icon = city.code_emoji or "🌡"

    parts = [
        f"{icon} {city.name}: {temp_str}",
        f"{wind_str}{press_str}",
    ]

    if is_sea and city.water_temp is not None:
        parts.append(f"• 🌊 {fmt_temp(city.water_temp)}")

    return " • ".join(parts)


def build_sea_extra_line(city: CityWeather) -> Optional[str]:
    """
    Дополнительная линия для морских городов:
    "🧜‍♂️ Отлично: SUP (NE/cross)"
    """
    msg_parts: List[str] = []

    if city.water_comment:
        msg_parts.append(city.water_comment)

    if city.sup_comment:
        msg_parts.append(city.sup_comment)

    if not msg_parts:
        return None

    return "   🧜‍♂️ " + " ".join(msg_parts)


# ---------------------------------------------------------------------------
# ГРУППОВЫЕ БЛОКИ (SEA / CONTINENTAL)
# ---------------------------------------------------------------------------


def build_city_block(
    title: str,
    cities: Sequence[CityWeather],
    sea_mode: bool = False,
    warm_split_temp: Optional[float] = None,
) -> str:
    """
    Формирует текстовый блок по списку городов.

    Если warm_split_temp задан, делим на "тёплые" и "прохладные" города.
    """
    if not cities:
        return ""

    lines: List[str] = [title]

    if warm_split_temp is not None:
        warm: List[CityWeather] = []
        cold: List[CityWeather] = []
        for c in cities:
            if c.temp_max is None:
                cold.append(c)
            elif c.temp_max >= warm_split_temp:
                warm.append(c)
            else:
                cold.append(c)

        if warm:
            lines.append("Тёплые города:")
            for c in warm:
                lines.append(build_city_weather_line(c, is_sea=sea_mode))
                extra = build_sea_extra_line(c)
                if extra:
                    lines.append(extra)

        if cold:
            if warm:
                lines.append("Холоднее:")
            for c in cold:
                lines.append(build_city_weather_line(c, is_sea=sea_mode))
                extra = build_sea_extra_line(c)
                if extra:
                    lines.append(extra)

    else:
        for c in cities:
            lines.append(build_city_weather_line(c, is_sea=sea_mode))
            extra = build_sea_extra_line(c)
            if extra:
                lines.append(extra)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ПЫЛЬЦА, РАДИАЦИЯ, ШУМАН, SAFecast
# ---------------------------------------------------------------------------


def build_pollen_block(pollen_info: Optional[Dict[str, Any]]) -> str:
    if not pollen_info:
        return "Пыльца: данных нет."
    return pollen.format_pollen_block(pollen_info)


def build_radiation_block(rad_info: Optional[Dict[str, Any]]) -> str:
    if not rad_info:
        return "Радиация: данных нет."
    return radiation.format_radiation_block(rad_info)


def build_schumann_block(sch_info: Optional[Dict[str, Any]]) -> str:
    if not sch_info:
        return "Шумановский резонанс: данных нет."
    return schumann.format_schumann_block(sch_info)


def build_safecast_block(safe_info: Optional[Dict[str, Any]]) -> str:
    if not safe_info:
        return "Safecast: нет свежих измерений."
    return safecast.format_safecast_block(safe_info)


# ---------------------------------------------------------------------------
# ASTRO / FX / ДРУГОЕ
# ---------------------------------------------------------------------------


def build_fx_block(fx_info: Optional[Dict[str, Any]]) -> str:
    if not fx_info:
        return "Валюты: данных нет."
    return fx.format_fx_block(fx_info)


def build_astro_block(
    astro_today: Optional[Dict[str, Any]],
    tz_name: str,
) -> str:
    """
    Строим небольшой блок "Астрособытия" для конца сообщения.
    """
    if not astro_today:
        return "Астрособытия: данных нет."

    # здесь используется логика из astro.py / lunar_calendar.json
    line = astro_today.get("line") or ""
    if not line:
        return "Астрособытия: данных нет."

    return line


# ---------------------------------------------------------------------------
# СБОР ВСЕХ ДАННЫХ ДЛЯ ДНЯ
# ---------------------------------------------------------------------------


def collect_all_data_for_region(
    *,
    today: date,
    tz_name: str,
    sea_cities_pairs: Sequence[Tuple[str, Tuple[float, float]]],
    other_cities_pairs: Sequence[Tuple[str, Tuple[float, float]]],
    warm_split_temp: Optional[float] = None,
    region_settings: Any,
) -> Dict[str, Any]:
    """
    Собираем всю информацию по региону в один словарь.
    """

    # Погода (воздух и море)
    logger.info("Collecting weather for region...")
    sea_weather = weather.collect_weather_block(
        today=today,
        tz_name=tz_name,
        cities_pairs=sea_cities_pairs,
        settings=region_settings,
    )
    other_weather = weather.collect_weather_block(
        today=today,
        tz_name=tz_name,
        cities_pairs=other_cities_pairs,
        settings=region_settings,
    )

    # Вода (только для морских городов)
    sea_names = [name for name, _ll in sea_cities_pairs]
    water_data = weather.collect_water_temps(
        today=today,
        tz_name=tz_name,
        sea_cities=sea_names,
        settings=region_settings,
    )

    # SUP и прочие комментарии по морю
    sup_map = weather.collect_sup_recommendations(
        today=today,
        tz_name=tz_name,
        sea_cities=sea_names,
        settings=region_settings,
    )

    # Пыльца
    pollen_info = pollen.collect_pollen(today=today, tz_name=tz_name)

    # Радиация
    rad_info = radiation.collect_radiation(today=today, tz_name=tz_name)

    # Шумановский резонанс
    sch_info = schumann.collect_schumann(today=today, tz_name=tz_name)

    # Safecast
    safe_info = safecast.collect_safecast(today=today, tz_name=tz_name)

    # FX
    fx_info = fx.collect_fx(today=today, tz_name=tz_name)

    # Астро (для блока в конце)
    astro_info = weather.collect_astro_summary(today=today, tz_name=tz_name)

    return {
        "sea_weather": sea_weather,
        "other_weather": other_weather,
        "water_data": water_data,
        "sup_map": sup_map,
        "pollen": pollen_info,
        "radiation": rad_info,
        "schumann": sch_info,
        "safecast": safe_info,
        "fx": fx_info,
        "astro": astro_info,
        "warm_split_temp": warm_split_temp,
    }


# ---------------------------------------------------------------------------
# СБОРКА СООБЩЕНИЯ
# ---------------------------------------------------------------------------


def build_message(
    *,
    region_title: str,
    today: date,
    tz_name: str,
    sea_label: str,
    sea_cities_pairs: Sequence[Tuple[str, Tuple[float, float]]],
    other_label: str,
    other_cities_pairs: Sequence[Tuple[str, Tuple[float, float]]],
    data: Dict[str, Any],
) -> str:
    """
    Формирует итоговый текст сообщения для региона.
    """

    lines: List[str] = []

    date_str = fmt_date_human(today, tz_name)
    lines.append(f"{region_title}: погода на завтра ({date_str})")

    # Морские города
    sea_city_list = _coerce_city_list(sea_cities_pairs)
    enrich_weather_for_city_list(
        sea_city_list,
        data["sea_weather"],
        data["water_data"],
        data["sup_map"],
    )
    sea_block = build_city_block(sea_label, sea_city_list, sea_mode=True)
    lines.append(sea_block)
    lines.append("———")

    # Континентальные города
    other_city_list = _coerce_city_list(other_cities_pairs)
    enrich_weather_for_city_list(
        other_city_list,
        data["other_weather"],
        data["water_data"],
        data["sup_map"],
    )
    warm_split_temp = data.get("warm_split_temp")
    other_block = build_city_block(
        other_label,
        other_city_list,
        sea_mode=False,
        warm_split_temp=warm_split_temp,
    )
    lines.append(other_block)
    lines.append("———")

    # Астрономические события / рассвет / пр.
    astro_block = build_astro_block(data.get("astro"), tz_name)
    lines.append("🌌 Астрособытия")
    lines.append(astro_block)

    # Пыльца / воздух / радиация / шуман / safecast
    lines.append("———")
    lines.append(build_pollen_block(data.get("pollen")))
    lines.append(build_radiation_block(data.get("radiation")))
    lines.append(build_schumann_block(data.get("schumann")))
    lines.append(build_safecast_block(data.get("safecast")))
    lines.append(build_fx_block(data.get("fx")))

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ОТПРАВКА В TELEGRAM
# ---------------------------------------------------------------------------


async def send_common_post(
    *,
    bot: Bot,
    chat_id: str,
    text: str,
    parse_mode: str = "HTML",
) -> None:
    """
    Универсальная отправка сообщения в Телеграм.
    """
    logger.info("Sending message to chat_id=%s", chat_id)
    await bot.send_message(
        chat_id=chat_id,
        text=text,
        parse_mode=parse_mode,
        disable_web_page_preview=True,
    )
    logger.info("Message sent.")


# ---------------------------------------------------------------------------
# MAIN_... ДЛЯ РЕГИОНОВ
# ---------------------------------------------------------------------------


async def main_common(
    *,
    bot: Bot,
    chat_id: str,
    region_title: str,
    tz_name: str,
    sea_label: str,
    sea_cities: Mapping[str, Tuple[float, float]],
    other_label: str,
    other_cities: Mapping[str, Tuple[float, float]],
    warm_split_temp: Optional[float],
    region_settings: Any,
) -> None:
    """
    Общая «точка входа» для ежедневного поста по региону.

    Все конкретные скрипты (post_cy.py, post_kld.py и т.п.) просто собирают
    нужные аргументы и вызывают main_common().
    """

    today = local_today(tz_name)
    logger.info("Дата зафиксирована как %s (TZ %s)", today, tz_name)

    sea_pairs = list(_iter_city_pairs(sea_cities))
    other_pairs = list(_iter_city_pairs(other_cities))

    data = collect_all_data_for_region(
        today=today,
        tz_name=tz_name,
        sea_cities_pairs=sea_pairs,
        other_cities_pairs=other_pairs,
        warm_split_temp=warm_split_temp,
        region_settings=region_settings,
    )

    msg = build_message(
        region_title=region_title,
        today=today,
        tz_name=tz_name,
        sea_label=sea_label,
        sea_cities_pairs=sea_pairs,
        other_label=other_label,
        other_cities_pairs=other_pairs,
        data=data,
    )

    await send_common_post(
        bot=bot,
        chat_id=chat_id,
        text=msg,
        parse_mode=ParseMode.HTML,
    )


# ---------------------------------------------------------------------------
# CLI / ОТЛАДКА
# ---------------------------------------------------------------------------


if __name__ == "__main__":
    # Пример локального запуска (для отладки: выведет только текст).
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--region", choices=["cy", "world_en"], default="cy")
    args = parser.parse_args()

    if args.region == "cy":
        settings = settings_cy
        tz_name = settings.TIMEZONE
        sea_cities = settings.SEA_CITIES
        other_cities = settings.OTHER_CITIES
        warm_split = 20.0
        region_title = "Кипр"
        sea_label = "🏖 Морские города"
        other_label = "🏞 Континентальные города"
    else:
        settings = settings_world_en
        tz_name = settings.TIMEZONE
        sea_cities = settings.SEA_CITIES
        other_cities = settings.OTHER_CITIES
        warm_split = None
        region_title = "World"
        sea_label = "Coastal cities"
        other_label = "Inland cities"

    today = local_today(tz_name)
    sea_pairs = list(_iter_city_pairs(sea_cities))
    other_pairs = list(_iter_city_pairs(other_cities))

    data = collect_all_data_for_region(
        today=today,
        tz_name=tz_name,
        sea_cities_pairs=sea_pairs,
        other_cities_pairs=other_pairs,
        warm_split_temp=warm_split,
        region_settings=settings,
    )

    msg = build_message(
        region_title=region_title,
        today=today,
        tz_name=tz_name,
        sea_label=sea_label,
        sea_cities_pairs=sea_pairs,
        other_label=other_label,
        other_cities_pairs=other_pairs,
        data=data,
    )
    print(msg)
