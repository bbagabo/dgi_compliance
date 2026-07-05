"""Generate a QR PNG (data URI) from the e-DEF qrCode content, for the print format."""
import io
import base64


def make_qr_data_uri(content: str) -> str | None:
    if not content:
        return None
    try:
        import qrcode
        img = qrcode.make(content)
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        # qrcode lib unavailable or render error -> print format falls back to the text code
        return None
