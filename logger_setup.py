"""
Logging setup - file rotation + console output.

Features:
- TimedRotatingFileHandler (midnight rotation, configurable backup count)
- Configurable log level and console output
- UTF-8 encoding for log files
"""

import os
import logging
from logging.handlers import TimedRotatingFileHandler


def setup_logging(config: dict, workdir: str) -> logging.Logger:
    """
    Configure and return the 'cline-proxy' logger.

    Args:
        config: The logging section from config.yaml.
        workdir: Absolute path of the project root (where logs/ directory lives).
    """
    level_str = config.get("level", "INFO").upper()
    level = getattr(logging, level_str, logging.INFO)
    console_enabled = config.get("console", True)

    # Ensure logs directory exists
    logs_dir = os.path.join(workdir, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    log_path = os.path.join(
        logs_dir, config.get("file", "proxy.log")
    )
    if not log_path.lower().endswith(".log"):
        log_path += ".log"

    logger = logging.getLogger("cline-proxy")
    logger.setLevel(level)

    # Avoid duplicate handlers on re-initialization
    if logger.hasHandlers():
        logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    # File handler with daily rotation
    file_handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=config.get("backup_count", 7),
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler.setLevel(level)
    logger.addHandler(file_handler)

    # Console handler
    if console_enabled:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        console_handler.setLevel(level)
        logger.addHandler(console_handler)

    return logger
