"""Конфигурация бота: читается из переменных окружения и файла .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent

# Загружаем .env рядом с проектом (если файла нет — просто идём дальше).
load_dotenv(BASE_DIR / ".env")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


@dataclass(frozen=True)
class Settings:
    bot_token: str
    admin_ids: tuple[int, ...]
    db_path: Path
    default_speedtest_mb: int
    max_speedtest_mb: int
    speedtest_seconds: int
    max_monitors_per_user: int
    max_download_mb: int
    webapp_url: str
    webapp_host: str
    webapp_port: int


def load_settings() -> Settings:
    token = os.getenv("BOT_TOKEN", "").strip()
    if not token:
        raise RuntimeError(
            "Не задан BOT_TOKEN.\n"
            "Скопируйте .env.example в .env и вставьте токен от @BotFather."
        )

    admin_ids: list[int] = []
    for chunk in os.getenv("ADMIN_IDS", "").replace(";", ",").split(","):
        chunk = chunk.strip()
        if chunk.isdigit():
            admin_ids.append(int(chunk))

    db_path = Path(os.getenv("DB_PATH", str(BASE_DIR / "internetometer.db")))

    # Публичный HTTPS-адрес Telegram Web App (например, туннель cloudflared).
    # Пустое значение — кнопка приложения и сервер не включаются.
    webapp_url = os.getenv("WEBAPP_URL", "").strip().rstrip("/")
    webapp_host = os.getenv("WEBAPP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        webapp_port = int(os.getenv("WEBAPP_PORT", "8787"))
    except ValueError:
        webapp_port = 8787

    return Settings(
        bot_token=token,
        admin_ids=tuple(admin_ids),
        db_path=db_path,
        default_speedtest_mb=_env_int("DEFAULT_SPEEDTEST_MB", 200),
        max_speedtest_mb=_env_int("MAX_SPEEDTEST_MB", 1000),
        speedtest_seconds=_env_int("SPEEDTEST_SECONDS", 8),
        max_monitors_per_user=_env_int("MAX_MONITORS_PER_USER", 10),
        max_download_mb=_env_int("MAX_DOWNLOAD_MB", 25),
        webapp_url=webapp_url,
        webapp_host=webapp_host,
        webapp_port=webapp_port,
    )
