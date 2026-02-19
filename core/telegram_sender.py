from telegram import Bot
from config.config import TOKEN, CHAT_ID

_bot = Bot(token=TOKEN)

_MAX_LENGTH = 4096


def _split_text(text: str) -> list[str]:
    """
    Розбиває текст на частини по _MAX_LENGTH символів.
    Розбиває тільки по переносу рядка щоб не розрізати запис манги навпіл.
    """
    if len(text) <= _MAX_LENGTH:
        return [text]

    parts = []
    current = ""

    for line in text.splitlines(keepends=True):
        # якщо один рядок довший за ліміт розбиває жорстко
        if len(line) > _MAX_LENGTH:
            if current:
                parts.append(current)
                current = ""
            for i in range(0, len(line), _MAX_LENGTH):
                parts.append(line[i:i + _MAX_LENGTH])
            continue

        if len(current) + len(line) > _MAX_LENGTH:
            parts.append(current)
            current = line
        else:
            current += line

    if current:
        parts.append(current)

    return parts


async def send_report(text: str):
    """Відправляє повідомлення, автоматично розбиваючи якщо більше 4096 символів"""
    parts = _split_text(text)
    for part in parts:
        await _bot.send_message(chat_id=CHAT_ID, text=part, disable_web_page_preview=True)