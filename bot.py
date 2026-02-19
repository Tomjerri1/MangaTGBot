"""
Telegram бот для керування списком манг.

Команди:
  /start   - привітання і список команд
  /status  - поточний список манг з останніми главами і посиланнями
  /add     - додати мангу: /add Назва | https://сайт.com/manga
  /remove  - видалити мангу: /remove Назва
  /check   - перевірити зараз
"""

import paths
import signal

from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

from config.config import TOKEN, CHAT_ID
from core.storage import load_data, save_data
from core.checker import run_check
from core.logger import get_logger

log = get_logger("bot").info

def owner_only(func):
    """Декоратор - ігнорує команди від будь-кого крім власника"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if str(update.effective_user.id) != str(CHAT_ID):
            await update.message.reply_text("Немає доступу.")
            return
        return await func(update, context)
    return wrapper

@owner_only
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "Привіт! Я слідкую за новими главами манги.\n\n"
        "Команди:\n"
        "/status - список манг і останні глави\n"
        "/add Назва | URL - додати мангу\n"
        "/remove Назва - видалити мангу\n"
        "/check - перевірити зараз"
    )
    await update.message.reply_text(text)


@owner_only
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    manga = data.get("manga", {})

    if not manga:
        await update.message.reply_text("Список манг порожній.")
        return

    last_check = data.get("last_check_date", "ніколи")
    lines = [f"Манги (остання перевірка: {last_check})\n"]
    for title, info in manga.items():
        chapter = info.get("last_chapter", "невідомо")
        url = info.get("url", "")
        lines.append(f"• {title}")
        lines.append(f"  Глава: {chapter}")
        lines.append(f"  {url}\n")

    await update.message.reply_text("\n".join(lines), disable_web_page_preview=True)


@owner_only 
async def cmd_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    raw = " ".join(context.args)
    if "|" not in raw:
        await update.effective_message.reply_text(
            "Формат: /add Назва | URL\n"
            "Приклад: /add Моя манга | https://manga/manga/test"
        )
        return

    parts = raw.split("|", 1)
    title = parts[0].strip()
    url = parts[1].strip()

    if not title or not url.startswith("http"):
        await update.effective_message.reply_text("Невірний формат. Назва і URL не можуть бути порожніми.")
        return

    data = load_data()
    if title in data["manga"]:
        await update.effective_message.reply_text(f" ! «{title}» вже є в списку.")
        return

    data["manga"][title] = {"url": url, "last_chapter": "невідомо"}
    save_data(data)
    await update.effective_message.reply_text(f" ✓ «{title}» додано!\nURL: {url}", disable_web_page_preview=True)


@owner_only
async def cmd_remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = " ".join(context.args).strip()
    if not title:
        await update.message.reply_text(" ! Формат: /remove Назва")
        return

    data = load_data()
    if title not in data["manga"]:
        names = "\n".join(f"• {t}" for t in data["manga"])
        await update.message.reply_text(
            f" ! «{title}» не знайдено.\n\nДоступні манги:\n{names}"
        )
        return

    del data["manga"][title]
    save_data(data)
    await update.message.reply_text(f"🗑 «{title}» видалено зі списку.")


@owner_only
async def cmd_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = load_data()
    manga = data.get("manga", {})

    if not manga:
        await update.message.reply_text("Список манг порожній.")
        return

    await update.message.reply_text(f"Перевіряю {len(manga)} манг, зачекай...")

    report_text, errors = await run_check()
    await update.message.reply_text(report_text, disable_web_page_preview=True)

    if errors:
        error_text = "Не вдалося перевірити:\n" + "\n".join(f"  • {t}" for t in errors)
        await update.message.reply_text(error_text)


def _handle_signal(sig, frame):
    """Коректне завершення при зупинці процесу"""
    log(f" ! Отримано сигнал {sig} - завершуємо бота...")
    raise SystemExit(0)


def run_bot():
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("add", cmd_add))
    app.add_handler(CommandHandler("remove", cmd_remove))
    app.add_handler(CommandHandler("check", cmd_check))

    try:
        app.run_polling()
    except SystemExit:
        log("Бот завершено коректно.")
    except Exception as e:
        log(f"Критична помилка бота: {e}")
        raise


if __name__ == "__main__":
    run_bot()