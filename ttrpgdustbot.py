# bot.py
# Khởi động bot Discord, kết nối Google Sheets, load cogs

import discord
from discord.ext import commands
import asyncio
import os
import sys

from core.gsheet_client import GoogleSheetsClient
from core.initializer import init_system
from config import SPREADSHEET_ID
from utils.helpers import setup_logger, get_logger

# Khởi tạo logger
setup_logger()
logger = get_logger()

# Cấu hình bot
TOKEN = os.getenv("DISCORD_BOT_TOKEN")
if not TOKEN:
    logger.error("Thiếu biến môi trường DISCORD_BOT_TOKEN")
    sys.exit(1)

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
if not os.path.exists(CREDENTIALS_FILE):
    logger.error(f"Không tìm thấy file credentials: {CREDENTIALS_FILE}")
    sys.exit(1)


class TTRPGBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.members = True
        super().__init__(command_prefix="!", intents=intents)
        self.gsheet_client = None

    async def setup_hook(self):
        logger.info("Đang khởi tạo Google Sheets client...")
        self.gsheet_client = GoogleSheetsClient(CREDENTIALS_FILE)
        await self.gsheet_client.initialize()

        logger.info("Đang khởi tạo hệ thống (kiểm tra sheet)...")
        await init_system(self.gsheet_client)

        logger.info("Đang load cogs...")
        from cogs.commands import setup as setup_commands
        await setup_commands(self, self.gsheet_client)

        # Đồng bộ slash commands
        await self.tree.sync()
        logger.info("Đã đồng bộ slash commands")

    async def on_ready(self):
        logger.info(f"Bot đã sẵn sàng! Đăng nhập với tên: {self.user.name}")
        await self.change_presence(activity=discord.Game(name="/hub | /combat"))


def main():
    bot = TTRPGBot()
    try:
        bot.run(TOKEN)
    except KeyboardInterrupt:
        logger.info("Bot đã dừng bởi người dùng")
    except Exception as e:
        logger.error(f"Lỗi khi chạy bot: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()