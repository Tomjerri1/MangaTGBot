"""
Manga Tracker Bot - єдина точка входу.

Команди:
  /start  - меню
  /cancel - скасувати поточну дію
"""

import sys
import os
import signal
import warnings
import functools
import asyncio
import time
import datetime

import psutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from telegram import Update, Message, InlineKeyboardButton, InlineKeyboardMarkup, InlineQueryResultArticle, InputTextMessageContent
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters, InlineQueryHandler
)
from telegram.warnings import PTBUserWarning

warnings.filterwarnings("ignore", message=".*CallbackQueryHandler.*", category=PTBUserWarning)

from config.config import TOKEN, CHAT_ID
from core.repository import get_repository, AbstractRepository
from core.checker import run_check
from core.logger import get_logger
from core.parser_playwright import _shutdown_event

log = get_logger("bot").info

# Час запуску бота і поточний процес для статистики
_BOT_START_TIME = time.time()
_process = psutil.Process()
_process.cpu_percent()  # перший виклик завжди повертає 0.0 - скидається одразу
_RAM_PEAK_MB: float = 0.0  # відстежується фоновою задачею кожну секунду

def _get_total_ram_mb() -> float:
    """RAM Python процесу + всі дочірні процеси (Chromium тощо)."""
    try:
        total = _process.memory_info().rss
        for child in _process.children(recursive=True):
            try:
                total += child.memory_info().rss
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        return total / 1024 / 1024
    except Exception:
        return _process.memory_info().rss / 1024 / 1024


async def _memory_monitor():
    """Фонова задача - кожну секунду оновлює пікове значення RAM."""
    global _RAM_PEAK_MB
    while True:
        try:
            ram_mb = _get_total_ram_mb()
            if ram_mb > _RAM_PEAK_MB:
                _RAM_PEAK_MB = ram_mb
        except Exception:
            pass
        await asyncio.sleep(1)


UNKNOWN_MSG = "Вибач але не можу зрозуміти твого запиту, виклич команду /start для початку роботи."

# TTL кеш для inline пошуку - щоб не бити MongoDB на кожен символ
# Структура: {user_id: {"data": {...}, "updated_at": float}}
_MANGA_CACHE: dict[str, dict] = {}
_CACHE_TTL = 60  # секунд


async def _get_cached_manga(context: ContextTypes.DEFAULT_TYPE, user_id: str) -> dict:
    """Повертає список манг з кешу для user_id, або завантажує з MongoDB якщо кеш застарів."""
    now = time.time()
    entry = _MANGA_CACHE.get(user_id)
    if not entry or now - entry["updated_at"] > _CACHE_TTL:
        repo: AbstractRepository = context.bot_data["repos"][user_id]
        data = await repo.load()
        _MANGA_CACHE[user_id] = {"data": data.get("manga", {}), "updated_at": now}
    return _MANGA_CACHE[user_id]["data"]


def _invalidate_manga_cache(user_id: str):
    """Примусово скидає кеш для user_id - викликати після додавання або видалення манги."""
    if user_id in _MANGA_CACHE:
        _MANGA_CACHE[user_id]["updated_at"] = 0.0


# Стани діалогів
ADD_TITLE, ADD_URL = range(2)
REMOVE_SEARCH, REMOVE_CONFIRM = range(2, 4)


# Захист від сторонніх

def _make_owner_guard(conv: bool):
    """Фабрика декораторів захисту від сторонніх.
    conv=False - для звичайних хендлерів (повертає None).
    conv=True  - для ConversationHandler entry_points (повертає END)."""
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if str(update.effective_user.id) != str(CHAT_ID):
                if update.callback_query:
                    await update.callback_query.answer("⛔ Немає доступу.", show_alert=True)
                else:
                    await update.effective_message.reply_text("⛔ Немає доступу.")
                return ConversationHandler.END if conv else None
            return await func(update, context)
        return wrapper
    return decorator


# Для звичайних хендлерів - повертати None при відмові
owner_only = _make_owner_guard(conv=False)

# Для entry_points ConversationHandler - повертає END при відмові
owner_only_conv = _make_owner_guard(conv=True)

# Для адмін-команд - завжди перевіряти тільки CHAT_ID
def admin_only(func):
    @functools.wraps(func)
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) != str(CHAT_ID):
            if update.callback_query:
                await update.callback_query.answer("⛔ Немає доступу.", show_alert=True)
            else:
                await update.effective_message.reply_text("⛔ Немає доступу.")
            return
        return await func(update, context)
    return wrapper


# /start

@owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = InlineKeyboardMarkup([
        [InlineKeyboardButton("🔎 Пошук", switch_inline_query_current_chat="")],
        [
            InlineKeyboardButton("📚 Статус", callback_data="start_status"),
            InlineKeyboardButton("🔍 Перевірити", callback_data="start_check"),
        ],
        [
            InlineKeyboardButton("➕ Додати", callback_data="start_add"),
            InlineKeyboardButton("🗑 Видалити", callback_data="start_remove"),
        ],
    ])
    await update.effective_message.reply_text(
        "Привіт! Я слідкую за новими главами манги.",
        reply_markup=keyboard
    )


# Статус з пагінацією

PAGE_SIZE = 10


def _build_status_page(manga: dict, last_check: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    items = list(manga.items())
    total = len(items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))
    chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    lines = [f"📚 Манги - {total} шт.\nОстання перевірка: {last_check} - сторінка {page + 1}/{total_pages}\n"]
    for title, info in chunk:
        chapter = info.get("last_chapter", "невідомо")
        url = info.get("url", "")
        lines.append(f"• {title}")
        lines.append(f"  Глава: {chapter}")
        lines.append(f"  {url}\n")

    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("◀️ Назад", callback_data=f"status:{page - 1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Вперед ▶️", callback_data=f"status:{page + 1}"))

    action_buttons = [
        InlineKeyboardButton("➕ Додати", callback_data="start_add"),
        InlineKeyboardButton("🗑 Видалити", callback_data="start_remove"),
    ]
    search_button = [InlineKeyboardButton("🔎 Пошук", switch_inline_query_current_chat="")]

    rows = []
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append(action_buttons)
    rows.append(search_button)

    return "\n".join(lines), InlineKeyboardMarkup(rows)


async def _show_status(message: Message, context: ContextTypes.DEFAULT_TYPE):
    repo: AbstractRepository = context.bot_data["repos"][str(message.chat_id)]
    data = await repo.load()
    manga = data.get("manga", {})
    if not manga:
        await message.reply_text("Список манг порожній.")
        return
    # Зберігаємо дані в user_data
    context.user_data["status_manga"] = manga
    context.user_data["status_last_check"] = data.get("last_check_date", "ніколи")
    text, keyboard = _build_status_page(manga, context.user_data["status_last_check"], page=0)
    await message.reply_text(text, reply_markup=keyboard, disable_web_page_preview=True)


@owner_only
async def cb_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    page = int(query.data.split(":")[1])
    manga = context.user_data.get("status_manga")
    last_check = context.user_data.get("status_last_check", "ніколи")
    if not manga:
        # Кеш відсутній (наприклад після перезапуску бота) - завантажуємо з БД
        repo: AbstractRepository = context.bot_data["repos"][str(update.effective_user.id)]
        data = await repo.load()
        manga = data.get("manga", {})
        last_check = data.get("last_check_date", "ніколи")
        context.user_data["status_manga"] = manga
        context.user_data["status_last_check"] = last_check
    text, keyboard = _build_status_page(manga, last_check, page=page)
    await query.edit_message_text(text, reply_markup=keyboard, disable_web_page_preview=True)


@owner_only
async def cb_start_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _show_status(update.effective_message, context)


# Перевірка

async def _run_check_command(message: Message, context: ContextTypes.DEFAULT_TYPE):
    # Захист від паралельного запуску
    if context.bot_data.get("check_running"):
        await message.reply_text("⏳ Перевірка вже виконується, зачекай...")
        return
    context.bot_data["check_running"] = True
    user_id = str(message.chat_id)
    try:
        repo: AbstractRepository = context.bot_data["repos"][user_id]
        # Завантажуємо дані один раз - передаємо в run_check щоб уникнути подвійного запиту
        data = await repo.load()
        manga = data.get("manga", {})
        if not manga:
            await message.reply_text("Список манг порожній.")
            return
        ram_before = _get_total_ram_mb()
        log(f"📊 RAM до перевірки: {ram_before:.1f} MB")
        await message.reply_text(f"🔍 Перевіряю {len(manga)} манг, зачекай...")
        report_text = await run_check(repo=repo, preloaded_data=data)
        ram_after = _get_total_ram_mb()
        log(f"📊 RAM після перевірки: {ram_after:.1f} MB | пік сесії: {_RAM_PEAK_MB:.1f} MB")
        _invalidate_manga_cache(user_id)
        await message.reply_text(report_text, disable_web_page_preview=True)
    finally:
        context.bot_data["check_running"] = False


@owner_only
async def cb_start_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await _run_check_command(update.effective_message, context)


# Діалог: Додати мангу

@owner_only_conv
async def cb_start_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    await update.effective_message.reply_text("Введи назву манги:\n/cancel - скасувати")
    return ADD_TITLE


async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.effective_message.text.strip()
    user_id = str(update.effective_user.id)
    repo: AbstractRepository = context.bot_data["repos"][user_id]
    data = await repo.load()
    if title in data["manga"]:
        await update.effective_message.reply_text(f"⚠️ «{title}» вже є в списку.")
        return ConversationHandler.END
    context.user_data["add_title"] = title
    await update.effective_message.reply_text(
        f"Назва: «{title}»\n\nТепер введи URL:\n/cancel - скасувати"
    )
    return ADD_URL


async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.effective_message.text.strip()

    if not (url.startswith("http://") or url.startswith("https://")):
        await update.effective_message.reply_text(
            "⚠️ Невірний URL. Посилання має починатись з http:// або https://\n"
            "Введи URL ще раз або /cancel:"
        )
        return ADD_URL

    title = context.user_data.pop("add_title", None)
    if not title:
        return ConversationHandler.END
    user_id = str(update.effective_user.id)
    repo: AbstractRepository = context.bot_data["repos"][user_id]
    await repo.add_manga(title, url)
    _invalidate_manga_cache(user_id)
    context.user_data.pop("status_manga", None)
    await update.effective_message.reply_text(f"✅ «{title}» додано!")
    return ConversationHandler.END


# Діалог: Видалити мангу

@owner_only_conv
async def cb_start_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    repo: AbstractRepository = context.bot_data["repos"][str(update.effective_user.id)]
    data = await repo.load()
    if not data["manga"]:
        await update.effective_message.reply_text("Список манг порожній.")
        return ConversationHandler.END
    await update.effective_message.reply_text(
        "Введи назву манги (або частину назви):\n/cancel - скасувати"
    )
    return REMOVE_SEARCH


async def remove_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.effective_message.text.strip().lower()
    repo: AbstractRepository = context.bot_data["repos"][str(update.effective_user.id)]
    data = await repo.load()
    matches = [t for t in data["manga"] if query_text in t.lower()]

    if not matches:
        await update.effective_message.reply_text(
            "⚠️ Нічого не знайдено. Спробуй інше слово або /cancel:"
        )
        return REMOVE_SEARCH

    if len(matches) == 1:
        context.user_data["remove_pending"] = matches[0]
        await update.effective_message.reply_text(
            f"Видалити «{matches[0]}»?",
            reply_markup=_confirm_keyboard()
        )
        return REMOVE_CONFIRM

    context.user_data["remove_matches"] = matches
    lines = ["Знайдено кілька манг, введи номер:"]
    for i, t in enumerate(matches, 1):
        lines.append(f"{i}. {t}")
    lines.append("\n/cancel - скасувати")
    await update.effective_message.reply_text("\n".join(lines))
    return REMOVE_CONFIRM


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Так", callback_data="delconfirm:yes"),
        InlineKeyboardButton("❌ Ні", callback_data="delconfirm:no"),
    ]])


async def remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Вибір манги з нумерованого списку. Викликається тільки якщо знайдено > 1 результат."""
    text = update.effective_message.text.strip()
    matches = context.user_data.get("remove_matches", [])

    if not matches:
        await update.effective_message.reply_text(
            "⚠️ Натисни кнопку ✅ Так або ❌ Ні, або /cancel для скасування:"
        )
        return REMOVE_CONFIRM

    if not text.isdigit():
        await update.effective_message.reply_text("⚠️ Введи номер зі списку або /cancel:")
        return REMOVE_CONFIRM

    idx = int(text) - 1
    if idx < 0 or idx >= len(matches):
        await update.effective_message.reply_text(f"⚠️ Введи число від 1 до {len(matches)} або /cancel:")
        return REMOVE_CONFIRM

    title = matches[idx]
    context.user_data["remove_pending"] = title
    await update.effective_message.reply_text(
        f"Видалити «{title}»?",
        reply_markup=_confirm_keyboard()
    )
    return REMOVE_CONFIRM


async def cb_remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    if str(query.from_user.id) != str(CHAT_ID):
        return ConversationHandler.END
    action = query.data.split(":", 1)[1]
    pending = context.user_data.pop("remove_pending", None)
    context.user_data.pop("remove_matches", None)

    if action == "yes" and pending:
        user_id = str(query.from_user.id)
        repo: AbstractRepository = context.bot_data["repos"][user_id]
        await repo.remove_manga(pending)
        _invalidate_manga_cache(user_id)
        context.user_data.pop("status_manga", None)
        await query.edit_message_text(f"🗑 «{pending}» видалено зі списку.")
    else:
        await query.edit_message_text("Скасовано.")
    return ConversationHandler.END


async def cancel_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.pop("add_title", None)
    context.user_data.pop("remove_matches", None)
    context.user_data.pop("remove_pending", None)
    await update.effective_message.reply_text("Скасовано.")
    return ConversationHandler.END


# Невідома команда / текст поза діалогом

@owner_only
async def cmd_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(UNKNOWN_MSG)


@owner_only
async def handle_unknown_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message and update.message.via_bot:
        return
    await update.effective_message.reply_text(UNKNOWN_MSG)


# Inline пошук

async def inline_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.inline_query
    user_id = str(query.from_user.id)
    if user_id not in context.bot_data["repos"]:
        await query.answer([], cache_time=0)
        return

    query_text = (query.query or "").strip().lower()
    manga = await _get_cached_manga(context, user_id)

    matches = {t: info for t, info in manga.items() if query_text in t.lower()} if query_text else manga

    results = []
    for i, (title, info) in enumerate(list(matches.items())[:50]):
        chapter = info.get("last_chapter", "невідомо")
        url = info.get("url", "")
        results.append(
            InlineQueryResultArticle(
                id=str(i),
                title=title,
                description=f"Глава: {chapter}",
                input_message_content=InputTextMessageContent(
                    message_text=f"📖 {title}\n\nГлава: {chapter}\n{url}",
                    disable_web_page_preview=True,
                ),
            )
        )

    await query.answer(results, cache_time=0, is_personal=True)



# /stats helpers
def _get_stats_process() -> str:
    global _RAM_PEAK_MB
    KOYEB_LIMIT_MB = 512
    ram_mb = _get_total_ram_mb()
    if ram_mb > _RAM_PEAK_MB:
        _RAM_PEAK_MB = ram_mb
    ram_warning = " ⚠️" if ram_mb > KOYEB_LIMIT_MB * 0.8 else ""
    cpu = _process.cpu_percent(interval=0.5)
    ram_mb_proc = _process.memory_info().rss / 1024 / 1024
    uptime = str(datetime.timedelta(seconds=int(time.time() - _BOT_START_TIME)))
    status = _process.status()
    return "\n".join([
        "⚙️ Процес бота",
        f"  Статус: {status}",
        f"  RAM (процес): {ram_mb_proc:.1f} MB",
        f"  RAM (з Chromium): {ram_mb:.1f} MB / ліміт {KOYEB_LIMIT_MB} MB{ram_warning}",
        f"  RAM пік (з Chromium): {_RAM_PEAK_MB:.1f} MB",
        f"  CPU: {cpu:.1f}%",
        f"  Uptime: {uptime}",
    ])


def _get_stats_server() -> str:
    boot_time = datetime.datetime.fromtimestamp(psutil.boot_time()).strftime("%Y-%m-%d %H:%M:%S")
    users = psutil.users()
    users_str = ", ".join(u.name for u in users) if users else "немає"
    return "\n".join([
        "🖥 Сервер",
        f"  Останнє завантаження: {boot_time}",
        f"  Залогінені користувачі: {users_str}",
    ])


def _get_stats_network() -> str:
    net = psutil.net_io_counters()
    try:
        conns = len([c for c in psutil.net_connections() if c.pid == _process.pid])
    except psutil.AccessDenied:
        conns = "н/д"
    return "\n".join([
        "🌐 Мережа",
        f"  Надіслано: {net.bytes_sent / 1024 / 1024:.1f} MB",
        f"  Отримано: {net.bytes_recv / 1024 / 1024:.1f} MB",
        f"  Активних з'єднань (процес): {conns}",
    ])


def _stats_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("⚙️ Процес", callback_data="stats:process"),
        InlineKeyboardButton("🖥 Сервер", callback_data="stats:server"),
        InlineKeyboardButton("🌐 Мережа", callback_data="stats:network"),
    ]])


# /stats
@admin_only
async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "📊 Статистика - обери розділ:",
        reply_markup=_stats_keyboard()
    )


@admin_only
async def cb_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    try:
        await query.answer()
    except Exception:
        pass
    section = query.data.split(":")[1]
    if section == "process":
        text = _get_stats_process()
    elif section == "server":
        text = _get_stats_server()
    else:
        text = _get_stats_network()
    try:
        await query.edit_message_text(text, reply_markup=_stats_keyboard())
    except Exception:
        pass


# Запуск

def _handle_signal(sig, frame):
    log(f"⚠️ Отримано сигнал {sig} - завершуємо бота...")
    _shutdown_event.set()
    raise SystemExit(0)


def run_bot():
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    repo = get_repository(user_id=CHAT_ID)
    app = ApplicationBuilder().token(TOKEN).build()
    app.bot_data["repos"] = {str(CHAT_ID): repo}

    add_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_start_add, pattern=r"^start_add$")],
        states={
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
            ADD_URL:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_url)],
        },
        fallbacks=[CommandHandler("cancel", cancel_dialog)],
        per_message=False,
    )

    remove_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(cb_start_remove, pattern=r"^start_remove$")],
        states={
            REMOVE_SEARCH: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_search)],
            REMOVE_CONFIRM: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, remove_confirm),
                CallbackQueryHandler(cb_remove_confirm, pattern=r"^delconfirm:"),
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel_dialog)],
        per_message=False,
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(add_conv)
    app.add_handler(remove_conv)
    app.add_handler(CallbackQueryHandler(cb_status, pattern=r"^status:"))
    app.add_handler(CallbackQueryHandler(cb_stats, pattern=r"^stats:"))
    app.add_handler(CallbackQueryHandler(cb_start_status, pattern=r"^start_status$"))
    app.add_handler(CallbackQueryHandler(cb_start_check, pattern=r"^start_check$"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_unknown_text))
    app.add_handler(MessageHandler(filters.COMMAND, cmd_unknown))
    app.add_handler(InlineQueryHandler(inline_search))

    async def on_shutdown(app):
        task = app.bot_data.get("monitor_task")
        if task and not task.done():
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
        log("🛑 Моніторинг RAM зупинено")
        for r in app.bot_data["repos"].values():
            r.close()
        log("🛑 З'єднання з MongoDB закрито")

    async def on_startup(app):
        for r in app.bot_data["repos"].values():
            await r.setup()
        await app.bot.set_my_commands([
            ("start", "Меню"),
            ("stats", "Статистика сервера"),
        ])
        app.bot_data["monitor_task"] = asyncio.create_task(_memory_monitor())
        log("🔍 Фоновий моніторинг RAM запущено")

    app.post_init = on_startup
    app.post_shutdown = on_shutdown

    try:
        app.run_polling()
    except SystemExit:
        log("🛑 Бот завершено коректно.")
    except Exception as e:
        log(f"❌ Критична помилка бота: {e}")
        raise


if __name__ == "__main__":
    run_bot()