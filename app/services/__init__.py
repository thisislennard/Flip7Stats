"""Fachlogik. Services kennen weder Flask noch HTML – die API-Schicht übersetzt ihre Fehler in HTTP-Antworten."""


class ServiceError(Exception):
    """Fachlicher Fehler mit verständlicher Meldung für die Oberfläche."""

    status = 400
    code = "bad_request"

    def __init__(self, message: str, *, details: dict | None = None, code: str | None = None):
        super().__init__(message)
        self.message = message
        self.details = details or {}
        if code:
            self.code = code


class NotFound(ServiceError):
    status = 404
    code = "not_found"


class Conflict(ServiceError):
    status = 409
    code = "conflict"


class Invalid(ServiceError):
    status = 422
    code = "validation_error"
