import os, re, asyncio
from telegram import Bot
from openai import OpenAI


PROMPT = """
Составь лаконичный ежедневный дайджест для жителей Лимассола на сегодня.
Разметка — только тег <b> для заголовков, никаких ``` и \\n.
Каждый новый абзац начинай с реального переноса строки.
Заканчивай фразой: Будьте на волне и слушайте своё тело!
"""

def fetch_digest() -> str:
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": PROMPT}],
        temperature=0.7,
    )
    text = resp.choices[0].message.content.strip()

    # 1️⃣  убираем возможный code-fence ```...```
    if text.startswith("```"):
        text = re.sub(r"^```[^\n]*\n|\n```$", "", text, flags=re.S)

    # 2️⃣  заменяем литералы \n на реальные переводы строк
    text = text.replace("\\n", "\n")

    return text

async def post_to_telegram(text: str) -> None:
    bot = Bot(token=os.environ["TELEGRAM_TOKEN"])
    await bot.send_message(
        chat_id=os.environ["CHANNEL_ID"],
        text=text,
        parse_mode="HTML",            #  <b> останется жирным
        disable_web_page_preview=True,
    )

async def main():
    digest = fetch_digest()
    await post_to_telegram(digest)

if __name__ == "__main__":
    asyncio.run(main())
