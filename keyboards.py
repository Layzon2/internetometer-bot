"""Клавиатуры и кнопки для inline-меню."""
from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo


def btn(text: str, callback_data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=callback_data)


def webapp_btn(url: str, text: str = "🎛 Открыть приложение") -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, web_app=WebAppInfo(url=url))


def main_menu(webapp_url: str | None = None) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if webapp_url:
        rows.append([webapp_btn(webapp_url)])
    rows += [
        [btn("🚀 Спидтест", "speed:menu"), btn("🧮 Калькулятор", "calc:menu")],
        [btn("📡 Мониторы", "mon:menu"), btn("🏓 Пинг", "ping:menu")],
        [btn("📊 Трафик", "traffic:show"), btn("📈 История", "hist:show")],
        [btn("🌐 Мой IP", "ip:show"), btn("📥 Скачать файл", "dl:menu")],
        [btn("❔ Помощь", "help:show")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_row() -> list[InlineKeyboardButton]:
    return [btn("🔙 В меню", "menu")]


def speed_sizes_menu(max_mb: int) -> InlineKeyboardMarkup:
    options = [100, 200, int(max_mb)]
    options = sorted({o for o in options if 1 <= o <= max_mb})
    labels = {100: "⚡ Быстрый · 100 МБ", 200: "🚀 Стандарт · 200 МБ"}
    rows = [[btn(labels.get(o, f"🏎 Максимум · {o} МБ"), f"speed:run:{o}")] for o in options]
    rows.append(back_row())
    return InlineKeyboardMarkup(inline_keyboard=rows)


def calc_sizes_menu() -> InlineKeyboardMarkup:
    presets: list[tuple[str, int]] = [
        ("📱 Приложение · 300 МБ", 300 * 1024 * 1024),
        ("🎬 Фильм · 4.7 ГБ", int(4.7 * 1024 ** 3)),
        ("💿 Образ · 8 ГБ", 8 * 1024 ** 3),
        ("🎮 Игра · 60 ГБ", 60 * 1024 ** 3),
    ]
    rows = [[btn(label, f"calc:size:{size}") for label, size in presets[:2]]]
    rows.append([btn(label, f"calc:size:{size}") for label, size in presets[2:]])
    rows.append([btn("✍️ Своё значение", "calc:custom"), btn("🔙 В меню", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def calc_speeds_menu(size_bytes: int) -> InlineKeyboardMarkup:
    rows = [
        [
            btn("📶 10 Мбит/с", f"calc:speed:{size_bytes}:10"),
            btn("📶 25 Мбит/с", f"calc:speed:{size_bytes}:25"),
        ],
        [
            btn("📶 50 Мбит/с", f"calc:speed:{size_bytes}:50"),
            btn("📶 100 Мбит/с", f"calc:speed:{size_bytes}:100"),
        ],
        [
            btn("🚀 500 Мбит/с", f"calc:speed:{size_bytes}:500"),
            btn("⏱ 1000 Мбит/с", f"calc:speed:{size_bytes}:1000"),
        ],
        [btn("✍️ Своя скорость", "calc:custom"), btn("🔙 Назад", "calc:menu")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def calc_result_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("🔄 Заново", "calc:menu"), btn("🔙 В меню", "menu")],
        ]
    )


def interval_menu() -> InlineKeyboardMarkup:
    rows = [
        [
            btn("30 с", "mon:interval:30"),
            btn("1 мин", "mon:interval:60"),
            btn("5 мин", "mon:interval:300"),
        ],
        [
            btn("15 мин", "mon:interval:900"),
            btn("1 час", "mon:interval:3600"),
            btn("❌ Отмена", "state:cancel"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def dialog_cancel_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[[btn("❌ Отмена", "state:cancel")]]
    )


def menu_close_kb() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[back_row()])