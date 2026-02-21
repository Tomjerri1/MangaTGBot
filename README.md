# Manga Tracker

Telegram бот який слідкує за новими главами манги. Дані зберігаються в MongoDB Atlas.

## Що робить

- Перевіряє нові глави на вимогу через `/check`
- Відправляє звіт тільки з новими главами (без спаму)
- Підтримує *com-x.life*, *mangabuff*, *mangalib*, *honey-manga.com.ua*, *zenko.online* та будь-які інші сайти через fallback парсер
- Керування списком манг
- Пагінація списку манг

## Структура проекту

```
manga/
├── core/
│   ├── __init__.py
│   ├── checker.py           # Логіка перевірки манг
│   ├── logger.py            # Централізоване логування (stdout)
│   ├── parser_playwright.py # Парсери сайтів (Playwright + aiohttp API)
│   └── repository.py        # MongoDB репозиторій
├── config/
│   ├── __init__.py
│   └── config.py
├── .env
├── .env.example
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

Як отримати токен — створи бота через [@BotFather](https://t.me/BotFather).  
Як отримати chat_id — напиши [@userinfobot](https://t.me/userinfobot).

### 5. Налаштуй MongoDB Atlas

1. Зареєструйся на [mongodb.com/atlas](https://www.mongodb.com/atlas)
2. Створи безкоштовний кластер
3. Database Access → створи користувача з роллю `readWrite`
4. Network Access → додай IP свого сервера (або `0.0.0.0/0` для розробки)
5. Connect → Drivers → скопіюй URI і встав в `.env`

---

## Запуск

```bash
python bot.py
```

---

## Команди бота

| Команда | Опис |
|---------|------|
| `/start` | Привітання і список команд |
| `/status` | Список всіх манг з останніми главами і посиланнями |
| `/add` | Додати мангу (покроковий діалог) |
| `/remove` | Видалити мангу (покроковий діалог з пошуком) |
| `/check` | Перевірити всі манги прямо зараз |
| `/cancel` | Скасувати поточний діалог |

---

## Підтримувані сайти

| Сайт | Метод |
|------|-------|
| `mangalib.me` | API запит (без браузера) |
| `honey-manga.com.ua` | API запит (без браузера) |
| `zenko.online` | API запит (без браузера) |
| `com-x.life` | Playwright → `window.__DATA__` |
| `mangabuff.ru` | Playwright → посилання `/chapter/N` |
| Будь-який інший | Fallback — пошук "Глава N" / "Chapter N" |

### Як додати новий сайт

В `core/parser_playwright.py` додай:

```python
@register_parser("новий-сайт.com")
@retry(times=3, delay=2.0)
async def _parse_новий(page, url: str) -> str:
    await page.goto(url, timeout=40000, wait_until="domcontentloaded")
    # твоя логіка парсингу
    return "номер_глави"
```

---

## Структура MongoDB

*Колекція `manga`* — кожна манга окремий документ:
```json
{"user_id": "123456789", "title": "Назва", "url": "https://...", "last_chapter": "199"}
```

*Колекція `meta`* — дата перевірки для кожного користувача:
```json
{"_id": "123456789", "last_check_date": "2026-02-20"}
```

---

## Налаштування `.env`

| Змінна | За замовчуванням | Опис |
|--------|-----------------|------|
| `TELEGRAM_TOKEN` | — | Токен бота від BotFather |
| `TELEGRAM_CHAT_ID` | — | Твій Telegram ID |
| `MONGODB_URI` | — | URI підключення до MongoDB Atlas |
| `MONGODB_DB` | `Manga` | Назва бази даних |
| `MONGODB_MANGA_COLLECTION` | `manga` | Колекція манг |
| `MONGODB_META_COLLECTION` | `meta` | Колекція мета-даних |
| `HEADLESS` | `true` | `false` щоб бачити браузер (дебаг) |
| `MAX_CONCURRENT_PAGES` | `10` | Максимум одночасних вкладок |
| `BROWSER_BATCH_SIZE` | `10` | Манг на один запуск браузера |
| `MAX_CONCURRENT_API` | `5` | Одночасних API запитів |
| `PAGE_TIMEOUT` | `120` | Таймаут на одну мангу (секунди) |