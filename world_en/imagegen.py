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
from dataclasses import dataclass, field
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


@dataclass
class ImageGenerationResult:
    path: str
    backend: str
    byte_count: int
    content_type: str | None = None
    backend_attempts: list[dict] = field(default_factory=list)

    def __str__(self) -> str:
        return self.path

    def __fspath__(self) -> str:
        return self.path


def _ensure_parent_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def _min_valid_image_bytes() -> int:
    try:
        value = int(os.getenv("IMAGEGEN_MIN_VALID_BYTES", "4096"))
    except Exception:
        value = 4096
    return max(512, value)


def _image_signature(data: bytes) -> str | None:
    if data.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "webp"
    return None


def _delete_invalid(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except Exception:
        pass


def _validate_generated_image(
    *,
    backend: str,
    out_path: Path,
    payload: bytes,
    status_code: int | None = None,
    content_type: str | None = None,
) -> ImageGenerationResult | None:
    byte_count = len(payload or b"")
    content_type_clean = (content_type or "").split(";", 1)[0].strip().lower()
    if content_type is not None and not content_type_clean.startswith("image/"):
        logger.warning(
            "%s invalid image response: status=%s content_type=%s bytes=%d reason=content_type",
            backend,
            status_code,
            content_type_clean or "missing",
            byte_count,
        )
        _delete_invalid(out_path)
        return None
    if byte_count <= _min_valid_image_bytes():
        logger.warning(
            "%s invalid image response: status=%s content_type=%s bytes=%d reason=too_small",
            backend,
            status_code,
            content_type_clean or "missing",
            byte_count,
        )
        _delete_invalid(out_path)
        return None
    signature = _image_signature(payload)
    if signature is None:
        logger.warning(
            "%s invalid image response: status=%s content_type=%s bytes=%d reason=signature",
            backend,
            status_code,
            content_type_clean or "missing",
            byte_count,
        )
        _delete_invalid(out_path)
        return None
    if Image is None:
        logger.warning(
            "%s image validation unavailable: Pillow missing; accepting signature=%s bytes=%d",
            backend,
            signature,
            byte_count,
        )
    else:
        try:
            with Image.open(out_path) as im:  # type: ignore[attr-defined]
                width, height = im.size
                im.verify()
            if width < 256 or height < 256:
                logger.warning(
                    "%s invalid image response: status=%s content_type=%s bytes=%d reason=dimensions %sx%s",
                    backend,
                    status_code,
                    content_type_clean or "missing",
                    byte_count,
                    width,
                    height,
                )
                _delete_invalid(out_path)
                return None
        except Exception as exc:
            logger.warning(
                "%s invalid image response: status=%s content_type=%s bytes=%d reason=pillow_verify error=%s",
                backend,
                status_code,
                content_type_clean or "missing",
                byte_count,
                exc.__class__.__name__,
            )
            _delete_invalid(out_path)
            return None
    return ImageGenerationResult(
        path=str(out_path),
        backend=backend,
        byte_count=byte_count,
        content_type=content_type_clean or None,
    )


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
) -> Optional[ImageGenerationResult]:
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
            "Pollinations non-200: %s bytes=%d content_type=%s",
            resp.status_code,
            len(resp.content or b""),
            resp.headers.get("Content-Type", ""),
        )
        return None

    if not resp.content:
        logger.warning("Pollinations returned empty content")
        return None

    _ensure_parent_dir(out_path)
    out_path.write_bytes(resp.content)
    result = _validate_generated_image(
        backend="pollinations",
        out_path=out_path,
        payload=resp.content,
        status_code=resp.status_code,
        content_type=resp.headers.get("Content-Type"),
    )
    if result is None:
        return None

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
    return result


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
) -> Tuple[Optional[ImageGenerationResult], Optional[int], str]:
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
            "Horde async non-2xx: %s bytes=%d content_type=%s",
            resp.status_code,
            len(resp.content or b""),
            resp.headers.get("Content-Type", ""),
        )
        return None, resp.status_code, "AsyncNon2xx"

    try:
        data = resp.json()
    except Exception as exc:
        logger.warning("Horde async JSON error: %s bytes=%d", exc, len(resp.content or b""))
        return None, resp.status_code, "AsyncJSONError"

    job_id = data.get("id")
    if not job_id:
        logger.warning("Horde async response missing id; keys=%s", sorted(data.keys()) if isinstance(data, dict) else [])
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
                "Horde check non-200: %s bytes=%d content_type=%s",
                check_resp.status_code,
                len(check_resp.content or b""),
                check_resp.headers.get("Content-Type", ""),
            )
            time.sleep(5)
            continue

        try:
            check = check_resp.json()
        except Exception as exc:
            logger.warning("Horde check JSON error: %s bytes=%d", exc, len(check_resp.content or b""))
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
            "Horde status non-200: %s bytes=%d content_type=%s",
            gen_resp.status_code,
            len(gen_resp.content or b""),
            gen_resp.headers.get("Content-Type", ""),
        )
        return None, gen_resp.status_code, "StatusNon200"

    try:
        gen_data = gen_resp.json()
    except Exception as exc:
        logger.warning("Horde status JSON error: %s bytes=%d", exc, len(gen_resp.content or b""))
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
    result = _validate_generated_image(
        backend="stable_horde",
        out_path=out_path,
        payload=img_bytes,
        status_code=200,
        content_type=None,
    )
    if result is None:
        return None, 200, "InvalidImage"
    logger.info(
        "Horde image saved to %s (%d bytes)",
        out_path,
        out_path.stat().st_size,
    )
    return result, 200, ""


def _fetch_from_horde(
    prompt: str,
    out_path: Path,
    size: Tuple[int, int] = (512, 512),
    timeout: float = HORDE_TIMEOUT,
) -> Optional[ImageGenerationResult]:
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
) -> Optional[ImageGenerationResult]:
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
            "Custom backend non-200: %s bytes=%d content_type=%s",
            resp.status_code,
            len(resp.content or b""),
            resp.headers.get("Content-Type", ""),
        )
        return None

    if not resp.content:
        logger.warning("Custom backend returned empty content")
        return None

    _ensure_parent_dir(out_path)
    out_path.write_bytes(resp.content)
    result = _validate_generated_image(
        backend="custom",
        out_path=out_path,
        payload=resp.content,
        status_code=resp.status_code,
        content_type=resp.headers.get("Content-Type"),
    )
    if result is None:
        return None
    logger.info(
        "Custom backend image saved to %s (%d bytes)",
        out_path,
        out_path.stat().st_size,
    )
    return result


def generate_astro_image_result(
    prompt: str,
    out_path: str,
    size: Tuple[int, int] = (512, 512),
) -> Optional[ImageGenerationResult]:
    """
    Основная точка входа.

    :param prompt: текстовый промпт (ENG), который ты передаёшь из world_astro_collect.
    :param out_path: путь к файлу (строка); директории будут созданы при необходимости.
    :param size: размер картинки (ширина, высота)
    :return: результат генерации или None, если все бэкенды и все попытки упали.
    """
    out = Path(out_path)
    logger.info("Requested astro image at %s", out)
    logger.info("Max attempts: %d", MAX_ATTEMPTS)
    backend_attempts: list[dict] = []

    for attempt in range(1, MAX_ATTEMPTS + 1):
        logger.info("Image generation attempt %d/%d", attempt, MAX_ATTEMPTS)

        # 1) Pollinations
        backend_attempts.append({"attempt": attempt, "backend": "pollinations"})
        img = _fetch_from_pollinations(prompt, out, size=size)
        if img is not None:
            img.backend_attempts = list(backend_attempts)
            logger.info("Using Pollinations backend on attempt %d", attempt)
            return img

        # 2) Stable Horde / AI Horde
        backend_attempts.append({"attempt": attempt, "backend": "stable_horde"})
        img = _fetch_from_horde(prompt, out, size=size)
        if img is not None:
            img.backend_attempts = list(backend_attempts)
            logger.info("Using Stable Horde backend on attempt %d", attempt)
            return img

        # 3) Custom backend
        backend_attempts.append({"attempt": attempt, "backend": "custom"})
        img = _fetch_from_custom_backend(prompt, out, size=size)
        if img is not None:
            img.backend_attempts = list(backend_attempts)
            logger.info("Using CUSTOM backend on attempt %d", attempt)
            return img

        if attempt < MAX_ATTEMPTS:
            logger.warning("All backends failed on attempt %d, will retry...", attempt)
        else:
            logger.error("All backends failed after %d attempts, giving up", MAX_ATTEMPTS)

    return None


def generate_astro_image_result_with_exclusions(
    prompt: str,
    out_path: str,
    size: Tuple[int, int] = (512, 512),
    excluded_backends: set[str] | None = None,
) -> Optional[ImageGenerationResult]:
    excluded = {str(item).strip().lower() for item in (excluded_backends or set()) if str(item).strip()}
    if not excluded:
        return generate_astro_image_result(prompt, out_path, size=size)

    out = Path(out_path)
    logger.info("Requested astro image at %s with excluded backends: %s", out, sorted(excluded))
    backend_attempts: list[dict] = []
    for attempt in range(1, MAX_ATTEMPTS + 1):
        if "pollinations" not in excluded:
            backend_attempts.append({"attempt": attempt, "backend": "pollinations"})
            img = _fetch_from_pollinations(prompt, out, size=size)
            if img is not None:
                img.backend_attempts = list(backend_attempts)
                return img
        if "stable_horde" not in excluded and "horde" not in excluded:
            backend_attempts.append({"attempt": attempt, "backend": "stable_horde"})
            img = _fetch_from_horde(prompt, out, size=size)
            if img is not None:
                img.backend_attempts = list(backend_attempts)
                return img
        if "custom" not in excluded:
            backend_attempts.append({"attempt": attempt, "backend": "custom"})
            img = _fetch_from_custom_backend(prompt, out, size=size)
            if img is not None:
                img.backend_attempts = list(backend_attempts)
                return img
    return None


def generate_astro_image(
    prompt: str,
    out_path: str,
    size: Tuple[int, int] = (512, 512),
) -> Optional[str]:
    result = generate_astro_image_result(prompt, out_path, size=size)
    return result.path if result is not None else None


__all__ = [
    "ImageGenerationResult",
    "generate_astro_image",
    "generate_astro_image_result",
    "generate_astro_image_result_with_exclusions",
]
