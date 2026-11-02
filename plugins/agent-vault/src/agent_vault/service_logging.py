"""Resident startup logging; the caller retains process admission."""

import logging.handlers


def configure_logging(logger: logging.Logger, log_file: str, *, verbose: bool) -> None:
    """Attach the existing rotating service log without failing service startup."""
    level = logging.DEBUG if verbose else logging.INFO
    try:
        handler = logging.handlers.RotatingFileHandler(
            log_file, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8",
        )
        handler.setLevel(level)
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(name)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        ))
        logging.getLogger().addHandler(handler)
    except Exception as error:
        logger.warning("Could not open log file %s: %s", log_file, error)
