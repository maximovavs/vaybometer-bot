#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Общий код формирования постов по регионам (Кипр, мир и т.п.).

Содержит:
- модели городов и показателей;
- форматирование строк, эмодзи и тэгов;
- функции сборки сообщений;
- общую корутину отправки постов в Telegram.
"""

from __future__ import annotations

import os, re, json, html, asyncio, logging, math, random, hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple, Union

import pendulum
from dateutil.relativedelta import relativedelta
from telegram import Bot, constants

try:
    from world_en.imagegen import generate_astro_image  # type: ignore
except Exception:
    try:
        from imagegen import generate_astro_image  # type: ignore
    except Exception:
        generate_astro_image = None  # type: ignore


logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

ROOT_DIR = Path(__file__).resolve().parent
DATA_DIR = ROOT_DIR / "data"
CACHE_DIR = ROOT_DIR / ".cache"

# На будущее: если захочется включать/выключать мировую Kp
USE_WORLD_KP = True

# Изображения для вечернего поста по Кипру
CY_IMAGE_ENABLED = os.getenv('CY_IMAGE_ENABLED', '1').strip().lower() not in ('0', 'false', 'no', 'off')
CY_IMAGE_DIR = Path(os.getenv('CY_IMAGE_DIR', 'cy_img'))

# ---------------------------------------------------------------------------
# Общие утилиты
# ---------------------------------------------------------------------------


def load_json(path: Union[str, Path], default: Any = None) -> Any:
    """Безопасная загрузка JSON (с возвратом default при ошибке)."""
    p = Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text("utf-8"))
    except Exception:
        logger.exception("Failed to load JSON from %s", p)
        return default


def save_json(path: Union[str, Path], data: Any) -> None:
    """Безопасная запись JSON."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def _as_tz(tz: Union[pendulum.Timezone, str, None]) -> pendulum.Timezone:
    """Унификация таймзоны."""
    if tz is None:
        return pendulum.timezone("UTC")
    if isinstance(tz, pendulum.Timezone):
        return tz
    try:
        return pendulum.timezone(tz)
    except Exception:
        return pendulum.timezone("UTC")


def round_half_up(x: float, ndigits: int = 0) -> float:
    """
    Округление "от половинки вверх", ближе к понятному человеку.

    1.25 -> 1.3 (при ndigits=1), 2.5 -> 3.0 (при ndigits=0) и т.п.
    """
    factor = 10 ** ndigits
    return math.floor(x * factor + 0.5) / factor


def fmt_temp(v: Optional[float]) -> str:
    """Форматирование температуры."""
    if v is None:
        return "—"
    return f"{int(round(v))} °C"


def fmt_pressure(hpa: Optional[float]) -> str:
    """Форматирование давления."""
    if hpa is None:
        return "— гПа"
    return f"{int(round(hpa))} гПа"


def fmt_speed(ms: Optional[float]) -> str:
    """Форматирование скорости ветра в м/с."""
    if ms is None:
        return "— м/с"
    return f"{round_half_up(ms, 1)} м/с"


def arrow_trend(prev: Optional[float], curr: Optional[float], eps: float = 0.4) -> str:
    """
    Стрелочка тренда давления:
    ↑ если выросло, ↓ если упало, → если почти не изменилось.
    """
    if prev is None or curr is None:
        return ""
    if curr - prev > eps:
        return "↑"
    if prev - curr > eps:
        return "↓"
    return "→"


def wind_dir_to_text(deg: Optional[float]) -> str:
    """
    Конвертация направления ветра в текст (8 румбов).
    0/360 — север, 90 — восток, etc.
    """
    if deg is None:
        return "—"
    dirs = ["С", "СВ", "В", "ЮВ", "Ю", "ЮЗ", "З", "СЗ"]
    ix = int((deg % 360) / 45 + 0.5) % 8
    return dirs[ix]


def deg_to_beaufort(ms: Optional[float]) -> str:
    """Грубая классификация скорости ветра через эмодзи."""
    if ms is None:
        return "💤"
    if ms < 1:
        return "🔹"
    if ms < 4:
        return "💨"
    if ms < 8:
        return "🌬"
    if ms < 14:
        return "🌪"
    return "🌀"


def uv_index_to_emoji(uv: Optional[float]) -> str:
    if uv is None:
        return ""
    if uv < 3:
        return "🟢"
    if uv < 6:
        return "🟡"
    if uv < 8:
        return "🟠"
    if uv < 11:
        return "🔴"
    return "🟣"


def make_sunrise_sunset_line(dt_obj: pendulum.DateTime, tz: pendulum.Timezone) -> str:
    """
    Читабельная строка про рассвет/закат для конкретной даты и TZ.
    """

    from lunar import get_sun_times  # локальный модуль

    sun = get_sun_times(dt_obj.date(), tz)
    if not sun:
        return ""

    sunrise = sun.get("sunrise")
    sunset = sun.get("sunset")
    if not (sunrise and sunset):
        return ""

    sunrise_local = pendulum.instance(sunrise).in_timezone(tz)
    sunset_local = pendulum.instance(sunset).in_timezone(tz)

    return f"🌅 Рассвет завтра: {sunrise_local.strftime('%H:%M')} • 🌇 Закат: {sunset_local.strftime('%H:%M')}"


# ---------------------------------------------------------------------------
# Данные по городам / погоде / морю
# ---------------------------------------------------------------------------


@dataclass
class CityWeather:
    name: str
    temp_max: Optional[float] = None
    temp_min: Optional[float] = None
    descr: str = ""
    wind_speed: Optional[float] = None
    wind_gusts: Optional[float] = None
    wind_dir_deg: Optional[float] = None
    pressure: Optional[float] = None
    pressure_prev: Optional[float] = None
    water_temp: Optional[float] = None
    uv_index: Optional[float] = None
    extra_emoji: str = ""
    rec_text: str = ""

    def is_warm(self, threshold: float = 20.0) -> bool:
        """Простейшая классификация: тёплый / холодный город."""
        if self.temp_max is None:
            return False
        return self.temp_max >= threshold


# ---------------------------------------------------------------------------
# Загрузка данных по регионам
# ---------------------------------------------------------------------------


def load_weather_for_region(region_key: str) -> Dict[str, Any]:
    """Загрузка погодных данных для конкретного региона."""
    path = DATA_DIR / f"{region_key}_weather.json"
    data = load_json(path, default={}) or {}
    return data


def load_marine_for_region(region_key: str) -> Dict[str, Any]:
    """Загрузка морских данных для региона."""
    path = DATA_DIR / f"{region_key}_marine.json"
    data = load_json(path, default={}) or {}
    return data


def load_uv_for_region(region_key: str) -> Dict[str, Any]:
    """Загрузка UV-индекса."""
    path = DATA_DIR / f"{region_key}_uv.json"
    data = load_json(path, default={}) or {}
    return data


def load_kp_index() -> Dict[str, Any]:
    """
    Загрузка глобальных данных Kp-индекса.

    Файл может формироваться отдельным collector-скриптом.
    """
    path = DATA_DIR / "kp_index.json"
    return load_json(path, default={}) or {}


# ---------------------------------------------------------------------------
# Формирование строк для городов
# ---------------------------------------------------------------------------


def build_city_line(city: CityWeather) -> str:
    """
    Строка для города в морском/континентальном блоке.

    Пример:
    "😎 Ларнака: 27/18 °C • ☀ ясно • 💨 3.5 м/с (СВ) • порывы 7 • 1013 гПа ↑ • 🌊 24"
    """
    temp = f"{fmt_temp(city.temp_max)}/{fmt_temp(city.temp_min)}"
    wind = fmt_speed(city.wind_speed)
    gusts = f"{int(round(city.wind_gusts))}" if city.wind_gusts is not None else "—"
    wdir = wind_dir_to_text(city.wind_dir_deg)
    pressure = fmt_pressure(city.pressure)
    trend = arrow_trend(city.pressure_prev, city.pressure)
    water = f"{int(round(city.water_temp))}" if city.water_temp is not None else "—"
    uv_emoji = uv_index_to_emoji(city.uv_index)

    parts = [
        f"{city.extra_emoji or '😌'} {city.name}:",
        f"{temp}",
        f"• {city.descr or '—'}",
        f"• 💨 {wind} ({wdir})",
        f"• порывы {gusts}",
        f"• {pressure} {trend}",
    ]
    if city.water_temp is not None:
        parts.append(f"• 🌊 {water}")
    if uv_emoji:
        parts.append(f"• UV {uv_emoji}")

    return " ".join(parts)


def build_city_recommendation_line(city: CityWeather) -> str:
    """
    Дополнительная строка с мини-рекомендацией по активности.

    Пример:
    "   🧜‍♂️ Отлично: SUP (NE/cross)"
    """
    if not city.rec_text:
        return ""
    base_emoji = "🧜‍♂️"
    return f"   {base_emoji} {city.rec_text}"


# ---------------------------------------------------------------------------
# Сборка блоков по группам городов
# ---------------------------------------------------------------------------


def build_group_block(label: str, cities: Iterable[CityWeather]) -> str:
    lines: List[str] = []
    label = label.strip()
    if label:
        lines.append(label)

    for city in cities:
        lines.append(build_city_line(city))
        rec = build_city_recommendation_line(city)
        if rec:
            lines.append(rec)

    return "\n".join(lines)


def split_cities_by_temp(cities: Iterable[CityWeather], warm_threshold: float = 20.0) -> Tuple[List[CityWeather], List[CityWeather]]:
    """
    Делит города на тёплые и холодные по максимальной температуре.

    Возвращает (warm, cold).
    """
    warm, cold = [], []
    for c in cities:
        if c.is_warm(warm_threshold):
            warm.append(c)
        else:
            cold.append(c)
    return warm, cold


def build_continental_block(label: str, cities: Iterable[CityWeather], warm_threshold: float = 20.0) -> str:
    """
    Формирует блок по континентальным городам, разделяя на "Тёплые" / "Холодные".
    """
    all_cities = list(cities)
    warm, cold = split_cities_by_temp(all_cities, warm_threshold=warm_threshold)

    lines: List[str] = []
    if label.strip():
        lines.append(label)

    if warm:
        lines.append("Тёплые города:")
        for c in warm:
            lines.append(build_city_line(c))
            rec = build_city_recommendation_line(c)
            if rec:
                lines.append(rec)

    if cold:
        lines.append("Холодные города:")
        for c in cold:
            lines.append(build_city_line(c))
            rec = build_city_recommendation_line(c)
            if rec:
                lines.append(rec)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Kp-индекс, космопогода, факты дня
# ---------------------------------------------------------------------------


def kp_level_to_emoji(kp: Optional[float]) -> str:
    if kp is None:
        return "❔"
    if kp < 3:
        return "🟢"
    if kp < 5:
        return "🟡"
    if kp < 7:
        return "🟠"
    return "🔴"


def build_kp_block(kp_data: Dict[str, Any]) -> str:
    """
    Строит блок по геомагнитной обстановке.

    Ожидается, что kp_data содержит поля:
    - "current": float
    - "forecast": [ ... ]
    """
    curr = kp_data.get("current")
    emoji = kp_level_to_emoji(curr)
    if curr is None:
        return f"🧲 Геомагнитка: {emoji} данных нет"
    return f"🧲 Геомагнитка: {emoji} Kp≈{curr}"


def load_fact_of_day(region_key: str, date: pendulum.DateTime) -> str:
    """
    Загружает факт дня для конкретного региона (если есть).
    """
    path = DATA_DIR / f"{region_key}_facts.json"
    data = load_json(path, default={}) or {}
    key = date.to_date_string()
    fact = data.get(key) or data.get("default") or ""
    return str(fact).strip()


# ---------------------------------------------------------------------------
# Сборка итогового сообщения
# ---------------------------------------------------------------------------


def header_line(region_name: str, date: pendulum.DateTime) -> str:
    """
    Формирует заголовок поста, напр.:
    "Кипр: погода на завтра (03.12.2025)"
    """
    return f"{region_name}: погода на завтра ({date.format('DD.MM.YYYY')})"


def astro_hint_block(region_key: str, date: pendulum.DateTime, tz: pendulum.Timezone) -> str:
    """
    Небольшой астроблок (если хотим подсветить какое-то астрособытие).
    Пока заглушка, может дополняться.
    """
    # На текущий момент астроданные подтягиваются в отдельных скриптах,
    # здесь можем просто заглянуть в precomputed JSON.
    path = ROOT_DIR / "lunar_calendar.json"
    data = load_json(path, default={}) or {}
    days = data.get("days") or {}
    today = date.date().isoformat()
    info = days.get(today) or {}

    phase = info.get("phase_name") or ""
    sign = info.get("sign") or ""

    if not phase and not sign:
        return ""

    parts = []
    if phase:
        parts.append(phase)
    if sign:
        parts.append(f"в {sign}")

    base = " ".join(parts).strip()
    if not base:
        return ""

    return f"🌌 Астрособытия\n🌕 {base} — земля под ногами прочна, а аппетит к жизни растёт.\n💰 Время ценить то, что уже есть, и приумножать: вложения и отношения крепнут без суеты."


def hashtags_line(region_key: str) -> str:
    """
    Формирует строку с хэштегами для региона.
    """
    if region_key == "cy":
        return "#Кипр #погода #здоровье #Лимассол #Тродос"
    if region_key == "world":
        return "#WorldVibeMeter #weather #mood #health"
    return "#погода #здоровье"


def _is_cyprus_region(region_name: str) -> bool:
    s = (region_name or "").lower()
    return "кипр" in s or "cyprus" in s


def _pick_cyprus_style_prompt(
    region_name: str,
    tz: Union[pendulum.Timezone, str, None],
    mode: Optional[str],
) -> Optional[tuple[str, str, str]]:
    """Выбор стиля и промпта для вечернего поста по Кипру.

    Возвращает (style_name, prompt, date_str) или None, если картинку
    генерировать не нужно (утро / другой регион / фича выключена).
    """
    if not CY_IMAGE_ENABLED:
        return None

    mode_lc = (mode or "").lower()
    if mode_lc not in ("evening", "tomorrow"):
        return None

    if not _is_cyprus_region(region_name):
        return None

    tz_obj = _as_tz(tz)
    now = pendulum.now(tz_obj)
    date_str = now.to_date_string()

    # Детминированный выбор стиля на день, чтобы при повторах дня
    # получался тот же вариант.
    key = f"cy-image-style|{region_name}|{mode_lc}|{date_str}"
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    idx = digest[0] % 3  # 0..2

    if idx == 0:
        style_name = "sea-sunrise"
        scene = (
            "Soft Mediterranean evening over Cyprus coast, gentle waves, distant hills, "  # noqa: E501
            "subtle city lights along the shore"
        )
    elif idx == 1:
        style_name = "harbor-lights"
        scene = (
            "Warm evening in Cyprus by the sea, harbor silhouettes, boats and reflections "  # noqa: E501
            "on the water"
        )
    else:
        style_name = "balcony-human"
        scene = (
            "Person standing on a hill or balcony in Cyprus, looking at the sea and sky, "  # noqa: E501
            "city lights glowing in the distance"
        )

    base_style = (
        "dreamy minimalist illustration, pastel colors, subtle gradients, soft light, "  # noqa: E501
        "digital art, square format, no text"
    )

    prompt = f"{scene}. {base_style}"
    return style_name, prompt, date_str


def _maybe_generate_cyprus_image(
    region_name: str,
    tz: Union[pendulum.Timezone, str, None],
    mode: Optional[str],
) -> tuple[Optional[str], Optional[str]]:
    """Синхронно пытается сгенерировать картинку для вечернего Кипра.

    Возвращает (image_path, style_name) или (None, None).
    """
    if not CY_IMAGE_ENABLED:
        return None, None

    if generate_astro_image is None:
        logging.info("CY image: imagegen backend not available")
        return None, None

    try:
        picked = _pick_cyprus_style_prompt(region_name, tz, mode)
        if not picked:
            return None, None
        style_name, prompt, date_str = picked
        out_path = CY_IMAGE_DIR / f"cy_{date_str}.jpg"
        img_path = generate_astro_image(prompt, str(out_path))
        if img_path and os.path.exists(img_path):
            logging.info("CY image generated: %s (style=%s)", img_path, style_name)
            return img_path, style_name
        logging.warning("CY image generation returned no file")
        return None, None
    except Exception as exc:
        logging.warning("CY image generation failed: %s", exc)
        return None, None


def build_message(
    region_name: str,
    sea_label: str,
    sea_cities: Iterable[CityWeather],
    other_label: str,
    other_cities: Iterable[CityWeather],
    tz: Union[pendulum.Timezone, str],
    mode: Optional[str] = None,
) -> str:
    """
    Основная функция, собирающая итоговый текст поста.
    """
    tz_obj = _as_tz(tz)
    now = pendulum.now(tz_obj)
    tomorrow = now.add(days=1)

    header = header_line(region_name, tomorrow)

    sea_block = build_group_block(sea_label, sea_cities)
    other_block = build_continental_block(other_label, other_cities)

    sunset_line = make_sunrise_sunset_line(tomorrow, tz_obj)

    kp_block = ""
    if USE_WORLD_KP:
        kp_data = load_kp_index()
        kp_block = build_kp_block(kp_data)

    astro_block = astro_hint_block("cy", tomorrow, tz_obj) if "кипр" in region_name.lower() or "cyprus" in region_name.lower() else ""

    fact = load_fact_of_day("cy", tomorrow) if "кипр" in region_name.lower() or "cyprus" in region_name.lower() else ""

    tags = hashtags_line("cy" if "кипр" in region_name.lower() or "cyprus" in region_name.lower() else "world")

    parts: List[str] = []
    parts.append(header)
    parts.append("🏖 Морские города")
    parts.append(sea_block)
    parts.append("———")
    parts.append("🏞 Континентальные города")
    parts.append(other_block)
    if sunset_line:
        parts.append("———")
        parts.append(sunset_line)
    if kp_block:
        parts.append("———")
        parts.append(kp_block)
    if astro_block:
        parts.append("🌌 Астрособытия")
        parts.append(astro_block.replace("🌌 Астрособытия\n", ""))
    if fact:
        parts.append("🧠 Факт дня")
        parts.append(fact)
    parts.append(tags)

    return "\n".join(p for p in parts if p.strip())


# ---------------------------------------------------------------------------
# Отправка поста
# ---------------------------------------------------------------------------


async def send_common_post(
    bot: Bot,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
    mode: Optional[str] = None,
) -> None:
    """Собирает текст и отправляет пост в канал.

    Для вечернего поста по Кипру дополнительно пытается сгенерировать
    картинку и отправить sendPhoto. В остальных случаях остаётся
    прежнее поведение — sendMessage только с текстом.
    """
    msg = build_message(
        region_name=region_name,
        sea_label=sea_label,
        sea_cities=sea_cities,
        other_label=other_label,
        other_cities=other_cities,
        tz=tz,
        mode=mode,
    )

    img_path: Optional[str] = None
    style_name: Optional[str] = None

    try:
        img_path, style_name = _maybe_generate_cyprus_image(
            region_name=region_name,
            tz=tz,
            mode=mode,
        )
    except Exception as exc:
        logging.warning("CY image helper failed: %s", exc)
        img_path, style_name = None, None

    if img_path and os.path.exists(img_path):
        logging.info(
            "Sending Cyprus image post with photo: %s (style=%s)",
            img_path,
            style_name or "?",
        )
        try:
            with open(img_path, "rb") as f:
                await bot.send_photo(
                    chat_id=chat_id,
                    photo=f,
                    caption=msg,
                    parse_mode=constants.ParseMode.HTML,
                )
            return
        except Exception as exc:
            logging.warning(
                "send_common_post: send_photo failed, fallback to text: %s",
                exc,
            )

    await bot.send_message(
        chat_id=chat_id,
        text=msg,
        parse_mode=constants.ParseMode.HTML,
        disable_web_page_preview=True,
    )


async def main_common(
    token: str,
    chat_id: int,
    region_name: str,
    sea_label: str,
    sea_cities,
    other_label: str,
    other_cities,
    tz: Union[pendulum.Timezone, str],
    mode: Optional[str] = None,
) -> None:
    """Создаёт Bot и отправляет общий пост."""
    bot = Bot(token=token)
    await send_common_post(
        bot=bot,
        chat_id=chat_id,
        region_name=region_name,
        sea_label=sea_label,
        sea_cities=sea_cities,
        other_label=other_label,
        other_cities=other_cities,
        tz=tz,
        mode=mode,
    )


if __name__ == "__main__":
    print("Этот модуль предполагается использовать как импортируемый (post_common).")
