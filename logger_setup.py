"""
Logging setup - file rotation + console output + auto-cleanup.

Features:
- TimedRotatingFileHandler (midnight rotation, configurable backup count)
- Auto-delete log files older than max_age_days on startup
- Configurable log level and console output
- UTF-8 encoding for log files
"""

import os
import time
import glob
import logging
from logging.handlers import TimedRotatingFileHandler


def _cleanup_old_logs(logs_dir: str, log_filename: str, filename_base: str, max_age_days: int, logger: logging.Logger):
    """
    Delete log files (rotated backups) that are older than max_age_days.
    The current active log file is preserved.
    """
    now = time.time()
    cutoff = now - max_age_days * 86400

    # Match rotated backup files: e.g. proxy.log.2026-04-26, proxy.log.2026-05-01
    pattern = os.path.join(logs_dir, f"{filename_base}.*")
    for fpath in glob.glob(pattern):
        if not os.path.isfile(fpath):
            continue
        # Skip the active log file itself (e.g. proxy.log has no date suffix after the name)
        basename = os.path.basename(fpath)
        if basename == log_filename or basename == f"{filename_base}.log":
            continue
        mtime = os.path.getmtime(fpath)
        if mtime < cutoff:
            try:
                os.remove(fpath)
                logger.info("Deleted old log file: %s (age: %.1f days)", fpath, (now - mtime) / 86400)
            except OSError as e:
                logger.warning("Failed to delete old log file %s: %s", fpath, e)


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
    backup_count = config.get("backup_count", 7)
    max_age_days = config.get("max_age_days", 7)

    # Ensure logs directory exists
    logs_dir = os.path.join(workdir, "logs")
    os.makedirs(logs_dir, exist_ok=True)

    log_filename = config.get("file", "proxy.log")
    if not log_filename.lower().endswith(".log"):
        log_filename += ".log"
    log_path = os.path.join(logs_dir, log_filename)

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
        backupCount=backup_count,
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

    # --- Cleanup old logs on startup ---
    # Extract base filename without extension for glob pattern
    filename_base = log_filename
    if filename_base.lower().endswith(".log"):
        filename_base = filename_base[:-4]
    _cleanup_old_logs(logs_dir, log_filename, filename_base, max_age_days, logger)

    return logger
