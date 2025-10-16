#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gpt.py

Единая обёртка для LLM и мини-генератор «Вывод/Рекомендации».

Приоритет провайдеров (по умолчанию): Gemini → Groq → OpenAI.
- На 404/429/недостаток квоты быстро переключаемся к следующему.
- Модели можно переопределить переменными окружения:
    GEMINI_MODEL   (default: "gemini-1.5-flash-latest")
    OPENAI_MODEL   (default: "gpt-4o-mini")
    GROQ_MODELS    (comma-separated; первая — приоритетная)
- Ключи (OPENAI_API_KEY / GEMINI_API_KEY / GROQ_API_KEY) НИКОГДА не передаются в промпт.

Публичные функции:
- gpt_complete(prompt, system, temperature, max_tokens) -> str
- gpt_blurb(culprit) -> (summary: str, tips: List[str])

Фолбэки:
- CULPRITS, ASTRO_HEALTH_FALLBACK — если LLM недоступен.
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
    from openai import OpenAI   # используется и для Groq (совместимый API)
except ImportError:
    OpenAI = None

try:
    import requests             # Gemini через REST
except Exception:
    requests = None

# ── ключи и порядок провайдеров ───────────────────────────────────────────
OPENAI_KEY = os.getenv("OPENAI_API_KEY") or ""
GEMINI_KEY = os.getenv("GEMINI_API_KEY") or ""
GROQ_KEY   = os.getenv("GROQ_API_KEY") or ""

# Порядок провайдеров: Gemini → Groq → OpenAI (можно переопределить LLM_ORDER="gemini,groq,openai")
_default_order = ["gemini", "groq", "openai"]
PROVIDER_ORDER = [p.strip().lower() for p in (os.getenv("LLM_ORDER") or ",".join(_default_order)).split(",") if p.strip()]

# Модели (переопределяемы env-переменными)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-1.5-flash-latest")
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-4o-mini")

_env_groq_models = [m.strip() for m in (os.getenv("GROQ_MODELS") or "").split(",") if m.strip()]
GROQ_MODELS = _env_groq_models or [
    "moonshotai/kimi-k2-instruct-0905",  # по вашему пожеланию — первая в приоритете
    "llama-3.3-70b-versatile",
    "llama-3.1-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "qwen/qwen3-32b",
    "deepseek-r1-distill-llama-70b",     # может возвращать <think>...</think>
]

# ── клиенты ────────────────────────────────────────────────────────────────
def _openai_client() -> Optional["OpenAI"]:
    """Клиент OpenAI без внутренних ретраев — на 429 переключаемся дальше."""
    if not OPENAI_KEY or not OpenAI:
        return None
    try:
        return OpenAI(api_key=OPENAI_KEY, timeout=20.0, max_retries=0)
    except Exception as e:
        log.warning("OpenAI client init error: %s", e)
        return None

def _groq_client() -> Optional["OpenAI"]:
    """OpenAI-совместимый клиент для Groq."""
    if not GROQ_KEY or not OpenAI:
        return None
    try:
        return OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1", timeout=25.0, max_retries=0)
    except Exception as e:
        log.warning("Groq client init error: %s", e)
        return None

# ── утилиты ────────────────────────────────────────────────────────────────
def _strip_think(text: str) -> str:
    """Убираем <think>…</think> и лишние повторы/пробелы."""
    if not text:
        return ""
    text = re.sub(r"(?is)<think>.*?</think>", "", text).strip()
    # лёгкая чистка повторов
    text = re.sub(r"(.)\1{4,}", r"\1\1\1", text)
    return text.strip()

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

    # Сообщения в формате OpenAI
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # 1) Gemini (REST v1beta)
    if "gemini" in PROVIDER_ORDER and not text and GEMINI_KEY and requests:
        try:
            full_prompt = f"{system.strip()}\n\n{prompt}" if system else prompt
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"
            params = {"key": GEMINI_KEY}
            payload = {
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens}
            }
            resp = requests.post(url, params=params, json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json() or {}
                # candidates[0].content.parts[*].text
                cand = (data.get("candidates") or [{}])[0]
                content = cand.get("content") or {}
                parts = content.get("parts") or []
                text = "".join(p.get("text", "") for p in parts).strip()
            else:
                log.warning("Gemini error %s: %s", resp.status_code, resp.text[:500])
        except Exception as e:
            log.warning("Gemini exception: %s", e)

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
                        break
                except Exception as e:
                    msg = str(e).lower()
                    if "decommissioned" in msg or ("model" in msg and "not found" in msg):
                        log.warning("Groq model %s not found/decommissioned, trying next.", mdl)
                        continue
                    if "rate limit" in msg or "429" in msg or "quota" in msg:
                        log.warning("Groq rate limit on %s, trying next.", mdl)
                        continue
                    log.warning("Groq error on %s: %s", mdl, e)
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
            except Exception as e:
                msg = str(e).lower()
                if any(k in msg for k in ("rate limit", "insufficient_quota", "429")):
                    log.warning("OpenAI error (skip): %s", e)
                else:
                    log.warning("OpenAI error: %s", e)

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
            tips += random.sample(remain, min(3 - len(tips), len(remain))) if remain else []
        return summary, tips[:3]

    # 1) «Погодный» фактор
    if culprit_lower in CULPRITS:
        pool = CULPRITS[culprit_lower]["tips"]
        text = gpt_complete(prompt=_make_prompt(culprit, astro=False), system=None, temperature=0.6, max_tokens=240)
        if not text:
            return f"Если завтра что-то пойдёт не так, вините {culprit}! 😉", random.sample(pool, min(3, len(pool)))
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return _from_lines(culprit, lines, pool)

    # 2) «Астро» фактор
    is_astro = any(k in culprit_lower for k in ["луна", "новолуние", "полнолуние", "четверть"])
    if is_astro:
        text = gpt_complete(prompt=_make_prompt(culprit, astro=True), system=None, temperature=0.6, max_tokens=240)
        if not text:
            return f"Если завтра что-то пойдёт не так, вините {culprit}! 😉", random.sample(ASTRO_HEALTH_FALLBACK, 3)
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return _from_lines(culprit, lines, ASTRO_HEALTH_FALLBACK)

    # 3) Общий случай
    text = gpt_complete(prompt=_make_prompt(culprit, astro=True), system=None, temperature=0.6, max_tokens=240)
    if not text:
        return f"Если завтра что-то пойдёт не так, вините {culprit}! 😉", random.sample(ASTRO_HEALTH_FALLBACK, 3)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    fallback_pool = ASTRO_HEALTH_FALLBACK + sum((c["tips"] for c in CULPRITS.values()), [])
    return _from_lines(culprit, lines, fallback_pool)
