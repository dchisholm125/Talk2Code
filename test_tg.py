import os
import unittest

from telegram import Bot
from telegram.constants import ParseMode

_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
_CHAT_ID = os.getenv("ALLOWED_USER_ID")


class TelegramSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_send_message_if_configured(self) -> None:
        if not _TOKEN or not _CHAT_ID:
            self.skipTest(
                "Telegram credentials not configured (set TELEGRAM_BOT_TOKEN and ALLOWED_USER_ID)."
            )

        bot = Bot(token=_TOKEN)
        response = await bot.send_message(
            chat_id=_CHAT_ID,
            text="~ opencode - opencode/minimax-m2.5-free",
            parse_mode=ParseMode.MARKDOWN,
        )
        self.assertIsNotNone(response.message_id)
