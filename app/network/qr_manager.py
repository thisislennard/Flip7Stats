"""QR-Codes als SVG (ohne Bildbibliothek, ohne Internet)."""

from __future__ import annotations

import io
from functools import lru_cache

import qrcode
import qrcode.image.svg


@lru_cache(maxsize=32)
def svg(data: str) -> str:
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M, border=2,
                       image_factory=qrcode.image.svg.SvgPathFillImage)
    qr.add_data(data)
    qr.make(fit=True)
    buf = io.BytesIO()
    qr.make_image().save(buf)
    return buf.getvalue().decode("utf-8")


def wifi_payload(ssid: str, password: str) -> str:
    """WLAN-QR-Code („WIFI:“-Format), mit dem sich Smartphones direkt verbinden."""
    def esc(s: str) -> str:
        return "".join("\\" + c if c in '\\;,:"' else c for c in s)
    return f"WIFI:T:WPA;S:{esc(ssid)};P:{esc(password)};;"
