import asyncio
import re
import json
import os

import aiohttp
from dotenv import load_dotenv
from playwright.async_api import async_playwright

from core.logger import get_logger

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_PAGES", "10"))
# Скидаємо контекст браузера кожні N манг щоб уникнути memory leak
CONTEXT_RESET_EVERY = int(os.getenv("CONTEXT_RESET_EVERY", "20"))
# Максимальний час на одну мангу (секунди)
PAGE_TIMEOUT = int(os.getenv("PAGE_TIMEOUT", "120"))

log = get_logger("parser").info

API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

BLOCKED_RESOURCES = {"image", "media", "font", "stylesheet"}

SITE_PARSERS = {}


def register_parser(domain: str):
    """Декоратор для реєстрації парсера в SITE_PARSERS"""
    def decorator(func):
        SITE_PARSERS[domain] = func
        return func
    return decorator

_CHAPTER_RE = re.compile(
    r"(?:Глава|Розділ|Chapter)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE
)


def _find_last_chapter(text: str) -> float | None:
    """
    Знаходить номер глави в тексті.
    Використовує max() щоб уникнути хибних спрацювань:
    "Глава 5 (була Глава 4)" → знайде [5, 4] → поверне 5
    """
    matches = _CHAPTER_RE.findall(text)
    if matches:
        return max(float(m) for m in matches)
    return None


def retry(times: int = 3, delay: float = 2.0):
    def decorator(func):
        async def wrapper(page, url: str, *args, **kwargs):
            last_error = None
            for attempt in range(1, times + 1):
                try:
                    if attempt > 1:
                        log(f"  Перезавантаження перед спробою {attempt}...")
                        try:
                            await page.reload(timeout=30000, wait_until="domcontentloaded")
                        except Exception:
                            await page.goto(url, timeout=40000, wait_until="domcontentloaded")
                    return await func(page, url, *args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < times:
                        log(f"  ! Спроба {attempt}/{times} невдала: {e}. Повтор через {delay}с...")
                        await asyncio.sleep(delay)
            log(f"  Всі {times} спроби невдалі: {last_error}")
            return "невідомо"
        return wrapper
    return decorator


def _extract_comx_chapters(html: str) -> list[int]:
    """Regex fallback для com-x.life"""
    m = re.search(r'window\.__DATA__\s*=\s*(\{.*?\})\s*(?:;|</script>)', html, re.DOTALL)
    if not m:
        nums = re.findall(r'"posi"\s*:\s*(\d+)', html)
        return [int(n) for n in nums]
    try:
        data = json.loads(m.group(1))
        return [ch["posi"] for ch in data.get("chapters", []) if ch.get("posi")]
    except Exception:
        nums = re.findall(r'"posi"\s*:\s*(\d+)', m.group(1))
        return [int(n) for n in nums]


async def _extract_comx_chapters_js(page) -> list[int]:
    """JS виконання - основний спосіб для com-x.life"""
    try:
        data = await page.evaluate(
            "() => typeof window.__DATA__ !== 'undefined' ? window.__DATA__ : null"
        )
        if data and isinstance(data, dict):
            chapters = data.get("chapters", [])
            return [ch["posi"] for ch in chapters if ch.get("posi")]
    except Exception as e:
        log(f"  ! JS evaluate не спрацював: {e}, використовую regex fallback")
    return []


async def _parse_mangalib_api(url: str, session: aiohttp.ClientSession) -> str:
    """Асинхронний запит до API mangalib через спільну сесію"""
    m = re.search(r'/manga/([^/?#]+)', url)
    if not m:
        return "невідомо"
    slug = m.group(1)
    api_url = f"https://api.cdnlibs.org/api/manga/{slug}/chapters"
    log(f"  → API запит: {api_url}")
    try:
        async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            r.raise_for_status()
            data = await r.json()
            nums = []
            for ch in data.get("data", []):
                try:
                    nums.append(float(ch["number"]))
                except (KeyError, ValueError):
                    pass
            if nums:
                last = max(nums)
                return str(int(last)) if last == int(last) else str(last)
    except Exception as e:
        log(f"  API помилка: {e}")
    return "невідомо"


@register_parser("com-x.life")
@retry(times=3, delay=2.0)
async def _parse_comx(page, url: str) -> str:
    await page.goto(url, timeout=40000, wait_until="domcontentloaded")
    try:
        await page.wait_for_selector(
            "script:has-text('__DATA__'), .page__chapters-list",
            timeout=10000
        )
    except Exception:
        pass

    chapters = await _extract_comx_chapters_js(page)
    if not chapters:
        html = await page.content()
        chapters = _extract_comx_chapters(html)

    if chapters:
        result = str(max(chapters))
        log(f"  ✓ com-x.life: {result}")
        return result
    raise Exception("главу не знайдено")


@register_parser("mangabuff.ru")
@retry(times=3, delay=2.0)
async def _parse_mangabuff(page, url: str) -> str:
    await page.goto(url, timeout=40000, wait_until="domcontentloaded")
    try:
        await page.wait_for_selector("a[href*='/chapter/']", timeout=10000)
    except Exception:
        pass

    chapters = []

    links = await page.query_selector_all("a[href*='/chapter/']")
    for link in links:
        href = await link.get_attribute("href") or ""
        m = re.search(r"/chapter/(\d+(?:\.\d+)?)", href)
        if m:
            chapters.append(float(m.group(1)))

    if not chapters:
        links = await page.query_selector_all("a")
        for link in links:
            text = (await link.inner_text()).strip()
            num = _find_last_chapter(text)
            if num is not None:
                chapters.append(num)

    if chapters:
        last = max(chapters)
        result = str(int(last)) if last == int(last) else str(last)
        log(f"  ✓ mangabuff.ru: {result}")
        return result
    raise Exception("главу не знайдено")


@retry(times=3, delay=2.0)
async def _parse_fallback(page, url: str) -> str:
    await page.goto(url, timeout=40000, wait_until="domcontentloaded")
    try:
        await page.wait_for_selector(
            "a:has-text('Глава'), a:has-text('Розділ'), a:has-text('Chapter')",
            timeout=10000
        )
    except Exception:
        pass

    chapters = []
    links = await page.query_selector_all("a")
    for link in links:
        text = (await link.inner_text()).strip()
        num = _find_last_chapter(text)
        if num is not None:
            chapters.append(num)

    if chapters:
        last = max(chapters)
        result = str(int(last)) if last == int(last) else str(last)
        log(f"  fallback: {result}")
        return result
    raise Exception(f"главу не знайдено ({url})")


async def _check_one(
    semaphore: asyncio.Semaphore,
    browser,
    context_holder: list,
    counter: list,
    session: aiohttp.ClientSession,
    title: str,
    url: str
) -> tuple[str, str]:
    log(f"\nПеревіряємо: {title}")

    # mangalib через API без браузера, семафор не потрібен
    if "mangalib.me" in url:
        result = await _parse_mangalib_api(url, session)
        if result == "невідомо":
            log(f"  !️ mangalib: главу не знайдено")
        return title, result

    # Гарантований таймаут
    try:
        result = await asyncio.wait_for(
            _check_one_browser(semaphore, browser, context_holder, counter, title, url),
            timeout=PAGE_TIMEOUT
        )
    except asyncio.TimeoutError:
        log(f"  {title} - перевищено ліміт часу {PAGE_TIMEOUT}с")
        result = "невідомо"

    return title, result


async def _check_one_browser(
    semaphore: asyncio.Semaphore,
    browser,
    context_holder: list,
    counter: list,
    title: str,
    url: str
) -> str:
    async with semaphore:
        # Скидаємо контекст кожні CONTEXT_RESET_EVERY манг очищає кеш і пам'ять
        counter[0] += 1
        if counter[0] % CONTEXT_RESET_EVERY == 0:
            log(f"  Скидаємо контекст браузера (перевірено {counter[0]} манг)...")
            old_context = context_holder[0]
            context_holder[0] = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1280, "height": 800},
                locale="ru-RU",
            )
            await old_context.close()

        context = context_holder[0]
        page = await context.new_page()

        # Блокуємо зайві ресурси
        async def block_resources(route):
            if route.request.resource_type in BLOCKED_RESOURCES:
                await route.abort()
            else:
                await route.continue_()
        await page.route("**/*", block_resources)

        try:
            parser = next(
                (func for domain, func in SITE_PARSERS.items() if domain in url),
                _parse_fallback
            )
            return await parser(page, url)
        except Exception as e:
            log(f"  Помилка: {e}")
            return "невідомо"
        finally:
            await page.close()


async def check_all(manga_dict: dict) -> dict[str, str]:
    log(f"Починаємо перевірку {len(manga_dict)} манг паралельно (макс. {MAX_CONCURRENT} одночасно)...")
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    # Розділяємо API манги (mangalib) і браузерні
    api_manga = {t: u for t, u in manga_dict.items() if "mangalib.me" in u}
    browser_manga = {t: u for t, u in manga_dict.items() if "mangalib.me" not in u}

    async with aiohttp.ClientSession(headers=API_HEADERS) as session:
        api_tasks = [
            _check_one(semaphore, None, [], [0], session, title, url)
            for title, url in api_manga.items()
        ]
        api_results = await asyncio.gather(*api_tasks) if api_tasks else []

        browser_results = []
        if browser_manga:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=HEADLESS)
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 800},
                    locale="ru-RU",
                )
                context_holder = [context]
                counter = [0]

                browser_tasks = [
                    _check_one(semaphore, browser, context_holder, counter, session, title, url)
                    for title, url in browser_manga.items()
                ]
                browser_results = await asyncio.gather(*browser_tasks)
                await context_holder[0].close()
                await browser.close()

    return dict(list(api_results) + list(browser_results))