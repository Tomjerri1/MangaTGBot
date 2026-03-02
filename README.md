# Manga Tracker

Telegram бот який слідкує за новими главами манги. Управління через inline-кнопки, дані зберігаються в MongoDB Atlas.

## Що робить

- Перевіряє нові глави на вимогу - звіт тільки з тим що оновилось
- Захист від подвійного запуску перевірки
- Підтримує **com-x.life**, **mangabuff**, **mangalib**, **honey-manga.com.ua**, **zenko.online**, **manga.in.ua** та будь-які інші сайти через fallback парсер
- Пошук манги через inline-режим (`@bot назва`) з TTL-кешем
- Керування через покрокові діалоги в Telegram
- Пагінація списку манг

## Структура проекту

```
manga/
├── core/
│   ├── __init__.py
│   ├── checker.py           # Логіка перевірки, формування звіту
│   ├── logger.py            # Централізоване логування (stdout)
│   ├── parser_playwright.py # Парсери: Playwright + aiohttp API
│   └── repository.py        # MongoDB репозиторій (AbstractRepository + MongoRepository)
├── config/
│   ├── __init__.py
│   └── config.py            # Читає TELEGRAM_TOKEN і TELEGRAM_CHAT_ID з .env
├── .env                     
├── .env.example             # Шаблон .env
├── .gitignore
├── bot.py                   # Єдина точка входу
├── README.md
└── requirements.txt
```

## Встановлення

### 1. Клонуй репозиторій

```bash
git clone https://github.com/твій-юзернейм/manga-tracker.git
cd manga-tracker
```

### 2. Встанови залежності

```bash
pip install -r requirements.txt
```

### 3. Встанови браузер для Playwright

```bash
playwright install chromium
```

### 4. Створи `.env` файл

```bash
cp .env.example .env
```

Відкрий `.env` і заповни:

```env
# Telegram
TELEGRAM_TOKEN=твій_токен_від_BotFather
TELEGRAM_CHAT_ID=твій_chat_id

# MongoDB Atlas
MONGODB_URI=mongodb+srv://user:password@cluster0.xxxxx.mongodb.net/
MONGODB_DB=Manga
MONGODB_MANGA_COLLECTION=manga
MONGODB_META_COLLECTION=meta

# Браузер
HEADLESS=true
MAX_CONCURRENT_PAGES=10
PAGE_TIMEOUT=120
BROWSER_BATCH_SIZE=10
MAX_CONCURRENT_API=5
```

### 5. Налаштуй MongoDB Atlas

1. Зареєструйся на [mongodb.com/atlas](https://www.mongodb.com/atlas)
2. Створи безкоштовний кластер **M0**
3. Database Access → створи користувача з роллю `readWrite`
4. Network Access → додай IP свого сервера (або `0.0.0.0/0` для розробки)
5. Connect → Drivers → скопіюй URI і встав в `.env`

---

## Запуск

```bash
python bot.py
```

---

## Інтерфейс бота

Бот керується виключно через inline-кнопки. Після `/start` з'являється головне меню:

| Кнопка | Дія |
|--------|-----|
| 🔎 Пошук | Inline-пошук по назві манги (`@bot назва`) |
| 📚 Статус | Список всіх манг з главами і посиланнями (з пагінацією) |
| 🔍 Перевірити | Перевірити всі манги прямо зараз |
| ➕ Додати | Діалог додавання нової манги |
| 🗑 Видалити | Діалог видалення манги з пошуком по назві |

**Команди:**

| Команда | Опис |
|---------|------|
| `/start` | Головне меню |
| `/cancel` | Скасувати поточний діалог |

---

## Підтримувані сайти

| Сайт | Метод |
|------|-------|
| `mangalib` | API запит (без браузера) |
| `honey-manga.com.ua` | API запит (без браузера) |
| `zenko.online` | API запит (без браузера) |
| `manga.in.ua` | HTTP двокроковий запит з ізольованим cookie jar |
| `com-x.life` | Playwright → `window.__DATA__` |
| `mangabuff` | Playwright → посилання `/chapter/N` |
| Будь-який інший | Fallback — пошук "Глава N" / "Chapter N" в посиланнях |

### Як додати новий браузерний сайт

В `core/parser_playwright.py`:

```python
@register_parser("новий-сайт.com")
@retry(times=3, delay=2.0)
async def _parse_новий(page: Page, url: str) -> str:
    await page.goto(url, timeout=40000, wait_until="domcontentloaded")
    # твоя логіка парсингу
    return "номер_глави"
```

### Як додати новий API сайт

1. Напиши парсер:

```python
async def _parse_новий_api(url: str, session: aiohttp.ClientSession) -> str | None:
    # твоя логіка
    return "номер_глави"  # або None якщо не вдалось
```

2. Додай домен в `API_DOMAINS`:

```python
API_DOMAINS = {"mangalib.me", "honey-manga.com.ua", ..., "новий-сайт.com"}
```

3. Додай гілку в `_check_one`:

```python
elif "новий-сайт.com" in url:
    parser_func = _parse_новий_api
```

---

## Структура MongoDB

**Колекція `manga`** — кожна манга окремий документ:
```json
{"user_id": "123456789", "title": "Назва", "url": "https://...", "last_chapter": "199"}
```

**Колекція `meta`** — дата перевірки:
```json
{"_id": "123456789", "last_check_date": "2026-02-20"}
```

---

## Налаштування `.env`

| Змінна | За замовчуванням | Опис |
|--------|-----------------|------|
| `TELEGRAM_TOKEN` | — | Токен бота від BotFather |
| `TELEGRAM_CHAT_ID` | — | Твій Telegram ID (бот відповідає тільки йому) |
| `MONGODB_URI` | — | URI підключення до MongoDB Atlas |
| `MONGODB_DB` | `Manga` | Назва бази даних |
| `MONGODB_MANGA_COLLECTION` | `manga` | Колекція манг |
| `MONGODB_META_COLLECTION` | `meta` | Колекція мета-даних |
| `HEADLESS` | `true` | `false` щоб бачити браузер (для дебагу) |
| `MAX_CONCURRENT_PAGES` | `10` | Максимум одночасних вкладок Playwright |
| `BROWSER_BATCH_SIZE` | `10` | Манг на один запуск браузера |
| `MAX_CONCURRENT_API` | `5` | Одночасних API запитів |
| `PAGE_TIMEOUT` | `120` | Таймаут на одну сторінку (секунди) |