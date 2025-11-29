import os
import time
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

# Абсолютный путь к каталогу astro_img в корне репозитория
ASTRO_IMG_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "astro_img"))
os.makedirs(ASTRO_IMG_DIR, exist_ok=True)

def _save_image(response_content, date_str):
    filename = f"astro_{date_str}.jpg"
    path = os.path.join(ASTRO_IMG_DIR, filename)
    with open(path, "wb") as f:
        f.write(response_content)
    logger.info("[imagegen] saved %s (%d bytes)", path, len(response_content))
    return path

def _make_prompt(moon_phase, moon_sign):
    base = (
        f"A {moon_phase} moon in the sky of {moon_sign}, "
        f"atmospheric, dreamy, high detail, soft light, 4K, no text"
    )
    negative = "ugly, blurry, distorted, text, watermark, low quality"
    return base, negative

def _try_pollinations(prompt):
    """
    Быстрый бесплатный рендер без ключей.
    Важно: используем image.pollinations.ai, который отдаёт сразу картинку.
    """
    try:
        url = f"https://image.pollinations.ai/prompt/{requests.utils.quote(prompt)}?width=1024&height=576"
        resp = requests.get(url, timeout=25, headers={"User-Agent": "curl/8"})
        if resp.status_code == 200 and resp.headers.get("content-type","").startswith("image/"):
            return resp.content
        logger.warning("Pollinations non-200 or non-image: %s %s", resp.status_code, resp.headers.get("content-type"))
    except Exception as e:
        logger.warning("Pollinations failed: %s", e)
    return None

def _try_stable_horde(prompt, negative_prompt):
    """
    Анонимно через Stable Horde (медленнее, но тоже бесплатно).
    Можно ускорить при наличии STABLE_HORDE_API_KEY.
    """
    try:
        payload = {
            "prompt": prompt,
            "params": {
                "n": 1,
                "width": 768,
                "height": 432,
                "steps": 25,
                "sampler_name": "k_euler",
                "cfg_scale": 7.0,
                "seed": None,
                "karras": True,
                "denoising_strength": None,
                "post_processing": [],
                "tiling": False,
                "hires_fix": False,
                "clip_skip": 1,
                "image_is_control": False,
                "return_control_map": False,
                "negative_prompt": negative_prompt
            },
            "models": ["stable_diffusion"],
            "nsfw": False
        }
        headers = {"Content-Type": "application/json"}
        api_key = os.getenv("STABLE_HORDE_API_KEY", "").strip()
        if api_key:
            headers["apikey"] = api_key

        submit = requests.post("https://stablehorde.net/api/v2/generate/async",
                               json=payload, headers=headers, timeout=30)
        if submit.status_code != 202:
            logger.warning("Stable Horde submit %s: %s", submit.status_code, submit.text[:200])
            return None

        req_id = submit.json().get("id")
        if not req_id:
            return None

        # Пуллим статус до 120 сек
        for _ in range(60):
            time.sleep(2)
            st = requests.get(f"https://stablehorde.net/api/v2/generate/status/{req_id}", timeout=15)
            if st.status_code != 200:
                continue
            data = st.json()
            if data.get("done") and data.get("generations"):
                gen = data["generations"][0]
                img_url = gen.get("img")
                if img_url:
                    img = requests.get(img_url, timeout=30)
                    if img.status_code == 200 and img.headers.get("content-type","").startswith("image/"):
                        return img.content
        logger.warning("Stable Horde timeout waiting for result")
    except Exception as e:
        logger.warning("Stable Horde failed: %s", e)
    return None

def generate_astro_image(moon_phase, moon_sign, date_str=None):
    """
    Генерирует изображение для астропоста.
    Возвращает абсолютный путь к файлу или None.
    """
    if date_str is None:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")

    prompt, negative_prompt = _make_prompt(moon_phase, moon_sign)

    # 1) быстрый Pollinations
    img = _try_pollinations(prompt)
    if not img:
        # 2) запасной Stable Horde
        img = _try_stable_horde(prompt, negative_prompt)

    if img:
        return _save_image(img, date_str)

    logger.error("All image generation methods failed.")
    return None
