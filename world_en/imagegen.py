"""
world_en/imagegen.py

Генерация картинок для астрологических постов.

Приоритет бэкендов внутри одной попытки:
1. Pollinations (без ключей) — быстрый бесплатный endpoint.
2. Stable Horde (через HORDE_API_KEY / STABLE_HORDE_API_KEY) как фолбэк.
3. Необязательный кастомный бэкенд (CUSTOM_IMAGE_BASE_URL), если настроен.

ФАЙЛ НИКОГДА НЕ ЛОГИРУЕТ КЛЮЧИ (они и не нужны, кроме HORDE_API_KEY).

Переменные окружения (опционально):

- POLLINATIONS_BASE_URL (по умолчанию "https://image.pollinations.ai/prompt/")
- POLLINATIONS_TIMEOUT (по умолчанию 20 секунд)

- HORDE_BASE_URL       (по умолчанию "https://stablehorde.net/api/v2")
- HORDE_TIMEOUT        (по умолчанию 90 секунд)
- STABLE_HORDE_API_KEY (секрет с API-ключом Horde; приоритетный)
- HORDE_API_KEY        (альтернативное имя переменной)
  если оба не заданы, используется "0000000000" — анонимный бесплатный ключ.

- IMAGEGEN_MAX_ATTEMPTS (общее число попыток генерации поверх всех бэкендов;
  по умолчанию 3, минимум 1, максимум 5)

Третий (опциональный) бэкенд:

- CUSTOM_IMAGE_BASE_URL — базовый URL сервиса, который принимает:
      GET {CUSTOM_IMAGE_BASE_URL}?prompt=...&width=...&height=...
  и возвращает непосредственно изображение (PNG/JPEG). Если переменная
  не задана или пустая, третий бэкенд не используется.

- CUSTOM_IMAGE_TIMEOUT  — таймаут для этого запроса (по умолчанию 20 секунд)
- CUSTOM_IMAGE_API_KEY  — опциональный токен для Authorization-заголовка
  (если внешний сервис его требует).

ОГРАНИЧЕНИЯ / ДОПУЩЕНИЯ:
- Предполагается, что Pollinations принимает GET:
    {POLLINATIONS_BASE_URL}/{urlencoded_prompt}?width=512&height=512
  и отдаёт непосредственно изображение (PNG/JPEG).
- Предполагается, что Stable Horde v2:
  * POST /generate/async -> {"id": "<job-id>", ...}
  * GET  /generate/check/{id} -> JSON с полем done / finished / state
  * GET  /generate/status/{id} -> {"generations": [{"img": "<base64>"}]}
"""

from __future__ import annotations

import base64
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple
from urllib.parse import quote_plus

import requests

# Базовый логгер для всех сообщений этого модуля.
logger = logging.getLogger("imagegen")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("[imagegen] %(levelname)s: %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
logger.setLevel(logging.INFO)

# ---------- Pollinations ----------

POLLINATIONS_BASE_URL = os.environ.get(
    "POLLINATIONS_BASE_URL",
    "https://image.pollinations.ai/prompt/",
)
POLLINATIONS_TIMEOUT = float(os.environ.get("POLLINATIONS_TIMEOUT", "20"))

# ---------- Stable Horde ----------

HORDE_BASE_URL = os.environ.get(
    "HORDE_BASE_URL",
    "https://stablehorde.net/api/v2",
)
HORDE_TIMEOUT = float(os.environ.get("HORDE_TIMEOUT", "90"))

# ВАЖНО: Stable Horde требует apikey даже для анонимного доступа.
# Приоритет:
#   1) STABLE_HORDE_API_KEY (секрет из GitHub Actions),
#   2) HORDE_API_KEY,
#   3) "0000000000" — стандартный анонимный ключ.
HORDE_API_KEY = (
    os.environ.get("STABLE_HORDE_API_KEY")
    or os.environ.get("HORDE_API_KEY")
    or "0000000000"
)

# ---------- Общие настройки ретраев ----------

# Максимальное количество общих попыток генерации поверх всех бэкендов.
# Настраивается через IMAGEGEN_MAX_ATTEMPTS, по умолчанию 3,
# минимум 1, максимум 5.
try:
    MAX_ATTEMPTS = int(os.environ.get("IMAGEGEN_MAX_ATTEMPTS", "3"))
    if MAX_ATTEMPTS < 1:
        MAX_ATTEMPTS = 1
    if MAX_ATTEMPTS > 5:
        MAX_ATTEMPTS = 5
except Exception:
    MAX_ATTEMPTS = 3

# ---------- Необязательный третий бэкенд ----------

CUSTOM_IMAGE_BASE_URL = os.environ.get("CUSTOM_IMAGE_BASE_URL", "").rstrip("/")
CUSTOM_IMAGE_TIMEOUT = float(os.environ.get("CUSTOM_IMAGE_TIMEOUT", "20"))
CUSTOM_IMAGE_API_KEY = os.environ.get("CUSTOM_IMAGE_API_KEY", "")


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _fetch_from_pollinations(
    prompt: str,
    out_path: Path,
    size: Tuple[int, int] = (512, 512),
) -> Optional[Path]:
    """
    Попытка получить картинку через Pollinations.

    Без ключей, только GET-запрос. Для борьбы с кэшем к prompt
    добавляется случайный UUID.
    """
    prompt_with_uuid = f"{prompt} :: {uuid.uuid4().hex}"
    query = quote_plus(prompt_with_uuid)

    # Допущение: width/height работают для управления размером изображения.
    url = (
        POLLINATIONS_BASE_URL.rstrip("/")
        + "/"
        + query
        + f"?width={size[0]}&height={size[1]}"
    )

    logger.info("Pollinations request: %s", url)
    headers = {
        "User-Agent": "WorldVibeMeterBot/1.0 (+https://t.me/worldvibemeter)",
    }

    try:
        resp = requests.get(url, headers=headers, timeout=POLLINATIONS_TIMEOUT)
    except Exception as exc:
        logger.warning("Pollinations error: %s", exc)
        return None

    if resp.status_code != 200:
        logger.warning(
            "Pollinations non-200: %s, body preview=%s",
            resp.status_code,
            resp.text[:200],
        )
        return None

    if not resp.content:
        logger.warning("Pollinations returned empty content")
        return None

    _ensure_parent_dir(out_path)
    out_path.write_bytes(resp.content)
    logger.info(
        "Pollinations image saved to %s (%d bytes)",
        out_path,
        out_path.stat().st_size,
    )
    return out_path


def _fetch_from_horde(
    prompt: str,
    out_path: Path,
    size: Tuple[int, int] = (512, 512),
    timeout: float = HORDE_TIMEOUT,
) -> Optional[Path]:
    """
    Фолбэк: генерация через Stable Horde.

    Используется HORDE_API_KEY (см. описание выше).
    Допущения по протоколу см. в модульном docstring.
    """
    headers = {
        "User-Agent": "WorldVibeMeterBot/1.0 (+https://t.me/worldvibemeter)",
        "Content-Type": "application/json",
        "apikey": HORDE_API_KEY,  # REQUIRED: иначе 400 "Missing required parameter"
    }

    payload = {
        "prompt": prompt,
        "params": {
            "width": size[0],
            "height": size[1],
            "steps": 25,
            "n": 1,
            "cfg_scale": 7,
            "sampler_name": "k_euler",
        },
        "nsfw": False,
        "censor_nsfw": True,
        "trusted_workers": False,
        "shared": True,
    }

    import json as _json

    try:
        logger.info("Stable Horde async request")
        resp = requests.post(
            f"{HORDE_BASE_URL}/generate/async",
            headers=headers,
            json=payload,
            timeout=15,
        )
    except Exception as exc:
        logger.warning("Horde async error: %s", exc)
        return None

    if resp.status_code not in (200, 202):
        logger.warning(
            "Horde async non-2xx: %s, body preview=%s",
            resp.status_code,
            resp.text[:200],
        )
        return None

    try:
        data = resp.json()
    except Exception as exc:
        logger.warning("Horde async JSON error: %s, body=%r", exc, resp.text[:200])
        return None

    job_id = data.get("id")
    if not job_id:
        logger.warning("Horde async response missing id: %s", _json.dumps(data)[:200])
        return None

    logger.info("Horde job id: %s", job_id)

    # Опрос статуса до HORDE_TIMEOUT.
    start = time.time()
    status_url = f"{HORDE_BASE_URL}/generate/check/{job_id}"
    done = False

    while time.time() - start < timeout:
        try:
            check_resp = requests.get(status_url, headers=headers, timeout=10)
        except Exception as exc:
            logger.warning("Horde check error: %s", exc)
            time.sleep(5)
            continue

        if check_resp.status_code != 200:
            logger.warning(
                "Horde check non-200: %s, body preview=%s",
                check_resp.status_code,
                check_resp.text[:200],
            )
            time.sleep(5)
            continue

        try:
            check = check_resp.json()
        except Exception as exc:
            logger.warning("Horde check JSON error: %s", exc)
            time.sleep(5)
            continue

        # ВАЖНО: формат статуса может меняться.
        # Здесь мы поддерживаем несколько вариантов:
        if check.get("done") or check.get("finished") or check.get("state") == "done":
            done = True
            break

        logger.info(
            "Horde still running: %s",
            {
                k: check.get(k)
                for k in ("queue_position", "waiting", "processing", "done")
            },
        )
        time.sleep(5)

    if not done:
        logger.warning("Horde timeout after %.1fs", time.time() - start)
        return None

    # Получаем результат
    try:
        gen_resp = requests.get(
            f"{HORDE_BASE_URL}/generate/status/{job_id}",
            headers=headers,
            timeout=20,
        )
    except Exception as exc:
        logger.warning("Horde status error: %s", exc)
        return None

    if gen_resp.status_code != 200:
        logger.warning(
            "Horde status non-200: %s, body preview=%s",
            gen_resp.status_code,
            gen_resp.text[:200],
        )
        return None

    try:
        gen_data = gen_resp.json()
    except Exception as exc:
        logger.warning("Horde status JSON error: %s", exc)
        return None

    generations = gen_data.get("generations") or []
    if not generations:
        logger.warning("Horde returned no generations: %s", str(gen_data)[:200])
        return None

    first = generations[0]
    b64_img = first.get("img")
    if not b64_img:
        logger.warning("Horde generation missing 'img' field: %s", str(first)[:200])
        return None

    try:
        img_bytes = base64.b64decode(b64_img)
    except Exception as exc:
        logger.warning("Horde base64 decode error: %s", exc)
        return None

    _ensure_parent_dir(out_path)
    out_path.write_bytes(img_bytes)
    logger.info(
        "Horde image saved to %s (%d bytes)",
        out_path,
        out_path.stat().st_size,
    )
    return out_path


def _fetch_from_custom_backend(
    prompt: str,
    out_path: Path,
    size: Tuple[int, int] = (512, 512),
) -> Optional[Path]:
    """
    Опциональный третий бэкенд.

    Предполагается самый простой протокол:
    - GET {CUSTOM_IMAGE_BASE_URL}?prompt=...&width=...&height=...
    - в ответ приходит сразу изображение (PNG/JPEG).

    Если CUSTOM_IMAGE_BASE_URL не задан, функция просто возвращает None.
    Это сделано, чтобы файл оставался рабочим "из коробки", даже если
    третий бэкенд не настроен.
    """
    if not CUSTOM_IMAGE_BASE_URL:
        return None

    query = quote_plus(prompt)
    url = (
        CUSTOM_IMAGE_BASE_URL
        + f"?prompt={query}&width={size[0]}&height={size[1]}"
    )

    headers = {
        "User-Agent": "WorldVibeMeterBot/1.0 (+https://t.me/worldvibemeter)",
    }
    if CUSTOM_IMAGE_API_KEY:
        # если какой-то сервис требует авторизацию — можно передать сюда
        headers["Authorization"] = CUSTOM_IMAGE_API_KEY

    logger.info("Custom backend request: %s", url)

    try:
        resp = requests.get(url, headers=headers, timeout=CUSTOM_IMAGE_TIMEOUT)
    except Exception as exc:
        logger.warning("Custom backend error: %s", exc)
        return None

    if resp.status_code != 200:
        logger.warning(
            "Custom backend non-200: %s, body preview=%s",
            resp.status_code,
            resp.text[:200],
        )
        return None

    if not resp.content:
        logger.warning("Custom backend returned empty content")
        return None

    _ensure_parent_dir(out_path)
    out_path.write_bytes(resp.content)
    logger.info(
        "Custom backend image saved to %s (%d bytes)",
        out_path,
        out_path.stat().st_size,
    )
    return out_path


def generate_astro_image(
    prompt: str,
    out_path: str,
    size: Tuple[int, int] = (512, 512),
) -> Optional[str]:
    """
    Основная точка входа.

    :param prompt: текстовый промпт (ENG), который ты передаёшь из world_astro_collect.
    :param out_path: путь к файлу (строка); директории будут созданы при необходимости.
    :param size: размер картинки (ширина, высота), сейчас используется как hint.
    :return: строка пути к файлу или None, если все бэкенды и все попытки упали.
    """
    out = Path(out_path)
    logger.info("Requested astro image at %s", out)
    logger.info("Max attempts: %d", MAX_ATTEMPTS)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.info("Image generation attempt %d/%d", attempt, MAX_ATTEMPTS)

        # 1. Pollinations
        img = _fetch_from_pollinations(prompt, out, size=size)
        if img is not None:
            logger.info("Using Pollinations backend on attempt %d", attempt)
            return str(img)

        # 2. Stable Horde
        img = _fetch_from_horde(prompt, out, size=size)
        if img is not None:
            logger.info("Using Stable Horde backend on attempt %d", attempt)
            return str(img)

        # 3. Custom backend (если настроен)
        img = _fetch_from_custom_backend(prompt, out, size=size)
        if img is not None:
            logger.info("Using CUSTOM backend on attempt %d", attempt)
            return str(img)

        if attempt < MAX_ATTEMPTS:
            logger.warning(
                "All backends failed on attempt %d, will retry...", attempt
            )
        else:
            logger.error(
                "All backends failed after %d attempts, giving up",
                MAX_ATTEMPTS,
            )

    return None


__all__ = ["generate_astro_image"]
