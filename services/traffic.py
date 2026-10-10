"""Счётчик трафика: сетевой ввод/вывод хоста через psutil (если доступен)."""
from __future__ import annotations

from dataclasses import dataclass

try:
    import psutil  # type: ignore

    _PSUTIL_OK = True
except Exception:  # pragma: no cover - psutil может отсутствовать
    psutil = None  # type: ignore
    _PSUTIL_OK = False


@dataclass
class HostNetIO:
    bytes_sent: int
    bytes_recv: int


def host_net_io() -> HostNetIO | None:
    """Суммарный сетевой трафик хоста с момента загрузки ОС."""
    if not _PSUTIL_OK:
        return None
    try:
        counters = psutil.net_io_counters()
    except Exception:
        return None
    return HostNetIO(bytes_sent=int(counters.bytes_sent), bytes_recv=int(counters.bytes_recv))


def psutil_available() -> bool:
    return _PSUTIL_OK


def process_net_io() -> HostNetIO | None:
    """Сетевой трафик процесса бота (требует psutil)."""
    if not _PSUTIL_OK:
        return None
    try:
        proc = psutil.Process()
        io = proc.io_counters()
        # read_bytes/write_bytes отражают дисковый, а не сетевой ввод-вывод,
        # поэтому используем только там, где это осмысленно — не подменяем.
        return HostNetIO(bytes_sent=int(io.write_bytes), bytes_recv=int(io.read_bytes))
    except Exception:
        return None
