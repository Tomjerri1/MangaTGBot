"""
Спільна логіка перевірки манг.
Використовує Dependency Injection через AbstractRepository.
"""
from datetime import datetime

from core.parser_playwright import check_all
from core.logger import get_logger
from core.repository import AbstractRepository

log = get_logger("checker").info


async def run_check(repo: AbstractRepository, preloaded_data: dict | None = None) -> tuple[str, list[str]]:
    # Якщо дані вже завантажені (наприклад з bot.py) не робимо зайвий запит до MongoDB
    data = preloaded_data if preloaded_data is not None else await repo.load()
    manga_urls = {title: info["url"] for title, info in data["manga"].items()}
    old_chapters = {title: info["last_chapter"] for title, info in data["manga"].items()}

    results = await check_all(manga_urls)

    new_lines = []
    error_lines = []
    errors = []

    for title, new_chapter in results.items():
        if title not in data["manga"]:
            log(f"  ℹ️ {title} — видалена під час перевірки, пропускаємо")
            continue

        old_chapter = str(old_chapters.get(title, "невідомо"))
        new_chapter = str(new_chapter) if new_chapter else "невідомо"
        url = data["manga"][title]["url"]

        if new_chapter == "невідомо":
            error_lines.append(f"⚠️ {title} — не вдалося перевірити\n  {url}")
            errors.append(title)
            continue

        if new_chapter != old_chapter:
            new_lines.append(f"✅ {title} — нова глава: {new_chapter}  (була: {old_chapter})\n  {url}")
            await repo.update_chapter(title, new_chapter)

    await repo.set_last_check_date(datetime.now().strftime("%Y-%m-%d"))

    report_lines = [f"📚 Звіт за {datetime.now().strftime('%d.%m.%Y')}\n"]

    if new_lines:
        report_lines.extend(new_lines)
    else:
        report_lines.append("Нових глав немає.")

    if error_lines:
        report_lines.append("")
        report_lines.extend(error_lines)

    return "\n".join(report_lines), errors