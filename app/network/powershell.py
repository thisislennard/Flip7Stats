"""PowerShell-Aufrufe ohne sichtbares Konsolenfenster. Parameter gehen über Umgebungsvariablen, nie in den Skripttext."""

from __future__ import annotations

import base64
import json
import logging
import os
import subprocess
import sys

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000


class PowerShellError(RuntimeError):
    pass


def available() -> bool:
    return sys.platform == "win32"


def run_json(script: str, env: dict[str, str] | None = None, timeout: float = 20) -> dict:
    """Führt ein Skript in Windows PowerShell 5.1 aus (dort sind die WinRT-Netzwerk-APIs nutzbar) und liest JSON."""
    if not available():
        raise PowerShellError("PowerShell ist nur unter Windows verfügbar.")
    full = ("[Console]::OutputEncoding = [System.Text.Encoding]::UTF8\n"
            "$ProgressPreference = 'SilentlyContinue'\n" + script)
    # -EncodedCommand führt das Skript als Ganzes aus (über stdin liest PowerShell zeilenweise und verschluckt
    # mehrzeilige Pipelines).
    encoded = base64.b64encode(full.encode("utf-16-le")).decode("ascii")
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-EncodedCommand", encoded],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=timeout, env={**os.environ, **(env or {})}, creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PowerShellError(f"PowerShell nicht ausführbar: {type(exc).__name__}") from exc
    out = proc.stdout.strip()
    start = out.find("{")
    if start < 0:
        raise PowerShellError(f"Keine Antwort von PowerShell (Code {proc.returncode}): {proc.stderr.strip()[:200]}")
    try:
        return json.loads(out[start:out.rfind("}") + 1])
    except ValueError as exc:
        raise PowerShellError("Antwort von PowerShell nicht lesbar") from exc


def run_text(args: list[str], timeout: float = 10) -> str:
    """Konsolenprogramm (z. B. netsh) ausführen; Ausgabe in der OEM-Codepage der Konsole."""
    try:
        proc = subprocess.run(args, capture_output=True, timeout=timeout, creationflags=CREATE_NO_WINDOW)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise PowerShellError(f"{args[0]} nicht ausführbar: {type(exc).__name__}") from exc
    for enc in ("oem", "utf-8", "cp1252"):
        try:
            return proc.stdout.decode(enc)
        except (LookupError, UnicodeDecodeError):
            continue
    return proc.stdout.decode("latin-1")
