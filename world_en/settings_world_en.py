import os

TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID_EN", "")

# Города для "hottest/coldest" (координаты достаточно точные)
HOT_CITIES = [
    ("Jazan, SA", 16.889, 42.570),
    ("Doha, QA", 25.285, 51.531),
    ("Kuwait City, KW", 29.375, 47.977),
    ("Phoenix, US", 33.448, -112.074),
]
COLD_SPOTS = [
    ("Dome A, AQ", -80.370, 77.350),
    ("Vostok, AQ", -78.465, 106.835),
    ("Summit Camp, GL", 72.579, -38.459),
    ("Oymyakon, RU", 63.463, 142.773),
]

# Города для рассвет/закат
SUN_CITIES = [
    ("Reykjavik, IS", 64.1466, -21.9426),
    ("Ushuaia, AR", -54.8019, -68.3030),
    ("Tokyo, JP", 35.6762, 139.6503),
    ("New York, US", 40.7128, -74.0060),
    ("Singapore, SG", 1.3521, 103.8198),
]

# Список коротких советов (без GPT, чтобы не зависеть от API)
VIBE_TIPS = [
    "Sip water and take 10 slow breaths.",
    "7-minute walk between tasks.",
    "Face daylight for 2 minutes.",
    "Stretch neck & shoulders for 60 seconds.",
    "Write one line of gratitude.",
]

# YouTube и фолбэки
YT_API_KEY = os.getenv("YT_API_KEY")
YT_CHANNEL_ID = "UC14f77rQoWM1-1dLGhdGAQ"  # твой канал @misserrelax
YOUTUBE_PLAYLIST_IDS = [s.strip() for s in os.getenv("YOUTUBE_PLAYLIST_IDS","").split(",") if s.strip()]
FALLBACK_NATURE_LIST = [s.strip() for s in os.getenv("FALLBACK_NATURE_LIST","").split(",") if s.strip()]
