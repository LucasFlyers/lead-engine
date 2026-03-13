"""
Structured logging configuration for all services.
Outputs JSON in production (Railway log drain compatible),
human-readable in development.
"""
import logging
import os
import sys
import json
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Structured JSON log output for Railway / Datadog / Logtail."""

    def format(self, record: logging.LogRecord) -> str:
        log: dict = {
            "ts":      datetime.now(timezone.utc).isoformat(),
            "level":   record.levelname,
            "service": getattr(record, "service", os.environ.get("SERVICE_NAME", "lead-engine")),
            "logger":  record.name,
            "msg":     record.getMessage(),
        }
        if record.exc_info:
            log["exc"] = self.formatException(record.exc_info)
        if hasattr(record, "extra"):
            log.update(record.extra)
        return json.dumps(log, default=str)


class HumanFormatter(logging.Formatter):
    LEVEL_COLORS = {
        "DEBUG":    "\033[90m",
        "INFO":     "\033[36m",
        "WARNING":  "\033[33m",
        "ERROR":    "\033[31m",
        "CRITICAL": "\033[35m",
    }
    RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelname, "")
        ts    = datetime.now().strftime("%H:%M:%S")
        svc   = getattr(record, "service", os.environ.get("SERVICE_NAME", "app"))
        return (
            f"{color}{ts} [{record.levelname[0]}] "
            f"\033[90m{svc}\033[0m {color}"
            f"{record.name.split('.')[-1]}: "
            f"{record.getMessage()}{self.RESET}"
            + (f"\n{self.formatException(record.exc_info)}" if record.exc_info else "")
        )


def configure_logging(service_name: str = "lead-engine") -> None:
    """Call once at worker/app startup."""
    env        = os.environ.get("ENV", "development")
    log_level  = os.environ.get("LOG_LEVEL", "INFO").upper()
    use_json   = env == "production" or os.environ.get("LOG_JSON", "").lower() == "true"

    # Inject service name into every record
    old_factory = logging.getLogRecordFactory()
    def record_factory(*args, **kwargs):
        record = old_factory(*args, **kwargs)
        record.service = service_name  # type: ignore[attr-defined]
        return record
    logging.setLogRecordFactory(record_factory)

    formatter = JSONFormatter() if use_json else HumanFormatter()
    handler   = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.setLevel(getattr(logging, log_level, logging.INFO))
    root.handlers.clear()
    root.addHandler(handler)

    # Quiet noisy libraries
    for lib in ("httpx", "httpcore", "playwright", "asyncio", "sqlalchemy.engine"):
        logging.getLogger(lib).setLevel(logging.WARNING)

    logging.getLogger(__name__).info(
        "Logging configured: service=%s level=%s json=%s", service_name, log_level, use_json
    )
