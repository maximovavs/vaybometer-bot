import os
import asyncio
from telegram import Bot
from openai import OpenAI


PROMPT = """
Составь лаконичный ежедневный дайджест для жителей Лимассола (Кипр) на сегодня
в формате HTML Telegram:

<b>☀️ Погода</b>
Температура: …
Облачность: …
Осадки: …
Ветер: …

<b>🌬️ Качество воздуха</b>
Индекс AQI: …
PM2.5: …
PM10: …
Комментарий: …

<b>🌿 Уровень пыльцы</b>
Деревья: …
Травы: …
Амброзия: …
Комментарий: …

<b>🌌 Геомагнитная активность</b>
Уровень: …
Комментарий: …

<b>📈 Резонанс Шумана</b>
Фоновая частота: …
Амплитуда: …

<b>🌊 Температура воды в море</b>
Сейчас: …
Комментарий: …

<b>🔮 Астрологические события</b>
Событие 1: …
Событие 2: …

<b>✅ Рекомендации</b>
– …
– …
– …

Заканчивай фразой «Будьте на волне и слушайте своё тело!».

❗ Используй только тег <b>; для новых строк ставь \\n; никаких <br>, <hr> и прочего.
"""

def fetch_digest() -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": PROMPT}],
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()

async def post_to_telegram(text: str) -> None:
    bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(
        chat_id=os.environ["CHANNEL_ID"],
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

async def main() -> None:
    digest = fetch_digest()
    await post_to_telegram(digest)

if __name__ == "__main__":
    asyncio.run(main())
