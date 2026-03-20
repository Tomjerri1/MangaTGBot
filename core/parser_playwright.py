import asyncio
import re
import json
import os
import functools

import aiohttp
from dotenv import load_dotenv
from playwright.async_api import async_playwright, BrowserContext, Page

from core.logger import get_logger

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

HEADLESS = os.getenv("HEADLESS", "true").lower() == "true"
MAX_CONCURRENT = int(os.getenv("MAX_CONCURRENT_PAGES", "10"))
MAX_CONCURRENT_API = int(os.getenv("MAX_CONCURRENT_API", "5"))
PAGE_TIMEOUT = int(os.getenv("PAGE_TIMEOUT", "120"))
BROWSER_BATCH_SIZE = int(os.getenv("BROWSER_BATCH_SIZE", "10"))


_PROXY_URL = os.getenv("PROXY_URL")

log = get_logger("parser").info

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

API_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "application/json",
}

MANGAINUA_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept-Language": "uk-UA,uk;q=0.9",
}

BLOCKED_RESOURCES = {"image", "media", "font", "stylesheet", "manifest", "other"}

BLOCKED_DOMAINS = {
    # Аналітика і трекінг
    "google-analytics.com", "googletagmanager.com",
    "hotjar.com", "clarity.ms",
    "mixpanel.com", "amplitude.com",
    "segment.com", "fullstory.com",
    # Реклама
    "googlesyndication.com", "doubleclick.net",
    "adnxs.com", "pubmatic.com",
    "rubiconproject.com", "openx.net", "criteo.com",
    # Соцмережі і чати
    "disqus.com", "disquscdn.com",
    "facebook.net", "twitter.com",
    "instagram.com", "tiktok.com",
    "intercom.io", "zendesk.com",
    "freshchat.com", "jivosite.com",
    # Моніторинг і інфраструктура
    "cloudflareinsights.com", "sentry.io", "nr-data.net",
}

SITE_PARSERS = {}

API_DOMAINS = {"honey-manga.com.ua", "zenko.online", "manga.in.ua"}

_shutdown_event = asyncio.Event()


def register_parser(domain: str):
    def decorator(func):
        SITE_PARSERS[domain] = func
        return func
    return decorator


#Regex

_CHAPTER_RE = re.compile(
    r"(?:Глава|Розділ|Chapter|Гл(?:ава)?\.?|Ch(?:apter)?\.?|Р(?:озділ)?\.?)\s*(\d+(?:\.\d+)?)",
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


#Retry декоратор

def retry(times: int = 3, delay: float = 2.0):
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(page: Page, url: str, *args, **kwargs):
            last_error = None
            for attempt in range(1, times + 1):
                try:
                    return await func(page, url, *args, **kwargs)
                except Exception as e:
                    last_error = e
                    if _shutdown_event.is_set():
                        log(f"  ⚠️ Зупинка бота — перериваємо retry для {url}")
                        return "невідомо"
                    if attempt < times:
                        log(f"  ⚠️ Спроба {attempt}/{times} невдала: {e}. Повтор через {delay}с...")
                        await asyncio.sleep(delay)
            log(f"  ❌ Всі {times} спроби невдалі: {last_error}")
            return "невідомо"
        return wrapper
    return decorator


#Допоміжні функції

def _extract_comx_chapters(html: str) -> list[int]:
    m = re.search(r'window\.__DATA__\s*=\s*({.*?})\s*(?:;|</script>)', html, re.DOTALL)
    if not m:
        nums = re.findall(r'"posi"\s*:\s*(\d+)', html)
        return [int(n) for n in nums]
    try:
        data = json.loads(m.group(1))
        return [ch["posi"] for ch in data.get("chapters", []) if ch.get("posi")]
    except (json.JSONDecodeError, KeyError):
        nums = re.findall(r'"posi"\s*:\s*(\d+)', m.group(1))
        return [int(n) for n in nums]


async def _extract_comx_chapters_js(page: Page) -> list[int]:
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



async def _parse_honeymanga_api(url: str, session: aiohttp.ClientSession) -> str | None:
    # URL: https://honey-manga.com.ua/book/{uuid}
    m = re.search(r'/book/([a-f0-9-]{36})', url)
    if not m:
        return None
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
                # DESC порядок перший елемент найновіший
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
                    log(f"  ✅ [API] honey-manga: {result}")
                    return result
            log(f"  ⚠️ honey-manga API: невідома структура відповіді: {str(data)[:200]}")
    except Exception as e:
        log(f"  ❌ honey-manga API помилка: {e}")
    return None


async def _parse_zenko_api(url: str, session: aiohttp.ClientSession) -> str | None:
    # URL: https://zenko.online/titles/{id}?translation=unset
    m = re.search(r'/titles/(\d+)', url)
    if not m:
        return None
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
                # Другий сегмент - номер глави
                parts = name.split("@#%&;№%#&**#!@")
                if len(parts) >= 2:
                    try:
                        chapters.append(float(parts[1]))
                    except ValueError:
                        pass
            if chapters:
                last = max(chapters)
                result = str(int(last)) if last == int(last) else str(last)
                log(f"  ✅ [API] zenko.online: {result}")
                return result
    except Exception as e:
        log(f"  ❌ zenko.online API помилка: {e}")
    return None



async def _parse_mangainua_api(url: str, session: aiohttp.ClientSession) -> str | None:
    # URL: https://manga.in.ua/mangas/{category}/{id}-{slug}.html
    m = re.search(r'/mangas/([^/]+)/(\d+)-', url)
    if not m:
        return None
    news_category_slug = m.group(1)
    news_id = m.group(2)
    log(f"  -> HTTP двокроковий запит: manga.in.ua (id={news_id})")
    # Власна сесія з ізольованим cookie jar - cookies manga.in.ua не змішуються
    # зі спільною сесією інших API парсерів при паралельній перевірці
    async with aiohttp.ClientSession(
        headers=MANGAINUA_HEADERS,
        cookie_jar=aiohttp.CookieJar(),
    ) as manga_session:
        try:
            #отримуємо сторінку, витягуємо hash - cookies зберігаються в manga_session
            async with manga_session.get(
                url,
                headers={"Accept": "text/html"},
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                r.raise_for_status()
                html = await r.text()
                # Фікс: regex з ASCII лапками (одинарна і подвійна)
                hash_match = re.search(
                    r"""site_login_hash\s*=\s*['"]([a-f0-9]{32,64})['"]""", html
                )
                if not hash_match:
                    log(f"  ⚠️ manga.in.ua: site_login_hash не знайдено")
                    return None
                site_login_hash = hash_match.group(1)

            #POST load_chapters - cookies з кроку 1 вже в manga_session
            async with manga_session.post(
                "https://manga.in.ua/engine/ajax/controller.php",
                data={
                    "mod": "load_chapters",
                    "action": "show",
                    "news_id": news_id,
                    "news_category": news_category_slug,
                    "this_link": url,
                    "user_hash": site_login_hash,
                },
                headers={
                    "Referer": url,
                    "X-Requested-With": "XMLHttpRequest",
                    "Accept": "application/json, text/javascript, */*",
                },
                timeout=aiohttp.ClientTimeout(total=20)
            ) as r:
                r.raise_for_status()
                body = await r.text()
                if not body.strip():
                    log(f"  ⚠️ manga.in.ua: порожня відповідь")
                    return None

                chapters = re.findall(r'manga-chappter="(\d+(?:\.\d+)?)"', body)
                if not chapters:
                    chapters = re.findall(r"manga-chappter='(\d+(?:\.\d+)?)'", body)
                if not chapters:
                    chapters = re.findall(
                        r"(?:Глава|Розділ|Chapter)\s*(\d+(?:\.\d+)?)", body, re.IGNORECASE
                    )
                if chapters:
                    last = max(float(n) for n in chapters)
                    result = str(int(last)) if last == int(last) else str(last)
                    log(f"  ✅ [API] manga.in.ua: {result}")
                    return result

        except Exception as e:
            log(f"  ❌ manga.in.ua помилка: {e}")
    return None

#Парсери сайтів

async def _sample_links(page, limit: int = 8) -> list[str]:
    """Збирає зразок href посилань зі сторінки для діагностики."""
    try:
        links = await page.query_selector_all("a[href]")
        hrefs = []
        for link in links[:50]:
            href = (await link.get_attribute("href") or "").strip()
            if href and not href.startswith(("javascript", "#", "mailto")):
                hrefs.append(href)
            if len(hrefs) >= limit:
                break
        return hrefs
    except Exception:
        return []

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
        log(f"  ✅ [browser] com-x.life: {result}")
        return result
    sample = await _sample_links(page)
    log(f"  ❌ com-x.life: window.__DATA__ не знайдено — сайт міг змінити структуру ({url})")
    log(f"     Зразок посилань на сторінці: {sample}")
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
        log(f"  ✅ [browser] mangabuff.ru: {result}")
        return result
    sample = await _sample_links(page)
    log(f"  ❌ mangabuff.ru: a[href*='/chapter/'] не знайдено ({url})")
    log(f"     Зразок посилань на сторінці: {sample}")
    raise Exception("главу не знайдено")


@register_parser("mangalib.me")
@retry(times=3, delay=2.0)
async def _parse_mangalib_browser(page: Page, url: str) -> str:
    """Браузерний парсер для mangalib.me — API закритий, використовуємо Playwright."""
    # Додаємо ?section=chapters якщо немає — без нього список глав не відображається
    if "section=chapters" not in url:
        url = url.rstrip("/") + "?section=chapters"
    await page.goto(url, timeout=40000, wait_until="domcontentloaded")
    try:
        await page.wait_for_load_state("networkidle", timeout=15000)
    except Exception:
        pass  # якщо networkidle не досягнуто за 15с — продовжуємо з тим що є
    try:
        await page.wait_for_selector("a[href*='/read/']", timeout=20000)
    except Exception:
        pass

    chapters = []
    links = await page.query_selector_all("a[href*='/read/']")
    for link in links:
        text = (await link.inner_text()).strip()
        m = re.search(r"[Гг]лава\s+(\d+(?:\.\d+)?)", text)
        if m:
            chapters.append(float(m.group(1)))

    if chapters:
        last = max(chapters)
        result = str(int(last)) if last == int(last) else str(last)
        log(f"  ✅ [browser] mangalib.me: {result}")
        return result
    sample = await _sample_links(page)
    log(f"  ❌ mangalib.me: a[href*='/read/'] не знайдено ({url})")
    log(f"     Зразок посилань на сторінці: {sample}")
    raise Exception(f"главу не знайдено ({url})")


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
        log(f"  ✅ [browser] fallback: {result}")
        return result
    sample = await _sample_links(page)
    log(f"  ❌ fallback: 'Глава/Розділ/Chapter N' не знайдено ({url})")
    log(f"     Зразок посилань на сторінці: {sample}")
    raise Exception(f"главу не знайдено ({url})")


async def _check_one_api(
    session: aiohttp.ClientSession,
    title: str,
    url: str,
) -> tuple[str, str]:
    log(f"=== Перевіряємо: {title} ===")

    parser_func = None
    if "honey-manga.com.ua" in url:
        parser_func = _parse_honeymanga_api
    elif "zenko.online" in url:
        parser_func = _parse_zenko_api
    elif "manga.in.ua" in url:
        parser_func = _parse_mangainua_api

    if parser_func is None:
        log(f"  ❌ {title} — невідомий API домен")
        return title, "невідомо"

    result = await parser_func(url, session)
    if result is None:
        log(f"  ⚠️ {title}: главу не знайдено")
        return title, "невідомо"
    return title, result


async def _check_one(
    semaphore: asyncio.Semaphore,
    context: BrowserContext,
    title: str,
    url: str,
) -> tuple[str, str]:
    log(f"=== Перевіряємо: {title} ===")
    try:
        result = await _check_one_browser(semaphore, context, title, url)
    except Exception as e:
        log(f"  ❌ {title} — помилка: {e}")
        result = "невідомо"
    return title, result


async def _check_one_browser(
    semaphore: asyncio.Semaphore,
    context: BrowserContext,
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
    batch: list[tuple[str, str]],
) -> list[tuple[str, str]]:
    """Запускає один браузер для батчу манг, закриває після завершення."""
    proxy = {"server": _PROXY_URL} if _PROXY_URL else None
    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=HEADLESS,
            proxy=proxy,
            args=[
                "--disable-gpu",
                "--disable-dev-shm-usage",
                "--no-sandbox",
                "--disable-extensions",
                "--disable-plugins",
                "--disable-blink-features=AutomationControlled",
            ]
        )
        context = await browser.new_context(
            user_agent=USER_AGENT,
            viewport={"width": 800, "height": 600},
            locale="uk-UA",
            extra_http_headers={"Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7"},
        )
        # Приховати ознаки headless браузера
        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
            Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3]});
            window.chrome = {runtime: {}};
        """)
        try:
            tasks = [
                _check_one(semaphore, context, title, url)
                for title, url in batch
            ]
            return list(await asyncio.gather(*tasks))
        finally:
            await context.close()
            await browser.close()



async def check_all(manga_dict: dict) -> dict[str, str]:
    log(f"Починаємо перевірку {len(manga_dict)} манг паралельно (макс. {MAX_CONCURRENT} одночасно)...")
    semaphore = asyncio.Semaphore(MAX_CONCURRENT)

    api_manga = {t: u for t, u in manga_dict.items() if any(d in u for d in API_DOMAINS)}
    browser_manga = list({t: u for t, u in manga_dict.items() if not any(d in u for d in API_DOMAINS)}.items())

    async with aiohttp.ClientSession(headers=API_HEADERS) as session:

        async def run_api() -> list[tuple[str, str]]:
            if not api_manga:
                return []
            api_semaphore = asyncio.Semaphore(MAX_CONCURRENT_API)

            async def _limited(title, url):
                async with api_semaphore:
                    try:
                        return await _check_one_api(session, title, url)
                    except Exception as e:
                        log(f"  ❌ Глобальна помилка API для {title}: {e}")
                        return title, "невідомо"

            tasks = [_limited(title, url) for title, url in api_manga.items()]
            return list(await asyncio.gather(*tasks))

        async def run_browser(fallback: list[tuple[str, str]] | None = None) -> list[tuple[str, str]]:
            if not browser_manga and not fallback:
                return []
            results = []
            batches = list(_chunks(browser_manga, BROWSER_BATCH_SIZE)) if browser_manga else []

            if browser_manga:
                log(f"Браузерні манги: {len(browser_manga)} шт., батчів: {len(batches)} по {BROWSER_BATCH_SIZE}")

            for i, batch in enumerate(batches, 1):
                log(f"  Батч {i}/{len(batches)} ({len(batch)} манг)...")
                batch_results = await _run_browser_batch(semaphore, list(batch))
                results.extend(batch_results)

            # Fallback запускається окремим браузером після закриття основних батчів
            if fallback:
                log(f"  Браузерний fallback для {len(fallback)} API манг...")
                fallback_results = await _run_browser_batch(semaphore, fallback)
                results.extend(fallback_results)

            return results

        api_results = await run_api()

        # Збираємо API манги які повернули "невідомо" - кандидати для браузерного fallback
        api_failed = [
            (title, api_manga[title])
            for title, result in api_results
            if result == "невідомо"
        ]

        if api_failed:
            log(f"  ⚠️ {len(api_failed)} API манг не вдалось — буде спроба через браузер: {[t for t, _ in api_failed]}")

        # Запускаємо браузер (з fallback якщо є невдалі API манги)
        browser_results = await run_browser(fallback=api_failed if api_failed else None)

        # Результати fallback замінюють оригінальні "невідомо" з API
        all_results = dict(api_results)
        all_results.update(dict(browser_results))

    return all_results