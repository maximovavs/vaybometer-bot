import os
import re
import time
import base64
import logging
from datetime import datetime

import requests

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Директория для сохранения изображений: world_en/astro_img
ASTRO_IMG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "astro_img"))
os.makedirs(ASTRO_IMG_DIR, exist_ok=True)

POLLINATIONS_BASE = "https://image.pollinations.ai/prompt"
HORDE_BASE = "https://stablehorde.net/api/v2"

# Вежливый Client-Agent для Stable Horde (рекомендуется)
CLIENT_AGENT = os.getenv(
    "HORDE_CLIENT_AGENT",
    "VayboMeter/1.0 (github.com/maximovavs/vaybometer-bot)"
)
HORDE_API_KEY = os.getenv("HORDE_API_KEY", "")  # можно пустым — будет аноним

def _safe_slug(s: str, max_len: int = 64) -> str:
    s = s.strip()
    s = re.sub(r"\s+", "_", s)
    s = re.sub(r"[^a-zA-Z0-9_\-\.]+", "", s)
    return s[:max_len] if s else "img"

def _save_image(image_bytes: bytes, date_str: str) -> str:
    # Имя файла — по дате; дата безопасна, но всё равно нормализуем
    safe = _safe_slug(date_str or datetime.utcnow().isoformat()[:10])
    filename = f"astro_{safe}.jpg"
    path = os.path.join(ASTRO_IMG_DIR, filename)
    with open(path, "wb") as f:
        f.write(image_bytes)
    return os.path.abspath(path)

def _make_prompt(moon_phase: str, moon_sign: str) -> tuple[str, str]:
    phase = (moon_phase or "Moon").strip()
    sign = (moon_sign or "Sky").strip()
    prompt = (
        f"{phase} moon in the sky of {sign}, atmospheric, dreamy, cinematic, "
        f"soft colors, volumetric light, shallow depth of field, high detail, 4k"
    )
    negative = "ugly, blurry, distorted, text, watermark, logo"
    return prompt, negative

def _try_pollinations(prompt: str, width: int = 768, height: int = 432) -> bytes | None:
    """
    Pollinations прямой байтовый эндпоинт: https://image.pollinations.ai/prompt/{prompt}
    """
    try:
        url = f"{POLLINATIONS_BASE}/{requests.utils.quote(prompt)}?width={width}&height={height}&nologo=true"
        resp = requests.get(url, timeout=30)
        if resp.status_code == 200 and resp.headers.get("Content-Type", "").startswith("image/"):
            return resp.content
        logger.warning(f"Pollinations non-image or bad status: {resp.status_code} {resp.headers.get('Content-Type')}")
    except Exception as e:
        logger.warning(f"Pollinations failed: {e}")
    return None

def _try_stable_horde(prompt: str, negative_prompt: str, width: int = 768, height: int = 432) -> bytes | None:
    """
    Stable Horde async API. Возвращает base64 в generations[0].img.
    """
    try:
        payload = {
            "prompt": prompt,
            "params": {
                "sampler_name": "k_euler_a",
                "cfg_scale": 7,
                "steps": 20,
                "width": width,
                "height": height,
                "karras": True,
                "clip_skip": 1,
                "post_processing": [],
                "n": 1,
                "use_nsfw_censor": True,
                "denoising_strength": None,
                "seed": None,
                "image_is_control": False,
                "tiling": False,
                "hires_fix": False,
                "negative_prompt": negative_prompt,
            },
            "nsfw": False,
            "censor_nsfw": True,
            # модель укажем более распространённую; fallback на бэкенд
            "models": ["Deliberate"],
            # r2 ускоряет выдачу CDN-ссылок; но нам достаточно base64
            "r2": False,
        }
        headers = {
            "Content-Type": "application/json",
            "Client-Agent": CLIENT_AGENT,
        }
        if HORDE_API_KEY:
            headers["apikey"] = HORDE_API_KEY

        # Старт задачи
        start = requests.post(f"{HORDE_BASE}/generate/async", json=payload, headers=headers, timeout=40)
        if start.status_code != 202:
            logger.warning(f"Horde start failed: {start.status_code} {start.text[:200]}")
            return None

        job_id = start.json().get("id")
        if not job_id:
            logger.warning("Horde: no job id in response")
            return None

        # Опрос статуса
        max_wait_s = int(os.getenv("HORDE_MAX_WAIT", "120"))
        deadline = time.monotonic() + max_wait_s
        while time.monotonic() < deadline:
            time.sleep(2)
            st = requests.get(f"{HORDE_BASE}/generate/status/{job_id}", headers={"Client-Agent": CLIENT_AGENT}, timeout=30)
            if st.status_code != 200:
                continue
            data = st.json()
            if data.get("done") and data.get("generations"):
                gen = data["generations"][0]
                img_b64 = gen.get("img")
                if not img_b64:
                    logger.warning("Horde: empty img field")
                    return None
                # Может приходить в виде data:image/png;base64,xxxx
                if img_b64.startswith("data:"):
                    img_b64 = img_b64.split(",", 1)[-1]
                try:
                    return base64.b64decode(img_b64)
                except Exception as e:
                    logger.warning(f"Horde base64 decode error: {e}")
                    return None
        logger.warning("Horde timed out waiting for result")
    except Exception as e:
        logger.warning(f"Stable Horde failed: {e}")
    return None

def generate_astro_image(moon_phase: str, moon_sign: str, date_str: str | None = None) -> str | None:
    """
    Генерация изображения на основе астроданных. Возвращает абсолютный путь к файлу или None.
    Порядок источников: Stable Horde -> Pollinations.
    """
    if not date_str:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")

    prompt, negative_prompt = _make_prompt(moon_phase, moon_sign)

    # 1) Stable Horde (бесплатно, но очередь/паузы возможны)
    img = _try_stable_horde(prompt, negative_prompt)
    if not img:
        # 2) Pollinations (бесплатно; иногда отдаёт не-изображение — проверяем Content-Type)
        img = _try_pollinations(prompt)

    if img:
        try:
            path = _save_image(img, date_str)
            logger.info(f"[imagegen] saved: {path}")
            return path
        except Exception as e:
            logger.error(f"Save failed: {e}")

    logger.error("All image generation methods failed.")
    return None
