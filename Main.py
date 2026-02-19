import asyncio
import signal
from datetime import datetime

from core.telegram_sender import send_report
from core.storage import load_data
from core.checker import run_check
from core.logger import get_logger

log = get_logger("main").info


def _handle_signal(sig, frame):
    log(f" ! Отримано сигнал {sig} - завершуємо роботу...")
    raise SystemExit(0)


async def check_manga():
    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    data = load_data()
    today = datetime.now().strftime("%Y-%m-%d")

    if data["last_check_date"] == today:
        log("Перевірка вже була сьогодні.")
        return

    try:
        report_text, errors = await run_check()
        log("\n" + report_text)
        await send_report(report_text)

        if errors:
            error_text = "Не вдалося перевірити:\n" + "\n".join(f"  • {t}" for t in errors)
            log(error_text)
            await send_report(error_text)

    except SystemExit:
        log("Main.py завершено коректно.")
    except Exception as e:
        log(f"Критична помилка: {e}")
        raise


if __name__ == "__main__":
    asyncio.run(check_manga())