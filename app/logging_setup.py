"""Logging in ``logs/application.log`` (rotierend). Tokens werden vor dem Schreiben geschwärzt."""

from __future__ import annotations

import logging
import re
from logging.handlers import RotatingFileHandler
from pathlib import Path

# Zugangsschlüssel in URLs (?key=…, ?once=…) und Header-Werte nie im Klartext protokollieren.
_REDACT = re.compile(r"((?:key|once|token|secret|password)=)[^&\s\"']+", re.IGNORECASE)


def redact(text: str) -> str:
    return _REDACT.sub(r"\1***", text)


class RedactingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = redact(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        return True


def setup_logging(log_file: Path, level: int = logging.INFO, console: bool = False) -> None:
    root = logging.getLogger()
    root.setLevel(level)
    for h in list(root.handlers):
        root.removeHandler(h)
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s")
    handlers: list[logging.Handler] = [RotatingFileHandler(log_file, maxBytes=2_000_000, backupCount=5, encoding="utf-8")]
    if console:
        handlers.append(logging.StreamHandler())
    for h in handlers:
        h.setFormatter(fmt)
        h.addFilter(RedactingFilter())
        root.addHandler(h)
    # Werkzeugs eigenes Zugriffsprotokoll enthielte Tokens aus URLs – eigenes, geschwärztes Protokoll stattdessen.
    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("engineio").setLevel(logging.WARNING)
    logging.getLogger("socketio").setLevel(logging.WARNING)
