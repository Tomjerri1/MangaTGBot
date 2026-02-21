"""
Централізоване логування для всього проекту.
Імпортуй get_logger в будь-якому файлі замість print().
"""
import logging
import sys

def _setup_logger() -> logging.Logger:
    logger = logging.getLogger("manga")

    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    ch = logging.StreamHandler(sys.stdout)
    ch.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(ch)

    return logger

logger = _setup_logger()


def get_logger(name: str = "manga") -> logging.Logger:
    return logging.getLogger(f"manga.{name}") if name != "manga" else logger