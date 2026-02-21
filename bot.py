"""
Manga Tracker Bot — єдина точка входу.

Команди:
  /start   — привітання і список команд
  /status  — поточний список манг з останніми главами і посиланнями
  /add     — додати мангу (покроковий діалог)
  /remove  — видалити мангу (покроковий діалог)
  /check   — запустити перевірку зараз
"""

import sys
import os
import signal

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler,
    CallbackQueryHandler, ConversationHandler, ContextTypes, filters
)

from config.config import TOKEN, CHAT_ID
from core.repository import get_repository, AbstractRepository
from core.checker import run_check
from core.logger import get_logger

log = get_logger("bot").info

# Стани діалогів
ADD_TITLE, ADD_URL = range(2)
REMOVE_SEARCH, REMOVE_CONFIRM = range(2, 4)

def owner_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) != str(CHAT_ID):
            if update.callback_query:
                await update.callback_query.answer("⛔ Немає доступу.", show_alert=True)
            else:
                await update.effective_message.reply_text("⛔ Немає доступу.")
            return
        return await func(update, context)
    return wrapper


@owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Привіт! Я слідкую за новими главами манги.\n\n"
        "Команди:\n"
        "/status — список манг і останні глави\n"
        "/add — додати мангу\n"
        "/remove — видалити мангу\n"
        "/check — перевірити зараз"
    )
    await update.effective_message.reply_text(text)


# /status з пагінацією

PAGE_SIZE = 10


def _build_status_page(manga: dict, last_check: str, page: int) -> tuple[str, InlineKeyboardMarkup]:
    items = list(manga.items())
    total = len(items)
    total_pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(0, min(page, total_pages - 1))

    chunk = items[page * PAGE_SIZE:(page + 1) * PAGE_SIZE]

    lines = [f"📚 Манги — {total} шт.\nОстання перевірка: {last_check} — сторінка {page + 1}/{total_pages}\n"]
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

    rows = []
    if nav_buttons:
        rows.append(nav_buttons)
    rows.append(action_buttons)

    return "\n".join(lines), InlineKeyboardMarkup(rows)


@owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    repo: AbstractRepository = context.bot_data["repo"]
    data = await repo.load()
    manga = data.get("manga", {})

    if not manga:
        await update.effective_message.reply_text("Список манг порожній.")
        return

    text, keyboard = _build_status_page(manga, data.get("last_check_date", "ніколи"), page=0)
    await update.effective_message.reply_text(text, reply_markup=keyboard, disable_web_page_preview=True)


@owner_only
async def cb_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    page = int(query.data.split(":")[1])
    repo: AbstractRepository = context.bot_data["repo"]
    data = await repo.load()
    manga = data.get("manga", {})

    text, keyboard = _build_status_page(manga, data.get("last_check_date", "ніколи"), page=page)
    await query.edit_message_text(text, reply_markup=keyboard, disable_web_page_preview=True)

def _is_busy(context: ContextTypes.DEFAULT_TYPE) -> bool:
    return context.user_data.get("in_dialog", False)


async def _busy_reply(update: Update):
    msg = "⏳ Спочатку заверши поточну дію або напиши /cancel."
    if update.callback_query:
        await update.callback_query.answer(msg, show_alert=True)
    else:
        await update.effective_message.reply_text(msg)


def _clear_dialog_state(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Очищає всі дані активного діалогу"""
    context.user_data["in_dialog"] = False
    context.user_data.pop("conv_state", None)
    context.user_data.pop("add_title", None)
    context.user_data.pop("remove_matches", None)
    context.user_data.pop("remove_pending", None)


@owner_only
async def cb_start_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка ➕ Додати"""
    query = update.callback_query
    await query.answer()

    if _is_busy(context):
        await _busy_reply(update)
        return

    context.user_data["in_dialog"] = True
    context.user_data["conv_state"] = ADD_TITLE
    await update.effective_message.reply_text("Введи назву манги:\n/cancel — скасувати")


@owner_only
async def cb_start_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Кнопка 🗑 Видалити"""
    query = update.callback_query
    await query.answer()

    if _is_busy(context):
        await _busy_reply(update)
        return

    repo: AbstractRepository = context.bot_data["repo"]
    data = await repo.load()

    if not data["manga"]:
        await update.effective_message.reply_text("Список манг порожній.")
        return

    context.user_data["in_dialog"] = True
    context.user_data["conv_state"] = REMOVE_SEARCH
    await update.effective_message.reply_text("Введи назву манги (або частину назви):\n/cancel — скасувати")


# /add
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(CHAT_ID):
        await update.effective_message.reply_text("⛔ Немає доступу.")
        return ConversationHandler.END

    if _is_busy(context):
        await _busy_reply(update)
        return ConversationHandler.END

    context.user_data["in_dialog"] = True
    await update.effective_message.reply_text("Введи назву манги:")
    return ADD_TITLE


async def add_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.effective_message.text.strip()

    repo: AbstractRepository = context.bot_data["repo"]
    data = await repo.load()

    if title in data["manga"]:
        await update.effective_message.reply_text(f"⚠️ «{title}» вже є в списку.")
        return ConversationHandler.END

    context.user_data["add_title"] = title
    await update.effective_message.reply_text(f"Назва: «{title}»\n\nТепер введи URL:\n/cancel — скасувати")
    return ADD_URL


async def add_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.effective_message.text.strip()

    if not url.startswith("http"):
        await update.effective_message.reply_text("⚠️ Невірний URL. Має починатись з http. Спробуй ще раз:")
        return ADD_URL

    title = context.user_data.pop("add_title", None)
    if not title:
        await update.effective_message.reply_text("⚠️ Щось пішло не так. Почни знову: /add")
        return ConversationHandler.END

    repo: AbstractRepository = context.bot_data["repo"]
    await repo.add_manga(title, url)
    _clear_dialog_state(context)
    await update.effective_message.reply_text(f"✅ «{title}» додано!\nURL: {url}", disable_web_page_preview=True)
    return ConversationHandler.END


async def add_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_dialog_state(context)
    await update.effective_message.reply_text("Скасовано.")
    return ConversationHandler.END


# /remove
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if str(update.effective_user.id) != str(CHAT_ID):
        await update.effective_message.reply_text("⛔ Немає доступу.")
        return ConversationHandler.END

    if _is_busy(context):
        await _busy_reply(update)
        return ConversationHandler.END

    context.user_data["in_dialog"] = True
    repo: AbstractRepository = context.bot_data["repo"]
    data = await repo.load()

    if not data["manga"]:
        await update.effective_message.reply_text("Список манг порожній.")
        _clear_dialog_state(context)
        return ConversationHandler.END

    await update.effective_message.reply_text("Введи назву манги (або частину назви):")
    return REMOVE_SEARCH


async def remove_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query_text = update.effective_message.text.strip().lower()
    repo: AbstractRepository = context.bot_data["repo"]
    data = await repo.load()

    matches = [t for t in data["manga"] if query_text in t.lower()]

    if not matches:
        await update.effective_message.reply_text(
            "⚠️ Нічого не знайдено. Спробуй інше слово або /cancel для скасування:"
        )
        return REMOVE_SEARCH

    if len(matches) == 1:
        context.user_data["remove_pending"] = matches[0]
        await update.effective_message.reply_text(
            f"Видалити «{matches[0]}»?",
            reply_markup=_confirm_keyboard()
        )
        return REMOVE_CONFIRM

    # Зберігаємо назви в user_data передаємо тільки індекс в callback_data
    # бо Telegram має ліміт 64 байти на callback_data
    context.user_data["remove_matches"] = matches
    lines = ["Знайдено кілька манг, введи номер:"]
    for i, t in enumerate(matches, 1):
        lines.append(f"{i}. {t}")
    lines.append("\n/cancel — скасувати")
    await update.effective_message.reply_text("\n".join(lines))
    return REMOVE_CONFIRM


def _confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([[
        InlineKeyboardButton("✅ Так", callback_data="delconfirm:yes"),
        InlineKeyboardButton("❌ Ні", callback_data="delconfirm:no"),
    ]])


async def remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.effective_message.text.strip()
    matches = context.user_data.get("remove_matches", [])
    pending = context.user_data.get("remove_pending")

    if not text.isdigit():
        await update.effective_message.reply_text("⚠️ Введи номер зі списку або /cancel для скасування:")
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


async def remove_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    _clear_dialog_state(context)
    await update.effective_message.reply_text("Скасовано.")
    return ConversationHandler.END


# /check
@owner_only
async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    repo: AbstractRepository = context.bot_data["repo"]
    data = await repo.load()
    manga = data.get("manga", {})

    if not manga:
        await update.effective_message.reply_text("Список манг порожній.")
        return

    await update.effective_message.reply_text(f"🔍 Перевіряю {len(manga)} манг, зачекай...")

    report_text, errors = await run_check(repo=repo)
    await update.effective_message.reply_text(report_text, disable_web_page_preview=True)

    if errors:
        error_text = "🚨 Не вдалося перевірити:\n" + "\n".join(f"  • {t}" for t in errors)
        await update.effective_message.reply_text(error_text)

async def route_dialog(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Роутер для діалогів запущених через кнопки (поза ConversationHandler).
    conv_state оновлюється всередині кожної функції через context.user_data."""
    state = context.user_data.get("conv_state")
    if state is None:
        return

    next_state = None
    if state == ADD_TITLE:
        next_state = await add_title(update, context)
    elif state == ADD_URL:
        next_state = await add_url(update, context)
    elif state == REMOVE_SEARCH:
        next_state = await remove_search(update, context)
    elif state == REMOVE_CONFIRM:
        next_state = await remove_confirm(update, context)

    if next_state is not None and next_state != ConversationHandler.END:
        context.user_data["conv_state"] = next_state
    elif next_state == ConversationHandler.END:
        _clear_dialog_state(context)


@owner_only
async def cb_remove_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обробник кнопок Так/Ні при підтвердженні видалення"""
    query = update.callback_query
    await query.answer()

    action = query.data.split(":", 1)[1]
    pending = context.user_data.pop("remove_pending", None)

    if action == "yes" and pending:
        repo: AbstractRepository = context.bot_data["repo"]
        await repo.remove_manga(pending)
        _clear_dialog_state(context)
        await query.edit_message_text(f"🗑 «{pending}» видалено зі списку.")
    else:
        _clear_dialog_state(context)
        await query.edit_message_text("Скасовано.")


async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Скасування кнопкового діалогу (для діалогів запущених через кнопки)"""
    if context.user_data.get("conv_state") is not None:
        _clear_dialog_state(context)
        await update.effective_message.reply_text("Скасовано.")


@owner_only
async def cmd_unknown(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        "Невідома команда. Доступні команди:\n"
        "/status — список манг і останні глави\n"
        "/add — додати мангу\n"
        "/remove — видалити мангу\n"
        "/check — перевірити зараз"
    )


def _handle_signal(sig, frame):
    log(f"⚠️ Отримано сигнал {sig} — завершуємо бота...")
    raise SystemExit(0)


def run_bot():
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    repo = get_repository(user_id=CHAT_ID)

    app = ApplicationBuilder().token(TOKEN).build()
    app.bot_data["repo"] = repo

    # ConversationHandler для /add
    add_conv = ConversationHandler(
        entry_points=[CommandHandler("add", cmd_add)],
        states={
            ADD_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, add_title)],
            ADD_URL:   [MessageHandler(filters.TEXT & ~filters.COMMAND, add_url)],
        },
        fallbacks=[CommandHandler("cancel", add_cancel)],
        per_message=False,
    )

    # ConversationHandler для /remove
    remove_conv = ConversationHandler(
        entry_points=[CommandHandler("remove", cmd_remove)],
        states={
            REMOVE_SEARCH:  [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_search)],
            REMOVE_CONFIRM: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_confirm)],
        },
        fallbacks=[CommandHandler("cancel", remove_cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("check", cmd_check))
    app.add_handler(add_conv)
    app.add_handler(remove_conv)
    app.add_handler(CallbackQueryHandler(cb_status, pattern=r"^status:"))
    app.add_handler(CallbackQueryHandler(cb_start_add, pattern=r"^start_add$"))
    app.add_handler(CallbackQueryHandler(cb_start_remove, pattern=r"^start_remove$"))
    app.add_handler(CallbackQueryHandler(cb_remove_confirm, pattern=r"^delconfirm:"))
    app.add_handler(CommandHandler("cancel", cmd_cancel))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, route_dialog))
    app.add_handler(MessageHandler(filters.COMMAND, cmd_unknown))

    async def on_startup(app):
        # Очищаємо stale діалогові стани після перезапуску бота
        # (якщо бот впав під час діалогу — conv_state міг залишитись)
        if hasattr(app, "user_data"):
            for user_data in app.user_data.values():
                user_data.pop("conv_state", None)
                user_data.pop("in_dialog", None)
                user_data.pop("add_title", None)
                user_data.pop("remove_matches", None)
                user_data.pop("remove_pending", None)
        await repo.setup()
        await app.bot.set_my_commands([
            ("start",  "Привітання і список команд"),
            ("status", "Список манг і останні глави"),
            ("check",  "Перевірити нові глави зараз"),
            ("add",    "Додати мангу"),
            ("remove", "Видалити мангу"),
            ("cancel", "Скасувати поточну дію"),
        ])

    app.post_init = on_startup

    try:
        app.run_polling()
    except SystemExit:
        log("🛑 Бот завершено коректно.")
    except Exception as e:
        log(f"❌ Критична помилка бота: {e}")
        raise


if __name__ == "__main__":
    run_bot()