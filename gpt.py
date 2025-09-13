#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
gpt.py (Cyprus)

Обновлённый модуль из KLD:
• gpt_complete(): провайдеры по очереди — OpenAI → Gemini(HTTP) → Groq.
• Быстрое переключение при 429/квоте.
• gpt_blurb(culprit) — возвращает (summary, tips[3]) с фоллбэками без ключей.

Секреты: OPENAI_API_KEY, GEMINI_API_KEY, GROQ_API_KEY.
Зависимости: openai (опц.), requests.
"""

from __future__ import annotations
import os
import random
import logging
from typing import Tuple, List, Optional

log = logging.getLogger(__name__)

# ── провайдеры ──────────────────────────────────────────────────────────────
try:
    from openai import OpenAI  # единый клиент для OpenAI и Groq (через base_url)
except Exception:
    OpenAI = None  # type: ignore

try:
    import requests  # для Gemini HTTP API
except Exception:
    requests = None  # type: ignore

OPENAI_KEY = os.getenv("OPENAI_API_KEY") or ""
GEMINI_KEY = os.getenv("GEMINI_API_KEY") or ""
GROQ_KEY   = os.getenv("GROQ_API_KEY") or ""

PROVIDER_ORDER = [p for p in ("openai", "gemini", "groq")]

# список моделей Groq — пробуем по порядку
GROQ_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
    "gemma2-9b-it",
    "deepseek-r1-distill-llama-70b",
]

# ── клиенты ────────────────────────────────────────────────────────────────
def _openai_client() -> Optional["OpenAI"]:
    if not OPENAI_KEY or not OpenAI:
        return None
    try:
        # без ретраев, чтобы быстро переключаться дальше
        return OpenAI(api_key=OPENAI_KEY, timeout=20.0, max_retries=0)
    except Exception as e:
        log.warning("OpenAI client init error: %s", e)
        return None

def _groq_client() -> Optional["OpenAI"]:
    if not GROQ_KEY or not OpenAI:
        return None
    try:
        return OpenAI(api_key=GROQ_KEY, base_url="https://api.groq.com/openai/v1", timeout=25.0, max_retries=0)
    except Exception as e:
        log.warning("Groq client init error: %s", e)
        return None

# ── общий вызов LLM ────────────────────────────────────────────────────────
def gpt_complete(
    prompt: str,
    system: Optional[str] = None,
    temperature: float = 0.7,
    max_tokens: int = 600,
) -> str:
    """
    Возвращает текст или "".
    Порядок: OpenAI → Gemini → Groq.
    """
    text = ""

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    # 1) OpenAI
    if "openai" in PROVIDER_ORDER and not text:
        cli = _openai_client()
        if cli:
            try:
                r = cli.chat.completions.create(
                    model="gpt-4o-mini",
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                )
                text = (r.choices[0].message.content or "").strip()
            except Exception as e:
                msg = str(e).lower()
                if any(k in msg for k in ("rate limit", "insufficient_quota", "429")):
                    log.warning("OpenAI rate/quota issue → fallback: %s", e)
                else:
                    log.warning("OpenAI error: %s", e)
                text = ""

    # 2) Gemini (HTTP)
    if "gemini" in PROVIDER_ORDER and not text and GEMINI_KEY and requests:
        try:
            full_prompt = f"{system.strip()}\n\n{prompt}" if system else prompt
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-1.5-flash:generateContent"
            resp = requests.post(
                url,
                params={"key": GEMINI_KEY},
                json={
                    "contents": [{"parts": [{"text": full_prompt}]}],
                    "generationConfig": {"temperature": temperature, "maxOutputTokens": max_tokens},
                },
                timeout=25,
            )
            if resp.status_code == 200:
                data = resp.json()
                cand = (data.get("candidates") or [{}])[0]
                parts = ((cand.get("content") or {}).get("parts") or [])
                text = "".join(p.get("text", "") for p in parts).strip()
            else:
                log.warning("Gemini HTTP %s: %s", resp.status_code, resp.text[:200])
        except Exception as e:
            log.warning("Gemini exception: %s", e)

    # 3) Groq (OpenAI-совместимый API)
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
                    if "not found" in msg or "decommissioned" in msg:
                        log.warning("Groq model %s unavailable, try next.", mdl)
                        continue
                    if "rate limit" in msg or "429" in msg:
                        log.warning("Groq rate limit on %s, try next.", mdl)
                        continue
                    log.warning("Groq error on %s: %s", mdl, e)
                    continue

    return text or ""

# ── фоллбэки ────────────────────────────────────────────────────────────────
CULPRITS = {
    "туман": {
        "emoji": "🌁",
        "tips": ["🔦 Светлая одежда и фонарь", "🚗 Водите аккуратно", "⏰ Планируйте поездки заранее", "🕶️ Очки против бликов"],
    },
    "магнитные бури": {
        "emoji": "🧲",
        "tips": ["🧘 5-минутная дыхательная пауза", "🌿 Тёплый травяной чай", "🙅 Меньше новостей", "😌 Растяжка перед сном"],
    },
    "низкое давление": {
        "emoji": "🌡️",
        "tips": ["💧 Пейте воду", "😴 20 минут отдыха", "🤸 Лёгкая зарядка", "🥗 Меньше соли вечером"],
    },
    "шальной ветер": {
        "emoji": "💨",
        "tips": ["🧣 Шарф с собой", "🚶 Небольшая прогулка", "🕶️ Защита глаз", "🌳 Избегайте открытых мест"],
    },
    "жара": {
        "emoji": "🔥",
        "tips": ["💦 Бутылка воды под рукой", "🧢 Головной убор", "🌳 Тень в полдень", "❄️ Прохладный компресс"],
    },
    "сырость": {
        "emoji": "💧",
        "tips": ["👟 Сменная обувь", "🌂 Компактный зонт", "🌬️ Проветривайте дом", "🧥 Непромокаемая куртка"],
    },
    "полная луна": {
        "emoji": "🌕",
        "tips": ["📝 Запишите идеи", "🧘 Мягкая медитация", "🌙 Смотреть на луну офлайн", "📚 10 минут чтения"],
    },
    "мини-парад планет": {
        "emoji": "✨",
        "tips": ["🔭 Рассветное небо", "📸 Фото заката", "🤔 Минутка созерцания", "🎶 Спокойная музыка вечером"],
    },
}

ASTRO_HEALTH_FALLBACK: List[str] = [
    "💤 Режим сна: в постель до 23:00",
    "🥦 Больше овощей и зелени",
    "🥛 Тёплое молоко/чай перед сном",
    "🧘 Лёгкая растяжка утром/вечером",
    "🚶 20 минут прогулки в день",
]

# ── публичное API для блока «Вывод/Рекомендации» ────────────────────────────
def gpt_blurb(culprit: str) -> Tuple[str, List[str]]:
    """
    Возвращает (summary, tips[3]).
    При наличии ключей просит LLM; иначе — фоллбэк из словарей.
    """
    culprit = (culprit or "").strip()
    culprit_lower = culprit.lower()

    def _make_prompt(astro: bool) -> str:
        if astro:
            return (
                f"Действуй как экспертный health coach со знаниями функциональной медицины, который постоянно изучает что-то новое и любит удивлять"
                f"Напиши одной строкой: «Если завтра что-то пойдёт не так, вините {culprit}!». "
                f"После точки — короткий позитив ≤12 слов для подписчиков.  Не пиши само слово совет."
                f"Затем дай ровно 3 рекомендации (сон, питание, дыхание/лёгкая активность) ≤12 слов с эмодзи. "
                f"Ответ — по строкам."
            )
        else:
            return (
                f"Действуй как опытный health coach со знаниями функциональной медицины, который постоянно изучает что-то новое и любит удивлять"
                f"Напиши одной строкой: «Если завтра что-то пойдёт не так, вините {culprit}!». "
                f"После точки — короткий позитив ≤12 слов для подписчиков.  Не пиши само слово совет."
                f"Затем дай ровно 3 рекомендации (питание, сон, лёгкая активность) ≤12 слов с эмодзи. "
                f"Ответ — по строкам."
            )

    def _from_lines(lines: List[str], fallback_pool: List[str]) -> Tuple[str, List[str]]:
        summary = lines[0] if lines else f"Если завтра что-то пойдёт не так, вините {culprit}! 😉"
        tips = [ln for ln in lines[1:] if ln][:3]
        if len(tips) < 3:
            remain = [t for t in fallback_pool if t not in tips]
            tips += random.sample(remain, min(3 - len(tips), len(remain))) if remain else []
        return summary, tips[:3]

    # 1) «Погодный» фактор из словаря
    if culprit_lower in CULPRITS:
        tips_pool = CULPRITS[culprit_lower]["tips"]
        text = gpt_complete(prompt=_make_prompt(astro=False), system=None, temperature=0.7, max_tokens=500)
        if not text:
            return (f"Если завтра что-то пойдёт не так, вините {culprit}! 😉",
                    random.sample(tips_pool, min(3, len(tips_pool))))
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return _from_lines(lines, tips_pool)

    # 2) Астро-фактор
    astro = any(k in culprit_lower for k in ("луна", "новолуние", "полнолуние", "четверть"))
    if astro:
        text = gpt_complete(prompt=_make_prompt(astro=True), system=None, temperature=0.7, max_tokens=500)
        if not text:
            return (f"Если завтра что-то пойдёт не так, вините {culprit}! 😉",
                    random.sample(ASTRO_HEALTH_FALLBACK, 3))
        lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
        return _from_lines(lines, ASTRO_HEALTH_FALLBACK)

    # 3) Общий случай
    text = gpt_complete(prompt=_make_prompt(astro=True), system=None, temperature=0.7, max_tokens=500)
    if not text:
        return (f"Если завтра что-то пойдёт не так, вините {culprit}! 😉",
                random.sample(ASTRO_HEALTH_FALLBACK, 3))
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    fallback_pool = ASTRO_HEALTH_FALLBACK + sum((c["tips"] for c in CULPRITS.values()), [])
    return _from_lines(lines, fallback_pool)
