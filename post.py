import os, re, asyncio
from telegram import Bot
from openai import OpenAI


PROMPT = """
Ты — «VayboМетр», присылай ежедневный вайб-дайджест для жителей Лимассола
СТРОГО в таком виде (используй <b> для заголовков, обычный перенос строки
между строками, НИКАКИХ ``` и \\n, никаких других html-тегов):

<b>☀️ Погода</b>
Температура: … °C
Облачность: …
Осадки: …
Ветер: …

<b>🌬️ Качество воздуха</b>
Индекс AQI: … (…)
PM2.5: … µg/m³
PM10: … µg/m³
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
Сейчас: … °C
Комментарий: …

<b>🔮 Астрологические события</b>
Событие 1: …
Событие 2: …

<b>✅ Рекомендации</b>
– …
– …
– …

Будьте на волне и слушайте своё тело!
"""

def fetch_digest() -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": PROMPT}],
        temperature=0.7,
    )
    text = resp.choices[0].message.content.strip()

    # убираем возможное обрамление в ``` (модель вдруг «решит помочь»)
    text = re.sub(r"^```[^\n]*\n|\n```$", "", text, flags=re.S)
    # подстраховка: заменяем литеральные \n на реальные переводы
    text = text.replace("\\n", "\n")

    return text

async def post_to_telegram(text: str) -> None:
    bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(
        chat_id=os.environ["CHANNEL_ID"],
        text=text,
        parse_mode="HTML",
        disable_web_page_preview=True,
    )

async def main():
    digest = fetch_digest()
    await post_to_telegram(digest)

if __name__ == "__main__":
    asyncio.run(main())
