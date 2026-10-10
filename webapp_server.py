"""Локальный веб-сервер для Telegram Web App «Интернетометр».

Отдаёт статику приложения (папка webapp/). Telegram требует, чтобы веб-приложение
открывалось по HTTPS: наружу сервер выставляется через бесплатный туннель, например:

    cloudflared tunnel --url http://localhost:8787

Полученный адрес https://...trycloudflare.com подставляется в WEBAPP_URL (.env).

Сервер можно запускать:
* отдельно:  .venv\\Scripts\\python.exe webapp_server.py  (порт из WEBAPP_PORT или 8787)
* вместе с ботом: bot.py сам поднимает его, если задан WEBAPP_URL
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from aiohttp import web

logger = logging.getLogger(__name__)

WEBAPP_DIR = Path(__file__).resolve().parent / "webapp"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8787


def create_app(webapp_dir: Path = WEBAPP_DIR) -> web.Application:
    app = web.Application()
    # Статика: /static/style.css, /static/app.js — физически лежат в webapp/static/
    app.router.add_static("/static", webapp_dir / "static", show_index=False)
    # Все остальные пути, кроме статики, отдают index.html (SPA-заглушка).
    app.router.add_get("/", lambda _request: web.FileResponse(webapp_dir / "index.html"))
    app.router.add_get("/index.html", lambda _request: web.FileResponse(webapp_dir / "index.html"))
    app.router.add_get("/health", lambda _request: web.Response(text="ok"))
    return app


async def run(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT) -> None:
    """Запускает сервер и ждёт остановки (для запуска задачей из bot.py)."""
    app = create_app()
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info("Web App сервер запущен: http://%s:%d", host, port)
    try:
        await asyncio.Event().wait()  # работаем, пока не отменят
    finally:
        await runner.cleanup()


async def _main() -> None:
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)-8s | %(name)s: %(message)s",
    )
    host = os.getenv("WEBAPP_HOST", DEFAULT_HOST)
    try:
        port = int(os.getenv("WEBAPP_PORT", str(DEFAULT_PORT)))
    except ValueError:
        port = DEFAULT_PORT
    await run(host, port)


if __name__ == "__main__":
    asyncio.run(_main())