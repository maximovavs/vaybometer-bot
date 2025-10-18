#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gpt.py

Единая обёртка для LLM и мини-генератор «Вывод/Рекомендации».

Приоритет провайдеров: Gemini → Groq → OpenAI (настраивается LLM_ORDER="gemini,groq,openai").
Быстрый фейловер: на 404/429/insufficient_quota переключаемся к следующему провайдеру без ретраев SDK.

ENV:
    GEMINI_API_KEY
    GROQ_API_KEY
    OPENAI_API_KEY

Модели можно переопределить:
    GEMINI_MODEL   (default: "gemini-1.5-flash")
    OPENAI_MODEL   (default: "gpt-4o-mini")
    GROQ_MODELS    (comma-separated; первая — приоритетная)
    LLM_ORDER      (comma-separated: "gemini,groq,openai")

Публичные функции:
    - gpt_complete(prompt, system=None, temperature=0.7, max_tokens=600) -> str
    - gpt_blurb(culprit) -> (summary: str, tips: List[str])

Важно:
    - Ключи НИКОГДА не передаются в промпт.
    - Для Gemini используем REST v1beta (+ systemInstruction).
"""

from __future__ import annotations
import os
import re
import random
import logging
from typing import Tuple, List, Optional

log = logging.getLogger(__name__)

# ── SDK / HTTP ─────────────────────────────────────────────────────────────
try:
    from openai import OpenAI  # используется и для Groq (OpenAI-совместимый API)
except ImportError:
    OpenAI = None  # type: ignore

try:
    import requests  # Gemini через REST
except Exception:
    requests = None  # type: ignore

# ── ключи и порядок провайдеров ───────────────────────────────────────────
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or ""
GEMINI_KEY = os.getenv("GEMINI_API_KEY") or ""
GROQ_KEY   = os.getenv("GROQ_API_KEY") or ""

_default_order = ["gemini", "groq", "openai"]
PROVIDER_ORDER = [
    p.strip().lower()
    for p in (os.getenv("LLM_ORDER") or ",".join(_default_order)).split(",")
    if p.strip()
]

# ── модели (переопределяемые env) ─────────────────────────────────────────
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash")  # стабильный, без -latest
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

_env_groq_models = [m.strip() for m in (os.getenv("GROQ_MODELS") or "").split(",") if m.strip()]
GROQ_MODELS = _env_groq_models or [
    "moonshotai/kimi-k2-instruct-0905",  # приоритетная
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "qwen/qwen3-32b",
    "deepseek-r1-distill-llama-70b",  # может вернуть <think>...</think>
]

# ── клиенты ────────────────────────────────────────────────────────────────
def _openai_client() -> Optional["OpenAI"]:
    """Клиент OpenAI без внутренних ретраев — на 429 переключаемся дальше."""
    if not OPENAI_KEY or not OpenAI:
        return None
    try:
        return OpenAI(api_key=OPENAI_KEY, timeout=20.0, max_retries=0)
    except Exception as e:
        log.warning("[openai] client init error: %s", e)
        return None

def _groq_client() -> Optional["OpenAI"]:
    """OpenAI-совместимый клиент для Groq."""
    if not GROQ_KEY or not OpenAI:
        return None
    try:
        return OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1", timeout=25.0, max_retries=0)
    except Exception as e:
        log.warning("[groq] client init error: %s", e)
        return None

# ── утилиты ────────────────────────────────────────────────────────────────
def _strip_think(text: str) -> str:
    """Убираем скрытые рассуждения/кодблоки и длинные повторы."""
    if not text:
        return ""
    # Скрытые теги рассуждений
    text = re.sub(r"(?is)<(think|reasoning|scratchpad)>.*?</\1>", "", text)
    # Тройные кавычки-кодблоки
    text = re.sub(r"(?is)```(?:\w+)?\n(.*?)```", r"\1", text)
    # Фрагменты вида <foo>...</foo> без whitelisted тегов
    text = re.sub(r"(?is)</?([a-z][a-z0-9_-]{0,20})>", "", text)
    # Лёгкая чистка повторов
    text = re.sub(r"(.)\1{4,}", r"\1\1\1", text)
    return text.strip()

def _shorten(s: str, n: int = 400) -> str:
    s = s or ""
    return s if len(s) <= n else s[:n] + "…"

# ── основной вызов ─────────────────────────────────────────────────────────
def gpt_complete(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 600,
) -> str:
    """
    Универсальный вызов LLM. Пробуем по очереди: Gemini → Groq → OpenAI (по PROVIDER_ORDER).
    Возвращает text или "".
    """
    text = ""

    # Сообщения в формате OpenAI (для Groq/OpenAI)
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # 1) Gemini (REST v1beta)
    if "gemini" in PROVIDER_ORDER and not text and GEMINI_KEY and requests:
        try:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
            params = {"key": GEMINI_KEY}
            payload: dict = {
                "contents": [{"role": "user", "parts": [{"text": prompt}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
            }
            if system:
                # Более корректно, чем склейка system+user
                payload["systemInstruction"] = {"role": "system", "parts": [{"text": system}]}

            resp = requests.post(url, params=params, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json() or {}

                # Safety-блокировки промпта
                fb = (data.get("promptFeedback") or {})
                block_reason = fb.get("blockReason")
                if block_reason:
                    log.warning("[gemini %s] prompt blocked: %s", GEMINI_MODEL, block_reason)
                else:
                    candidates = data.get("candidates") or []
                    if candidates:
                        cand = candidates[0] or {}
                        finish = cand.get("finishReason")
                        if finish in (None, "STOP"):
                            content = cand.get("content") or {}
                            parts = content.get("parts") or []
                            text = "".join(p.get("text", "") for p in parts).strip()
                            if text:
                                log.info("[gemini %s] ok (%d chars)", GEMINI_MODEL, len(text))
                        else:
                            log.warning("[gemini %s] finishReason=%s", GEMINI_MODEL, finish)
                    else:
                        log.warning("[gemini %s] no candidates", GEMINI_MODEL)
            else:
                code = resp.status_code
                body = _shorten(resp.text)
                # Быстрый фейловер на типовые статусы
                if code in (404, 409, 413, 422, 429, 500, 503):
                    log.warning("[gemini %s] http %s: %s", GEMINI_MODEL, code, body)
                else:
                    log.warning("[gemini %s] http %s: %s", GEMINI_MODEL, code, body)
        except Exception as e:
            log.warning("[gemini %s] exception: %s", GEMINI_MODEL, e)

    # 2) Groq (OpenAI-совместимый)
    if "groq" in PROVIDER_ORDER and not text:
        cli = _groq_client()
        if cli:
            for mdl in GROQ_MODELS:
                try:
                    r = cli.chat.completions.create(
                        model=mdl,
                        messages=messages,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    text = (r.choices[0].message.content or "").strip()
                    if text:
                        log.info("[groq %s] ok (%d chars)", mdl, len(text))
                        break
                except Exception as e:
                    msg = str(e).lower()
                    if "decommissioned" in msg or ("model" in msg and "not found" in msg):
                        log.warning("[groq %s] model not found/decommissioned, trying next", mdl)
                        continue
                    if "rate limit" in msg or "429" in msg or "quota" in msg:
                        log.warning("[groq %s] rate limit/quota, trying next", mdl)
                        continue
                    log.warning("[groq %s] error: %s", mdl, _shorten(str(e)))
                    continue

    # 3) OpenAI
    if "openai" in PROVIDER_ORDER and not text:
        cli = _openai_client()
        if cli:
            try:
                r = cli.chat.completions.create(
                    model=OPENAI_MODEL,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                text = (r.choices[0].message.content or "").strip()
                if text:
                    log.info("[openai %s] ok (%d chars)", OPENAI_MODEL, len(text))
            except Exception as e:
                msg = str(e).lower()
                if any(k in msg for k in ("rate limit", "insufficient_quota", "429")):
                    log.warning("[openai %s] rate limit/quota: %s", OPENAI_MODEL, _shorten(str(e)))
                else:
                    log.warning("[openai %s] error: %s", OPENAI_MODEL, _shorten(str(e)))

    return _strip_think(text or "")

# ── фолбэки ────────────────────────────────────────────────────────────────
CULPRITS = {
    "туман": {
        "emoji": "🌁",
        "tips": [
            "🔦 Светлая одежда и фонарь",
            "🚗 Водите аккуратно",
            "⏰ Планируйте поездки заранее",
            "🕶️ Очки против бликов",
        ],
    },
    "магнитные бури": {
        "emoji": "🧲",
        "tips": [
            "🧘 5-минутная дыхательная пауза",
            "🌿 Тёплый травяной чай",
            "🙅 Меньше новостей и экранов",
            "😌 Лёгкая растяжка вечером",
        ],
    },
    "низкое давление": {
        "emoji": "🌡️",
        "tips": [
            "💧 Пейте больше воды",
            "😴 20-минутный дневной отдых",
            "🤸 Небольшая зарядка утром",
            "🥗 Меньше соли вечером",
        ],
    },
    "шальной ветер": {
        "emoji": "💨",
        "tips": [
            "🧣 Захватите шарф",
            "🚶 Короткая прогулка",
            "🕶️ Защитите глаза от пыли",
            "🌳 Избегайте открытых пространств",
        ],
    },
    "жара": {
        "emoji": "🔥",
        "tips": [
            "💦 Бутылка воды под рукой",
            "🧢 Головной убор и тень",
            "⏱ Избегайте полудня",
            "❄️ Охлаждающий компресс",
        ],
    },
    "сырость": {
        "emoji": "💧",
        "tips": [
            "👟 Сменная обувь",
            "🌂 Компактный зонт",
            "🌬️ Проветривайте дом",
            "🧥 Лёгкая непромокаемая куртка",
        ],
    },
    "полная луна": {
        "emoji": "🌕",
        "tips": [
            "📝 Запишите идеи перед сном",
            "🧘 Мягкая медитация",
            "🌙 Минутка без гаджетов",
            "📚 Небольшое чтение",
        ],
    },
    "мини-парад планет": {
        "emoji": "✨",
        "tips": [
            "🔭 Посмотрите на небо на рассвете",
            "📸 Сфотографируйте закат",
            "🤔 Минутка тишины",
            "🎶 Спокойная музыка вечером",
        ],
    },
}

ASTRO_HEALTH_FALLBACK: List[str] = [
    "💤 Ложитесь не позже 23:00",
    "🥦 Больше зелени и овощей",
    "🚶 20 минут прогулки",
    "🫖 Тёплый настой перед сном",
    "🧘 3 минуты дыхания 4-7-8",
]

# ── публичная функция для «Вывод/Рекомендации» ────────────────────────────
def gpt_blurb(culprit: str) -> Tuple[str, List[str]]:
    """
    Возвращает (summary: str, tips: List[str]).
    Если LLM недоступен — используем фолбэк-списки.
    Температура здесь намеренно низкая (≈0.2), как по ТЗ.
    """
    culprit_lower = (culprit or "").lower().strip()

    def _make_prompt(cul: str, astro: bool) -> str:
        base = (
            "Ты — экспертный health-коуч: дружелюбный, конкретный, без штампов. "
            "Дай ответ на русском, по строкам."
        )
        tail = (
            f"1) Первая строка: «Если завтра что-то пойдёт не так, вините {cul}!». "
            "Добавь короткий позитив (≤12 слов). "
            "2) Далее ровно 3 короткие практичные рекомендации (≤12 слов) с эмодзи. "
            "Темы: сон, питание, лёгкая активность/дыхание."
        )
        if astro:
            tail += " Учитывай чувствительность к циклам и мягкий тон."
        return base + " " + tail

    def _from_lines(cul: str, lines: List[str], fallback_pool: List[str]) -> Tuple[str, List[str]]:
        summary = lines[0] if lines else f"Если завтра что-то пойдёт не так, вините {cul}! 😉"
        tips = [ln for ln in lines[1:] if ln][:3]
        if len(tips) < 3:
            remain = [t for t in fallback_pool if t not in tips]
            if remain:
                # Если меньше 3 — дозаполняем, но без ошибки, если пул пуст
                k = min(3 - len(tips), len(remain))
                tips += random.sample(remain, k)
        return summary, tips[:3]

    # 1) «Погодный» фактор
    if culprit_lower in CULPRITS:
        pool = CULPRITS[culprit_lower]["tips"]
        text = gpt_complete(prompt=_make_prompt(culprit, astro=False), system=None, temperature=0.2, max_tokens=240)
        if not text:
            # LLM недоступен — фолбэк
            k = min(3, len(pool))
            return f"Если завтра что-то пойдёт не так, вините {culprit}! 😉", random.sample(pool, k) if k else []
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return _from_lines(culprit, lines, pool)

    # 2) «Астро» фактор
    is_astro = any(k in culprit_lower for k in ["луна", "новолуние", "полнолуние", "четверть"])
    if is_astro:
        text = gpt_complete(prompt=_make_prompt(culprit, astro=True), system=None, temperature=0.2, max_tokens=240)
        if not text:
            k = min(3, len(ASTRO_HEALTH_FALLBACK))
            return f"Если завтра что-то пойдёт не так, вините {culprit}! 😉", random.sample(ASTRO_HEALTH_FALLBACK, k)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return _from_lines(culprit, lines, ASTRO_HEALTH_FALLBACK)

    # 3) Общий случай
    text = gpt_complete(prompt=_make_prompt(culprit, astro=True), system=None, temperature=0.2, max_tokens=240)
    if not text:
        k = min(3, len(ASTRO_HEALTH_FALLBACK))
        return f"Если завтра что-то пойдёт не так, вините {culprit}! 😉", random.sample(ASTRO_HEALTH_FALLBACK, k)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    fallback_pool = ASTRO_HEALTH_FALLBACK + sum((c["tips"] for c in CULPRITS.values()), [])
    return _from_lines(culprit, lines, fallback_pool)
