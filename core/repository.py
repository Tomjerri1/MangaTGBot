"""
Репозиторій даних MongoDB Atlas.

Структура БД:
  Колекція manga:
    - Кожна манга окремий документ:
      {"_id": ObjectId, "user_id": "123", "title": "...", "url": "...", "last_chapter": "199"}

  Колекція meta:
    - Дата перевірки окремо для кожного користувача:
      {"_id": "1431783762", "last_check_date": "2026-02-20"}
"""
import os
from abc import ABC, abstractmethod
from dotenv import load_dotenv

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

class AbstractRepository(ABC):

    @abstractmethod
    async def setup(self) -> None:
        pass

    @abstractmethod
    async def load(self) -> dict:
        pass

    @abstractmethod
    async def add_manga(self, title: str, url: str) -> None:
        pass

    @abstractmethod
    async def remove_manga(self, title: str) -> None:
        pass

    @abstractmethod
    async def update_chapter(self, title: str, chapter: str) -> None:
        pass

    @abstractmethod
    async def set_last_check_date(self, date: str) -> None:
        pass

class MongoRepository(AbstractRepository):
    """
    MongoDB Atlas для продакшну на сервері.
    Кожна манга окремий документ в колекції manga.
    Мета-дані (last_check_date) окремий документ в колекції meta.
    """

    def __init__(self, uri: str, db_name: str, user_id: str,
                 manga_col: str = "manga", meta_col: str = "meta"):
        from motor.motor_asyncio import AsyncIOMotorClient
        self.client = AsyncIOMotorClient(
            uri,
            serverSelectionTimeoutMS=10000,
            connectTimeoutMS=10000,
            socketTimeoutMS=20000,
        )
        db = self.client[db_name]
        self.manga_col = db[manga_col]
        self.meta_col = db[meta_col]
        self.user_id = str(user_id)

    async def setup(self) -> None:
        """Створює індекси при старті бота.
        MongoDB пропускає створення якщо індекс вже існує безпечно викликати кожен раз."""
        await self.manga_col.create_index([("user_id", 1), ("title", 1)])

    async def load(self) -> dict:
        """Завантажує всі манги і дату перевірки для поточного користувача"""
        cursor = self.manga_col.find({"user_id": self.user_id})
        manga = {}
        async for doc in cursor:
            manga[doc["title"]] = {
                "url": doc["url"],
                "last_chapter": doc.get("last_chapter", "невідомо"),
            }

        # Дата перевірки
        meta = await self.meta_col.find_one({"_id": self.user_id})
        last_check_date = meta["last_check_date"] if meta else ""

        return {"manga": manga, "last_check_date": last_check_date}

    async def add_manga(self, title: str, url: str) -> None:
        await self.manga_col.update_one(
            {"user_id": self.user_id, "title": title},
            {"$set": {
                "user_id": self.user_id,
                "title": title,
                "url": url,
                "last_chapter": "невідомо",
            }},
            upsert=True
        )

    async def remove_manga(self, title: str) -> None:
        await self.manga_col.delete_one({"user_id": self.user_id, "title": title})

    async def update_chapter(self, title: str, chapter: str) -> None:
        await self.manga_col.update_one(
            {"user_id": self.user_id, "title": title},
            {"$set": {"last_chapter": chapter}}
        )

    async def set_last_check_date(self, date: str) -> None:
        await self.meta_col.update_one(
            {"_id": self.user_id},
            {"$set": {"last_check_date": date}},
            upsert=True
        )

def get_repository(user_id: str = None) -> AbstractRepository:
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise ValueError("MONGODB_URI не вказано в .env")
    db_name = os.getenv("MONGODB_DB", "Manga")
    manga_col = os.getenv("MONGODB_MANGA_COLLECTION", "manga")
    meta_col = os.getenv("MONGODB_META_COLLECTION", "meta")
    if user_id is None:
        user_id = os.getenv("TELEGRAM_CHAT_ID", "default")
    return MongoRepository(
        uri=uri,
        db_name=db_name,
        user_id=user_id,
        manga_col=manga_col,
        meta_col=meta_col,
    )