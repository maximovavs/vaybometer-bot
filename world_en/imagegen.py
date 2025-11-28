import os
import time
import requests
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

# Для сохранения изображений
ASTRO_IMG_DIR = os.path.join(os.path.dirname(__file__), "..", "astro_img")
os.makedirs(ASTRO_IMG_DIR, exist_ok=True)

def _save_image(response_content, date_str):
    filename = f"astro_{date_str}.jpg"
    path = os.path.join(ASTRO_IMG_DIR, filename)
    with open(path, "wb") as f:
        f.write(response_content)
    return path

def _make_prompt(moon_phase, moon_sign):
    return (
        f"A {moon_phase} moon in the sky of {moon_sign}, atmospheric, dreamy, high detail, 4K, soft colors"
    ), "ugly, blurry, distorted, text"

def _try_pollinations(prompt):
    try:
        url = f"https://pollinations.ai/prompt/{requests.utils.quote(prompt)}"
        resp = requests.get(url, timeout=20)
        if resp.status_code == 200:
            return resp.content
    except Exception as e:
        logger.warning(f"Pollinations failed: {e}")
    return None

def _try_stable_horde(prompt, negative_prompt):
    try:
        payload = {
            "prompt": prompt,
            "params": {
                "n": 1,
                "width": 512,
                "height": 512,
                "negative_prompt": negative_prompt
            },
            "models": ["stable_diffusion"]
        }
        headers = {
            "Content-Type": "application/json"
        }
        resp = requests.post("https://stablehorde.net/api/v2/generate/async", 
                             json=payload, headers=headers, timeout=30)
        if resp.status_code == 202:
            uuid = resp.json().get("id")
            # опрос статуса
            for _ in range(60):
                time.sleep(2)
                check = requests.get(f"https://stablehorde.net/api/v2/generate/status/{uuid}")
                if check.status_code == 200:
                    data = check.json()
                    if data.get("done") and data.get("generations"):
                        gen = data["generations"][0]
                        image_resp = requests.get(gen["img"])
                        if image_resp.status_code == 200:
                            return image_resp.content
        else:
            logger.warning(f"Stable Horde API response {resp.status_code}: {resp.text}")
    except Exception as e:
        logger.warning(f"Stable Horde failed: {e}")
    return None

def generate_astro_image(moon_phase, moon_sign, date_str=None):
    """
    Генерация изображения на основе астрологических данных.
    Возвращает путь к изображению или None.
    """
    if date_str is None:
        date_str = datetime.utcnow().strftime("%Y-%m-%d")

    prompt, negative_prompt = _make_prompt(moon_phase, moon_sign)

    # 1. Попытка через Stable Horde
    image_bytes = _try_stable_horde(prompt, negative_prompt)
    if not image_bytes:
        # 2. Попытка через Pollinations
        image_bytes = _try_pollinations(prompt)

    if image_bytes:
        return _save_image(image_bytes, date_str)

    logger.error("All image generation methods failed.")
    return None
