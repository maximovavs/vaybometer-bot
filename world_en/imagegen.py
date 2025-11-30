"""
world_en/imagegen.py

Генерация картинок для астрологических постов.

Приоритет:
1. Pollinations (без ключей) — быстрый бесплатный endpoint.
2. Stable Horde (анонимный доступ) как фолбэк.

ФАЙЛ НИКОГДА НЕ ЛОГИРУЕТ КЛЮЧИ (они и не используются).
Все параметры конфигурируются через переменные окружения:

- POLLINATIONS_BASE_URL (по умолчанию "https://image.pollinations.ai/prompt/")
- POLLINATIONS_TIMEOUT (по умолчанию 20 секунд)
- HORDE_BASE_URL (по умолчанию "https://stablehorde.net/api/v2")
- HORDE_TIMEOUT (по умолчанию 90 секунд)

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

POLLINATIONS_BASE_URL = os.environ.get(
    "POLLINATIONS_BASE_URL",
    "https://image.pollinations.ai/prompt/",
)
POLLINATIONS_TIMEOUT = float(os.environ.get("POLLINATIONS_TIMEOUT", "20"))

HORDE_BASE_URL = os.environ.get(
    "HORDE_BASE_URL",
    "https://stablehorde.net/api/v2",
)
HORDE_TIMEOUT = float(os.environ.get("HORDE_TIMEOUT", "90"))


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

    Используется анонимный доступ (без api key).
    Допущения по протоколу см. в модульном docstring.
    """
    headers = {
        "User-Agent": "WorldVibeMeterBot/1.0 (+https://t.me/worldvibemeter)",
        "Content-Type": "application/json",
        # "apikey": "0000000000",  # можно включить анонимный ключ при необходимости
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
            {k: check.get(k) for k in ("queue_position", "waiting", "processing", "done")},
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
    :return: строка пути к файлу или None, если все бэкенды упали.
    """
    out = Path(out_path)
    logger.info("Requested astro image at %s", out)

    # 1. Pollinations
    img = _fetch_from_pollinations(prompt, out, size=size)
    if img is not None:
        logger.info("Using Pollinations backend")
        return str(img)

    # 2. Stable Horde
    img = _fetch_from_horde(prompt, out, size=size)
    if img is not None:
        logger.info("Using Stable Horde backend")
        return str(img)

    logger.error("All image backends failed for prompt: %r", prompt)
    return None


__all__ = ["generate_astro_image"]
