"""Преобразование единиц: размеры, скорости и время скачивания."""
from __future__ import annotations

import re

# ------------------------------- размеры ------------------------------------

_SIZE_UNITS: dict[str, int] = {
    "b": 1, "byte": 1, "bytes": 1, "б": 1, "байт": 1, "байта": 1, "байтов": 1,
    "kb": 1024, "кб": 1024, "kbyte": 1024, "kbytes": 1024, "килобайт": 1024,
    "mb": 1024 ** 2, "мб": 1024 ** 2, "mbyte": 1024 ** 2, "mbytes": 1024 ** 2,
    "мегабайт": 1024 ** 2,
    "gb": 1024 ** 3, "гб": 1024 ** 3, "gbyte": 1024 ** 3, "гигабайт": 1024 ** 3,
    "tb": 1024 ** 4, "тб": 1024 ** 4, "tbyte": 1024 ** 4, "терабайт": 1024 ** 4,
    "pb": 1024 ** 5, "пб": 1024 ** 5, "петабайт": 1024 ** 5,
}

_NUM = r"(\d+(?:[.,]\d+)?)"


def _to_float(raw: str) -> float:
    return float(raw.replace(",", "."))


def human_bytes(size: int | float) -> str:
    size = float(size)
    for unit in ("Б", "КБ", "МБ", "ГБ", "ТБ"):
        if abs(size) < 1024.0 or unit == "ТБ":
            if unit == "Б":
                return f"{int(size)} {unit}"
            return f"{size:.2f} {unit}"
        size /= 1024.0
    return f"{size:.2f} ТБ"


def parse_size(token: str) -> int | None:
    """Разбирает «1.5gb», «700 MB», «512кб». Без единицы — считаем МБ."""
    token = token.strip().lower().replace(" ", "")
    if not token:
        return None
    match = re.fullmatch(_NUM + r"([a-zа-яё/]+)?", token)
    if not match:
        return None
    value = _to_float(match.group(1))
    unit = (match.group(2) or "mb").rstrip(".")
    multiplier = _SIZE_UNITS.get(unit)
    if multiplier is None:
        return None
    return int(value * multiplier)


# ------------------------------- скорости -----------------------------------

# Сколько мегабит в секунду даёт единица (множитель к числу).
_SPEED_UNITS: dict[str, float] = {
    "": 1.0,
    "mbit": 1.0, "mbit/s": 1.0, "mbps": 1.0, "mbitps": 1.0,
    "мбит": 1.0, "мбит/с": 1.0,
    "mbyte/s": 8.0, "mbyte": 8.0, "mb/s": 8.0, "mbytes/s": 8.0,
    "мбайт/с": 8.0, "мб/с": 8.0, "мегабайт/с": 8.0,
    "gbit/s": 1000.0, "gbps": 1000.0, "гбит": 1000.0, "гбит/с": 1000.0,
    "gbyte/s": 8000.0, "gb/s": 8000.0, "гбайт/с": 8000.0, "гб/с": 8000.0,
    "kbit/s": 0.001, "kbps": 0.001, "кбит": 0.001, "кбит/с": 0.001,
    "kbyte/s": 0.008, "kb/s": 0.008, "кбайт/с": 0.008, "кб/с": 0.008,
}


def parse_speed(token: str) -> float | None:
    """Возвращает скорость в мегабитах/с. «Mbps»/«мбит» = мегабиты, «MB/s» = мегабайты."""
    token = token.strip().lower().replace(" ", "")
    if not token:
        return None
    match = re.fullmatch(_NUM + r"([a-zа-яё/]*)?", token)
    if not match:
        return None
    value = _to_float(match.group(1))
    unit = match.group(2) or ""
    multiplier = _SPEED_UNITS.get(unit)
    if multiplier is None:
        return None
    return value * multiplier


def format_speed(mbps: float, with_bytes: bool = True) -> str:
    """Форматирует скорость, опционально добавляя эквивалент в МБ/с."""
    text = f"{mbps:.1f} Мбит/с"
    if with_bytes:
        text += f" ({mbps / 8:.1f} МБ/с)"
    return text


# --------------------------- время и калькулятор ----------------------------


def format_duration(seconds: float) -> str:
    if seconds < 0:
        seconds = 0
    if seconds < 1:
        return f"{seconds:.2f} с"
    if seconds < 60:
        return f"{seconds:.1f} с"
    total = int(round(seconds))
    minutes, sec = divmod(total, 60)
    if minutes < 60:
        return f"{minutes} мин {sec} с"
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ч {minutes} мин"


def download_seconds(size_bytes: int, mbps: float) -> float | None:
    """Сколько секунд качать файл при заданной скорости (Мбит/с)."""
    if mbps <= 0:
        return None
    return size_bytes * 8 / (mbps * 1_000_000)
