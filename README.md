# Manga Tracker

Telegram бот який слідкує за новими главами манги. Дані зберігаються в MongoDB Atlas.

## Що робить

- Перевіряє нові глави на вимогу через Telegram
- Відправляє звіт тільки з новими главами (без спаму)
- Підтримує **com-x.life**, **mangabuff.ru**, **mangalib.me** та будь-які інші сайти через fallback парсер
- Керування списком манг через Telegram

## Структура проекту

```
manga/
├── core/
│   ├── __init__.py
│   ├── checker.py           # Логіка перевірки
│   ├── logger.py            # Централізоване логування
│   ├── parser_playwright.py # Парсери сайтів (Playwright + aiohttp API)
│   ├── repository.py        # MongoDB репозиторій (Dependency Injection)
│   └── telegram_sender.py   # Відправка повідомлень з розбивкою на частини
├── config/
│   ├── __init__.py
│   └── config.py            # Читає налаштування з .env
├── data/
│   └── manga.log            # Лог всіх запусків (створюється автоматично)
├── .env                     # Токени і налаштування
├── .env.example
├── .gitignore
├── bot.py                   # Єдина точка входу
├── README.md
└── requirements.txt
```

## Встановлення

### 1. Встанови залежності

```bash
pip install -r requirements.txt
```

### 2. Встанови браузер для Playwright

```bash
playwright install chromium
```

### 3. Створи `.env` файл

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
MONGODB_COLLECTION=MangaTG

# Браузер
HEADLESS=true
MAX_CONCURRENT_PAGES=10
CONTEXT_RESET_EVERY=20
PAGE_TIMEOUT=120
```

### 4. Налаштуй MongoDB Atlas

1. Зареєструйся на [mongodb.com/atlas](https://www.mongodb.com/atlas)
2. Створи безкоштовний кластер **M0**
3. Database Access → створи користувача з роллю `readWrite`
4. Network Access → додай `0.0.0.0/0`
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
| `/add Назва \| URL` | Додати мангу |
| `/remove Назва` | Видалити мангу |
| `/check` | Перевірити всі манги прямо зараз |

**Приклад додавання:**
```
/add Berserk | https://manga/manga/berserk
```

---

## Підтримувані сайти

| Сайт | Метод |
|------|-------|
| `com-x.life` | `window.__DATA__` через JS + regex fallback |
| `mangabuff.ru` | Посилання `/chapter/N` |
| `mangalib.me` | Прямий API запит (без браузера) |
| Будь-який інший | Fallback — пошук "Глава N" / "Розділ N" / "Chapter N" |

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