"""
Спільна логіка перевірки манг використовується і в Main.py і в bot.py.
Містить файловий лок щоб уникнути одночасного запису з двох процесів.
"""
import os
from datetime import datetime
from filelock import FileLock, Timeout

from core.storage import load_data, save_data
from core.parser_playwright import check_all
from core.logger import get_logger

log = get_logger("checker").info

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOCK_FILE = os.path.join(_BASE_DIR, "data", "manga.lock")


async def run_check() -> tuple[str, list[str]]:
    """
    Перевіряє всі манги і оновлює data.json.
    Повертає (текст_звіту, список_помилок).
    """
    lock = FileLock(_LOCK_FILE, timeout=5)

    try:
        with lock:
            data = load_data()
            manga_urls = {title: info["url"] for title, info in data["manga"].items()}
            old_chapters = {title: info["last_chapter"] for title, info in data["manga"].items()}

    except Timeout:
        log(" ! Перевірка вже виконується в іншому процесі тому пропускаємо.")
        return " ! Перевірка вже виконується, спробуй пізніше.", []

    results = await check_all(manga_urls)

    try:
        with lock:
            fresh_data = load_data()

            report_lines = [f"Звіт за {datetime.now().strftime('%d.%m.%Y')}\n"]
            errors = []

            for title, new_chapter in results.items():
                if title not in fresh_data["manga"]:
                    log(f"  {title} - видалена під час перевірки, пропускаємо")
                    continue

                old_chapter = old_chapters.get(title, "невідомо")
                url = fresh_data["manga"][title]["url"]

                if not new_chapter or new_chapter == "невідомо":
                    report_lines.append(f" ! {title} - не вдалося перевірити\n  {url}")
                    errors.append(title)
                    continue

                if new_chapter != old_chapter:
                    report_lines.append(f" ✓ {title} - нова глава: {new_chapter}  (була: {old_chapter})\n  {url}")
                    fresh_data["manga"][title]["last_chapter"] = new_chapter
                else:
                    report_lines.append(f" {title} - нових глав немає (остання: {old_chapter})\n  {url}")

            fresh_data["last_check_date"] = datetime.now().strftime("%Y-%m-%d")
            save_data(fresh_data)

            return "\n".join(report_lines), errors

    except Timeout:
        log(" ! Не вдалося зберегти результати, інший процес тримає лок.")
        return " ! Перевірка завершена але не вдалося зберегти результати.", []