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
MAX_CONCURRENT_API = int(os.getenv("MAX_CONCURRENT_API", "5"))
PAGE_TIMEOUT = int(os.getenv("PAGE_TIMEOUT", "120"))
BROWSER_BATCH_SIZE = int(os.getenv("BROWSER_BATCH_SIZE", "10"))

log = get_logger("parser").info

API_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json",
}

BLOCKED_RESOURCES = {"image", "media", "font", "stylesheet"}

BLOCKED_DOMAINS = {
    "google-analytics.com", "googletagmanager.com",
    "googlesyndication.com", "doubleclick.net",
    "disqus.com", "disquscdn.com",
    "facebook.net", "mc.yandex.ru",
}

SITE_PARSERS = {}


def register_parser(domain: str):
    def decorator(func):
        SITE_PARSERS[domain] = func
        return func
    return decorator


_CHAPTER_RE = re.compile(
    r"(?:Глава|Розділ|Chapter)\s*(\d+(?:\.\d+)?)",
    re.IGNORECASE
)


def _find_last_chapter(text: str) -> float | None:
    matches = _CHAPTER_RE.findall(text)
    if matches:
        return max(float(m) for m in matches)
    return None


def _chunks(lst: list, n: int):
    """Ділить список на батчі по n елементів"""
    for i in range(0, len(lst), n):
        yield lst[i:i + n]


def retry(times: int = 3, delay: float = 2.0):
    def decorator(func):
        async def wrapper(page, url: str, *args, **kwargs):
            last_error = None
            for attempt in range(1, times + 1):
                try:
                    return await func(page, url, *args, **kwargs)
                except Exception as e:
                    last_error = e
                    if attempt < times:
                        log(f"  ⚠️ Спроба {attempt}/{times} невдала: {e}. Повтор через {delay}с...")
                        await asyncio.sleep(delay)
            log(f"  ❌ Всі {times} спроби невдалі: {last_error}")
            return "невідомо"
        return wrapper
    return decorator


def _extract_comx_chapters(html: str) -> list[int]:
    m = re.search(r'window\.__DATA__\s*=\s*({.*?})\s*(?:;|</script>)', html, re.DOTALL)
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
    try:
        data = await page.evaluate(
            "() => typeof window.__DATA__ !== 'undefined' ? window.__DATA__ : null"
        )
        if data and isinstance(data, dict):
            chapters = data.get("chapters", [])
            return [ch["posi"] for ch in chapters if ch.get("posi")]
    except Exception as e:
        log(f"  ⚠️ JS evaluate не спрацював: {e}, використовую regex fallback")
    return []


async def _parse_mangalib_api(url: str, session: aiohttp.ClientSession) -> str:
    m = re.search(r'/manga/([^/?#]+)', url)
    if not m:
        return "невідомо"
    slug = m.group(1)
    api_url = f"https://api.cdnlibs.org/api/manga/{slug}/chapters"
    log(f"  -> API запит: {api_url}")
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
                result = str(int(last)) if last == int(last) else str(last)
                log(f"  ✅ mangalib.me: {result}")
                return result
    except Exception as e:
        log(f"  ❌ API помилка: {e}")
    return "невідомо"




async def _parse_honeymanga_api(url: str, session: aiohttp.ClientSession) -> str:
    m = re.search(r'/book/([a-f0-9-]{36})', url)
    if not m:
        return "невідомо"
    manga_id = m.group(1)
    api_url = "https://data.api.honey-manga.com.ua/v2/chapter/cursor-list"
    log(f"  -> API запит: {api_url}")
    try:
        # Спочатку отримуємо загальну кількість глав щоб знайти останню
        async with session.post(
            api_url,
            json={"mangaId": manga_id, "page": 1, "pageSize": 1, "sortOrder": "DESC"},
            timeout=aiohttp.ClientTimeout(total=20)
        ) as r:
            r.raise_for_status()
            data = await r.json()
            items = data.get("list", []) or data.get("data", []) or data.get("items", [])
            if not items and isinstance(data, list):
                items = data
            if items:
                first = items[0]
                # Номер глави може бути в різних полях
                chapter = (
                    first.get("chapterNum") or
                    first.get("number") or
                    first.get("chapter") or
                    first.get("index")
                )
                if chapter is not None:
                    result = str(int(float(chapter))) if float(chapter) == int(float(chapter)) else str(chapter)
                    log(f"  ✅ honey-manga API: {result}")
                    return result
            log(f"  ⚠️ honey-manga API: невідома структура відповіді: {str(data)[:200]}")
    except Exception as e:
        log(f"  ❌ honey-manga API помилка: {e}")
    return "невідомо"


async def _parse_zenko_api(url: str, session: aiohttp.ClientSession) -> str:
    m = re.search(r'/titles/(\d+)', url)
    if not m:
        return "невідомо"
    title_id = m.group(1)
    api_url = f"https://api.zenko.online/titles/{title_id}/chapters"
    log(f"  -> API запит: {api_url}")
    try:
        async with session.get(api_url, timeout=aiohttp.ClientTimeout(total=20)) as r:
            r.raise_for_status()
            data = await r.json()
            items = data if isinstance(data, list) else data.get("data", [])
            chapters = []
            for item in items:
                name = item.get("name", "")
                # Формат: "18@#%&;№%#&**#!@151@#%&;№%#&**#!@Назва"
                # Другий сегмент — номер глави
                parts = name.split("@#%&;№%#&**#!@")
                if len(parts) >= 2:
                    try:
                        chapters.append(float(parts[1]))
                    except ValueError:
                        pass
            if chapters:
                last = max(chapters)
                result = str(int(last)) if last == int(last) else str(last)
                log(f"  ✅ zenko.online API: {result}")
                return result
    except Exception as e:
        log(f"  ❌ zenko.online API помилка: {e}")
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
        log(f"  ✅ com-x.life: {result}")
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
        log(f"  ✅ mangabuff.ru: {result}")
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
        if len(text) > 80:
            continue
        num = _find_last_chapter(text)
        if num is not None:
            chapters.append(num)

    if chapters:
        last = max(chapters)
        result = str(int(last)) if last == int(last) else str(last)
        log(f"  ✅ fallback: {result}")
        return result
    raise Exception(f"главу не знайдено ({url})")

async def _check_one(
    semaphore: asyncio.Semaphore,
    context,
    session: aiohttp.ClientSession,
    title: str,
    url: str
) -> tuple[str, str]:
    log(f"\n=== Перевіряємо: {title} ===")

    if "mangalib.me" in url:
        result = await _parse_mangalib_api(url, session)
        if result == "невідомо":
            log(f"  ⚠️ mangalib: главу не знайдено")
        return title, result

    if "honey-manga.com.ua" in url:
        result = await _parse_honeymanga_api(url, session)
        if result == "невідомо":
            log(f"  ⚠️ honey-manga API: главу не знайдено")
        return title, result

    if "zenko.online" in url:
        result = await _parse_zenko_api(url, session)
        if result == "невідомо":
            log(f"  ⚠️ zenko.online API: главу не знайдено")
        return title, result

    if context is None:
        log(f"  ❌ {title} — context не передано")
        return title, "невідомо"

    try:
        result = await _check_one_browser(semaphore, context, title, url)
    except Exception as e:
        log(f"  ❌ {title} — помилка: {e}")
        result = "невідомо"

    return title, result


async def _check_one_browser(
    semaphore: asyncio.Semaphore,
    context,
    title: str,
    url: str
) -> str:
    async with semaphore:
        page = await context.new_page()

        page.set_default_navigation_timeout(PAGE_TIMEOUT * 1000)
        page.set_default_timeout(PAGE_TIMEOUT * 1000)

        async def block_resources(route):
            try:
                if route.request.resource_type in BLOCKED_RESOURCES:
                    await route.abort()
                    return
                if any(domain in route.request.url for domain in BLOCKED_DOMAINS):
                    await route.abort()
                    return
                await route.continue_()
            except Exception:
                pass
        await page.route("**/*", block_resources)

        try:
            parser = next(
                (func for domain, func in SITE_PARSERS.items() if domain in url),
                _parse_fallback
            )
            return await parser(page, url)
        except Exception as e:
            log(f"  ❌ Помилка: {e}")
            return "невідомо"
        finally:
            await page.close()


async def _run_browser_batch(
    semaphore: asyncio.Semaphore,
    session: aiohttp.ClientSession,
    batch: list[tuple[str, str]]
) -> list[tuple[str, str]]:
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            args=[
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-extensions",
                "--disable-plugins",
            ]
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="ru-RU",
        )
        try:
            tasks = [
                _check_one(semaphore, context, session, title, url)
                for title, url in batch
            ]
            return list(await asyncio.gather(*tasks))
        finally:
            await context.close()
            await browser.close()


async def check_all(manga_dict: dict) -> dict[str, str]:
    log(f"Починаємо перевірку {len(manga_dict)} манг паралельно (макс. {MAX_CONCURRENT} одночасно)...")
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    API_DOMAINS = {"mangalib.me", "honey-manga.com.ua", "zenko.online"}
    api_manga = {t: u for t, u in manga_dict.items() if any(d in u for d in API_DOMAINS)}
    browser_manga = list({t: u for t, u in manga_dict.items() if not any(d in u for d in API_DOMAINS)}.items())

    async with aiohttp.ClientSession(headers=API_HEADERS) as session:

        async def run_api():
            if not api_manga:
                return []
            api_semaphore = asyncio.Semaphore(MAX_CONCURRENT_API)

            async def _limited(title, url):
                async with api_semaphore:
                    return await _check_one(semaphore, None, session, title, url)

            tasks = [_limited(title, url) for title, url in api_manga.items()]
            return await asyncio.gather(*tasks)

        async def run_browser():
            if not browser_manga:
                return []
            results = []
            batches = list(_chunks(browser_manga, BROWSER_BATCH_SIZE))
            log(f"Браузерні манги: {len(browser_manga)} шт., батчів: {len(batches)} по {BROWSER_BATCH_SIZE}")
            for i, batch in enumerate(batches, 1):
                log(f"  Батч {i}/{len(batches)} ({len(batch)} манг)...")
                batch_results = await _run_browser_batch(semaphore, session, batch)
                results.extend(batch_results)
            return results

        api_results, browser_results = await asyncio.gather(run_api(), run_browser())

    return dict(api_results + browser_results)