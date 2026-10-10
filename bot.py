"""Точка входа бота-интернетометра."""
from __future__ import annotations

import asyncio
import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

import handlers
import webapp_server
from config import load_settings
from database import Database
from services.monitor import MonitorService

logger = logging.getLogger(__name__)


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
    )

    settings = load_settings()

    db = Database(settings.db_path)
    await db.connect()
    logger.info("База данных: %s", settings.db_path)

    bot = Bot(
        token=settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp["settings"] = settings
    dp["db"] = db
    dp.include_router(handlers.router)

    monitor = MonitorService(bot, db)
    monitor.start()

    # Локальный сервер Telegram Web App (если задан публичный WEBAPP_URL).
    webapp_task: asyncio.Task | None = None
    if settings.webapp_url:
        webapp_task = asyncio.create_task(
            webapp_server.run(settings.webapp_host, settings.webapp_port)
        )

    try:
        await bot.delete_webhook(drop_pending_updates=True)
        me = await bot.get_me()
        logger.info("Бот запущен: @%s (%s)", me.username, settings.webapp_url or "без веб-приложения")
        await dp.start_polling(bot)
    finally:
        if webapp_task is not None:
            webapp_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await webapp_task
        await monitor.stop()
        await db.close()
        await bot.session.close()


if __name__ == "__main__":
    with contextlib.suppress(KeyboardInterrupt, SystemExit):
        asyncio.run(main())
