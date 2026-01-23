"""
world_en/imagegen.py

Генерация картинок для астрологических постов.

Приоритет бэкендов внутри одной попытки:
1. Pollinations (prefer token; иначе анонимный endpoint с лимитами).
2. Stable Horde / AI Horde (через STABLE_HORDE_API_KEY / HORDE_API_KEY) как фолбэк.
3. Необязательный кастомный бэкенд (CUSTOM_IMAGE_BASE_URL), если настроен.

ФАЙЛ НИКОГДА НЕ ЛОГИРУЕТ КЛЮЧИ.

Переменные окружения (опционально):

Pollinations:
- POLLINATIONS_BASE_URL (по умолчанию "https://image.pollinations.ai/prompt/")
- POLLINATIONS_TIMEOUT (по умолчанию 30 секунд)
- POLLINATIONS_TOKEN   (секретный токен/ключ; используется в заголовках)
  Синонимы (на всякий случай): POLLINATIONS_API_KEY, POLLINATIONS_KEY
- POLLINATIONS_REFERRER (строка для referrer-параметра; по умолчанию "worldvibemeter")
- POLLINATIONS_TOKEN_AS_QUERY (0/1) — если 1, при неудаче попробует token ещё и query-параметром
  В логах токен будет замаскирован.
- POLLINATIONS_TOKEN_PARAM (по умолчанию "token") — имя query-параметра, если включён режим выше.
- POLLINATIONS_PLACEHOLDER_MAX_HAMMING (по умолчанию 10) — порог для детекта placeholder по aHash.

Stable Horde / AI Horde:
- HORDE_BASE_URL       (по умолчанию "https://stablehorde.net/api/v2")
- HORDE_TIMEOUT        (по умолчанию 90 секунд)
- STABLE_HORDE_API_KEY (секрет с API-ключом Horde; приоритетный)
- HORDE_API_KEY        (альтернативное имя переменной)
  если оба не заданы, используется "0000000000" — анонимный ключ (может быть ограничен).
- HORDE_TRY_ANON_ON_401 (0/1) — если 1 и ваш ключ дал 401, один раз попробует "0000000000"

Общие:
- IMAGEGEN_MAX_ATTEMPTS (общее число попыток генерации поверх всех бэкендов;
  по умолчанию 3, минимум 1, максимум 5)

Третий (опциональный) бэкенд:
- CUSTOM_IMAGE_BASE_URL — базовый URL сервиса, который принимает:
      GET {CUSTOM_IMAGE_BASE_URL}?prompt=...&width=...&height=...
  и возвращает непосредственно изображение (PNG/JPEG).
- CUSTOM_IMAGE_TIMEOUT  — таймаут для этого запроса (по умолчанию 20 секунд)
- CUSTOM_IMAGE_API_KEY  — опциональный токен для Authorization-заголовка.

Ограничения:
- Pollinations может вернуть картинку-заглушку "RATE LIMIT REACHED".
  Мы детектим её по perceptual aHash и НЕ считаем успехом (переходим к фолбэкам).
"""

from __future__ import annotations

import base64
import logging
import os
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple, Dict
from urllib.parse import quote_plus

import requests

# Pillow используется ТОЛЬКО для детекта placeholder.
try:
    from PIL import Image  # type: ignore
except Exception:
    Image = None  # type: ignore

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
POLLINATIONS_TIMEOUT = float(os.environ.get("POLLINATIONS_TIMEOUT", "30"))

# Токен/ключ Pollinations (если задан — используем).
POLLINATIONS_TOKEN = (
    os.environ.get("POLLINATIONS_TOKEN")
    or os.environ.get("POLLINATIONS_API_KEY")
    or os.environ.get("POLLINATIONS_KEY")
    or ""
).strip()

POLLINATIONS_REFERRER = os.environ.get("POLLINATIONS_REFERRER", "worldvibemeter").strip()

POLLINATIONS_TOKEN_AS_QUERY = os.environ.get("POLLINATIONS_TOKEN_AS_QUERY", "0").strip() == "1"
POLLINATIONS_TOKEN_PARAM = os.environ.get("POLLINATIONS_TOKEN_PARAM", "token").strip() or "token"

try:
    POLLINATIONS_PLACEHOLDER_MAX_HAMMING = int(
        os.environ.get("POLLINATIONS_PLACEHOLDER_MAX_HAMMING", "10")
    )
except Exception:
    POLLINATIONS_PLACEHOLDER_MAX_HAMMING = 10

# aHash (8x8) для известной заглушки Pollinations "RATE LIMIT REACHED"
# Получено по референсному изображению. Если Pollinations поменяет дизайн,
# можно обновить значение (или увеличить порог).
_POLLINATIONS_PLACEHOLDER_AHASHES = {
    0x007EFF1E6C6C0E1C,
}

# ---------- Stable Horde / AI Horde ----------

HORDE_BASE_URL = os.environ.get(
    "HORDE_BASE_URL",
    "https://stablehorde.net/api/v2",
)
HORDE_TIMEOUT = float(os.environ.get("HORDE_TIMEOUT", "90"))

# Приоритет:
#   1) STABLE_HORDE_API_KEY,
#   2) HORDE_API_KEY,
#   3) "0000000000"
HORDE_API_KEY = (
    (os.environ.get("STABLE_HORDE_API_KEY") or "").strip()
    or (os.environ.get("HORDE_API_KEY") or "").strip()
    or "0000000000"
)

HORDE_TRY_ANON_ON_401 = os.environ.get("HORDE_TRY_ANON_ON_401", "1").strip() == "1"

# ---------- Общие настройки ретраев ----------

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
CUSTOM_IMAGE_API_KEY = os.environ.get("CUSTOM_IMAGE_API_KEY", "").strip()


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _hamming_distance(a: int, b: int) -> int:
    return (a ^ b).bit_count()


def _ahash_8x8(img: "Image.Image") -> int:  # type: ignore[name-defined]
    """
    Average hash 8x8 -> 64-bit int.
    """
    gray = img.convert("L").resize((8, 8), Image.Resampling.LANCZOS)  # type: ignore[attr-defined]
    pixels = list(gray.getdata())
    avg = sum(pixels) / len(pixels)
    bits = 0
    for i, px in enumerate(pixels):
        if px > avg:
            bits |= 1 << (63 - i)
    return bits


def _looks_like_pollinations_placeholder(img_path: Path) -> bool:
    """
    Быстрый детект заглушки Pollinations ("RATE LIMIT REACHED") по aHash.
    Если Pillow недоступен — возвращает False (не блокируем пайплайн).
    """
    if Image is None:
        return False

    try:
        with Image.open(img_path) as im:  # type: ignore[attr-defined]
            h = _ahash_8x8(im)
    except Exception:
        return False

    for ref in _POLLINATIONS_PLACEHOLDER_AHASHES:
        if _hamming_distance(h, ref) <= POLLINATIONS_PLACEHOLDER_MAX_HAMMING:
            return True
    return False


def _pollinations_headers() -> Dict[str, str]:
    """
    Собираем заголовки Pollinations.
    Токен никогда не логируем.
    """
    headers = {
        "User-Agent": "WorldVibeMeterBot/1.0 (+https://t.me/worldvibemeter)",
        "Accept": "image/*",
    }
    if POLLINATIONS_TOKEN:
        # Основной вариант: Bearer token (согласно их auth-докам).
        headers["Authorization"] = f"Bearer {POLLINATIONS_TOKEN}"
        # На случай альтернативной схемы на их стороне — добавляем распространённые варианты.
        headers["X-API-Key"] = POLLINATIONS_TOKEN
        headers["apikey"] = POLLINATIONS_TOKEN
    return headers


def _pollinations_url(prompt: str, size: Tuple[int, int]) -> str:
    """
    Строим URL, добавляя referrer (безопасно) и размеры.
    Для борьбы с кэшем добавляем UUID в prompt.
    """
    prompt_with_uuid = f"{prompt} :: {uuid.uuid4().hex}"
    query = quote_plus(prompt_with_uuid)

    base = POLLINATIONS_BASE_URL.rstrip("/")
    # referrer — не секрет, можно держать в URL
    return f"{base}/{query}?width={size[0]}&height={size[1]}&referrer={quote_plus(POLLINATIONS_REFERRER)}"


def _pollinations_url_with_token(url: str) -> Tuple[str, str]:
    """
    Добавляет токен query-параметром (если включено), возвращает:
    (real_url, safe_url_for_logs)
    """
    if not POLLINATIONS_TOKEN:
        return url, url

    joiner = "&" if "?" in url else "?"
    real = f"{url}{joiner}{POLLINATIONS_TOKEN_PARAM}={quote_plus(POLLINATIONS_TOKEN)}"
    safe = f"{url}{joiner}{POLLINATIONS_TOKEN_PARAM}=***"
    return real, safe


def _fetch_from_pollinations(
    prompt: str,
    out_path: Path,
    size: Tuple[int, int] = (512, 512),
) -> Optional[Path]:
    """
    Попытка получить картинку через Pollinations.

    1) Пробуем с заголовками (Bearer/keys).
    2) Если включён POLLINATIONS_TOKEN_AS_QUERY=1 — при неудаче пробуем query-token.
    3) Если получили заглушку RATE LIMIT — считаем это НЕУДАЧЕЙ, чтобы включился фолбэк.
    """
    url = _pollinations_url(prompt, size)
    headers = _pollinations_headers()

    # 1) основной запрос (заголовки)
    logger.info("Pollinations request: %s", url)
    try:
        resp = requests.get(url, headers=headers, timeout=POLLINATIONS_TIMEOUT)
    except Exception as exc:
        logger.warning("Pollinations error: %s", exc)
        resp = None

    # 2) опциональный повтор с query-token (если включено)
    if (resp is None or resp.status_code != 200) and POLLINATIONS_TOKEN_AS_QUERY:
        real_url, safe_url = _pollinations_url_with_token(url)
        logger.info("Pollinations request (query-token): %s", safe_url)
        try:
            resp = requests.get(real_url, headers={k: v for k, v in headers.items() if k.lower() != "authorization"}, timeout=POLLINATIONS_TIMEOUT)
        except Exception as exc:
            logger.warning("Pollinations error (query-token): %s", exc)
            return None

    if resp is None:
        return None

    if resp.status_code != 200:
        logger.warning(
            "Pollinations non-200: %s, body preview=%s",
            resp.status_code,
            (resp.text or "")[:200],
        )
        return None

    if not resp.content:
        logger.warning("Pollinations returned empty content")
        return None

    _ensure_parent_dir(out_path)
    out_path.write_bytes(resp.content)

    # Детект заглушки (если это она — удаляем и считаем неудачей)
    if _looks_like_pollinations_placeholder(out_path):
        logger.warning("Pollinations returned RATE LIMIT placeholder image (detected) — will fallback")
        try:
            out_path.unlink(missing_ok=True)  # py3.8+; на GH actions обычно 3.11+
        except Exception:
            pass
        return None

    logger.info(
        "Pollinations image saved to %s (%d bytes)",
        out_path,
        out_path.stat().st_size,
    )
    return out_path


def _horde_headers(api_key: str) -> Dict[str, str]:
    return {
        "User-Agent": "WorldVibeMeterBot/1.0 (+https://t.me/worldvibemeter)",
        "Content-Type": "application/json",
        "apikey": (api_key or "0000000000"),
    }


def _fetch_from_horde_once(
    prompt: str,
    out_path: Path,
    size: Tuple[int, int],
    timeout: float,
    api_key: str,
) -> Tuple[Optional[Path], Optional[int], str]:
    """
    Одна попытка Horde с конкретным api_key.
    Возвращает (path|None, http_status|None, error_code_str)
    """
    headers = _horde_headers(api_key)

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
        return None, None, "AsyncRequestError"

    if resp.status_code not in (200, 202):
        logger.warning(
            "Horde async non-2xx: %s, body preview=%s",
            resp.status_code,
            resp.text[:200],
        )
        return None, resp.status_code, "AsyncNon2xx"

    try:
        data = resp.json()
    except Exception as exc:
        logger.warning("Horde async JSON error: %s, body=%r", exc, resp.text[:200])
        return None, resp.status_code, "AsyncJSONError"

    job_id = data.get("id")
    if not job_id:
        logger.warning("Horde async response missing id: %s", _json.dumps(data)[:200])
        return None, resp.status_code, "MissingJobId"

    logger.info("Horde job id: %s", job_id)

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
        return None, 200, "Timeout"

    try:
        gen_resp = requests.get(
            f"{HORDE_BASE_URL}/generate/status/{job_id}",
            headers=headers,
            timeout=20,
        )
    except Exception as exc:
        logger.warning("Horde status error: %s", exc)
        return None, None, "StatusRequestError"

    if gen_resp.status_code != 200:
        logger.warning(
            "Horde status non-200: %s, body preview=%s",
            gen_resp.status_code,
            gen_resp.text[:200],
        )
        return None, gen_resp.status_code, "StatusNon200"

    try:
        gen_data = gen_resp.json()
    except Exception as exc:
        logger.warning("Horde status JSON error: %s", exc)
        return None, gen_resp.status_code, "StatusJSONError"

    generations = gen_data.get("generations") or []
    if not generations:
        logger.warning("Horde returned no generations: %s", str(gen_data)[:200])
        return None, gen_resp.status_code, "NoGenerations"

    first = generations[0]
    b64_img = first.get("img")
    if not b64_img:
        logger.warning("Horde generation missing 'img' field: %s", str(first)[:200])
        return None, gen_resp.status_code, "MissingImgField"

    try:
        img_bytes = base64.b64decode(b64_img)
    except Exception as exc:
        logger.warning("Horde base64 decode error: %s", exc)
        return None, gen_resp.status_code, "Base64DecodeError"

    _ensure_parent_dir(out_path)
    out_path.write_bytes(img_bytes)
    logger.info(
        "Horde image saved to %s (%d bytes)",
        out_path,
        out_path.stat().st_size,
    )
    return out_path, 200, ""


def _fetch_from_horde(
    prompt: str,
    out_path: Path,
    size: Tuple[int, int] = (512, 512),
    timeout: float = HORDE_TIMEOUT,
) -> Optional[Path]:
    """
    Фолбэк: генерация через Stable Horde / AI Horde.

    Используется HORDE_API_KEY (см. описание выше).
    Если получаем 401 и HORDE_TRY_ANON_ON_401=1 — пробуем один раз "0000000000".
    """
    img, status, err = _fetch_from_horde_once(prompt, out_path, size, timeout, HORDE_API_KEY)
    if img is not None:
        return img

    if status == 401 and HORDE_TRY_ANON_ON_401 and (HORDE_API_KEY.strip() != "0000000000"):
        logger.warning("Horde returned 401 for provided key — trying anonymous key 0000000000 once")
        img2, _, _ = _fetch_from_horde_once(prompt, out_path, size, timeout, "0000000000")
        return img2

    return None


def _fetch_from_custom_backend(
    prompt: str,
    out_path: Path,
    size: Tuple[int, int] = (512, 512),
) -> Optional[Path]:
    """
    Опциональный третий бэкенд.

    Протокол:
    - GET {CUSTOM_IMAGE_BASE_URL}?prompt=...&width=...&height=...
    - в ответ приходит сразу изображение (PNG/JPEG).
    """
    if not CUSTOM_IMAGE_BASE_URL:
        return None

    query = quote_plus(prompt)
    url = CUSTOM_IMAGE_BASE_URL + f"?prompt={query}&width={size[0]}&height={size[1]}"

    headers = {
        "User-Agent": "WorldVibeMeterBot/1.0 (+https://t.me/worldvibemeter)",
        "Accept": "image/*",
    }
    if CUSTOM_IMAGE_API_KEY:
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
            (resp.text or "")[:200],
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
    :param size: размер картинки (ширина, высота)
    :return: строка пути к файлу или None, если все бэкенды и все попытки упали.
    """
    out = Path(out_path)
    logger.info("Requested astro image at %s", out)
    logger.info("Max attempts: %d", MAX_ATTEMPTS)

    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.info("Image generation attempt %d/%d", attempt, MAX_ATTEMPTS)

        # 1) Pollinations
        img = _fetch_from_pollinations(prompt, out, size=size)
        if img is not None:
            logger.info("Using Pollinations backend on attempt %d", attempt)
            return str(img)

        # 2) Stable Horde / AI Horde
        img = _fetch_from_horde(prompt, out, size=size)
        if img is not None:
            logger.info("Using Stable Horde backend on attempt %d", attempt)
            return str(img)

        # 3) Custom backend
        img = _fetch_from_custom_backend(prompt, out, size=size)
        if img is not None:
            logger.info("Using CUSTOM backend on attempt %d", attempt)
            return str(img)

        if attempt < MAX_ATTEMPTS:
            logger.warning("All backends failed on attempt %d, will retry...", attempt)
        else:
            logger.error("All backends failed after %d attempts, giving up", MAX_ATTEMPTS)

    return None


__all__ = ["generate_astro_image"]
