import logging
import os
import sys

_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_PATH = os.path.join(_BASE_DIR, "data", "manga.log")

VERBOSE = "PYCHARM_HOSTED" in os.environ or sys.stderr.isatty()


def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("manga")

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # Файловий handler завжди активний
    os.makedirs(os.path.dirname(_LOG_PATH), exist_ok=True)
    fh = logging.FileHandler(_LOG_PATH, encoding="utf-8")
    fh.setFormatter(logging.Formatter(
        "[%(asctime)s] [%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    ))
    logger.addHandler(fh)

    # Консольний handler тільки в PyCharm або терміналі
    if VERBOSE:
        ch = logging.StreamHandler(sys.stdout)
        ch.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(ch)

    return logger


# Єдиний логер для всього проекту
logger = _setup_logger()


def get_logger(name: str = "manga") -> logging.Logger:
    """Повертає логер з потрібним іменем (підлогер основного)"""
    return logging.getLogger(f"manga.{name}") if name != "manga" else logger


def log(msg: str):
    """Швидкий виклик для основного логера"""
    logger.info(msg)