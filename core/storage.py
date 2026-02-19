import json
import os
import shutil

from core.logger import get_logger

log = get_logger("storage").info

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(_BASE_DIR, "data", "data.json")
_TEMP_FILE = DATA_FILE + ".tmp"
_BACKUP_FILE = DATA_FILE + ".bak"

DEFAULT_DATA = {
    "last_check_date": "",
    "manga": {}
}


def _validate(data) -> bool:
    if not isinstance(data, dict):
        return False
    if "last_check_date" not in data or "manga" not in data:
        return False
    if not isinstance(data["manga"], dict):
        return False
    for title, info in data["manga"].items():
        if not isinstance(info, dict):
            return False
        if "url" not in info or "last_chapter" not in info:
            return False
    return True


def _try_restore_backup() -> dict | None:
    """Спробує відновити дані з бекапу якщо основний файл пошкоджений"""
    if not os.path.exists(_BACKUP_FILE):
        return None
    try:
        with open(_BACKUP_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if _validate(data):
            log("Відновлено дані з бекапу data.json.bak")
            return data
    except Exception:
        pass
    return None


def load_data() -> dict:
    if not os.path.exists(DATA_FILE):
        os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)
        save_data(DEFAULT_DATA)
        return DEFAULT_DATA

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except json.JSONDecodeError as e:
        log(f"data.json пошкоджений: {e}. Спробую відновити з бекапу...")
        restored = _try_restore_backup()
        if restored:
            return restored
        raise ValueError(
            f"data.json пошкоджений і бекап відсутній або теж пошкоджений.\n"
            f"Помилка: {e}"
        )

    if not _validate(data):
        log("data.json має неправильну структуру. Спробую відновити з бекапу...")
        restored = _try_restore_backup()
        if restored:
            return restored
        raise ValueError(
            "data.json має неправильну структуру і бекап відсутній.\n"
            "Очікується: {\"last_check_date\": \"\", \"manga\": {\"Назва\": {\"url\": \"...\", \"last_chapter\": \"...\"}}}"
        )

    return data


def save_data(data: dict):
    os.makedirs(os.path.dirname(DATA_FILE), exist_ok=True)

    if os.path.exists(DATA_FILE):
        shutil.copy2(DATA_FILE, _BACKUP_FILE)

    with open(_TEMP_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

    os.replace(_TEMP_FILE, DATA_FILE)