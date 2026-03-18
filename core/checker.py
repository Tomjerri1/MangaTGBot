"""
Спільна логіка перевірки манг.
Використовує Dependency Injection через AbstractRepository.
"""
from datetime import datetime

from core.parser_playwright import check_all
from core.logger import get_logger
from core.repository import AbstractRepository

log = get_logger("checker").info


def _normalize_chapter(value: str) -> str:
    """Нормалізує номер глави до єдиного формату для коректного порівняння.

    "1.0" -> "1", "010" -> "10", "1.5" -> "1.5"
    "99 extra" -> "99 extra" (не число — повертає stripped оригінал)
    """
    try:
        num = float(value)
        return str(int(num)) if num == int(num) else str(num)
    except (ValueError, TypeError):
        return value.strip()


async def run_check(repo: AbstractRepository, preloaded_data: dict | None = None) -> str:
    # Якщо дані вже завантажені, не робити зайвий запит до MongoDB
    data = preloaded_data if preloaded_data is not None else await repo.load()
    manga_urls = {title: info["url"] for title, info in data["manga"].items()}
    old_chapters = {title: info["last_chapter"] for title, info in data["manga"].items()}

    results = await check_all(manga_urls)

    new_lines = []
    error_lines = []

    for title, new_chapter in results.items():
        if title not in data["manga"]:
            log(f"  ℹ️ {title} — видалена під час перевірки, пропускаємо")
            continue

        old_chapter = _normalize_chapter(old_chapters.get(title, "невідомо"))
        new_chapter = _normalize_chapter(new_chapter) if new_chapter else "невідомо"
        url = data["manga"][title]["url"]

        if new_chapter == "невідомо":
            error_lines.append(f"⚠️ {title} — не вдалося перевірити\n  {url}")
            continue

        if new_chapter != old_chapter:
            # Якщо обидва числа - нова глава тільки якщо номер більший
            # Захист від помилкових сповіщень коли сайт повертає некоректний номер
            try:
                if float(new_chapter) < float(old_chapter):
                    log(f"  ⚠️ {title}: нова глава ({new_chapter}) менша за стару ({old_chapter}) — пропускаємо")
                    continue
            except (ValueError, TypeError):
                pass  # нечислові значення ("99 extra") - порівнюємо як рядки, вже перевірили != вище

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

    return "\n".join(report_lines)