import logging
import sys
import os
from logging.handlers import TimedRotatingFileHandler

from app.core.request_context import get_request_id
from app.core.config import settings 


class RequestIdFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_request_id() or "N/A"
        return True


def setup_logger():
    logger = logging.getLogger("construction-mgmt")

    if logger.hasHandlers():
        logger.handlers.clear()

    level = logging.DEBUG if settings.LOG_LEVEL == "DEBUG" else logging.INFO
    logger.setLevel(level)

    os.makedirs("logs", exist_ok=True)

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(filename)s:%(lineno)d | request_id=%(request_id)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)

    file_handler = TimedRotatingFileHandler(
        "logs/app.log",
        when="midnight",
        interval=1,
        backupCount=7
    )
    file_handler.suffix = "%Y-%m-%d"
    file_handler.setFormatter(formatter)

    console_handler.addFilter(RequestIdFilter())
    file_handler.addFilter(RequestIdFilter())

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)

    logger.info(f"Logging initialized | env={settings.APP_ENV} level={settings.LOG_LEVEL}")

    return logger


logger = logging.getLogger("construction-mgmt")