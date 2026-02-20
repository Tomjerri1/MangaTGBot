import os
from abc import ABC, abstractmethod
from dotenv import load_dotenv

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
load_dotenv(os.path.join(_BASE_DIR, ".env"))

class AbstractRepository(ABC):

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
    MongoDB Atlas — для продакшну на сервері.
    Використовує motor (асинхронний драйвер для MongoDB).
    """

    DEFAULT = {"last_check_date": "", "manga": {}}

    def __init__(self, uri: str, db_name: str = "manga_tracker", collection: str = "state"):
        from motor.motor_asyncio import AsyncIOMotorClient
        self.client = AsyncIOMotorClient(
            uri,
            serverSelectionTimeoutMS=10000,  # 10с на пошук сервера
            connectTimeoutMS=10000,           # 10с на підключення
            socketTimeoutMS=20000,            # 20с на операцію
        )
        self.col = self.client[db_name][collection]

    async def load(self) -> dict:
        doc = await self.col.find_one({"_id": "state"})
        if not doc:
            return dict(self.DEFAULT)
        result = dict(doc)
        result.pop("_id", None)
        return result

    async def add_manga(self, title: str, url: str) -> None:
        await self.col.update_one(
            {"_id": "state"},
            {"$set": {f"manga.{title}": {"url": url, "last_chapter": "невідомо"}}},
            upsert=True
        )

    async def remove_manga(self, title: str) -> None:
        await self.col.update_one(
            {"_id": "state"},
            {"$unset": {f"manga.{title}": ""}}
        )

    async def update_chapter(self, title: str, chapter: str) -> None:
        await self.col.update_one(
            {"_id": "state"},
            {"$set": {f"manga.{title}.last_chapter": chapter}}
        )

    async def set_last_check_date(self, date: str) -> None:
        await self.col.update_one(
            {"_id": "state"},
            {"$set": {"last_check_date": date}},
            upsert=True
        )

def get_repository() -> AbstractRepository:
    uri = os.getenv("MONGODB_URI")
    if not uri:
        raise ValueError("MONGODB_URI не вказано в .env")
    db_name = os.getenv("MONGODB_DB", "manga_tracker")
    collection = os.getenv("MONGODB_COLLECTION", "state")
    return MongoRepository(uri=uri, db_name=db_name, collection=collection)