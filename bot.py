"""SplitEvenlyBot entry point. Polling by default; webhooks when WEBHOOK_URL is set."""

import asyncio
import logging
import os
import sys

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramUnauthorizedError
from aiogram.fsm.storage.base import BaseEventIsolation
from aiogram.fsm.storage.memory import SimpleEventIsolation
from dotenv import load_dotenv

from splitbot.handlers import groups, private
from splitbot.reader import BillReader
from splitbot.storage import SQLiteStorage


def env(name: str, default: str | None = None) -> str:
    value = os.getenv(name, default)
    if not value:
        sys.exit(f"Missing {name}. Copy .env.example to .env and fill it in.")
    return value


def build() -> tuple[Bot, Dispatcher]:
    load_dotenv()
    bot = Bot(env("TELEGRAM_BOT_TOKEN"), default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    isolation: BaseEventIsolation = SimpleEventIsolation()  # one update at a time per user
    dp = Dispatcher(storage=SQLiteStorage(env("DB_PATH", "data/splitbot.db")), events_isolation=isolation)
    dp["reader"] = BillReader(env("ANTHROPIC_API_KEY"), env("CLAUDE_MODEL", "claude-opus-5-5"))
    dp.include_routers(groups, private)
    return bot, dp


async def run_polling() -> None:
    bot, dp = build()
    try:
        await bot.delete_webhook(drop_pending_updates=False)
    except TelegramUnauthorizedError:
        await bot.session.close()
        sys.exit("Telegram rejected TELEGRAM_BOT_TOKEN. Copy the current token from @BotFather "
                 "(/mybots → your bot → API Token) into .env.")
    await dp.start_polling(bot, allowed_updates=dp.resolve_used_update_types())


def run_webhook() -> None:
    from aiohttp import web
    from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

    bot, dp = build()
    url = env("WEBHOOK_URL").rstrip("/") + "/webhook"
    secret = os.getenv("WEBHOOK_SECRET") or None

    async def on_startup(bot: Bot) -> None:
        await bot.set_webhook(url, secret_token=secret, allowed_updates=dp.resolve_used_update_types())

    dp.startup.register(on_startup)
    app = web.Application()
    SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=secret).register(app, path="/webhook")
    setup_application(app, dp, bot=bot)
    web.run_app(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")))


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    load_dotenv()
    if os.getenv("WEBHOOK_URL"):
        run_webhook()
    else:
        asyncio.run(run_polling())
