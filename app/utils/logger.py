import logging


def configure_logging(app_env: str = "development") -> None:
    level = logging.DEBUG if app_env.lower() in {"development", "dev"} else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )

