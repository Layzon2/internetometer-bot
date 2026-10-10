"""Обработчики бота-интернетометра (инаялйн-меню + диалоги FSM)."""
from __future__ import annotations

import json
import logging
import time
from contextlib import suppress
from typing import Any
from urllib.parse import unquote, urlparse

import aiohttp
from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from config import Settings
from database import Database
from keyboards import (
    back_row,
    btn,
    calc_result_kb,
    calc_sizes_menu,
    calc_speeds_menu,
    dialog_cancel_kb,
    interval_menu,
    main_menu,
    menu_close_kb,
    speed_sizes_menu,
)
from services.netcheck import check_url, normalize_url, tcp_ping
from services.speedtest_service import HEADERS, SpeedTestResult, fetch_meta, run_speed_test
from services.traffic import host_net_io, psutil_available
from services.units import (
    download_seconds,
    format_duration,
    format_speed,
    human_bytes,
    parse_size,
    parse_speed,
)

logger = logging.getLogger(__name__)

router = Router(name="main")

HELP_TEXT = (
    "🌐 <b>Интернетометр</b> — бот для проверки интернета.\n\n"
    "<b>Скорость и сеть:</b>\n"
    "🚀 /speed [МБ] — замер скорости (ping, загрузка, отдача + оценка)\n"
    "🧮 /calc &lt;размер&gt; &lt;скорость&gt; — за сколько скачается файл\n"
    "🏓 /ping &lt;хост&gt;[:порт] — задержка и доступность узла\n"
    "🌐 /myip — ваш внешний IP, провайдер и локация\n"
    "📈 /history — история ваших замеров скорости\n"
    "📥 /download &lt;ссылка&gt; — скачать файл и посчитать трафик\n"
    "📊 /traffic — счётчик трафика\n\n"
    "<b>Мониторинг сайтов:</b>\n"
    "<code>/monitor add https://example.com 300</code> — добавить (интервал в секундах)\n"
    "<code>/monitor list</code> · <code>/monitor del 1</code> · "
    "<code>/monitor on 1</code> · <code>/monitor off 1</code> · <code>/monitor check 1</code>\n\n"
    "<b>Примеры калькулятора:</b>\n"
    "<code>/calc 700mb 20mbit</code> · <code>/calc 5gb 100mbps</code> · <code>/calc 2gb 5mb/s</code>\n\n"
    "🆔 /id — ваш Telegram ID"
)


class AddMonitor(StatesGroup):
    url = State()
    interval = State()


class PingFlow(StatesGroup):
    host = State()


class DownloadFlow(StatesGroup):
    url = State()


# -------------------------------- утилиты ----------------------------------


async def edit_into(message: Message, text: str, markup=None, **kwargs) -> None:
    with suppress(Exception):
        await message.edit_text(text, reply_markup=markup, **kwargs)
        return
    await message.answer(text, reply_markup=markup, **kwargs)


async def show_main_menu(message: Message, text: str | None = None, webapp_url: str | None = None) -> None:
    await edit_into(
        message,
        text or "🗂 <b>Главное меню</b>\nВыберите действие:",
        main_menu(webapp_url),
        disable_web_page_preview=True,
    )


def rerun_kb(size_mb: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [btn("🔁 Повторить замер", f"speed:run:{size_mb}")],
            back_row(),
        ]
    )


def _verdict(down: float | None, up: float | None, ping_avg: float | None) -> str | None:
    parts: list[str] = []
    if down is not None:
        if down >= 100:
            parts.append("🚀 гигабитный уровень")
        elif down >= 50:
            parts.append("✅ отлично для 4K")
        elif down >= 25:
            parts.append("✅ хватает для 4K")
        elif down >= 10:
            parts.append("👍 Full HD без проблем")
        elif down >= 3:
            parts.append("📺 HD и видеозвонки")
        else:
            parts.append("⚠️ только сёрфинг и музыка")
    if ping_avg is not None:
        if ping_avg <= 20:
            parts.append("🎮 идеально для игр")
        elif ping_avg <= 50:
            parts.append("🎮 комфортно для игр")
        elif ping_avg <= 100:
            parts.append("🎮 игры возможны")
        else:
            parts.append("⚠️ высокий пинг для игр")
    return " · ".join(parts) if parts else None


def _parse_host_port(raw: str) -> tuple[str, int] | None:
    host = (raw or "").strip()
    if not host:
        return None
    if "://" in host:
        host = host.split("://", 1)[1]
    host = host.split("/", 1)[0]
    port = 443
    if ":" in host:
        host_part, _, port_part = host.partition(":")
        host = host_part
        if port_part.isdigit():
            port = int(port_part)
    if not host:
        return None
    return host, port


# ------------------------ трекинг пользователей и трафика ------------------------


@router.message.middleware()
async def track_user(handler, event: Message, data: dict[str, Any]):
    db: Database | None = data.get("db")
    user = event.from_user
    if db is not None and user is not None:
        await db.upsert_user(user.id, user.username, user.first_name)
        text = event.text or event.caption or ""
        if text:
            await db.add_traffic(user.id, in_bytes=len(text.encode("utf-8")))
    return await handler(event, data)


# --------------------------------- команды ----------------------------------


@router.message(CommandStart())
async def cmd_start(message: Message, settings: Settings) -> None:
    name = message.from_user.first_name if message.from_user else "друг"
    await show_main_menu(message, f"Привет, {name}! 👋\n\n{HELP_TEXT}", settings.webapp_url)


@router.message(Command("help"))
async def cmd_help(message: Message, settings: Settings) -> None:
    await show_main_menu(message, HELP_TEXT, settings.webapp_url)


@router.message(Command("id"))
async def cmd_id(message: Message) -> None:
    user = message.from_user
    chat = message.chat
    await message.answer(
        f"🆔 Ваш ID: <code>{user.id if user else '—'}</code>\n"
        f"💬 ID чата: <code>{chat.id}</code>"
    )


# --------------------------------- спидтест ---------------------------------


def _format_speed_result(result: SpeedTestResult, size_mb: int) -> str:
    lines = ["🚀 <b>Интернетометр — результат</b>", "━━━━━━━━━━━━━━━━━━━"]

    if result.ping is not None:
        p = result.ping
        lines.append(
            f"🏓 Пинг: <b>{p.avg_ms:.0f} мс</b> "
            f"(мин {p.min_ms:.0f} / макс {p.max_ms:.0f}, джиттер {p.jitter_ms:.1f})"
        )
        if p.loss_percent:
            lines.append(f"📉 Потери: {p.loss_percent:.0f}%")
    else:
        lines.append("🏓 Пинг: недоступен")

    if result.download_mbps is not None:
        lines.append(f"⬇️ Загрузка: <b>{format_speed(result.download_mbps)}</b>")
    else:
        lines.append("⬇️ Загрузка: не удалось измерить")

    if result.upload_mbps is not None:
        lines.append(f"⬆️ Отдача: <b>{format_speed(result.upload_mbps)}</b>")
    else:
        lines.append("⬆️ Отдача: не удалось измерить")

    verdict = _verdict(
        result.download_mbps,
        result.upload_mbps,
        result.ping.avg_ms if result.ping else None,
    )
    if verdict:
        lines.append(f"🧠 Оценка: {verdict}")

    meta = result.meta or {}
    if meta:
        lines.append("━━━━━━━━━━━━━━━━━━━")
        if meta.get("asOrganization"):
            lines.append(f"🏢 Провайдер: {meta['asOrganization']}")
        if meta.get("clientIp"):
            lines.append(f"🌐 IP: <code>{meta['clientIp']}</code>")
        location = ", ".join(x for x in (meta.get("city"), meta.get("country")) if x)
        if location:
            lines.append(f"📍 Локация: {location}")

    lines.append("━━━━━━━━━━━━━━━━━━━")
    lines.append(
        f"📦 Выборка: ↓ {human_bytes(result.downloaded_bytes)} за "
        f"{result.download_seconds:.1f} с · ↑ {human_bytes(result.uploaded_bytes)} за "
        f"{result.upload_seconds:.1f} с"
    )
    lines.append(f"⏱ Всего времени: {result.duration:.1f} с (объём до {size_mb} МБ)")
    return "\n".join(lines)


async def _run_speed(
    status: Message,
    db: Database,
    settings: Settings,
    size_mb: int,
    user_id: int,
) -> None:
    last_edit = 0.0

    async def progress(text: str) -> None:
        nonlocal last_edit
        now = time.monotonic()
        if now - last_edit < 1.0:
            return
        last_edit = now
        with suppress(Exception):
            await status.edit_text(text)

    try:
        result = await run_speed_test(
            max_download_mb=size_mb,
            target_seconds=settings.speedtest_seconds,
            progress=progress,
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка спидтеста")
        with suppress(Exception):
            await status.edit_text(f"❌ Не удалось выполнить замер: {exc}")
        return

    await db.add_traffic(
        user_id,
        in_bytes=result.downloaded_bytes,
        out_bytes=result.uploaded_bytes,
    )
    await db.add_speedtest(
        user_id,
        result.ping.avg_ms if result.ping else None,
        result.download_mbps,
        result.upload_mbps,
    )
    await status.edit_text(
        _format_speed_result(result, size_mb),
        reply_markup=rerun_kb(size_mb),
        disable_web_page_preview=True,
    )


@router.message(Command("speed"))
async def cmd_speed(message: Message, command: CommandObject, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    if command.args and command.args.split()[0].isdigit():
        size_mb = max(1, min(int(command.args.split()[0]), settings.max_speedtest_mb))
        status = await message.answer(
            f"⏳ Запускаю замер (до {size_mb} МБ, ~{settings.speedtest_seconds} с)..."
        )
        await _run_speed(status, db, settings, size_mb, message.from_user.id)
        return
    await message.answer(
        "🚀 <b>Спидтест</b>\nВыберите объём теста (объём — верхняя граница):",
        reply_markup=speed_sizes_menu(settings.max_speedtest_mb),
    )


@router.callback_query(F.data.startswith("speed:run:"))
async def cb_speed_run(query: CallbackQuery, db: Database, settings: Settings) -> None:
    try:
        size_mb = int(query.data.split(":")[2])
    except (IndexError, ValueError):
        size_mb = settings.default_speedtest_mb
    size_mb = max(1, min(size_mb, settings.max_speedtest_mb))
    await query.answer("Запускаю замер...")
    if query.message is None:
        return
    with suppress(Exception):
        await query.message.edit_text(
            f"⏳ Запускаю замер (до {size_mb} МБ, ~{settings.speedtest_seconds} с)..."
        )
    await _run_speed(query.message, db, settings, size_mb, query.from_user.id)


# -------------------------------- калькулятор -------------------------------


@router.message(Command("calc"))
async def cmd_calc(message: Message, command: CommandObject) -> None:
    args = (command.args or "").split()
    if len(args) < 2:
        await message.answer(
            "🧮 <b>Калькулятор загрузки</b>\nВыберите размер файла:",
            reply_markup=calc_sizes_menu(),
        )
        return

    size_bytes = parse_size(args[0])
    if size_bytes is None:
        await message.answer(
            "❌ Не понял размер. Укажите единицу: <code>500kb</code>, "
            "<code>700mb</code>, <code>1.5gb</code>, <code>2tb</code>."
        )
        return

    speed_mbps = parse_speed("".join(args[1:]))
    if speed_mbps is None or speed_mbps <= 0:
        await message.answer(
            "❌ Не понял скорость. Примеры: <code>20mbit</code>, <code>100mbps</code>, "
            "<code>5mb/s</code> или просто число (Мбит/с)."
        )
        return

    await message.answer(_calc_text(size_bytes, speed_mbps), reply_markup=calc_result_kb())


def _calc_text(size_bytes: int, speed_mbps: float) -> str:
    seconds = download_seconds(size_bytes, speed_mbps)
    assert seconds is not None
    lines = [
        "🧮 <b>Калькулятор загрузки</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"📦 Файл: <b>{human_bytes(size_bytes)}</b>",
        f"🚀 Скорость: <b>{format_speed(speed_mbps)}</b>",
        f"⏱ Время: <b>{format_duration(seconds)}</b>",
        "━━━━━━━━━━━━━━━━━━━",
        f"ℹ️ Идеальный предел: {speed_mbps / 8:.1f} МБ/с = "
        f"файл уйдёт за {seconds:.0f} с при полностью свободном канале.",
    ]
    return "\n".join(lines)


@router.callback_query(F.data == "calc:menu")
async def cb_calc_menu(query: CallbackQuery) -> None:
    await query.answer()
    if query.message is None:
        return
    await edit_into(query.message, "🧮 <b>Калькулятор</b>\nКакой размер файла?", calc_sizes_menu())


@router.callback_query(F.data.startswith("calc:size:"))
async def cb_calc_size(query: CallbackQuery) -> None:
    try:
        size_bytes = int(query.data.split(":")[2])
    except (IndexError, ValueError):
        await query.answer("Ошибка", show_alert=True)
        return
    await query.answer()
    if query.message is None:
        return
    await edit_into(
        query.message,
        f"🧮 Файл <b>{human_bytes(size_bytes)}</b>\nТеперь выберите скорость:",
        calc_speeds_menu(size_bytes),
    )


@router.callback_query(F.data.startswith("calc:speed:"))
async def cb_calc_speed(query: CallbackQuery) -> None:
    parts = query.data.split(":")
    try:
        size_bytes = int(parts[2])
        speed_mbps = float(parts[3])
    except (IndexError, ValueError):
        await query.answer("Ошибка", show_alert=True)
        return
    await query.answer()
    if query.message is None:
        return
    await edit_into(query.message, _calc_text(size_bytes, speed_mbps), calc_result_kb())


@router.callback_query(F.data == "calc:custom")
async def cb_calc_custom(query: CallbackQuery) -> None:
    await query.answer()
    await query.message.answer(
        "✍️ Напишите вручную, например:\n"
        "<code>/calc 700mb 20mbit</code> — 700 МБ на 20 Мбит/с\n"
        "<code>/calc 2gb 5mb/s</code> — 2 ГБ на 5 МБ/с",
        reply_markup=menu_close_kb(),
    )


# ---------------------------------- пинг -----------------------------------


async def _perform_ping(message: Message, raw: str) -> None:
    parsed = _parse_host_port(raw)
    if parsed is None:
        await message.answer(
            "❌ Не похоже на адрес. Примеры: <code>example.com</code>, <code>1.1.1.1:443</code>"
        )
        return
    host, port = parsed
    status = await message.answer(f"📡 Проверяю <code>{host}:{port}</code>...")
    stats = await tcp_ping(host, port, attempts=4)
    if stats is None:
        await status.edit_text(
            f"⛔️ <b>Узел недоступен</b>\n<code>{host}:{port}</code>\n"
            "Не удалось установить TCP-соединение.",
            reply_markup=menu_close_kb(),
        )
        return
    await status.edit_text(
        f"✅ <b>{host}:{port}</b> доступен\n"
        f"🏓 Средняя задержка: <b>{stats.avg_ms:.0f} мс</b>\n"
        f"мин {stats.min_ms:.0f} / макс {stats.max_ms:.0f}, джиттер {stats.jitter_ms:.1f} мс\n"
        f"📉 Потери: {stats.loss_percent:.0f}% ({stats.samples}/4)",
        reply_markup=menu_close_kb(),
    )


@router.message(Command("ping"))
async def cmd_ping(message: Message, command: CommandObject, state: FSMContext) -> None:
    await state.clear()
    if command.args:
        await _perform_ping(message, command.args)
        return
    await state.set_state(PingFlow.host)
    await message.answer(
        "🏓 Пришлите адрес для проверки:\n"
        "<code>example.com</code> или <code>1.1.1.1:443</code>",
        reply_markup=dialog_cancel_kb(),
    )


@router.message(PingFlow.host, F.text)
async def ping_got_host(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if text.startswith("/") or text.lower() in ("отмена", "cancel"):
        await state.clear()
        await message.answer("Отменено.", reply_markup=menu_close_kb())
        return
    await state.clear()
    await _perform_ping(message, text)


# ----------------------------------- myip -----------------------------------


async def _myip_text() -> str | None:
    try:
        async with aiohttp.ClientSession(headers=HEADERS) as session:
            meta = await fetch_meta(session)
    except Exception:  # noqa: BLE001
        meta = {}
    if not meta or not meta.get("clientIp"):
        return None
    lines = ["🌐 <b>Ваше подключение</b>", "━━━━━━━━━━━━━━━━━━━"]
    lines.append(f"🖥 IP: <code>{meta['clientIp']}</code>")
    if meta.get("asOrganization"):
        lines.append(f"🏢 Провайдер: {meta['asOrganization']}")
    if meta.get("asn"):
        lines.append(f"🔢 ASN: {meta['asn']}")
    location = ", ".join(
        x for x in (meta.get("city"), meta.get("region"), meta.get("country")) if x
    )
    if location:
        lines.append(f"📍 Локация: {location}")
    if meta.get("colo"):
        colo = meta["colo"]
        if isinstance(colo, dict):
            colo = colo.get("iata") or colo.get("city")
        if colo:
            lines.append(f"🛰 Узел Cloudflare: {colo}")
    if meta.get("httpProtocol"):
        lines.append(f"🔒 Протокол: {meta['httpProtocol']}")
    return "\n".join(lines)


@router.message(Command("myip"))
async def cmd_myip(message: Message) -> None:
    status = await message.answer("🌐 Определяю ваш IP и провайдера...")
    text = await _myip_text()
    if text is None:
        await status.edit_text("❌ Сервис не вернул данные. Попробуйте позже.")
        return
    await status.edit_text(text, reply_markup=menu_close_kb(), disable_web_page_preview=True)


# ------------------------------ история замеров -----------------------------


async def _history_text(db: Database, user_id: int) -> str:
    rows = await db.get_speedtests(user_id, limit=7)
    if not rows:
        return "📈 История пуста. Сделайте первый замер в меню «🚀 Спидтест»."
    lines = ["📈 <b>История замеров</b> (последние 7)", "━━━━━━━━━━━━━━━━━━━"]
    for r in rows:
        when = time.strftime("%d.%m %H:%M", time.localtime(r["created_at"]))
        ping = f"{r['ping_ms']:.0f} мс" if r["ping_ms"] is not None else "—"
        down = f"{r['download_mbps']:.1f}" if r["download_mbps"] is not None else "—"
        up = f"{r['upload_mbps']:.1f}" if r["upload_mbps"] is not None else "—"
        lines.append(f"🕒 {when} · 🏓 {ping} · ⬇️ {down} · ⬆️ {up} Мбит/с")
    downs = [r["download_mbps"] for r in rows if r["download_mbps"] is not None]
    ups = [r["upload_mbps"] for r in rows if r["upload_mbps"] is not None]
    if downs or ups:
        lines.append("━━━━━━━━━━━━━━━━━━━")
        if downs:
            lines.append(f"📊 Средняя загрузка: <b>{sum(downs) / len(downs):.1f} Мбит/с</b>")
        if ups:
            lines.append(f"📊 Средняя отдача: <b>{sum(ups) / len(ups):.1f} Мбит/с</b>")
    return "\n".join(lines)


@router.message(Command("history"))
async def cmd_history(message: Message, db: Database) -> None:
    await message.answer(
        await _history_text(db, message.from_user.id),
        reply_markup=menu_close_kb(),
        disable_web_page_preview=True,
    )


# --------------------------------- трафик -----------------------------------


async def _traffic_text(db: Database, user_id: int) -> str:
    in_b, out_b = await db.get_user_traffic(user_id)
    lines = [
        "📊 <b>Счётчик трафика</b>",
        "━━━━━━━━━━━━━━━━━━━",
        "👤 <b>Ваш трафик</b> (замеры, загрузки и сообщения):",
        f"    ⬇️ принято: {human_bytes(in_b)}",
        f"    ⬆️ отдано:  {human_bytes(out_b)}",
        f"    Σ всего:   {human_bytes(in_b + out_b)}",
    ]
    io = host_net_io()
    if io is not None:
        lines += [
            "━━━━━━━━━━━━━━━━━━━",
            "🖥 <b>Сеть хоста</b> (с момента загрузки ОС):",
            f"    ⬇️ принято: {human_bytes(io.bytes_recv)}",
            f"    ⬆️ отдано:  {human_bytes(io.bytes_sent)}",
        ]
    else:
        lines += [
            "━━━━━━━━━━━━━━━━━━━",
            "ℹ️ Статистика хоста недоступна (не установлен psutil).",
        ]
    return "\n".join(lines)


@router.message(Command("traffic"))
async def cmd_traffic(message: Message, db: Database) -> None:
    await message.answer(
        await _traffic_text(db, message.from_user.id),
        reply_markup=menu_close_kb(),
        disable_web_page_preview=True,
    )


# -------------------------------- скачивание --------------------------------


async def _perform_download(message: Message, raw: str, db: Database, settings: Settings, user_id: int) -> None:
    url = normalize_url(raw)
    if url is None:
        await message.answer(
            "❌ Не похоже на ссылку. Пример: <code>/download https://example.com/file.zip</code>",
            reply_markup=menu_close_kb(),
        )
        return

    limit = settings.max_download_mb * 1024 * 1024
    status = await message.answer("⏳ Скачиваю файл...")
    try:
        timeout = aiohttp.ClientTimeout(total=120, connect=15)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(url) as resp:
                if resp.status >= 400:
                    await status.edit_text(f"❌ Сервер вернул HTTP {resp.status}")
                    return
                data = bytearray()
                async for chunk in resp.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > limit:
                        await status.edit_text(
                            f"❌ Файл больше лимита {settings.max_download_mb} МБ."
                        )
                        return
    except Exception as exc:  # noqa: BLE001
        await status.edit_text(f"❌ Ошибка загрузки: {exc}")
        return

    size = len(data)
    filename = unquote(urlparse(url).path.rsplit("/", 1)[-1]) or "file.bin"
    await db.add_traffic(user_id, in_bytes=size, out_bytes=size)
    try:
        await message.answer_document(
            BufferedInputFile(bytes(data), filename=filename),
            caption=f"📄 {filename}\nРазмер: {human_bytes(size)}",
            reply_markup=menu_close_kb(),
        )
        with suppress(Exception):
            await status.delete()
    except Exception:  # noqa: BLE001
        await status.edit_text(f"✅ Скачано {human_bytes(size)}, но отправить файл не удалось.")


@router.message(Command("download"))
async def cmd_download(message: Message, command: CommandObject, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    if command.args:
        await _perform_download(message, command.args, db, settings, message.from_user.id)
        return
    await state.set_state(DownloadFlow.url)
    await message.answer(
        "📥 Пришлите прямую ссылку на файл\n"
        f"(до {settings.max_download_mb} МБ). Загружу, отправлю и посчитаю трафик.",
        reply_markup=dialog_cancel_kb(),
    )


@router.message(DownloadFlow.url, F.text)
async def download_got_url(message: Message, state: FSMContext, db: Database, settings: Settings) -> None:
    text = message.text.strip()
    if text.startswith("/") or text.lower() in ("отмена", "cancel"):
        await state.clear()
        await message.answer("Отменено.", reply_markup=menu_close_kb())
        return
    await state.clear()
    await _perform_download(message, text, db, settings, message.from_user.id)


# --------------------------------- мониторы ---------------------------------


def _mon_text(rows: list[dict[str, Any]]) -> str:
    if not rows:
        return "📡 У вас пока нет мониторов.\nНажмите «➕ Добавить сайт», чтобы следить за ресурсом."
    lines = ["📡 <b>Ваши мониторы</b>", ""]
    for m in rows:
        icon = "🟢" if m["last_ok"] == 1 else ("🔴" if m["last_ok"] == 0 else "⚪️")
        enabled = "" if m["enabled"] else " (выкл)"
        status_part = f", HTTP {m['last_status']}" if m["last_status"] else ""
        lines.append(
            f"{icon} <b>#{m['id']}</b>{enabled} — {m['url']}\n"
            f"    ⏱ каждые {m['interval']} с{status_part}"
        )
    lines.append("")
    lines.append("Кнопки: включить/выключить, проверить, удалить.")
    return "\n".join(lines)


def _mon_markup(rows: list[dict[str, Any]]) -> InlineKeyboardMarkup:
    kb: list[list[InlineKeyboardButton]] = []
    for m in rows:
        icon = "🟢" if m["last_ok"] == 1 else ("🔴" if m["last_ok"] == 0 else "⚪️")
        label = f"{icon} #{m['id']} {m['url'].replace('https://', '').rstrip('/')[:26]}"
        kb.append(
            [
                btn(label, f"mon:toggle:{m['id']}"),
                btn("🔍", f"mon:check:{m['id']}"),
                btn("🗑", f"mon:del:{m['id']}"),
            ]
        )
    kb.append([btn("➕ Добавить сайт", "mon:add")])
    kb.append([btn("🔄 Обновить", "mon:refresh"), btn("🔙 В меню", "menu")])
    return InlineKeyboardMarkup(inline_keyboard=kb)


async def _show_mon_menu(message: Message, db: Database, user_id: int, note: str | None = None) -> None:
    rows = await db.list_monitors(user_id)
    text = _mon_text(rows)
    if note:
        text = f"{note}\n\n" + text
    await edit_into(message, text, _mon_markup(rows), disable_web_page_preview=True)


@router.callback_query(F.data == "mon:menu")
async def cb_mon_menu(query: CallbackQuery, db: Database) -> None:
    await query.answer()
    if query.message is None:
        return
    await _show_mon_menu(query.message, db, query.from_user.id)


@router.callback_query(F.data == "mon:refresh")
async def cb_mon_refresh(query: CallbackQuery, db: Database) -> None:
    await query.answer()
    if query.message is None:
        return
    await _show_mon_menu(query.message, db, query.from_user.id)


@router.callback_query(F.data.startswith("mon:toggle:"))
async def cb_mon_toggle(query: CallbackQuery, db: Database) -> None:
    try:
        monitor_id = int(query.data.split(":")[2])
    except (IndexError, ValueError):
        await query.answer("Ошибка", show_alert=True)
        return
    mon = await db.get_monitor(monitor_id)
    if mon and mon["user_id"] == query.from_user.id:
        await db.set_monitor_enabled(monitor_id, query.from_user.id, not mon["enabled"])
    await query.answer("Готово")
    if query.message is None:
        return
    await _show_mon_menu(query.message, db, query.from_user.id)


@router.callback_query(F.data.startswith("mon:check:"))
async def cb_mon_check(query: CallbackQuery, db: Database) -> None:
    try:
        monitor_id = int(query.data.split(":")[2])
    except (IndexError, ValueError):
        await query.answer("Ошибка", show_alert=True)
        return
    await query.answer("Проверяю...")
    mon = await db.get_monitor(monitor_id)
    if mon is None or mon["user_id"] != query.from_user.id or query.message is None:
        await query.answer()
        return
    result = await check_url(mon["url"])
    now = int(time.time())
    await db.update_monitor_status(monitor_id, result.ok, result.status, result.error, now)
    if result.ok:
        note = f"🔍 #{monitor_id}: ✅ HTTP {result.status}, {result.latency_ms:.0f} мс"
    else:
        reason = f"HTTP {result.status}" if result.status else (result.error or "нет ответа")
        note = f"🔍 #{monitor_id}: ⛔️ {reason}"
    await _show_mon_menu(query.message, db, query.from_user.id, note)


@router.callback_query(F.data.startswith("mon:del:"))
async def cb_mon_del(query: CallbackQuery, db: Database) -> None:
    try:
        monitor_id = int(query.data.split(":")[2])
    except (IndexError, ValueError):
        await query.answer("Ошибка", show_alert=True)
        return
    ok = await db.delete_monitor(monitor_id, query.from_user.id)
    await query.answer("Удалён" if ok else "Не найден")
    if query.message is None:
        return
    await _show_mon_menu(query.message, db, query.from_user.id)


@router.callback_query(F.data == "mon:add")
async def cb_mon_add(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await state.set_state(AddMonitor.url)
    await query.message.answer(
        "➕ Отправьте адрес сайта, например:\n<code>https://example.com</code>",
        reply_markup=dialog_cancel_kb(),
    )


@router.message(AddMonitor.url, F.text)
async def mon_got_url(message: Message, state: FSMContext) -> None:
    text = message.text.strip()
    if text.startswith("/") or text.lower() in ("отмена", "cancel"):
        await state.clear()
        await message.answer("Отменено.", reply_markup=menu_close_kb())
        return
    url = normalize_url(text)
    if url is None:
        await message.answer("❌ Не похоже на адрес. Пример: <code>https://example.com</code>")
        return
    await state.update_data(url=url)
    await state.set_state(AddMonitor.interval)
    await message.answer(
        f"🔗 <b>{url}</b>\nКак часто проверять доступность?",
        reply_markup=interval_menu(),
    )


@router.callback_query(AddMonitor.interval, F.data.startswith("mon:interval:"))
async def mon_got_interval(query: CallbackQuery, state: FSMContext, db: Database, settings: Settings) -> None:
    try:
        seconds = int(query.data.split(":")[2])
    except (IndexError, ValueError):
        await query.answer("Ошибка", show_alert=True)
        return
    data = await state.get_data()
    if await db.count_monitors(query.from_user.id) >= settings.max_monitors_per_user:
        await state.clear()
        await query.message.answer(
            f"❌ Достигнут лимит мониторов ({settings.max_monitors_per_user})."
        )
        await query.answer()
        return
    monitor_id = await db.add_monitor(
        query.from_user.id, query.message.chat.id, data["url"], seconds
    )
    await state.clear()
    await query.answer("Добавлен")
    await _show_mon_menu(
        query.message,
        db,
        query.from_user.id,
        f"✅ Монитор <b>#{monitor_id}</b> добавлен. Проверка каждые {seconds} с.",
    )


# ------------------------------ команда /monitor -----------------------------


@router.message(Command("monitor", "monitors"))
async def cmd_monitor(message: Message, command: CommandObject, db: Database, settings: Settings, state: FSMContext) -> None:
    await state.clear()
    user_id = message.from_user.id
    args = (command.args or "").split()
    action = args[0].lower() if args else "list"

    if action in ("list", "ls"):
        rows = await db.list_monitors(user_id)
        await message.answer(_mon_text(rows), reply_markup=_mon_markup(rows))
        return

    if action == "add":
        if len(args) < 2:
            await message.answer("Использование: <code>/monitor add https://example.com 300</code>")
            return
        url = normalize_url(args[1])
        if url is None:
            await message.answer("❌ Некорректный адрес. Пример: <code>https://example.com</code>")
            return
        interval = 300
        if len(args) >= 3 and args[2].isdigit():
            interval = max(30, min(int(args[2]), 86400))
        if await db.count_monitors(user_id) >= settings.max_monitors_per_user:
            await message.answer(f"❌ Достигнут лимит мониторов ({settings.max_monitors_per_user}).")
            return
        monitor_id = await db.add_monitor(user_id, message.chat.id, url, interval)
        await message.answer(
            f"✅ Монитор <b>#{monitor_id}</b> добавлен.\n"
            f"🔗 {url}\n⏱ Проверка каждые {interval} с.\n"
            "Об изменениях сообщу сам.",
            reply_markup=menu_close_kb(),
        )
        return

    if action in ("del", "delete", "rm", "remove"):
        if len(args) < 2 or not args[1].isdigit():
            await message.answer("Использование: <code>/monitor del 1</code>")
            return
        ok = await db.delete_monitor(int(args[1]), user_id)
        await message.answer("🗑 Монитор удалён." if ok else "❌ Монитор не найден.")
        return

    if action in ("on", "off"):
        if len(args) < 2 or not args[1].isdigit():
            await message.answer(f"Использование: <code>/monitor {action} 1</code>")
            return
        enabled = action == "on"
        ok = await db.set_monitor_enabled(int(args[1]), user_id, enabled)
        if not ok:
            await message.answer("❌ Монитор не найден.")
            return
        await message.answer("▶️ Монитор включён." if enabled else "⏸ Монитор выключен.")
        return

    if action == "check":
        if len(args) < 2 or not args[1].isdigit():
            await message.answer("Использование: <code>/monitor check 1</code>")
            return
        monitor = await db.get_monitor(int(args[1]))
        if monitor is None or monitor["user_id"] != user_id:
            await message.answer("❌ Монитор не найден.")
            return
        result = await check_url(monitor["url"])
        if result.ok:
            await message.answer(
                f"✅ {monitor['url']}\nHTTP {result.status}, {result.latency_ms:.0f} мс"
            )
        else:
            reason = f"HTTP {result.status}" if result.status else (result.error or "нет ответа")
            await message.answer(f"⛔️ {monitor['url']}\nПричина: {reason}")
        return

    rows = await db.list_monitors(user_id)
    await message.answer(_mon_text(rows), reply_markup=_mon_markup(rows))


# --------------------------- общие callback-навигация ---------------------------


@router.callback_query(F.data == "menu")
async def cb_menu(query: CallbackQuery, settings: Settings) -> None:
    await query.answer()
    if query.message is None:
        return
    await show_main_menu(query.message, webapp_url=settings.webapp_url)


@router.callback_query(F.data == "state:cancel")
async def cb_cancel(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await state.clear()
    await query.answer("Отменено")
    if query.message is None:
        return
    await show_main_menu(query.message, webapp_url=settings.webapp_url)


@router.callback_query(F.data == "traffic:show")
async def cb_traffic(query: CallbackQuery, db: Database) -> None:
    await query.answer()
    if query.message is None:
        return
    await edit_into(
        query.message,
        await _traffic_text(db, query.from_user.id),
        menu_close_kb(),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "hist:show")
async def cb_history(query: CallbackQuery, db: Database) -> None:
    await query.answer()
    if query.message is None:
        return
    await edit_into(
        query.message,
        await _history_text(db, query.from_user.id),
        menu_close_kb(),
        disable_web_page_preview=True,
    )


@router.callback_query(F.data == "ip:show")
async def cb_myip(query: CallbackQuery) -> None:
    await query.answer("Определяю...")
    if query.message is None:
        return
    with suppress(Exception):
        await query.message.edit_text("🌐 Определяю ваш IP и провайдера...")
    text = await _myip_text()
    if text is None:
        text = "❌ Сервис не вернул данные. Попробуйте позже."
    await edit_into(query.message, text, menu_close_kb(), disable_web_page_preview=True)


@router.callback_query(F.data == "help:show")
async def cb_help(query: CallbackQuery, settings: Settings) -> None:
    await query.answer()
    if query.message is None:
        return
    await show_main_menu(query.message, HELP_TEXT, settings.webapp_url)


@router.callback_query(F.data == "speed:menu")
async def cb_speed_menu(query: CallbackQuery, settings: Settings) -> None:
    await query.answer()
    if query.message is None:
        return
    await edit_into(
        query.message,
        "🚀 <b>Спидтест</b>\nВыберите объём (верхняя граница):",
        speed_sizes_menu(settings.max_speedtest_mb),
    )


@router.callback_query(F.data == "ping:menu")
async def cb_ping_menu(query: CallbackQuery, state: FSMContext) -> None:
    await query.answer()
    await state.set_state(PingFlow.host)
    await query.message.answer(
        "🏓 Пришлите адрес для проверки:\n"
        "<code>example.com</code> или <code>1.1.1.1:443</code>",
        reply_markup=dialog_cancel_kb(),
    )


@router.callback_query(F.data == "dl:menu")
async def cb_dl_menu(query: CallbackQuery, state: FSMContext, settings: Settings) -> None:
    await query.answer()
    await state.set_state(DownloadFlow.url)
    await query.message.answer(
        "📥 Пришлите прямую ссылку на файл\n"
        f"(до {settings.max_download_mb} МБ). Загружу, отправлю и посчитаю трафик.",
        reply_markup=dialog_cancel_kb(),
    )


# -------------------------------- админ: статистика --------------------------------


@router.message(Command("stats"))
async def cmd_stats(message: Message, db: Database, settings: Settings) -> None:
    user = message.from_user
    if user is None or (settings.admin_ids and user.id not in settings.admin_ids):
        await message.answer("⛔️ Команда доступна только администратору.")
        return
    stats = await db.stats()
    io = host_net_io()
    lines = [
        "🛠 <b>Статистика бота</b>",
        f"👥 пользователей: {stats['users']}",
        f"📡 мониторов: {stats['enabled_monitors']} активных / {stats['monitors']} всего",
        f"🧩 psutil: {'да' if psutil_available() else 'нет'}",
    ]
    if io is not None:
        lines.append(f"🖥 трафик хоста: ↓{human_bytes(io.bytes_recv)} ↑{human_bytes(io.bytes_sent)}")
    await message.answer("\n".join(lines))


@router.message(F.text)
async def fallback(message: Message, settings: Settings) -> None:
    await message.answer(
        "Не понял команду. Нажмите кнопку ниже или отправьте /help, "
        "чтобы увидеть возможности.",
        reply_markup=main_menu(settings.webapp_url),
    )


# ----------------------------- Telegram Web App -----------------------------


@router.message(F.web_app_data)
async def on_webapp_data(message: Message, db: Database) -> None:
    """Принимает результат теста из мини-приложения (Telegram.WebApp.sendData)."""
    user = message.from_user
    if user is None or message.web_app_data is None:
        return

    try:
        payload = json.loads(message.web_app_data.data or "{}")
    except (json.JSONDecodeError, TypeError):
        payload = {}

    if not isinstance(payload, dict) or payload.get("action") != "speedtest":
        return

    down = _safe_float(payload.get("download_mbps"))
    up = _safe_float(payload.get("upload_mbps"))
    ping = _safe_float(payload.get("ping_ms"))
    down_bytes = int(payload.get("downloaded_bytes") or 0)
    up_bytes = int(payload.get("uploaded_bytes") or 0)

    await db.add_traffic(user.id, in_bytes=down_bytes, out_bytes=up_bytes)
    await db.add_speedtest(user.id, ping, down, up)

    lines = ["📱 <b>Результат из приложения</b> (замер устройства)", "━━━━━━━━━━━━━━━━━━━"]
    lines.append(f"🏓 Пинг: <b>{ping:.0f} мс</b>" if ping else "🏓 Пинг: —")
    lines.append(f"⬇️ Загрузка: <b>{_fmt_mbps(down)}</b>" if down else "⬇️ Загрузка: —")
    lines.append(f"⬆️ Отдача: <b>{_fmt_mbps(up)}</b>" if up else "⬆️ Отдача: —")
    if down or up or ping:
        verdict = _verdict(
            down if down else None,
            up if up else None,
            ping if ping else None,
        )
        if verdict:
            lines.append(f"🧠 Оценка: {verdict}")
    lines.append("━━━━━━━━━━━━━━━━━━━")
    meta_bits = []
    if payload.get("client_ip"):
        meta_bits.append(f"🌐 IP устройства: <code>{payload['client_ip']}</code>")
    if payload.get("provider"):
        meta_bits.append(f"🏢 {payload['provider']}")
    if meta_bits:
        lines.extend(meta_bits)
    if down_bytes or up_bytes:
        lines.append(
            f"📦 Выборка: ↓ {human_bytes(down_bytes)} · ↑ {human_bytes(up_bytes)}"
        )
    lines.append("\n📈 Результат сохранён в историю (/history).")

    await message.answer("\n".join(lines), reply_markup=menu_close_kb())


def _safe_float(value: Any) -> float | None:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return None
    return num if num > 0 else None


def _fmt_mbps(v: float | None) -> str:
    return format_speed(v) if v is not None else "—"