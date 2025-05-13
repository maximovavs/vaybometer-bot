#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import random
from typing import Tuple, List

try:
    from openai import OpenAI
except ImportError:
    OpenAI = None  # Если библиотека не установлена, просто не будем вызывать GPT

OPENAI_KEY = os.getenv("OPENAI_API_KEY")

# Словарь «виновников» со своими эмоджи и пулом советов
CULPRITS = {
    "туман": {
        "emoji": "🌁",
        "tips": [
            "🔦 Светлая одежда и фонарь",
            "🚗 Водите аккуратнее",
            "⏰ Планируйте дорогу с запасом",
            "👀 Увеличьте дистанцию между авто",
        ],
    },
    "магнитные бури": {
        "emoji": "🧲",
        "tips": [
            "🧘 5-минутная дыхательная пауза",
            "🌿 Заварите чай с мелиссой",
            "🙅 Избегайте стрессовых новостей",
            "😌 Лёгкая растяжка перед сном",
        ],
    },
    "низкое давление": {
        "emoji": "🌡️",
        "tips": [
            "💧 Пейте больше воды",
            "😴 20-мин дневной отдых",
            "🤸 Нежная зарядка",
            "🥗 Лёгкий ужин без соли",
        ],
    },
    "шальной ветер": {
        "emoji": "💨",
        "tips": [
            "🧣 Захватите шарф",
            "🚶 Короткая быстрая прогулка",
            "🕶️ Защитите глаза от пыли",
            "🤚 Проверьте крышки мусорных баков",
        ],
    },
    "жара": {
        "emoji": "🔥",
        "tips": [
            "💦 Держите воду под рукой",
            "🧢 Головной убор обязателен",
            "🌳 Ищите тень в полдень",
            "❄️ Холодный компресс на лоб",
        ],
    },
    "сырость": {
        "emoji": "💧",
        "tips": [
            "👟 Сменная обувь не помешает",
            "🌂 Компактный зонт в рюкзак",
            "🌬️ Проветривайте помещения",
            "🧥 лёгкая водонепроницаемая куртка",
        ],
    },
    "полная луна": {
        "emoji": "🌕",
        "tips": [
            "📝 Запишите яркие идеи",
            "🧘 Мягкая медитация перед сном",
            "🌙 Полюбуйтесь луной без гаджетов",
            "📚 Прочитайте спокойную книгу",
        ],
    },
    "мини-парад планет": {
        "emoji": "✨",
        "tips": [
            "🔭 Посмотрите на небо на рассвете",
            "📸 Сделайте фото заката",
            "🤔 Подумайте о бескрайних просторах",
            "✨ Насладитесь мгновением",
        ],
    },
}

def gpt_blurb(culprit: str) -> Tuple[str, List[str]]:
    """
    Возвращает (summary, tips) для заданного виновника.
    Если есть OPENAI_KEY и библиотека OpenAI — попытаемся взять 1 строку + 3 совета,
    иначе — возьмём 2 любых совета из CULPRITS[culprit].
    """
    # Возьмём пул советов, если ключ не найден — используем «мини-парад планет» как дефолт
    entry = CULPRITS.get(culprit, CULPRITS["мини-парад планет"])
    tips_pool = entry["tips"]
    # Базовый summary
    summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"

    # Без OpenAI — берём просто два совета
    if not OPENAI_KEY or OpenAI is None:
        count = min(2, len(tips_pool))
        return summary, random.sample(tips_pool, count)

    # Иначе — гоняем запрос в GPT
    prompt = (
        f"Одна строка: «Если завтра что-то пойдёт не так, вините {culprit}!». "
        "После точки — позитив ≤12 слов. Затем 3 bullet-совета ≤12 слов с эмодзи."
    )
    try:
        client = OpenAI(api_key=OPENAI_KEY)
        resp = client.chat.completions.create(
            model="gpt-4o-mini",
            temperature=0.6,
            messages=[{"role": "user", "content": prompt}],
        )
        text = resp.choices[0].message.content.strip().splitlines()
        lines = [l.strip() for l in text if l.strip()]
        # первая строка — summary
        summary = lines[0]
        # следующие до трёх — советы
        tips = [l.lstrip("-• ").strip() for l in lines[1:4]]
        # если по какой-то причине меньше 2 советов, дополняем из пула
        if len(tips) < 2:
            extras = random.sample(tips_pool, min(2, len(tips_pool)))
            tips = (tips + extras)[:2]
        return summary, tips
    except Exception:
        # при сбое OpenAI всё равно вернём пару советов из пула
        count = min(2, len(tips_pool))
        return summary, random.sample(tips_pool, count)
