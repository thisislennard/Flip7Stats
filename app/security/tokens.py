"""Zufällige Zugangsschlüssel und einmalige Anmeldelinks."""

from __future__ import annotations

import hmac
import secrets
import threading
import time


def new_token(nbytes: int = 24) -> str:
    return secrets.token_urlsafe(nbytes)


def matches(given: str | None, expected: str | None) -> bool:
    """Vergleich in konstanter Zeit (kein Erraten über Antwortzeiten)."""
    if not given or not expected:
        return False
    return hmac.compare_digest(given.encode(), expected.encode())


class OneTimeTokens:
    """Einmal-Tokens mit kurzer Laufzeit – für den Admin-Anmeldelink, den nur die App selbst im Browser öffnet."""

    def __init__(self, ttl_seconds: float = 120):
        self.ttl = ttl_seconds
        self._tokens: dict[str, float] = {}
        self._lock = threading.Lock()

    def issue(self) -> str:
        token = new_token(32)
        now = time.monotonic()
        with self._lock:
            self._tokens = {t: exp for t, exp in self._tokens.items() if exp > now}
            self._tokens[token] = now + self.ttl
        return token

    def consume(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.monotonic()
        with self._lock:
            for known in list(self._tokens):
                if matches(token, known):
                    expiry = self._tokens.pop(known)
                    return expiry > now
        return False
