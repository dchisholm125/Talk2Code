import asyncio
import os
from telegram import Bot
from telegram.constants import ParseMode
async def main():
    bot = Bot(token=os.getenv("TELEGRAM_BOT_TOKEN"))
    try:
        await bot.send_message(chat_id=os.getenv("ALLOWED_USER_ID"), text="~ opencode - opencode/minimax-m2.5-free", parse_mode=ParseMode.MARKDOWN)
        print("SUCCESS")
    except Exception as e:
        print("ERROR:", e)

asyncio.run(main())
