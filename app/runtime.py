from collections.abc import Callable

from app.exceptions import ApplicationError
from app.logging_config import configure_logging, get_logger


Entrypoint = Callable[[], None]


def run_entrypoint(
    entrypoint: Entrypoint,
    *,
    logger_name: str,
) -> int:
    """Run an executable entry point without exposing expected failures."""
    configure_logging()
    logger = get_logger(logger_name)

    try:
        entrypoint()
    except ApplicationError as exc:
        logger.error(
            "Application entry point failed",
            extra={
                "event": "application.entrypoint.failed",
                "error_type": type(exc).__name__,
            },
        )
        return 1

    return 0
