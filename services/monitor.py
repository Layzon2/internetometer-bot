"""Фоновый мониторинг доступности URL с уведомлениями об изменениях."""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError

from .netcheck import check_url

logger = logging.getLogger(__name__)


class MonitorService:
    def __init__(self, bot: Bot, db, poll_interval: int = 15, concurrency: int = 5) -> None:
        self.bot = bot
        self.db = db
        self.poll_interval = poll_interval
        self._sem = asyncio.Semaphore(concurrency)
        self._task: asyncio.Task | None = None
        self._running = False

    def start(self) -> None:
        self._running = True
        self._task = asyncio.create_task(self._run(), name="monitor-service")

    async def stop(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._task = None

    async def _run(self) -> None:
        logger.info("Служба мониторинга запущена (интервал опроса %s c)", self.poll_interval)
        while self._running:
            try:
                due = await self.db.get_due_monitors()
                if due:
                    await asyncio.gather(*(self._check(m) for m in due))
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                logger.exception("Ошибка в цикле мониторинга")
            try:
                await asyncio.sleep(self.poll_interval)
            except asyncio.CancelledError:
                raise

    async def _check(self, monitor: dict[str, Any]) -> None:
        async with self._sem:
            result = await check_url(monitor["url"])
            now = int(time.time())
            await self.db.update_monitor_status(
                monitor["id"], result.ok, result.status, result.error, now
            )

            previous_ok = monitor["last_ok"]
            # Первую проверку не превращаем в уведомление.
            if previous_ok is None:
                return
            if bool(previous_ok) == result.ok:
                return

            await self._notify(monitor, result.ok, result.status, result.latency_ms, result.error)

    async def _notify(
        self,
        monitor: dict[str, Any],
        ok: bool,
        status: int | None,
        latency_ms: float | None,
        error: str | None,
    ) -> None:
        url = monitor["url"]
        if ok:
            text = (
                "✅ <b>Ресурс снова доступен</b>\n"
                f"🔗 {url}\n"
                + (f"📶 HTTP {status}" + (f", {latency_ms:.0f} мс" if latency_ms else "") + "\n")
                + f"🆔 Монитор #{monitor['id']}"
            )
        else:
            reason = f"HTTP {status}" if status else (error or "нет ответа")
            text = (
                "⛔️ <b>Ресурс недоступен</b>\n"
                f"🔗 {url}\n"
                f"❗️ Причина: {reason}\n"
                f"🆔 Монитор #{monitor['id']}"
            )
        try:
            await self.bot.send_message(monitor["chat_id"], text)
        except TelegramAPIError as exc:
            logger.warning("Не удалось отправить уведомление (монитор #%s): %s", monitor["id"], exc)
        except Exception:  # noqa: BLE001
            logger.exception("Неожиданная ошибка отправки уведомления")
