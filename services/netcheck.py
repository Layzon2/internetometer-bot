"""Сетевые проверки: TCP-пинг и проверка доступности URL."""
from __future__ import annotations

import asyncio
import statistics
import time
from dataclasses import dataclass


@dataclass
class PingStats:
    min_ms: float
    avg_ms: float
    max_ms: float
    jitter_ms: float
    loss_percent: float
    samples: int


async def tcp_ping(
    host: str,
    port: int = 443,
    attempts: int = 5,
    timeout: float = 5.0,
) -> PingStats | None:
    """Измеряет задержку через TCP-подключение (аналог ping, но по TCP)."""
    latencies: list[float] = []
    for i in range(attempts):
        start = time.perf_counter()
        writer = None
        try:
            _reader, writer = await asyncio.wait_for(
                asyncio.open_connection(host, port), timeout=timeout
            )
            latencies.append((time.perf_counter() - start) * 1000.0)
        except Exception:
            pass
        finally:
            if writer is not None:
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
        if i < attempts - 1:
            await asyncio.sleep(0.1)

    if not latencies:
        return None

    return PingStats(
        min_ms=min(latencies),
        avg_ms=sum(latencies) / len(latencies),
        max_ms=max(latencies),
        jitter_ms=statistics.pstdev(latencies) if len(latencies) > 1 else 0.0,
        loss_percent=(attempts - len(latencies)) / attempts * 100.0,
        samples=len(latencies),
    )


@dataclass
class UrlCheck:
    ok: bool
    status: int | None
    latency_ms: float | None
    error: str | None


async def check_url(url: str, timeout: float = 15.0) -> UrlCheck:
    """Проверяет доступность URL. Успех = получен HTTP-ответ."""
    import aiohttp

    client_timeout = aiohttp.ClientTimeout(total=timeout)
    headers = {"User-Agent": "InternetometerBot/1.0 (+https://t.me/)"}
    start = time.perf_counter()
    try:
        async with aiohttp.ClientSession(timeout=client_timeout) as session:
            async with session.get(
                url, headers=headers, allow_redirects=True, ssl=False
            ) as resp:
                # дочитываем тело, чтобы соединение закрылось корректно
                await resp.read()
                latency = (time.perf_counter() - start) * 1000.0
                return UrlCheck(ok=True, status=resp.status, latency_ms=latency, error=None)
    except asyncio.TimeoutError:
        return UrlCheck(ok=False, status=None, latency_ms=None, error="Таймаут")
    except aiohttp.ClientError as exc:
        return UrlCheck(ok=False, status=None, latency_ms=None, error=f"{type(exc).__name__}: {exc}")
    except Exception as exc:  # noqa: BLE001
        return UrlCheck(ok=False, status=None, latency_ms=None, error=f"{type(exc).__name__}: {exc}")


def normalize_url(raw: str) -> str | None:
    """Приводит ввод пользователя к корректному http(s) URL или возвращает None."""
    raw = (raw or "").strip()
    if not raw:
        return None
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    # грубая проверка, что есть точка и нет пробелов
    host_part = raw.split("://", 1)[1].split("/", 1)[0]
    if " " in raw or "." not in host_part:
        return None
    return raw
