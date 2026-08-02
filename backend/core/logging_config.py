"""
Central logging configuration for JARVIS.

Sets up:
- A rotating UTF-8 file handler at ~/.jarvis/jarvis.log (or JARVIS_LOG_DIR)
- A console handler mirroring the same records
- A single consistent level for all app loggers

Call configure_logging() once at application startup.
"""

import logging
import logging.handlers
import os
import sys


def default_log_path() -> str:
    """Default location for the JARVIS log file."""
    env_dir = os.environ.get("JARVIS_LOG_DIR", "")
    if env_dir:
        return os.path.join(env_dir, "jarvis.log")
    from .storage import log_path
    return log_path()


_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_DATE_FMT = "%Y-%m-%d %H:%M:%S"


def configure_logging(level: int = logging.INFO) -> None:
    """Configure root logging with file + console handlers.

    Idempotent: calling more than once only adds handlers once, so it is safe
    to invoke from both uvicorn startup and test fixtures.
    """
    root = logging.getLogger()
    if any(getattr(h, "_jarvis_handler", False) for h in root.handlers):
        return

    root.setLevel(level)

    formatter = logging.Formatter(_FORMAT, datefmt=_DATE_FMT)

    log_path = default_log_path()
    try:
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        file_handler = logging.handlers.RotatingFileHandler(
            log_path,
            maxBytes=5 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(level)
        file_handler._jarvis_handler = True
        root.addHandler(file_handler)
    except OSError as e:
        # Logging should never crash startup; fall back to console only.
        sys.stderr.write(f"JARVIS: could not open log file {log_path}: {e}\n")

    console_handler = logging.StreamHandler(sys.stderr)
    console_handler.setFormatter(formatter)
    console_handler.setLevel(level)
    console_handler._jarvis_handler = True
    root.addHandler(console_handler)

    # Keep library noise (httpx, uvicorn access logs) from flooding the file.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
