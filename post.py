# post.py
import os
import asyncio
from telegram import Bot
from openai import OpenAI


def fetch_digest() -> str:
    """Берём короткий «дайджест вайба» у ChatGPT."""
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    prompt = (
        "Скажи мне, что может повлиять на моё самочувствие и настроение "
        "сегодня в Лимассоле, включая погоду, качество воздуха, геомагнитную "
        "активность, резонанс Шумана, температуру воды в море и астрособытия."
    )

    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.7,
    )
    return resp.choices[0].message.content.strip()


async def post_to_telegram(text: str) -> None:
    """Отправляем текст в Telegram-канал."""
    bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(
        chat_id=os.environ["CHANNEL_ID"],
        text=text,
        parse_mode="Markdown",
        disable_web_page_preview=True,
    )


async def main() -> None:
    digest = fetch_digest()
    await post_to_telegram(digest)


if __name__ == "__main__":
    asyncio.run(main())
