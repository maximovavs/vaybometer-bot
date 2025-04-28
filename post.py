import os
import asyncio
from telegram import Bot
from openai import OpenAI


PROMPT_TEMPLATE = """
Составь лаконичный ежедневный дайджест для жителей Лимассола (Кипр) на сегодня.
Используй СТРОГО ЭТУ HTML-структуру:

<b>☀️ Погода</b><br>
Температура: …<br>
Облачность: …<br>
Осадки: …<br>
Ветер: …<br><br>

<b>🌬️ Качество воздуха</b><br>
Индекс качества воздуха (AQI): …<br>
PM2.5: …<br>
PM10: …<br>
Комментарий: …<br><br>

<b>🌿 Уровень пыльцы</b><br>
Деревья: …<br>
Травы: …<br>
Амброзия: …<br>
Комментарий: …<br><br>

<b>🌌 Геомагнитная активность</b><br>
Уровень: …<br>
Комментарий: …<br><br>

<b>📈 Резонанс Шумана</b><br>
Фоновая частота: …<br>
Амплитуда: …<br><br>

<b>🌊 Температура воды в море</b><br>
Сейчас: …<br>
Комментарий: …<br><br>

<b>🔮 Астрологические события</b><br>
Событие 1: …<br>
Событие 2: …<br><br>
<hr>

<b>✅ Рекомендации</b><br>
– …<br>
– …<br>
– …<br>

Заканчивай мотивирующей фразой «Будьте на волне и слушайте своё тело!».
Никаких лишних тегов, только те, что в шаблоне. Данные заполни актуальными
числами, где уместно — диапазон, где нет данных — пиши «нет данных».
"""

def fetch_digest() -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": PROMPT_TEMPLATE}],
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


async def post_to_telegram(text: str) -> None:
    bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(
        chat_id=os.environ["CHANNEL_ID"],
        text=text,
        parse_mode="HTML",                 # <<< ключевой момент
        disable_web_page_preview=True,
    )


async def main() -> None:
    digest = fetch_digest()
    await post_to_telegram(digest)


if __name__ == "__main__":
    asyncio.run(main())
