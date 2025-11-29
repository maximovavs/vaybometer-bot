#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import time
import base64
import logging
from datetime import datetime
from typing import Optional
import requests

logger = logging.getLogger(__name__)
if not logger.handlers:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

# Директория для сохранения изображений: <repo_root>/astro_img
ASTRO_IMG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "astro_img"))
os.makedirs(ASTRO_IMG_DIR, exist_ok=True)

# Настройки Stable Horde
STABLE_HORDE_API = "https://stablehorde.net/api/v2"
STABLE_HORDE_API_KEY = os.getenv("STABLE_HORDE_API_KEY", "0000000000")  # анонимный ключ допустим
STABLE_HORDE_TIMEOUT_SEC = int(os.getenv("STABLE_HORDE_TIMEOUT_SEC", "90"))

# Общие HTTP-заголовки
COMMON_HEADERS = {
    "User-Agent": "WorldVibeMeterBot/1.0 (+https://github.com/)",
    "Cache-Control": "no-cache",
    "Pragma": "no-cache",
}

def _save_image_bytes(image_bytes: bytes, date_str: str) -> str:
    filename = f"astro_{date_str}.jpg"
    path = os.path.join(ASTRO_IMG_DIR, filename)
    with open(path, "wb") as f:
        f.write(image_bytes)
    logger.info("[imagegen] saved %s (%d bytes)", path, len(image_bytes))
    return path

def _make_prompt(moon_phase: str, moon_sign: str) -> tuple[str, str]:
    phase = (moon_phase or "Moon").strip()
    sign = (moon_sign or "").strip()
    prompt = (
        f"{phase} in {sign} sky, atmospheric dreamy night, moonglow, clouds, "
        f"cinematic composition, high detail, 4k, soft color grading"
    ).strip()
    negative = "ugly, blurry, deformed, distorted, text, watermark, lowres"
    return prompt, negative

# ---------------- Pollinations (без ключа; быстрый фолбэк) ----------------

def _try_pollinations(prompt: str, width: int = 1024, height: int = 576) -> Optional[bytes]:
    """
    Корректный эндпоинт: https://image.pollinations.ai/prompt/{prompt}
    Параметры через query: ?n=1&width=...&height=...
    """
    try:
        url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}"
        params = {"n": 1, "width": width, "height": height}
        headers = dict(COMMON_HEADERS)
        headers["Accept"] = "image/*"
        r = requests.get(url, params=params, headers=headers, timeout=25)
        if r.status_code == 200 and r.content:
            # Иногда сервис возвращает HTML при перегрузке — проверим сигнатуру JPEG/PNG
            b = r.content
            if b.startswith(b"\xff\xd8") or b.startswith(b"\x89PNG"):
                return b
            logger.warning("[imagegen] Pollinations returned non-image payload (%d bytes)", len(b))
        else:
            logger.warning("[imagegen] Pollinations HTTP %s", r.status_code)
    except Exception as e:
        logger.warning("[imagegen] Pollinations failed: %s", e)
    return None

# ---------------- Stable Horde (бесплатно, но может быть очередь) ----------------

def _decode_horde_image(img_field: str) -> Optional[bytes]:
    """
    В ответах бывает либо полный URL, либо data:image/...;base64,....
    """
    if not img_field:
        return None
    if img_field.startswith("data:"):
        try:
            b64 = img_field.split(",", 1)[1]
            return base64.b64decode(b64)
        except Exception:
            return None
    # Иначе считаем, что это URL
    try:
        r = requests.get(img_field, headers=COMMON_HEADERS, timeout=30)
        if r.status_code == 200 and r.content:
            return r.content
    except Exception:
        return None
    return None

def _try_stable_horde(prompt: str, negative_prompt: str,
                      width: int = 768, height: int = 512) -> Optional[bytes]:
    """
    Асинхронный запрос генерации и опрос статуса.
    """
    try:
        headers = dict(COMMON_HEADERS)
        headers["Content-Type"] = "application/json"
        headers["apikey"] = STABLE_HORDE_API_KEY

        payload = {
            "prompt": prompt,
            "params": {
                "width": width,
                "height": height,
                "steps": 28,
                "cfg_scale": 7.0,
                "sampler_name": "k_euler",
                "seed": None,
                "karras": True,
                "hires_fix": False,
                "post_processing": [],
                "tiling": False,
                "denoising_strength": None,
                "facefixer_strength": None,
                "clip_skip": None,
                "lora": [],
                "negative_prompt": negative_prompt,
            },
            "nsfw": False,
            "censor_nsfw": True,
            "trusted_workers": False,
            "models": ["stable_diffusion"],
            "r2": True,
            "shared": False,
            "slow_workers": True,
        }

        submit = requests.post(
            f"{STABLE_HORDE_API}/generate/async",
            json=payload, headers=headers, timeout=30
        )
        if submit.status_code != 202:
            logger.warning("[imagegen] Horde submit HTTP %s: %s", submit.status_code, submit.text)
            return None
        job_id = submit.json().get("id")
        if not job_id:
            return None

        deadline = time.time() + STABLE_HORDE_TIMEOUT_SEC
        while time.time() < deadline:
            time.sleep(2.5)
            st = requests.get(f"{STABLE_HORDE_API}/generate/status/{job_id}",
                              headers=COMMON_HEADERS, timeout=20)
            if st.status_code != 200:
                continue
            data = st.json() or {}
            # В разных ответах бывает "done" или "finished"
            if data.get("done") is True or data.get("finished") is True:
                gens = data.get("generations") or []
                if gens:
                    img_bytes = _decode_horde_image(gens[0].get("img", ""))
                    if img_bytes:
                        return img_bytes
                break
        logger.warning("[imagegen] Horde timeout or no generations")
    except Exception as e:
        logger.warning("[imagegen] Horde failed: %s", e)
    return None

# ---------------- Public API ----------------

def generate_astro_image(moon_phase: str, moon_sign: str, date_str: Optional[str] = None) -> Optional[str]:
    """
    Возвращает путь к сохранённому изображению (str) либо None.
    Приоритет: Stable Horde → Pollinations.
    """
    date_str = date_str or datetime.utcnow().strftime("%Y-%m-%d")
    prompt, negative = _make_prompt(moon_phase, moon_sign)

    # 1) Stable Horde
    img = _try_stable_horde(prompt, negative)
    if not img:
        # 2) Pollinations (быстро и без ключей)
        img = _try_pollinations(prompt)

    if img:
        return _save_image_bytes(img, date_str)

    logger.error("[imagegen] All image generation methods failed.")
    return None
