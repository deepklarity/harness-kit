import copy
import json
import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Dict, Optional

IMPORTANT_LEVEL = logging.INFO + 5
logging.addLevelName(IMPORTANT_LEVEL, "IMPORTANT")

# Comprehensive ANSI escape regex — covers SGR, cursor movement, bracketed paste, etc.
ANSI_ESCAPE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def important(self, message, *args, **kwargs):
    if self.isEnabledFor(IMPORTANT_LEVEL):
        self._log(IMPORTANT_LEVEL, message, args, **kwargs)


logging.Logger.important = important  # type: ignore[attr-defined]


def strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return ANSI_ESCAPE.sub("", text)


class Colors:
    RESET = "\033[0m"
    INFO = "\033[38;5;110m"       # Light blue
    WARNING = "\033[38;5;178m"    # Light orange
    ERROR = "\033[38;5;174m"      # Light red
    DEBUG = "\033[38;5;240m"      # Gray
    CRITICAL = "\033[38;5;162m"   # Pink
    IMPORTANT = "\033[38;5;42m"   # Vibrant green


class CustomFormatter(logging.Formatter):
    def __init__(self, fmt, datefmt, use_colors=True, show_exc=True, abbreviate_exc_message=False):
        super().__init__(fmt, datefmt)
        self.use_colors = use_colors
        self.show_exc = show_exc
        self.abbreviate_exc_message = abbreviate_exc_message

    def format(self, record):
        record = copy.copy(record)

        # Format any dict/list messages to pretty JSON
        if isinstance(record.msg, (dict, list)):
            try:
                record.msg = json.dumps(record.msg, indent=2, default=str)
            except (TypeError, ValueError):
                record.msg = f"<Non-serializable {type(record.msg).__name__}: {str(record.msg)}>"

        # Get the relative path instead of absolute path
        try:
            relative_path = Path(record.pathname).relative_to(Path.cwd())
        except ValueError:
            relative_path = record.pathname

        record.file_info = f"{relative_path}:{record.lineno}"

        if self.abbreviate_exc_message and record.exc_info:
            message = record.getMessage()
            exc_text = str(record.exc_info[1]) if record.exc_info[1] else ""
            if exc_text and message.endswith(exc_text):
                message = message[: -len(exc_text)].rstrip()
            record.msg = message
            record.args = ()
        else:
            record.msg = record.getMessage()
            record.args = ()

        # Strip ANSI from file output; add colors for console
        if self.use_colors:
            color_code = getattr(Colors, record.levelname, Colors.RESET)
            record.levelname = f"{color_code}{record.levelname}{Colors.RESET}"
            record.msg = f"{color_code}{record.msg}{Colors.RESET}"
        else:
            # Ensure no ANSI codes leak into file logs
            record.msg = strip_ansi(str(record.msg))
            record.levelname = strip_ansi(record.levelname)

        if not self.show_exc:
            record.exc_info = None
            record.exc_text = None

        return super().format(record)


class TaskContextAdapter(logging.LoggerAdapter):
    """Logger adapter that prepends [task:<id>] when task_id is set.

    Usage:
        log = TaskContextAdapter(logger)
        log.set_task("42")
        log.info("Starting execution")  # -> "[task:42] Starting execution"
        log.clear_task()
    """

    def __init__(self, logger: logging.Logger, extra: Optional[dict] = None):
        super().__init__(logger, extra or {})

    def set_task(self, task_id: str) -> None:
        self.extra["task_id"] = task_id

    def clear_task(self) -> None:
        self.extra.pop("task_id", None)

    def process(self, msg, kwargs):
        task_id = self.extra.get("task_id")
        if task_id:
            msg = f"[task:{task_id}] {msg}"
        return msg, kwargs


def setup_logger(name: str = "odin", log_dir: Optional[str] = None) -> logging.Logger:
    """Configure a logger with console, file, and detail-file handlers.

    Args:
        name: Logger name (e.g. "odin", "odin.orchestrator").
        log_dir: Directory for log files. Defaults to ".odin/logs".
                 Writes <basename>.log (abbreviated) and <basename>_detail.log
                 (full tracebacks). basename is derived from the last segment
                 of the logger name.
    """
    if log_dir is None:
        log_dir = str(Path(".odin") / "logs")

    log_path = Path(log_dir)
    log_path.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.DEBUG)

    # Prevent adding handlers multiple times
    if logger.handlers:
        return logger

    # Derive file basename from logger name (e.g. "odin.orchestrator" -> "odin")
    basename = name.split(".")[0]

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.DEBUG)

    # File Handler with rotation
    file_handler = RotatingFileHandler(
        str(log_path / f"{basename}.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    file_handler.setLevel(logging.DEBUG)

    # Detail file handler (includes full exceptions)
    detail_handler = RotatingFileHandler(
        str(log_path / f"{basename}_detail.log"),
        maxBytes=10 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    detail_handler.setLevel(logging.DEBUG)

    log_format = "[%(asctime)s] %(levelname)s [%(file_info)s] - %(message)s"

    console_formatter = CustomFormatter(
        log_format, datefmt="%Y-%m-%d %H:%M:%S",
        use_colors=True, show_exc=False, abbreviate_exc_message=True,
    )
    file_formatter = CustomFormatter(
        log_format, datefmt="%Y-%m-%d %H:%M:%S",
        use_colors=False, show_exc=False, abbreviate_exc_message=True,
    )
    detail_formatter = CustomFormatter(
        log_format, datefmt="%Y-%m-%d %H:%M:%S",
        use_colors=False, show_exc=True, abbreviate_exc_message=False,
    )

    console_handler.setFormatter(console_formatter)
    file_handler.setFormatter(file_formatter)
    detail_handler.setFormatter(detail_formatter)

    logger.addHandler(console_handler)
    logger.addHandler(file_handler)
    logger.addHandler(detail_handler)

    return logger


def flatten_dict(data: Dict[str, Any], parent_key: str = "", sep: str = ".") -> Dict[str, Any]:
    """Flatten nested dictionaries for logging or analytics."""
    items: Dict[str, Any] = {}
    for key, value in data.items():
        new_key = f"{parent_key}{sep}{key}" if parent_key else key
        if isinstance(value, dict):
            items.update(flatten_dict(value, new_key, sep=sep))
        else:
            items[new_key] = value
    return items
