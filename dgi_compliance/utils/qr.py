"""
QR rendering helper used by the print format.

We never call this during a submission — the DGI response already provides
the canonical `qrCode` payload string. This helper just turns that string
into a base64 PNG that the Jinja template embeds.
"""

from __future__ import annotations

import base64
import io


def render_qr_png(payload: str, box_size: int = 5, border: int = 2) -> str:
    """Return a 'data:image/png;base64,...' string. Requires `qrcode` + Pillow."""
    if not payload:
        return ""
    try:
        import qrcode  # noqa: WPS433 (runtime dep, declared in pyproject)
    except ImportError:
        return ""
    img = qrcode.make(payload, box_size=box_size, border=border)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
