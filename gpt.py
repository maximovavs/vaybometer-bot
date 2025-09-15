#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gpt.py — «Вывод» и «Рекомендации» для VayboMeter.

Контракт:
    gpt_blurb(theme) -> (summary: str, tips: List[str])

Поведение:
- Если DISABLE_LLM_DAILY=1 или провайдеры недоступны/падают — возвращаем нейтральный фоллбэк.
- Провайдеры по очереди: OpenAI -> Gemini -> Groq. Любой удачный ответ парсится в (summary, tips).
- Чистим возможные <think>…</think> из ответов моделей.
- Советы короткие (до ~12 слов), с эмодзи. При недостатке строк — добираем из локального пула.

Окружение:
- OPENAI_API_KEY, GEMINI_API_KEY, GROQ_API_KEY — по возможности.
- DISABLE_LLM_DAILY=1 — мгновенный фоллбэк без запросов к LLM.
"""

from __future__ import annotations
from typing import List, Tuple, Optional
import os, re, random

# ────────────────────────────── Конфиг ──────────────────────────────

def _llm_disabled() -> bool:
    return os.getenv("DISABLE_LLM_DAILY", "0").strip().lower() in ("1", "true", "yes", "on")

_THINK_TAG_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_TAG_RE = re.compile(r"</?think>", re.IGNORECASE)

def _strip_think(s: str) -> str:
    s = _THINK_TAG_BLOCK_RE.sub("", s or "")
    s = _THINK_TAG_RE.sub("", s)
    return s.strip()

# Карта «темы» -> слово в фразе «вините …»
_CULPRIT_WORD = {
    "плохая погода": "погоду",
    "магнитные бури": "магнитные бури",
    "плохой воздух": "воздух",
    "здоровый день": "здоровый режим",
    "астрология": "Луну",
}

# Бейзлайн советы, чтобы было чем добирать
ASTRO_HEALTH_FALLBACK: List[str] = [
    "💧 Пейте воду маленькими глотками в течение дня",
    "😴 Ложитесь спать на 30 минут раньше обычного",
    "🫁 4-7-8: вдох 4с, задержка 7с, выдох 8с — три цикла",
    "🥗 Ужин до 19:00, больше овощей и белка",
    "🚶 15 минут прогулки после еды — сахар и сон лучше",
    "📵 За час до сна — без экранов, тусклый свет",
    "🧂 Меньше соли и алкоголя — давление скажет спасибо",
    "🧘 5 минут растяжки шеи и плеч, плечи вниз",
    "☀️ 10 минут дневного света — синхронизируйте ритмы",
]

# Дополнительные пулы под конкретные причины
CULPRITS = {
    "плохая погода": {
        "tips": [
            "🧥 Слои одежды и капюшон — тепло и сухо",
            "☔ Короткие выходы между ливнями, проверяйте радары",
            "🥣 Тёплый суп и электролиты — комфорт и гидратация",
        ]
    },
    "магнитные бури": {
        "tips": [
            "💧 Больше воды и магний вечером — меньше головной боли",
            "🫁 Дыхание по квадрату 4-4-4-4 — успокоит пульс",
            "😴 Режим сна без кофеина после 15:00",
        ]
    },
    "плохой воздух": {
        "tips": [
            "😷 Маска при нагрузке на улице, окна по ситуации",
            "🌿 Воздухоочиститель/HEPA, влажная уборка вечером",
            "🚶 Прогулка у моря/парка — пыли меньше",
        ]
    },
    "здоровый день": {
        "tips": [
            "🥗 Тарелка: ½ овощи, ¼ белок, ¼ цельные крупы",
            "🚶 7–10k шагов, без фанатизма",
            "💤 90 минут до сна — без тяжёлой еды",
        ]
    },
    "астрология": {
        "tips": [
            "🧘 5 минут тишины утром — сфокусируйтесь на намерении",
            "📓 Запишите три цели на день — чёткий вектор",
            "🤝 Небольшой добрый поступок — укрепит связи",
        ]
    },
}

# ─────────────────────── Промпты и парсинг ───────────────────────

def _make_prompt(theme: str) -> str:
    cul = _CULPRIT_WORD.get(theme, theme)
    return (
        "Действуй как экспертный health coach со знаниями функциональной медицины, "
        "который даёт короткие практичные рекомендации.\n"
        f"1) Одной строкой: «Если завтра что-то пойдёт не так, вините {cul}!». "
        "После точки — позитив ≤12 слов.\n"
        "2) Затем ровно 3 коротких пункта (≤12 слов каждый) с эмодзи: "
        "питание, сон/дыхание, лёгкая активность/режим. "
        "Не пиши слово «совет». Ответ — по строкам."
    )

def _from_lines(culprit: str, lines: List[str], tips_pool: List[str]) -> Tuple[str, List[str]]:
    lines = [ln.strip() for ln in lines if ln and ln.strip()]
    summary = ""
    tips: List[str] = []

    if lines:
        summary = lines[0]
        tail = [ln for ln in lines[1:] if ln and not ln.startswith("#")]
        for ln in tail:
            if len(tips) >= 3:
                break
            # Отсечь костыли вроде «Совет 1:»
            ln = re.sub(r"^(?:[-•\d\.\)]\s*)?(?:совет|tip)\s*\d*[:\-]\s*", "", ln, flags=re.I).strip()
            if ln:
                tips.append(ln)

    # Если summary пустой — поставим базовую фразу
    if not summary:
        summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"

    # Добираем советы из пула
    pool = (ASTRO_HEALTH_FALLBACK + tips_pool) if tips_pool else ASTRO_HEALTH_FALLBACK
    while len(tips) < 3 and pool:
        candidate = random.choice(pool)
        pool.remove(candidate)
        if candidate not in tips:
            tips.append(candidate)

    # Ограничим длины (страховка)
    def _clip(s: str) -> str:
        s = s.strip()
        return s if len(s) <= 120 else (s[:117].rstrip() + "…")

    summary = _clip(summary)
    tips = [_clip(t) for t in tips[:3]]
    return summary, tips

# ───────────────────────── Провайдеры ─────────────────────────

def _try_openai(prompt: str) -> Optional[str]:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        # Новый SDK (openai>=1.0)
        from openai import OpenAI  # type: ignore
        client = OpenAI(api_key=api_key)
        model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Отвечай кратко, по-русски."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
            max_tokens=280,
        )
        text = resp.choices[0].message.content or ""
        return _strip_think(text)
    except Exception:
        # Попытка на совместимый старый SDK (openai<1.0)
        try:
            import openai  # type: ignore
            openai.api_key = api_key
            model = os.getenv("OPENAI_MODEL", "gpt-4o-mini")
            resp = openai.ChatCompletion.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Отвечай кратко, по-русски."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.6,
                max_tokens=280,
            )
            text = resp["choices"][0]["message"]["content"] or ""
            return _strip_think(text)
        except Exception:
            return None

def _try_gemini(prompt: str) -> Optional[str]:
    api_key = os.getenv("GEMINI_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        import google.generativeai as genai  # type: ignore
        genai.configure(api_key=api_key)
        model_name = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")
        model = genai.GenerativeModel(model_name)
        resp = model.generate_content(prompt)
        text = ""
        if hasattr(resp, "text") and resp.text:
            text = resp.text
        elif hasattr(resp, "candidates") and resp.candidates:
            text = resp.candidates[0].content.parts[0].text
        return _strip_think(text)
    except Exception:
        return None

def _try_groq(prompt: str) -> Optional[str]:
    api_key = os.getenv("GROQ_API_KEY", "").strip()
    if not api_key:
        return None
    try:
        from groq import Groq  # type: ignore
        client = Groq(api_key=api_key)
        model = os.getenv("GROQ_MODEL", "llama-3.1-70b-versatile")
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "Отвечай кратко, по-русски."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.6,
            max_tokens=280,
        )
        text = resp.choices[0].message.content or ""
        return _strip_think(text)
    except Exception:
        return None

# ───────────────────────── Публичное API ─────────────────────────

def gpt_blurb(theme: str) -> Tuple[str, List[str]]:
    """
    Вернёт (summary, tips).
    При отключённом LLM или любой ошибке — фоллбэк без исключений.
    """
    culprit = _CULPRIT_WORD.get(theme, theme)
    tips_pool = CULPRITS.get(theme, {}).get("tips", [])

    if _llm_disabled():
        # мгновенный фоллбэк
        summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
        pool = (ASTRO_HEALTH_FALLBACK + tips_pool) if tips_pool else ASTRO_HEALTH_FALLBACK
        tips = random.sample(pool, k=min(3, len(pool))) if pool else []
        while len(tips) < 3:
            tips.append("💧 Пейте воду и высыпайтесь")
        return summary, tips[:3]

    prompt = _make_prompt(theme)

    # 1) OpenAI
    text = _try_openai(prompt)
    if text:
        return _from_lines(culprit, text.splitlines(), tips_pool)

    # 2) Gemini
    text = _try_gemini(prompt)
    if text:
        return _from_lines(culprit, text.splitlines(), tips_pool)

    # 3) Groq
    text = _try_groq(prompt)
    if text:
        return _from_lines(culprit, text.splitlines(), tips_pool)

    # 4) Полный фоллбэк
    summary = f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
    pool = (ASTRO_HEALTH_FALLBACK + tips_pool) if tips_pool else ASTRO_HEALTH_FALLBACK
    tips = random.sample(pool, k=min(3, len(pool))) if pool else []
    while len(tips) < 3:
        tips.append("😴 Режим сна: лягте на 30 минут раньше")
    return summary, tips[:3]


# Локальный прогон
if __name__ == "__main__":
    for t in ("плохая погода", "магнитные бури", "плохой воздух", "здоровый день", "астрология"):
        s, tips = gpt_blurb(t)
        print("—", t, "—")
        print(s)
        for x in tips:
            print(" •", x)
        print()
