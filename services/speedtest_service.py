"""Замер скорости интернета через публичные эндпоинты Cloudflare.

Особенности:
* прогрев соединения перед замером (убирает «провал» на старте TLS);
* замер идёт потоково в течение нескольких секунд, а не одним маленьким пакетом;
* размер теста отдачи подбирается под измеренную скорость загрузки.

Не требует внешних бинарников и работает асинхронно.
"""
from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

import aiohttp

from .netcheck import PingStats, tcp_ping

DEFAULT_PING_HOST = "1.1.1.1"
DEFAULT_PING_PORT = 443
CLOUDFLARE_DOWN = "https://speed.cloudflare.com/__down"
CLOUDFLARE_UP = "https://speed.cloudflare.com/__up"
CLOUDFLARE_META = "https://speed.cloudflare.com/meta"

# Cloudflare отдаёт /meta только запросам с «человеческим» User-Agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
    ),
    "Referer": "https://speed.cloudflare.com/",
    "Accept": "application/json",
}

_TIMEOUT = aiohttp.ClientTimeout(total=None, connect=15, sock_read=120)
_CHUNK = 262144  # 256 КБ
_WARMUP_BYTES = 1_000_000
_MAX_UPLOAD_BYTES = 160 * 1024 * 1024
_PROBE_BYTES = 4 * 1024 * 1024  # 4 МБ — перекрывает сокетный буфер
_PROBE_SLOW_SECONDS = 3.0

ProgressCallback = Callable[[str], Awaitable[None]]
ChunkCallback = Callable[[int, float], Awaitable[None]]


@dataclass
class SpeedTestResult:
    ping: Optional[PingStats] = None
    download_mbps: Optional[float] = None
    upload_mbps: Optional[float] = None
    downloaded_bytes: int = 0
    uploaded_bytes: int = 0
    download_seconds: float = 0.0
    upload_seconds: float = 0.0
    duration: float = 0.0
    meta: dict[str, Any] = field(default_factory=dict)


async def fetch_meta(session: aiohttp.ClientSession) -> dict[str, Any]:
    try:
        async with session.get(CLOUDFLARE_META, timeout=aiohttp.ClientTimeout(total=10)) as resp:
            if resp.status == 200:
                return await resp.json(content_type=None)
    except Exception:  # noqa: BLE001
        pass
    return {}


async def _drain(session: aiohttp.ClientSession, size_bytes: int) -> None:
    """Прогревающий запрос: цель — установить соединение и разогнать канал."""
    with contextlib.suppress(Exception):
        async with session.get(f"{CLOUDFLARE_DOWN}?bytes={size_bytes}", timeout=_TIMEOUT) as resp:
            await resp.read()


async def measure_download(
    session: aiohttp.ClientSession,
    max_bytes: int,
    min_duration: float = 8.0,
    on_chunk: ChunkCallback | None = None,
) -> tuple[float | None, int, float]:
    """Качает данные до max_bytes или min_duration секунд. Возвращает (Мбит/с, байты, сек)."""
    await _drain(session, _WARMUP_BYTES)

    url = f"{CLOUDFLARE_DOWN}?bytes={max_bytes}"
    total = 0
    start = time.perf_counter()
    last_report = start
    failed = False

    try:
        async with session.get(url, timeout=_TIMEOUT) as resp:
            resp.raise_for_status()
            async for chunk in resp.content.iter_chunked(_CHUNK):
                total += len(chunk)
                now = time.perf_counter()
                if on_chunk is not None and now - last_report >= 0.8:
                    last_report = now
                    elapsed = now - start
                    if elapsed > 0:
                        await on_chunk(total, total * 8 / elapsed / 1_000_000)
                if now - start >= min_duration:
                    break
    except Exception:  # noqa: BLE001
        failed = total == 0

    elapsed = time.perf_counter() - start
    if failed or total == 0 or elapsed <= 0:
        return None, total, elapsed
    return total * 8 / elapsed / 1_000_000, total, elapsed


async def _post_upload(
    session: aiohttp.ClientSession, size_bytes: int
) -> tuple[float | None, int, float]:
    payload = b"\x00" * size_bytes
    start = time.perf_counter()
    completed = False
    try:
        async with session.post(
            CLOUDFLARE_UP,
            data=payload,
            headers={"Content-Type": "application/octet-stream"},
            timeout=_TIMEOUT,
        ) as resp:
            await resp.read()
            completed = True
    except Exception:  # noqa: BLE001
        completed = False

    elapsed = time.perf_counter() - start
    if not completed or elapsed <= 0:
        return None, 0, elapsed
    return size_bytes * 8 / elapsed / 1_000_000, size_bytes, elapsed


async def measure_upload(
    session: aiohttp.ClientSession,
    target_seconds: float = 8.0,
) -> tuple[float | None, int, float]:
    """Замер отдачи: сначала пробник 4 МБ (перекрывает сокетный буфер), затем
    добор объёма до нужного времени, если канал быстрый.

    Пробник обязателен: на 0.5 МБ скорость завышается в разы из-за буферизации.
    """
    probe_mbps, probe_bytes, probe_time = await _post_upload(session, _PROBE_BYTES)
    if probe_mbps is None:
        return None, 0, probe_time

    # Если даже 4 МБ качались долго — выборки уже достаточно, не удлиняем тест.
    if probe_time >= _PROBE_SLOW_SECONDS:
        return probe_mbps, probe_bytes, probe_time

    bytes_per_second = probe_mbps * 1_000_000 / 8
    main_bytes = int(bytes_per_second * target_seconds)
    main_bytes = max(1 * 1024 * 1024, min(main_bytes, _MAX_UPLOAD_BYTES))

    main_mbps, main_sent, main_time = await _post_upload(session, main_bytes)
    if main_mbps is None:
        return probe_mbps, probe_bytes, probe_time
    return max(probe_mbps, main_mbps), probe_bytes + main_sent, probe_time + main_time


async def run_speed_test(
    max_download_mb: int = 200,
    target_seconds: float = 8.0,
    progress: ProgressCallback | None = None,
    ping_host: str = DEFAULT_PING_HOST,
    ping_port: int = DEFAULT_PING_PORT,
) -> SpeedTestResult:
    """Полный замер. ``max_download_mb`` — верхняя граница объёма (не менее 100 МБ)."""
    # Не даём тесту быть слишком коротким: минимум 100 МБ сверху и target_seconds по времени.
    cap_bytes = max(100, max_download_mb) * 1024 * 1024

    result = SpeedTestResult()
    started = time.perf_counter()

    async with aiohttp.ClientSession(headers=HEADERS) as session:
        if progress:
            await progress("🏓 Замеряю задержку (ping)...")
        result.ping = await tcp_ping(ping_host, ping_port, attempts=5)

        if progress:
            await progress("⬇️ Замеряю скорость загрузки (несколько секунд)...")

        async def on_download(total: int, mbps: float) -> None:
            if progress:
                await progress(
                    f"⬇️ Загрузка: <b>{mbps:.1f} Мбит/с</b> "
                    f"({total / 1_048_576:.0f} МБ, продолжается...)"
                )

        result.download_mbps, result.downloaded_bytes, result.download_seconds = (
            await measure_download(session, cap_bytes, target_seconds, on_download)
        )

        # Размер теста отдачи подбирается адаптивно внутри measure_upload.
        if progress:
            await progress("⬆️ Замеряю скорость отдачи...")
        result.upload_mbps, result.uploaded_bytes, result.upload_seconds = await measure_upload(
            session, target_seconds
        )

        result.meta = await fetch_meta(session)

    result.duration = time.perf_counter() - started
    return result
