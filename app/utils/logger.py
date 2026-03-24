import logging
import os

# -------------------------
# LOG DIRECTORY SETUP
# -------------------------
LOG_DIR = "logs"
os.makedirs(LOG_DIR, exist_ok=True)

LOG_FILE = os.path.join(LOG_DIR, "app.log")


# -------------------------
# GLOBAL CONFIGURATION
# -------------------------
def configure_logging(app_env: str = "development") -> None:
    """
    Call this ONCE in main.py
    """
    level = logging.DEBUG if app_env.lower() in {"development", "dev"} else logging.INFO

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)s | %(name)s | %(message)s"
    )

    root_logger = logging.getLogger()
    root_logger.setLevel(level)

   
    if root_logger.handlers:
        root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)

    # File handler
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(formatter)

    root_logger.addHandler(console_handler)
    root_logger.addHandler(file_handler)


# -------------------------
# MODULE LOGGER
# -------------------------
def get_logger(name: str):
    """
    Use in every file:
    logger = get_logger(__name__)
    """
    return logging.getLogger(name)